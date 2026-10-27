"""Ledger context from existing booking snapshots, under real PostgreSQL RLS."""
from datetime import date, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database import tenant_transaction
from app.management_models import finance
from test_management import appointment, customer, entry, management_db

pytestmark = pytest.mark.integration


async def ledger(d, **params):
    response = await d["client"].get(d["base"] + "/finance", params={**d["period"], **params})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
async def test_historical_ledger_context_uses_ordered_snapshots_without_rewriting_entries(management_db):
    d = management_db
    person = await customer(d, name="Cliente do atendimento")
    starts = d["now"] - timedelta(days=2)
    visit = await appointment(d, person["id"], status="completed", start_at=starts, amount=5500)
    second = uuid4()
    async with d["migrator"].begin() as db:
        await db.execute(text("""
            INSERT INTO services (id,tenant_id,name,duration_min,price_cents)
            VALUES (:id,:tenant,'Barba original',20,2000)
        """), {"id": second, "tenant": d["a"]})
        await db.execute(text("""
            UPDATE appointment_items SET item_order=1,name_snapshot='Corte original',price_snapshot_cents=3500
            WHERE tenant_id=:tenant AND appointment_id=:visit
        """), {"tenant": d["a"], "visit": UUID(visit)})
        # Inserted after the first item, but selected before it by the customer.
        await db.execute(text("""
            INSERT INTO appointment_items (tenant_id,appointment_id,service_id,name_snapshot,
                duration_snapshot_min,price_snapshot_cents,prep_snapshot_min,cleanup_snapshot_min,item_order)
            VALUES (:tenant,:visit,:service,'Barba original',20,2000,0,0,0)
        """), {"tenant": d["a"], "visit": UUID(visit), "service": second})
        await db.execute(text("""
            UPDATE appointments SET end_at=:ends,occupied_until=:ends
            WHERE tenant_id=:tenant AND id=:visit
        """), {"tenant": d["a"], "visit": UUID(visit), "ends": starts + timedelta(minutes=50)})
        protocol = (await db.execute(text("SELECT protocol FROM appointments WHERE tenant_id=:tenant AND id=:visit"),
                                    {"tenant": d["a"], "visit": UUID(visit)})).scalar_one()

    # Historical imports may have the appointment link without duplicating its
    # customer/professional IDs. Reading context must not backfill the ledger.
    linked = await entry(d, description="Atendimento " + protocol, appointment_id=visit, amount_cents=5500)
    manual = await entry(d, description="Venda avulsa de produto", amount_cents=700,
                         customer_id=person["id"], professional_id=str(d["pro"]))
    async with d["migrator"].begin() as db:
        before = dict((await db.execute(text("SELECT * FROM financial_entries WHERE tenant_id=:tenant AND id=:id"),
                                       {"tenant": d["a"], "id": UUID(linked["id"])})).mappings().one())
        await db.execute(text("""
            UPDATE services SET name='Nome atual do catálogo',duration_min=90,price_cents=9900
            WHERE tenant_id=:tenant AND id=ANY(CAST(:ids AS uuid[]))
        """), {"tenant": d["a"], "ids": [d["service"], second]})

    result = await ledger(d)
    assert result["total"] == 2 and result["summary"]["income_cents"] == 6200
    rows = {row["id"]: row for row in result["items"]}
    row = rows[linked["id"]]
    assert row["description"] == "Atendimento " + protocol
    assert row["amount_cents"] == 5500 and row["version"] == 1
    assert row["customer_id"] is None and row["professional_id"] is None
    assert row["customer_name"] == row["customer_display_name"] == "Cliente do atendimento"
    assert row["customer_deleted"] is False
    assert row["professional_name"] == "Profissional de teste"
    context = row["appointment"]
    assert context["id"] == visit and context["protocol"] == protocol
    assert context["timezone"] == "America/Sao_Paulo"
    assert datetime.fromisoformat(context["start_at"]) == starts
    assert datetime.fromisoformat(context["end_at"]) == starts + timedelta(minutes=50)
    assert row["occurred_at"] != context["start_at"]
    assert context["services"] == [
        {"service_id": str(second), "name": "Barba original", "duration_min": 20,
         "price_cents": 2000, "price_mode_snapshot": "fixed"},
        {"service_id": str(d["service"]), "name": "Corte original", "duration_min": 30,
         "price_cents": 3500, "price_mode_snapshot": "fixed"},
    ]
    assert rows[manual["id"]]["appointment"] is None
    assert rows[manual["id"]]["description"] == "Venda avulsa de produto"
    assert rows[manual["id"]]["customer_display_name"] == "Cliente do atendimento"
    assert rows[manual["id"]]["professional_name"] == "Profissional de teste"
    assert not any(key.startswith("_") for row in result["items"] for key in row)
    async with d["migrator"].connect() as db:
        after = dict((await db.execute(text("SELECT * FROM financial_entries WHERE tenant_id=:tenant AND id=:id"),
                                      {"tenant": d["a"], "id": UUID(linked["id"])})).mappings().one())
    assert after == before


