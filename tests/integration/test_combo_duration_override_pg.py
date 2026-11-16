"""Combo duration overrides use the real scoped database and booking engine."""
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from test_public_catalog import (
    public_db, combo, save_site, publish, availability, book_body,
)

pytestmark = pytest.mark.integration


def edited_payload(original, version, override):
    return {
        "name": original["name"],
        "service_ids": original["service_ids"],
        "price_cents": original["price_cents"],
        "duration_override_min": override,
        "expected_version": version,
    }


@pytest.mark.asyncio
async def test_combo_switches_between_automatic_and_custom_and_preserves_bookings(public_db):
    d = public_db
    c = d["client"]
    route = d["base"] + "/service-packages"
    saved, original = await combo(d)
    assert saved["duration_override_min"] is None
    assert saved["automatic_duration_min"] == 50
    assert saved["duration_min"] == 50

    async with d["migrator"].begin() as db:
        await db.execute(text("UPDATE services SET duration_min=45 WHERE tenant_id=:t AND id=:s"),
                         {"t": d["a"], "s": d["cut"]})
    derived = (await c.get(route)).json()["items"][0]
    assert derived["duration_min"] == derived["automatic_duration_min"] == 65

    change = await c.patch(route + "/" + saved["id"],
                           json=edited_payload(original, derived["version"], 38))
    assert change.status_code == 200, change.text
    custom = change.json()
    assert custom["duration_min"] == 38
    assert custom["duration_override_min"] == 38
    assert custom["automatic_duration_min"] == 65
    assert custom["available"] is True

    await save_site(d)
    await publish(d)
    live = (await c.get(d["public"])).json()
    listed = next(s for s in live["services"] if s["id"] == saved["synthetic_service_id"])
    assert listed["duration_min"] == 38

    quoted = await availability(d, service=UUID(saved["synthetic_service_id"]))
    assert quoted["duration_min"] == 38
    booked = await c.post(d["public"] + "/appointments", json=book_body(quoted))
    assert booked.status_code == 201, booked.text
    protocol = booked.json()["protocol"]

    async with d["migrator"].begin() as db:
        await db.execute(text("UPDATE services SET duration_min=50 WHERE tenant_id=:t AND id=:s"),
                         {"t": d["a"], "s": d["cut"]})
    still_custom = (await c.get(route)).json()["items"][0]
    assert still_custom["duration_min"] == 38
    assert still_custom["automatic_duration_min"] == 70

    longer = await c.patch(route + "/" + saved["id"],
                           json=edited_payload(original, still_custom["version"], 85))
    assert longer.status_code == 200, longer.text
    assert longer.json()["duration_min"] == 85
    await publish(d)
    refreshed_quote = await availability(d, service=UUID(saved["synthetic_service_id"]))
    assert refreshed_quote["duration_min"] == 85

    async with d["migrator"].connect() as db:
        snapshot = (await db.execute(text("""
            SELECT i.duration_snapshot_min
            FROM appointment_items i JOIN appointments a
              ON a.tenant_id=i.tenant_id AND a.id=i.appointment_id
            WHERE a.tenant_id=:t AND a.protocol=:protocol
        """), {"t": d["a"], "protocol": protocol})).scalar_one()
        assert snapshot == 38, "existing appointment must retain its booked duration"

    back = await c.patch(route + "/" + saved["id"],
                         json=edited_payload(original, longer.json()["version"], None))
    assert back.status_code == 200, back.text
    assert back.json()["duration_override_min"] is None
    assert back.json()["duration_min"] == back.json()["automatic_duration_min"] == 70


@pytest.mark.asyncio
async def test_custom_combo_time_is_used_by_added_services_and_variants(public_db):
    d = public_db
    c = d["client"]
    saved, payload = await combo(d, duration_override_min=35)
    assert saved["duration_min"] == 35
    assert saved["automatic_duration_min"] == 50

    addons = await c.put(d["base"] + "/services/" + saved["synthetic_service_id"] + "/addons",
                         json={"expected_version": 0, "items": [
                             {"addon_service_id": str(d["wash"]), "add_duration": True}
                         ]})
    assert addons.status_code == 200, addons.text

    selection = {"service_id": saved["synthetic_service_id"],
                 "professional_id": str(d["pro"]), "addon_ids": [str(d["wash"])]}
    first = await c.post(d["base"] + "/booking-selection", json=selection)
    assert first.status_code == 200, first.text
    assert first.json()["duration_min"] == 45

    update = await c.patch(d["base"] + "/service-packages/" + saved["id"],
                           json=edited_payload(payload, saved["version"], 90))
    assert update.status_code == 200, update.text
    repeated = await c.post(d["base"] + "/booking-selection", json=selection)
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["duration_min"] == 100
    assert repeated.json()["engine_service_id"] == first.json()["engine_service_id"]


@pytest.mark.asyncio
async def test_invalid_custom_duration_cannot_change_packages(public_db):
    d = public_db
    c = d["client"]
    payload = {"name": "Combo sintético", "service_ids": [str(d["cut"]), str(d["beard"])],
               "price_cents": 4200}
    for override in (0, 1441, -1, True, "45", 45.5):
        response = await c.post(d["base"] + "/service-packages", json={
            **payload, "duration_override_min": override,
            "idempotency_key": "duration-rejected-" + uuid4().hex,
        })
        assert response.status_code == 422, (override, response.text)
    untouched, _ = await combo(d)
    assert untouched["duration_override_min"] is None
    assert untouched["duration_min"] == 50
