"""Actual Redis claims and PG account/membership revocation on every HTTP request."""
import os
from uuid import UUID

from fastapi import Depends, FastAPI
import httpx
import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app import account_auth, catalog_api, panel_auth
from tests.integration.test_panel_session_pool_redis import session_case, onboarding_db

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_joined_account_scope_is_one_live_query_and_never_crosses_requests(session_case, onboarding_db, monkeypatch):
    store, _client, put = session_case
    ctx = onboarding_db
    monkeypatch.setenv("SUPPORT_API_TOKEN", "synthetic-support-token-of-at-least-32-characters")
    monkeypatch.setattr(catalog_api, "get_session_factory", lambda: ctx.runtime)
    app = FastAPI()
    app.state.panel_session_redis = store
    app.add_api_route("/api/v1/tenants/{tenant_id}/services", catalog_api.list_services, methods=["GET"])
    @app.post("/api/v1/tenants/{tenant_id}/write-probe")
    async def probe(tenant_id: UUID, actor=Depends(catalog_api.authenticate)):
        catalog_api.authorize(actor, tenant_id, write=True)
        return {"ok": True}
    async with ctx.migration.connect() as db:
        version = (await db.execute(text("SELECT auth_version FROM panel_accounts WHERE id=:id"),
                                   {"id": ctx.actor.account_id})).scalar_one()
    headers, key = await put({"role": "member", "account_id": str(ctx.actor.account_id),
                             "auth_version": version, "csrf": "synthetic-csrf"})
    other_headers, _ = await put({"role": "member", "account_id": str(ctx.other),
                                  "auth_version": version, "csrf": "other-synthetic-csrf"})
    engine = catalog_api.support_session_factory().kw["bind"]
    # Exclude one-time dialect discovery; count every actual auth query below.
    async with engine.connect() as db:
        await db.execute(text("SELECT 1"))
    queries = []
    def record(_conn, _cursor, statement, _parameters, _context, _many):
        queries.append(statement)
    event.listen(engine.sync_engine, "before_cursor_execute", record)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test") as client:
            async def call(path, status, *, method="GET", request_headers=None):
                queries.clear()
                response = await client.request(method, path, headers=request_headers or headers)
                assert response.status_code == status, response.text
                assert len(queries) == 1 and "panel_accounts" in queries[0] and "LEFT JOIN tenant_memberships" in queries[0]
                return response
            path = f"/api/v1/tenants/{ctx.a}/services"
            assert (await call(path, 200)).json() == []
            await call(f"/api/v1/tenants/{ctx.b}/services", 403)
            await call(path, 403, request_headers=other_headers)
            await call(path, 200)
            # A historical second membership must not be selectable by changing
            # the route UUID; the same single live query rejects both routes.
            async with ctx.migration.begin() as db:
                await db.execute(text("INSERT INTO tenant_memberships(tenant_id,account_id,role) VALUES(:t,:a,'editor')"),
                                 {"t": ctx.b, "a": ctx.actor.account_id})
            await call(path, 409)
            await call(f"/api/v1/tenants/{ctx.b}/services", 409)
            async with ctx.migration.begin() as db:
                await db.execute(text("UPDATE tenant_memberships SET active=false WHERE tenant_id=:t AND account_id=:a"),
                                 {"t": ctx.b, "a": ctx.actor.account_id})
            await call(path, 200)
            async with ctx.migration.begin() as db:
                await db.execute(text("UPDATE tenant_memberships SET active=false WHERE tenant_id=:t AND account_id=:a"),
                                 {"t": ctx.a, "a": ctx.actor.account_id})
            await call(path, 403)
            async with ctx.migration.begin() as db:
                await db.execute(text("UPDATE tenant_memberships SET active=true,role='viewer' WHERE tenant_id=:t AND account_id=:a"),
                                 {"t": ctx.a, "a": ctx.actor.account_id})
            await call(path, 200)
            await call(f"/api/v1/tenants/{ctx.a}/write-probe", 403, method="POST",
                       request_headers={**headers, "x-orion-csrf": "synthetic-csrf"})
            async with ctx.migration.begin() as db:
                await db.execute(text("UPDATE panel_accounts SET auth_version=auth_version+1 WHERE id=:a"), {"a": ctx.actor.account_id})
            assert await store.exists(key)
            await call(path, 401)
            async with ctx.migration.begin() as db:
                await db.execute(text("UPDATE panel_accounts SET auth_version=:v,role='admin' WHERE id=:a"),
                                 {"v": version, "a": ctx.actor.account_id})
            await call(path, 401)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", record)


@pytest.mark.asyncio
async def test_single_read_returns_same_pooled_backend_with_transaction_rollback_intact(onboarding_db, monkeypatch):
    ctx = onboarding_db
    url = os.environ["SUPPORT_DATABASE_URL"].replace("postgresql://", "postgresql+asyncpg://", 1)
    engine = create_async_engine(url, pool_size=1, max_overflow=0, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(account_auth, "_storage", lambda: factory)
    async with ctx.migration.connect() as db:
        original = (await db.execute(text("SELECT name,auth_version FROM panel_accounts WHERE id=:a"),
                                    {"a": ctx.actor.account_id})).mappings().one()
    query = account_auth._CURRENT_ACCOUNT_SCOPE_SQL
    try:
        async with engine.connect() as db:
            pid = (await db.execute(text("SELECT pg_backend_pid()"))).scalar_one()
        for failed in (False, True):
            if failed:
                monkeypatch.setattr(account_auth, "_CURRENT_ACCOUNT_SCOPE_SQL",
                    text("SELECT unavailable_scope_column FROM panel_accounts"))
                with pytest.raises(DBAPIError):
                    await account_auth.current_account_and_membership(str(ctx.actor.account_id), original["auth_version"], ctx.a)
                monkeypatch.setattr(account_auth, "_CURRENT_ACCOUNT_SCOPE_SQL", query)
            else:
                row = await account_auth.current_account_and_membership(str(ctx.actor.account_id), original["auth_version"], ctx.a)
                assert row["name"] == original["name"] and row["membership_role"] == "owner"
            # If read-only autocommit leaked into the shared pool, this UPDATE
            # would survive the exception despite the ordinary session rollback.
            with pytest.raises(RuntimeError, match="synthetic rollback"):
                async with factory() as db:
                    async with db.begin():
                        assert (await db.execute(text("SELECT pg_backend_pid()"))).scalar_one() == pid
                        await db.execute(text("UPDATE panel_accounts SET name='Must roll back' WHERE id=:a"),
                                         {"a": ctx.actor.account_id})
                        raise RuntimeError("synthetic rollback")
            async with engine.connect() as db:
                assert (await db.execute(text("SELECT pg_backend_pid()"))).scalar_one() == pid
                name = (await db.execute(text("SELECT name FROM panel_accounts WHERE id=:a"),
                                        {"a": ctx.actor.account_id})).scalar_one()
                assert name == original["name"]
    finally:
        await engine.dispose()
