"""Regress�es do motor Orion IA: data, contexto, confirma��o e isolamento."""
import json
from datetime import date, datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
import pytest

from app import qr_ai
from app.groq_ai import Intention
from app.main import app


class FakeRedis:
    def __init__(self):
        self.data = {}
    async def get(self, key):
        return self.data.get(key)
    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.data:
            return False
        self.data[key] = value
        return True
    async def delete(self, key):
        self.data.pop(key, None)


def test_relative_dates_and_time_are_independent_of_llm():
    now = datetime(2026, 10, 17, 10, 0, tzinfo=ZoneInfo("America/Campo_Grande"))
    assert qr_ai.requested_day("amanh�", "America/Campo_Grande", now) == date(2026, 10, 18)
    assert qr_ai.requested_day("depois de amanh�", "America/Campo_Grande", now) == date(2026, 10, 19)
    assert qr_ai.requested_day("18/10", "America/Campo_Grande", now) == date(2026, 10, 18)
    assert qr_ai.requested_day("31/02", "America/Campo_Grande", now) is None
    assert qr_ai.requested_time("Pode ser �s duas da tarde") == "14:00"
    assert qr_ai.requested_time("14:30") == "14:30"
    assert qr_ai.format_money(5500) == "R$ 55,00"


@pytest.mark.parametrize("phrase, expected", [
    ("Quero pra segunda", date(2026, 10, 19)),
    ("ter�a-feira", date(2026, 10, 20)),
    ("Quero na quarta", date(2026, 10, 21)),
    ("quinta", date(2026, 10, 22)), ("sexta", date(2026, 10, 23)),
    ("s�bado", date(2026, 10, 17)), ("domingo", date(2026, 10, 18)),
    ("pr�ximo s�bado", date(2026, 10, 24)),
    ("segunda ou ter�a", None), ("segunda op��o", None),
    ("segunda, 20/10", None),
])
def test_weekdays_use_business_timezone_and_ambiguity_is_not_first_match(phrase, expected):
    now = datetime(2026, 10, 17, 10, 0, tzinfo=ZoneInfo("America/Campo_Grande"))
    assert qr_ai.requested_day(phrase, "America/Campo_Grande", now) == expected


@pytest.mark.asyncio
async def test_preview_from_natural_language_to_confirm_never_writes(monkeypatch):
    tenant = uuid4()
    corte, barba = uuid4(), uuid4()
    professional = uuid4()
    redis = FakeRedis()
    async def data(_):
        return (
            {"name": "Barbearia do Jo�o", "timezone": "America/Campo_Grande"},
            [{"id": corte, "name": "Corte", "duration_min": 40, "price_cents": 3500},
             {"id": barba, "name": "Barba", "duration_min": 20, "price_cents": 2000}],
            [{"id": professional, "name": "Jo�o"}],
        )
    async def classify(**_):
        return Intention(intent="agendar", service_ids=[corte, barba],
                         requested_when="amanh�")
    async def pros(*_):
        return [{"id": professional, "name": "Jo�o"}]
    async def slots(_tenant, state, _preview):
        state.update(step="time", duration=60, price=5500,
                     slots=["2026-10-18T14:00:00-04:00"])
        return "1. 14:00", state
    async def forbid(*args, **kwargs):
        raise AssertionError("Preview n�o deve chamar motor de escrita")
    monkeypatch.setattr(qr_ai, "tenant_data", data)
    monkeypatch.setattr(qr_ai, "interpret", classify)
    monkeypatch.setattr(qr_ai, "professionals_for", pros)
    monkeypatch.setattr(qr_ai, "offer_slots", slots)
    monkeypatch.setattr(qr_ai, "book_appointment", forbid)
    monkeypatch.setattr(qr_ai, "confirm_hold", forbid)
    key = "preview:session-unique"
    first = await qr_ai.turn(tenant, "5567999999999",
                             "Quero corte e barba amanh�", redis, key, preview=True)
    assert first["step"] == "time"
    assert "55,00" in first["answer"]
    second = await qr_ai.turn(tenant, "5567999999999", "14:00", redis, key, preview=True)
    assert second["step"] == "confirm"
    assert "SIM ou N�O" in second["answer"]
    third = await qr_ai.turn(tenant, "5567999999999", "sim", redis, key, preview=True)
    assert third["step"] == "preview_complete"
    assert "Nenhum agendamento real" in third["answer"]


@pytest.mark.asyncio
async def test_intent_human_pauses_auto_response(monkeypatch):
    tenant = uuid4()
    redis = FakeRedis()
    async def data(_):
        return {"name": "Teste", "timezone": "America/Campo_Grande"}, [
            {"id": uuid4(), "name": "Corte", "price_cents": 100, "duration_min": 15}
        ], []
    async def classify(**_):
        return Intention(intent="humano", service_ids=[], requested_when=None)
    monkeypatch.setattr(qr_ai, "tenant_data", data)
    monkeypatch.setattr(qr_ai, "interpret", classify)
    reply = await qr_ai.turn(tenant, "5567999999999", "quero um humano", redis, "person", preview=True)
    assert reply["step"] == "human"
    again = await qr_ai.turn(tenant, "5567999999999", "Oi?", redis, "person", preview=True)
    assert again["answer"] is None


@pytest.mark.asyncio
async def test_qr_webhook_rejects_missing_bridge_auth(monkeypatch):
    monkeypatch.setenv("QR_BRIDGE_TOKEN", "test-bridge-token-that-is-long-enough")
    monkeypatch.setenv("QR_AI_ENABLED", "1")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        res = await client.post("/internal/qr/ai/reply", json={
            "phone": "5567999999999", "message_id": "ABC123", "text": "Corte"
        })
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_qr_webhook_disabled_without_live_side_effects(monkeypatch):
    monkeypatch.setenv("QR_BRIDGE_TOKEN", "test-bridge-token-that-is-long-enough")
    monkeypatch.delenv("QR_AI_ENABLED", raising=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        res = await client.post("/internal/qr/ai/reply",
            headers={"X-Orion-Bridge-Key": "test-bridge-token-that-is-long-enough"},
            json={"phone": "5567999999999", "message_id": "ABC123", "text": "Corte"})
    assert res.status_code == 200
    assert res.json()["answer"] is None


@pytest.mark.asyncio
async def test_preview_requires_auth(monkeypatch):
    monkeypatch.setenv("GROQ_PREVIEW_ENABLED", "1")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        res = await client.post(
            "/api/v1/tenants/00000000-0000-4000-8000-000000000001/ai/conversation-preview",
            json={"session": "session12345", "text": "oi"}
        )
    assert res.status_code in (401, 503)
