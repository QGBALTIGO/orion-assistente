"""Execute the actual Lua only against an explicitly designated ephemeral Redis."""
import asyncio
import os
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
import pytest_asyncio

from app import qr_intake_revision as revision

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def redis_case(monkeypatch):
    required = os.getenv("ORION_REQUIRE_INTAKE_REDIS_TEST") == "1"
    url = os.getenv("ORION_INTAKE_TEST_REDIS_URL")
    if not url:
        if required:
            pytest.fail("Mandatory ephemeral intake Redis was not configured")
        pytest.skip("Explicit ephemeral intake Redis not configured")
    parsed = urlsplit(url)
    if (parsed.scheme != "redis" or parsed.hostname not in {"localhost", "127.0.0.1", "::1", "redis"}
            or parsed.path != "/15" or parsed.username or parsed.password or parsed.query or parsed.fragment):
        pytest.fail("Intake test Redis must be the designated local ephemeral database 15")
    from redis.asyncio import from_url
    redis = from_url(url, socket_connect_timeout=2, socket_timeout=3)
    try:
        await redis.ping()
    except Exception:
        await redis.aclose()
        if required:
            pytest.fail("Mandatory ephemeral intake Redis unavailable")
        pytest.skip("Ephemeral intake Redis unavailable")
    monkeypatch.setenv("QR_AI_INTAKE_REVISION_ENABLED", "1")
    tenant, phone = uuid4(), "5521999999999"
    own = revision.keys(tenant, phone)
    try:
        yield redis, tenant, phone
    finally:
        await redis.delete(*own)  # Only this test's unique, verified local namespace.
        await redis.aclose()


@pytest.mark.asyncio
async def test_real_lua_claims_duplicate_atomically_and_new_notice_revokes_old_turn(redis_case):
    redis, tenant, phone = redis_case
    values = await asyncio.gather(*(revision.register(redis, tenant, phone, "same-id") for _ in range(40)))
    assert set(values) == {1}
    assert await revision.register(redis, tenant, phone, "correction") == 2
    token = revision.bind(tenant, phone, ["same-id"], 1, redis)
    try:
        with pytest.raises(revision.Superseded) as error:
            await revision.check_before_write()
        assert error.value.not_committed
    finally:
        revision.reset(token)
    for key in revision.keys(tenant, phone):
        assert 0 < await redis.ttl(key) <= revision.TTL


@pytest.mark.asyncio
async def test_real_lua_binds_latest_revision_to_the_ids_actually_interpreted(redis_case):
    redis, tenant, phone = redis_case
    await revision.register(redis, tenant, phone, "audio")
    await revision.register(redis, tenant, phone, "correction")
    token = revision.bind(tenant, phone, ["audio"], 2, redis)
    try:
        with pytest.raises(revision.Superseded):
            await revision.assert_current()
    finally:
        revision.reset(token)
    token = revision.bind(tenant, phone, ["audio", "correction"], 2, redis)
    try:
        await revision.check_before_write()
        await revision.register(redis, tenant, phone, "after-write-started")
        with pytest.raises(revision.Superseded) as error:
            await revision.assert_current()
        assert not error.value.not_committed
    finally:
        revision.reset(token)


@pytest.mark.asyncio
async def test_real_lua_history_is_bounded_and_foreign_scope_cannot_supply_revision(redis_case, monkeypatch):
    redis, tenant, phone = redis_case
    monkeypatch.setattr(revision, "MAX_SEEN", 4)
    for number in range(12):
        await revision.register(redis, tenant, phone, f"message-{number}")
    _, seen, order = revision.keys(tenant, phone)
    assert await redis.hlen(seen) == 4 and await redis.zcard(order) == 4
    assert await revision.register(redis, tenant, phone, "message-11") == 12
    token = revision.bind(uuid4(), phone, ["message-11"], 12, redis)
    try:
        with pytest.raises(revision.Superseded):
            await revision.check_before_write()
    finally:
        revision.reset(token)
    token = revision.bind(tenant, "5521999999998", ["message-11"], 12, redis)
    try:
        with pytest.raises(revision.Superseded):
            await revision.check_before_write()
    finally:
        revision.reset(token)


@pytest.mark.asyncio
async def test_real_lua_restores_only_provisional_state_without_extending_original_deadlines(redis_case, monkeypatch):
    from app import qr_ai

    redis, tenant, _ = redis_case
    base = f"orion:qr:ai:{tenant}:synthetic-rollback"
    state, commerce, catalog = qr_ai._provisional_keys(base)
    receipt = base + ":unrelated-receipt"
    own = (state, commerce, catalog, receipt)
    try:
        await redis.set(state, "before", ex=10)
        await redis.set(commerce, "", ex=15)
        await redis.set(receipt, "verified-completion", ex=20)
        monkeypatch.setattr(qr_ai, "monotonic", lambda: 100.0)
        snapshot = await qr_ai._snapshot_provisional_state(redis, base)
        assert snapshot[1][4] is None and snapshot[1][5] == -2
        for target in (state, commerce, catalog):
            await redis.set(target, "superseded", ex=300)
        monkeypatch.setattr(qr_ai, "monotonic", lambda: 102.0)
        await qr_ai._restore_provisional_state(redis, base, snapshot)
        assert await redis.get(state) == b"before"
        assert 0 < await redis.pttl(state) <= 8000
        assert await redis.get(commerce) == b""
        assert 0 < await redis.pttl(commerce) <= 13000
        assert await redis.get(catalog) is None
        assert await redis.get(receipt) == b"verified-completion"

        await redis.set(state, "about-to-expire", ex=1)
        monkeypatch.setattr(qr_ai, "monotonic", lambda: 200.0)
        expired = await qr_ai._snapshot_provisional_state(redis, base)
        await redis.set(state, "superseded", ex=300)
        monkeypatch.setattr(qr_ai, "monotonic", lambda: 202.0)
        await qr_ai._restore_provisional_state(redis, base, expired)
        assert await redis.get(state) is None
        assert await redis.get(receipt) == b"verified-completion"
    finally:
        await redis.delete(*own)
