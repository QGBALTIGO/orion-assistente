"""One account cannot acquire two businesses through distinct membership writers."""
import asyncio
import hashlib
import secrets
from uuid import uuid4

from fastapi import HTTPException, Response
import pytest
from sqlalchemy import text

from app import business_profile_api, management_team, onboarding_service
from app.business_profile_schemas import MembershipChange
from app.catalog_api import Actor
from tests.integration.test_onboarding_postgres import onboarding_db, creation

pytestmark = pytest.mark.integration


async def invitation(ctx, tenant, *, claimed=True):
    token = secrets.token_urlsafe(32)
    async with ctx.migration.begin() as db:
        await db.execute(text("UPDATE panel_accounts SET verified_at=now() WHERE id=:a"),
                         {"a": ctx.new_actor.account_id})
        row = (await db.execute(text("""INSERT INTO management_team_invitations
            (tenant_id,role,token_digest,idempotency_key,fingerprint,expires_at,claimed_by,status)
            VALUES(:t,'editor',:digest,:key,:digest,now()+interval '1 day',:account,:status)
            RETURNING id,version"""),
            {"t": tenant, "digest": hashlib.sha256(token.encode()).hexdigest(), "key": str(uuid4()),
             "account": ctx.new_actor.account_id if claimed else None,
             "status": "claimed" if claimed else "pending"})).mappings().one()
    return dict(row), token


async def owner_of_other_business(ctx):
    async with ctx.migration.begin() as db:
        await db.execute(text("INSERT INTO tenant_memberships(tenant_id,account_id,role) VALUES(:t,:a,'owner')"),
                         {"t": ctx.b, "a": ctx.other})
    return Actor("member", account_id=ctx.other)


async def guest_memberships(ctx):
    async with ctx.migration.connect() as db:
        return (await db.execute(text("SELECT tenant_id,role,active FROM tenant_memberships WHERE account_id=:a ORDER BY tenant_id"),
                                 {"a": ctx.new_actor.account_id})).all()


@pytest.mark.asyncio
async def test_accepting_foreign_invitation_does_not_claim_or_move_an_existing_membership(onboarding_db):
    ctx = onboarding_db
    row, token = await invitation(ctx, ctx.a, claimed=False)
    async with ctx.migration.begin() as db:
        await db.execute(text("INSERT INTO tenant_memberships(tenant_id,account_id,role) VALUES(:t,:a,'viewer')"),
                         {"t": ctx.b, "a": ctx.new_actor.account_id})
    with pytest.raises(HTTPException) as denied:
        await management_team.accept(management_team.InvitationToken(token=token), Response(), ctx.new_actor)
    assert denied.value.status_code == 409
    assert await guest_memberships(ctx) == [(ctx.b, "viewer", True)]
    async with ctx.migration.connect() as db:
        stored = (await db.execute(text("SELECT status,claimed_by,version FROM management_team_invitations WHERE id=:id"),
                                  {"id": row["id"]})).one()
        assert stored == ("pending", None, row["version"])


@pytest.mark.asyncio
async def test_approval_rechecks_business_assigned_after_the_invitation_was_claimed(onboarding_db):
    ctx = onboarding_db
    row, _ = await invitation(ctx, ctx.a)
    async with ctx.migration.begin() as db:
        await db.execute(text("INSERT INTO tenant_memberships(tenant_id,account_id,role) VALUES(:t,:a,'viewer')"),
                         {"t": ctx.b, "a": ctx.new_actor.account_id})
    with pytest.raises(HTTPException) as denied:
        await management_team.review(ctx.a, row["id"],
            management_team.InvitationReview(action="approve", expected_version=row["version"]), ctx.actor)
    assert denied.value.status_code == 409
    assert await guest_memberships(ctx) == [(ctx.b, "viewer", True)]
    async with ctx.migration.connect() as db:
        assert (await db.execute(text("SELECT status FROM management_team_invitations WHERE id=:id"),
                                 {"id": row["id"]})).scalar_one() == "claimed"


@pytest.mark.asyncio
async def test_concurrent_owners_in_different_businesses_can_only_approve_one_membership(onboarding_db):
    ctx = onboarding_db
    other_owner = await owner_of_other_business(ctx)
    first, _ = await invitation(ctx, ctx.a)
    second, _ = await invitation(ctx, ctx.b)
    results = await asyncio.gather(*(
        management_team.review(tenant, row["id"],
            management_team.InvitationReview(action="approve", expected_version=row["version"]), actor)
        for tenant, row, actor in ((ctx.a, first, ctx.actor), (ctx.b, second, other_owner))
    ), return_exceptions=True)
    assert sum(isinstance(r, dict) for r in results) == 1
    assert [r.status_code for r in results if isinstance(r, HTTPException)] == [409]
    rows = await guest_memberships(ctx)
    assert len(rows) == 1 and rows[0].role == "editor" and rows[0].active
    async with ctx.migration.connect() as db:
        assert (await db.execute(text("SELECT count(*) FROM tenant_membership_events WHERE account_id=:a"),
                                 {"a": ctx.new_actor.account_id})).scalar_one() == 1
        assert (await db.execute(text("SELECT count(*) FROM management_team_invitations WHERE claimed_by=:a AND status='approved'"),
                                 {"a": ctx.new_actor.account_id})).scalar_one() == 1


