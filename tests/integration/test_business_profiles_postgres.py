"""Real PostgreSQL: additive profile history, authorization, RLS and concurrency."""
import asyncio
import os
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI, HTTPException
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.business_profile_api import router
from app.business_profile_schemas import SaveDraft, SelectVersion
from app.business_profile_service import read_profile, published_profile, save_draft, select_version, profile_version
from app.catalog_api import authenticate, support_session_factory
from app.database import get_engine, tenant_transaction
from app.tenant_access import TenantAccess, resolve_tenant_access

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_verified_member_catalog_access_tracks_role_scope_and_revocation(profiles, monkeypatch):
    from app import catalog_api, panel_auth
    a, b, account, factory, migration = profiles
    monkeypatch.setenv("BUSINESS_PROFILES_ENABLED", "1")
    monkeypatch.setenv("SUPPORT_API_TOKEN", "synthetic-member-scope-support-" + "x" * 40)
    monkeypatch.setattr(catalog_api, "get_session_factory", lambda: factory)
    async def verified_session(_):
        return {"role": "member", "account_id": str(account)}
    monkeypatch.setattr(panel_auth, "panel_actor", verified_session)
    api = FastAPI()
    api.include_router(catalog_api.router)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="https://test",
                                     cookies={"__Host-orion_session": "synthetic-verified-session"}) as client:
            for role in ("owner", "editor", "viewer"):
                async with migration.begin() as db:
                    await db.execute(text("""INSERT INTO tenant_memberships(tenant_id,account_id,role,active)
                        VALUES (:tenant,:account,:role,true) ON CONFLICT (tenant_id,account_id)
                        DO UPDATE SET role=EXCLUDED.role,active=true"""),
                        {"tenant": a, "account": account, "role": role})
                assert (await client.get(f"/api/v1/tenants/{a}/services")).status_code == 200
                assert (await client.get(f"/api/v1/tenants/{b}/services")).status_code == 403
                assert (await client.get("/api/v1/tenants")).status_code == 403
                response = await client.post(f"/api/v1/tenants/{a}/services", json={
                    "name": "Synthetic membership service", "duration_min": 30, "price_cents": 2500})
                assert response.status_code == (403 if role == "viewer" else 201), response.text
            async with migration.begin() as db:
                await db.execute(text("UPDATE tenant_memberships SET active=false WHERE tenant_id=:tenant AND account_id=:account"),
                                 {"tenant": a, "account": account})
            assert (await client.get(f"/api/v1/tenants/{a}/services")).status_code == 403
    finally:
        async with migration.begin() as db:
            await db.execute(text("DELETE FROM services WHERE tenant_id=:tenant"), {"tenant": a})


@pytest_asyncio.fixture
async def profiles():
    runtime_url, migration_url = os.getenv("APP_DATABASE_URL"), os.getenv("MIGRATION_DATABASE_URL")
    if not runtime_url or not migration_url or not os.getenv("SUPPORT_DATABASE_URL"):
        if os.getenv("REQUIRE_POSTGRES_TESTS") == "1":
            pytest.fail("PostgreSQL URLs required")
        pytest.skip("Real PostgreSQL with migration 0010 required")
    mig = create_async_engine(migration_url.replace("postgresql+psycopg://", "postgresql+asyncpg://", 1), poolclass=NullPool)
    runtime = create_async_engine(runtime_url, poolclass=NullPool)
    factory = async_sessionmaker(runtime, expire_on_commit=False)
    a, b, account = uuid4(), uuid4(), uuid4()
    get_engine.cache_clear()
    support_session_factory.cache_clear()
    try:
        async with mig.begin() as db:
            for tenant in (a, b):
                await db.execute(text("INSERT INTO tenants (id,name,slug) VALUES (:id,'Synthetic business',:slug)"), {"id": tenant, "slug": f"profile-{tenant}"})
            await db.execute(text("INSERT INTO panel_accounts (id,name,email,password_hash) VALUES (:id,'Synthetic account',:email,'unused-test-hash')"), {"id": account, "email": f"profile-{account}@example.test"})
        yield a, b, account, factory, mig
    finally:
        async with mig.begin() as db:
            for table in ("business_profile_events", "business_profile_state", "business_profile_versions", "tenant_membership_events", "tenant_memberships", "tenant_audit", "platform_audit"):
                await db.execute(text(f"DELETE FROM {table} WHERE tenant_id IN (:a,:b)"), {"a": a, "b": b})
            await db.execute(text("DELETE FROM panel_accounts WHERE id=:account"), {"account": account})
            await db.execute(text("DELETE FROM tenants WHERE id IN (:a,:b)"), {"a": a, "b": b})
        await runtime.dispose()
        await mig.dispose()
        get_engine.cache_clear()
        support_session_factory.cache_clear()


async def draft(factory, tenant, generation, name):
    async with factory() as db:
        return await save_draft(db, tenant, SaveDraft(expected_generation=generation, profile={"business_name": name}), TenantAccess("support"))


async def publish(factory, tenant, version, generation, rollback=False):
    async with factory() as db:
        return await select_version(db, tenant, SelectVersion(expected_generation=generation, version_id=version), TenantAccess("support"), rollback=rollback)


