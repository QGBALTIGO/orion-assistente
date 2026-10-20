"""Managed notice-only races on real PostgreSQL; synthetic contacts, no transport."""
import asyncio
import hashlib
from uuid import uuid4

from fastapi import HTTPException
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from starlette.requests import Request

from app import automation_runtime as runtime, qr_channel_scope, qr_channels
from app.database import tenant_transaction
from test_automation_postgres import automation_db, queue, PHONE  # noqa: F401

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def managed_notice(automation_db, monkeypatch):
    ctx = automation_db
    monkeypatch.setenv("QR_CHANNELS_ENABLED", "1")
    owner = uuid4()
    secret = "synthetic-notice-private-credential-" + "x" * 32
    async with ctx.migration.begin() as db:
        await db.execute(text("UPDATE tenants SET bot_enabled=false WHERE id=:t"), {"t": ctx.tenant})
        await db.execute(text("""UPDATE qr_channels SET assistant_enabled=false,lease_owner=:owner,
            fence=7,lease_expires_at=clock_timestamp()+interval '30 seconds',credential_hash=:digest
            WHERE id=:id"""), {"id": ctx.channel, "owner": owner, "digest": hashlib.sha256(secret.encode()).hexdigest()})
    request = Request({"type": "http", "method": "POST", "path": "/", "path_params": {"channel_id": str(ctx.channel)},
        "headers": [(b"x-orion-channel-key", secret.encode()), (b"x-orion-channel-owner", str(owner).encode()),
                    (b"x-orion-channel-fence", b"7")]})
    ctx.identity = await qr_channels.authenticated_channel(request, phone=PHONE)
    assert not ctx.identity.assistant_enabled and ctx.identity.allowed_sender
    ctx.created, ctx.job = await queue(ctx)
    assert ctx.created
    yield ctx


async def facts(ctx):
    async with ctx.migration.connect() as db:
        contact = (await db.execute(text("SELECT * FROM automation_contact_state WHERE tenant_id=:t ORDER BY contact_e164"), {"t": ctx.tenant})).mappings().all()
        jobs = (await db.execute(text("SELECT * FROM automation_jobs WHERE tenant_id=:t ORDER BY id"), {"t": ctx.tenant})).mappings().all()
        consent = (await db.execute(text("SELECT * FROM contact_consents WHERE tenant_id=:t"), {"t": ctx.tenant})).mappings().all()
        return {"contact": [dict(v) for v in contact], "jobs": [dict(v) for v in jobs], "consent": [dict(v) for v in consent]}


@pytest.mark.asyncio
async def test_notice_ai_off_cancels_pending_without_granting_domain_authority(managed_notice):
    ctx = managed_notice
    before = await facts(ctx)
    await runtime.interaction(ctx.tenant, PHONE, channel_identity=ctx.identity)
    after = await facts(ctx)
    assert after["jobs"][0]["status"] == "cancelled" and after["jobs"][0]["last_error"] == "conversation_changed"
    assert after["jobs"][0]["attempted_at"] is None and not ctx.sent
    assert after["contact"][0]["revision"] == 1 and after["contact"][0]["human_paused"] is False
    assert after["consent"] == before["consent"] and qr_channel_scope.current() is None
    with pytest.raises(HTTPException): qr_channel_scope.bind(ctx.identity, lambda: None)
    async with ctx.factory() as db:
        async with tenant_transaction(db, ctx.tenant):
            # The original domain guard must still deny assistant-off writes.
            assert not (await db.execute(text("SELECT public.qr_channel_transaction_guard(:t,:c,:a,:o,:f)"),
                {"t": ctx.tenant, "c": ctx.channel, "a": ctx.account,
                 "o": ctx.identity.lease_owner, "f": ctx.identity.fence})).scalar_one()


REVOKE = {
    "owner": "UPDATE tenant_memberships SET active=false WHERE tenant_id=:t AND account_id=:a",
    "channel": "UPDATE qr_channels SET enabled=false WHERE id=:c",
    "company": "UPDATE tenants SET status='paused' WHERE id=:t",
    "fence": "UPDATE qr_channels SET fence=fence+1 WHERE id=:c",
    "sender": "UPDATE qr_channels SET allowed_senders='[\"5567999999902\"]'::jsonb WHERE id=:c",
}


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", list(REVOKE))
async def test_notice_holds_authority_until_commit_and_revocation_blocks_later_call(managed_notice, monkeypatch, changed):
    ctx = managed_notice
    entered, release = asyncio.Event(), asyncio.Event()
    original, count = runtime._notice_guard, 0
    async def held(db, values):
        nonlocal count
        await original(db, values)
        count += 1
        if count == 1:
            entered.set()
            await release.wait()
    monkeypatch.setattr(runtime, "_notice_guard", held)
    task = asyncio.create_task(runtime.interaction(ctx.tenant, PHONE, channel_identity=ctx.identity))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        params = {"t": ctx.tenant, "a": ctx.account, "c": ctx.channel}
        async with ctx.migration.connect() as competitor:
            with pytest.raises(DBAPIError) as blocked:
                async with competitor.begin():
                    await competitor.execute(text("SET LOCAL lock_timeout='150ms'"))
                    await competitor.execute(text(REVOKE[changed]), params)
            assert getattr(blocked.value.orig, "sqlstate", None) == "55P03"
        release.set()
        await asyncio.wait_for(task, 5)
        after = await facts(ctx)
        assert after["jobs"][0]["status"] == "cancelled"
        async with ctx.migration.begin() as competitor:
            await competitor.execute(text(REVOKE[changed]), params)
        with pytest.raises(HTTPException) as denied:
            await runtime.interaction(ctx.tenant, PHONE, channel_identity=ctx.identity)
        assert denied.value.status_code == 409 and await facts(ctx) == after
    finally:
        release.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_notice_expiring_during_write_rolls_back_contact_and_pending_jobs(managed_notice, monkeypatch):
    ctx = managed_notice
    async with ctx.migration.begin() as db:
        await db.execute(text("UPDATE qr_channels SET lease_expires_at=clock_timestamp()+interval '2 seconds' WHERE id=:id"), {"id": ctx.channel})
    before = await facts(ctx)
    original, count = runtime._notice_guard, 0
    async def delayed(db, values):
        nonlocal count
        count += 1
        if count == 2: await db.execute(text("SELECT pg_sleep(2.1)"))
        await original(db, values)
    monkeypatch.setattr(runtime, "_notice_guard", delayed)
    with pytest.raises(HTTPException) as denied:
        await runtime.interaction(ctx.tenant, PHONE, channel_identity=ctx.identity)
    assert count == 2 and denied.value.status_code == 409 and await facts(ctx) == before and not ctx.sent


