"""Professional report history against PostgreSQL, including tenant RLS."""
from datetime import date, datetime, timedelta, timezone
import os
from uuid import UUID, uuid4

from fastapi import HTTPException
import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.appointment_lifecycle import LifecycleActor, SYSTEM_ACTOR
from app.booking_service import expire_holds, history as record_history
from app.catalog_api import Actor, authenticate
from app.database import tenant_transaction
from app.performance_history import HISTORY_STATUSES, read_professional_history
from test_management import appointment, customer, management_db

pytestmark = pytest.mark.integration


async def report(d, *, professional_id=None, tenant_id=None, rls_tenant=None, **parameters):
    tenant_id = tenant_id or d["a"]
    session_factory = async_sessionmaker(d["runtime"])
    async with session_factory() as db:
        async with tenant_transaction(db, rls_tenant or tenant_id):
            # The report must work without permission to perform any data write.
            await db.execute(text("SET TRANSACTION READ ONLY"))
            return await read_professional_history(
                db, tenant_id, professional_id or d["pro"],
                start_date=parameters.pop("start_date", date.fromisoformat(d["period"]["start_date"])),
                end_date=parameters.pop("end_date", date.fromisoformat(d["period"]["end_date"])),
                **parameters,
            )


@pytest.mark.asyncio
async def test_history_includes_every_state_and_cancellation_without_mutating_expired_holds(management_db):
    d = management_db
    person = await customer(d, name="Cliente do histórico")
    starts = (d["now"] - timedelta(days=2)).replace(hour=1, minute=0, second=0, microsecond=0)
    visits = {}
    for number, (label, status) in enumerate([
        ("completed", "completed"), ("confirmed", "confirmed"), ("no_show", "no_show"),
        ("hold", "hold"), ("stale_hold", "hold"), ("cancelled", "cancelled"),
        ("expired_cancelled", "cancelled"), ("legacy_cancelled", "cancelled"),
    ]):
        visits[label] = UUID(await appointment(
            d, person["id"], status=status, start_at=starts + timedelta(hours=number),
        ))
    async with d["migrator"].begin() as db:
        await db.execute(text("""
            UPDATE appointments SET hold_expires_at=:expiry
            WHERE tenant_id=:tenant AND id=:id
        """), {"tenant": d["a"], "id": visits["stale_hold"], "expiry": d["now"] - timedelta(hours=1)})
        await record_history(db, d["a"], visits["cancelled"], "cancelled", calendar=False,
            context=LifecycleActor(role="customer", source="public_site", customer_id=UUID(person["id"])),
            requested_by="customer", reason="Cliente pediu outra data")
        await record_history(db, d["a"], visits["expired_cancelled"], "hold_expired", calendar=False,
            context=SYSTEM_ACTOR)
        await db.execute(text("UPDATE professionals SET active=false WHERE tenant_id=:tenant AND id=:id"),
                         {"tenant": d["a"], "id": d["pro"]})
        before = (await db.execute(text("""
            SELECT id,status,hold_expires_at,updated_at FROM appointments
            WHERE tenant_id=:tenant ORDER BY id
        """), {"tenant": d["a"]})).all()

    result = await report(d)
    assert result["professional"] == {"id": d["pro"], "name": "Profissional de teste", "active": False}
    assert result["total"] == 8 and result["has_more"] is False
    assert result["counts"] == {
        "all": 8, "completed": 1, "confirmed": 1, "no_show": 1,
        "hold": 1, "expired_hold": 2, "cancelled": 2,
    }
    rows = {row["id"]: row for row in result["items"]}
    assert set(rows) == set(visits.values())
    assert rows[visits["stale_hold"]]["status"] == "hold"
    assert rows[visits["stale_hold"]]["history_status"] == "expired_hold"
    assert rows[visits["expired_cancelled"]]["status"] == "cancelled"
    assert rows[visits["expired_cancelled"]]["history_status"] == "expired_hold"
    assert rows[visits["expired_cancelled"]]["cancellation"]["event"] == "hold_expired"
    assert rows[visits["expired_cancelled"]]["cancellation"]["source"] == "system"
    cancellation = rows[visits["cancelled"]]["cancellation"]
    assert cancellation["event"] == "cancelled"
    assert cancellation["requested_by"] == "customer"
    assert cancellation["reason"] == "Cliente pediu outra data"
    assert cancellation["source"] == "public_site"
    assert rows[visits["legacy_cancelled"]]["cancellation"]["requested_by"] == "unknown"
    assert rows[visits["legacy_cancelled"]]["cancellation"]["reason"] is None
    assert all(row["allowed_actions"] == [] for row in result["items"])
    for status in HISTORY_STATUSES:
        filtered = await report(d, status=status, limit=1)
        assert filtered["total"] == result["counts"][status]
        assert filtered["counts"] == result["counts"]
        assert all(row["history_status"] == status for row in filtered["items"])
        assert filtered["has_more"] == (filtered["total"] > 1)
    async with d["migrator"].connect() as db:
        after = (await db.execute(text("""
            SELECT id,status,hold_expires_at,updated_at FROM appointments
            WHERE tenant_id=:tenant ORDER BY id
        """), {"tenant": d["a"]})).all()
    assert after == before


