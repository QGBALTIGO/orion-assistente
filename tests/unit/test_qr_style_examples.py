"""Visual examples use one real scoped grid; every business write is forbidden."""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, time, timedelta
from types import SimpleNamespace
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException
import httpx
import pytest

from app import qr_ai, qr_profile, tenant_access
from app.catalog_api import Actor, authenticate
from app.business_profile_schemas import BusinessProfile


@pytest.fixture
def visual(monkeypatch):
    tenant, other, cut, beard, pro, account=(uuid4() for _ in range(6))
    zone=ZoneInfo("America/Campo_Grande");target=datetime.now(zone).date()+timedelta(days=1)
    catalog=[{"id":cut,"name":"Corte","price_cents":3500,"duration_min":40},
             {"id":beard,"name":"Barba","price_cents":2000,"duration_min":20}]
    professionals=[{"id":pro,"name":"Bruno (Demonstração)"}]
    business={"name":"Empresa sintética","timezone":str(zone),"profile_id":str(uuid4()),
        "profile":BusinessProfile(business_name="Empresa sintética",capabilities=["appointments","catalog_answers"]).model_dump(mode="json")}
    events=[]
    values={"rows":[{"timezone":str(zone),"booking_horizon_days":30,"min_notice_minutes":30,
                     "weekday":target.weekday(),"end_time":time(18)}],"empty":False,"eligible":True,"slow":False}
    actor=Actor("member",account_id=account)
    async def access(given,t,operation="read"):
        assert given.account_id == account and operation == "read"
        events.append(("access",t))
        if t != tenant: raise HTTPException(403,"synthetic-outside-membership")
        return tenant_access.TenantAccess("viewer",account)
    async def snapshot(t,version=None,style_rules=None):
        assert t == tenant
        events.append(("profile",version));return None
    async def data(t):
        assert t == tenant;events.append(("data",t))
        return business,catalog,professionals
    async def eligible(t,ids):
        assert t == tenant and set(ids) <= {cut,beard}
        events.append(("eligible",tuple(ids)))
        return professionals if values["eligible"] else []
    class DB:
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
        async def execute(self,query,parameters):
            assert str(query).strip().startswith("SELECT") and "working_hours" in str(query)
            assert parameters == {"tenant":tenant,"professional":pro}
            events.append(("metadata",parameters))
            return SimpleNamespace(mappings=lambda:SimpleNamespace(all=lambda:values["rows"]))
    @asynccontextmanager
    async def transaction(db,t):
        assert t == tenant;yield db
    async def grid(db,t,p,ids,day):
        assert t == tenant and p == pro and set(ids) <= {cut,beard}
        today=datetime.now(zone).date()
        assert today <= day <= today+timedelta(days=values["rows"][0]["booking_horizon_days"])
        events.append(("grid",tuple(ids),day))
        if values["slow"]: await asyncio.sleep(.2)
        return {"slots":[] if values["empty"] else [datetime.combine(day,time(14,30),zone).isoformat()],
            "price_cents":sum(x["price_cents"] for x in catalog if x["id"] in ids),
            "duration_min":sum(x["duration_min"] for x in catalog if x["id"] in ids),"timezone":str(zone)}
    async def forbidden(*a,**k):
        raise AssertionError("Visual examples must never execute or read a customer's operation")
    monkeypatch.setattr(tenant_access,"resolve_tenant_access",access)
    monkeypatch.setattr(qr_profile,"resolve_preview",snapshot)
    monkeypatch.setattr(qr_ai,"tenant_data",data)
    monkeypatch.setattr(qr_ai,"professionals_for",eligible)
    monkeypatch.setattr(qr_ai,"get_session_factory",lambda:DB)
    monkeypatch.setattr(qr_ai,"tenant_transaction",transaction)
    monkeypatch.setattr(qr_ai,"list_slots",grid)
    for name in ("book_appointment","confirm_hold","reschedule_appointment","remember_name","customer_appointments"):
        monkeypatch.setattr(qr_ai,name,forbidden)
    app=FastAPI();app.include_router(qr_ai.router)
    app.dependency_overrides[authenticate]=lambda:actor
    async def request(t=None):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="https://test") as client:
            return await client.post(f"/api/v1/tenants/{t or tenant}/ai/style-examples",json={})
    return SimpleNamespace(**locals())


