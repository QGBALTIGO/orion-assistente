"""Real role/RLS/transaction coverage; never substitutes SQLite for PostgreSQL."""
import asyncio
import os
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool

from app import onboarding_service as service, onboarding_schemas as schemas
from app.catalog_api import Actor, support_session_factory
from app.database import tenant_transaction

pytestmark = pytest.mark.integration


def creation(key=None, **changes):
    return schemas.CreateBusiness(name="Empresa sintética onboarding", timezone="America/Cuiaba", niche="outro",
        other_niche="Serviços sintéticos", activity="services", contact_phone="+5567999999901",
        idempotency_key=key or str(uuid4()), **changes)


async def save(ctx, state, step, data, *, key=None, version=None):
    return await service.save_step(ctx.actor, UUID(state["tenant_id"]), step, schemas.SaveStep(
        expected_version=state["version"] if version is None else version,
        idempotency_key=key or str(uuid4()), data=data))


@pytest_asyncio.fixture
async def onboarding_db(monkeypatch):
    urls = [os.getenv(k) for k in ("APP_DATABASE_URL", "MIGRATION_DATABASE_URL", "SUPPORT_DATABASE_URL")]
    if not all(urls):
        if os.getenv("REQUIRE_POSTGRES_TESTS") == "1":
            pytest.fail("Real PostgreSQL runtime/support/migrator URLs required")
        pytest.skip("Real PostgreSQL with onboarding migration 0020 required")
    runtime = create_async_engine(urls[0].replace("postgresql://", "postgresql+asyncpg://", 1), poolclass=NullPool)
    migration = create_async_engine(urls[1].replace("postgresql+psycopg://", "postgresql+asyncpg://", 1).replace("postgresql://", "postgresql+asyncpg://", 1), poolclass=NullPool)
    factory = async_sessionmaker(runtime, expire_on_commit=False)
    owner, other, new_account, a, b = (uuid4() for _ in range(5))
    monkeypatch.setenv("ONBOARDING_ENABLED", "1")
    monkeypatch.setenv("BUSINESS_PROFILES_ENABLED", "1")
    monkeypatch.setenv("COMMERCIAL_CATALOG_ENABLED", "1")
    monkeypatch.setattr(service, "get_session_factory", lambda: factory)
    support_session_factory.cache_clear()
    async with migration.begin() as db:
        for account in (owner, other, new_account):
            await db.execute(text("INSERT INTO panel_accounts(id,name,email,password_hash) VALUES(:id,'Pessoa sintética',:email,'unused-synthetic')"), {"id": account, "email": f"onboarding-{account}@example.test"})
        for tenant in (a, b):
            await db.execute(text("INSERT INTO tenants(id,name,slug) VALUES(:t,'Legado sintético',:s)"), {"t": tenant, "s": "onboarding-" + tenant.hex})
        await db.execute(text("INSERT INTO tenant_memberships(tenant_id,account_id,role) VALUES(:t,:a,'owner')"), {"t": a, "a": owner})
    ctx = SimpleNamespace(actor=Actor("member", account_id=owner), new_actor=Actor("member", account_id=new_account),
        other=other, a=a, b=b, runtime=factory, migration=migration)
    try:
        yield ctx
    finally:
        async with migration.begin() as db:
            new = (await db.execute(text("SELECT tenant_id FROM business_onboarding_creations WHERE account_id IN (:a,:b,:c)"), {"a": owner, "b": other, "c": new_account})).scalars().all()
            for tenant in [a, b, *new]:
                for table in ("business_onboarding_operations", "business_onboarding_creations", "business_onboarding",
                    "business_profile_events", "business_profile_state", "business_profile_versions", "tenant_membership_events",
                    "management_team_invitations", "tenant_memberships", "commercial_events", "commercial_inventory",
                    "commercial_variants", "commercial_products", "working_breaks", "working_hours", "professional_services",
                    "services", "professionals", "tenant_audit", "platform_audit"):
                    await db.execute(text(f"DELETE FROM {table} WHERE tenant_id=:t"), {"t": tenant})
                await db.execute(text("DELETE FROM tenants WHERE id=:t"), {"t": tenant})
            await db.execute(text("DELETE FROM panel_accounts WHERE id IN (:a,:b,:c)"), {"a": owner, "b": other, "c": new_account})
        await runtime.dispose()
        await migration.dispose()
        support_session_factory.cache_clear()