@pytest.mark.asyncio
async def test_performance_cancellations_match_history_before_and_after_hold_expiry(management_db):
    d = management_db
    person = await customer(d)
    starts = (d["now"] - timedelta(days=2)).replace(hour=4, minute=0, second=0, microsecond=0)
    visits = {}
    for number, (label, status) in enumerate([
        ("completed", "completed"), ("confirmed", "confirmed"), ("no_show", "no_show"),
        ("expired", "hold"), ("cancelled", "cancelled"), ("legacy_cancelled", "cancelled"),
    ]):
        visits[label] = UUID(await appointment(
            d, person["id"], status=status, amount=7500, start_at=starts + timedelta(hours=number),
        ))
    async with d["migrator"].begin() as db:
        await db.execute(text("UPDATE appointments SET hold_expires_at=:expiry WHERE tenant_id=:tenant AND id=:id"),
                         {"tenant": d["a"], "id": visits["expired"], "expiry": d["now"] - timedelta(minutes=1)})
        # An older expiry event must not hide a later real cancellation.
        await record_history(db, d["a"], visits["cancelled"], "hold_expired", calendar=False, context=SYSTEM_ACTOR)
        await record_history(db, d["a"], visits["cancelled"], "cancelled", calendar=False,
            context=LifecycleActor(role="customer", source="public_site", customer_id=UUID(person["id"])),
            requested_by="customer", reason="Cliente pediu o cancelamento")

    async def assert_consistent():
        response = await d["client"].get(d["base"] + "/performance", params=d["period"])
        assert response.status_code == 200, response.text
        summary = next(item for item in response.json()["items"] if item["id"] == str(d["pro"]))
        historical = await report(d)
        assert summary["cancelled"] == historical["counts"]["cancelled"] == 2
        assert historical["counts"]["expired_hold"] == 1
        assert summary["completed"] == historical["counts"]["completed"] == 1
        assert summary["no_show"] == historical["counts"]["no_show"] == 1
        assert summary["revenue_cents"] == 7500 and summary["average_ticket_cents"] == 7500
        return historical

    before = await assert_consistent()
    expired = next(item for item in before["items"] if item["id"] == visits["expired"])
    assert expired["status"] == "hold" and expired["history_status"] == "expired_hold"
    session_factory = async_sessionmaker(d["runtime"])
    async with session_factory() as db:
        async with tenant_transaction(db, d["a"]):
            await expire_holds(db, d["a"], d["pro"], datetime.now(timezone.utc))
    after = await assert_consistent()
    expired = next(item for item in after["items"] if item["id"] == visits["expired"])
    assert expired["status"] == "cancelled" and expired["history_status"] == "expired_hold"
    assert expired["cancellation"]["event"] == "hold_expired"


@pytest.mark.asyncio
async def test_history_period_uses_local_start_boundaries_including_cancelled_appointments(management_db):
    d = management_db
    person = await customer(d)
    day = date(2026, 9, 1)
    lower = datetime(2026, 9, 1, 4, tzinfo=timezone.utc)
    upper = lower + timedelta(days=1)
    async with d["migrator"].begin() as db:
        await db.execute(text("UPDATE tenants SET timezone='America/Campo_Grande' WHERE id=:id"),
                         {"id": d["a"]})
    before = await appointment(d, person["id"], status="completed", start_at=lower - timedelta(microseconds=1))
    first = await appointment(d, person["id"], status="cancelled", start_at=lower)
    last = await appointment(d, person["id"], status="completed", start_at=upper - timedelta(microseconds=1))
    after = await appointment(d, person["id"], status="completed", start_at=upper)
    result = await report(d, start_date=day, end_date=day)
    assert result["period"] == {"start_date": day, "end_date": day, "timezone": "America/Campo_Grande"}
    assert result["total"] == 2
    assert [row["id"] for row in result["items"]] == [UUID(last), UUID(first)]
    assert {UUID(before), UUID(after)}.isdisjoint(row["id"] for row in result["items"])
    assert all(row["timezone"] == "America/Campo_Grande" for row in result["items"])
    assert result["counts"]["completed"] == 1 and result["counts"]["cancelled"] == 1


