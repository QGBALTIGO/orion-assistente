"""Independent dispatch guards: a disabled operation never enters its flow."""
from uuid import uuid4

import pytest

from app import qr_ai
from app.business_profile_schemas import BusinessProfile


class MemoryRedis:
    def __init__(self):
        self.values = {}

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value, **_):
        self.values[key] = value

    async def delete(self, key):
        self.values.pop(key, None)


@pytest.mark.asyncio
@pytest.mark.parametrize("phrase", ["REMARCAR", "trocar meu horário", "Que dia tem livre?", "Quais horários estão disponíveis?"])
async def test_published_profile_without_appointments_does_not_dispatch_schedule_flows(monkeypatch, phrase):
    tenant, cut, profile_id = uuid4(), uuid4(), uuid4()
    profile = BusinessProfile(capabilities=["catalog_answers"]).model_dump(mode="json")
    calls = []

    async def facts(t):
        assert t == tenant
        return {"name": "Synthetic store", "timezone": "America/Campo_Grande", "profile_id": str(profile_id),
                "profile": profile}, [{"id": cut, "name": "Corte", "price_cents": 3500, "duration_min": 30}], []

    async def dispatched(*_, **__):
        calls.append(phrase)
        return {"answer": "Unexpected schedule flow", "step": "unexpected_dispatch"}

    monkeypatch.setattr(qr_ai, "tenant_data", facts)
    monkeypatch.setattr(qr_ai, "begin_reschedule", dispatched)
    monkeypatch.setattr(qr_ai, "answer_availability", dispatched)
    result = await qr_ai.turn(tenant, "5567999999999", phrase, MemoryRedis(), "preview:cap-review", preview=True)
    assert not calls, "Disabled schedule capability must be checked before dispatch"
    assert result["step"] == "capability_unavailable"