def new_business_context(ctx):
    """Keep the seeded legacy owner separate from a first-time business creator."""
    return SimpleNamespace(**{**vars(ctx), "actor": ctx.new_actor})


async def configured(ctx, *, activity="services", state=None):
    payload = creation() if activity == "services" else schemas.CreateBusiness(**{**creation().model_dump(), "activity": activity})
    if state is None:
        state = await service.create_business(ctx.actor, payload)
    state = await save(ctx, state, "location", {"modality": "online"})
    if activity in {"services", "both"}:
        state = await save(ctx, state, "professionals", {"item": {"name": "Pessoa sintética"}, "self_provider": True})
        pro = state["result"]["id"]
        state = await save(ctx, state, "catalog", {"kind": "service", "item": {"name": "Serviço sintético", "duration_min": 40, "price_cents": 3500, "prep_min": 5, "cleanup_min": 10}, "professional_ids": [pro]})
        state = await save(ctx, state, "hours", {"professional_id": pro, "weekdays": [0, 2], "start_time": "09:00", "end_time": "18:00", "break_start": "12:00", "break_end": "13:00"})
    if activity in {"products", "both"}:
        state = await save(ctx, state, "catalog", {"kind": "product", "item": {"name": "Produto sintético"}, "variant": {"name": "Padrão", "price_kind": "on_request"}})
    return state


@pytest.mark.asyncio
async def test_new_business_requires_whatsapp_and_adopts_timezone_from_private_address(onboarding_db):
    ctx = new_business_context(onboarding_db)
    missing = creation().model_copy(update={"contact_phone": None})
    with pytest.raises(HTTPException) as err:
        await service.create_business(ctx.actor, missing)
    assert err.value.status_code == 422
    first = await service.create_business(ctx.actor, creation())
    assert first["timezone"] == "America/Cuiaba"
    old_no_number = {"modality": "onsite", "address": {
        "street": "Rua sintética", "number": None, "no_number": True,
        "neighborhood": "Centro", "city": "Campo Grande", "state": "MS"}}
    with pytest.raises(HTTPException) as blocked:
        await save(ctx, first, "location", old_no_number)
    assert blocked.value.status_code == 422
    with pytest.raises(HTTPException) as blocked_text:
        await save(ctx, first, "location", {**old_no_number, "address": {
            **old_no_number["address"], "number": "S/N", "no_number": False}})
    assert blocked_text.value.status_code == 422
    unchanged = await service.get_state(ctx.actor, UUID(first["tenant_id"]))
    assert unchanged["version"] == first["version"]
    state = await save(ctx, first, "location", {
        "modality": "onsite", "address": {
            "postal_code": "79002-000", "street": "Rua sintética", "number": "123A",
            "no_number": False, "neighborhood": "Centro", "city": "Campo Grande", "state": "MS",
        }
    })
    assert state["timezone"] == "America/Campo_Grande"
    async with ctx.migration.connect() as db:
        saved_tz = (await db.execute(text("SELECT timezone FROM tenants WHERE id=:t"),
                                     {"t": UUID(first["tenant_id"])})).scalar_one()
    assert saved_tz == "America/Campo_Grande"


@pytest.mark.asyncio
async def test_create_unverified_personal_account_is_atomic_paused_and_idempotent(onboarding_db):
    ctx = new_business_context(onboarding_db)
    payload = creation()
    first, second = await asyncio.gather(service.create_business(ctx.actor, payload), service.create_business(ctx.actor, payload))
    assert first["tenant_id"] == second["tenant_id"] and first["version"] == second["version"] == 1
    assert first["required"] and first["status"] == "incomplete"
    assert first["last_operation_key"] == payload.idempotency_key
    async with ctx.migration.connect() as db:
        tenant = (await db.execute(text("SELECT status,bot_enabled FROM tenants WHERE id=:t"), {"t": UUID(first["tenant_id"])})).mappings().one()
        assert tenant == {"status": "paused", "bot_enabled": False}
        assert (await db.execute(text("SELECT role FROM panel_accounts WHERE id=:a"), {"a": ctx.actor.account_id})).scalar_one() == "member"
        assert (await db.execute(text("SELECT count(*) FROM tenant_memberships WHERE tenant_id=:t AND role='owner'"), {"t": UUID(first["tenant_id"])})).scalar_one() == 1
    with pytest.raises(HTTPException) as changed:
        await service.create_business(ctx.actor, payload.model_copy(update={"name": "Outro pedido"}))
    assert changed.value.status_code == 409


