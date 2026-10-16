"""FATIA 3: autenticação Meta, dois canais isolados e webhook idempotente."""
import hashlib
import hmac
import json
import os
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.main import app
from app.messaging import FakeMessagingProvider, verify_meta_signature, extract_messages

pytestmark = pytest.mark.integration


def signed(body: bytes, secret: str) -> dict:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return {"X-Hub-Signature-256": "sha256=" + digest}


def make_payload(number: str, event_id: str, sender="5567999999999"):
    return {"object":"whatsapp_business_account","entry":[{"changes":[{"value":{
        "metadata":{"phone_number_id":number},
        "messages":[{"from":sender,"id":event_id,"type":"text","text":{"body":"Oi"}}]
    }}]}]}


@pytest.mark.asyncio
async def test_meta_handshake_signature_and_fake(monkeypatch):
    monkeypatch.setenv("META_VERIFY_TOKEN","token-meta-testing-muito-seguro")
    monkeypatch.setenv("META_APP_SECRET","secret-meta-ficticio-para-testes")
    data=b'{"demo":true}'
    assert verify_meta_signature(data,signed(data,"secret-meta-ficticio-para-testes")["X-Hub-Signature-256"],
                                 "secret-meta-ficticio-para-testes")
    assert not verify_meta_signature(data, "sha256=" + "0"*64,"secret-meta-ficticio-para-testes")
    assert not verify_meta_signature(data, None, "secret-meta-ficticio-para-testes")
    provider=FakeMessagingProvider()
    await provider.send_text({},"+5567999999999","Mensagem fake")
    assert provider.sent == [("+5567999999999","Mensagem fake")]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test") as c:
        ok=await c.get("/webhooks/whatsapp",params={
            "hub.mode":"subscribe", "hub.verify_token":"token-meta-testing-muito-seguro",
            "hub.challenge":"12345"
        })
        assert ok.status_code == 200 and ok.text == "12345"
        assert (await c.get("/webhooks/whatsapp",params={
            "hub.mode":"subscribe","hub.verify_token":"wrong","hub.challenge":"12345"
        })).status_code==403
        assert (await c.post("/webhooks/whatsapp",content=data)).status_code==401
        assert (await c.post("/webhooks/whatsapp",content=data,
                headers={"X-Hub-Signature-256":"sha256="+"1"*64})).status_code==401


@pytest.mark.asyncio
async def test_signed_webhook_routes_two_tenants_and_deduplicates(monkeypatch):
    url=os.getenv("MIGRATION_DATABASE_URL")
    if not url:
        if os.getenv("REQUIRE_POSTGRES_TESTS")=="1":
            pytest.fail("PostgreSQL real requerido")
        pytest.skip("PostgreSQL real não configurado")
    secret="secret-for-test-only-do-not-use"
    monkeypatch.setenv("META_APP_SECRET",secret)
    db=create_async_engine(url.replace("postgresql+psycopg://","postgresql+asyncpg://",1))
    a,b,chan_a,chan_b=[uuid4() for _ in range(4)]
    n1,n2="synthetic-meta-phone-1","synthetic-meta-phone-2"
    try:
        async with db.begin() as con:
            for tenant,channel,phone in ((a,chan_a,n1),(b,chan_b,n2)):
                await con.execute(text(
                    "INSERT INTO tenants (id,name,slug) VALUES (:tenant,'Teste',:slug)"
                ),{"tenant":tenant,"slug":"meta-test-"+str(tenant)})
                await con.execute(text("""
                    INSERT INTO whatsapp_channels (id,tenant_id,phone_number_id,
                        business_number,status)
                    VALUES (:channel,:tenant,:phone,'+5567999999999','testing')
                """),{"channel":channel,"tenant":tenant,"phone":phone})
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test") as c:
            for number,event in ((n1,"wamid.1"),(n2,"wamid.2"),(n1,"wamid.1")):
                body=json.dumps(make_payload(number,event)).encode()
                response=await c.post("/webhooks/whatsapp",content=body,headers=signed(body,secret))
                assert response.status_code==200,response.text
            forged=json.dumps(make_payload("unknown-phone","wamid.forge")).encode()
            assert (await c.post("/webhooks/whatsapp",content=forged,
                                  headers=signed(forged,secret))).status_code==200
        async with db.connect() as con:
            rows=(await con.execute(text("""
                SELECT tenant_id,channel_id,external_id FROM inbound_events
                WHERE tenant_id IN (:a,:b) ORDER BY external_id
            """),{"a":a,"b":b})).mappings().all()
            assert len(rows)==2
            assert {(r["tenant_id"],r["channel_id"]) for r in rows}=={(a,chan_a),(b,chan_b)}
    finally:
        async with db.begin() as con:
            for table in ("message_outbox","inbound_events","whatsapp_channels"):
                await con.execute(text(f"DELETE FROM {table} WHERE tenant_id IN (:a,:b)"),{"a":a,"b":b})
            await con.execute(text("DELETE FROM tenants WHERE id IN (:a,:b)"),{"a":a,"b":b})
        await db.dispose()


def test_parse_meta_message_and_ignore_status_only():
    value=make_payload("my_meta_number","wamid.abc")
    events=extract_messages(value)
    assert events[0]["sender_e164"]=="+5567999999999"
    assert events[0]["phone_number_id"]=="my_meta_number"
    assert events[0]["text"]=="Oi"
    assert extract_messages({"object":"whatsapp_business_account","entry":[]})==[]
