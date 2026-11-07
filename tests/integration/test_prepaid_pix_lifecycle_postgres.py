"""Whole prepaid lifecycle against local PostgreSQL; no live provider calls."""
import asyncio
import hashlib
import hmac
import json
import time
from datetime import datetime,timedelta,timezone
from uuid import uuid4

from fastapi import HTTPException
import pytest
import pytest_asyncio
import httpx
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from cryptography.fernet import Fernet

from app import prepaid_pix_api as api,prepaid_pix_reconcile as reconcile
from app.catalog_api import Actor
from app.prepaid_pix_recovery import recover_prepaid_provider_receipts
from test_manual_plan_permissions_postgres import manual_db  # noqa: F401

pytestmark=pytest.mark.integration


@pytest_asyncio.fixture
async def pix_db(manual_db,monkeypatch):
    c=manual_db
    monkeypatch.setenv("INTEGRATION_FERNET_KEY",Fernet.generate_key().decode())
    c.orders={};c.reads=[]
    class Provider:
        async def get_order(self,ref):
            c.reads.append(ref)
            return c.orders[ref]
    monkeypatch.setattr(api,"cached_cakto_client",Provider)
    c.api.include_router(api.router)
    c.actor=Actor("member",c.tenant,c.account,"owner")
    try:
        yield c
    finally:
        async with c.engine.begin() as db:
            await db.execute(text("""DELETE FROM cakto_webhook_events WHERE external_order_id IN
                (SELECT external_order_id FROM orion_prepaid_pix_orders WHERE tenant_id IN (:a,:b))
            """),{"a":c.tenant,"b":c.other})
            for table in ("orion_prepaid_pix_orders","orion_prepaid_pix_access"):
                await db.execute(text(f"DELETE FROM {table} WHERE tenant_id IN (:a,:b)"),{"a":c.tenant,"b":c.other})


async def purchase(c,*,state="created",external=True):
    identity=uuid4();ref="synthetic-"+identity.hex;token="synthetic-proof-"+identity.hex
    now=datetime.now(timezone.utc)
    async with c.engine.begin() as db:
        record=(await db.execute(text("""INSERT INTO orion_prepaid_pix_orders
            (id,tenant_id,account_id,plan_code,offer_id,expected_cents,callback_hash,
             external_order_id,state,created_at,expires_at)
            VALUES(:id,:tenant,:account,'essencial','synthetic-pix-offer',2990,:hash,
                   :external,:state,:created,:expires) RETURNING *
        """),{"id":identity,"tenant":c.tenant,"account":c.account,
               "hash":hashlib.sha256(token.encode()).hexdigest(),"external":ref if external else None,
               "state":state,"created":now-timedelta(minutes=3),"expires":now+timedelta(hours=23)})).mappings().one()
    order={"id":ref,"type":"unique","paymentMethod":"pix","offer_type":"main",
           "offer":{"id":"synthetic-pix-offer"},"amount":"29.90","currency":"BRL",
           "sck":token,"status":"paid","paidAt":now.isoformat(),
           "pix":{"qrCode":"synthetic-qr-"*10,"expirationDate":(now+timedelta(minutes=12)).isoformat()}}
    event={"event_type":"purchase_approved","external_order_id":ref,
           "external_offer_id":record["offer_id"],"external_subscription_id":None,
           "callback_hash":record["callback_hash"],"amount_cents":2990,"currency":"BRL"}
    c.orders[ref]=order
    return record,order,event


async def access(c):
    async with c.engine.connect() as db:
        return (await db.execute(text("SELECT * FROM orion_prepaid_pix_access WHERE tenant_id=:tenant"),{"tenant":c.tenant})).mappings().first()


@pytest.mark.asyncio
async def test_database_mapping_is_accepted_and_parallel_approval_grants_exactly_one_period(pix_db):
    c=pix_db;record,order,event=await purchase(c)
    assert reconcile.authenticated_pix_order(event,order,record)
    outcomes=await asyncio.gather(*(reconcile.reconcile_prepaid_receipt(event,order) for _ in range(8)))
    assert outcomes.count("activated")==1 and outcomes.count("already_verified")==7
    grant=await access(c)
    assert grant["paid_until"]==datetime.fromisoformat(order["paidAt"])+timedelta(days=30)
    assert (await api.pix_status(c.tenant,record["id"],c.actor))["status"]=="active"
    assert await api.pix_current(c.tenant,c.actor)=={"status":"none"}


