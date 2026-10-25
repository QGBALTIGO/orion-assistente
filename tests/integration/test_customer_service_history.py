"""Read-only customer registration chronology and completed-service timeline.

All fixtures are synthetic and run on disposable tenant-scoped PostgreSQL rows.
"""
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from test_management import appointment, customer, management_db

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_customer_registered_at_is_original_for_create_edit_list_and_export(management_db):
    d = management_db
    person = await customer(d, name="Cadastro de teste")
    original = datetime.fromisoformat(person["created_at"].replace("Z", "+00:00"))
    assert original.tzinfo is not None and original.utcoffset() is not None
    assert abs((original - datetime.now(timezone.utc)).total_seconds()) < 120

    changed = await d["client"].patch(
        d["base"] + "/customers/" + person["id"],
        json={"name": "Novo nome", "expected_version": person["version"]})
    assert changed.status_code == 200, changed.text
    assert changed.json()["created_at"] == person["created_at"]

    detail = await d["client"].get(d["base"] + "/customers/" + person["id"])
    listing = await d["client"].get(d["base"] + "/customers")
    export = await d["client"].get(d["base"] + "/customers/export.csv")
    assert detail.status_code == listing.status_code == export.status_code == 200
    assert detail.json()["created_at"] == person["created_at"]
    assert next(row for row in listing.json()["items"] if row["id"] == person["id"])["created_at"] == person["created_at"]
    assert "Cadastrado em (UTC)" in export.text
    assert person["created_at"].split(".")[0].replace("T", " ")[:10] in export.text


@pytest.mark.asyncio
async def test_completed_services_only_are_grouped_with_original_prices_and_pagination(management_db):
    d = management_db
    person = await customer(d)
    old = await appointment(
        d, person["id"], status="completed",
        start_at=d["now"] - timedelta(days=7), amount=7000)
    recent = await appointment(
        d, person["id"], status="completed",
        start_at=d["now"] - timedelta(days=1), amount=10000)
    await appointment(
        d, person["id"], status="confirmed",
        start_at=d["now"] + timedelta(days=2), amount=9000)
    await appointment(
        d, person["id"], status="cancelled",
        start_at=d["now"] - timedelta(days=3), amount=15000)
    await appointment(
        d, person["id"], status="no_show",
        start_at=d["now"] - timedelta(days=4), amount=4500)

    addon = uuid4()
    async with d["migrator"].begin() as db:
        await db.execute(text("""
            INSERT INTO services (id,tenant_id,name,duration_min,price_cents)
            VALUES (:service,:tenant,'Barba adicional',15,3000)
        """), {"service": addon, "tenant": d["a"]})
        await db.execute(text("""
            UPDATE appointment_items
            SET price_snapshot_cents=7000
            WHERE tenant_id=:tenant AND appointment_id=:booking
        """), {"tenant": d["a"], "booking": UUID(recent)})
        await db.execute(text("""
            INSERT INTO appointment_items (
                tenant_id,appointment_id,service_id,name_snapshot,
                duration_snapshot_min,price_snapshot_cents,
                prep_snapshot_min,cleanup_snapshot_min,item_order
            ) VALUES (:tenant,:booking,:service,'Barba adicional',15,3000,0,0,1)
        """), {"tenant": d["a"], "booking": UUID(recent), "service": addon})
        await db.execute(text("""
            UPDATE appointments SET total_cents=8500, original_total_cents=10000,
                                    discount_cents=1500 WHERE id=:booking
        """), {"booking": UUID(recent)})
        await db.execute(text("""
            UPDATE services SET name='Nome novo do catálogo',price_cents=3400
            WHERE id=:service
        """), {"service": addon})

    url = d["base"] + "/customers/" + person["id"] + "/services"
    page1 = await d["client"].get(url, params={"limit": 1, "offset": 0})
    page2 = await d["client"].get(url, params={"limit": 1, "offset": 1})
    assert page1.status_code == page2.status_code == 200
    newer, older = page1.json(), page2.json()
    assert newer["total"] == older["total"] == 2
    assert newer["limit"] == 1 and newer["offset"] == 0
    assert [row["id"] for row in newer["items"] + older["items"]] == [recent, old]
    row = newer["items"][0]
    assert row["professional_name"] == "Profissional de teste"
    assert row["total_cents"] == 8500 and row["original_total_cents"] == 10000
    assert row["discount_cents"] == 1500
    assert [service["name"] for service in row["services"]] == ["Corte sintético", "Barba adicional"]
    assert [service["price_cents"] for service in row["services"]] == [7000, 3000]
    assert [service["duration_min"] for service in row["services"]] == [30, 15]
    assert older["items"][0]["services"][0]["name"] == "Corte sintético"
    assert (await d["client"].get(url, params={"limit": 1, "offset": 2})).json()["items"] == []

    detail = (await d["client"].get(d["base"] + "/customers/" + person["id"])).json()
    assert detail["completed_appointments"] == 2
    assert detail["total_spent_cents"] == 15500


@pytest.mark.asyncio
async def test_service_history_read_scopes_and_limits(management_db):
    d = management_db
    person = await customer(d)
    other = await customer(d, name="Cliente sem visitas")
    await appointment(d, person["id"], status="completed",
                      start_at=d["now"] - timedelta(days=1), amount=3500)
    base = d["base"] + "/customers/" + person["id"] + "/services"
    viewer = {"Authorization": "Bearer " + "management-test-viewer-token-of-at-least-32-characters"}
    readonly = await d["client"].get(base, headers=viewer)
    assert readonly.status_code == 200
    assert readonly.json()["total"] == 1
    assert "phone" not in readonly.json()["items"][0]
    assert (await d["client"].get(d["base"] + "/customers/" + other["id"] + "/services")).json()["total"] == 0
    assert (await d["client"].get(d["other_base"] + "/customers/" + person["id"] + "/services")).status_code == 404
    assert (await d["client"].get(d["base"] + "/customers/" + str(uuid4()) + "/services")).status_code == 404
    assert (await d["client"].get(base, params={"limit": 0})).status_code == 422
    assert (await d["client"].get(base, params={"limit": 21})).status_code == 422
    assert (await d["client"].get(base, params={"offset": -1})).status_code == 422
    assert (await d["client"].get(base, params={"offset": 999999})).json()["items"] == []
