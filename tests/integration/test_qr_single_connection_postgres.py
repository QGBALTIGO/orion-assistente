"""Canonical connection invariants against isolated PostgreSQL and real roles."""
import asyncio
import importlib.util
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import HTTPException, Response
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app import qr_channels as channels, qr_legacy
from app.database import tenant_transaction
from test_qr_channels_postgres import channel_case  # noqa: F401
from test_business_profiles_postgres import profiles  # noqa: F401

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def licensed_channel_case(channel_case):
    from plan_fixtures import seed_fixture_plan, clear_fixture_plans
    c=channel_case
    async with c.migration.begin() as db:
        actor=await seed_fixture_plan(db,(c.a,),"elite")
    try:
        yield c
    finally:
        async with c.migration.begin() as db:
            await clear_fixture_plans(db,(c.a,),(actor,))


async def provision(c, key=None, tenant=None):
    target = tenant or c.a
    actor = c.actor_b if target == c.b else c.actor
    return await channels.provision_connection(target,
        channels.ProvisionConnection(idempotency_key=key or uuid4()), Response(), actor)


@pytest.mark.asyncio
async def test_concurrent_distinct_keys_provision_only_one_disabled_identity(channel_case):
    c = channel_case
    results = await asyncio.gather(*(provision(c) for _ in range(12)))
    identities = {value["canonical_id"] for value in results}
    assert len(identities) == 1
    assert all(len(value["items"]) == 1 and value["connection"]["enabled"] is False and
        value["connection"]["assistant_enabled"] is False and value["connection"]["assistant_activation_allowed"] is False
        for value in results)
    assert all("credential" not in str(value) and "allowed_senders" not in str(value) for value in results)
    async with c.migration.connect() as db:
        assert (await db.execute(text("SELECT count(*) FROM qr_channels WHERE tenant_id=:t"), {"t": c.a})).scalar_one() == 1
        stored = (await db.execute(text("SELECT allowed_senders,enabled,assistant_enabled FROM qr_channels WHERE tenant_id=:t"), {"t": c.a})).one()
        assert stored.allowed_senders == [] and stored.enabled is False and stored.assistant_enabled is False


@pytest.mark.asyncio
async def test_disabled_existing_connection_is_reused_without_replacing_identity_or_auth(channel_case):
    c = channel_case
    first = await provision(c)
    async with c.migration.connect() as db:
        before = (await db.execute(text("SELECT id,credential_hash,credential_encrypted,revision,enabled,assistant_enabled FROM qr_channels WHERE tenant_id=:t"), {"t": c.a})).one()
    again = await provision(c)
    assert again["canonical_id"] == first["canonical_id"] and again["connection"]["enabled"] is False
    async with c.migration.connect() as db:
        after = (await db.execute(text("SELECT id,credential_hash,credential_encrypted,revision,enabled,assistant_enabled FROM qr_channels WHERE tenant_id=:t"), {"t": c.a})).one()
    assert before == after


@pytest.mark.asyncio
async def test_binding_fk_cannot_cross_tenants_and_policy_constraint_denies_unrestricted_activation(licensed_channel_case):
    c = licensed_channel_case
    a, b = await provision(c), await provision(c, tenant=c.b)
    async with c.migration.begin() as db:
        with pytest.raises(DBAPIError):
            async with db.begin_nested():
                await db.execute(text("UPDATE tenants SET whatsapp_channel_id=:id WHERE id=:t"), {"id": b["canonical_id"], "t": c.a})
        with pytest.raises(DBAPIError):
            async with db.begin_nested():
                await db.execute(text("UPDATE qr_channels SET assistant_enabled=true WHERE id=:id"), {"id": a["canonical_id"]})
    with pytest.raises(HTTPException) as caught:
        await channels.change_state(c.a, UUID(a["connection"]["id"]), channels.ChannelState(expected_revision=1, assistant_enabled=True), c.actor)
    assert caught.value.status_code == 409


