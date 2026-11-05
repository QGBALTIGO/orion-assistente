"""Delayed inference cannot outlive its conversation ownership; no live I/O."""
import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app import automation_runtime, qr_ai, qr_intake_revision, qr_conversation_lock, qr_channel_scope
from test_qr_revision_hooks import RevisionRedis


class LeaseRedis(RevisionRedis):
    def __init__(self):
        super().__init__()
        self.clock = 100.0
        self.owner = None
        self.expiry = None
        self.renewals = []

    def lock(self, key, *, timeout, **kwargs):
        redis = self
        identity = object()

        class Lock:
            name = key
            local = SimpleNamespace(token=identity)
            async def acquire(self):
                if redis.owner is not None and redis.expiry > redis.clock:
                    return False
                redis.owner, redis.expiry = identity, redis.clock + timeout
                redis.lock_tokens[key] = identity
                return True

            async def owned(self):
                return redis.owner is identity and redis.expiry > redis.clock

            async def extend(self, additional_time, replace_ttl=False):
                if not await self.owned():
                    return False
                redis.renewals.append(additional_time)
                redis.expiry = redis.clock + additional_time if replace_ttl else redis.expiry + additional_time
                return True

            async def release(self):
                if redis.owner is identity:
                    redis.owner = None
                    redis.lock_tokens.pop(key, None)

            async def __aenter__(self):
                assert await self.acquire()
                return self

            async def __aexit__(self, *args):
                await self.release()

        return Lock()


@pytest.fixture
def lease_turn(monkeypatch):
    import redis.asyncio as redis_asyncio
    from plan_fixture import install_plan_fixture
    install_plan_fixture(monkeypatch, "elite")
    redis = LeaseRedis()
    monkeypatch.setenv("QR_AI_INTAKE_REVISION_ENABLED", "0")
    monkeypatch.setattr(redis_asyncio, "from_url", lambda *a, **kw: redis)

    async def interaction(*a, **kw):
        pass

    monkeypatch.setattr(automation_runtime, "interaction", interaction)
    return redis


@pytest.mark.asyncio
async def test_expired_conversation_owner_cannot_write_even_without_ingress_flag(lease_turn, monkeypatch):
    redis = lease_turn
    writes = []

    async def delayed(*args, **kwargs):
        redis.clock += 91
        await qr_intake_revision.check_before_write()
        writes.append("must not commit")
        return {"answer": "stale reply", "step": "confirmed"}

    monkeypatch.setattr(qr_ai, "turn", delayed)
    with pytest.raises(HTTPException) as denied:
        await qr_ai.run_locked(uuid4(), "5567999999999", "Confirmar", "5567999999999", "stale-id")
    assert denied.value.status_code in {409, 503}
    assert writes == []
    assert redis.transactions == []


@pytest.mark.asyncio
async def test_replaced_conversation_owner_cannot_restore_its_old_state(lease_turn, monkeypatch):
    redis = lease_turn
    tenant = uuid4()
    phone = "5567999999999"
    state_key = f"orion:qr:ai:{tenant}:{phone}"

    async def delayed(*args, **kwargs):
        redis.owner = object()
        redis.values[state_key] = "successor state"
        await qr_intake_revision.assert_current()

    monkeypatch.setattr(qr_ai, "turn", delayed)
    with pytest.raises(HTTPException) as denied:
        await qr_ai.run_locked(tenant, phone, "Confirmar", phone, "stale-id")
    assert denied.value.status_code == 409
    assert redis.values[state_key] == "successor state" and not redis.restored
    assert qr_conversation_lock._lease.get() is None


@pytest.mark.asyncio
async def test_restore_and_owner_comparison_are_one_redis_operation(lease_turn, monkeypatch):
    redis = lease_turn
    key = "orion:qr:ai:synthetic:contact"
    await redis.set(key, json.dumps({"step": "date"}), ex=60)
    lock = redis.lock("conversation-lock", timeout=90)
    original = redis.eval

    async def rotate_before_restore(script, count, *args):
        if script == qr_ai._STATE_RESTORE_OWNED_SCRIPT:
            redis.owner = object()
            redis.lock_tokens[lock.name] = redis.owner
            redis.values[key] = "successor state"
        return await original(script, count, *args)

    monkeypatch.setattr(redis, "eval", rotate_before_restore)
    with pytest.raises(HTTPException) as denied:
        async with qr_conversation_lock.hold(lock):
            snapshot = await qr_ai._snapshot_provisional_state(redis, key)
            await qr_ai._restore_provisional_state(redis, key, snapshot)
    assert denied.value.status_code == 409 and redis.values[key] == "successor state"