@pytest.mark.asyncio
async def test_renewal_stacks_paid_days_and_refund_restores_previous_remaining_period(pix_db):
    c=pix_db;r1,o1,e1=await purchase(c)
    assert await reconcile.reconcile_prepaid_receipt(e1,o1)=="activated"
    previous=(await access(c))["paid_until"]
    r2,o2,e2=await purchase(c)
    assert await reconcile.reconcile_prepaid_receipt(e2,o2)=="activated"
    assert (await access(c))["paid_until"]==previous+timedelta(days=30)
    # An older refund cannot silently revoke the newer purchase's paid days.
    assert await reconcile.reconcile_prepaid_reversal({**e1,"event_type":"refund"},{**o1,"status":"refunded"})=="manual_review"
    assert (await access(c))["paid_until"]==previous+timedelta(days=30)
    assert await reconcile.reconcile_prepaid_reversal({**e2,"event_type":"refund"},{**o2,"status":"refunded"})=="reconciled"
    assert (await access(c))["paid_until"]==previous
    assert await reconcile.reconcile_prepaid_reversal({**e2,"event_type":"refund"},{**o2,"status":"refunded"})=="already_verified"


@pytest.mark.asyncio
async def test_refund_before_approval_closes_attempt_without_ever_granting_access(pix_db):
    c=pix_db;record,order,event=await purchase(c)
    assert await reconcile.reconcile_prepaid_reversal({**event,"event_type":"refund"},{**order,"status":"refunded"})=="reconciled"
    assert await access(c) is None
    assert await reconcile.reconcile_prepaid_receipt(event,order)=="manual_review"
    assert (await api.pix_status(c.tenant,record["id"],c.actor))["status"]=="refunded"
    assert await api.pix_current(c.tenant,c.actor)=={"status":"none"}


@pytest.mark.asyncio
async def test_ambiguous_post_can_recover_only_after_exact_known_order_proof(pix_db):
    c=pix_db;_,order,event=await purchase(c,state="needs_review")
    assert await reconcile.reconcile_prepaid_receipt(event,order)=="activated"
    assert await access(c) is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value",[("currency","USD"),("amount","89.90"),
    ("offer",{"id":"foreign-offer"}),("offer",{"id":None}),("sck","foreign-token"),
    ("type","subscription"),("paymentMethod","pix_auto"),("subscription","foreign-sub")])
async def test_divergent_provider_proof_never_grants_access(pix_db,field,value):
    c=pix_db;_,order,event=await purchase(c)
    assert await reconcile.reconcile_prepaid_receipt(event,{**order,field:value})=="manual_review"
    assert await access(c) is None


@pytest.mark.asyncio
async def test_reopen_recovers_same_qr_and_frozen_price_without_creating_payment(pix_db):
    c=pix_db;record,order,_=await purchase(c)
    order["status"]="waiting_payment"
    for _ in range(2):
        resumed=await api.pix_current(c.tenant,c.actor)
        assert resumed["intent_id"]==str(record["id"]) and resumed["monthly_cents"]==2990
        assert resumed["pix_code"]==order["pix"]["qrCode"]
    assert c.reads==[order["id"],order["id"]]
    c.orders[order["id"]]={**order,"offer":{"id":"foreign-offer"}}
    assert (await api.pix_current(c.tenant,c.actor))["status"]=="review_required"


