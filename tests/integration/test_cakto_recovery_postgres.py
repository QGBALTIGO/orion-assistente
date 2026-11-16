"""Real isolated receipt ledger/worker recovery; every provider operation is fake GET."""
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import FastAPI
import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app import cakto_api, cakto_recovery
from app.plan_catalog import CODES
from tests.integration.test_cakto_lifecycle_boundaries_postgres import contract, state
from tests.integration.test_manual_plan_permissions_postgres import manual_db

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def recovery_db(manual_db):
    c = manual_db
    try:
        yield c
    finally:
        # Order IDs in these tests end with this fixture's unique tenant token.
        async with c.engine.begin() as db:
            await db.execute(text("DELETE FROM cakto_webhook_events WHERE external_order_id LIKE :suffix"),
                             {"suffix":"%-"+c.tenant.hex})


class ReadOnlyProvider:
    configured = True

    def __init__(self,data):
        self.data = data
        self.calls = []
        self.orders = {
            data.order["id"]:dict(data.order),
            data.previous:{**data.order,"id":data.previous,
                           "paidAt":(datetime.now(timezone.utc)-timedelta(days=30)).isoformat()},
        }

    async def get_order(self,order_id):
        self.calls.append(("GET","order"))
        return dict(self.orders[order_id])

    async def _get(self,path,**params):
        self.calls.append(("GET","subscription" if path.startswith("/subscriptions/") else "orders"))
        if path == "/subscriptions/"+self.data.remote["id"]+"/":
            return {**self.data.remote,"orders":list(self.orders)}
        assert path == "/orders/"
        ids = params["id"].split(",")
        return {"results":[dict(self.orders[value]) for value in ids],"next":None}

    async def list_offers(self):
        raise AssertionError("Recovery must not consult a mutable sales catalog")


def signed_payload(data,secret,event="subscription_renewed"):
    payload = {"secret":secret,"event":event,"data":{
        **data.order,"customer":{"name":"Never retain this synthetic body"},
    }}
    raw = json.dumps(payload,separators=(",",":")).encode()
    timestamp = str(int(time.time()))
    signature = "v1="+hmac.new(secret.encode(),timestamp.encode()+b"."+raw,hashlib.sha256).hexdigest()
    return raw,{"X-Cakto-Timestamp":timestamp,"X-Cakto-Signature":signature}


