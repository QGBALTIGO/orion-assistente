"""Native ADMIN QR authority against a disposable database, never a socket."""
from types import SimpleNamespace
from uuid import uuid4

from cryptography.fernet import Fernet
from fastapi import HTTPException, Response
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app import qr_channels as channels, qr_legacy
from app.catalog_api import Actor
from test_admin_identity_ops_postgres import (  # noqa: F401
    admin_source,
    admin_migration_source,
    communication_source,
    inbox_source,
    onboarding_source,
    management_source,
    fulfillment_source,
    migration_source,
)

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def native_qr(admin_source, monkeypatch):
    case = admin_source
    email_admin, foreign = uuid4(), uuid4()
    with case.engine.begin() as db:
        db.execute(text("""INSERT INTO panel_accounts
            (id,name,email,password_hash,role,auth_provider,legacy_username,legacy_credential_digest)
            VALUES (:account,'Synthetic native ADMIN',NULL,NULL,'admin','legacy_admin',:username,:digest)"""),
            {"account": case.admin, "username": case.settings.username,
             "digest": case.settings.credential_digest})
        db.execute(text("""INSERT INTO panel_accounts(id,name,email,password_hash,role)
            VALUES (:account,'Synthetic email admin',:email,'unused-test-hash','admin')"""),
            {"account": email_admin, "email": f"qr-admin-{email_admin}@example.test"})
        for account in (case.admin, email_admin):
            db.execute(text("""INSERT INTO tenant_memberships(tenant_id,account_id,role,active)
                VALUES (:tenant,:account,'owner',true)"""), {"tenant": case.tenant, "account": account})
        db.execute(text("INSERT INTO tenants(id,name,slug) VALUES (:id,'Synthetic QR foreign tenant',:slug)"),
                   {"id": foreign, "slug": f"qr-foreign-{foreign}"})
    support_url = case.support_url
    engine = create_async_engine(support_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    network = []

    async def no_bridge(*args):
        network.append(args)
        raise AssertionError("No socket or outbound transport allowed in isolated QR identity tests")

    monkeypatch.setattr(channels, "support_session_factory", lambda: factory)
    monkeypatch.setattr(channels, "_bridge_control", no_bridge)
    monkeypatch.setenv("QR_CHANNELS_ENABLED", "1")
    monkeypatch.setenv("INTEGRATION_FERNET_KEY", Fernet.generate_key().decode())
    value = SimpleNamespace(source=case, tenant=case.tenant, account=case.admin,
                            actor=Actor("admin", account_id=case.admin), email_admin=email_admin,
                            foreign=foreign, factory=factory, network=network)
    try:
        yield value
    finally:
        await engine.dispose()


async def create(case, *, tenant=None, actor=None):
    payload = channels.CreateChannel(label="Synthetic native ADMIN QR", allowed_senders=["5567999999901"],
                                     idempotency_key=uuid4())
    async with case.factory() as db:
        row = await channels.create_channel(db, tenant or case.tenant, payload, actor or case.actor)
    return row, payload


@pytest.mark.asyncio
async def test_real_native_account_can_own_list_and_change_only_its_qr(native_qr):
    case = native_qr
    row, payload = await create(case)
    assert row["owner_account_id"] == case.account and row["enabled"] is False
    assert row["assistant_enabled"] is False
    async with case.factory() as db:
        repeated = await channels.create_channel(db, case.tenant, payload, case.actor)
    assert repeated["id"] == row["id"] and repeated["credential"] is None
    listing = await channels.list_channels(case.tenant, Response(), case.actor)
    assert listing["access"]["can_manage"] is True
    assert listing["items"][0]["can_manage"] is True
    assert listing["items"][0]["allowed_senders"] == ["5567999999901"]
    assert row["credential"] not in str(listing)
    changed = await channels.change_state(case.tenant, row["id"],
                                          channels.ChannelState(expected_revision=1, assistant_enabled=True), case.actor)
    assert changed["assistant_enabled"] is True and changed["enabled"] is False
    assert "credential_encrypted" not in changed and "credential_hash" not in changed
    assert not case.network


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["revoked", "viewer", "foreign_tenant", "password_admin", "no_opt_in", "stale_configuration"])
async def test_real_admin_identity_never_replaces_current_exact_tenant_ownership(native_qr, monkeypatch, failure):
    case = native_qr
    row, _ = await create(case)
    actor, tenant = case.actor, case.tenant
    if failure in {"revoked", "viewer"}:
        with case.source.engine.begin() as db:
            db.execute(text("""UPDATE tenant_memberships SET active=:active,role=:role
                WHERE tenant_id=:tenant AND account_id=:account"""),
                {"tenant": case.tenant, "account": case.account, "active": failure != "revoked",
                 "role": "viewer" if failure == "viewer" else "owner"})
    elif failure == "foreign_tenant":
        tenant = case.foreign
    elif failure == "password_admin":
        actor = Actor("admin", account_id=case.email_admin)
    elif failure == "no_opt_in":
        monkeypatch.setenv("PANEL_LEGACY_ADMIN_IDENTITY_ENABLED", "0")
    else:
        monkeypatch.setenv("PANEL_ADMIN_USERNAME", "different-synthetic-admin")
    with pytest.raises(HTTPException) as caught:
        await create(case, tenant=tenant, actor=actor)
    assert caught.value.status_code == 403
    listing = await channels.list_channels(tenant, Response(), actor)
    assert listing["access"]["can_manage"] is False
    assert all(item["can_manage"] is False and "allowed_senders" not in item for item in listing["items"])
    with pytest.raises(HTTPException) as caught:
        await channels._owned_channel(tenant, row["id"], actor)
    assert caught.value.status_code == 403 and not case.network