@pytest.mark.asyncio
async def test_three_visual_examples_per_style_share_one_scoped_real_slot_and_no_operations(visual,monkeypatch):
    preserved=[];render=qr_ai.presentation.render_summary
    def observed(summary,*args,**kwargs):
        answer=render(summary,*args,**kwargs)
        preserved.append(summary.protocol)
        return answer
    monkeypatch.setattr(qr_ai.presentation,"render_summary",observed)
    h=visual;response=await h.request();assert response.status_code == 200
    data=response.json();assert len(data["examples"]) == 4
    assert len([x for x in h.events if x[0]=="grid"]) == 1
    assert len([x for x in h.events if x[0]=="metadata"]) == 1
    for row in data["examples"]:
        assert {"answer","confirmation_example","cancellation_example"} <= row.keys()
        assert "R$ 55,00" in row["answer"]
        for field in ("confirmation_example","cancellation_example"):
            text=row[field]
            assert text.startswith("*Exemplo visual — nenhuma operação realizada*")
            assert all(value in text for value in ("Corte e Barba","Bruno (Demonstração)",h.target.strftime("%d/%m/%Y"),"14h30","1 hora","R$ 55,00"))
            assert ("EXEMPLO-VISUAL-SEM-VALIDADE" in text) is (field == "cancellation_example")
            assert "Protocolo de exemplo, sem validade" in text
    assert preserved == ["EXEMPLO-VISUAL-SEM-VALIDADE"]*8
    assert qr_profile.preview_snapshot.get() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("condition",["disabled","products_only","no_services","no_professional","no_eligibility","no_hours","outside_horizon","no_slots"])
async def test_missing_operational_facts_explain_without_fabricated_date_name_or_protocol(visual,condition):
    h=visual
    if condition == "disabled": h.business["profile"]["capabilities"]=["catalog_answers"]
    elif condition == "products_only":
        h.business["profile"]["capabilities"]=["catalog_answers","product_orders"];h.catalog.clear();h.professionals.clear()
    elif condition == "no_services": h.catalog.clear()
    elif condition == "no_professional": h.professionals.clear()
    elif condition == "no_eligibility": h.values["eligible"]=False
    elif condition == "no_hours": h.values["rows"]=[{**h.values["rows"][0],"weekday":None,"end_time":None}]
    elif condition == "outside_horizon": h.values["rows"]=[{**h.values["rows"][0],"booking_horizon_days":1,"min_notice_minutes":4000}]
    elif condition == "no_slots": h.values["empty"]=True
    response=await h.request();assert response.status_code == 200
    for row in response.json()["examples"]:
        for field in ("confirmation_example","cancellation_example"):
            text=row[field]
            assert text.startswith("*Exemplo visual — nenhuma operação realizada*")
            assert "*Data:*" not in text and "*Profissional:*" not in text and "*Protocolo:*" not in text
            assert "EXEMPLO-VISUAL-SEM-VALIDADE" not in text
            if condition in {"disabled","products_only"}: assert "não se aplicam" in text
    assert len([x for x in h.events if x[0]=="grid"]) == int(condition=="no_slots")


@pytest.mark.asyncio
async def test_grid_deadline_keeps_price_example_and_explains_operational_absence(visual,monkeypatch):
    h=visual;h.values["slow"]=True
    original=asyncio.timeout
    def bounded(seconds):
        assert seconds == 12
        return original(.02)
    monkeypatch.setattr(qr_ai.asyncio,"timeout",bounded)
    response=await h.request();assert response.status_code == 200
    for row in response.json()["examples"]:
        assert "R$ 55,00" in row["answer"] and "12 segundos" in row["confirmation_example"]
        assert "*Protocolo:*" not in row["confirmation_example"]
    assert len([x for x in h.events if x[0]=="grid"]) == 1


@pytest.mark.asyncio
async def test_another_tenant_is_rejected_before_catalog_or_grid_read(visual):
    h=visual;response=await h.request(h.other)
    assert response.status_code == 403 and h.events == [("access",h.other)]


@pytest.mark.asyncio
async def test_eligibility_fallback_remains_one_grid_and_quote_uses_that_factual_bundle(visual,monkeypatch):
    h=visual
    async def eligible(t,ids):
        assert t == h.tenant;h.events.append(("eligible",tuple(ids)))
        return h.professionals if ids == [h.cut] else []
    monkeypatch.setattr(qr_ai,"professionals_for",eligible)
    response=await h.request();assert response.status_code == 200
    assert len([x for x in h.events if x[0]=="eligible"]) == 2
    assert len([x for x in h.events if x[0]=="grid"]) == 1
    for row in response.json()["examples"]:
        assert "R$ 35,00" in row["answer"] and "Barba" not in row["answer"]
        assert "R$ 35,00" in row["confirmation_example"] and "40 minutos" in row["confirmation_example"]
