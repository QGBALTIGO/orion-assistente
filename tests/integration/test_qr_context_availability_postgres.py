"""Actual registered work is distinct from availability; synthetic PG tenants."""
import json
from datetime import timedelta

import pytest
from sqlalchemy import text

from app import qr_ai
from app.qr_understanding import build_plan
from test_booking import booking_db, phone, start_at

pytestmark = pytest.mark.integration


class Redis:
    def __init__(self):
        self.data = {}
    async def get(self, key):
        return self.data.get(key)
    async def set(self, key, value, ex=None):
        self.data[key] = value
    async def delete(self, key):
        self.data.pop(key, None)


async def setup(d, monkeypatch):
    async with d["migrator"].begin() as db:
        await db.execute(text("UPDATE professionals SET name='Disponivel Alpha' WHERE tenant_id=:tenant AND id=:id"),
            {"tenant": d["a"], "id": d["pro"]})
        await db.execute(text("UPDATE professionals SET name='Ocupado Bravo' WHERE tenant_id=:tenant AND id=:id"),
            {"tenant": d["a"], "id": d["pro2"]})
        await db.execute(text("""INSERT INTO schedule_blocks (tenant_id,professional_id,starts_at,ends_at)
            VALUES (:tenant,:professional,:begin,:end)"""),
            {"tenant": d["a"], "professional": d["pro2"], "begin": start_at(d["day"], 0),
             "end": start_at(d["day"]+timedelta(days=1), 0)})
    catalog = [{"id": d["cut"], "name": "Corte", "price_cents": 3500, "duration_min": 40},
               {"id": d["beard"], "name": "Barba", "price_cents": 2000, "duration_min": 20}]
    professionals = [{"id": d["pro"], "name": "Disponivel Alpha"}, {"id": d["pro2"], "name": "Ocupado Bravo"}]
    business = {"name": "Empresa sintética", "timezone": "America/Campo_Grande", "use_working_hours": True,
        "profile": {"capabilities": ["catalog_answers", "appointments"], "hours": [], "style_rules": {}}}
    async def data(tenant):
        assert tenant == d["a"]
        return business, catalog, professionals
    async def planner(**kwargs):
        return build_plan(message=kwargs["message"], catalog=kwargs["catalog"], professionals=kwargs["professionals"],
            context=kwargs["context"], faqs=kwargs["faqs"], reference_date=kwargs["reference_date"])
    async def forbidden(*args, **kwargs):
        raise AssertionError("These queries and previews cannot mutate appointments, names or consent")
    monkeypatch.setattr(qr_ai, "get_session_factory", lambda: d["factory"])
    monkeypatch.setattr(qr_ai, "tenant_data", data)
    monkeypatch.setattr(qr_ai, "interpret_plan", planner)
    for name in ("book_appointment", "confirm_hold", "remember_name", "reschedule_appointment"):
        monkeypatch.setattr(qr_ai, name, forbidden)
    monkeypatch.setenv("QR_AI_MULTI_INTENT_ENABLED", "1")
    monkeypatch.setenv("QR_AI_COMMERCE_ENABLED", "0")
    return Redis()


async def state_counts(d):
    async with d["migrator"].connect() as db:
        return {table: (await db.execute(text("SELECT count(*) FROM " + table + " WHERE tenant_id=:tenant"),
            {"tenant": d["a"]})).scalar_one() for table in ("appointments", "appointment_history", "calendar_outbox", "customers")}


@pytest.mark.asyncio
async def test_real_grid_distinguishes_working_professional_from_available_professional(booking_db, monkeypatch):
    d = booking_db; redis = await setup(d, monkeypatch)
    before = await state_counts(d)
    namespace = "preview:who-real-grid"
    question = await qr_ai.turn(d["a"], phone(881)[1:], f"Quem está disponível em {d['day']}?", redis, namespace, preview=True)
    assert "serviço" in question["answer"].lower()
    answer = await qr_ai.turn(d["a"], phone(881)[1:], "Cabelo", redis, namespace, preview=True)
    assert "Disponivel Alpha" in answer["answer"] and "Ocupado Bravo" not in answer["answer"]
    key = f"orion:qr:ai:{d['a']}:{namespace}"
    state = json.loads(redis.data[key])
    assert not state.get("services") and not state.get("hold")
    assert state["last_availability"]["day"] == d["day"].isoformat()
    assert await state_counts(d) == before


@pytest.mark.asyncio
async def test_real_combo_buffers_and_later_clock_prepare_only_preview_after_new_grid_check(booking_db, monkeypatch):
    d = booking_db; redis = await setup(d, monkeypatch)
    before = await state_counts(d)
    namespace = "preview:combo-real-grid"
    await qr_ai.turn(d["a"], phone(882)[1:], f"Quem está disponível em {d['day']}?", redis, namespace, preview=True)
    answer = await qr_ai.turn(d["a"], phone(882)[1:], "Corte de cabelo e barba", redis, namespace, preview=True)
    assert "Disponivel Alpha" in answer["answer"] and "Ocupado Bravo" not in answer["answer"]
    proposal = await qr_ai.turn(d["a"], phone(882)[1:], "Pode ser às 14.", redis, namespace, preview=True)
    assert proposal["step"] == "confirm" and "14h" in proposal["answer"] and "R$ 55,00" in proposal["answer"]
    key = f"orion:qr:ai:{d['a']}:{namespace}"
    state = json.loads(redis.data[key])
    assert state["hold"] == "preview" and state["professional"] == str(d["pro"])
    assert state["duration"] == 60 and set(state["services"]) == {str(d["cut"]), str(d["beard"])}
    assert await state_counts(d) == before