@pytest.mark.asyncio
async def test_http503_without_receipt_is_recovered_by_get_and_late_hmac_cannot_double_renew(
    recovery_db,monkeypatch,
):
    c = recovery_db
    data = await contract(c)
    provider = ReadOnlyProvider(data)
    monkeypatch.setattr(cakto_api,"cached_cakto_client",lambda:provider)
    monkeypatch.setenv("ORION_CAKTO_LIFECYCLE_ENABLED","1")
    secret = "synthetic-webhook-secret-for-recovery-only"
    monkeypatch.setenv("CAKTO_WEBHOOK_SECRET",secret)
    monkeypatch.setenv("CAKTO_PIX_WEBHOOK_SECRET","")
    api = FastAPI()
    api.include_router(cakto_api.router)
    raw,headers = signed_payload(data,secret)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api),base_url="https://synthetic") as client:
        async def unavailable(events,**kwargs): raise SQLAlchemyError("synthetic outage")
        with monkeypatch.context() as temporary:
            temporary.setattr(cakto_api,"persist_events",unavailable)
            response = await client.post("/webhooks/cakto",content=raw,headers=headers)
        assert response.status_code == 503
        async with c.engine.connect() as db:
            assert (await db.execute(text("SELECT count(*) FROM cakto_webhook_events WHERE external_order_id=:id"),
                                     {"id":data.order["id"]})).scalar_one() == 0
        assert await cakto_api.scan_pending_cakto({}) == 1
        paid = await state(c)
        assert paid.last_order_id == data.order["id"]
        assert paid.last_verified_payment_at == datetime.fromisoformat(data.order["paidAt"])
        assert (await client.post("/webhooks/cakto",content=raw,headers=headers)).status_code == 200
        assert await cakto_api.scan_pending_cakto({}) == 1
        assert await state(c) == paid
    async with c.engine.connect() as db:
        rows = (await db.execute(text("""
            SELECT receipt_origin,verification_state,delivery_digest
            FROM cakto_webhook_events WHERE external_order_id=:id
        """), {"id":data.order["id"]})).all()
        assert {(r.receipt_origin,r.verification_state) for r in rows} == {
            ("provider_read","verified"),("webhook_hmac","verified")}
        assert len(rows) == 2 and len({r.delivery_digest for r in rows}) == 2
    assert all(method=="GET" for method,_ in provider.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("prior,expected", [
    ("failed","pending"),("mismatch","pending"),("manual_review","manual_review"),
    ("superseded","superseded"),("verified","verified"),
])
async def test_only_fresh_bound_read_reopens_retryable_provider_receipt(recovery_db,prior,expected):
    c = recovery_db
    data = await contract(c)
    event = cakto_recovery.receipt("subscription_renewed",data.order,offer_id=data.event["external_offer_id"])
    await cakto_api.persist_events([event],origin="provider_read")
    async with c.engine.begin() as db:
        await db.execute(text("""UPDATE cakto_webhook_events SET verification_state=:state,
            attempts=8,next_attempt_at=now()+interval '1 day' WHERE external_order_id=:id"""),
                         {"state":prior,"id":data.order["id"]})
    async def on_receipt(item): await cakto_api.persist_events([item],origin="provider_read")
    receipts = await cakto_recovery.recover_recurring_provider_receipts(
        ReadOnlyProvider(data),limit=1,on_receipt=on_receipt)
    assert len(receipts) == 1
    async with c.engine.connect() as db:
        row = (await db.execute(text("""SELECT verification_state,attempts FROM cakto_webhook_events
            WHERE external_order_id=:id"""), {"id":data.order["id"]})).one()
    assert row.verification_state == expected
    assert row.attempts == (0 if expected=="pending" else 8)


@pytest.mark.asyncio
async def test_cancelled_live_paid_access_is_cleared_by_missing_refund_webhook(recovery_db,monkeypatch):
    c = recovery_db
    data = await contract(c)
    provider = ReadOnlyProvider(data)
    monkeypatch.setenv("ORION_CAKTO_LIFECYCLE_ENABLED","1")
    monkeypatch.setattr(cakto_api,"cached_cakto_client",lambda:provider)
    assert await cakto_api.scan_pending_cakto({}) == 1
    provider.data.remote["status"] = "canceled"
    async with c.engine.begin() as db:
        await db.execute(text("UPDATE orion_cakto_bindings SET provider_checked_at=NULL WHERE tenant_id=:tenant"),
                         {"tenant":c.tenant})
    assert await cakto_api.scan_pending_cakto({}) == 1
    canceled = await state(c)
    assert canceled.status == "canceled" and canceled.grace_until is not None
    provider.orders[data.order["id"]]["status"] = "refunded"
    async with c.engine.begin() as db:
        await db.execute(text("UPDATE orion_cakto_bindings SET provider_checked_at=NULL WHERE tenant_id=:tenant"),
                         {"tenant":c.tenant})
    assert await cakto_api.scan_pending_cakto({}) == 1
    assert (await state(c)).status == "canceled" and (await state(c)).grace_until is None
    async with c.engine.connect() as db:
        assert (await db.execute(text("""SELECT count(*) FROM cakto_webhook_events
            WHERE external_order_id=:id AND event_type='refund'
              AND receipt_origin='provider_read' AND verification_state='verified'"""),
                                 {"id":data.order["id"]})).scalar_one() == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("prior,attempts,bound,claimed", [
    ("failed",8,True,True),("failed",16,True,False),("failed",8,False,False),
    ("mismatch",8,True,False),("manual_review",8,True,False),("superseded",8,True,False),
])
async def test_failed_retry_is_finite_and_requires_a_preexisting_local_binding(
    recovery_db,monkeypatch,prior,attempts,bound,claimed,
):
    c = recovery_db
    data = await contract(c)
    provider = ReadOnlyProvider(data)
    event = cakto_recovery.receipt("subscription_renewed",data.order,offer_id=data.event["external_offer_id"])
    if not bound:
        event["subscription_id"] = "unbound-subscription"
    await cakto_api.persist_events([event])
    async with c.engine.begin() as db:
        await db.execute(text("""UPDATE cakto_webhook_events SET verification_state=:state,
            attempts=:attempts,next_attempt_at=now() WHERE external_order_id=:id"""),
                         {"state":prior,"attempts":attempts,"id":data.order["id"]})
    monkeypatch.setenv("ORION_CAKTO_LIFECYCLE_ENABLED","1")
    monkeypatch.setattr(cakto_api,"cached_cakto_client",lambda:provider)
    monkeypatch.setattr(cakto_api,"recover_provider_receipts",AsyncMock(return_value=0))
    assert await cakto_api.scan_pending_cakto({}) == int(claimed)
    async with c.engine.connect() as db:
        row = (await db.execute(text("SELECT verification_state,attempts FROM cakto_webhook_events WHERE external_order_id=:id"),
                                {"id":data.order["id"]})).one()
    assert row.verification_state == ("verified" if claimed else prior)
    assert row.attempts == attempts+int(claimed)
    if not claimed:
        assert not provider.calls


@pytest.mark.asyncio
async def test_failed_hosted_receipt_recovers_only_exact_pending_owner_intent(recovery_db,monkeypatch):
    c = recovery_db
    token = "synthetic-callback-"+c.tenant.hex
    first = "initial-"+c.tenant.hex
    sub = "subscription-"+c.tenant.hex
    offer = "offer-"+c.tenant.hex
    paid_at = datetime.now(timezone.utc)-timedelta(minutes=15)
    async with c.engine.begin() as db:
        await db.execute(text("""INSERT INTO orion_cakto_checkout_intents
          (tenant_id,account_id,plan_code,offer_id,expected_cents,callback_hash,state,created_at,expires_at)
          VALUES (:tenant,:account,'essencial',:offer,2990,:hash,'pending',
                  now()-interval '1 day',now()-interval '10 minutes')"""),
                         {"tenant":c.tenant,"account":c.account,"offer":offer,
                          "hash":hashlib.sha256(token.encode()).hexdigest()})
    order = {"id":first,"subscription":sub,"type":"subscription","offer_type":"main",
             "status":"paid","amount":"29.90","currency":"BRL","paymentMethod":"pix_auto",
             "paidAt":paid_at.isoformat(),"createdAt":paid_at.isoformat(),"sck":token,"callback":token,
             "offer":{"id":offer},"checkoutUrl":CODES["essencial"]["checkout_url"]}
    data = SimpleNamespace(order=order,previous="previous-"+c.tenant.hex,remote={
        "id":sub,"parent_order":first,"status":"active","offer":offer,
        "amount":"29.90","paymentMethod":"pix_auto","recurrence_period":30})
    event = cakto_recovery.receipt("purchase_approved",order,offer_id=offer,
                                  callback_hash=hashlib.sha256(token.encode()).hexdigest())
    await cakto_api.persist_events([event])
    async with c.engine.begin() as db:
        await db.execute(text("""UPDATE cakto_webhook_events SET verification_state='failed',attempts=8,
            next_attempt_at=now() WHERE external_order_id=:id"""), {"id":first})
    monkeypatch.setenv("ORION_CAKTO_LIFECYCLE_ENABLED","1")
    monkeypatch.setattr(cakto_api,"cached_cakto_client",lambda:ReadOnlyProvider(data))
    monkeypatch.setattr(cakto_api,"recover_provider_receipts",AsyncMock(return_value=0))
    assert await cakto_api.scan_pending_cakto({}) == 1
    async with c.engine.connect() as db:
        record = (await db.execute(text("""SELECT s.status,s.last_verified_payment_at,b.last_order_id
            FROM subscriptions s JOIN orion_cakto_bindings b ON b.subscription_id=s.id
            WHERE s.tenant_id=:tenant"""), {"tenant":c.tenant})).one()
        assert record.status == "active" and record.last_order_id == first
        assert record.last_verified_payment_at == paid_at