@pytest.mark.asyncio
async def test_new_completed_receivable_keeps_final_value_and_original_service_quote(management_db):
    d = management_db
    person = await customer(d, name="Cliente com preço confirmado")
    visit = await appointment(d, person["id"], amount=10000)
    async with d["migrator"].begin() as db:
        await db.execute(text("""
            UPDATE appointment_items SET price_mode_snapshot='from'
            WHERE tenant_id=:tenant AND appointment_id=:visit
        """), {"tenant": d["a"], "visit": UUID(visit)})
    completed = await d["client"].post(d["base"] + f"/appointments/{visit}/complete",
        json={"idempotency_key": str(uuid4()), "final_total_cents": 13000})
    assert completed.status_code == 200, completed.text
    result = await ledger(d)
    assert result["total"] == 1
    row = result["items"][0]
    assert row["description"] == "Atendimento " + completed.json()["protocol"]
    assert row["amount_cents"] == 13000 and row["status"] == "pending"
    assert result["summary"]["pending_income_cents"] == 13000
    assert result["summary"]["balance_cents"] == 0
    assert row["customer_display_name"] == "Cliente com preço confirmado"
    assert row["professional_name"] == "Profissional de teste"
    assert row["appointment"]["id"] == visit
    assert row["appointment"]["services"][0]["price_cents"] == 10000
    assert row["appointment"]["services"][0]["price_mode_snapshot"] == "from"


@pytest.mark.asyncio
async def test_finance_customer_fallback_and_deleted_identity_preserve_booking_history(management_db):
    d = management_db
    person = await customer(d, name="Cliente WhatsApp", phone="11912345678")
    visit = await appointment(d, person["id"], status="completed")
    linked = await entry(d, appointment_id=visit, amount_cents=10000)
    manual = await entry(d, description="Atendimento AG-ABCDEF sem vínculo", amount_cents=500)
    rows = {row["id"]: row for row in (await ledger(d))["items"]}
    assert rows[linked["id"]]["customer_name"] == "Cliente WhatsApp"
    assert rows[linked["id"]]["customer_display_name"] == "+5511912345678"
    assert rows[linked["id"]]["customer_deleted"] is False
    assert rows[manual["id"]]["appointment"] is None
    assert rows[manual["id"]]["customer_name"] is None
    assert rows[manual["id"]]["customer_display_name"] is None
    assert rows[manual["id"]]["customer_deleted"] is False

    removed = await d["client"].request("DELETE", d["base"] + "/customers/" + person["id"],
        json={"expected_version": 1})
    assert removed.status_code == 200, removed.text
    result = await ledger(d)
    row = next(item for item in result["items"] if item["id"] == linked["id"])
    assert row["customer_name"] is None and row["customer_display_name"] is None
    assert row["customer_deleted"] is True
    assert row["appointment"]["id"] == visit
    assert row["appointment"]["services"][0]["name"] == "Corte sintético"
    assert row["amount_cents"] == 10000 and row["version"] == 1
    assert "+5511912345678" not in str(result) and "Cliente WhatsApp" not in str(result)


