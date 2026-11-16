"""Regressões do atendimento: memória estruturada, desvio de contexto e remarcação."""
import json
from datetime import datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi import HTTPException

from app import qr_ai
from app.groq_ai import GroqServiceError
from app.main import app
from app.qr_behaviors import control_intent, catalog_match
from app.qr_customer import supplied_name


class MemoryRedis:
    def __init__(self):
        self.values = {}
    async def get(self, key):
        return self.values.get(key)
    async def set(self, key, value, ex=None):
        self.values[key] = value
    async def delete(self, key):
        self.values.pop(key, None)


def test_requested_name_only_explicit_and_control_intents():
    assert supplied_name("Me chamo Maria de Souza!") == "Maria de Souza"
    assert supplied_name("Meu nome é João, quero corte") == "João"
    assert supplied_name("Nossa, João é legal") is None
    assert control_intent("Quero REMARCAR meu horário") == "reschedule"
    assert control_intent("Tenho agendamento?") == "my_bookings"
    assert control_intent("Seu bot é um idiota") == "hostile"
    assert control_intent("e aí!") == "greeting"


def test_catalog_fallback_matches_only_existing_explicit_names():
    haircut, beard = uuid4(), uuid4()
    rows = [{"id": haircut, "name": "Corte"}, {"id": beard, "name": "Barba"}]
    assert catalog_match("Quero corte e barba", rows) == [haircut, beard]
    assert catalog_match("faça tatuagem agora", rows) == []


@pytest.mark.asyncio
async def test_greeting_stays_short_and_accepts_a_natural_service_reply_without_llm(monkeypatch):
    tenant, service = uuid4(), uuid4()
    redis = MemoryRedis()
    async def data(_):
        assert _ == tenant
        return ({"name": "Salão Teste", "timezone": "America/Campo_Grande"},
                [{"id": service, "name": "Corte", "duration_min": 30, "price_cents": 2500}], [])
    async def must_not_call(**_):
        raise AssertionError("Não chamar provedor para saudação simples")
    monkeypatch.setattr(qr_ai, "tenant_data", data)
    monkeypatch.setattr(qr_ai, "interpret", must_not_call)
    monkeypatch.setattr(qr_ai, "interpret_plan", must_not_call)
    async def no_effect(*args, **kwargs):
        raise AssertionError("Saudação e escolha de serviço não autorizam escrita ou captura de nome")
    for name in ("book_appointment", "confirm_hold", "reschedule_appointment", "remember_name", "list_slots"):
        monkeypatch.setattr(qr_ai, name, no_effect)
    first = await qr_ai.turn(tenant, "5567999999999", "oi", redis, "preview:test", True)
    assert first["step"] == "greeting" and first["answer"].startswith("Oi!")
    assert "Como posso ajudar" in first["answer"] and len(first["answer"]) <= 240
    assert "R$" not in first["answer"] and "Corte" not in first["answer"] and "_catalog_page" not in first
    key = f"orion:qr:ai:{tenant}:preview:test"
    state = json.loads(redis.values[key])
    assert state["step"] == "menu" and state["menu_ids"] == [str(service)] and state["service_reply_expected"]
    assert set(state) == {"step", "menu_ids", "service_reply_expected", "history"}
    assert state["history"] == [{"role": "user", "content": "oi"}, {"role": "assistant", "content": first["answer"]}]
    chosen = await qr_ai.turn(tenant, "5567999999999", "Corte", redis, "preview:test", True)
    assert chosen["step"] == "date" and json.loads(redis.values[key])["services"] == [str(service)]
    assert not set(json.loads(redis.values[key])) & {"hold", "hold_snapshot", "expected_snapshot", "consent",
        "appointment", "confirmation_summary", "confirmation_message_id", "confirmation_expires_at"}


@pytest.mark.asyncio
async def test_model_failure_uses_trusted_catalog_without_fake_answer(monkeypatch):
    tenant, service = uuid4(), uuid4()
    redis = MemoryRedis()
    async def data(_):
        return ({"name": "Salão Teste", "timezone": "America/Campo_Grande"},
                [{"id": service, "name": "Corte", "duration_min": 30, "price_cents": 2500}], [])
    async def fail(**_):
        raise GroqServiceError(502, "provider_error")
    monkeypatch.setattr(qr_ai, "tenant_data", data)
    monkeypatch.setattr(qr_ai, "interpret", fail)
    res = await qr_ai.turn(tenant, "5567999999999", "Preciso de maquiagem", redis, "preview:unknown", True)
    assert res["degraded"] and res["step"] == "menu"
    assert "IA está temporariamente indisponível" in res["answer"]
    assert "maquiagem" not in res["answer"].lower()
    matched = await qr_ai.turn(tenant, "5567999999999", "Quero corte", redis, "preview:known", True)
    assert matched["step"] == "date"


@pytest.mark.asyncio
async def test_history_lookup_does_not_cross_phone_scope(monkeypatch):
    tenant, service = uuid4(), uuid4()
    redis = MemoryRedis()
    async def data(_):
        return ({"name": "Salão Teste", "timezone": "America/Campo_Grande"},
                [{"id": service, "name": "Corte", "duration_min": 30, "price_cents": 2500}], [])
    seen = []
    async def history(t, p, limit=8):
        seen.append((t,p))
        return [{"id": uuid4(), "start_at": datetime.now(ZoneInfo("America/Campo_Grande")),
                 "status": "confirmed", "protocol": "AG-ABC123"}]
    monkeypatch.setattr(qr_ai, "tenant_data", data)
    monkeypatch.setattr(qr_ai, "customer_appointments", history)
    result = await qr_ai.turn(tenant, "5567999999999",
                              "Tenho agendamento?", redis, "5567999999999")
    assert "AG-ABC123" in result["answer"]
    assert seen == [(tenant, "5567999999999")]