@pytest.mark.asyncio
async def test_expired_qr_and_paid_unreconciled_order_have_no_payable_qr(pix_db):
    c=pix_db;record,order,_=await purchase(c)
    assert "pix_code" not in await api.pix_current(c.tenant,c.actor)
    order["status"]="waiting_payment"
    order["pix"]["expirationDate"]=(datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat()
    result=await api.pix_current(c.tenant,c.actor)
    assert result["status"]=="pending_confirmation" and "pix_code" not in result


@pytest.mark.asyncio
async def test_price_change_is_rejected_before_provider_read_or_local_reservation(pix_db,monkeypatch):
    c=pix_db
    async def prohibited(*_):pytest.fail("No provider call after stale price consent")
    monkeypatch.setattr(api,"verified_pix_catalog",prohibited)
    payload=api.PixPurchase(plan_code="essencial",consent_one_time=True,expected_cents=2991,
        payer={"name":"Pessoa Sintética","email":"payer@example.test","phone":"67999999901",
               "doc_type":"cpf","doc_number":"11144477735","fingerprint":"synthetic-local-fingerprint"})
    with pytest.raises(HTTPException) as error:await api.create_pix_charge(c.tenant,payload,c.actor)
    assert error.value.status_code==409
    async with c.engine.connect() as db:
        assert (await db.execute(text("SELECT count(*) FROM orion_prepaid_pix_orders WHERE tenant_id=:tenant"),{"tenant":c.tenant})).scalar_one()==0


@pytest.mark.asyncio
async def test_owner_transfer_blocks_first_activation_and_exposes_no_previous_payer_qr(pix_db):
    c=pix_db;record,order,event=await purchase(c)
    async with c.engine.begin() as db:
        await db.execute(text("UPDATE tenant_memberships SET active=false WHERE tenant_id=:tenant"),{"tenant":c.tenant})
        await db.execute(text("UPDATE tenant_memberships SET active=false WHERE tenant_id=:tenant"),{"tenant":c.other})
        await db.execute(text("INSERT INTO tenant_memberships(tenant_id,account_id,role,active) VALUES(:tenant,:account,'owner',true)"),{"tenant":c.tenant,"account":c.second})
    assert await reconcile.reconcile_prepaid_receipt(event,order)=="manual_review"
    assert await access(c) is None
    result=await api.pix_current(c.tenant,Actor("member",c.tenant,c.second,"owner"))
    assert result["status"]=="review_required" and "pix_code" not in result and c.reads==[]
    with pytest.raises(HTTPException) as exc:await api.pix_current(c.tenant,c.actor)
    assert exc.value.status_code in {403,404}


@pytest.mark.asyncio
async def test_known_provider_recovery_is_bounded_fair_and_does_not_create_payments(pix_db):
    c=pix_db;record,order,event=await purchase(c)
    class Provider:
        async def get_order(self,ref):
            assert ref==order["id"]
            return order
    receipts=await recover_prepaid_provider_receipts(Provider(),limit=1)
    assert len(receipts)==1 and receipts[0]["order_id"]==order["id"]
    assert await recover_prepaid_provider_receipts(Provider(),limit=1)==[]
    assert await access(c) is None  # Polling enqueues proof; it is not a grant.
    assert await reconcile.reconcile_prepaid_receipt(event,order)=="activated"
    assert (await access(c))["paid_until"]==datetime.fromisoformat(order["paidAt"])+timedelta(days=30)


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario",["exact","no_match","ambiguous","incomplete","foreign_offer"])
async def test_unknown_timeout_recovery_needs_unique_exact_server_tracking_proof(pix_db,scenario):
    c=pix_db;record,order,event=await purchase(c,state="needs_review",external=False)
    class Provider:
        async def _get(self,path,**params):
            assert path=="/orders/" and "sck" not in params and params["limit"]==100
            assert params["offer"]==record["offer_id"]
            if scenario=="no_match":return {"results":[{**order,"sck":"foreign"}],"next":None}
            if scenario=="ambiguous":return {"results":[order,{**order,"id":"synthetic-second"}],"next":None}
            if scenario=="foreign_offer":return {"results":[{**order,"offer":{"id":"foreign"}}],"next":None}
            return {"results":[order],"next":"more" if scenario=="incomplete" else None}
        async def get_order(self,ref):
            assert scenario=="exact" and ref==order["id"]
            return order
    receipts=await recover_prepaid_provider_receipts(Provider(),limit=1)
    async with c.engine.connect() as db:
        found=(await db.execute(text("SELECT state,external_order_id FROM orion_prepaid_pix_orders WHERE id=:id"),{"id":record["id"]})).one()
    if scenario=="exact":
        assert len(receipts)==1 and tuple(found)==("created",order["id"])
        assert await reconcile.reconcile_prepaid_receipt(event,order)=="activated"
    else:
        assert receipts==[] and tuple(found)==("needs_review",None)
        assert await access(c) is None


@pytest.mark.asyncio
async def test_known_order_id_cannot_be_replaced_by_another_id_in_provider_get(pix_db):
    c=pix_db;_,order,_=await purchase(c)
    class Provider:
        async def get_order(self,ref):return {**order,"id":"synthetic-foreign-order"}
    assert await recover_prepaid_provider_receipts(Provider(),limit=1)==[]
    assert await access(c) is None


@pytest.mark.asyncio
async def test_late_post_response_cannot_replace_recovered_external_order_id(pix_db):
    c=pix_db;record,order,_=await purchase(c)
    assert not await api.set_attempt(record["id"],"created","synthetic-foreign-order")
    async with c.engine.connect() as db:
        actual=(await db.execute(text("SELECT state,external_order_id FROM orion_prepaid_pix_orders WHERE id=:id"),{"id":record["id"]})).one()
    assert tuple(actual)==("needs_review",order["id"])


@pytest.mark.asyncio
async def test_get_resume_rechecks_membership_after_provider_await(pix_db,monkeypatch):
    c=pix_db;_,order,_=await purchase(c)
    order["status"]="waiting_payment"
    class Provider:
        async def get_order(self,ref):
            async with c.engine.begin() as db:
                await db.execute(text("UPDATE tenant_memberships SET active=false WHERE tenant_id=:tenant"),{"tenant":c.tenant})
            return order
    monkeypatch.setattr(api,"cached_cakto_client",Provider)
    with pytest.raises(HTTPException) as exc:await api.pix_current(c.tenant,c.actor)
    assert exc.value.status_code==403


@pytest.mark.asyncio
async def test_get_resume_rechecks_terminal_payment_after_provider_await(pix_db,monkeypatch):
    c=pix_db;_,order,event=await purchase(c)
    class Provider:
        async def get_order(self,ref):
            assert await reconcile.reconcile_prepaid_receipt(event,order)=="activated"
            return {**order,"status":"waiting_payment"}
    monkeypatch.setattr(api,"cached_cakto_client",Provider)
    resumed=await api.pix_current(c.tenant,c.actor)
    assert resumed["status"]=="active" and "pix_code" not in resumed


@pytest.mark.asyncio
async def test_private_status_responses_are_not_cacheable(pix_db):
    c=pix_db;record,_,_=await purchase(c)
    response=await c.client.get(f"/api/v1/tenants/{c.tenant}/management/plan-pix-status",params={"intent_id":str(record["id"])})
    assert response.status_code==200 and "no-store" in response.headers["cache-control"]
    assert response.headers["pragma"]=="no-cache"


def charge_payload():
    return api.PixPurchase(plan_code="essencial",consent_one_time=True,expected_cents=2990,
        payer={"name":"Pessoa Sintética","email":"payer@example.test","phone":"67999999901",
               "doc_type":"cpf","doc_number":"11144477735","fingerprint":"synthetic-local-fingerprint"})


def fake_creation(monkeypatch,c,*,failure=None,on_token=None,on_post=None):
    c.posts=[]
    async def catalog(_):return {"essencial":{"id":"synthetic-pix-offer"}}
    class Provider:
        async def token(self):
            if on_token:await on_token()
            return "synthetic-unused-oauth"
    class Http:
        def __init__(self,**_):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*_):pass
        async def post(self,url,**kw):
            assert url==api.BASE+"/payments/"
            assert kw["json"]["paymentMethod"]=="pix"
            assert kw["headers"]["X-Idempotency-Key"]
            c.posts.append(kw)
            if on_post:await on_post()
            request=httpx.Request("POST",url)
            if failure=="timeout":raise httpx.ReadTimeout("synthetic",request=request)
            if isinstance(failure,int):return httpx.Response(failure,json={"detail":"private-provider-message"},request=request)
            now=datetime.now(timezone.utc)
            return httpx.Response(201,request=request,json={"id":"synthetic-created-order",
                "paymentMethod":"pix","status":"waiting_payment","amount":"29.90",
                "offer":{"id":"synthetic-pix-offer"},"type":"unique","currency":"BRL",
                "pix":{"qrCode":"synthetic-qr-"*10,"expirationDate":(now+timedelta(minutes=15)).isoformat()}})
    monkeypatch.setattr(api,"cached_cakto_client",Provider)
    monkeypatch.setattr(api,"verified_pix_catalog",catalog)
    monkeypatch.setattr(api.httpx,"AsyncClient",Http)
    monkeypatch.setenv("INTEGRATION_FERNET_KEY",Fernet.generate_key().decode())