@pytest.mark.asyncio
async def test_get_entry_no_creation_legacy_empty_optional_and_employee_skips(onboarding_db):
    ctx = onboarding_db
    before = await service.entry(ctx.actor)
    assert before["kind"] == "onboarding" and before["businesses"][0]["required"] is False
    state = await service.get_state(ctx.actor, ctx.a)
    assert state["status"] == "legacy" and state["version"] == 0
    async with ctx.migration.begin() as db:
        assert (await db.execute(text("SELECT count(*) FROM business_onboarding WHERE tenant_id=:t"), {"t": ctx.a})).scalar_one() == 0
        await db.execute(text("UPDATE tenant_memberships SET role='editor' WHERE tenant_id=:t"), {"t": ctx.a})
    assert (await service.entry(ctx.actor))["kind"] == "dashboard"


@pytest.mark.asyncio
async def test_new_business_entry_resumes_the_only_business_and_disallows_another(onboarding_db):
    ctx = new_business_context(onboarding_db)
    before = await service.entry(ctx.actor)
    assert before["kind"] == "onboarding" and before["create_allowed"]
    state = await service.create_business(ctx.actor, creation())
    selected = await service.entry(ctx.actor)
    assert selected["kind"] == "onboarding" and selected["current_step"] == "location"
    assert selected["tenant_id"] == state["tenant_id"] and not selected["create_allowed"]
    with pytest.raises(HTTPException) as foreign:
        await service.entry(ctx.actor, ctx.b)
    assert foreign.value.status_code == 403
    with pytest.raises(HTTPException) as duplicate:
        await service.create_business(ctx.actor, creation())
    assert duplicate.value.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize("role,active", [("owner", True), ("editor", True), ("viewer", True), ("owner", False)])
async def test_existing_business_blocks_creation_without_changing_memberships(onboarding_db, role, active):
    ctx = onboarding_db
    async with ctx.migration.begin() as db:
        await db.execute(text("UPDATE tenant_memberships SET role=:r,active=:active WHERE tenant_id=:t AND account_id=:a"),
                         {"r": role, "active": active, "t": ctx.a, "a": ctx.actor.account_id})
    with pytest.raises(HTTPException) as denied:
        await service.create_business(ctx.actor, creation())
    assert denied.value.status_code == 409
    assert not (await service.entry(ctx.actor))["create_allowed"]
    async with ctx.migration.connect() as db:
        membership = (await db.execute(text("SELECT tenant_id,role,active FROM tenant_memberships WHERE account_id=:a"),
                         {"a": ctx.actor.account_id})).one()
        assert membership == (ctx.a, role, active)
        assert (await db.execute(text("SELECT count(*) FROM business_onboarding_creations WHERE account_id=:a"),
                         {"a": ctx.actor.account_id})).scalar_one() == 0


@pytest.mark.asyncio
async def test_different_creation_keys_race_to_one_company_without_orphan_records(onboarding_db):
    ctx = new_business_context(onboarding_db)
    results = await asyncio.gather(*(service.create_business(ctx.actor, creation()) for _ in range(2)), return_exceptions=True)
    successful = [r for r in results if isinstance(r, dict)]
    assert len(successful) == 1
    assert [r.status_code for r in results if isinstance(r, HTTPException)] == [409]
    tenant = UUID(successful[0]["tenant_id"])
    async with ctx.migration.connect() as db:
        assert (await db.execute(text("SELECT tenant_id FROM tenant_memberships WHERE account_id=:a AND active"),
                         {"a": ctx.actor.account_id})).scalars().all() == [tenant]
        assert (await db.execute(text("SELECT tenant_id FROM business_onboarding_creations WHERE account_id=:a"),
                         {"a": ctx.actor.account_id})).scalars().all() == [tenant]
        assert (await db.execute(text("SELECT tenant_id FROM business_onboarding WHERE created_by=:a"),
                         {"a": ctx.actor.account_id})).scalars().all() == [tenant]
        assert (await db.execute(text("SELECT count(*) FROM tenant_membership_events WHERE account_id=:a"),
                         {"a": ctx.actor.account_id})).scalar_one() == 1


