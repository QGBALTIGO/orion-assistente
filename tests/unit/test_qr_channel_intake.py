"""Managed notices precede ASR; stale inference never becomes fresh consent."""
import asyncio
from dataclasses import replace
import json

import httpx
import pytest

from app import qr_ai, qr_channel_scope as scope, qr_intake_revision as revision
from test_qr_channels_scope import ingress, PHONE
from test_qr_multi_flow import flow  # noqa: F401

REAL_TURN = qr_ai.turn


async def post(api, identity, action, body):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="https://test") as client:
        return await client.post(f"/internal/qr/channels/{identity.channel_id}/{action}", json=body)


async def announce(api, identity, message):
    return await post(api, identity, "interaction", {"phone": PHONE, "message_id": message})


async def reply(api, identity, ids, value, **extra):
    return await post(api, identity, "ai/reply", {"phone": PHONE, "message_id": ids[0], "message_ids": ids,
        "intake_revision": value, "text": "Pode confirmar", **extra})


@pytest.mark.asyncio
async def test_notice_returns_scoped_revision_before_asr_even_with_assistant_off(ingress):
    a, b, identities, redis, calls, interactions, _, api = ingress
    identities[str(a.channel_id)] = replace(a, assistant_enabled=False)
    first = await announce(api, a, "audio-first")
    duplicate = await announce(api, a, "audio-first")
    second = await announce(api, a, "text-correction")
    other = await announce(api, b, "same-correction")
    assert [v.json() for v in (first, duplicate, second, other)] == [
        {"recorded": True, "revision": 1}, {"recorded": True, "revision": 1},
        {"recorded": True, "revision": 2}, {"recorded": True, "revision": 1}]
    assert not calls and len(interactions) == 3 and scope.current() is None
    assert len({event[1] for event in redis.events}) == 2


@pytest.mark.asyncio
async def test_text_notice_during_audio_inference_stops_first_write_and_restores_facts(ingress, monkeypatch):
    a, _, _, redis, _, _, _, api = ingress
    first = await announce(api, a, "audio-first")
    entered, release = asyncio.Event(), asyncio.Event()
    state_key = f"orion:qr:ai:{a.tenant_id}:{a.context_namespace}:{PHONE}"
    original = {"step": "confirm", "services": ["synthetic-cut"], "day": "2030-01-08", "professional": "synthetic-professional"}
    await redis.set(state_key, json.dumps(original), ex=300)
    writes = []
    async def delayed(*args, **kwargs):
        await redis.set(state_key, json.dumps({"step": "human"}), ex=100)
        entered.set(); await release.wait()
        await revision.check_before_write()
        writes.append("must not confirm")
        return {"answer": "should not escape", "step": "confirmed"}
    monkeypatch.setattr(qr_ai, "turn", delayed)
    task = asyncio.create_task(reply(api, a, ["audio-first"], first.json()["revision"]))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        newer = await announce(api, a, "text-correction")
        assert newer.json()["revision"] == 2
        release.set()
        response = await asyncio.wait_for(task, 3)
    finally:
        release.set()
        if not task.done(): task.cancel(); await asyncio.gather(task, return_exceptions=True)
    assert response.json() == {"answer": None, "step": "superseded", "not_committed": True}
    assert not writes and not redis.transactions and json.loads(redis.values[state_key]) == original
    combined = []
    async def consume(tenant, phone, body, *_args, **kwargs):
        await revision.assert_current()
        combined.append(body)
        assert json.loads(redis.values[state_key]) == original
        return {"answer": "Novo pedido precisa de novo resumo", "step": "review"}
    monkeypatch.setattr(qr_ai, "turn", consume)
    result = await reply(api, a, ["audio-first", "text-correction"], 2,
                         text="Quero corte com Bruno terça às 15h\nPera, melhor quarta às 16h")
    assert result.json()["step"] == "review" and len(combined) == 1 and not writes
    assert any(":done:" in key and "audio-first" in key for key in redis.values)


@pytest.mark.asyncio
@pytest.mark.parametrize("revision_value,ids", [(99999, ["announced"]), (1, ["announced", "never-announced"])])
async def test_caller_revision_or_unannounced_batch_cannot_register_itself(ingress, revision_value, ids):
    a, _, _, redis, calls, _, _, api = ingress
    assert (await announce(api, a, "announced")).json()["revision"] == 1
    before = list(redis.events)
    response = await reply(api, a, ids, revision_value)
    assert response.json() == {"answer": None, "step": "superseded", "not_committed": True}
    assert redis.events == before and not calls and not redis.transactions


@pytest.mark.asyncio
async def test_missing_notice_revision_fails_closed_without_assigning_one(ingress):
    a, _, _, redis, calls, _, _, api = ingress
    response = await post(api, a, "ai/reply", {"phone": PHONE, "message_id": "unannounced", "text": "Pode confirmar"})
    assert response.status_code == 422 and not redis.events and not calls and not redis.transactions