@pytest.mark.asyncio
async def test_runtime_guard_rejects_wrong_tenant_guc_and_unlisted_sender(managed_notice):
    ctx = managed_notice
    values = runtime._notice_identity(ctx.tenant, PHONE, ctx.identity)
    before = await facts(ctx)
    async with ctx.factory() as db:
        async with tenant_transaction(db, ctx.other):
            with pytest.raises(HTTPException): await runtime._notice_guard(db, values)
    with pytest.raises(HTTPException):
        await runtime.interaction(ctx.tenant, "+5567999999902", channel_identity=ctx.identity)
    with pytest.raises(HTTPException):
        await runtime.interaction(ctx.other, PHONE, channel_identity=ctx.identity)
    assert await facts(ctx) == before


@pytest.mark.asyncio
async def test_worker_observes_committed_notice_and_never_sends(managed_notice):
    ctx = managed_notice
    await runtime.interaction(ctx.tenant, PHONE, channel_identity=ctx.identity)
    assert await runtime.process_automation_job({"automation_sender": ctx.sender}, str(ctx.tenant), str(ctx.job["id"])) == "already_claimed"
    assert not ctx.sent


@pytest.mark.asyncio
async def test_vacancy_request_cannot_enable_or_plan_until_removed_and_reviewed(managed_notice, monkeypatch):
    from app import automation_api as api
    ctx = managed_notice
    async with ctx.migration.begin() as db:
        await db.execute(text("""UPDATE management_preferences SET preferences=jsonb_set(preferences,
            '{automations,vacancy_requested}','true'::jsonb),version=2 WHERE tenant_id=:t"""), {"t": ctx.tenant})
        # Even a preexisting/legacy approved flag cannot grant this capability.
        await db.execute(text("UPDATE automation_execution SET reviewed_preferences_version=2 WHERE tenant_id=:t"), {"t": ctx.tenant})
    status = await api.get_execution(ctx.tenant, ctx.actor)
    assert not status["enabled"] and status["reason"] == "waitlist_contract_missing"
    assert status["features"]["vacancy"] == {"available": False, "enabled": False, "mode": "draft", "reason": "waitlist_contract_missing"}
    assert await runtime.plan_tenant(ctx.tenant) == 0 and not (await queue(ctx))[0]
    assert await runtime.process_automation_job({"automation_sender": ctx.sender}, str(ctx.tenant), str(ctx.job["id"])) == "waitlist_contract_missing"
    assert not ctx.sent
    async def checked(*args): return {}
    monkeypatch.setattr(api, "checked_channel", checked)
    enable = api.ExecutionSave(expected_version=1, expected_preferences_version=2, idempotency_key=str(uuid4()),
        enabled=True, channel_id=ctx.channel, acknowledged=True)
    with pytest.raises(HTTPException) as denied:
        await api.save_execution(ctx.tenant, enable, ctx.actor)
    assert denied.value.status_code == 409
    # Owner can always pause. No jobs are recreated and no opt-in is added.
    pause = enable.model_copy(update={"enabled": False, "idempotency_key": str(uuid4())})
    assert not (await api.save_execution(ctx.tenant, pause, ctx.actor))["enabled"]
    async with ctx.migration.begin() as db:
        await db.execute(text("""UPDATE management_preferences SET preferences=jsonb_set(preferences,
            '{automations,vacancy_requested}','false'::jsonb),version=3 WHERE tenant_id=:t"""), {"t": ctx.tenant})
    assert not (await api.get_execution(ctx.tenant, ctx.actor))["enabled"]
    fresh = enable.model_copy(update={"expected_version": 2, "expected_preferences_version": 3, "idempotency_key": str(uuid4())})
    assert (await api.save_execution(ctx.tenant, fresh, ctx.actor))["enabled"]
    assert not ctx.sent