@pytest.mark.asyncio
async def test_creation_history_prevents_a_second_company_after_access_is_revoked(onboarding_db):
    ctx = new_business_context(onboarding_db)
    payload = creation()
    state = await service.create_business(ctx.actor, payload)
    async with ctx.migration.begin() as db:
        await db.execute(text("UPDATE tenant_memberships SET role='editor',active=false WHERE account_id=:a"),
                         {"a": ctx.actor.account_id})
    with pytest.raises(HTTPException) as denied:
        await service.create_business(ctx.actor, creation())
    assert denied.value.status_code == 409
    assert not (await service.entry(ctx.actor))["create_allowed"]
    async with ctx.migration.connect() as db:
        assert (await db.execute(text("SELECT tenant_id FROM business_onboarding_creations WHERE account_id=:a"),
                         {"a": ctx.actor.account_id})).scalars().all() == [UUID(state["tenant_id"])]


@pytest.mark.asyncio
@pytest.mark.parametrize("requested", [None, "a", "b"])
async def test_legacy_multiple_memberships_require_review_even_with_a_requested_company(onboarding_db, requested):
    ctx = onboarding_db
    async with ctx.migration.begin() as db:
        await db.execute(text("INSERT INTO tenant_memberships(tenant_id,account_id,role) VALUES(:t,:a,'editor')"),
                         {"t": ctx.b, "a": ctx.actor.account_id})
    result = await service.entry(ctx.actor, getattr(ctx, requested) if requested else None)
    assert result["kind"] == "business_conflict" and not result["create_allowed"]
    assert result.get("tenant_id") is None
    with pytest.raises(HTTPException) as denied:
        await service.create_business(ctx.actor, creation())
    assert denied.value.status_code == 409
    async with ctx.migration.connect() as db:
        assert set((await db.execute(text("SELECT tenant_id FROM tenant_memberships WHERE account_id=:a AND active"),
                         {"a": ctx.actor.account_id})).scalars().all()) == {ctx.a, ctx.b}


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["editor", "viewer", "revoked"])
async def test_current_owner_membership_is_required_for_mutation(onboarding_db, role):
    ctx = onboarding_db
    async with ctx.migration.begin() as db:
        await db.execute(text("UPDATE tenant_memberships SET role=:role,active=:active WHERE tenant_id=:t"), {"t": ctx.a, "role": role if role != "revoked" else "owner", "active": role != "revoked"})
    with pytest.raises(HTTPException) as denied:
        await service.save_step(ctx.actor, ctx.a, "location", schemas.SaveStep(expected_version=0, idempotency_key=str(uuid4()), data={"modality": "online"}))
    assert denied.value.status_code == 403


@pytest.mark.asyncio
async def test_step_replay_stale_cas_changed_key_and_separate_tenant(onboarding_db):
    ctx = new_business_context(onboarding_db)
    initial = await service.create_business(ctx.actor, creation())
    key = str(uuid4())
    first = await save(ctx, initial, "location", {"modality": "online"}, key=key)
    replay = await save(ctx, initial, "location", {"modality": "online"}, key=key)
    assert first["version"] == replay["version"] and replay["last_operation_key"] == key
    for data, operation_key in [({"modality": "client_address"}, key), ({"modality": "online"}, str(uuid4()))]:
        with pytest.raises(HTTPException) as changed:
            await save(ctx, initial, "location", data, key=operation_key)
        assert changed.value.status_code == 409
    with pytest.raises(HTTPException):
        await service.save_step(ctx.actor, ctx.b, "location", schemas.SaveStep(expected_version=0, idempotency_key=key, data={"modality": "online"}))


