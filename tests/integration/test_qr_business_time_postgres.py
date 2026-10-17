"""Registered working hours and a real grid, under the existing PG/RLS fixture."""
import json
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text

from app import qr_ai, qr_profile
from test_booking import booking_db, phone

pytestmark = pytest.mark.integration


class Redis:
    def __init__(self):
        self.values = {}
    async def get(self, key):
        return self.values.get(key)
    async def set(self, key, value, ex=None):
        self.values[key] = value
    async def delete(self, key):
        self.values.pop(key, None)


@pytest.mark.asyncio
async def test_registered_hours_follow_tenant_professional_and_day(booking_db, monkeypatch):
    d = booking_db
    other_day = d["day"] + timedelta(days=1)
    async with d["migrator"].begin() as db:
        await db.execute(text("""UPDATE working_hours SET start_time='09:00',end_time='18:00'
            WHERE tenant_id=:tenant AND professional_id=:pro"""), {"tenant": d["a"], "pro": d["pro"]})
        await db.execute(text("""UPDATE working_hours SET start_time='03:00',end_time='07:00'
            WHERE tenant_id=:tenant AND professional_id=:pro"""), {"tenant": d["a"], "pro": d["pro2"]})
        await db.execute(text("""INSERT INTO working_hours
            (tenant_id,professional_id,weekday,start_time,end_time)
            VALUES (:tenant,:pro,:weekday,'09:00','13:00')"""),
            {"tenant": d["a"], "pro": d["pro"], "weekday": other_day.weekday()})
    monkeypatch.setattr(qr_ai, "get_session_factory", lambda: d["factory"])
    assert await qr_ai.working_hours_for(d["a"], [d["pro"]], d["day"]) == [{"opens": "09:00", "closes": "18:00"}]
    assert await qr_ai.working_hours_for(d["a"], [d["pro2"]], d["day"]) == [{"opens": "03:00", "closes": "07:00"}]
    assert await qr_ai.working_hours_for(d["a"], [d["pro"]], other_day) == [{"opens": "09:00", "closes": "13:00"}]
    assert await qr_ai.working_hours_for(d["b"], [d["pro"]], d["day"]) == []
    assert await qr_ai.working_hours_for(d["a"], [uuid4()], d["day"]) == []


@pytest.mark.asyncio
async def test_spoken_half_hour_uses_real_work_and_grid_without_writing_a_booking(booking_db, monkeypatch):
    d = booking_db
    async with d["migrator"].begin() as db:
        await db.execute(text("""UPDATE working_hours SET start_time='09:00',end_time='18:00'
            WHERE tenant_id=:tenant AND professional_id=:pro"""), {"tenant": d["a"], "pro": d["pro"]})
    business = {"name": "Synthetic working hours", "timezone": "America/Campo_Grande", "use_working_hours": True}
    catalog = [{"id": d["cut"], "name": "Corte", "price_cents": 3500, "duration_min": 40}]
    professionals = [{"id": d["pro"], "name": "Barbeiro sintético"}]
    async def data(tenant):
        assert tenant == d["a"]
        return business, catalog, professionals
    async def forbidden(*args, **kwargs):
        raise AssertionError("A complete natural time reply in preview cannot invoke the provider or write a booking")
    monkeypatch.setattr(qr_ai, "get_session_factory", lambda: d["factory"])
    monkeypatch.setattr(qr_ai, "tenant_data", data)
    for name in ("interpret_plan", "interpret", "book_appointment", "confirm_hold", "remember_name"):
        monkeypatch.setattr(qr_ai, name, forbidden)
    monkeypatch.setenv("QR_AI_MULTI_INTENT_ENABLED", "1")
    redis = Redis()
    namespace = "preview:real-working-hours"
    key = f"orion:qr:ai:{d['a']}:{namespace}"
    redis.values[key] = json.dumps({"step": "time", "services": [str(d["cut"])],
        "day": d["day"].isoformat(), "professional": str(d["pro"]),
        "professional_name": "Barbeiro sintético", "slots": [], "slot_menu_numbered": False})
    redis.values[key + ":catalog-context"] = qr_profile.catalog_revision(business, catalog, professionals)
    result = await qr_ai.turn(d["a"], phone(871)[1:], "3 e meia", redis, namespace, preview=True)
    assert result["step"] == "confirm" and "15h30" in result["answer"] and "R$ 35" in result["answer"]
    state = json.loads(redis.values[key])
    assert state["selected_start"].endswith("15:30:00-04:00") and state["hold"] == "preview"
    assert state["professional"] == str(d["pro"]) and state["services"] == [str(d["cut"])]
    async with d["migrator"].connect() as db:
        assert (await db.execute(text("SELECT count(*) FROM appointments WHERE tenant_id=:tenant"),
            {"tenant": d["a"]})).scalar_one() == 0
