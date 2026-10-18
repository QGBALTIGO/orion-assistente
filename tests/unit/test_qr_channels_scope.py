"""Actual managed ingress/admission with synthetic identity and Redis, no I/O."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
import json
from uuid import uuid4

from fastapi import FastAPI, HTTPException
import httpx
import pytest

from app import qr_ai, qr_channels, qr_channel_scope as scope
from app import qr_intake_revision as revision, qr_operation_receipt as receipt
from test_qr_revision_hooks import RevisionRedis

PHONE = "5567999999901"


def identity(**kwargs):
    return qr_channels.ChannelIdentity(uuid4(), uuid4(), uuid4(), "baileys", 1, uuid4(), True, True, **kwargs)


class ScopedRedis(RevisionRedis):
    def __init__(self):
        super().__init__(); self.notices = {}; self.events = []

    async def eval(self, script, count, *args):
        if script == revision.NOTICE_SCRIPT:
            key, seen, _order, message, *_ = args
            self.events.append(("notice", key, message))
            known = self.notices.setdefault(seen, {})
            if message not in known:
                self.values[key] = int(self.values.get(key, 0)) + 1
                known[message] = self.values[key]
            return known[message]
        if script == revision.VALIDATE_SCRIPT:
            key, seen, expected, *ids = args
            known = self.notices.get(seen, {})
            return int(self.values.get(key) == expected and all(x in known for x in ids)
                       and max(known[x] for x in ids) == expected)
        return await super().eval(script, count, *args)


@pytest.fixture
def ingress(monkeypatch):
    import redis.asyncio as redis_asyncio
    from app import automation_runtime
    a = identity(); b = replace(a, channel_id=uuid4())
    identities = {str(a.channel_id): a, str(b.channel_id): b}
    redis = ScopedRedis(); calls = []; interactions = []; auth_calls = []
    monkeypatch.setenv("QR_AI_INTAKE_REVISION_ENABLED", "0")
    monkeypatch.setenv("QR_AI_ENABLED", "0")  # legacy switch cannot enable/disable another channel
    monkeypatch.setenv("QR_AI_TENANT_ID", str(uuid4()))
    monkeypatch.setattr(redis_asyncio, "from_url", lambda *a, **k: redis)

    async def auth(request, *, phone):
        assert phone == PHONE
        auth_calls.append(str(request.path_params["channel_id"]))
        return identities[str(request.path_params["channel_id"])]

    async def ready(current):
        return True

    async def interaction(tenant, phone):
        interactions.append((tenant, phone))

    async def turn(tenant, phone, body, redis, namespace, **kwargs):
        await revision.check_before_write()
        calls.append((tenant, phone, namespace, body))
        return {"answer": "Operação sintética concluída", "step": "confirmed", "_verified_operation": True}

    monkeypatch.setattr(qr_channels, "authenticated_channel", auth)
    monkeypatch.setattr(qr_ai, "_managed_assistant_ready", ready)
    monkeypatch.setattr(qr_ai, "turn", turn)
    monkeypatch.setattr(automation_runtime, "interaction", interaction)
    api = FastAPI(); api.include_router(qr_ai.router)
    return a, b, identities, redis, calls, interactions, auth_calls, api


async def request(api, channel, **extra):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="https://test") as client:
        return await client.post(f"/internal/qr/channels/{channel.channel_id}/ai/reply",
            json={"phone": PHONE, "message_id": "synthetic-id", "text": "Confirmar", **extra})


@pytest.mark.asyncio
async def test_default_disabled_managed_endpoints_never_open_database_or_redis(monkeypatch):
    import redis.asyncio as redis_asyncio
    monkeypatch.delenv("QR_CHANNELS_ENABLED", raising=False)
    def forbidden(*args, **kwargs): raise AssertionError("disabled ingress must do no I/O")
    monkeypatch.setattr(qr_channels, "support_session_factory", forbidden)
    monkeypatch.setattr(redis_asyncio, "from_url", forbidden)
    api = FastAPI(); api.include_router(qr_ai.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="https://test") as client:
        for action in ("ai/reply", "interaction"):
            data = {"phone": PHONE, "message_id": "synthetic"}
            if action == "ai/reply": data["text"] = "test"
            response = await client.post(f"/internal/qr/channels/{uuid4()}/{action}", json=data)
            assert response.status_code == 423


@pytest.mark.asyncio
async def test_same_contact_message_tenant_is_independent_between_channels_and_legacy(ingress):
    a, b, _, redis, calls, interactions, auth_calls, api = ingress
    legacy = f"orion:qr:ai:done:{a.tenant_id}:synthetic-id"
    redis.values[legacy] = "legacy-marker"
    for channel in (a, b):
        result = await request(api, channel)
        assert result.status_code == 200 and result.json()["step"] == "confirmed"
    assert len(calls) == 2 and len(interactions) == 2
    assert calls[0][2] == f"{a.context_namespace}:{PHONE}"
    assert calls[1][2] == f"{b.context_namespace}:{PHONE}"
    assert all(item[0] == a.tenant_id for item in calls)
    assert len({x[1] for x in redis.events}) == 2
    assert redis.values[legacy] == "legacy-marker"
    done = [key for key in redis.values if ":done:" in key and key != legacy]
    assert len(done) == 2 and all(str(a.channel_id) in x or str(b.channel_id) in x for x in done)
    assert (await request(api, a)).json()["step"] == "duplicate"
    assert len(calls) == 2 and len(interactions) == 2
    assert len(auth_calls) > 4 and scope.current() is None and revision._turn.get() is None


@pytest.mark.asyncio
async def test_registration_precedes_actual_lock_and_caller_revision_is_not_authority(ingress, monkeypatch):
    a, _, _, redis, _, _, _, api = ingress
    original = qr_ai.run_locked
    async def locked(*args, **kwargs):
        assert len(redis.events) == 2
        assert kwargs["intake_revision"] == 2 and revision.enabled()
        return await original(*args, **kwargs)
    monkeypatch.setattr(qr_ai, "run_locked", locked)
    result = await request(api, a, message_ids=["synthetic-id", "synthetic-next"], intake_revision=99999)
    assert result.json()["step"] == "confirmed"


@pytest.mark.asyncio
@pytest.mark.parametrize("gate", ["assistant", "tenant", "onboarding"])
async def test_disabled_or_unready_ingress_never_registers_or_calls_turn(ingress, monkeypatch, gate):
    a, _, identities, redis, calls, interactions, _, api = ingress
    if gate == "assistant":
        identities[str(a.channel_id)] = replace(a, assistant_enabled=False)
    else:
        async def ready(current):
            if gate == "onboarding":
                raise HTTPException(409, "Configuração pendente")
            return False
        monkeypatch.setattr(qr_ai, "_managed_assistant_ready", ready)
    result = await request(api, a)
    assert result.status_code == (409 if gate == "onboarding" else 200)
    if result.status_code == 200:
        assert result.json() == {"answer": None, "step": "disabled"}
    assert not redis.events and not calls and not interactions and scope.current() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["fence", "lease_owner", "owner_account_id", "tenant_id", "channel_id", "assistant_enabled", "allowed_sender"])
async def test_changed_authority_during_inference_prevents_first_write_even_with_legacy_flag_off(ingress, monkeypatch, field):
    a, _, identities, redis, calls, _, _, api = ingress
    async def turn(*args, **kwargs):
        value = 2 if field == "fence" else False if field in {"assistant_enabled", "allowed_sender"} else uuid4()
        identities[str(a.channel_id)] = replace(a, **{field: value})
        await revision.check_before_write()
        calls.append("must not write")
    monkeypatch.setattr(qr_ai, "turn", turn)
    result = await request(api, a)
    assert result.status_code in {409, 423} and not calls and not redis.transactions
    assert scope.current() is None and revision._turn.get() is None


@pytest.mark.asyncio
async def test_fresh_authentication_failure_is_not_replaced_with_cached_identity(ingress, monkeypatch):
    a, _, _, _, calls, _, _, api = ingress
    lost = False
    async def auth(*args, **kwargs):
        if lost:
            raise HTTPException(401, "Owner revoked")
        return a
    monkeypatch.setattr(qr_channels, "authenticated_channel", auth)
    async def turn(*args, **kwargs):
        nonlocal lost
        lost = True
        await revision.check_before_write()
        calls.append("must not write")
    monkeypatch.setattr(qr_ai, "turn", turn)
    assert (await request(api, a)).status_code == 401
    assert calls == []


@pytest.mark.asyncio
async def test_marketing_interaction_failure_stops_new_turn_and_no_done_marker(ingress, monkeypatch):
    from app import automation_runtime
    a, _, _, redis, calls, _, _, api = ingress
    async def unavailable(*args):
        raise ConnectionError("synthetic unavailable")
    monkeypatch.setattr(automation_runtime, "interaction", unavailable)
    assert (await request(api, a)).status_code == 503
    assert not calls and not redis.transactions


@pytest.mark.asyncio
async def test_loss_after_write_keeps_scoped_receipt_and_consumes_unknown_attempt(ingress, monkeypatch):
    a, _, identities, redis, calls, _, _, api = ingress
    async def turn(*args, **kwargs):
        await revision.check_before_write()
        calls.append("one actual synthetic write")
        identities[str(a.channel_id)] = replace(a, fence=2)
        return {"answer": "Concluído", "step": "confirmed", "_verified_operation": True}
    monkeypatch.setattr(qr_ai, "turn", turn)
    assert (await request(api, a)).status_code == 409
    assert len(calls) == 1 and len(redis.transactions) == 1
    saved = [json.loads(value) for key, value in redis.values.items() if ":operation-receipt:" in key]
    assert saved[0]["channel_id"] == str(a.channel_id) and saved[0]["step"] == "confirmed"
    assert (await request(api, a)).json()["step"] == "duplicate"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_notice_only_cancels_marketing_with_assistant_off_and_shared_dedup_prevents_second_update(ingress):
    a, _, identities, redis, calls, interactions, _, api = ingress
    identities[str(a.channel_id)] = replace(a, assistant_enabled=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="https://test") as client:
        url = f"/internal/qr/channels/{a.channel_id}/interaction"
        body = {"phone": PHONE, "message_id": "synthetic-id"}
        for _ in range(2):
            assert (await client.post(url, json=body)).json() == {"recorded": True}
        assert (await client.post(url, json={**body, "text": "not processed"})).status_code == 422
    assert interactions == [(a.tenant_id, "+" + PHONE)] and not calls and not redis.events
    identities[str(a.channel_id)] = a
    assert (await request(api, a)).json()["step"] == "confirmed"
    assert len(calls) == 1 and len(interactions) == 1


@pytest.mark.asyncio
async def test_notice_only_rechecks_fence_inside_contact_lock_before_cancelling(ingress, monkeypatch):
    a, _, _, redis, calls, interactions, _, api = ingress
    count = 0
    async def auth(*args, **kwargs):
        nonlocal count
        count += 1
        return a if count == 1 else replace(a, fence=2)
    monkeypatch.setattr(qr_channels, "authenticated_channel", auth)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="https://test") as client:
        result = await client.post(f"/internal/qr/channels/{a.channel_id}/interaction",
                                  json={"phone": PHONE, "message_id": "synthetic-id"})
    assert result.status_code == 409 and not interactions and not calls and not redis.values


@pytest.mark.asyncio
@pytest.mark.parametrize("extra", [{"tenant_id": str(uuid4())}, {"channel_id": str(uuid4())}, {"phone": "123456@lid"}])
async def test_untrusted_authority_fields_never_enter_ingress(ingress, extra):
    a, _, _, redis, calls, _, auth, api = ingress
    assert (await request(api, a, **extra)).status_code == 422
    assert not auth and not redis.events and not calls


@pytest.mark.asyncio
async def test_scope_guard_runs_without_revision_binding_or_flag_and_is_task_local(monkeypatch):
    monkeypatch.setenv("QR_AI_INTAKE_REVISION_ENABLED", "0")
    a, b = identity(), identity(); seen = []
    async def job(channel):
        async def guard():
            await asyncio.sleep(0)
            seen.append(scope.current())
            return channel
        token = scope.bind(channel, guard)
        try:
            await revision.assert_current()
            assert scope.current() == channel and revision.enabled()
        finally:
            scope.reset(token)
    await asyncio.gather(job(a), job(b))
    assert set(seen) == {a, b} and scope.current() is None and not revision.enabled()


@pytest.mark.asyncio
async def test_receipts_cannot_be_copied_across_channels_or_into_legacy_and_legacy_keys_are_exact():
    a = identity(); b = replace(a, channel_id=uuid4()); redis = ScopedRedis()
    async def guard(): return a
    token = scope.bind(a, guard)
    try:
        await receipt.save(redis, a.tenant_id, PHONE, "synthetic", {"step": "confirmed", "answer": "Completed"}, verified_operation=True)
        akey = receipt.key(a.tenant_id, PHONE); raw = redis.values[akey]
        assert receipt._read(raw, a.tenant_id, PHONE)["channel_id"] == str(a.channel_id)
        with pytest.raises(ValueError): revision.keys(uuid4(), PHONE)
    finally: scope.reset(token)
    token = scope.bind(b, guard)
    try:
        assert receipt.key(a.tenant_id, PHONE) != akey
        assert receipt._read(raw, a.tenant_id, PHONE) is None
    finally: scope.reset(token)
    assert receipt._read(raw, a.tenant_id, PHONE) is None
    assert receipt.key(a.tenant_id, PHONE) == f"orion:qr:operation-receipt:{a.tenant_id}:{PHONE}"
    assert revision.keys(a.tenant_id, PHONE)[0] == f"orion:qr:intake:{a.tenant_id}:{PHONE}:revision"


@pytest.mark.asyncio
async def test_scope_rejects_arbitrary_run_locked_namespace_before_opening_redis(ingress):
    a, _, _, redis, *_ = ingress
    async def guard(): return a
    token = scope.bind(a, guard)
    try:
        with pytest.raises(HTTPException):
            await qr_ai.run_locked(a.tenant_id, PHONE, "text", PHONE, "synthetic")
        assert not redis.events and not redis.locks
    finally: scope.reset(token)


@pytest.mark.asyncio
async def test_domain_idempotency_and_receipt_identity_differ_between_channels_without_changing_legacy(ingress, monkeypatch):
    a, b, _, redis, *_ = ingress
    api = ingress[-1]; ids = []
    async def turn(*args, **kwargs):
        ids.append(qr_ai._message_identity.get())
        return {"answer": "Concluído", "step": "confirmed", "_verified_operation": True}
    monkeypatch.setattr(qr_ai, "turn", turn)
    for channel in (a, b):
        assert (await request(api, channel)).status_code == 200
    assert len(set(ids)) == 2 and all(len(x) == 64 for x in ids)
    assert "synthetic-id" not in ids and scope.message_identity("synthetic-id") == "synthetic-id"
    saved = [json.loads(value)["message_id"] for key, value in redis.values.items() if ":operation-receipt:" in key]
    assert set(saved) == set(ids)


@pytest.mark.asyncio
@pytest.mark.parametrize("active", [True, False, None])
async def test_managed_readiness_uses_authenticated_tenant_and_assistant_capability(monkeypatch, active):
    from app import onboarding_service
    channel = identity(); seen = []
    class DB:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def execute(self, sql, values):
            assert values == {"tenant": channel.tenant_id}
            assert "bot_enabled" in str(sql)
            return type("Result", (), {"scalar_one_or_none": lambda self: active})()
    @asynccontextmanager
    async def transaction(db, tenant):
        assert tenant == channel.tenant_id
        yield db
    async def ready(db, tenant, capability): seen.append((tenant, capability))
    monkeypatch.setattr(qr_ai, "get_session_factory", lambda: DB)
    monkeypatch.setattr(qr_ai, "tenant_transaction", transaction)
    monkeypatch.setattr(onboarding_service, "require_operational_ready", ready)
    assert await qr_ai._managed_assistant_ready(channel) is (active is True)
    assert seen == ([(channel.tenant_id, "assistant")] if active is True else [])