@pytest.mark.asyncio
async def test_real_service_catalog_hours_staged_then_complete_no_channels_or_publication(onboarding_db):
    ctx = new_business_context(onboarding_db)
    state = await configured(ctx)
    tenant = UUID(state["tenant_id"])
    assert not state["pending"]
    assert all(not p["active"] and p["draft"] for p in state["catalog"]["professionals"])
    assert all(not s["active"] for s in state["catalog"]["services"])
    assert len(state["catalog"]["hours"]) == 2 and all(len(w["breaks"]) == 1 for w in state["catalog"]["hours"])
    request = schemas.Complete(expected_version=state["version"], expected_snapshot=state["snapshot"], idempotency_key=str(uuid4()), activate_catalog=True, acknowledged=True)
    done = await service.complete(ctx.actor, tenant, request)
    repeat = await service.complete(ctx.actor, tenant, request)
    assert done["status"] == "complete" and done["version"] == repeat["version"]
    assert done["catalog"]["services"][0]["active"] and done["catalog"]["professionals"][0]["active"]
    assert done["channels"]["assistant"]["status"] == "not_configured"
    async with ctx.migration.connect() as db:
        assert (await db.execute(text("SELECT status FROM tenants WHERE id=:t"), {"t": tenant})).scalar_one() == "active"
        for table in ("appointments", "authorized_staff", "whatsapp_channels", "google_connections", "business_profile_versions", "management_site_preferences"):
            assert (await db.execute(text(f"SELECT count(*) FROM {table} WHERE tenant_id=:t"), {"t": tenant})).scalar_one() == 0


@pytest.mark.asyncio
async def test_products_only_complete_has_no_calendar_or_fabricated_stock(onboarding_db):
    ctx = new_business_context(onboarding_db)
    state = await configured(ctx, activity="products")
    tenant = UUID(state["tenant_id"])
    done = await service.complete(ctx.actor, tenant, schemas.Complete(expected_version=state["version"], expected_snapshot=state["snapshot"], idempotency_key=str(uuid4()), activate_catalog=True, acknowledged=True))
    assert done["status"] == "complete" and not done["catalog"]["professionals"] and not done["catalog"]["hours"]
    variant = done["catalog"]["products"][0]["variants"][0]
    assert variant["price_kind"] == "on_request" and variant["unit_price_cents"] is None and variant["active"]
    async with ctx.migration.connect() as db:
        row = (await db.execute(text("SELECT mode,quantity_on_hand,reserved_quantity FROM commercial_inventory WHERE tenant_id=:t"), {"t": tenant})).mappings().one()
        assert row == {"mode": "unknown", "quantity_on_hand": None, "reserved_quantity": 0}


@pytest.mark.asyncio
async def test_incomplete_finish_rolls_back_and_runtime_guard_rechecks_changes(onboarding_db):
    ctx = new_business_context(onboarding_db)
    state = await service.create_business(ctx.actor, creation())
    tenant = UUID(state["tenant_id"])
    with pytest.raises(HTTPException):
        await service.complete(ctx.actor, tenant, schemas.Complete(expected_version=state["version"], expected_snapshot=state["snapshot"], idempotency_key=str(uuid4()), activate_catalog=True, acknowledged=True))
    async with ctx.runtime() as db:
        with pytest.raises(HTTPException):
            async with tenant_transaction(db, tenant):
                await service.require_operational_ready(db, tenant, "appointments")
    configured_state = await configured(ctx, state=state)
    tenant = UUID(configured_state["tenant_id"])
    done = await service.complete(ctx.actor, tenant, schemas.Complete(expected_version=configured_state["version"], expected_snapshot=configured_state["snapshot"], idempotency_key=str(uuid4()), activate_catalog=True, acknowledged=True))
    async with ctx.runtime() as db:
        async with tenant_transaction(db, tenant):
            await service.require_operational_ready(db, tenant, "appointments")
    async with ctx.migration.begin() as db:
        await db.execute(text("UPDATE professionals SET active=false WHERE tenant_id=:t"), {"t": tenant})
    async with ctx.runtime() as db:
        with pytest.raises(HTTPException):
            async with tenant_transaction(db, tenant):
                await service.require_operational_ready(db, tenant, "appointments")