@pytest.mark.asyncio
async def test_history_preserves_multiple_service_snapshots_and_redacts_deleted_customer_identity(management_db):
    d = management_db
    person = await customer(d, name="Nome privado do histórico", phone="11912345678")
    visit = UUID(await appointment(d, person["id"], status="completed", amount=5800))
    second = uuid4()
    async with d["migrator"].begin() as db:
        await db.execute(text("""
            INSERT INTO services (id,tenant_id,name,duration_min,price_cents)
            VALUES (:id,:tenant,'Barba atual',45,9000)
        """), {"id": second, "tenant": d["a"]})
        await db.execute(text("""
            UPDATE appointment_items SET item_order=1,name_snapshot='Corte original',
                price_snapshot_cents=3500 WHERE tenant_id=:tenant AND appointment_id=:visit
        """), {"tenant": d["a"], "visit": visit})
        await db.execute(text("""
            INSERT INTO appointment_items (tenant_id,appointment_id,service_id,name_snapshot,
                duration_snapshot_min,price_snapshot_cents,price_mode_snapshot,
                prep_snapshot_min,cleanup_snapshot_min,item_order)
            VALUES (:tenant,:visit,:service,'Barba original',20,2500,'from',0,0,0)
        """), {"tenant": d["a"], "visit": visit, "service": second})
        await db.execute(text("""
            UPDATE appointments SET original_total_cents=6000,discount_cents=200,
                end_at=start_at+interval '50 minutes',occupied_until=start_at+interval '50 minutes'
            WHERE tenant_id=:tenant AND id=:visit
        """), {"tenant": d["a"], "visit": visit})
        await db.execute(text("""
            UPDATE services SET name='Novo nome do catálogo',price_cents=12000,duration_min=90
            WHERE tenant_id=:tenant AND id=:service
        """), {"tenant": d["a"], "service": d["service"]})
    item = (await report(d))["items"][0]
    assert item["id"] == visit
    assert item["total_cents"] == 5800 and item["original_total_cents"] == 6000 and item["discount_cents"] == 200
    assert item["customer_name"] == item["customer_display_name"] == "Nome privado do histórico"
    assert item["customer_phone"] == "+5511912345678" and item["customer_deleted"] is False
    assert [(line["service_id"], line["name_snapshot"], line["duration_snapshot_min"],
             line["price_snapshot_cents"], line["price_mode_snapshot"]) for line in item["items"]] == [
        (second, "Barba original", 20, 2500, "from"),
        (d["service"], "Corte original", 30, 3500, "fixed"),
    ]
    removed = await d["client"].request("DELETE", d["base"] + "/customers/" + person["id"],
                                      json={"expected_version": 1})
    assert removed.status_code == 200, removed.text
    result = await report(d)
    hidden = result["items"][0]
    assert hidden["id"] == visit and hidden["customer_deleted"] is True
    assert hidden["customer_name"] is None
    assert hidden["customer_display_name"] is None
    assert hidden["customer_phone"] is None
    assert hidden["items"] == item["items"] and hidden["total_cents"] == 5800
    assert "Nome privado do histórico" not in str(result)
    assert "+5511912345678" not in str(result)


