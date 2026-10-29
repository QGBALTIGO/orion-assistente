"""New checkout admission under manual priority; no real provider or database."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

from fastapi import HTTPException
import pytest

from app import plan_management as plans, orion_direct_checkout as direct
from app.catalog_api import Actor


class Result:
    def __init__(self,value=None): self.value=value
    def scalar_one_or_none(self): return self.value
    def mappings(self): return self
    def first(self): return self.value


class Database:
    def __init__(self):
        self.tenant,self.account,self.intent_id=uuid4(),uuid4(),uuid4()
        self.manual=False;self.transaction=False;self.shared=False;self.checked_owner=False
        self.provider_posts=0;self.provider_reads=0;self.attempt=None;self.calls=[]
        self.on_offers=None
    async def __aenter__(self): return self
    async def __aexit__(self,*_): self.transaction=False;self.shared=False
    def begin(self): return Transaction(self)
    async def execute(self,query,params=None):
        sql=str(query);self.calls.append((sql,params))
        if "pg_advisory_xact_lock" in sql: return Result()
        if "SELECT a.state,a.method" in sql:
            return Result({"state":self.attempt["state"],"method":"pix","intent_state":"pending"})
        if "FROM orion_cakto_checkout_intents" in sql:
            return Result({"id":self.intent_id,"account_id":self.account,"tenant_id":self.tenant,"plan_code":"essencial",
                "offer_id":"synthetic-offer","expected_cents":2990,"state":"pending","expires_at":datetime.now(timezone.utc)+timedelta(days=1)})
        if "FROM subscriptions" in sql: return Result()
        if "SELECT id FROM orion_direct_payment_attempts" in sql:
            return Result(self.attempt["id"] if self.attempt else None)
        if "INSERT INTO orion_direct_payment_attempts" in sql:
            self.attempt={"id":params["id"],"state":"reserved","external_order_id":None};return Result()
        if "UPDATE orion_direct_payment_attempts" in sql:
            if self.attempt["state"]=="reserved":
                self.attempt.update(state=params["state"],external_order_id=params["order_id"])
            return Result()
        raise AssertionError("Unexpected synthetic SQL: "+sql)


class Transaction:
    def __init__(self,db): self.db=db
    async def __aenter__(self): self.db.transaction=True;return self.db
    async def __aexit__(self,*_): self.db.transaction=False;self.db.shared=False


@pytest.fixture
def checkout(monkeypatch):
    db=Database()
    async def state(session,tenant,lock="shared"):
        assert session is db and tenant==db.tenant and lock=="shared"
        db.shared=True
        return {"manual_assignment":{"plan_code":"essencial","active":True} if db.manual else None}
    async def access(*_): return SimpleNamespace(role="owner",account_id=db.account)
    async def owner(*_): return db.account
    async def ensure(session,tenant,account):
        assert session is db and tenant==db.tenant and account==db.account
        db.checked_owner=True
    async def webhooks(*_): pass
    class ReadOnlyCakto:
        async def list_offers(self):
            db.provider_reads+=1
            if db.on_offers: db.on_offers()
            return [{"id":"synthetic-offer","name":"Orion Essencial","price":29.9,
                "status":"active","type":"subscription","recurrence_period":30}]
        async def token(self): return "synthetic-local-token"
    class Http:
        def __init__(self,**kwargs):
            assert kwargs=={"timeout":25,"follow_redirects":False}
        async def __aenter__(self): return self
        async def __aexit__(self,*_): pass
        async def post(self,url,**kwargs):
            assert db.transaction and db.shared and db.checked_owner
            assert url.endswith("/payments/") and kwargs["headers"]["X-Idempotency-Key"]==str(db.attempt["id"])
            db.provider_posts+=1
            value={"id":"synthetic-order","paymentMethod":"pix_auto","offer":{"id":"synthetic-offer"},"amount":29.9,
                "status":"waiting_payment","pix":{"qrCode":"synthetic-pix-data-"*6,"user_journey":"JORNADA_1"}}
            return SimpleNamespace(raise_for_status=lambda:None,json=lambda:value)
    monkeypatch.setattr(plans,"plan_state",state)
    monkeypatch.setattr(plans,"resolve_site_access",access)
    monkeypatch.setattr(plans,"_ensure_owner",ensure)
    monkeypatch.setattr(direct,"_ensure_owner",ensure)
    monkeypatch.setattr(plans,"support_session_factory",lambda:lambda:db)
    monkeypatch.setattr(direct,"support_session_factory",lambda:lambda:db)
    monkeypatch.setattr(plans,"CaktoClient",ReadOnlyCakto)
    monkeypatch.setattr(direct,"CaktoClient",ReadOnlyCakto)
    monkeypatch.setattr(direct,"require_enabled",lambda:None)
    monkeypatch.setattr(direct,"owner",owner)
    monkeypatch.setattr(direct,"require_verified_webhooks",webhooks)
    monkeypatch.setattr(direct.httpx,"AsyncClient",Http)
    monkeypatch.setenv("CAKTO_BILLING_LIVE_ENABLED","1")
    monkeypatch.setenv("INTEGRATION_FERNET_KEY","synthetic-hmac-only-checkout-key-"+"x"*32)
    return db


def charge(db):
    return direct.DirectCharge(intent_id=db.intent_id,method="pix",confirmed_monthly=True,consent_to_processor=True,
        payer={"name":"Pessoa Sintética","email":"payer@example.test","phone":"67999999901","doc_type":"cpf",
               "doc_number":"11144477735","fingerprint":"synthetic-local-fingerprint"})


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["hosted","prepare","charge"])
async def test_manual_active_blocks_new_checkout_before_any_provider_call_or_reservation(checkout,operation):
    db=checkout;db.manual=True
    with pytest.raises(HTTPException) as caught:
        if operation=="hosted": await plans.start_plan_checkout(db.tenant,plans.CheckoutChoice(plan_code="essencial"),Actor("member",account_id=db.account))
        elif operation=="prepare": await direct.direct_prepare(db.tenant,direct.DirectPrepare(plan_code="essencial"),Actor("member",account_id=db.account))
        else: await direct.direct_charge(db.tenant,charge(db),Actor("member",account_id=db.account))
    assert caught.value.status_code==409 and caught.value.detail["code"]=="MANUAL_PLAN_ACTIVE"
    assert db.provider_reads==db.provider_posts==0 and db.attempt is None


@pytest.mark.asyncio
async def test_manual_change_during_hosted_offer_read_is_rechecked_before_creating_an_intent(checkout):
    db=checkout;db.on_offers=lambda:setattr(db,"manual",True)
    with pytest.raises(HTTPException) as caught:
        await plans.start_plan_checkout(db.tenant,plans.CheckoutChoice(plan_code="essencial"),Actor("member",account_id=db.account))
    assert caught.value.detail["code"]=="MANUAL_PLAN_ACTIVE"
    assert not any("INSERT INTO orion_cakto_checkout_intents" in sql for sql,_ in db.calls)


@pytest.mark.asyncio
async def test_manual_arriving_after_reservation_prevents_provider_post_and_preserves_reviewable_receipt(checkout):
    db=checkout;db.on_offers=lambda:setattr(db,"manual",True)
    with pytest.raises(HTTPException) as caught:
        await direct.direct_charge(db.tenant,charge(db),Actor("member",account_id=db.account))
    assert caught.value.detail["code"]=="MANUAL_PLAN_ACTIVE" and db.provider_posts==0
    assert db.attempt["state"]=="needs_review" and db.attempt["external_order_id"] is None
    assert (await direct.direct_status(db.tenant,db.intent_id,Actor("member",account_id=db.account)))["status"]=="review_required"


@pytest.mark.asyncio
async def test_provider_start_holds_shared_permission_until_bounded_response_and_attempt_remains_single(checkout):
    db=checkout
    response=await direct.direct_charge(db.tenant,charge(db),Actor("member",account_id=db.account))
    assert response["status"]=="pending_confirmation" and db.provider_posts==1
    assert db.attempt["state"]=="created" and not db.transaction and not db.shared
    with pytest.raises(HTTPException) as caught:
        await direct.direct_charge(db.tenant,charge(db),Actor("member",account_id=db.account))
    assert caught.value.status_code==409 and db.provider_posts==1


@pytest.mark.asyncio
async def test_manual_does_not_disable_config_or_status_reads(checkout,monkeypatch):
    db=checkout;db.manual=True
    monkeypatch.setattr(direct,"checkout_enabled",lambda:False)
    assert (await direct.direct_config(db.tenant,Actor("member",account_id=db.account)))["available"] is False
    db.attempt={"id":uuid4(),"state":"created"}
    assert (await direct.direct_status(db.tenant,db.intent_id,Actor("member",account_id=db.account)))["status"]=="pending"
    assert db.provider_posts==0


@pytest.mark.asyncio
async def test_expired_or_absent_manual_assignment_does_not_block_admission(checkout,monkeypatch):
    db=checkout
    async def state(*_,**kwargs): return {"manual_assignment":{"plan_code":"elite","active":False}}
    monkeypatch.setattr(plans,"plan_state",state)
    await plans.check_new_checkout_access(db.tenant,db.account)