@pytest.mark.asyncio
async def test_existing_day_and_catalog_edits_require_real_fingerprint_and_preserve_other_fields(onboarding_db):
    ctx = new_business_context(onboarding_db)
    state = await configured(ctx)
    original = state["catalog"]["professionals"][0]
    # A normal module change invalidates the wizard's old entity version.
    async with ctx.migration.begin() as db:
        await db.execute(text("UPDATE professionals SET specialty='Nova',color='#112233',commission_percent=15 WHERE id=:id"), {"id": UUID(original["id"])})
    with pytest.raises(HTTPException):
        await save(ctx, state, "professionals", {"item": {"id": original["id"], "name": "Editado", "expected_fingerprint": original["fingerprint"]}})
    state = await service.get_state(ctx.actor, UUID(state["tenant_id"]))
    current = state["catalog"]["professionals"][0]
    state = await save(ctx, state, "professionals", {"item": {"id": current["id"], "name": "Editado", "expected_fingerprint": current["fingerprint"]}})
    changed = state["catalog"]["professionals"][0]
    assert changed["specialty"] == "Nova" and changed["color"] == "#112233" and float(changed["commission_percent"]) == 15
    hour = state["catalog"]["hours"][0]
    update = {"professional_id": current["id"], "weekdays": [hour["weekday"]], "start_time": "10:00", "end_time": "17:00"}
    with pytest.raises(HTTPException):
        await save(ctx, state, "hours", update)
    state = await save(ctx, state, "hours", {**update, "replace_existing": True, "expected_fingerprints": {str(hour["weekday"]): hour["fingerprint"]}})
    other = next(w for w in state["catalog"]["hours"] if w["id"] != hour["id"])
    assert other["start_time"] == "09:00:00" and len(other["breaks"]) == 1


@pytest.mark.asyncio
async def test_profile_setup_is_draft_only_preserves_existing_facts_and_generation(onboarding_db):
    ctx = new_business_context(onboarding_db)
    state = await service.create_business(ctx.actor, creation())
    data = {"assistant_name": "Assistente sintética", "style_rules": {"style": "direct"}, "capabilities": ["catalog_answers"], "expected_profile_generation": 0}
    state = await save(ctx, state, "channels", data)
    assert state["channels"]["assistant"]["status"] == "draft" and state["channels"]["assistant"]["generation"] == 1
    with pytest.raises(HTTPException):
        await save(ctx, state, "channels", data)
    async with ctx.migration.connect() as db:
        assert (await db.execute(text("SELECT published_version_id FROM business_profile_state WHERE tenant_id=:t"), {"t": UUID(state["tenant_id"])})).scalar_one() is None


@pytest.mark.asyncio
async def test_rls_and_narrow_tenant_functions_cannot_cross_or_activate_missing_facts(onboarding_db):
    ctx = new_business_context(onboarding_db)
    state = await service.create_business(ctx.actor, creation())
    tenant = UUID(state["tenant_id"])
    async with ctx.runtime() as db:
        async with db.begin():
            assert (await db.execute(text("SELECT tenant_id FROM business_onboarding"))).all() == []
    for operation, scope in [("SELECT public.onboarding_update_identity(:t,'Troca','UTC')", ctx.b),
                             ("SELECT public.onboarding_activate_tenant(:t)", tenant)]:
        async with ctx.runtime() as db:
            with pytest.raises(DBAPIError):
                async with tenant_transaction(db, tenant):
                    await db.execute(text(operation), {"t": scope})
    async with ctx.runtime() as db:
        with pytest.raises(DBAPIError):
            async with tenant_transaction(db, tenant):
                await db.execute(text("UPDATE tenants SET status='active' WHERE id=:t"), {"t": tenant})
    async with ctx.migration.connect() as db:
        functions = (await db.execute(text("""SELECT p.prosecdef,p.proconfig,
            has_function_privilege('app_runtime',p.oid,'EXECUTE') AS runtime,
            EXISTS(SELECT 1 FROM aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a
              WHERE a.grantee=0 AND a.privilege_type='EXECUTE') AS public FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname='public' AND p.proname IN ('onboarding_activate_tenant','onboarding_update_identity')"""))).mappings().all()
        assert len(functions) == 2 and all(f["prosecdef"] and f["runtime"] and not f["public"] for f in functions)
        assert all(any("search_path=pg_catalog, public" == c for c in f["proconfig"]) for f in functions)


@pytest.mark.asyncio
async def test_two_tabs_same_version_only_one_step_commits(onboarding_db):
    ctx = new_business_context(onboarding_db)
    state = await service.create_business(ctx.actor, creation())
    results = await asyncio.gather(save(ctx, state, "location", {"modality": "online"}),
                                   save(ctx, state, "location", {"modality": "client_address"}), return_exceptions=True)
    assert sum(isinstance(r, dict) for r in results) == 1
    assert [r.status_code for r in results if isinstance(r, HTTPException)] == [409]
    current = await service.get_state(ctx.actor, UUID(state["tenant_id"]))
    assert current["version"] == state["version"] + 1