@pytest.mark.asyncio
async def test_parallel_double_click_creates_only_one_durable_intent_and_one_provider_post(pix_db,monkeypatch):
    c=pix_db;fake_creation(monkeypatch,c)
    answers=await asyncio.gather(*(api.create_pix_charge(c.tenant,charge_payload(),c.actor) for _ in range(2)),return_exceptions=True)
    assert len(c.posts)==1
    assert sum(isinstance(answer,dict) and answer["status"]=="pending_confirmation" for answer in answers)==1
    rejected=[answer for answer in answers if isinstance(answer,HTTPException)]
    assert len(rejected)==1 and rejected[0].status_code==409
    async with c.engine.connect() as db:
        rows=(await db.execute(text("SELECT state,external_order_id FROM orion_prepaid_pix_orders WHERE tenant_id=:tenant"),{"tenant":c.tenant})).all()
    assert [tuple(row) for row in rows]==[("created","synthetic-created-order")]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure,expected",[(400,"declined"),(401,"declined"),(403,"declined"),
    (404,"declined"),(422,"declined"),(409,"needs_review"),(500,"needs_review"),(503,"needs_review"),("timeout","needs_review")])
async def test_provider_rejection_vs_uncertain_result_never_automatically_reposts(pix_db,monkeypatch,failure,expected):
    c=pix_db;fake_creation(monkeypatch,c,failure=failure)
    with pytest.raises(HTTPException) as exc:await api.create_pix_charge(c.tenant,charge_payload(),c.actor)
    assert "private-provider-message" not in str(exc.value.detail)
    assert len(c.posts)==1 and await access(c) is None
    async with c.engine.connect() as db:
        states=(await db.execute(text("SELECT state FROM orion_prepaid_pix_orders WHERE tenant_id=:tenant"),{"tenant":c.tenant})).scalars().all()
    assert states==[expected]
    if expected=="needs_review":
        assert (await api.pix_current(c.tenant,c.actor))["status"]=="review_required"
        with pytest.raises(HTTPException) as duplicate:await api.create_pix_charge(c.tenant,charge_payload(),c.actor)
        assert duplicate.value.status_code==409 and len(c.posts)==1