@pytest.mark.asyncio
async def test_history_has_stable_pages_and_does_not_include_another_professional_or_tenant(management_db):
    d = management_db
    person = await customer(d)
    starts = d["now"] - timedelta(days=1)
    visits = [UUID(await appointment(d, person["id"], status="completed", start_at=starts)) for _ in range(4)]
    colleague, other_service = uuid4(), uuid4()
    async with d["migrator"].begin() as db:
        await db.execute(text("""
            INSERT INTO professionals (id,tenant_id,name) VALUES (:id,:tenant,'Outro profissional da empresa')
        """), {"id": colleague, "tenant": d["a"]})
        await db.execute(text("""
            INSERT INTO services (id,tenant_id,name,duration_min,price_cents)
            VALUES (:id,:tenant,'Serviço privado da outra empresa',30,1000)
        """), {"id": other_service, "tenant": d["b"]})
    await appointment(d, person["id"], status="completed", start_at=starts, professional_id=colleague)
    other = {**d, "a": d["b"], "pro": d["other_pro"], "service": other_service, "base": d["other_base"]}
    other_person = await customer(other, name="Cliente privado da outra empresa")
    await appointment(other, other_person["id"], status="completed", start_at=starts)
    first = await report(d, limit=2)
    second = await report(d, limit=2, offset=2)
    empty = await report(d, limit=2, offset=4)
    assert first["total"] == second["total"] == empty["total"] == 4
    assert first["has_more"] is True and second["has_more"] is False and empty["has_more"] is False
    assert empty["items"] == []
    assert [row["id"] for row in first["items"] + second["items"]] == sorted(visits, reverse=True)
    assert first["items"] == (await report(d, limit=2))["items"]
    assert "Cliente privado da outra empresa" not in str(first)
    for parameters in [
        {"professional_id": d["other_pro"]},
        {"tenant_id": d["b"], "professional_id": d["other_pro"], "rls_tenant": d["a"]},
        {"professional_id": uuid4()},
    ]:
        with pytest.raises(HTTPException) as error:
            await report(d, **parameters)
        assert error.value.status_code == 404
    assert (await report(d, professional_id=colleague))["total"] == 1
    assert (await report(other))["total"] == 1


