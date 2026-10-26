"""Variable minimum-price services: tenant boundaries, snapshots and cash safety."""
from datetime import timedelta
from uuid import UUID

import pytest
from sqlalchemy import text

from test_booking import booking_db, book_appointment, confirm_hold, UTC, start_at
from test_management import management_db, appointment, customer
from test_public_catalog import public_db, save_site, publish, availability, book_body, combo

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_booking_snapshots_preserve_variable_minimum_and_actual_catalog_changes(booking_db):
    d = booking_db
    async with d["migrator"].begin() as db:
        await db.execute(text("""
            UPDATE services SET price_mode='from', price_cents=8000
            WHERE tenant_id=:tenant AND id=:service
        """), {"tenant": d["a"], "service": d["botox"]})
    now = start_at(d["day"], 11)
    async with d["factory"]() as session:
        proposal = await book_appointment(
            session, d["a"], d["pro"], [d["botox"]],
            "+5567999991234", "Cliente de alisamento", now, "variable-minimum-test")
        assert proposal["total_cents"] == 8000
        result = await confirm_hold(session, d["a"], proposal["id"], customer_phone="+5567999991234")
        assert result["status"] == "confirmed"
        assert result["price_mode"] == "from"
        assert result["items"][0]["price_mode_snapshot"] == "from"
        assert result["items"][0]["price_snapshot_cents"] == 8000
    async with d["migrator"].begin() as db:
        await db.execute(text("""
            UPDATE services SET price_cents=9500,price_mode='fixed'
            WHERE tenant_id=:tenant AND id=:service
        """), {"tenant": d["a"], "service": d["botox"]})
        row = (await db.execute(text("""
            SELECT a.total_cents,i.price_mode_snapshot,i.price_snapshot_cents
            FROM appointments a JOIN appointment_items i
              ON i.tenant_id=a.tenant_id AND i.appointment_id=a.id
            WHERE a.tenant_id=:tenant AND a.id=:appointment
        """), {"tenant": d["a"], "appointment": UUID(str(result["id"]))})).one()
        assert tuple(row) == (8000, "from", 8000)


@pytest.mark.asyncio
async def test_variable_completion_requires_agreed_total_before_revenue_or_commission(management_db):
    d = management_db
    person = await customer(d)
    visit = await appointment(d, person["id"], amount=8000)
    async with d["migrator"].begin() as db:
        await db.execute(text("""
            UPDATE appointment_items SET price_mode_snapshot='from'
            WHERE tenant_id=:tenant AND appointment_id=:id
        """), {"tenant": d["a"], "id": UUID(visit)})
    endpoint = d["base"] + f"/appointments/{visit}/complete"
    missing = await d["client"].post(endpoint, json={"idempotency_key": "variable-missing"})
    smaller = await d["client"].post(endpoint, json={"idempotency_key": "variable-smaller", "final_total_cents": 7000})
    assert missing.status_code == 422, missing.text
    assert smaller.status_code == 422, smaller.text
    async with d["migrator"].begin() as db:
        assert (await db.execute(text("SELECT status FROM appointments WHERE id=:id"), {"id": UUID(visit)})).scalar_one() == "confirmed"
        assert (await db.execute(text("SELECT count(*) FROM financial_entries WHERE appointment_id=:id"), {"id": UUID(visit)})).scalar_one() == 0

    payload = {"idempotency_key": "variable-agreed", "final_total_cents": 15000}
    first = await d["client"].post(endpoint, json=payload)
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "completed"
    assert first.json()["total_cents"] == 15000
    assert first.json()["commission_cents"] == 3750
    again = await d["client"].post(endpoint, json=payload)
    assert again.status_code == 200, again.text
    async with d["migrator"].begin() as db:
        row = (await db.execute(text("""
            SELECT status,total_cents,commission_cents,commission_percent_snapshot
            FROM appointments WHERE id=:id
        """), {"id": UUID(visit)})).one()
        assert row[0] == "completed" and row[1] == 15000 and row[2] == 3750
        ledger = (await db.execute(text("""
            SELECT amount_cents,status FROM financial_entries WHERE appointment_id=:id
        """), {"id": UUID(visit)})).all()
        assert ledger == [(15000, "pending")]
        assert (await db.execute(text("""
            SELECT count(*) FROM appointment_history
            WHERE tenant_id=:tenant AND appointment_id=:id AND event='completed'
        """), {"tenant": d["a"], "id": UUID(visit)})).scalar_one() == 1
    assert (await d["client"].post(endpoint, json={
        "idempotency_key": "variable-agreed", "final_total_cents": 17000})).status_code == 409


