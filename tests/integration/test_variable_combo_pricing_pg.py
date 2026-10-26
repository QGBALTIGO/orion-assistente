"""End-to-end dynamic minimum-priced combos against isolated PostgreSQL."""
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from test_public_catalog import (
    public_db, save_site, publish, availability, book_body, combo,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_variable_component_creates_a_variable_combo_on_site_and_manual_finance(public_db):
    d = public_db
    c = d["client"]
    await save_site(d)
    await publish(d)
    async with d["migrator"].begin() as db:
        await db.execute(text("""
          UPDATE services SET price_mode='from',price_cents=8000
          WHERE tenant_id=:tenant AND id=:service
        """), {"tenant": d["a"], "service": d["cut"]})
    saved, _ = await combo(d, name="Corte e barba - valor inicial", price_cents=7500)
    identity = UUID(saved["synthetic_service_id"])
    assert saved["price_mode"] == "from"
    assert saved["effective_price_cents"] == 7500
    assert saved["available"] is True
    assert [i["price_mode"] for i in saved["items"]] == ["from", "fixed"]

    catalog = await c.get(d["public"])
    assert catalog.status_code == 200, catalog.text
    offered = next(item for item in catalog.json()["services"] if item["id"] == str(identity))
    assert offered["price_mode"] == "from"
    assert offered["price_cents"] == 7500
    assert set(offered["professional_ids"]) == {str(d["pro"])}

    slot = await availability(d, service=identity)
    assert slot["price_cents"] == 7500 and slot["price_mode"] == "from"
    appointment = await c.post(d["public"] + "/appointments",
                               json=book_body(slot, "+5511987654381"))
    assert appointment.status_code == 201, appointment.text
    assert appointment.json()["price_mode"] == "from"
    assert appointment.json()["price_cents"] == 7500
    protocol = appointment.json()["protocol"]
    # A catalog change after booking must not reprice that customer's snapshot.
    async with d["migrator"].begin() as db:
        old = (await db.execute(text("""
          SELECT a.id,a.total_cents,i.price_mode_snapshot
          FROM appointments a JOIN appointment_items i
            ON i.tenant_id=a.tenant_id AND i.appointment_id=a.id
          WHERE a.tenant_id=:tenant AND a.protocol=:protocol
        """), {"tenant": d["a"], "protocol": protocol})).one()
        assert old.total_cents == 7500 and old.price_mode_snapshot == "from"
        await db.execute(text("""
          UPDATE services SET price_mode='fixed' WHERE tenant_id=:tenant AND id=:service
        """), {"tenant": d["a"], "service": d["cut"]})
        await db.execute(text("""
          UPDATE appointments SET start_at=now()-interval '1 hour',
            end_at=now()-interval '10 minutes',
            occupied_from=now()-interval '1 hour',
            occupied_until=now()-interval '10 minutes'
          WHERE tenant_id=:tenant AND id=:appointment
        """), {"tenant": d["a"], "appointment": old.id})

    endpoint = d["base"] + "/appointments/" + str(old.id) + "/complete"
    missing = await c.post(endpoint, json={"idempotency_key": str(uuid4())})
    below_min = await c.post(endpoint, json={"idempotency_key": str(uuid4()),
                                             "final_total_cents": 7400})
    assert missing.status_code == 422, missing.text
    assert below_min.status_code == 422, below_min.text
    completed = await c.post(endpoint, json={"idempotency_key": str(uuid4()),
                                              "final_total_cents": 9500})
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "completed"
    assert completed.json()["total_cents"] == 9500
    assert completed.json()["original_total_cents"] == 9500
    async with d["migrator"].connect() as db:
        ledger = (await db.execute(text("""
          SELECT amount_cents,status FROM financial_entries
          WHERE tenant_id=:tenant AND appointment_id=:appointment
        """), {"tenant": d["a"], "appointment": old.id})).all()
        assert ledger == [(9500, "pending")]
        snapshot = (await db.execute(text("""
          SELECT price_mode_snapshot,price_snapshot_cents
          FROM appointment_items WHERE tenant_id=:tenant AND appointment_id=:appointment
        """), {"tenant": d["a"], "appointment": old.id})).one()
        assert tuple(snapshot) == ("from", 7500)


@pytest.mark.asyncio
async def test_changed_service_auto_converts_combo_both_ways_without_losing_its_price(public_db):
    d = public_db
    saved, _ = await combo(d, name="Combo normal", price_cents=4200)
    synthetic = UUID(saved["synthetic_service_id"])
    assert saved["price_mode"] == "fixed"
    async with d["migrator"].begin() as db:
        await db.execute(text("""
          UPDATE services SET price_mode='from',price_cents=3400
          WHERE tenant_id=:tenant AND id=:service
        """), {"tenant": d["a"], "service": d["cut"]})
    changed = (await d["client"].get(d["base"] + "/service-packages")).json()["items"][0]
    assert changed["price_mode"] == "from"
    assert changed["effective_price_cents"] == 4200
    assert changed["available"] is True
    assert changed["price_cents"] == 4200, "configured combo price is not overwritten"
    async with d["migrator"].connect() as db:
        raw = (await db.execute(text("""
          SELECT price_mode,price_cents,active FROM services
          WHERE tenant_id=:tenant AND id=:id
        """), {"tenant": d["a"], "id": synthetic})).one()
        assert tuple(raw) == ("from", 4200, True)

    async with d["migrator"].begin() as db:
        await db.execute(text("""
          UPDATE services SET price_mode='fixed' WHERE tenant_id=:tenant AND id=:service
        """), {"tenant": d["a"], "service": d["cut"]})
    reverted = (await d["client"].get(d["base"] + "/service-packages")).json()["items"][0]
    assert reverted["price_mode"] == "fixed"
    assert reverted["effective_price_cents"] == 4200
    assert reverted["available"] is True


@pytest.mark.asyncio
async def test_price_minimum_is_required_and_old_free_combo_gets_safe_fallback(public_db):
    d = public_db
    fixed, _ = await combo(d, price_cents=0)
    async with d["migrator"].begin() as db:
        await db.execute(text("""
          UPDATE services SET price_mode='from',price_cents=8000
          WHERE tenant_id=:tenant AND id=:service
        """), {"tenant": d["a"], "service": d["cut"]})
    current = (await d["client"].get(d["base"] + "/service-packages")).json()["items"][0]
    assert current["price_mode"] == "from"
    assert current["price_cents"] == 0, "no silent mutation of configured historic price"
    assert current["effective_price_cents"] == 10000, "zero must never be a fake variable floor"
    assert current["available"] is True

    invalid = await d["client"].post(d["base"] + "/service-packages", json={
        "name": "Preço sem mínimo", "service_ids": [str(d["cut"]), str(d["beard"])],
        "price_cents": 0, "idempotency_key": "variable-floor-zero-test",
    })
    assert invalid.status_code == 422, invalid.text
    assert "mínimo" in invalid.text
    assert (await d["client"].get(d["base"] + "/service-packages")).json()["items"][0]["id"] == fixed["id"]


@pytest.mark.asyncio
async def test_combo_with_variable_addon_retains_estimated_quote_and_confidential_scope(public_db):
    d = public_db
    combo_record, _ = await combo(d, price_cents=4300)
    synthetic = UUID(combo_record["synthetic_service_id"])
    async with d["migrator"].begin() as db:
        await db.execute(text("""
          UPDATE services SET price_mode='from',price_cents=900
          WHERE tenant_id=:tenant AND id=:service
        """), {"tenant": d["a"], "service": d["wash"]})
    added = await d["client"].put(
        d["base"] + "/services/" + str(synthetic) + "/addons",
        json={"expected_version": 0, "items": [
            {"addon_service_id": str(d["wash"]), "add_duration": False}
        ]}
    )
    assert added.status_code == 200, added.text
    await save_site(d)
    await publish(d)
    slot = await availability(d, service=synthetic, addons=[d["wash"]])
    assert slot["price_mode"] == "from"
    assert slot["price_cents"] == 5200
    booked = await d["client"].post(d["public"] + "/appointments",
                                     json=book_body(slot, "+5511987654382"))
    assert booked.status_code == 201, booked.text
    assert booked.json()["price_mode"] == "from"
    async with d["migrator"].connect() as db:
        kinds = (await db.execute(text("""
          SELECT i.price_mode_snapshot,s.catalog_kind
          FROM appointments a JOIN appointment_items i ON i.tenant_id=a.tenant_id AND i.appointment_id=a.id
          JOIN services s ON s.tenant_id=i.tenant_id AND s.id=i.service_id
          WHERE a.tenant_id=:tenant AND a.protocol=:protocol
        """), {"tenant": d["a"], "protocol": booked.json()["protocol"]})).one()
        assert kinds == ("from", "variant")
    # Another company cannot read this combo.
    forbidden = await d["client"].get(d["base"].replace(str(d["a"]), str(d["b"])) + "/service-packages")
    assert forbidden.status_code in (403, 404)
