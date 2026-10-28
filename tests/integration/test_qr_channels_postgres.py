"""Distributed fencing, account revocation and RLS against real local PostgreSQL."""
import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from fastapi import HTTPException, Response
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from starlette.requests import Request

from app import qr_channels as channels
from app.catalog_api import Actor, support_session_factory
from app.database import tenant_transaction
from test_business_profiles_postgres import profiles  # noqa: F401

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def channel_case(profiles, monkeypatch):
    a, b, account, runtime, migration = profiles
    monkeypatch.setenv("QR_CHANNELS_ENABLED", "1")
    monkeypatch.setenv("BUSINESS_PROFILES_ENABLED", "1")
    monkeypatch.setenv("INTEGRATION_FERNET_KEY", Fernet.generate_key().decode())
    async with migration.begin() as db:
        for tenant in (a, b):
            await db.execute(text("INSERT INTO tenant_memberships(tenant_id,account_id,role,active) VALUES(:tenant,:account,'owner',true)"),
                             {"tenant": tenant, "account": account})
    case = SimpleNamespace(a=a, b=b, account=account, runtime=runtime, migration=migration,
                           support=support_session_factory(), actor=Actor("member", a, account, "owner"))
    try:
        yield case
    finally:
        async with migration.begin() as db:
            await db.execute(text("UPDATE tenants SET whatsapp_channel_id=NULL WHERE id IN (:a,:b)"), {"a": a, "b": b})
            await db.execute(text("DELETE FROM qr_channels WHERE tenant_id IN (:a,:b)"), {"a": a, "b": b})


async def create(case, *, tenant=None, key=None, activate=True):
    payload = channels.CreateChannel(label="Synthetic channel", allowed_senders=["5567999999901"], idempotency_key=key or uuid4())
    async with case.support() as db:
        row = await channels.create_channel(db, tenant or case.a, payload, case.actor)
    if activate and row["credential"]:
        await channels.change_state(tenant or case.a, row["id"], channels.ChannelState(expected_revision=1, enabled=True), case.actor)
    return row, payload


async def lease(case, row, owner, action="claim", fence=None, key=None):
    async with case.support() as db:
        return await channels.lease_operation(db, row["id"], key or row["credential"],
                                             channels.LeaseRequest(owner=owner, fence=fence), action)


def request(row, owner, fence):
    return Request({"type": "http", "method": "POST", "path": "/", "path_params": {"channel_id": str(row["id"])},
                    "headers": [(b"x-orion-channel-key", row["credential"].encode()),
                                (b"x-orion-channel-owner", str(owner).encode()),
                                (b"x-orion-channel-fence", str(fence).encode())]})


@pytest.mark.asyncio
async def test_creation_is_disabled_idempotent_and_secret_is_encrypted(channel_case):
    c = channel_case
    row, payload = await create(c, activate=False)
    assert row["enabled"] is False and row["assistant_enabled"] is False and row["credential_issued"] is True
    async with c.support() as db:
        repeated = await channels.create_channel(db, c.a, payload, c.actor)
        assert repeated["id"] == row["id"] and repeated["credential"] is None
        stored = (await db.execute(text("SELECT credential_hash,credential_encrypted FROM qr_channels WHERE id=:id"), {"id": row["id"]})).one()
        assert row["credential"] not in stored.credential_encrypted
        assert channels._cipher().decrypt(stored.credential_encrypted.encode()).decode() == row["credential"]
    with pytest.raises(HTTPException):
        await lease(c, row, uuid4())


@pytest.mark.asyncio
async def test_two_owners_cannot_hold_same_channel_distributed(channel_case):
    c = channel_case; row, _ = await create(c)
    values = await asyncio.gather(*(lease(c, row, uuid4()) for _ in range(12)), return_exceptions=True)
    winners = [v for v in values if isinstance(v, dict)]
    assert len(winners) == 1
    assert all(isinstance(v, dict) or isinstance(v, HTTPException) and v.status_code == 409 for v in values)


@pytest.mark.asyncio
async def test_expiry_takeover_fences_old_renew_send_and_release(channel_case):
    c = channel_case; row, _ = await create(c); old, new = uuid4(), uuid4()
    first = await lease(c, row, old)
    async with c.migration.begin() as db:
        await db.execute(text("UPDATE qr_channels SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=:id"), {"id": row["id"]})
    second = await lease(c, row, new)
    assert second["fence"] > first["fence"]
    for action in ("renew", "release", "check"):
        with pytest.raises(HTTPException) as exc:
            await lease(c, row, old, action, first["fence"])
        assert exc.value.status_code == 409
    with pytest.raises(HTTPException):
        await channels.authenticated_channel(request(row, old, first["fence"]), phone="5567999999901")
    assert (await lease(c, row, new, "check", second["fence"]))["owner"] == str(new)


@pytest.mark.asyncio
async def test_credential_scopes_tenant_sender_and_context(channel_case):
    c = channel_case; a, _ = await create(c); b, _ = await create(c, tenant=c.b); owner = uuid4()
    first = await lease(c, a, owner)
    with pytest.raises(HTTPException) as exc:
        await lease(c, b, uuid4(), key=a["credential"])
    assert exc.value.status_code == 401
    identity = await channels.authenticated_channel(request(a, owner, first["fence"]), phone="+5567999999901")
    assert identity.tenant_id == c.a and identity.channel_id == a["id"] and identity.allowed_sender
    with pytest.raises(HTTPException) as exc:
        await channels.authenticated_channel(request(a, owner, first["fence"]), phone="5567999999902")
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_revoked_owner_stops_new_reads_leases_and_credentials(channel_case):
    c = channel_case; row, _ = await create(c); owner = uuid4(); held = await lease(c, row, owner)
    async with c.migration.begin() as db:
        await db.execute(text("UPDATE tenant_memberships SET active=false WHERE tenant_id=:tenant AND account_id=:account"),
                         {"tenant": c.a, "account": c.account})
    with pytest.raises(HTTPException):
        await lease(c, row, owner, "renew", held["fence"])
    with pytest.raises(HTTPException):
        await channels.outbound_channel_credentials(c.a, row["id"])
    with pytest.raises(HTTPException):
        await channels.authenticated_channel(request(row, owner, held["fence"]), phone="5567999999901")