@pytest.mark.asyncio
async def test_unexpected_error_after_write_is_not_permission_to_replay(lease_turn, monkeypatch):
    tenant, phone = uuid4(), "5567999999999"
    async def crash(*args, **kwargs):
        await qr_intake_revision.check_before_write()
        raise RuntimeError("synthetic post-commit interruption")
    monkeypatch.setattr(qr_ai, "turn", crash)
    with pytest.raises(RuntimeError):
        await qr_ai.run_locked(tenant, phone, "Confirmar", phone, "written-id")
    assert (await qr_ai.run_locked(tenant, phone, "Confirmar", phone, "written-id"))["step"] == "duplicate"
    assert qr_conversation_lock._lease.get() is None


@pytest.mark.asyncio
async def test_interruption_while_recording_completion_also_consumes_written_attempt(lease_turn, monkeypatch):
    tenant, phone = uuid4(), "5567999999999"
    original = lease_turn.pipeline
    attempts = []
    def flaky_pipeline(*args, **kwargs):
        pipeline = original(*args, **kwargs)
        execute = pipeline.execute
        async def once():
            attempts.append(True)
            if len(attempts) == 1:
                raise ConnectionError("synthetic completion acknowledgement lost")
            return await execute()
        pipeline.execute = once
        return pipeline
    monkeypatch.setattr(lease_turn, "pipeline", flaky_pipeline)
    async def commit(*args, **kwargs):
        await qr_intake_revision.check_before_write()
        return {"answer": "Attempted", "step": "confirmed"}
    monkeypatch.setattr(qr_ai, "turn", commit)
    with pytest.raises(ConnectionError):
        await qr_ai.run_locked(tenant, phone, "Confirmar", phone, "written-id")
    assert len(attempts) == 2
    assert (await qr_ai.run_locked(tenant, phone, "Confirmar", phone, "written-id"))["step"] == "duplicate"


@pytest.mark.asyncio
async def test_deadline_blocks_provider_that_swallows_cancellation(lease_turn, monkeypatch):
    writes = []
    monkeypatch.setattr(qr_conversation_lock, "PROCESSING_SECONDS", .01)

    async def delayed(*args, **kwargs):
        try:
            await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass  # Deliberately emulate an adapter mishandling cancellation.
        await qr_intake_revision.check_before_write()
        writes.append("stale effect")
        return {"answer": "must not escape", "step": "confirmed"}

    monkeypatch.setattr(qr_ai, "turn", delayed)
    with pytest.raises(HTTPException) as denied:
        await qr_ai.run_locked(uuid4(), "5567999999999", "Confirmar", "5567999999999", "slow-id")
    assert denied.value.status_code == 503 and writes == []
    assert lease_turn.transactions == [] and lease_turn.closed == 1
    assert qr_conversation_lock._lease.get() is None


@pytest.mark.asyncio
async def test_timeout_after_write_consumes_uncertain_message_without_ingress_flag(lease_turn, monkeypatch):
    monkeypatch.setattr(qr_conversation_lock, "PROCESSING_SECONDS", .01)
    tenant, phone = uuid4(), "5567999999999"
    writes = []

    async def delayed(*args, **kwargs):
        await qr_intake_revision.check_before_write()
        writes.append("already attempted")
        await asyncio.sleep(1)

    monkeypatch.setattr(qr_ai, "turn", delayed)
    with pytest.raises(HTTPException) as denied:
        await qr_ai.run_locked(tenant, phone, "Confirmar", phone, "written-id")
    assert denied.value.status_code == 503 and writes == ["already attempted"]
    assert lease_turn.values[f"orion:qr:ai:done:{tenant}:{phone}:written-id"] == "1"
    assert (await qr_ai.run_locked(tenant, phone, "Confirmar", phone, "written-id"))["step"] == "duplicate"
    assert writes == ["already attempted"]


