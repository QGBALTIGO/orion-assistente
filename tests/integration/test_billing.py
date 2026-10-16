"""FATIA 6: Asaas autenticado, idempotência, reconciliação Fake e landing."""
import os
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.billing_api import verify_webhook_token,reconcile_event
from app.main import app


@pytest.mark.asyncio
async def test_landing_without_fake_phone_and_with_configured_sales_number(monkeypatch):
    monkeypatch.delenv("SALES_WHATSAPP_E164",raising=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test") as c:
        r=await c.get("/")
        assert r.status_code==200
        assert "Contato comercial em configuração" in r.text
        assert "DEMONSTRAÇÃO" not in r.text or "demonstração" in r.text.lower()
        assert "Você cuida do corte" in r.text
        assert "https://wa.me/" not in r.text
        monkeypatch.setenv("SALES_WHATSAPP_E164","+5567999999999")
        r=await c.get("/")
        assert "https://wa.me/5567999999999" in r.text
        assert "Exemplo ilustrativo" in r.text


@pytest.mark.asyncio
async def test_asaas_header_not_api_key(monkeypatch):
    monkeypatch.setenv("ASAAS_WEBHOOK_TOKEN","asaas-token-sintetico-para-os-testes-de-autenticacao")
    assert verify_webhook_token("asaas-token-sintetico-para-os-testes-de-autenticacao")
    assert not verify_webhook_token("api-key-invalid")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test") as c:
        assert (await c.post("/webhooks/asaas",json={"id":"evt1"})).status_code==401
        assert (await c.post("/webhooks/asaas",json={"id":"evt1"},
                   headers={"asaas-access-token":"wrong"})).status_code==401
        assert (await c.post("/webhooks/asaas",json={
                   "id":"evt_test_unknown","event":"PAYMENT_RECEIVED",
                   "payment":{"id":"pay_test","subscription":"unknown"}
               },headers={"asaas-access-token":"asaas-token-sintetico-para-os-testes-de-autenticacao"})).status_code==200


class FakeVerifiedAsaas:
    def __init__(self,external_id,status="RECEIVED"):
        self.external_id=external_id
        self.status=status

    async def get_payment(self,payment_id:str):
        return {"id":payment_id,"subscription":self.external_id,"status":self.status}


@pytest.mark.integration
@pytest.mark.asyncio
async def test_subscription_changes_only_after_reconciled_payment(monkeypatch):
    url=os.getenv("MIGRATION_DATABASE_URL")
    if not url:
        if os.getenv("REQUIRE_POSTGRES_TESTS")=="1": pytest.fail("Banco real necessário")
        pytest.skip("Banco não conectado")
    token="asaas-token-sintetico-para-os-testes-de-autenticacao"
    monkeypatch.setenv("ASAAS_WEBHOOK_TOKEN",token)
    admin=create_async_engine(url.replace("postgresql+psycopg://","postgresql+asyncpg://",1))
    tenant,other,plan,sub=[uuid4() for _ in range(4)]
    external_id="sub_synthetic_"+str(sub)
    event_id="event_fake_"+str(uuid4())
    payment_id="pay_fake_"+str(uuid4())
    try:
        async with admin.begin() as con:
            for tid in (tenant,other):
                await con.execute(text("INSERT INTO tenants (id,name,slug) VALUES (:id,'Test',:slug)"),
                                  {"id":tid,"slug":"billing-"+str(tid)})
            await con.execute(text("""
                INSERT INTO plans (id,code,name,monthly_cents,max_professionals,
                  max_messages_month,active) VALUES
                (:id,:code,'Plano sintético',5990,1,1000,true)
            """),{"id":plan,"code":"synthetic-"+str(plan)})
            await con.execute(text("""
                INSERT INTO subscriptions
                    (id,tenant_id,plan_id,external_subscription_id,status)
                VALUES (:id,:t,:plan,:external,'trialing')
            """),{"id":sub,"t":tenant,"plan":plan,"external":external_id})
        event={"id":event_id,"event":"PAYMENT_RECEIVED",
               "payment":{"id":payment_id,"subscription":external_id}}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test") as client:
            for i in range(2):
                result=await client.post("/webhooks/asaas",json=event,
                                          headers={"asaas-access-token":token})
                assert result.status_code==200,result.text
        async with admin.connect() as con:
            before=(await con.execute(text("SELECT status FROM subscriptions WHERE id=:id"),
                                      {"id":sub})).scalar_one()
            events=(await con.execute(text("""
                SELECT count(*) FROM billing_events WHERE tenant_id=:tenant
            """),{"tenant":tenant})).scalar_one()
            assert before=="trialing"
            assert events==1
        bad=await reconcile_event(event_id,FakeVerifiedAsaas("different-subscription"))
        assert bad=="mismatch"
        assert await reconcile_event(event_id,FakeVerifiedAsaas(external_id))=="active"
        assert await reconcile_event(event_id,FakeVerifiedAsaas(external_id))=="already_verified"
        async with admin.connect() as con:
            status=(await con.execute(text("SELECT status FROM subscriptions WHERE id=:id"),
                                      {"id":sub})).scalar_one()
            assert status=="active"
            other_subs=(await con.execute(text("SELECT count(*) FROM subscriptions WHERE tenant_id=:t"),
                                           {"t":other})).scalar_one()
            assert other_subs==0
    finally:
        async with admin.begin() as con:
            await con.execute(text("DELETE FROM billing_events WHERE tenant_id=:t"),{"t":tenant})
            await con.execute(text("DELETE FROM subscriptions WHERE tenant_id=:t"),{"t":tenant})
            await con.execute(text("DELETE FROM plans WHERE id=:id"),{"id":plan})
            await con.execute(text("DELETE FROM tenants WHERE id IN (:t,:other)"),{"t":tenant,"other":other})
        await admin.dispose()