@pytest.mark.asyncio
async def test_history_remains_intact_but_cannot_claim_start_pair_or_activate(channel_case):
    c = channel_case
    current = await provision(c)
    identity = current["canonical_id"]
    async with c.migration.begin() as db:
        historical = (await db.execute(text("""INSERT INTO qr_channels
            (tenant_id,owner_account_id,provider,label,credential_hash,credential_encrypted,allowed_senders,idempotency_key,request_fingerprint,enabled)
            SELECT tenant_id,owner_account_id,provider,'Historical',credential_hash,credential_encrypted,allowed_senders,:key,request_fingerprint,true
            FROM qr_channels WHERE id=:id RETURNING id,credential_encrypted"""), {"key": uuid4(), "id": identity})).one()
    history = historical.id
    secret = channels._cipher().decrypt(historical.credential_encrypted.encode()).decode()
    listing = await channels.list_channels(c.a, Response(), c.actor)
    assert listing["canonical_id"] == identity and len(listing["items"]) == 1
    for action in ("start", "qr", "status"):
        with pytest.raises(HTTPException) as caught:
            await channels._control(c.a, history, c.actor, action, 1)
        assert caught.value.status_code == 409
    with pytest.raises(HTTPException):
        await channels.change_state(c.a, history, channels.ChannelState(expected_revision=1, enabled=True), c.actor)
    with pytest.raises(HTTPException):
        await channels.pair_control(c.a, history, channels.ChannelPairCommand(expected_revision=1, phone="5567999999901"), Response(), c.actor)
    async with c.support() as db:
        with pytest.raises(HTTPException):
            await channels.lease_operation(db, history, secret, channels.LeaseRequest(owner=uuid4()), "claim")
    async with c.support() as db:
        receipt = await channels.lease_operation(db, history, secret, channels.LeaseRequest(owner=uuid4()), "receipt")
    assert receipt["read_only"] is True
    async with c.migration.connect() as db:
        after = (await db.execute(text("SELECT credential_encrypted,revision,enabled FROM qr_channels WHERE id=:id"), {"id": history})).one()
        assert after.credential_encrypted == historical.credential_encrypted and after.revision == 1 and after.enabled is True


@pytest.mark.asyncio
async def test_ambiguous_active_registries_do_not_get_a_pointer_or_new_socket(channel_case):
    c = channel_case
    first = await provision(c)
    async with c.migration.begin() as db:
        await db.execute(text("UPDATE tenants SET whatsapp_channel_id=NULL WHERE id=:t"), {"t": c.a})
        await db.execute(text("""INSERT INTO qr_channels
            (tenant_id,owner_account_id,provider,label,credential_hash,credential_encrypted,allowed_senders,idempotency_key,request_fingerprint,enabled)
            SELECT tenant_id,owner_account_id,provider,'Historical',credential_hash,credential_encrypted,allowed_senders,:key,request_fingerprint,true
            FROM qr_channels WHERE id=:id"""), {"key": uuid4(), "id": first["canonical_id"]})
        await db.execute(text("UPDATE qr_channels SET enabled=true WHERE tenant_id=:t"), {"t": c.a})
    listing = await channels.list_channels(c.a, Response(), c.actor)
    assert listing["canonical_state"] == "ambiguous" and listing["connection"] is None and listing["items"] == []
    assert not any(listing["capabilities"].values())
    with pytest.raises(HTTPException) as caught:
        await provision(c)
    assert caught.value.status_code == 409
    async with c.migration.connect() as db:
        assert (await db.execute(text("SELECT whatsapp_channel_id FROM tenants WHERE id=:t"), {"t": c.a})).scalar_one() is None


@pytest.mark.asyncio
async def test_pointer_is_tenant_scoped_but_credentials_and_policy_remain_private(channel_case):
    c = channel_case
    a, b = await provision(c), await provision(c, tenant=c.b)
    async with c.runtime() as db:
        async with tenant_transaction(db, c.a):
            values = (await db.execute(text("SELECT whatsapp_channel_id FROM tenants"))).scalars().all()
            assert len(values) == 1 and str(values[0]) == a["canonical_id"]
    async with c.runtime() as db:
        with pytest.raises(DBAPIError):
            async with tenant_transaction(db, c.a):
                await db.execute(text("SELECT allowed_senders,credential_encrypted FROM qr_channels"))
    async with c.runtime() as db:
        with pytest.raises(DBAPIError):
            async with tenant_transaction(db, c.a):
                await db.execute(text("UPDATE tenants SET whatsapp_channel_id=NULL WHERE id=:id"), {"id": c.a})


