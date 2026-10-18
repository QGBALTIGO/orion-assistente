"""QR handoff persists a conservative marketing pause, without any live send."""
from dataclasses import replace

import pytest

from app import automation_runtime, qr_ai
from test_qr_channels_scope import ingress, request, PHONE

REAL_INTERACTION = automation_runtime.interaction


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", ["Vou deixar para a equipe", "Certo! Atendimento humano", None])
async def test_any_managed_human_result_pauses_marketing_before_response(ingress, monkeypatch, answer):
    a, *_, api = ingress
    events = []
    async def interaction(tenant, phone, *, human=None):
        assert tenant == a.tenant_id and phone == "+" + PHONE
        events.append(human)
    async def turn(*args, **kwargs):
        return {"answer": answer, "step": "human"}
    monkeypatch.setattr(automation_runtime, "interaction", interaction)
    monkeypatch.setattr(qr_ai, "turn", turn)
    result = await request(api, a)
    assert result.status_code == 200 and result.json()["step"] == "human"
    assert events == [None, True]


@pytest.mark.asyncio
async def test_resume_or_menu_never_clears_marketing_pause_automatically(ingress, monkeypatch):
    a, *_, api = ingress
    events = []
    async def interaction(tenant, phone, *, human=None): events.append(human)
    async def turn(*args, **kwargs): return {"answer": "Atendimento retomado", "step": "menu"}
    monkeypatch.setattr(automation_runtime, "interaction", interaction)
    monkeypatch.setattr(qr_ai, "turn", turn)
    assert (await request(api, a, text="MENU")).status_code == 200
    assert events == [None] and False not in events


@pytest.mark.asyncio
async def test_failed_durable_pause_never_advertises_handoff_or_consumes_message(ingress, monkeypatch):
    a, _, _, redis, *_, api = ingress
    async def interaction(tenant, phone, *, human=None):
        if human: raise TimeoutError("synthetic unavailable")
    async def turn(*args, **kwargs): return {"answer": "Handoff", "step": "human"}
    monkeypatch.setattr(automation_runtime, "interaction", interaction)
    monkeypatch.setattr(qr_ai, "turn", turn)
    result = await request(api, a)
    assert result.status_code == 503 and "Handoff" not in result.text and not redis.transactions


@pytest.mark.asyncio
async def test_revoked_channel_cannot_persist_human_pause(ingress, monkeypatch):
    a, _, identities, _, *_, api = ingress
    events = []
    async def interaction(tenant, phone, *, human=None): events.append(human)
    async def turn(*args, **kwargs):
        identities[str(a.channel_id)] = replace(a, fence=2)
        return {"answer": "Handoff", "step": "human"}
    monkeypatch.setattr(automation_runtime, "interaction", interaction)
    monkeypatch.setattr(qr_ai, "turn", turn)
    assert (await request(api, a)).status_code == 409 and events == [None]


@pytest.mark.asyncio
async def test_preview_handoff_has_no_marketing_side_effect(ingress, monkeypatch):
    a = ingress[0]
    async def interaction(*args, **kwargs): raise AssertionError("preview cannot alter marketing")
    async def turn(*args, **kwargs): return {"answer": "Simulação", "step": "human"}
    monkeypatch.setattr(automation_runtime, "interaction", interaction)
    monkeypatch.setattr(qr_ai, "turn", turn)
    result = await qr_ai.run_locked(a.tenant_id, PHONE, "Quero humano", "preview:synthetic", "id-preview", preview=True)
    assert result["step"] == "human"


@pytest.mark.asyncio
async def test_legacy_handoff_also_pauses_without_new_scope(ingress, monkeypatch):
    a = ingress[0]; events = []
    async def interaction(tenant, phone, *, human=None): events.append(human)
    async def turn(*args, **kwargs): return {"answer": "Handoff", "step": "human"}
    monkeypatch.setattr(automation_runtime, "interaction", interaction)
    monkeypatch.setattr(qr_ai, "turn", turn)
    result = await qr_ai.run_locked(a.tenant_id, PHONE, "Quero humano", PHONE, "id-legacy")
    assert result["step"] == "human" and events == [None, True]


@pytest.mark.asyncio
async def test_disabled_marketing_handoff_does_not_touch_new_schema(ingress, monkeypatch):
    a = ingress[0]
    monkeypatch.setenv("MARKETING_EXECUTION_ENABLED", "0")
    # Restore the actual implementation; an unapplied schema must be untouched.
    def forbidden(): raise AssertionError("disabled execution opened database")
    async def turn(*args, **kwargs): return {"answer": "Handoff", "step": "human"}
    monkeypatch.setattr(automation_runtime, "interaction", REAL_INTERACTION)
    monkeypatch.setattr(automation_runtime, "get_session_factory", forbidden)
    monkeypatch.setattr(qr_ai, "turn", turn)
    assert (await qr_ai.run_locked(a.tenant_id, PHONE, "Humano", PHONE, "id-off"))["step"] == "human"