@pytest.mark.asyncio
async def test_concurrent_reactivation_in_two_businesses_preserves_the_losing_inactive_record(onboarding_db):
    ctx = onboarding_db
    other_owner = await owner_of_other_business(ctx)
    versions = {}
    async with ctx.migration.begin() as db:
        for tenant in (ctx.a, ctx.b):
            versions[tenant] = (await db.execute(text("""INSERT INTO tenant_memberships(tenant_id,account_id,role,active)
                VALUES(:t,:a,'editor',false) RETURNING updated_at"""),
                {"t": tenant, "a": ctx.new_actor.account_id})).scalar_one()
    results = await asyncio.gather(*(
        management_team.change_member(tenant, ctx.new_actor.account_id,
            management_team.MemberChange(role="editor", active=True, expected_updated_at=versions[tenant]), actor)
        for tenant, actor in ((ctx.a, ctx.actor), (ctx.b, other_owner))
    ), return_exceptions=True)
    assert sum(isinstance(r, dict) for r in results) == 1
    assert [r.status_code for r in results if isinstance(r, HTTPException)] == [409]
    rows = await guest_memberships(ctx)
    assert len(rows) == 2 and sum(row.active for row in rows) == 1


@pytest.mark.asyncio
async def test_support_membership_writer_cannot_add_another_active_business(onboarding_db):
    ctx = onboarding_db
    async with ctx.migration.begin() as db:
        await db.execute(text("INSERT INTO tenant_memberships(tenant_id,account_id,role) VALUES(:t,:a,'editor')"),
                         {"t": ctx.b, "a": ctx.new_actor.account_id})
    with pytest.raises(HTTPException) as denied:
        await business_profile_api.set_membership(ctx.a, ctx.new_actor.account_id,
            MembershipChange(role="owner", active=True), Actor("support"))
    assert denied.value.status_code == 409
    assert await guest_memberships(ctx) == [(ctx.b, "editor", True)]


@pytest.mark.asyncio
async def test_business_creation_and_support_assignment_share_the_account_lock(onboarding_db):
    ctx = onboarding_db
    results = await asyncio.gather(
        onboarding_service.create_business(ctx.new_actor, creation()),
        business_profile_api.set_membership(ctx.a, ctx.new_actor.account_id,
            MembershipChange(role="viewer", active=True), Actor("support")),
        return_exceptions=True,
    )
    assert sum(isinstance(r, dict) for r in results) == 1
    assert [r.status_code for r in results if isinstance(r, HTTPException)] == [409]
    rows = await guest_memberships(ctx)
    assert len(rows) == 1 and rows[0].active
    async with ctx.migration.connect() as db:
        created = (await db.execute(text("SELECT tenant_id FROM business_onboarding_creations WHERE account_id=:a"),
                                   {"a": ctx.new_actor.account_id})).scalars().all()
        assert created == ([] if rows[0].tenant_id == ctx.a else [rows[0].tenant_id])


@pytest.mark.asyncio
async def test_legacy_conflict_can_be_resolved_by_explicit_deactivation_without_deleting_data(onboarding_db):
    ctx = onboarding_db
    async with ctx.migration.begin() as db:
        for tenant in (ctx.a, ctx.b):
            await db.execute(text("INSERT INTO tenant_memberships(tenant_id,account_id,role) VALUES(:t,:a,'editor')"),
                             {"t": tenant, "a": ctx.new_actor.account_id})
    await business_profile_api.set_membership(ctx.a, ctx.new_actor.account_id,
        MembershipChange(role="editor", active=False), Actor("support"))
    rows = await guest_memberships(ctx)
    assert len(rows) == 2
    assert {(row.tenant_id, row.active) for row in rows} == {(ctx.a, False), (ctx.b, True)}


@pytest.mark.asyncio
async def test_native_admin_keeps_configured_context_without_registering_a_personal_business(onboarding_db, monkeypatch):
    ctx = onboarding_db
    async with ctx.migration.begin() as db:
        await db.execute(text("""UPDATE panel_accounts SET role='admin',auth_provider='legacy_admin',
            email=NULL,password_hash=NULL,legacy_username='synthetic-account-business-admin',
            legacy_credential_digest=:digest WHERE id=:account"""),
            {"digest": "a" * 64, "account": ctx.new_actor.account_id})
    actor = Actor("admin", account_id=ctx.new_actor.account_id)
    monkeypatch.setenv("PANEL_OPERATOR_TENANT_ID", str(ctx.a))
    businesses = await business_profile_api.my_businesses(actor)
    assert [row["id"] for row in businesses] == [str(ctx.a)]
    entry = await onboarding_service.entry(actor)
    assert entry["kind"] == "dashboard" and not entry["create_allowed"]

    payload = creation().model_copy(update={"name": "Technical admin creation " + str(uuid4())})
    with pytest.raises(HTTPException) as denied:
        await onboarding_service.create_business(actor, payload)
    assert denied.value.status_code == 403
    assert await guest_memberships(ctx) == []
    async with ctx.migration.connect() as db:
        assert (await db.execute(text("SELECT count(*) FROM business_onboarding_creations WHERE account_id=:a"),
                                 {"a": actor.account_id})).scalar_one() == 0
        assert (await db.execute(text("SELECT count(*) FROM tenants WHERE name=:name"),
                                 {"name": payload.name})).scalar_one() == 0