@pytest.mark.asyncio
async def test_support_role_cannot_bypass_single_connection_by_using_the_old_insert_or_activation_sql(channel_case):
    c = channel_case
    current = await provision(c)
    duplicate = text("""INSERT INTO qr_channels
        (tenant_id,owner_account_id,provider,label,credential_hash,credential_encrypted,allowed_senders,idempotency_key,request_fingerprint)
        SELECT tenant_id,owner_account_id,provider,'Extra',credential_hash,credential_encrypted,allowed_senders,:key,request_fingerprint
        FROM qr_channels WHERE id=:id RETURNING id""")
    async with c.support() as db:
        async with db.begin():
            with pytest.raises(DBAPIError):
                async with db.begin_nested():
                    await db.execute(duplicate, {"key": uuid4(), "id": current["canonical_id"]})
    # Migrator-only historical import is retained for audit/recovery. It does
    # not give an old API process authority to activate that historical row.
    async with c.migration.begin() as db:
        historical = (await db.execute(duplicate, {"key": uuid4(), "id": current["canonical_id"]})).scalar_one()
    async with c.support() as db:
        async with db.begin():
            for column in ("enabled", "assistant_enabled"):
                with pytest.raises(DBAPIError):
                    async with db.begin_nested():
                        await db.execute(text(f"UPDATE qr_channels SET {column}=true WHERE id=:id"), {"id": historical})
            with pytest.raises(DBAPIError):
                async with db.begin_nested():
                    await db.execute(text("UPDATE qr_channels SET lease_owner=:owner,fence=fence+1,lease_expires_at=clock_timestamp()+interval '30 seconds' WHERE id=:id"),
                        {"owner": uuid4(), "id": historical})
    async with c.migration.connect() as db:
        assert (await db.execute(text("SELECT count(*) FROM qr_channels WHERE tenant_id=:tenant"), {"tenant": c.a})).scalar_one() == 2


@pytest.mark.asyncio
async def test_existing_ambiguous_live_lease_can_renew_and_release_but_not_be_reclaimed(channel_case):
    c = channel_case
    first = await provision(c)
    owner = uuid4()
    async with c.migration.begin() as db:
        await db.execute(text("UPDATE tenants SET whatsapp_channel_id=NULL WHERE id=:tenant"), {"tenant": c.a})
        await db.execute(text("UPDATE qr_channels SET enabled=true,lease_owner=:owner,fence=1,lease_expires_at=clock_timestamp()+interval '30 seconds' WHERE id=:id"),
            {"owner": owner, "id": first["canonical_id"]})
        await db.execute(text("""INSERT INTO qr_channels
            (tenant_id,owner_account_id,provider,label,credential_hash,credential_encrypted,allowed_senders,idempotency_key,request_fingerprint,enabled)
            SELECT tenant_id,owner_account_id,provider,'Historical',credential_hash,credential_encrypted,allowed_senders,:key,request_fingerprint,true
            FROM qr_channels WHERE id=:id"""), {"key": uuid4(), "id": first["canonical_id"]})
        encrypted = (await db.execute(text("SELECT credential_encrypted FROM qr_channels WHERE id=:id"), {"id": first["canonical_id"]})).scalar_one()
    key = channels._cipher().decrypt(encrypted.encode()).decode()
    identity = UUID(first["canonical_id"])
    for operation in ("renew", "check"):
        async with c.support() as db:
            result = await channels.lease_operation(db, identity, key, channels.LeaseRequest(owner=owner, fence=1), operation)
        assert result["fence"] == 1 and result["owner"] == str(owner) and result["expires_in_ms"] > 0
    async with c.support() as db:
        async with db.begin():
            await db.execute(text("UPDATE qr_channels SET lease_expires_at=clock_timestamp()+interval '30 seconds' WHERE id=:id"), {"id": first["canonical_id"]})
            with pytest.raises(DBAPIError):
                async with db.begin_nested():
                    await db.execute(text("UPDATE qr_channels SET lease_owner=:owner,fence=fence+1 WHERE id=:id"), {"owner": uuid4(), "id": first["canonical_id"]})
    async with c.support() as db:
        released = await channels.lease_operation(db, identity, key, channels.LeaseRequest(owner=owner, fence=1), "release")
    assert released["expires_in_ms"] == 0
    async with c.support() as db:
        with pytest.raises(HTTPException) as caught:
            await channels.lease_operation(db, identity, key, channels.LeaseRequest(owner=owner), "claim")
    assert caught.value.status_code == 409


