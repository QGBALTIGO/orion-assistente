"""Real PostgreSQL locks/rollback; no provider, socket or production operations."""
import asyncio
from contextlib import contextmanager
from uuid import uuid4

from fastapi import HTTPException
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app import qr_channel_scope as scope, qr_intake_revision as revision
from app.database import tenant_transaction
from app import qr_channels as channels
from test_qr_channels_postgres import channel_case, profiles, create, lease, request  # noqa: F401

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def active_case(channel_case):
    c = channel_case
    row, _ = await create(c)
    await channels.change_state(c.a, row["id"], channels.ChannelState(expected_revision=2, assistant_enabled=True), c.actor)
    async with c.migration.begin() as db:
        await db.execute(text("UPDATE tenants SET bot_enabled=true WHERE id=:id"), {"id": c.a})
    owner = uuid4(); grant = await lease(c, row, owner)
    async def guard():
        return await channels.authenticated_channel(request(row, owner, grant["fence"]), phone="5567999999901")
    c.row = row; c.owner = owner; c.grant = grant; c.guard = guard; c.identity = await guard()
    try:
        yield c
    finally:
        async with c.migration.begin() as db:
            await db.execute(text("DELETE FROM tenant_probe WHERE tenant_id=:tenant"), {"tenant": c.a})


@contextmanager
def bound(c):
    token = scope.bind(c.identity, c.guard)
    try: yield
    finally: scope.reset(token)


async def probe(db, c):
    await revision.check_before_write()
    await db.execute(text("INSERT INTO tenant_probe(tenant_id,label) VALUES(:tenant,'managed-atomic-write')"), {"tenant": c.a})


async def count(c):
    async with c.migration.connect() as db:
        return (await db.execute(text("SELECT count(*) FROM tenant_probe WHERE tenant_id=:tenant"), {"tenant": c.a})).scalar_one()


@pytest.mark.asyncio
async def test_revocation_is_blocked_until_domain_commit_then_rejects_old_scope(active_case):
    c = active_case
    revoke = text("UPDATE tenant_memberships SET active=false WHERE tenant_id=:tenant AND account_id=:account")
    params = {"tenant": c.a, "account": c.account}
    with bound(c):
        async with c.runtime() as db:
            async with tenant_transaction(db, c.a):
                await probe(db, c)
                async with c.migration.connect() as competing:
                    with pytest.raises(DBAPIError) as exc:
                        async with competing.begin():
                            await competing.execute(text("SET LOCAL lock_timeout='150ms'"))
                            await competing.execute(revoke, params)
                    assert getattr(exc.value.orig, "sqlstate", None) == "55P03"
        assert await count(c) == 1
        async with c.migration.begin() as competing: await competing.execute(revoke, params)
        async with c.runtime() as db:
            with pytest.raises(HTTPException):
                async with tenant_transaction(db, c.a): await probe(db, c)
    assert await count(c) == 1


@pytest.mark.asyncio
async def test_expired_owner_rolls_back_before_waiting_takeover_can_acquire(active_case):
    c = active_case; takeover = None
    async with c.migration.begin() as db:
        await db.execute(text("UPDATE qr_channels SET lease_expires_at=clock_timestamp()+interval '2 seconds' WHERE id=:id"), {"id": c.row["id"]})
    try:
        with bound(c):
            async with c.runtime() as db:
                with pytest.raises(HTTPException) as exc:
                    async with tenant_transaction(db, c.a):
                        await probe(db, c)
                        await db.execute(text("SELECT pg_sleep(2.1)"))
                        takeover = asyncio.create_task(lease(c, c.row, uuid4()))
                        with pytest.raises(asyncio.TimeoutError):
                            await asyncio.wait_for(asyncio.shield(takeover), .15)
                assert exc.value.status_code == 409
        fresh = await asyncio.wait_for(takeover, 3)
        assert fresh["fence"] > c.grant["fence"] and await count(c) == 0
    finally:
        if takeover is not None and not takeover.done():
            takeover.cancel()
            await asyncio.gather(takeover, return_exceptions=True)


@pytest.mark.asyncio
async def test_revocation_committed_before_transaction_prevents_any_domain_write(active_case):
    c = active_case
    with bound(c):
        async with c.migration.begin() as db:
            await db.execute(text("UPDATE tenant_memberships SET active=false WHERE tenant_id=:tenant AND account_id=:account"), {"tenant": c.a, "account": c.account})
        async with c.runtime() as db:
            with pytest.raises(HTTPException):
                async with tenant_transaction(db, c.a): await probe(db, c)
    assert await count(c) == 0


@pytest.mark.asyncio
async def test_takeover_committed_before_transaction_prevents_stale_fence_write(active_case):
    c = active_case
    async with c.migration.begin() as db:
        await db.execute(text("UPDATE qr_channels SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=:id"), {"id": c.row["id"]})
    fresh = await lease(c, c.row, uuid4())
    assert fresh["fence"] > c.identity.fence
    with bound(c):
        async with c.runtime() as db:
            with pytest.raises(HTTPException):
                async with tenant_transaction(db, c.a): await probe(db, c)
    assert await count(c) == 0


@pytest.mark.asyncio
async def test_body_failure_releases_locks_and_rolls_back_write(active_case):
    c = active_case
    with bound(c):
        async with c.runtime() as db:
            with pytest.raises(RuntimeError):
                async with tenant_transaction(db, c.a):
                    await probe(db, c)
                    raise RuntimeError("synthetic domain failure")
        await asyncio.wait_for(scope.assert_current(), 2)
    assert await count(c) == 0 and scope._hold.get() is None
    await asyncio.wait_for(lease(c, c.row, c.owner, "renew", c.grant["fence"]), 2)


@pytest.mark.asyncio
async def test_nested_write_guard_avoids_self_deadlock_and_child_cannot_borrow_lock(active_case):
    c = active_case
    with bound(c):
        async with c.runtime() as db:
            async with tenant_transaction(db, c.a):
                # asyncio.timeout keeps the current task; wait_for would create
                # a child task, which deliberately cannot borrow this authority.
                async with asyncio.timeout(2): await probe(db, c)
                with pytest.raises(HTTPException): await asyncio.create_task(scope.assert_current())
                # The definer must also reject a tenant GUC mismatch by itself.
                rejected = (await db.execute(text("""SELECT public.qr_channel_transaction_guard(
                    CAST(:tenant AS uuid), CAST(:channel AS uuid), CAST(:account AS uuid),
                    CAST(:owner AS uuid), CAST(:fence AS bigint))"""),
                    {"tenant": c.b, "channel": c.row["id"], "account": c.account,
                     "owner": c.owner, "fence": c.grant["fence"]})).scalar_one()
                assert rejected is False
    assert await count(c) == 1