@pytest.mark.asyncio
async def test_runtime_rls_cannot_read_other_channel_or_secret(channel_case):
    c = channel_case; a, _ = await create(c); await create(c, tenant=c.b)
    async with c.runtime() as db:
        async with tenant_transaction(db, c.a):
            assert (await db.execute(text("SELECT id FROM qr_channels"))).scalars().all() == [a["id"]]
    async with c.runtime() as db:
        with pytest.raises(DBAPIError):
            async with tenant_transaction(db, c.a):
                await db.execute(text("SELECT credential_hash,credential_encrypted FROM qr_channels"))


@pytest.mark.asyncio
async def test_explicit_disable_invalidates_lease_and_stale_revision(channel_case):
    c = channel_case; row, _ = await create(c); owner = uuid4(); held = await lease(c, row, owner)
    await channels.change_state(c.a, row["id"], channels.ChannelState(expected_revision=2, enabled=False), c.actor)
    with pytest.raises(HTTPException):
        await lease(c, row, owner, "renew", held["fence"])
    with pytest.raises(HTTPException) as exc:
        await channels.change_state(c.a, row["id"], channels.ChannelState(expected_revision=2, enabled=True), c.actor)
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_outbound_credentials_are_server_only_and_scope_checked(channel_case):
    c = channel_case; row, _ = await create(c)
    value = await channels.outbound_channel_credentials(c.a, row["id"])
    assert value["key"] == row["credential"] and value["path"].endswith('/send')
    with pytest.raises(HTTPException):
        await channels.outbound_channel_credentials(c.b, row["id"])
    listing = await channels.list_channels(c.a, Response(), c.actor)
    assert row["credential"] not in str(listing)
    assert "credential_encrypted" not in str(listing) and "credential_hash" not in str(listing)


@pytest.mark.asyncio
async def test_assistant_activation_is_separate_and_invalidates_old_fence(channel_case):
    c = channel_case; row, _ = await create(c); owner = uuid4(); held = await lease(c, row, owner)
    identity = await channels.authenticated_channel(request(row, owner, held["fence"]), phone="5567999999901")
    assert identity.assistant_enabled is False
    result = await channels.change_state(c.a, row["id"], channels.ChannelState(expected_revision=2, assistant_enabled=True), c.actor)
    assert result["enabled"] is True and result["assistant_enabled"] is True
    with pytest.raises(HTTPException):
        await lease(c, row, owner, "check", held["fence"])
    fresh = await lease(c, row, owner)
    assert (await channels.authenticated_channel(request(row, owner, fresh["fence"]), phone="5567999999901")).assistant_enabled is True


@pytest.mark.asyncio
@pytest.mark.parametrize("pause", ["channel", "tenant", "both"])
async def test_receipts_survive_pause_without_restoring_live_permissions(channel_case, pause):
    c = channel_case; row, _ = await create(c); owner = uuid4(); held = await lease(c, row, owner)
    if pause in {"channel", "both"}:
        await channels.change_state(c.a, row["id"], channels.ChannelState(expected_revision=2, enabled=False), c.actor)
    if pause in {"tenant", "both"}:
        async with c.migration.begin() as db:
            await db.execute(text("UPDATE tenants SET status='paused' WHERE id=:id"), {"id": c.a})
    async with c.migration.connect() as db:
        before = (await db.execute(text("SELECT enabled,fence,lease_owner,lease_expires_at,updated_at FROM qr_channels WHERE id=:id"), {"id": row["id"]})).mappings().one()
    result = await lease(c, row, owner, "receipt")
    assert result == {"channel_id": str(row["id"]), "tenant_id": str(c.a), "provider": "baileys", "read_only": True}
    credentials = await channels.outbound_channel_credentials(c.a, row["id"], read_only=True)
    assert credentials["key"] == row["credential"] and credentials["path"].endswith("/operations")
    for operation in ("claim", "renew", "check", "inspect"):
        with pytest.raises(HTTPException): await lease(c, row, owner, operation, held["fence"])
    with pytest.raises(HTTPException): await channels.outbound_channel_credentials(c.a, row["id"])
    with pytest.raises(HTTPException): await channels.authenticated_channel(request(row, owner, held["fence"]), phone="5567999999901")
    with pytest.raises(HTTPException): await channels.outbound_channel_credentials(c.b, row["id"], read_only=True)
    async with c.migration.connect() as db:
        after = (await db.execute(text("SELECT enabled,fence,lease_owner,lease_expires_at,updated_at FROM qr_channels WHERE id=:id"), {"id": row["id"]})).mappings().one()
    assert before == after


@pytest.mark.asyncio
async def test_receipt_grant_still_requires_current_membership_and_channel_credential(channel_case):
    c = channel_case; row, _ = await create(c, activate=False)
    with pytest.raises(HTTPException): await lease(c, row, uuid4(), "receipt", key="wrong-credential-"*4)
    async with c.migration.begin() as db:
        await db.execute(text("UPDATE tenant_memberships SET active=false WHERE tenant_id=:t AND account_id=:a"), {"t": c.a, "a": c.account})
    with pytest.raises(HTTPException): await lease(c, row, uuid4(), "receipt")
    with pytest.raises(HTTPException): await channels.outbound_channel_credentials(c.a, row["id"], read_only=True)
