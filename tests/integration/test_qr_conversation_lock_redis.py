"""Actual Redis token ownership and recovery Lua, in a unique ephemeral scope."""
import asyncio

import pytest
from fastapi import HTTPException

from app import qr_ai, qr_conversation_lock as conversation
from test_qr_intake_revision_redis import redis_case


pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_real_redis_renewal_and_owned_cleanup_preserve_protocol(redis_case, monkeypatch):
    redis, tenant, phone = redis_case
    base = f"orion:qr:ai:{tenant}:{phone}"
    lock_key = f"orion:qr:ai:lock:{tenant}:{phone}"
    keys = (*qr_ai._provisional_keys(base), lock_key)
    monkeypatch.setattr(conversation, "RENEW_SECONDS", .01)
    lock = redis.lock(lock_key, timeout=90, blocking_timeout=.1)
    renewed = asyncio.Event()
    extend = lock.extend
    async def observe(*args, **kwargs):
        result = await extend(*args, **kwargs)
        renewed.set()
        return result
    lock.extend = observe
    try:
        await redis.set(base, "original", ex=30)
        async with conversation.hold(lock):
            assert await lock.owned()
            await redis.pexpire(lock_key, 1500)
            await asyncio.wait_for(renewed.wait(), 1)
            assert 85000 < await redis.pttl(lock_key) <= 90000
            snapshot = await qr_ai._snapshot_provisional_state(redis, base)
            await redis.set(base, "provisional", ex=300)
            await qr_ai._restore_provisional_state(redis, base, snapshot)
            assert await redis.get(base) == b"original"
            assert 0 < await redis.ttl(base) <= 30
            await conversation.set_cleanup(redis, base, "review-required", ex=30)
            assert await redis.get(base) == b"review-required"
        assert await redis.get(lock_key) is None and conversation._lease.get() is None
    finally:
        await redis.delete(*keys)


@pytest.mark.asyncio
async def test_real_redis_expired_owner_cannot_restore_or_release_successor(redis_case):
    redis, tenant, phone = redis_case
    base = f"orion:qr:ai:{tenant}:{phone}"
    lock_key = f"orion:qr:ai:lock:{tenant}:{phone}"
    keys = (*qr_ai._provisional_keys(base), lock_key)
    old = redis.lock(lock_key, timeout=90, blocking_timeout=.1)
    successor = redis.lock(lock_key, timeout=90, blocking_timeout=.1)
    try:
        await redis.set(base, "original", ex=30)
        with pytest.raises(HTTPException) as lost:
            async with conversation.hold(old):
                snapshot = await qr_ai._snapshot_provisional_state(redis, base)
                await redis.pexpire(lock_key, 1)
                await asyncio.sleep(.03)
                assert await successor.acquire()
                await redis.set(base, "successor-state", ex=30)
                with pytest.raises(HTTPException):
                    await qr_ai._restore_provisional_state(redis, base, snapshot)
                with pytest.raises(HTTPException):
                    await conversation.set_cleanup(redis, base, "stale-review", ex=30)
                await conversation.assert_current()
        assert lost.value.status_code == 409
        assert await successor.owned() and await redis.get(base) == b"successor-state"
        await successor.release()
    finally:
        await redis.delete(*keys)
