"""Real isolated PostgreSQL: manual control, replay, commercial coexistence and RLS."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import os
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app import admin_accounts, plan_management, management_api
from app.catalog_api import Actor, authenticate, support_session_factory
from app.database import get_engine, get_session_factory, tenant_transaction
from app.plan_permissions import derive_plan
from app.cakto_subscription_lifecycle import reconcile_existing_cakto_subscription

pytestmark=pytest.mark.integration


@pytest_asyncio.fixture
async def manual_db(monkeypatch):
    source=os.getenv("MIGRATION_DATABASE_URL")
    if not source or not os.getenv("APP_DATABASE_URL") or not os.getenv("SUPPORT_DATABASE_URL"):
        if os.getenv("REQUIRE_POSTGRES_TESTS")=="1": pytest.fail("Mandatory isolated PostgreSQL URLs missing")
        pytest.skip("Requires isolated PostgreSQL roles and migration0050")
    if make_url(source).host not in {"127.0.0.1","localhost","::1"}:
        pytest.fail("Manual plan tests require loopback PostgreSQL")
    engine=create_async_engine(source.replace("postgresql+psycopg://","postgresql+asyncpg://",1),poolclass=NullPool)
    tenant,other,account,second,operator=(uuid4() for _ in range(5))
    monkeypatch.setenv("BUSINESS_PROFILES_ENABLED","1")
    support_session_factory.cache_clear();get_engine.cache_clear()
    try:
        async with engine.begin() as db:
            for identity in (tenant,other):
                await db.execute(text("INSERT INTO tenants(id,name,slug) VALUES(:id,'Empresa de plano sintética',:slug)"),
                    {"id":identity,"slug":"manual-plan-"+str(identity)})
            for identity,role in ((account,"member"),(second,"member"),(operator,"admin")):
                await db.execute(text("""INSERT INTO panel_accounts(id,name,email,password_hash,role,verified_at)
                    VALUES(:id,'Conta sintética',:email,'unused-test-hash',:role,now())"""),
                    {"id":identity,"email":str(identity)+"@example.test","role":role})
            for company,owner in ((tenant,account),(other,second)):
                await db.execute(text("INSERT INTO tenant_memberships(tenant_id,account_id,role,active) VALUES(:tenant,:owner,'owner',true)"),
                    {"tenant":company,"owner":owner})
        api=FastAPI();api.include_router(admin_accounts.router);api.include_router(plan_management.router);api.include_router(management_api.router)
        api.dependency_overrides[admin_accounts.require_platform_owner]=lambda:operator
        api.dependency_overrides[authenticate]=lambda:Actor("member",tenant,account,"owner")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api),base_url="https://test") as client:
            yield SimpleNamespace(engine=engine,tenant=tenant,other=other,account=account,second=second,operator=operator,
                api=api,client=client,path=f"/api/v1/admin/accounts/{account}/companies/{tenant}/plan")
    finally:
        async with engine.begin() as db:
            params={"a":tenant,"b":other}
            for table in ("tenant_plan_audit","tenant_plan_controls","orion_cakto_bindings","subscriptions",
                          "orion_plan_trial_requests","professionals","tenant_memberships"):
                await db.execute(text(f"DELETE FROM {table} WHERE tenant_id IN (:a,:b)"),params)
            await db.execute(text("DELETE FROM panel_accounts WHERE id IN (:a,:b,:c)"),{"a":account,"b":second,"c":operator})
            await db.execute(text("DELETE FROM tenants WHERE id IN (:a,:b)"),params)
        await engine.dispose()
        support_session_factory.cache_clear();get_engine.cache_clear()


async def assignment(c, code="elite", **extra):
    response=await c.client.get(c.path)
    assert response.status_code==200,response.text
    state=response.json()["effective_plan"]
    return {"expected_version":state["version"],"expected_revision":state["revision"],
        "operation_id":str(uuid4()),"confirmation":"ALTERAR PLANO","plan_code":code,"access_type":"indefinite",**extra}


@pytest.mark.asyncio
async def test_parallel_replay_and_stale_cas_create_one_audit_and_preserve_account_and_billing(manual_db):
    c=manual_db
    async with c.engine.connect() as db:
        original=(await db.execute(text("SELECT auth_version,password_hash FROM panel_accounts WHERE id=:id"),{"id":c.account})).one()
    payload=await assignment(c)
    results=await asyncio.gather(*(c.client.put(c.path,json=payload) for _ in range(8)))
    assert all(response.status_code==200 for response in results),[(r.status_code,r.text) for r in results]
    assert sum(not response.json()["replayed"] for response in results)==1
    async with c.engine.connect() as db:
        assert (await db.execute(text("SELECT count(*) FROM tenant_plan_audit WHERE tenant_id=:tenant"),{"tenant":c.tenant})).scalar_one()==1
        assert (await db.execute(text("SELECT count(*) FROM subscriptions WHERE tenant_id=:tenant"),{"tenant":c.tenant})).scalar_one()==0
        assert (await db.execute(text("SELECT auth_version,password_hash FROM panel_accounts WHERE id=:id"),{"id":c.account})).one()==original
    stale={**payload,"operation_id":str(uuid4()),"plan_code":"essencial"}
    assert (await c.client.put(c.path,json=stale)).status_code==409
    changed={**payload,"plan_code":"profissional"}
    assert (await c.client.put(c.path,json=changed)).status_code==409


@pytest.mark.asyncio
async def test_only_one_of_two_distinct_concurrent_changes_wins_and_other_tenant_is_untouched(manual_db):
    c=manual_db
    first=await assignment(c)
    second={**first,"operation_id":str(uuid4()),"plan_code":"profissional"}
    results=await asyncio.gather(c.client.put(c.path,json=first),c.client.put(c.path,json=second))
    assert sorted(response.status_code for response in results)==[200,409]
    foreign=f"/api/v1/admin/accounts/{c.account}/companies/{c.other}/plan"
    assert (await c.client.put(foreign,json=await assignment(c))).status_code==404
    async with c.engine.connect() as db:
        assert (await db.execute(text("SELECT count(*) FROM tenant_plan_controls WHERE tenant_id=:tenant"),{"tenant":c.other})).scalar_one()==0


@pytest.mark.asyncio
async def test_removal_restores_trial_then_no_access_without_changing_trial_or_session(manual_db):
    c=manual_db
    async with c.engine.begin() as db:
        await db.execute(text("""INSERT INTO orion_plan_trial_requests
            (tenant_id,account_id,full_name,company_name,whatsapp_e164,status,started_at,ends_at)
            VALUES(:tenant,:account,'Pessoa Sintética','Empresa Sintética','+5567999999901','active',now()-interval '1 day',now()+interval '2 days')"""),
            {"tenant":c.tenant,"account":c.account})
    response=await c.client.put(c.path,json=await assignment(c))
    state=response.json()["effective_plan"]
    removal={"expected_version":state["version"],"expected_revision":state["revision"],"operation_id":str(uuid4()),"confirmation":"REMOVER ATRIBUIÇÃO"}
    response=await c.client.request("DELETE",c.path,json=removal)
    assert response.status_code==200,response.text
    assert response.json()["effective_plan"]["source"]=="trial"
    assert (await c.client.request("DELETE",c.path,json=removal)).json()["replayed"] is True
    async with c.engine.begin() as db:
        await db.execute(text("UPDATE orion_plan_trial_requests SET started_at=now()-interval '4 days',ends_at=now()-interval '1 day' WHERE tenant_id=:tenant"),{"tenant":c.tenant})
    result=(await c.client.get(c.path)).json()["effective_plan"]
    assert result["source"]=="none" and result["status"]=="expired"
    assert result["permissions"]["plan.manage"] and not result["permissions"]["workspace.access"]


@pytest.mark.asyncio
async def test_verified_cakto_renewal_and_replay_keep_manual_elite_then_removal_uses_commercial_essencial(manual_db):
    c=manual_db
    subscription_id="synthetic-sub-"+c.tenant.hex
    previous_order="previous-"+c.tenant.hex
    current_order="current-"+c.tenant.hex
    async with c.engine.begin() as db:
        identity=(await db.execute(text("""INSERT INTO subscriptions(tenant_id,plan_id,external_subscription_id,status,last_verified_payment_at)
            SELECT :tenant,id,:external,'active',now()-interval '30 days' FROM plans WHERE code='orion_essencial' RETURNING id"""),
            {"tenant":c.tenant,"external":subscription_id})).scalar_one()
        await db.execute(text("""INSERT INTO orion_cakto_bindings(tenant_id,subscription_id,external_subscription_id,plan_code,last_order_id)
            VALUES(:tenant,:id,:external,'essencial',:order)"""),{"tenant":c.tenant,"id":identity,"external":subscription_id,"order":previous_order})
    assert (await c.client.put(c.path,json=await assignment(c))).status_code==200
    event={"event_type":"subscription_renewed","external_order_id":current_order,"external_subscription_id":subscription_id,
        "external_offer_id":"synthetic-essential-offer","currency":"BRL","amount_cents":2990}
    order={"id":current_order,"subscription":subscription_id,"type":"subscription","offer_type":"main","currency":"BRL",
        "status":"paid","amount":29.9,"paidAt":datetime.now(timezone.utc).isoformat()}
    class ReadOnlyProvider:
        async def _get(self,path):
            assert path=="/subscriptions/"+subscription_id+"/"
            return {"id":subscription_id,"offer":"synthetic-essential-offer","recurrence_period":30,
                    "paymentMethod":"credit_card","status":"active","amount":29.9}
        async def list_offers(self):
            return [{"id":"synthetic-essential-offer","name":"Orion Essencial","price":29.9,"status":"active","type":"subscription","recurrence_period":30}]
    assert await reconcile_existing_cakto_subscription(event,order,ReadOnlyProvider())=="reconciled"
    assert await reconcile_existing_cakto_subscription(event,order,ReadOnlyProvider())=="already_verified"
    state=(await c.client.get(c.path)).json()["effective_plan"]
    assert state["code"]=="elite" and state["source"]=="manual"
    assert state["commercial_subscription"]["code"]=="orion_essencial"
    removal={"expected_version":state["version"],"expected_revision":state["revision"],"operation_id":str(uuid4()),"confirmation":"REMOVER ATRIBUIÇÃO"}
    result=await c.client.request("DELETE",c.path,json=removal)
    assert result.status_code==200,result.text
    assert result.json()["effective_plan"]["source"]=="cakto" and result.json()["effective_plan"]["code"]=="essencial"


@pytest.mark.asyncio
async def test_downgrade_keeps_all_eight_professional_rows_and_existing_financial_state(manual_db):
    c=manual_db
    assert (await c.client.put(c.path,json=await assignment(c))).status_code==200
    async with c.engine.begin() as db:
        for index in range(8):
            await db.execute(text("INSERT INTO professionals(tenant_id,name) VALUES(:tenant,:name)"),{"tenant":c.tenant,"name":"Sintético "+str(index)})
        before=(await db.execute(text("SELECT * FROM professionals WHERE tenant_id=:tenant ORDER BY id"),{"tenant":c.tenant})).all()
    response=await c.client.put(c.path,json=await assignment(c,"essencial"))
    assert response.status_code==200,response.text
    assert response.json()["effective_plan"]["limits"]=={"max_professionals":2,"active_professionals":8,"excess_professionals":6}
    async with c.engine.connect() as db:
        after=(await db.execute(text("SELECT * FROM professionals WHERE tenant_id=:tenant ORDER BY id"),{"tenant":c.tenant})).all()
    assert before==after


@pytest.mark.asyncio
async def test_overview_permissions_and_legacy_subscription_share_rights_and_do_not_advance_auth_version(manual_db):
    c=manual_db
    assert (await c.client.put(c.path,json=await assignment(c,"profissional"))).status_code==200
    root=f"/api/v1/tenants/{c.tenant}/management"
    overview,permissions,legacy=await asyncio.gather(c.client.get(root+"/plan-overview"),c.client.get(root+"/permissions"),c.client.get(root+"/subscription"))
    assert overview.status_code==permissions.status_code==legacy.status_code==200
    assert overview.json()["effective_plan"]==permissions.json()["effective_plan"]==legacy.json()["effective_plan"]
    assert all(response.headers["cache-control"]=="no-store" for response in (overview,permissions,legacy))
    assert (await c.client.get(f"/api/v1/tenants/{c.other}/management/permissions")).status_code==403
    async with c.engine.connect() as db:
        row=(await db.execute(text("SELECT auth_version,last_access_at FROM panel_accounts WHERE id=:id"),{"id":c.account})).one()
    assert row.auth_version==1 and row.last_access_at is not None
    c.api.dependency_overrides[authenticate]=lambda:Actor("member",c.tenant,c.account,"owner",maintenance=True)
    assert (await c.client.get(root+"/permissions")).status_code==200
    async with c.engine.connect() as db:
        assert (await db.execute(text("SELECT auth_version,last_access_at FROM panel_accounts WHERE id=:id"),{"id":c.account})).one()==row


@pytest.mark.asyncio
async def test_temporary_manual_expiry_uses_the_same_real_snapshot_and_restores_valid_trial_without_mutation(manual_db):
    c=manual_db
    async with c.engine.begin() as db:
        await db.execute(text("""INSERT INTO orion_plan_trial_requests
            (tenant_id,account_id,full_name,company_name,whatsapp_e164,status,started_at,ends_at)
            VALUES(:tenant,:account,'Pessoa Sintética','Empresa Sintética','+5567999999901','active',now()-interval '1 hour',now()+interval '3 days')"""),
            {"tenant":c.tenant,"account":c.account})
    expiry=datetime.now(timezone.utc)+timedelta(days=1)
    response=await c.client.put(c.path,json=await assignment(c,access_type="temporary",expires_at=expiry.isoformat()))
    assert response.status_code==200,response.text
    assert response.json()["effective_plan"]["source"]=="manual"
    async with c.engine.connect() as db:
        snapshot=(await db.execute(text("SELECT public.orion_plan_snapshot(:tenant)"),{"tenant":c.tenant})).scalar_one()
        before=(await db.execute(text("SELECT version,manual_plan_code,manual_expires_at FROM tenant_plan_controls WHERE tenant_id=:tenant"),{"tenant":c.tenant})).one()
    # Advance the resolver's explicit clock, never the real database/system clock.
    effective=derive_plan(snapshot,now=expiry+timedelta(seconds=1))
    assert effective["source"]=="trial" and effective["code"]=="essencial"
    assert effective["manual_assignment"]["active"] is False
    async with c.engine.connect() as db:
        assert (await db.execute(text("SELECT version,manual_plan_code,manual_expires_at FROM tenant_plan_controls WHERE tenant_id=:tenant"),{"tenant":c.tenant})).one()==before


@pytest.mark.asyncio
async def test_runtime_can_read_only_its_effective_projection_and_support_cannot_rewrite_audit(manual_db):
    c=manual_db
    assert (await c.client.put(c.path,json=await assignment(c))).status_code==200
    async with get_session_factory()() as db:
        async with tenant_transaction(db,c.tenant):
            snapshot=(await db.execute(text("SELECT public.orion_plan_snapshot(:tenant)"),{"tenant":c.tenant})).scalar_one()
            assert snapshot["manual"]["plan_code"]=="elite"
    async with get_session_factory()() as db:
        with pytest.raises(DBAPIError):
            async with tenant_transaction(db,c.tenant):
                await db.execute(text("SELECT public.orion_plan_snapshot(:tenant)"),{"tenant":c.other})
    async with get_session_factory()() as db:
        with pytest.raises(DBAPIError):
            async with tenant_transaction(db,c.tenant):
                await db.execute(text("SELECT * FROM tenant_plan_controls"))
    async with support_session_factory()() as db:
        async with db.begin():
            for query in ("UPDATE tenant_plan_audit SET reason='changed' WHERE tenant_id=:tenant",
                          "DELETE FROM tenant_plan_audit WHERE tenant_id=:tenant"):
                with pytest.raises(DBAPIError):
                    async with db.begin_nested():
                        await db.execute(text(query),{"tenant":c.tenant})