@pytest.mark.asyncio
@pytest.mark.parametrize("role,has_id", [("support", False), ("support", True), ("viewer", True), ("admin", False)])
async def test_real_support_or_unidentified_admin_never_creates_native_owned_qr(native_qr, role, has_id):
    case = native_qr
    actor = Actor(role, account_id=case.account if has_id else None)
    with pytest.raises(HTTPException) as caught:
        await create(case, actor=actor)
    assert caught.value.status_code == 403 and not case.network
    with case.source.engine.connect() as db:
        count = db.execute(text("SELECT count(*) FROM qr_channels WHERE tenant_id=:tenant"),
                           {"tenant": case.tenant}).scalar_one()
    assert count == 0


@pytest.mark.asyncio
async def test_real_native_owner_cannot_manage_another_owners_channel(native_qr):
    case = native_qr
    row, _ = await create(case)
    with case.source.engine.begin() as db:
        db.execute(text("UPDATE qr_channels SET owner_account_id=:owner WHERE id=:channel"),
                   {"owner": case.email_admin, "channel": row["id"]})
    listing = await channels.list_channels(case.tenant, Response(), case.actor)
    assert listing["access"]["can_manage"] is True
    assert listing["items"][0]["can_manage"] is False and "allowed_senders" not in listing["items"][0]
    with pytest.raises(HTTPException) as caught:
        await channels._owned_channel(case.tenant, row["id"], case.actor)
    assert caught.value.status_code == 404 and not case.network


@pytest.mark.asyncio
async def test_real_revocation_during_qr_lookup_discards_the_result(native_qr, monkeypatch):
    case = native_qr
    row, _ = await create(case)
    with case.source.engine.begin() as db:
        db.execute(text("UPDATE qr_channels SET enabled=true WHERE id=:channel"), {"channel": row["id"]})

    async def response_after_revocation(*args):
        with case.source.engine.begin() as db:
            db.execute(text("UPDATE tenant_memberships SET active=false WHERE tenant_id=:tenant AND account_id=:account"),
                       {"tenant": case.tenant, "account": case.account})
        return {"image": "data:image/png;base64,iVBORw0KGgo=", "expires_in_ms": 30000}

    monkeypatch.setattr(channels, "_bridge_control", response_after_revocation)
    with pytest.raises(HTTPException) as caught:
        await channels._control(case.tenant, row["id"], case.actor, "qr")
    assert caught.value.status_code == 403


@pytest.mark.asyncio
async def test_real_native_owner_keeps_primary_credentials_and_never_repairs_or_disconnects(native_qr):
    case = native_qr
    row, _ = await create(case)
    alias = qr_legacy.channel_id(case.tenant)
    with case.source.engine.begin() as db:
        db.execute(text("UPDATE qr_channels SET id=:alias,enabled=true WHERE id=:channel"),
                   {"alias": alias, "channel": row["id"]})
        before = db.execute(text("SELECT credential_hash,credential_encrypted,enabled FROM qr_channels WHERE id=:id"),
                            {"id": alias}).mappings().one()
    for action in ("start", "stop", "qr"):
        with pytest.raises(HTTPException) as caught:
            await channels._control(case.tenant, alias, case.actor, action)
        assert caught.value.status_code == 409
    with pytest.raises(HTTPException) as caught:
        await channels.change_state(case.tenant, alias, channels.ChannelState(expected_revision=1, enabled=False), case.actor)
    assert caught.value.status_code == 409
    assistant = await channels.change_state(case.tenant, alias,
                                            channels.ChannelState(expected_revision=1, assistant_enabled=True), case.actor)
    assert assistant["enabled"] is True and assistant["assistant_enabled"] is True
    with case.source.engine.connect() as db:
        after = db.execute(text("SELECT credential_hash,credential_encrypted,enabled FROM qr_channels WHERE id=:id"),
                           {"id": alias}).mappings().one()
    assert dict(before) == dict(after) and not case.network