@pytest.mark.asyncio
async def test_owner_revoked_after_reservation_cannot_start_financial_post(pix_db,monkeypatch):
    c=pix_db
    async def revoke():
        async with c.engine.begin() as db:
            await db.execute(text("UPDATE tenant_memberships SET active=false WHERE tenant_id=:tenant"),{"tenant":c.tenant})
    fake_creation(monkeypatch,c,on_token=revoke)
    with pytest.raises(HTTPException) as exc:await api.create_pix_charge(c.tenant,charge_payload(),c.actor)
    assert exc.value.status_code==403 and c.posts==[]
    async with c.engine.connect() as db:
        assert (await db.execute(text("SELECT state FROM orion_prepaid_pix_orders WHERE tenant_id=:tenant"),{"tenant":c.tenant})).scalar_one()=="needs_review"


@pytest.mark.asyncio
async def test_administrative_plan_change_waits_for_started_provider_post(pix_db,monkeypatch):
    from test_manual_plan_permissions_postgres import assignment
    c=pix_db;entered=asyncio.Event();release=asyncio.Event()
    async def wait_post():
        entered.set()
        await asyncio.wait_for(release.wait(),10)
    fake_creation(monkeypatch,c,on_post=wait_post)
    payment=asyncio.create_task(api.create_pix_charge(c.tenant,charge_payload(),c.actor))
    change=None
    try:
        await asyncio.wait_for(entered.wait(),10)
        change=asyncio.create_task(c.client.put(c.path,json=await assignment(c)))
        await asyncio.sleep(.1)
        assert not change.done()
    finally:
        release.set()
    result=await asyncio.wait_for(payment,10)
    granted=await asyncio.wait_for(change,10)
    assert result["status"]=="pending_confirmation" and len(c.posts)==1
    assert granted.status_code==200 and granted.json()["effective_plan"]["source"]=="manual"


@pytest.mark.asyncio
async def test_real_documented_get_without_pix_or_offer_resumes_encrypted_creation_qr(pix_db,monkeypatch):
    c=pix_db;fake_creation(monkeypatch,c)
    created=await api.create_pix_charge(c.tenant,charge_payload(),c.actor)
    bank={"id":"synthetic-created-order","type":"unique","offer_type":"main",
          "paymentMethod":"pix","currency":"BRL","amount":"29.90","status":"waiting_payment"}
    class Provider:
        async def get_order(self,ref):
            assert ref==bank["id"]
            return bank
    monkeypatch.setattr(api,"cached_cakto_client",Provider)
    resumed=await api.pix_current(c.tenant,c.actor)
    assert resumed["pix_code"]==created["pix_code"] and resumed["intent_id"]==created["intent_id"]
    assert len(c.posts)==1
    async with c.engine.begin() as db:
        saved=(await db.execute(text("SELECT pix_code_enc,pix_expires_at FROM orion_prepaid_pix_orders WHERE id=:id"),{"id":created["intent_id"]})).one()
        assert created["pix_code"] not in saved.pix_code_enc and saved.pix_expires_at is not None
        await db.execute(text("UPDATE orion_prepaid_pix_orders SET pix_code_enc=:bad WHERE id=:id"),{"id":created["intent_id"],"bad":"x"*150})
    assert "pix_code" not in await api.pix_current(c.tenant,c.actor)


