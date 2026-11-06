"""Real Redis contracts for signed-by-state assistant choices, without sends.

All keys belong to a unique synthetic namespace; no FLUSH or production URL.
Selection serialization uses the production conversation lease, as run_locked
does. These are Redis/backend tests, not native-client rendering qualification.
"""
import asyncio
import json
import os
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
import httpx
from fastapi import HTTPException
from redis.asyncio import from_url

from app import qr_assistant_interactions as choices, qr_conversation_lock

pytestmark = pytest.mark.integration
PHONE = "5567999999999"


@pytest_asyncio.fixture
async def redis_choices():
    url = os.getenv("TEST_REDIS_URL")
    if not url:
        if os.getenv("REQUIRE_REDIS_TESTS") == "1":
            pytest.fail("Disposable Redis is required")
        pytest.skip("Disposable Redis not configured")
    redis = from_url(url, decode_responses=True, socket_connect_timeout=2, socket_timeout=2)
    await redis.ping()
    tenant, namespace = uuid4(), "qualification:"+str(uuid4())
    key = f"orion:qr:ai:{tenant}:{namespace}"
    state = {"step": "confirm", "hold": str(uuid4()), "confirmation_summary": "Corte com profissional sintético às 14h, R$ 35,00",
             "confirmation_message_id": "synthetic-summary", "confirmation_expires_at": time.time()+90}
    await redis.set(key, json.dumps(state), ex=120)
    await redis.set(key+":catalog-context", "synthetic-catalog-revision", ex=120)
    result = await choices.present(redis, tenant, PHONE, namespace,
        {"answer": state["confirmation_summary"], "step": "confirm"}, choices.Capabilities(assistant_interactions=1))
    presentation = result["presentation"]
    token = presentation["action"]["buttons"][0]["id"]
    context = SimpleNamespace(redis=redis, tenant=tenant, namespace=namespace, key=key, state=state,
                              presentation=presentation, token=token, url=url)
    try:
        yield context
    finally:
        await redis.delete(key, key+":presentation", key+":catalog-context", key+":lock")
        await redis.aclose()


async def ack(h, provider="synthetic-provider"):
    return await choices.acknowledge(h.redis, h.tenant, PHONE, h.namespace,
        choices.PresentationAck(phone=PHONE, presentation_id=h.presentation["id"], provider_id=provider))


def selected(h, **changes):
    return choices.Selection(**{"id": h.token, "quoted_message_id": "synthetic-provider", **changes})


@pytest.mark.asyncio
async def test_I03_actual_redis_ack_binds_one_current_summary_and_keeps_original_deadline(redis_choices):
    h = redis_choices
    before = await h.redis.pttl(h.key+":presentation")
    assert await ack(h) == {"acknowledged": True}
    assert 0 < await h.redis.pttl(h.key+":presentation") <= before
    assert await choices.resolve(h.redis, h.tenant, PHONE, h.namespace, selected(h)) == "sim"
    assert await h.redis.get(h.key+":presentation") is None


@pytest.mark.asyncio
async def test_D09_concurrent_selection_under_real_conversation_lease_consumes_once(redis_choices):
    h = redis_choices
    await ack(h)
    async def consume():
        lock = h.redis.lock(h.key+":lock", timeout=90, blocking_timeout=3, thread_local=False)
        async with qr_conversation_lock.hold(lock):
            return await choices.resolve(h.redis, h.tenant, PHONE, h.namespace, selected(h))
    results = await asyncio.gather(consume(), consume())
    assert results.count("sim") == 1 and results.count(None) == 1


@pytest.mark.asyncio
async def test_I04_current_state_change_invalidates_original_provider_bound_button(redis_choices):
    h = redis_choices
    await ack(h)
    await h.redis.set(h.key, json.dumps({**h.state, "hold": str(uuid4())}), ex=120)
    assert await choices.resolve(h.redis, h.tenant, PHONE, h.namespace, selected(h)) is None


