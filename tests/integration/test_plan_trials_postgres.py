"""Ephemeral PostgreSQL RLS/tenant-scoping tests for 3-day trials. No real users."""
import os
from datetime import timedelta
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.catalog_api import Actor, authenticate, support_session_factory
from app.database import get_engine
from app import plan_management
from app.admin_accounts import require_platform_owner

pytestmark=pytest.mark.integration


@pytest_asyncio.fixture
async def trial_db(monkeypatch):
    source=os.getenv("MIGRATION_DATABASE_URL")
    if not source or not os.getenv("APP_DATABASE_URL") or not os.getenv("SUPPORT_DATABASE_URL"):
        if os.getenv("REQUIRE_POSTGRES_TESTS")=="1":
            pytest.fail("PostgreSQL CI environment is mandatory")
        pytest.skip("PostgreSQL CI environment unavailable")
    migration=create_async_engine(
        source.replace("postgresql+psycopg://","postgresql+asyncpg://",1),
        poolclass=NullPool)
    account,tenant,other=(uuid4(),uuid4(),uuid4())
    monkeypatch.setenv("BUSINESS_PROFILES_ENABLED","1")
    get_engine.cache_clear()
    support_session_factory.cache_clear()
    try:
        async with migration.begin() as con:
            for t in (tenant,other):
                await con.execute(text("INSERT INTO tenants(id,name,slug) VALUES (:t,'Empresa Sintética',:slug)"),
                                  {"t":t,"slug":"plan-ci-"+str(t)})
            await con.execute(text("""
                INSERT INTO panel_accounts(id,name,email,password_hash)
                VALUES (:account,'Pessoa Sintética',:email,'test-unused-hash')
            """),{"account":account,"email":str(account)+"@example.test"})
            await con.execute(text("""
                INSERT INTO tenant_memberships(tenant_id,account_id,role,active)
                VALUES (:tenant,:account,'owner',true)
            """),{"tenant":tenant,"account":account})
        yield migration,account,tenant,other
    finally:
        async with migration.begin() as con:
            await con.execute(text("DELETE FROM orion_cakto_checkout_intents WHERE tenant_id IN (:a,:b)"),{"a":tenant,"b":other})
            await con.execute(text("DELETE FROM orion_plan_trial_requests WHERE tenant_id IN (:a,:b)"),{"a":tenant,"b":other})
            await con.execute(text("DELETE FROM tenant_memberships WHERE account_id=:account"),{"account":account})
            await con.execute(text("DELETE FROM panel_accounts WHERE id=:account"),{"account":account})
            await con.execute(text("DELETE FROM tenants WHERE id IN (:a,:b)"),{"a":tenant,"b":other})
        await migration.dispose()
        support_session_factory.cache_clear()
        get_engine.cache_clear()


@pytest.mark.asyncio
async def test_real_postgres_pending_duplicate_owner_only_admin_start_3_days(trial_db, monkeypatch):
    db,account,tenant,other=trial_db
    app=FastAPI()
    app.include_router(plan_management.router)
    app.dependency_overrides[authenticate]=lambda:Actor("member",account_id=account)
    app.dependency_overrides[require_platform_owner]=lambda:account
    monkeypatch.delenv("CAKTO_BILLING_LIVE_ENABLED",raising=False)
    req={"full_name":"Pessoa Sintética", "company_name":"Empresa Sintética",
         "whatsapp":"(67) 99999-1111","privacy_ack":True}
    base="/api/v1/tenants/"+str(tenant)+"/management"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="https://test") as client:
        initial=await client.get(base+"/plan-overview")
        assert initial.status_code==200,initial.text
        catalog=initial.json()
        assert catalog["subscription"] is None and catalog["trial"] is None
        assert [(p["monthly_cents"],p["max_professionals"]) for p in catalog["plans"]]==[
            (2990,2),(8990,6),(13990,10)]
        assert not catalog["checkout_enabled"] and catalog["can_manage_plan"]
        assert (await client.post(base+"/plan-trial",
            json={**req,"privacy_ack":False})).status_code==422
        assert (await client.post("/api/v1/tenants/"+str(other)+"/management/plan-trial",
            json=req)).status_code==403
        first=await client.post(base+"/plan-trial",json=req)
        assert first.status_code==201,first.text
        assert first.json()["status"]=="pending"
        assert (await client.post(base+"/plan-trial",json=req)).status_code==409
        overview=(await client.get(base+"/plan-overview")).json()
        assert overview["trial"]["status"]=="pending"
        assert overview["trial"]["started_at"] is None
        assert overview["trial"]["ends_at"] is None
        listing=(await client.get("/api/v1/admin/plan-trials")).json()["items"]
        mine=next(row for row in listing if str(row["tenant_id"])==str(tenant))
        assert mine["whatsapp_e164"]=="+5567999991111"
        decision=await client.post("/api/v1/admin/plan-trials/"+str(mine["id"]),
            json={"expected_version":mine["version"],"action":"activate"})
        assert decision.status_code==200,decision.text
        started=decision.json()
        assert started["status"]=="active"
        from datetime import datetime
        start_dt=datetime.fromisoformat(started["started_at"])
        end_dt=datetime.fromisoformat(started["ends_at"])
        assert timedelta(hours=71,minutes=59)<end_dt-start_dt<timedelta(hours=72,minutes=1)
        updated=(await client.get(base+"/plan-overview")).json()
        assert updated["trial"]["status"]=="active"
        assert updated["subscription"] is None
        assert (await client.post("/api/v1/admin/plan-trials/"+str(mine["id"]),
            json={"expected_version":mine["version"],"action":"activate"})).status_code==409
        assert (await client.post(base+"/plan-checkout",
            json={"plan_code":"profissional"})).status_code==409
    async with db.connect() as con:
        role=(await con.execute(text("""
            SELECT has_table_privilege('app_runtime',oid,'SELECT'),
                   has_table_privilege('app_support',oid,'SELECT'),
                   relrowsecurity,relforcerowsecurity FROM pg_class
            WHERE oid='public.orion_plan_trial_requests'::regclass
        """))).one()
        assert tuple(role)==(False,True,True,True)


@pytest.mark.asyncio
async def test_editor_cannot_request_trial_even_using_owner_payload(trial_db):
    db,account,tenant,_=trial_db
    async with db.begin() as con:
        await con.execute(text("""
            UPDATE tenant_memberships SET role='editor'
            WHERE tenant_id=:tenant AND account_id=:account
        """),{"tenant":tenant,"account":account})
    app=FastAPI()
    app.include_router(plan_management.router)
    app.dependency_overrides[authenticate]=lambda:Actor("member",account_id=account)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="https://test") as client:
        result=await client.post("/api/v1/tenants/"+str(tenant)+"/management/plan-trial",
          json={"full_name":"Pessoa de Teste","company_name":"Empresa Sintética",
                "whatsapp":"(67) 99999-2222","privacy_ack":True})
        assert result.status_code==403