@pytest.mark.asyncio
async def test_history_reads_one_bounded_page_with_constant_query_count(management_db):
    d = management_db
    person = await customer(d)
    for number in range(8):
        await appointment(d, person["id"], status="completed", start_at=d["now"] - timedelta(days=number + 1))
    factory = async_sessionmaker(d["runtime"])
    statements = []

    def capture(_connection, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    async def measured(limit):
        async with factory() as db:
            async with tenant_transaction(db, d["a"]):
                await db.execute(text("SET TRANSACTION READ ONLY"))
                statements.clear()
                event.listen(d["runtime"].sync_engine, "before_cursor_execute", capture)
                try:
                    result = await read_professional_history(db, d["a"], d["pro"],
                        date.fromisoformat(d["period"]["start_date"]),
                        date.fromisoformat(d["period"]["end_date"]), limit=limit)
                finally:
                    event.remove(d["runtime"].sync_engine, "before_cursor_execute", capture)
                return result, list(statements)

    small, small_queries = await measured(1)
    large, large_queries = await measured(8)
    assert len(small["items"]) == 1 and len(large["items"]) == 8
    assert small["total"] == large["total"] == 8
    assert len(small_queries) == len(large_queries) <= 5
    assert all(query.lstrip().upper().startswith(("SELECT", "WITH")) for query in large_queries)


@pytest.mark.asyncio
async def test_history_rejects_invalid_filters_and_report_periods(management_db):
    d = management_db
    for parameters in [
        {"limit": 0}, {"limit": 201}, {"offset": -1}, {"offset": 2_147_483_648},
        {"status": "anything' OR 1=1 --"},
        {"start_date": date(2026, 10, 27), "end_date": date(2026, 10, 26)},
        {"start_date": date(2025, 1, 1), "end_date": date(2026, 1, 2)},
    ]:
        with pytest.raises(HTTPException) as error:
            await report(d, **parameters)
        assert error.value.status_code == 422
    empty = await report(d)
    assert empty["total"] == 0 and empty["items"] == [] and empty["has_more"] is False
    assert all(count == 0 for count in empty["counts"].values())


@pytest.mark.asyncio
async def test_history_api_allows_viewers_and_verifies_current_membership_before_reading(management_db):
    d = management_db
    person = await customer(d, name="Cliente visível à equipe")
    await appointment(d, person["id"], status="completed")
    path = d["base"] + f"/performance/professionals/{d['pro']}/history"
    other_path = d["other_base"] + f"/performance/professionals/{d['other_pro']}/history"
    viewer = {"Authorization": "Bearer " + os.environ["VIEWER_API_TOKEN"]}
    result = await d["client"].get(path, params=d["period"], headers=viewer)
    assert result.status_code == 200, result.text
    assert result.json()["items"][0]["customer_name"] == "Cliente visível à equipe"
    assert result.json()["items"][0]["allowed_actions"] == []
    assert (await d["client"].get(other_path, params=d["period"], headers=viewer)).status_code == 403
    foreign_professional = d["base"] + f"/performance/professionals/{d['other_pro']}/history"
    assert (await d["client"].get(foreign_professional, params=d["period"], headers=viewer)).status_code == 404
    assert (await d["client"].get(path, headers={"Authorization": ""})).status_code == 401
    assert (await d["client"].get(path, headers={"Authorization": "Bearer invalid-test-token"})).status_code == 401

    async def cached_member():
        return Actor("member", tenant_id=d["b"], account_id=d["account"], membership_role="owner")

    async def stale_role_member():
        return Actor("member", tenant_id=d["a"], account_id=d["account"], membership_role="owner")

    async def stranger():
        return Actor("member", tenant_id=d["a"], account_id=uuid4(), membership_role="owner")

    try:
        async with d["migrator"].begin() as db:
            await db.execute(text("UPDATE tenant_memberships SET role='viewer' WHERE tenant_id=:tenant AND account_id=:id"),
                             {"tenant": d["a"], "id": d["account"]})
        d["app"].dependency_overrides[authenticate] = cached_member
        # Authentication cannot carry tenant B into a request for A. With the
        # correct scope, the domain still rechecks a stale cached owner role.
        assert (await d["client"].get(path, params=d["period"])).status_code == 403
        d["app"].dependency_overrides[authenticate] = stale_role_member
        current_viewer = await d["client"].get(path, params=d["period"])
        assert current_viewer.status_code == 200
        assert current_viewer.json()["items"][0]["allowed_actions"] == []
        assert (await d["client"].get(other_path, params=d["period"])).status_code == 403
        async with d["migrator"].begin() as db:
            await db.execute(text("UPDATE tenant_memberships SET active=false WHERE tenant_id=:tenant AND account_id=:id"),
                             {"tenant": d["a"], "id": d["account"]})
        assert (await d["client"].get(path, params=d["period"])).status_code == 403
        d["app"].dependency_overrides[authenticate] = stranger
        assert (await d["client"].get(path, params=d["period"])).status_code == 403
    finally:
        d["app"].dependency_overrides.pop(authenticate, None)


@pytest.mark.asyncio
async def test_history_api_serializes_the_frontend_contract_and_validates_page_status_and_period(management_db):
    d = management_db
    person = await customer(d, name="Cliente do contrato", phone="11912345678")
    visits = [await appointment(d, person["id"], status=status,
                start_at=d["now"] - timedelta(days=number + 1), amount=4200)
              for number, status in enumerate(("completed", "cancelled", "no_show"))]
    path = d["base"] + f"/performance/professionals/{d['pro']}/history"
    response = await d["client"].get(path, params={**d["period"], "limit": 1, "offset": 1})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["professional"] == {"id": str(d["pro"]), "name": "Profissional de teste", "active": True}
    assert result["period"] == {**d["period"], "timezone": "America/Sao_Paulo"}
    assert (result["total"], result["limit"], result["offset"], result["has_more"]) == (3, 1, 1, True)
    assert result["counts"]["all"] == 3
    item = result["items"][0]
    assert item["id"] == visits[1] and item["professional_id"] == str(d["pro"])
    assert item["status"] == item["history_status"] == "cancelled"
    assert item["customer_name"] == item["customer_display_name"] == "Cliente do contrato"
    assert item["customer_phone"] == "+5511912345678" and item["customer_deleted"] is False
    assert item["total_cents"] == 4200 and item["protocol"].startswith("AG-")
    assert item["timezone"] == "America/Sao_Paulo"
    assert datetime.fromisoformat(item["start_at"]) < datetime.fromisoformat(item["end_at"])
    assert item["items"][0]["service_id"] == str(d["service"])
    assert item["items"][0]["name_snapshot"] == "Corte sintético"
    assert item["items"][0]["price_snapshot_cents"] == 4200
    assert item["items"][0]["duration_snapshot_min"] == 30
    assert item["cancellation"]["requested_by"] == "unknown"
    assert item["allowed_actions"] == [] and "tenant_id" not in item
    filtered = await d["client"].get(path, params={**d["period"], "status": "cancelled"})
    assert filtered.status_code == 200
    assert [row["id"] for row in filtered.json()["items"]] == [visits[1]]
    assert filtered.json()["total"] == 1 and filtered.json()["counts"]["all"] == 3
    expired = await d["client"].get(path, params={**d["period"], "status": "expired_hold"})
    assert expired.status_code == 200 and expired.json()["total"] == 0
    for invalid in [
        {"limit": 0}, {"limit": 201}, {"limit": "abc"}, {"offset": -1},
        {"offset": 2_147_483_648}, {"status": "expired"}, {"start_date": "invalid-date"},
        {"start_date": "2026-10-27", "end_date": "2026-10-26"},
        {"start_date": "2025-01-01", "end_date": "2026-01-02"},
    ]:
        error = await d["client"].get(path, params={**d["period"], **invalid})
        assert error.status_code == 422, (invalid, error.text)
