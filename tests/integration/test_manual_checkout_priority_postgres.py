"""Checkout admission and provider-start serialization on isolated PostgreSQL.

Every provider call is replaced. No real payment, webhook or message is sent.
"""
import asyncio
import hashlib
from datetime import datetime

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import text

from app import plan_management as plans, orion_direct_checkout as direct
from app.catalog_api import Actor
from test_manual_plan_permissions_postgres import manual_db, assignment  # noqa: F401


@pytest_asyncio.fixture
async def checkout_db(manual_db,monkeypatch):
    c=manual_db
    c.provider_posts=0;c.offer_reads=0;c.on_offers=None;c.on_post=None
    monkeypatch.setenv("CAKTO_BILLING_LIVE_ENABLED","1")
    monkeypatch.setenv("INTEGRATION_FERNET_KEY","synthetic-private-checkout-key-for-isolated-pg-"+"x"*32)
    monkeypatch.setattr(direct,"require_enabled",lambda:None)
    async def webhooks(*_): pass
    monkeypatch.setattr(direct,"require_verified_webhooks",webhooks)
    class Client:
        async def list_offers(self):
            c.offer_reads+=1
            if c.on_offers: await c.on_offers()
            return [{"id":"synthetic-offer","name":"Orion Essencial","price":29.9,"status":"active","type":"subscription","recurrence_period":30}]
        async def token(self): return "synthetic-not-a-provider-token"
    class Http:
        def __init__(self,**_): pass
        async def __aenter__(self): return self
        async def __aexit__(self,*_): pass
        async def post(self,url,**kwargs):
            assert url.endswith("/payments/")
            c.provider_posts+=1
            if c.on_post: await c.on_post()
            payload={"id":"synthetic-created-order","paymentMethod":"pix_auto","offer":{"id":"synthetic-offer"},"amount":29.9,
                "status":"waiting_payment","pix":{"qrCode":"synthetic-pix-authorization-"*5,"user_journey":"JORNADA_1"}}
            class Reply:
                def raise_for_status(self): pass
                def json(self): return payload
            return Reply()
    monkeypatch.setattr(plans,"CaktoClient",Client)
    monkeypatch.setattr(direct,"CaktoClient",Client)
    monkeypatch.setattr(direct.httpx,"AsyncClient",Http)
    try:
        yield c
    finally:
        async with c.engine.begin() as db:
            for table in ("orion_direct_payment_attempts","orion_cakto_checkout_intents"):
                await db.execute(text(f"DELETE FROM {table} WHERE tenant_id IN (:a,:b)"),{"a":c.tenant,"b":c.other})


async def intent(c):
    from uuid import uuid4
    identity=uuid4()
    token=plans._replayable_checkout_token(c.tenant,identity,"essencial")
    async with c.engine.begin() as db:
        await db.execute(text("""INSERT INTO orion_cakto_checkout_intents
            (id,tenant_id,account_id,plan_code,offer_id,expected_cents,callback_hash,expires_at)
            VALUES(:id,:tenant,:account,'essencial','synthetic-offer',2990,:hash,now()+interval '1 day')"""),
            {"id":identity,"tenant":c.tenant,"account":c.account,"hash":hashlib.sha256(token.encode()).hexdigest()})
    return identity


def charge(identity):
    return direct.DirectCharge(intent_id=identity,method="pix",confirmed_monthly=True,consent_to_processor=True,
        payer={"name":"Pessoa Sintética","email":"payer@example.test","phone":"67999999901","doc_type":"cpf",
               "doc_number":"11144477735","fingerprint":"synthetic-local-fingerprint"})


@pytest.mark.asyncio
async def test_manual_priority_blocks_all_new_checkout_forms_without_changing_existing_intent(checkout_db):
    c=checkout_db;identity=await intent(c)
    assert (await c.client.put(c.path,json=await assignment(c,"essencial"))).status_code==200
    actor=Actor("member",c.tenant,c.account,"owner")
    for action in (
        lambda:plans.start_plan_checkout(c.tenant,plans.CheckoutChoice(plan_code="essencial"),actor),
        lambda:direct.direct_prepare(c.tenant,direct.DirectPrepare(plan_code="essencial"),actor),
        lambda:direct.direct_charge(c.tenant,charge(identity),actor)):
        with pytest.raises(HTTPException) as caught: await action()
        assert caught.value.status_code==409 and caught.value.detail["code"]=="MANUAL_PLAN_ACTIVE"
    async with c.engine.connect() as db:
        assert (await db.execute(text("SELECT state FROM orion_cakto_checkout_intents WHERE id=:id"),{"id":identity})).scalar_one()=="pending"
        assert (await db.execute(text("SELECT count(*) FROM orion_direct_payment_attempts WHERE intent_id=:id"),{"id":identity})).scalar_one()==0
    assert c.provider_posts==c.offer_reads==0


@pytest.mark.asyncio
async def test_manual_committed_after_durable_reservation_blocks_unsent_provider_attempt(checkout_db):
    c=checkout_db;identity=await intent(c)
    async def grant():
        c.on_offers=None
        response=await c.client.put(c.path,json=await assignment(c))
        assert response.status_code==200,response.text
    c.on_offers=grant
    with pytest.raises(HTTPException) as caught:
        await direct.direct_charge(c.tenant,charge(identity),Actor("member",c.tenant,c.account,"owner"))
    assert caught.value.detail["code"]=="MANUAL_PLAN_ACTIVE" and c.provider_posts==0
    async with c.engine.connect() as db:
        row=(await db.execute(text("SELECT state,external_order_id FROM orion_direct_payment_attempts WHERE intent_id=:id"),{"id":identity})).one()
    assert row.state=="needs_review" and row.external_order_id is None
    assert (await direct.direct_status(c.tenant,identity,Actor("member",c.tenant,c.account,"owner")))["status"]=="review_required"


@pytest.mark.asyncio
async def test_manual_change_waits_for_already_started_provider_request_without_cancelling_or_repeating_it(checkout_db):
    c=checkout_db;identity=await intent(c)
    entered,release=asyncio.Event(),asyncio.Event()
    async def pause_post():
        entered.set()
        await asyncio.wait_for(release.wait(),10)
    c.on_post=pause_post
    payment=asyncio.create_task(direct.direct_charge(c.tenant,charge(identity),Actor("member",c.tenant,c.account,"owner")))
    administrative=None
    try:
        await asyncio.wait_for(entered.wait(),10)
        payload=await assignment(c)
        administrative=asyncio.create_task(c.client.put(c.path,json=payload))
        await asyncio.sleep(0.15)
        assert not administrative.done(),"Manual change must wait on the provider's shared plan lease"
        async with c.engine.connect() as db:
            assert (await db.execute(text("SELECT count(*) FROM tenant_plan_controls WHERE tenant_id=:tenant"),{"tenant":c.tenant})).scalar_one()==0
    finally:
        release.set()
    paid=await asyncio.wait_for(payment,10)
    changed=await asyncio.wait_for(administrative,10)
    assert paid["status"]=="pending_confirmation" and changed.status_code==200
    assert changed.json()["effective_plan"]["source"]=="manual" and c.provider_posts==1
    async with c.engine.connect() as db:
        row=(await db.execute(text("SELECT state,external_order_id FROM orion_direct_payment_attempts WHERE intent_id=:id"),{"id":identity})).one()
    assert row.state=="created" and row.external_order_id=="synthetic-created-order"