@pytest.mark.asyncio
async def test_fixed_price_cannot_be_overridden_on_completion(management_db):
    d = management_db
    person = await customer(d)
    visit = await appointment(d, person["id"])
    invalid = await d["client"].post(d["base"] + f"/appointments/{visit}/complete", json={
        "idempotency_key": "fixed-price-override", "final_total_cents": 12500})
    assert invalid.status_code == 422, invalid.text


@pytest.mark.asyncio
async def test_public_from_pricing_and_fixed_combo_rejection(public_db):
    d = public_db
    await save_site(d)
    async with d["migrator"].begin() as db:
        await db.execute(text("""
            UPDATE services SET price_mode='from',price_cents=8000
            WHERE tenant_id=:tenant AND id=:id
        """), {"tenant": d["a"], "id": d["cut"]})
    bad = await d["client"].post(d["base"] + "/service-packages", json={
        "name": "Combo sem preço garantido", "service_ids": [str(d["cut"]),str(d["wash"])],
        "price_cents": 9000, "idempotency_key": "variable-combo-rejected"})
    assert bad.status_code == 422, bad.text
    await publish(d)
    catalog = await d["client"].get(d["public"])
    assert catalog.status_code == 200, catalog.text
    offer = next(x for x in catalog.json()["services"] if x["id"] == str(d["cut"]))
    assert offer["price_mode"] == "from" and offer["price_cents"] == 8000
    slot = await availability(d, service=d["cut"])
    assert slot["price_mode"] == "from" and slot["price_cents"] == 8000
    confirmed = await d["client"].post(d["public"] + "/appointments", json=book_body(slot,phone="+5511987654309"))
    assert confirmed.status_code == 201, confirmed.text
    assert confirmed.json()["price_mode"] == "from"
    assert confirmed.json()["price_cents"] == 8000


@pytest.mark.asyncio
async def test_variable_synthetic_addon_retains_floor_and_snapshot(public_db):
    d = public_db
    await save_site(d)
    async with d["migrator"].begin() as db:
        await db.execute(text("""
            UPDATE services SET price_mode='from',price_cents=8500
            WHERE tenant_id=:tenant AND id=:id
        """), {"tenant": d["a"], "id": d["wash"]})
    base = d["base"] + "/services/" + str(d["cut"]) + "/addons"
    put = await d["client"].put(base, json={"expected_version": 0, "items": [
        {"addon_service_id": str(d["wash"]), "add_duration": False}]})
    assert put.status_code == 200, put.text
    await publish(d)
    slot = await availability(d, service=d["cut"], addons=[d["wash"]])
    assert slot["price_mode"] == "from"
    assert slot["price_cents"] == 11500
    booked = await d["client"].post(d["public"] + "/appointments", json=book_body(slot,phone="+5511987654310"))
    assert booked.status_code == 201, booked.text
    assert booked.json()["price_mode"] == "from"
    async with d["migrator"].begin() as db:
        row = (await db.execute(text("""
            SELECT i.price_mode_snapshot,s.catalog_kind
            FROM appointments a JOIN appointment_items i ON i.tenant_id=a.tenant_id AND i.appointment_id=a.id
            JOIN services s ON s.tenant_id=i.tenant_id AND s.id=i.service_id
            WHERE a.tenant_id=:tenant AND a.protocol=:protocol
        """), {"tenant": d["a"], "protocol": booked.json()["protocol"]})).one()
        assert row == ("from", "variant")
