"""PostgreSQL 16 real: série atômica, conflitos, recorrência e isolamento por empresa."""
from datetime import datetime, time, timedelta, timezone
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from fastapi import FastAPI
import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import booking_api, recurring_appointments
from app.catalog_api import Actor, authenticate
from app.database import tenant_transaction
from test_management import management_db, customer, appointment

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def recurring_db(management_db, monkeypatch):
    d = management_db
    app = d["app"]
    app.include_router(recurring_appointments.router)
    app.include_router(booking_api.router)
    # Match main.py: concrete booking endpoints precede the generic catalog route.
    concrete = [r for r in app.router.routes
                if getattr(r, 'path', None) == '/api/v1/tenants/{tenant_id}/appointments'
                and 'GET' in getattr(r, 'methods', set())]
    for route in concrete:
        app.router.routes.remove(route)
        app.router.routes.insert(0, route)
    factory = async_sessionmaker(d["runtime"], expire_on_commit=False)
    monkeypatch.setattr(recurring_appointments, "get_session_factory", lambda: factory)
    monkeypatch.setattr(booking_api, "get_session_factory", lambda: factory)
    app.dependency_overrides[authenticate] = lambda: Actor("member", d["a"], d["account"], "owner")
    zone = ZoneInfo("America/Sao_Paulo")
    today = d["now"].astimezone(zone).date()
    first = today + timedelta(days=8)
    async with d["migrator"].begin() as db:
        await db.execute(text("""
            INSERT INTO working_hours(tenant_id,professional_id,weekday,start_time,end_time)
            VALUES(:tenant,:pro,:weekday,'08:00','18:00')
        """), {"tenant": d["a"], "pro": d["pro"], "weekday": first.weekday()})
        await db.execute(text("""
            INSERT INTO professional_services(tenant_id,professional_id,service_id)
            VALUES(:tenant,:pro,:service)
        """), {"tenant": d["a"], "pro": d["pro"], "service": d["service"]})
    try:
        yield {**d, "first": first, "zone": zone, "base_series": f"/api/v1/tenants/{d['a']}/appointments/series"}
    finally:
        async with d["migrator"].begin() as db:
            await db.execute(text("DELETE FROM usage_counters WHERE tenant_id=:tenant"), {"tenant": d["a"]})


def payload(d, **changes):
    data = {
        "professional_id": str(d["pro"]), "service_ids": [str(d["service"])],
        "customer_name": "Cliente fixo sintético", "customer_phone": "+5511999990060",
        "first_date": d["first"].isoformat(), "weekday": d["first"].weekday(),
        "local_time": "10:00", "interval_weeks": 1, "occurrence_count": 3,
        "idempotency_key": str(uuid4()),
    }
    data.update(changes)
    return data


@pytest.mark.asyncio
async def test_atomic_series_preview_creation_replay_and_stop(recurring_db):
    d = recurring_db
    body = payload(d)
    preview = await d["client"].post(d["base_series"] + "/preview",
                                     json={key: value for key, value in body.items() if key != "idempotency_key"})
    assert preview.status_code == 200, preview.text
    before = preview.json()
    assert before["available_count"] == 3 and before["conflict_count"] == 0
    assert before["duration_min"] == 30 and before["price_cents"] == 10000
    assert [row["day"] for row in before["occurrences"]] == [
        (d["first"] + timedelta(days=7*i)).isoformat() for i in range(3)]

    response = await d["client"].post(d["base_series"], json=body)
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["replayed"] is False and result["confirmed_count"] == 3
    assert len(result["appointments"]) == 3
    assert [x["series_index"] for x in result["appointments"]] == [1, 2, 3]
    assert all(x["status"] == "confirmed" and x["protocol"].startswith("AG-") for x in result["appointments"])

    replay = await d["client"].post(d["base_series"], json=body)
    assert replay.status_code == 200 and replay.json()["replayed"] is True
    assert replay.json()["id"] == result["id"]
    changed = await d["client"].post(d["base_series"], json={**body, "occurrence_count": 4})
    assert changed.status_code == 409

    list_response = await d["client"].get(d["base_series"])
    assert list_response.status_code == 200
    assert len(list_response.json()["items"]) == 1

    ordinary = await d["client"].get(f"/api/v1/tenants/{d['a']}/appointments",
                                     params={"day": d["first"].isoformat()})
    assert ordinary.status_code == 200, ordinary.text
    assert ordinary.json()[0]["series_id"] == result["id"]

    async with factory_session(d) as db:
        async with tenant_transaction(db, d["b"]):
            count = (await db.execute(text("SELECT count(*) FROM appointment_series"))).scalar_one()
            assert count == 0

    stop = await d["client"].post(d["base_series"] + f"/{result['id']}/stop", json={})
    assert stop.status_code == 200, stop.text
    assert stop.json()["cancelled_count"] == 3
    assert all(x["status"] == "cancelled" for x in stop.json()["series"]["appointments"])
    assert stop.json()["series"]["closed_at"] is not None
    again = await d["client"].post(d["base_series"] + f"/{result['id']}/stop", json={})
    assert again.status_code == 200 and again.json()["cancelled_count"] == 0
    async with d["migrator"].connect() as db:
        count = (await db.execute(text("""
            SELECT count(*) FROM appointment_history
            WHERE tenant_id=:tenant AND appointment_id=ANY(
                SELECT id FROM appointments WHERE series_id=:series)
        """), {"tenant": d["a"], "series": UUID(result["id"])})).scalar_one()
        assert count == 6   # 3 confirmations and 3 cancellations


def factory_session(d):
    return async_sessionmaker(d["runtime"], expire_on_commit=False)()


@pytest.mark.asyncio
async def test_existing_booking_conflict_aborts_every_recurring_visit(recurring_db):
    d = recurring_db
    client = await customer(d, phone="11976543210")
    await appointment(d, client["id"], start_at=datetime.combine(d["first"], time(10), d["zone"]))
    body = payload(d)
    preview = await d["client"].post(d["base_series"] + "/preview",
                                     json={key: value for key, value in body.items() if key != "idempotency_key"})
    assert preview.status_code == 200, preview.text
    assert preview.json()["conflict_count"] == 1
    assert preview.json()["available_count"] == 2
    assert preview.json()["can_create"] is False

    creation = await d["client"].post(d["base_series"], json=body)
    assert creation.status_code == 409, creation.text
    async with d["migrator"].connect() as db:
        series_count = (await db.execute(text("""
            SELECT count(*) FROM appointment_series WHERE tenant_id=:tenant
        """), {"tenant": d["a"]})).scalar_one()
        appointments = (await db.execute(text("""
            SELECT count(*) FROM appointments WHERE tenant_id=:tenant
        """), {"tenant": d["a"]})).scalar_one()
    assert series_count == 0 and appointments == 1


@pytest.mark.asyncio
async def test_other_company_cannot_read_fixed_client_details(recurring_db):
    d = recurring_db
    created = await d["client"].post(d["base_series"], json=payload(d))
    assert created.status_code == 201, created.text
    target = f"/api/v1/tenants/{d['b']}/appointments/series/{created.json()['id']}"
    forbidden = await d["client"].get(target)
    assert forbidden.status_code == 403