@pytest.mark.asyncio
async def test_other_channels_notice_does_not_supersede_own_valid_revision(ingress):
    a, b, _, _, calls, _, _, api = ingress
    await announce(api, a, "own")
    await announce(api, b, "other-one"); await announce(api, b, "other-two")
    response = await reply(api, a, ["own"], 1)
    assert response.json()["step"] == "confirmed" and len(calls) == 1


@pytest.mark.asyncio
async def test_new_notice_after_write_started_never_acknowledges_replayable_zero_write(ingress, monkeypatch):
    a, _, _, redis, _, _, _, api = ingress
    await announce(api, a, "first")
    writes = []
    async def committed(*args, **kwargs):
        await revision.check_before_write(); writes.append("known synthetic commit")
        assert (await announce(api, a, "late-correction")).status_code == 200
        return {"answer": "Operação sintética concluída", "step": "confirmed", "_verified_operation": True}
    monkeypatch.setattr(qr_ai, "turn", committed)
    response = await reply(api, a, ["first"], 1)
    assert response.json() == {"answer": None, "step": "superseded_after_write", "not_committed": False}
    assert len(writes) == 1 and redis.transactions
    assert (await reply(api, a, ["first"], 1)).json()["step"] == "duplicate" and len(writes) == 1


@pytest.mark.asyncio
async def test_actual_turn_keeps_known_facts_but_stale_audio_cannot_confirm_then_correction_is_draft(flow, ingress, monkeypatch):
    from datetime import datetime
    h = flow
    old, _, identities, redis, _, _, _, api = ingress
    a = replace(old, tenant_id=h.tenant)
    identities[str(a.channel_id)] = a
    redis.values = h.redis.data
    key = f"orion:qr:ai:{a.tenant_id}:{a.context_namespace}:{PHONE}"
    seed = h.seed("confirm", services=[str(h.cut), str(h.beard)], hold=str(h.appointment), price=5500, duration=60,
        confirmation_summary="Corte e Barba com Bruno às15h", confirmation_expires_at=datetime.now().timestamp()+240)
    await redis.set(key, json.dumps(seed), ex=300)
    await redis.set(key+":catalog-context", h.redis.data[h.key+":catalog-context"], ex=300)
    monkeypatch.setattr(qr_ai, "turn", REAL_TURN)
    assert (await announce(api, a, "audio-confirm")).json()["revision"] == 1
    assert (await announce(api, a, "text-change")).json()["revision"] == 2
    stale = await reply(api, a, ["audio-confirm"], 1, text="Pode confirmar")
    assert stale.json() == {"answer": None, "step": "superseded", "not_committed": True}
    assert not h.calls["writes"] and json.loads(redis.values[key]) == seed
    combined = await reply(api, a, ["audio-confirm", "text-change"], 2,
        text="Pode confirmar\nPera, melhor só corte às16h30. Quanto fica?", require_summary_review=True)
    assert combined.status_code == 200 and "35,00" in combined.json()["answer"]
    state = json.loads(redis.values[key])
    assert state["services"] == [str(h.cut)] and state["professional"] == str(h.bruno) and state["day"] == h.tomorrow.isoformat()
    assert state["pending_clock"] == "16:30" and state.get("booking_draft")
    assert not any(state.get(name) for name in ("hold", "hold_snapshot", "confirmation_message_id", "confirmation_summary"))
    assert not h.calls["writes"] and not h.calls["reads"] and not h.calls["names"]


@pytest.mark.asyncio
async def test_channel_notice_keys_equal_managed_turn_and_cannot_pollute_legacy(ingress):
    a, b, _, redis, *_ = ingress
    direct = revision.channel_keys(a, PHONE)
    legacy = revision.keys(a.tenant_id, PHONE)
    assert direct != legacy and direct != revision.channel_keys(b, PHONE)
    async def guard(): return a
    token = scope.bind(a, guard)
    try:
        assert revision.keys(a.tenant_id, PHONE) == direct
    finally:
        scope.reset(token)
    assert scope.current() is None


@pytest.mark.asyncio
async def test_revocation_after_notice_registration_cannot_report_current_success(ingress, monkeypatch):
    a, _, identities, redis, calls, _, _, api = ingress
    original = revision.register_channel
    async def changed(*args):
        result = await original(*args)
        identities[str(a.channel_id)] = replace(a, fence=a.fence+1)
        return result
    monkeypatch.setattr(revision, "register_channel", changed)
    response = await announce(api, a, "before-revocation")
    assert response.status_code == 409 and "recorded" not in response.json() and not calls
    assert scope.current() is None and len(redis.events) == 1