@pytest.mark.asyncio
async def test_I05_other_contact_cannot_resolve_or_ack_same_presentation(redis_choices):
    h = redis_choices
    await ack(h)
    assert await choices.resolve(h.redis, h.tenant, "5567999999998", h.namespace, selected(h)) is None
    with pytest.raises(HTTPException) as exc:
        await choices.acknowledge(h.redis, h.tenant, "5567999999998", h.namespace,
            choices.PresentationAck(phone="5567999999998", presentation_id=h.presentation["id"], provider_id="foreign-provider"))
    assert exc.value.status_code == 409
    assert await choices.resolve(h.redis, h.tenant, PHONE, h.namespace, selected(h)) == "sim"


@pytest.mark.asyncio
async def test_I06_copy_to_other_tenant_fails_authority_even_when_same_record_is_injected(redis_choices):
    h = redis_choices
    await ack(h)
    foreign = uuid4()
    other = f"orion:qr:ai:{foreign}:{h.namespace}"
    try:
        await h.redis.set(other, await h.redis.get(h.key), ex=120)
        await h.redis.set(other+":catalog-context", "synthetic-catalog-revision", ex=120)
        await h.redis.set(other+":presentation", await h.redis.get(h.key+":presentation"), ex=90)
        assert await choices.resolve(h.redis, foreign, PHONE, h.namespace, selected(h)) is None
    finally:
        await h.redis.delete(other, other+":catalog-context", other+":presentation")


@pytest.mark.asyncio
async def test_I09_forged_or_wrong_quoted_message_id_never_consumes_valid_choice(redis_choices):
    h = redis_choices
    await ack(h)
    assert await choices.resolve(h.redis, h.tenant, PHONE, h.namespace, selected(h, id="oa1_forged_0")) is None
    assert await choices.resolve(h.redis, h.tenant, PHONE, h.namespace, selected(h, quoted_message_id="another-provider")) is None
    assert await choices.resolve(h.redis, h.tenant, PHONE, h.namespace, selected(h)) == "sim"


@pytest.mark.asyncio
async def test_I10_new_client_preserves_pending_choice_but_new_channel_owner_is_rejected(redis_choices, monkeypatch):
    h = redis_choices
    await ack(h)
    new_client = from_url(h.url, decode_responses=True)
    changed = SimpleNamespace(channel_id=uuid4(), owner_account_id=uuid4(), fence=2,
                              lease_owner=uuid4(), preserved_session_fingerprint="synthetic-new-session")
    monkeypatch.setattr(choices.qr_channel_scope, "current", lambda: changed)
    monkeypatch.setattr(choices, "control_revision", _no_control)
    try:
        assert await choices.resolve(new_client, h.tenant, PHONE, h.namespace, selected(h)) is None
    finally:
        await new_client.aclose()


async def _no_control(*_, **__):
    return None


@pytest.mark.asyncio
async def test_ack_same_provider_idempotent_but_divergent_provider_refused_without_ttl_extension(redis_choices):
    h = redis_choices
    await ack(h)
    before = await h.redis.pttl(h.key+":presentation")
    await ack(h)
    with pytest.raises(HTTPException):
        await ack(h, "another-provider")
    assert 0 < await h.redis.pttl(h.key+":presentation") <= before
    assert await choices.resolve(h.redis, h.tenant, PHONE, h.namespace, selected(h)) == "sim"


@pytest.mark.asyncio
async def test_ack_race_two_provider_ids_has_exactly_one_winner_in_real_lua(redis_choices):
    h = redis_choices
    results = await asyncio.gather(ack(h, "first-provider"), ack(h, "second-provider"), return_exceptions=True)
    assert sum(isinstance(result, dict) for result in results) == 1
    assert sum(isinstance(result, HTTPException) for result in results) == 1


@pytest.mark.asyncio
async def test_expired_record_not_revived_by_ack_even_if_redis_key_still_exists(redis_choices):
    h = redis_choices
    record = json.loads(await h.redis.get(h.key+":presentation"))
    record["expires_at"] = time.time()-1
    await h.redis.set(h.key+":presentation", json.dumps(record), ex=90)
    with pytest.raises(HTTPException):
        await ack(h)
    assert await choices.resolve(h.redis, h.tenant, PHONE, h.namespace, selected(h)) is None