@pytest.mark.asyncio
async def test_remarking_uses_a_fresh_summary_and_cannot_write_in_preview(monkeypatch):
    tenant, service, pro, appointment = uuid4(), uuid4(), uuid4(), uuid4()
    redis = MemoryRedis()
    async def data(_):
        return ({"name": "Salão Teste", "timezone": "America/Campo_Grande"},
                [{"id": service, "name": "Corte", "duration_min": 30, "price_cents": 2500}], [])
    async def slots(_t, state, preview):
        state.update(step="time", slots=["2026-11-20T14:00:00-04:00"],
                     duration=30, price=2500)
        return "1. 14:00", state
    async def forbid(*args, **kwargs):
        raise AssertionError("Simulação não pode alterar agendamento")
    monkeypatch.setattr(qr_ai, "tenant_data", data)
    monkeypatch.setattr(qr_ai, "offer_slots", slots)
    for name in ("reschedule_appointment", "book_appointment", "confirm_hold", "remember_name"):
        monkeypatch.setattr(qr_ai, name, forbid)
    state = {"step": "reschedule_day", "services": [str(service)],
             "professional": str(pro), "appointment": str(appointment), "customer_name": "Lucas"}
    k = f"orion:qr:ai:{tenant}:preview:fake"
    redis.values[k] = json.dumps(state)
    business, catalog, professionals = await data(tenant)
    redis.values[k + ":catalog-context"] = qr_ai.qr_profile.catalog_revision(business, catalog, professionals)
    a = await qr_ai.turn(tenant, "5567999999999", "20/11", redis, "preview:fake", True)
    assert a["step"] == "reschedule_time"
    b = await qr_ai.turn(tenant, "5567999999999", "14:00", redis, "preview:fake", True)
    assert b["step"] == "reschedule_confirm"
    proposal_identity = json.loads(redis.values[k])["confirmation_message_id"]
    c = await qr_ai.turn(tenant, "5567999999999", "não", redis, "preview:fake", True)
    assert c["step"] == "request_closed" and "encerrei esse pedido" in c["answer"]
    closed = json.loads(redis.values[k])
    assert closed["step"] == "menu" and closed["customer_name"] == "Lucas"
    assert set(closed) == {"step", "customer_name", "history"}
    assert 0 < len(closed["history"]) <= 8 and closed["history"][-2:] == [
        {"role": "user", "content": "não"}, {"role": "assistant", "content": c["answer"]}]
    assert all(set(item) == {"role", "content"} and item["role"] in {"user", "assistant"}
        and len(item["content"]) <= 1500 for item in closed["history"])
    assert not set(closed) & {"hold", "hold_snapshot", "expected_snapshot", "consent", "appointment", "new_start",
        "professional", "services", "confirmation_summary", "confirmation_message_id", "confirmation_expires_at"}
    returned = await qr_ai.turn(tenant, "5567999999999", "Oi", redis, "preview:fake", True)
    assert returned["step"] == "greeting" and "Oi, Lucas!" in returned["answer"] and "R$" not in returned["answer"]
    assert not set(json.loads(redis.values[k])) & {"hold", "hold_snapshot", "expected_snapshot", "consent", "appointment",
        "confirmation_summary", "confirmation_message_id", "confirmation_expires_at", "new_start"}
    redis.values[k] = json.dumps({**state, "step": "reschedule_confirm",
                                   "new_start": "2026-11-20T14:00:00-04:00",
                                   "confirmation_summary": b["answer"],
                                   "confirmation_message_id": proposal_identity,
                                   "confirmation_expires_at": datetime.now().timestamp() + 240})
    done = await qr_ai.turn(tenant, "5567999999999", "sim", redis, "preview:fake", True)
    assert done["step"] == "preview_complete"


@pytest.mark.asyncio
async def test_unlisted_numbers_never_get_automated_responses(monkeypatch):
    monkeypatch.setenv("QR_BRIDGE_TOKEN", "test-bridge-token-that-is-long-enough")
    monkeypatch.setenv("QR_AI_ENABLED", "1")
    monkeypatch.delenv("QR_AI_ALLOWED_SENDERS", raising=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        res = await client.post("/internal/qr/ai/reply",
            headers={"X-Orion-Bridge-Key": "test-bridge-token-that-is-long-enough"},
            json={"phone": "5567999999999", "message_id": "ABC123", "text": "Corte"})
    assert res.status_code == 200
    assert res.json()["step"] == "not_allowlisted"


@pytest.mark.asyncio
async def test_brazilian_mobile_requires_ninth_digit(monkeypatch):
    monkeypatch.setenv("QR_BRIDGE_TOKEN", "test-bridge-token-that-is-long-enough")
    monkeypatch.setenv("QR_AI_ENABLED", "1")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        res = await client.post("/internal/qr/ai/reply",
            headers={"X-Orion-Bridge-Key": "test-bridge-token-that-is-long-enough"},
            json={"phone": "552199755364", "message_id": "ABC123", "text": "Oi"})
    assert res.status_code == 422