@pytest.mark.asyncio
async def test_foreign_pro_link_rolls_back_entire_new_service(onboarding_db):
    ctx = new_business_context(onboarding_db)
    state = await service.create_business(ctx.actor, creation())
    foreign = uuid4()
    async with ctx.migration.begin() as db:
        await db.execute(text("INSERT INTO professionals(id,tenant_id,name) VALUES(:id,:t,'Pessoa de outra empresa')"), {"id": foreign, "t": ctx.b})
    with pytest.raises(HTTPException) as denied:
        await save(ctx, state, "catalog", {"kind": "service", "item": {"name": "Não deve existir", "duration_min": 30, "price_cents": 1000}, "professional_ids": [str(foreign)]})
    assert denied.value.status_code == 422
    current = await service.get_state(ctx.actor, UUID(state["tenant_id"]))
    assert not current["catalog"]["services"] and current["version"] == state["version"]


@pytest.mark.asyncio
async def test_pending_invitation_uses_verified_identity_without_claiming_or_creating(onboarding_db):
    ctx = onboarding_db
    actor = Actor("member", account_id=ctx.other)
    async with ctx.migration.begin() as db:
        await db.execute(text("UPDATE panel_accounts SET verified_at=now() WHERE id=:a"), {"a": ctx.other})
        await db.execute(text("""INSERT INTO management_team_invitations
            (tenant_id,email,role,token_digest,idempotency_key,fingerprint,expires_at,created_by)
            VALUES(:t,:email,'editor',:digest,:key,:digest,now()+interval '1 day',:owner)"""),
            {"t": ctx.a, "email": f"onboarding-{ctx.other}@example.test", "digest": uuid4().hex*2, "key": str(uuid4()), "owner": ctx.actor.account_id})
    result = await service.entry(actor)
    assert result["kind"] == "invitation" and not result["create_allowed"]
    assert result["invitations"][0]["status"] == "pending"
    with pytest.raises(HTTPException) as blocked:
        await service.create_business(actor, creation())
    assert blocked.value.status_code == 409
    async with ctx.migration.connect() as db:
        assert (await db.execute(text("SELECT count(*) FROM tenant_memberships WHERE account_id=:a"), {"a": ctx.other})).scalar_one() == 0
        assert (await db.execute(text("SELECT count(*) FROM business_onboarding_creations WHERE account_id=:a"), {"a": ctx.other})).scalar_one() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("condition", ["absent", "incomplete", "fake_complete", "legacy"])
async def test_narrow_activation_never_accepts_metadata_flags_without_operational_facts(onboarding_db, condition):
    ctx = onboarding_db
    tenant = ctx.a
    if condition != "absent":
        async with ctx.migration.begin() as db:
            await db.execute(text("""INSERT INTO business_onboarding(tenant_id,is_new,configuration,completed_at)
                VALUES(:t,:new,CAST(:c AS jsonb),CASE WHEN :done THEN now() ELSE NULL END)"""),
                {"t": tenant, "new": condition != "legacy", "done": condition != "incomplete",
                 "c": '{"niche":"outro","other_niche":"Teste","activity":"services","modality":"online","reviewed":true,"activation_requested":true}'})
            await db.execute(text("UPDATE tenants SET status='paused' WHERE id=:t"), {"t": tenant})
    async with ctx.runtime() as db:
        with pytest.raises(DBAPIError):
            async with tenant_transaction(db, tenant):
                await db.execute(text("SELECT public.onboarding_activate_tenant(:t)"), {"t": tenant})


@pytest.mark.asyncio
async def test_staged_edits_preserve_requested_active_and_same_key_detects_omitted_default(onboarding_db):
    ctx = new_business_context(onboarding_db)
    state = await configured(ctx)
    pro = state["catalog"]["professionals"][0]
    key = str(uuid4())
    item = {"id": pro["id"], "expected_fingerprint": pro["fingerprint"], "name": "Novo nome"}
    state = await save(ctx, state, "professionals", {"item": item}, key=key)
    changed = state["catalog"]["professionals"][0]
    assert changed["requested_active"] is True and changed["active"] is False
    with pytest.raises(HTTPException) as conflict:
        await save(ctx, state, "professionals", {"item": {**item, "color": "#31ad52"}}, key=key)
    assert conflict.value.status_code == 409


