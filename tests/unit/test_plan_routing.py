from uuid import UUID, uuid4
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, FastAPI
import httpx
import pytest

from app import catalog_api, plan_permissions, plan_routing
from app.catalog_api import Actor, authenticate


@pytest.mark.parametrize("path,feature", [
    ("/management/dashboard","dashboard.basic"),("/management/finance/dre","finance.advanced"),
    ("/management/finance/commissions","commissions.advanced"),("/management/marketing/automations","notifications.basic"),
    ("/management/marketing/campaigns","marketing.campaigns"),("/management/marketing/jobs/{id}/pause","account.manage"),
    ("/management/ai/consultant","consultant.ai"),("/qr-channels","conversations.manual"),
    ("/workflows/{id}","reception.ai"),("/management/plan-overview","plan.manage"),
    ("/onboarding/complete","business.setup"),("/business/profile","business.setup"),
    ("/business/profile/publish","site.manage"),("/appointment-communications","notifications.basic")])
def test_route_classification(path,feature):
    assert plan_routing.feature_for_path(path)==feature


def test_unclassified_new_route_prevents_application_start():
    app=FastAPI();router=APIRouter()
    @router.get("/api/v1/tenants/{tenant_id}/unknown-future-feature")
    def endpoint(tenant_id:UUID): return {}
    with pytest.raises(RuntimeError): plan_routing.include_plan_router(app,router)


@pytest.mark.asyncio
async def test_real_included_route_dependency_denies_premium_before_handler_and_honors_target(monkeypatch):
    tenant,foreign,account=uuid4(),uuid4(),uuid4();called=[];locks=[]
    state={"tenant_id":str(tenant),"tenant_status":"active","manual":{"plan_code":"essencial",
        "started_at":(datetime.now(timezone.utc)-timedelta(days=1)).isoformat(),"expires_at":None}}
    class Result:
        def scalar_one(self): return state
    class Database:
        async def execute(self,query,params=None):
            if "advisory" in str(query): locks.append(str(query))
            return Result()
        @asynccontextmanager
        async def begin(self): yield self
        async def __aenter__(self): return self
        async def __aexit__(self,*args): return None
    monkeypatch.setattr(catalog_api,"support_session_factory",lambda:Database)
    app=FastAPI();router=APIRouter()
    @router.get("/api/v1/tenants/{tenant_id}/management/finance/dre")
    async def premium(tenant_id:UUID): called.append("premium");return {"ok":True}
    @router.post("/api/v1/tenants/{tenant_id}/management/professionals")
    async def staff(tenant_id:UUID):
        assert plan_permissions._scope.get().exclusive
        called.append("staff");return {"ok":True}
    plan_routing.include_plan_router(app,router)
    actor=Actor("member",tenant,account,"owner")
    app.dependency_overrides[authenticate]=lambda:actor
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="https://test") as client:
        response=await client.get(f"/api/v1/tenants/{tenant}/management/finance/dre")
        assert response.status_code==403 and called==[]
        assert response.json()["detail"]["code"]=="PLAN_FEATURE_UNAVAILABLE"
        assert (await client.post(f"/api/v1/tenants/{tenant}/management/professionals")).status_code==200
        assert (await client.get(f"/api/v1/tenants/{foreign}/management/finance/dre")).status_code==403
        state["manual"]["plan_code"]="elite"
        assert (await client.get(f"/api/v1/tenants/{tenant}/management/finance/dre")).status_code==200
        state["manual"]["plan_code"]="essencial"
        actor=Actor("admin",account_id=account)
        assert (await client.get(f"/api/v1/tenants/{tenant}/management/finance/dre")).status_code==200
        actor=Actor("member",tenant,account,"owner",maintenance=True)
        assert (await client.get(f"/api/v1/tenants/{tenant}/management/finance/dre")).status_code==403
    assert locks and plan_permissions._scope.get() is None


def test_all_current_tenant_routes_have_explicit_policy():
    from app.main import app
    schema=app.openapi()
    for path in schema["paths"]:
        if path.startswith(plan_routing.TENANT_PREFIX):
            assert plan_routing.feature_for_path(path[len(plan_routing.TENANT_PREFIX):]) is not None,path