@pytest.mark.asyncio
async def test_lease_renews_with_compare_token_and_is_released(lease_turn, monkeypatch):
    monkeypatch.setattr(qr_conversation_lock, "RENEW_SECONDS", .001)
    lock = lease_turn.lock("synthetic", timeout=qr_conversation_lock.LOCK_SECONDS)
    renewed = asyncio.Event()
    extend = lock.extend
    async def observe(*args, **kwargs):
        result = await extend(*args, **kwargs)
        renewed.set()
        return result
    lock.extend = observe
    async with qr_conversation_lock.hold(lock):
        await asyncio.wait_for(renewed.wait(), .5)
        await qr_conversation_lock.assert_current()
    assert lease_turn.renewals and all(value == 90 for value in lease_turn.renewals)
    assert lease_turn.owner is None and qr_conversation_lock._lease.get() is None


@pytest.mark.asyncio
async def test_failed_renewal_cancels_owner_and_cannot_write(lease_turn, monkeypatch):
    monkeypatch.setattr(qr_conversation_lock, "RENEW_SECONDS", .001)
    writes = []
    original = lease_turn.lock

    def lock(*args, **kwargs):
        selected = original(*args, **kwargs)
        async def fail(*args, **kwargs):
            raise ConnectionError("synthetic Redis interruption")
        selected.extend = fail
        return selected

    lease_turn.lock = lock

    async def delayed(*args, **kwargs):
        try:
            await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        await qr_intake_revision.check_before_write()
        writes.append("must not run")

    monkeypatch.setattr(qr_ai, "turn", delayed)
    with pytest.raises(HTTPException) as denied:
        await qr_ai.run_locked(uuid4(), "5567999999999", "Confirmar", "5567999999999", "slow-id")
    assert denied.value.status_code == 409 and writes == []


@pytest.mark.asyncio
async def test_transaction_exit_rechecks_conversation_before_commit(lease_turn):
    lock = lease_turn.lock("synthetic", timeout=qr_conversation_lock.LOCK_SECONDS)
    with pytest.raises(HTTPException) as denied:
        async with qr_conversation_lock.hold(lock):
            async with qr_channel_scope.transaction_guard(None, uuid4()):
                lease_turn.owner = object()
    assert denied.value.status_code == 409


@pytest.mark.asyncio
async def test_failure_cleanup_has_its_own_deadline(lease_turn, monkeypatch, caplog):
    monkeypatch.setattr(qr_conversation_lock, "CLEANUP_SECONDS", .01)
    async def stalled(*args, **kwargs):
        await asyncio.sleep(10)
    async def crash(*args, **kwargs):
        await qr_intake_revision.check_before_write()
        monkeypatch.setattr(lease_turn, "get", stalled)
        raise RuntimeError("synthetic interrupted attempt")
    monkeypatch.setattr(qr_ai, "turn", crash)
    with pytest.raises(RuntimeError):
        await asyncio.wait_for(qr_ai.run_locked(uuid4(), "5567999999999", "Confirmar", "5567999999999", "written-id"), .5)
    assert "qr_conversation_cleanup_incomplete type=TimeoutError" in caplog.text
    assert qr_conversation_lock._lease.get() is None


@pytest.mark.asyncio
async def test_owner_audio_preview_keeps_its_independent_provider_budget(lease_turn, monkeypatch):
    monkeypatch.setattr(qr_conversation_lock, "PROCESSING_SECONDS", .01)
    monkeypatch.setattr(qr_conversation_lock, "PREVIEW_SECONDS", .5)
    async def transcript():
        await asyncio.sleep(.05)
        return {"text": "simulated transcription"}
    async def preview(*args, **kwargs):
        assert kwargs["preview"] is True
        return {"answer": "Preview only", "step": "preview"}
    monkeypatch.setattr(qr_ai, "turn", preview)
    result = await qr_ai.run_locked(uuid4(), "5567999999999", "", "preview:audio", "preview-id",
                                    preview=True, prepare=transcript)
    assert result["step"] == "preview" and result["input_kind"] == "audio"