@pytest.mark.asyncio
async def test_new_inactive_or_unbound_requested_items_cannot_be_silently_made_ready(onboarding_db):
    ctx = new_business_context(onboarding_db)
    state = await configured(ctx)
    state = await save(ctx, state, "catalog", {"kind": "service", "item": {"name": "Serviço ainda sem vínculo", "duration_min": 30, "price_cents": 2000}, "professional_ids": []})
    assert any(x["code"] == "scheduling_bindings" for x in state["pending"])
    with pytest.raises(HTTPException):
        await service.complete(ctx.actor, UUID(state["tenant_id"]), schemas.Complete(expected_version=state["version"], expected_snapshot=state["snapshot"], idempotency_key=str(uuid4()), activate_catalog=True, acknowledged=True))


@pytest.mark.asyncio
async def test_catalog_change_after_review_requires_new_factual_snapshot(onboarding_db):
    ctx = new_business_context(onboarding_db)
    state = await configured(ctx)
    tenant = UUID(state["tenant_id"])
    async with ctx.migration.begin() as db:
        await db.execute(text("UPDATE services SET price_cents=4500 WHERE tenant_id=:t"), {"t": tenant})
    with pytest.raises(HTTPException) as changed:
        await service.complete(ctx.actor, tenant, schemas.Complete(expected_version=state["version"], expected_snapshot=state["snapshot"], idempotency_key=str(uuid4()), activate_catalog=True, acknowledged=True))
    assert changed.value.status_code == 409
    current = await service.get_state(ctx.actor, tenant)
    assert current["version"] == state["version"] and current["snapshot"] != state["snapshot"]
    assert current["status"] == "incomplete" and not current["catalog"]["services"][0]["active"]
    done = await service.complete(ctx.actor, tenant, schemas.Complete(expected_version=current["version"], expected_snapshot=current["snapshot"], idempotency_key=str(uuid4()), activate_catalog=True, acknowledged=True))
    assert done["status"] == "complete" and done["catalog"]["services"][0]["price_cents"] == 4500


@pytest.mark.asyncio
async def test_current_owner_lock_serializes_revocation_with_single_committed_step(onboarding_db, monkeypatch):
    ctx = new_business_context(onboarding_db)
    state = await service.create_business(ctx.actor, creation())
    tenant = UUID(state["tenant_id"])
    entered, release = asyncio.Event(), asyncio.Event()
    original = service._apply_step
    async def paused(*args, **kwargs):
        result = await original(*args, **kwargs)
        entered.set()
        await release.wait()
        return result
    monkeypatch.setattr(service, "_apply_step", paused)
    task = asyncio.create_task(save(ctx, state, "location", {"modality": "online"}))
    await asyncio.wait_for(entered.wait(), 5)
    async def revoke():
        async with ctx.migration.begin() as db:
            await db.execute(text("UPDATE tenant_memberships SET active=false WHERE tenant_id=:t AND account_id=:a"), {"t": tenant, "a": ctx.actor.account_id})
    revocation = asyncio.create_task(revoke())
    try:
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(revocation), .1)
    finally:
        release.set()
    await task
    await revocation
    with pytest.raises(HTTPException) as denied:
        await service.get_state(ctx.actor, tenant)
    assert denied.value.status_code == 403


@pytest.mark.asyncio
async def test_actual_booking_engine_does_not_create_for_incomplete_new_company(onboarding_db):
    from datetime import datetime, timedelta, timezone
    from app.booking_service import book_appointment
    ctx = new_business_context(onboarding_db)
    state = await configured(ctx)
    tenant = UUID(state["tenant_id"])
    async with ctx.runtime() as db:
        with pytest.raises(HTTPException) as denied:
            await book_appointment(db, tenant, UUID(state["catalog"]["professionals"][0]["id"]),
                [UUID(state["catalog"]["services"][0]["id"])], "+5567999999901", "Cliente sintético",
                datetime.now(timezone.utc)+timedelta(days=7), "onboarding-synthetic-book")
    assert denied.value.status_code == 409
    async with ctx.migration.connect() as db:
        for table in ("appointments", "customers", "appointment_history", "calendar_outbox"):
            assert (await db.execute(text(f"SELECT count(*) FROM {table} WHERE tenant_id=:t"), {"t": tenant})).scalar_one() == 0