@pytest.mark.asyncio
async def test_resolved_ambiguity_is_bound_only_by_explicit_claim_not_read_only_get(channel_case):
    c = channel_case
    first = await provision(c)
    identity = UUID(first["canonical_id"])
    async with c.migration.begin() as db:
        await db.execute(text("UPDATE tenants SET whatsapp_channel_id=NULL WHERE id=:tenant"), {"tenant": c.a})
        await db.execute(text("UPDATE qr_channels SET enabled=true WHERE id=:id"), {"id": identity})
        history = (await db.execute(text("""INSERT INTO qr_channels
            (tenant_id,owner_account_id,provider,label,credential_hash,credential_encrypted,allowed_senders,idempotency_key,request_fingerprint,enabled)
            SELECT tenant_id,owner_account_id,provider,'Historical',credential_hash,credential_encrypted,allowed_senders,:key,request_fingerprint,true
            FROM qr_channels WHERE id=:id RETURNING id"""), {"key": uuid4(), "id": identity})).scalar_one()
        encrypted = (await db.execute(text("SELECT credential_encrypted FROM qr_channels WHERE id=:id"), {"id": identity})).scalar_one()
    assert (await channels.list_channels(c.a, Response(), c.actor))["canonical_state"] == "ambiguous"
    async with c.migration.begin() as db:
        await db.execute(text("UPDATE qr_channels SET enabled=false WHERE id=:id"), {"id": history})
    listing = await channels.list_channels(c.a, Response(), c.actor)
    assert listing["canonical_state"] == "ready" and listing["canonical_id"] == str(identity)
    async with c.migration.connect() as db:
        assert (await db.execute(text("SELECT whatsapp_channel_id FROM tenants WHERE id=:tenant"), {"tenant": c.a})).scalar_one() is None
    key = channels._cipher().decrypt(encrypted.encode()).decode()
    async with c.support() as db:
        claimed = await channels.lease_operation(db, identity, key, channels.LeaseRequest(owner=uuid4()), "claim")
    assert claimed["fence"] == 1
    async with c.migration.connect() as db:
        assert (await db.execute(text("SELECT whatsapp_channel_id FROM tenants WHERE id=:tenant"), {"tenant": c.a})).scalar_one() == identity
        assert (await db.execute(text("SELECT enabled FROM qr_channels WHERE id=:id"), {"id": history})).scalar_one() is False


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["legacy_history", "single_disabled", "single_active", "ambiguous_active", "ambiguous_disabled"])
async def test_migration_backfill_preserves_auth_and_states_and_never_chooses_ambiguity(channel_case, case):
    c = channel_case
    first = await provision(c)
    original = UUID(first["canonical_id"])
    alias = qr_legacy.channel_id(c.a)
    async with c.migration.begin() as db:
        await db.execute(text("UPDATE tenants SET whatsapp_channel_id=NULL WHERE id=:t"), {"t": c.a})
        if case == "legacy_history":
            await db.execute(text("UPDATE qr_channels SET id=:alias,enabled=true WHERE id=:id"), {"alias": alias, "id": original})
            original = alias
        elif case in {"single_active", "ambiguous_active"}:
            await db.execute(text("UPDATE qr_channels SET enabled=true WHERE id=:id"), {"id": original})
        if case != "single_disabled":
            await db.execute(text("""INSERT INTO qr_channels
                (tenant_id,owner_account_id,provider,label,credential_hash,credential_encrypted,allowed_senders,idempotency_key,request_fingerprint,enabled)
                SELECT tenant_id,owner_account_id,provider,'Historical',credential_hash,credential_encrypted,allowed_senders,:key,request_fingerprint,:enabled
                FROM qr_channels WHERE id=:id"""), {"key": uuid4(), "id": original, "enabled": case == "ambiguous_active"})
        before = (await db.execute(text("SELECT * FROM qr_channels WHERE tenant_id=:t ORDER BY id"), {"t": c.a})).mappings().all()
    spec = importlib.util.spec_from_file_location("single_whatsapp_migration", Path(__file__).resolve().parents[2] / "alembic/versions/0044_single_whatsapp_connection.py")
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    async with c.migration.begin() as db:
        await db.run_sync(migration.backfill_bindings)
        selected = (await db.execute(text("SELECT whatsapp_channel_id FROM tenants WHERE id=:t"), {"t": c.a})).scalar_one()
        after = (await db.execute(text("SELECT * FROM qr_channels WHERE tenant_id=:t ORDER BY id"), {"t": c.a})).mappings().all()
    assert before == after
    assert selected == (None if case.startswith("ambiguous") else original)