@pytest.mark.asyncio
async def test_finance_context_is_tenant_scoped_page_bounded_and_batched(management_db):
    d = management_db
    person = await customer(d, name="Cliente da empresa A")
    visits = [await appointment(d, person["id"], status="completed",
                               start_at=d["now"]-timedelta(days=number+1)) for number in range(3)]
    for number, visit in enumerate(visits):
        await entry(d, appointment_id=visit, amount_cents=1000,
                    occurred_at=(d["now"]-timedelta(minutes=number)).isoformat())

    other_service = uuid4()
    async with d["migrator"].begin() as db:
        await db.execute(text("""
            INSERT INTO services (id,tenant_id,name,duration_min,price_cents)
            VALUES (:id,:tenant,'Serviço privado da empresa B',30,8000)
        """), {"id": other_service, "tenant": d["b"]})
    other = {**d, "a": d["b"], "pro": d["other_pro"], "service": other_service, "base": d["other_base"]}
    other_person = await customer(other, name="Cliente privado da empresa B")
    other_visit = await appointment(other, other_person["id"], status="completed")
    other_entry = await entry(other, appointment_id=other_visit, amount_cents=8000)
    async with d["migrator"].begin() as db:
        # Protocols are unique within a tenant, not across tenants. Never infer
        # a booking from an entry's text or a globally matched short protocol.
        await db.execute(text("""
            UPDATE appointments SET protocol=(SELECT protocol FROM appointments WHERE tenant_id=:a AND id=:first)
            WHERE tenant_id=:b AND id=:second
        """), {"a": d["a"], "b": d["b"], "first": UUID(visits[0]), "second": UUID(other_visit)})
        await db.execute(text("""
            UPDATE appointment_items SET name_snapshot='Serviço privado da empresa B'
            WHERE tenant_id=:tenant AND appointment_id=:visit
        """), {"tenant": d["b"], "visit": UUID(other_visit)})

    page = await ledger(d, limit=1, offset=1)
    assert page["total"] == 3 and len(page["items"]) == 1
    assert page["summary"]["income_cents"] == 3000
    assert page["items"][0]["appointment"]["id"] == visits[1]
    assert page["items"][0]["customer_display_name"] == "Cliente da empresa A"
    assert "empresa B" not in str(page) and other_entry["id"] not in str(page)
    other_page = await ledger(other)
    assert other_page["total"] == 1
    assert other_page["items"][0]["appointment"]["id"] == other_visit
    assert other_page["items"][0]["customer_display_name"] == "Cliente privado da empresa B"
    assert other_page["items"][0]["appointment"]["services"][0]["name"] == "Serviço privado da empresa B"
    forbidden = await d["client"].post(d["base"] + "/finance", json={
        "kind": "income", "description": "Vínculo de outra empresa", "amount_cents": 100,
        "occurred_at": d["now"].isoformat(), "appointment_id": other_visit, "idempotency_key": str(uuid4()),
    })
    assert forbidden.status_code == 404

    statements = []
    def record_statement(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(d["runtime"].sync_engine, "before_cursor_execute", record_statement)
    try:
        async with async_sessionmaker(d["runtime"], expire_on_commit=False)() as db:
            async with tenant_transaction(db, d["a"]):
                query_counts = []
                for page_size in (1, 3):
                    statements.clear()
                    result = await finance(db, d["a"], start_date=date.fromisoformat(d["period"]["start_date"]),
                                           end_date=date.fromisoformat(d["period"]["end_date"]), limit=page_size)
                    assert len(result["items"]) == page_size
                    query_counts.append(len(statements))
                    assert sum("FROM appointment_items" in sql for sql in statements) == 1
                    assert all(row["appointment"]["services"] for row in result["items"])
                assert query_counts[0] == query_counts[1], "Reading more rows must not add per-entry queries"
    finally:
        event.remove(d["runtime"].sync_engine, "before_cursor_execute", record_statement)