@pytest.mark.asyncio
async def test_draft_publish_rollback_and_stale_edit_are_separate(profiles):
    a, _, _, factory, mig = profiles
    first = await draft(factory, a, 0, "First name")
    async with factory() as db:
        assert await published_profile(db, a) is None
    await publish(factory, a, first["version_id"], 1)
    second = await draft(factory, a, 2, "Second name")
    async with factory() as db:
        assert (await published_profile(db, a)).business_name == "First name"
    with pytest.raises(HTTPException) as stale:
        await draft(factory, a, 2, "Stale name")
    assert stale.value.status_code == 409
    with pytest.raises(HTTPException):
        await publish(factory, a, second["version_id"], 3, rollback=True)
    await publish(factory, a, second["version_id"], 3)
    pending = await draft(factory, a, 4, "Unpublished pending name")
    await publish(factory, a, first["version_id"], 5, rollback=True)
    async with factory() as db:
        result = await read_profile(db, a)
        assert result["published"]["profile"]["business_name"] == "First name"
        assert result["draft"]["id"] == pending["version_id"]
        assert result["generation"] == 6
    async with mig.connect() as db:
        assert (await db.execute(text("SELECT count(*) FROM business_profile_versions WHERE tenant_id=:t"), {"t": a})).scalar_one() == 3
        assert (await db.execute(text("SELECT count(*) FROM business_profile_events WHERE tenant_id=:t"), {"t": a})).scalar_one() == 6


@pytest.mark.asyncio
async def test_profile_rls_and_cross_tenant_references(profiles):
    a, b, _, factory, _ = profiles
    first = await draft(factory, a, 0, "Tenant A private facts")
    async with factory() as db:
        async with db.begin():
            assert (await db.execute(text("SELECT id FROM business_profile_versions"))).all() == []
    async with factory() as db:
        async with tenant_transaction(db, b):
            assert (await db.execute(text("SELECT id FROM business_profile_versions"))).all() == []
    async with factory() as db:
        with pytest.raises(HTTPException) as foreign_version:
            await profile_version(db, b, first["version_id"])
        assert foreign_version.value.status_code == 404
    with pytest.raises(HTTPException) as missing:
        await publish(factory, b, first["version_id"], 0)
    assert missing.value.status_code == 404
    async with factory() as db:
        with pytest.raises(DBAPIError):
            async with tenant_transaction(db, b):
                await db.execute(text("INSERT INTO business_profile_state (tenant_id,published_version_id) VALUES (:b,:v)"), {"b": b, "v": first["version_id"]})
    for statement in ("UPDATE business_profile_versions SET revision=999 WHERE tenant_id=:t",
                      "DELETE FROM business_profile_events WHERE tenant_id=:t",
                      "SELECT * FROM tenant_memberships"):
        async with factory() as db:
            with pytest.raises(DBAPIError):
                async with tenant_transaction(db, a):
                    await db.execute(text(statement), {"t": a})


@pytest.mark.asyncio
async def test_two_concurrent_editors_cannot_overwrite_each_other(profiles):
    a, _, _, factory, _ = profiles
    results = await asyncio.gather(draft(factory, a, 0, "One"), draft(factory, a, 0, "Two"), return_exceptions=True)
    assert sum(isinstance(item, dict) for item in results) == 1
    failure = next(item for item in results if isinstance(item, HTTPException))
    assert failure.status_code == 409


@pytest.mark.asyncio
async def test_explicit_membership_revocation_and_role_boundaries(profiles, monkeypatch):
    monkeypatch.setenv("BUSINESS_PROFILES_ENABLED", "1")
    a, b, account, _, mig = profiles
    actor = SimpleNamespace(role="member", account_id=account)
    with pytest.raises(HTTPException):
        await resolve_tenant_access(actor, a)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[authenticate] = lambda: SimpleNamespace(role="support", account_id=None)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        path = f"/api/v1/tenants/{a}/business/memberships/{account}"
        assert (await client.put(path, json={"role": "editor"})).status_code == 200
        assert (await resolve_tenant_access(actor, a, "draft")).role == "editor"
        with pytest.raises(HTTPException):
            await resolve_tenant_access(actor, a, "publish")
        with pytest.raises(HTTPException):
            await resolve_tenant_access(actor, b)
        assert (await client.put(path, json={"role": "owner"})).status_code == 200
        assert (await resolve_tenant_access(actor, a, "publish")).role == "owner"
        assert (await client.put(path, json={"role": "owner", "active": False})).status_code == 200
        with pytest.raises(HTTPException):
            await resolve_tenant_access(actor, a)
    async with mig.connect() as db:
        assert (await db.execute(text("SELECT count(*) FROM tenant_membership_events WHERE tenant_id=:t"), {"t": a})).scalar_one() == 3


@pytest.mark.asyncio
async def test_every_new_table_has_force_rls(profiles):
    _, _, _, _, mig = profiles
    async with mig.connect() as db:
        for table in ("tenant_memberships", "tenant_membership_events", "business_profile_versions", "business_profile_state", "business_profile_events"):
            row = (await db.execute(text("SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid=CAST(:name AS regclass)"), {"name": table})).one()
            assert row == (True, True)
