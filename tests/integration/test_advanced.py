"""FATIA 8: o padrão é não enviar; somente consentimento e workflow draft."""
import os
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.advanced_api import WorkflowDraft
from app.main import app


def test_workflow_rejects_arbitrary_code_and_excessive_steps():
    with pytest.raises(ValidationError):
        WorkflowDraft.model_validate({"name":"Malicioso","steps":[
            {"kind":"run_python","label":"exec"}]})
    with pytest.raises(ValidationError):
        WorkflowDraft.model_validate({"name":"Muito longo","steps":[
            {"kind":"tag","label":"x"} for _ in range(11)]})


@pytest.mark.integration
@pytest.mark.asyncio
async def test_inbox_campaign_consent_isolation_and_blocked_send():
    admin_url=os.getenv("MIGRATION_DATABASE_URL")
    api_key=os.getenv("SUPPORT_API_TOKEN")
    if not admin_url or not api_key:
        if os.getenv("REQUIRE_POSTGRES_TESTS")=="1":pytest.fail("Integração PostgreSQL requer credenciais sintéticas")
        pytest.skip("Ambiente indisponível")
    engine=create_async_engine(admin_url.replace("postgresql+psycopg://","postgresql+asyncpg://",1))
    a,b,ch=[uuid4() for _ in range(3)]
    auth={"Authorization":"Bearer "+api_key}
    phone="+5567999999999"
    try:
        async with engine.begin() as s:
            for tid in (a,b):
                await s.execute(text("""
                    INSERT INTO tenants (id,name,slug) VALUES (:id,'Teste',:slug)
                """),{"id":tid,"slug":"advanced-"+str(tid)})
            await s.execute(text("""
                INSERT INTO whatsapp_channels
                (id,tenant_id,phone_number_id,business_number,status)
                VALUES (:channel,:tenant,:phoneid,:phone,'testing')
            """),{"channel":ch,"tenant":a,"phoneid":"fatia8-"+str(ch),"phone":phone})
            await s.execute(text("""
                INSERT INTO human_handoffs
                (tenant_id,channel_id,contact_e164)
                VALUES (:tenant,:channel,:phone)
            """),{"tenant":a,"channel":ch,"phone":phone})
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test") as c:
            a_h=await c.get(f"/api/v1/tenants/{a}/inbox/handoffs",headers=auth)
            b_h=await c.get(f"/api/v1/tenants/{b}/inbox/handoffs",headers=auth)
            assert a_h.status_code==200 and len(a_h.json())==1
            assert b_h.status_code==200 and b_h.json()==[]
            consent=await c.post(f"/api/v1/tenants/{a}/crm/consent",headers=auth,
                                  json={"contact_e164":phone,"marketing_opt_in":True,
                                        "source":"customer_consent"})
            assert consent.status_code==200,consent.text
            campaign=await c.post(f"/api/v1/tenants/{a}/campaigns/draft",headers=auth,
                json={"name":"Lembrete teste","template_name":"template_sandbox"})
            assert campaign.status_code==201,campaign.text
            campaign_id=campaign.json()["id"]
            assert (await c.get(f"/api/v1/tenants/{a}/campaigns/{campaign_id}/eligible-count",
                                headers=auth)).json()["eligible_contacts"]==1
            assert (await c.get(f"/api/v1/tenants/{b}/campaigns/{campaign_id}/eligible-count",
                                headers=auth)).status_code==404
            blocked=await c.post(f"/api/v1/tenants/{a}/campaigns/{campaign_id}/send",headers=auth)
            assert blocked.status_code==423
            stop=await c.post(f"/api/v1/tenants/{a}/crm/consent",headers=auth,
                              json={"contact_e164":phone,"marketing_opt_in":False,"source":"whatsapp"})
            assert stop.status_code==200
            assert (await c.get(f"/api/v1/tenants/{a}/campaigns/{campaign_id}/eligible-count",
                                headers=auth)).json()["eligible_contacts"]==0
            flow=await c.post(f"/api/v1/tenants/{a}/workflows/draft",headers=auth,
                json={"name":"Workflow fictício","steps":[{"kind":"handoff","label":"Atendimento humano"}]})
            assert flow.status_code==201,flow.text
            assert flow.json()["enabled"] is False
        runtime_url=os.getenv("APP_DATABASE_URL")
        if runtime_url:
            runtime=create_async_engine(runtime_url)
            try:
                async with runtime.connect() as con:
                    assert (await con.execute(text("SELECT id FROM contact_consents"))).all()==[]
                    assert (await con.execute(text("SELECT id FROM human_handoffs"))).all()==[]
            finally:await runtime.dispose()
    finally:
        async with engine.begin() as s:
            for table in ("workflow_definitions","campaign_recipients","campaigns",
                          "contact_consents","human_handoffs","whatsapp_channels"):
                await s.execute(text(f"DELETE FROM {table} WHERE tenant_id=:tenant"),{"tenant":a})
            await s.execute(text("DELETE FROM tenants WHERE id IN (:a,:b)"),{"a":a,"b":b})
        await engine.dispose()