@pytest.mark.asyncio
async def test_catalog_revision_change_invalidates_otherwise_unchanged_summary(redis_choices):
    h = redis_choices
    await ack(h)
    await h.redis.set(h.key+":catalog-context", "new-catalog-revision", ex=120)
    assert await choices.resolve(h.redis, h.tenant, PHONE, h.namespace, selected(h)) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("case",["current_delivery_proof","stale_quote","persisted_review"],ids=str)
async def test_fast_click_run_locked_delivery_latch_and_actual_redis_dedupe(redis_choices,monkeypatch,case):
    from app import qr_ai, plan_permissions, automation_runtime
    h=redis_choices
    monkeypatch.setenv("REDIS_URL",h.url)
    monkeypatch.setenv("QR_AI_INTAKE_REVISION_ENABLED","0")
    calls,interactions=[],[]
    async def require_plan(tenant,feature):
        assert tenant==h.tenant and feature=="reception.ai"
    async def interaction(tenant,phone,**kw):
        assert tenant==h.tenant and phone=="+"+PHONE
        interactions.append((tenant,phone))
    async def factual_turn(tenant,phone,body,redis,namespace,**kw):
        assert tenant==h.tenant and phone==PHONE and namespace==h.namespace
        state=json.loads(await redis.get(h.key))
        calls.append((body,state.get("confirmation_requires_review",False)))
        return {"answer":"Escolha recebida para conferir os dados atuais.","step":"information"}
    monkeypatch.setattr(plan_permissions,"require_tenant_feature",require_plan)
    monkeypatch.setattr(automation_runtime,"interaction",interaction)
    monkeypatch.setattr(qr_ai,"turn",factual_turn)
    await ack(h)
    selection=selected(h,quoted_message_id="old-provider") if case=="stale_quote" else selected(h)
    if case=="persisted_review":
        await h.redis.set(h.key,json.dumps({**h.state,"confirmation_requires_review":True}),ex=90)
    identity="fast-click-"+str(uuid4())
    try:
        result=await qr_ai.run_locked(h.tenant,PHONE,"[Escolha recebida]",h.namespace,identity,
            capabilities=choices.Capabilities(assistant_interactions=1),selection=selection,require_summary_review=True)
        assert len(interactions)==1
        if case=="current_delivery_proof":
            assert calls==[("sim",False)] and result["step"]=="information"
        else:
            assert calls==[] and result["step"]=="interaction_expired" and result["answer"]==choices.STALE
        replay=await qr_ai.run_locked(h.tenant,PHONE,"[Escolha recebida]",h.namespace,identity,
            capabilities=choices.Capabilities(assistant_interactions=1),selection=selection,require_summary_review=True)
        assert replay=={"answer":None,"step":"duplicate"} and len(interactions)==1
        assert len(calls)==(1 if case=="current_delivery_proof" else 0)
    finally:
        await h.redis.delete(f"orion:qr:ai:done:{h.tenant}:{h.namespace}:{identity}",
            f"orion:qr:ai:done:{h.tenant}:{identity}",f"orion:qr:ai:lock:{h.tenant}:{h.namespace}")


@pytest.mark.asyncio
async def test_selection_with_extra_text_is_rejected_as_422_before_any_turn(redis_choices,monkeypatch):
    from app.main import app
    from app import qr_ai
    h=redis_choices
    async def forbidden(*_,**__):
        pytest.fail("Malformed selection must never reach conversation processing")
    monkeypatch.setattr(qr_ai,"run_locked",forbidden)
    monkeypatch.setenv("QR_BRIDGE_TOKEN","synthetic-private-bridge-token-with-at-least-32-characters")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test") as client:
        reply=await client.post("/internal/qr/ai/reply",json={"phone":PHONE,"message_id":"mixed-selection",
            "text":"[Escolha recebida]\nMas troque o horário", "capabilities":{"assistant_interactions":1},
            "selection":{"id":h.token,"quoted_message_id":"synthetic-provider"}},
            headers={"x-orion-bridge-key":"synthetic-private-bridge-token-with-at-least-32-characters"})
    assert reply.status_code==422