@pytest.mark.asyncio
async def test_documented_paid_get_without_offer_can_settle_exact_persisted_post(pix_db,monkeypatch):
    c=pix_db;fake_creation(monkeypatch,c)
    created=await api.create_pix_charge(c.tenant,charge_payload(),c.actor)
    async with c.engine.connect() as db:
        record=(await db.execute(text("SELECT * FROM orion_prepaid_pix_orders WHERE id=:id"),{"id":created["intent_id"]})).mappings().one()
    order={"id":record["external_order_id"],"type":"unique","offer_type":"main",
           "status":"paid","paymentMethod":"pix","currency":"BRL","amount":"29.90",
           "paidAt":datetime.now(timezone.utc).isoformat()}
    event={"event_type":"purchase_approved","external_order_id":order["id"],
           "external_offer_id":record["offer_id"],"external_subscription_id":None,
           "callback_hash":record["callback_hash"],"amount_cents":2990,"currency":"BRL"}
    assert await reconcile.reconcile_prepaid_receipt(event,order)=="activated"
    assert await access(c) is not None and len(c.posts)==1


@pytest.mark.asyncio
async def test_worker_recovers_missing_webhook_then_late_signed_delivery_does_not_double_grant(pix_db,monkeypatch):
    from app import cakto_api as cakto
    c=pix_db;record,order,event=await purchase(c)
    order.pop("offer")  # Actual documented GET schema, not a richer fake.
    class Provider:
        configured=True
        async def get_order(self,ref):
            assert ref==order["id"]
            return order
    monkeypatch.setattr(cakto,"cached_cakto_client",Provider)
    monkeypatch.setenv("ORION_CAKTO_LIFECYCLE_ENABLED","0")
    secret="synthetic-prepaid-hmac-secret-only-in-isolated-tests"
    monkeypatch.setenv("CAKTO_PIX_WEBHOOK_SECRET",secret)
    c.api.include_router(cakto.router)
    assert await cakto.scan_pending_cakto({})==1
    first=(await access(c))["paid_until"]
    async with c.engine.connect() as db:
        receipts=(await db.execute(text("SELECT receipt_origin,verification_state FROM cakto_webhook_events WHERE external_order_id=:order"),{"order":order["id"]})).all()
    assert [tuple(r) for r in receipts]==[("provider_read","verified")]
    payload={"secret":secret,"event":"purchase_approved","data":{
        **order,"offer":{"id":record["offer_id"]},"customer":{"email":"private@example.test"}}}
    raw=json.dumps(payload,separators=(",",":")).encode();stamp=str(int(time.time()))
    signature="v1="+hmac.new(secret.encode(),stamp.encode()+b"."+raw,hashlib.sha256).hexdigest()
    delivered=await c.client.post("/webhooks/cakto",content=raw,
        headers={"X-Cakto-Timestamp":stamp,"X-Cakto-Signature":signature})
    assert delivered.status_code==200,delivered.text
    assert await cakto.scan_pending_cakto({})==1
    assert (await access(c))["paid_until"]==first
    async with c.engine.connect() as db:
        receipts=(await db.execute(text("SELECT receipt_origin,verification_state FROM cakto_webhook_events WHERE external_order_id=:order"),{"order":order["id"]})).all()
    assert {tuple(r) for r in receipts}=={("provider_read","verified"),("webhook_hmac","verified")}


@pytest.mark.asyncio
async def test_failed_qr_storage_preserves_verified_external_id_without_reposting(pix_db,monkeypatch):
    c=pix_db;fake_creation(monkeypatch,c)
    original=api.set_attempt
    async def fail_qr_save(identity,state,external_id=None,**kwargs):
        if state=="created":raise SQLAlchemyError("synthetic-database-failure")
        return await original(identity,state,external_id,**kwargs)
    monkeypatch.setattr(api,"set_attempt",fail_qr_save)
    with pytest.raises(HTTPException) as exc:await api.create_pix_charge(c.tenant,charge_payload(),c.actor)
    assert exc.value.status_code==503 and len(c.posts)==1
    async with c.engine.connect() as db:
        actual=(await db.execute(text("SELECT state,external_order_id FROM orion_prepaid_pix_orders WHERE tenant_id=:tenant"),{"tenant":c.tenant})).one()
    assert tuple(actual)==("needs_review","synthetic-created-order")
    assert await access(c) is None
