"""Contract tests for one-time Pix plans; no live payments or customers."""
import hashlib
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.prepaid_pix_api import (
    PIX_NAMES, PixPurchase, cents, payment_request, prepaid_offers,
    verified_pix_creation, verified_pix_catalog, pix_environment_ready,
)
from app.prepaid_pix_reconcile import authenticated_pix_order, verified_paid_at
from app.plan_catalog import CODES
from app.plan_permissions import derive_plan

def offers():
    return [
        {"id":"unique-"+code,"name":PIX_NAMES[code],
         "type":"unique","status":"active","product":{"id":"product-prepaid"},
         "price":str(plan["price_cents"]/100)}
        for code,plan in CODES.items()
    ]

def payer():
    from app.orion_direct_checkout import Payer
    return Payer(name="Pessoa Sintética",email="synthetic@example.test",
        phone="(67) 99999-1111",doc_type="cpf",doc_number="52998224725",
        fingerprint="orion-synthetic-abcdef123456")

def test_requires_three_exact_unique_offers_and_catalog_prices():
    matched=prepaid_offers(offers())
    assert set(matched)==set(CODES)
    assert [cents(matched[k]["price"]) for k in CODES]==[2990,8990,13990]
    assert all(matched[k]["type"]=="unique" for k in CODES)

@pytest.mark.parametrize("field,value",[
    ("name","Orion Essencial"),("type","subscription"),
    ("status","inactive"),("price","29.91"),("product",None)
])
def test_recurring_wrong_price_and_non_pix_product_are_never_matched(field,value):
    entries=offers()
    entries[0][field]=value
    with pytest.raises(HTTPException) as exc:prepaid_offers(entries)
    assert exc.value.status_code==409

def test_duplicate_offers_fail_closed():
    with pytest.raises(HTTPException):prepaid_offers(offers()+[offers()[0]])

def test_money_is_decimal_exact_not_float_rounded():
    assert cents("29.90")==2990
    assert cents("29.901") is None
    assert cents("NaN") is None
    assert cents(None) is None

def test_pix_provider_payload_is_one_time_only():
    req=payment_request("unique-essencial",payer(),"synthetic-confirmation-token")
    assert req["paymentMethod"]=="pix"
    assert req["items"]==[{"offerId":"unique-essencial","quantity":1,"offerType":"main"}]
    assert req["pixExpiresIn"]==900
    assert req["metadata"]["sck"]=="synthetic-confirmation-token"
    assert "recurrence_period" not in str(req).lower()
    assert "card" not in str(req).lower()
    assert req["customer"]["docNumber"]=="52998224725"

def test_single_payment_consent_is_required():
    data={"plan_code":"essencial","payer":payer(),"consent_one_time":False}
    with pytest.raises(ValueError):PixPurchase.model_validate(data)
    assert PixPurchase.model_validate({**data,"consent_one_time":True}).plan_code=="essencial"

@pytest.mark.parametrize("mutation",[
    {"paymentMethod":"pix_auto"},{"amount":"30.00"},
    {"status":"paid"},{"offer":{"id":"wrong-offer"}},
    {"pix":None},
    {"pix":{"qrCode":"too short","user_journey":None}},
])
def test_unverified_pix_creation_fails_before_displaying_qr(mutation):
    item={"id":"synthetic-order-id","paymentMethod":"pix",
          "status":"waiting_payment","amount":"29.90",
          "offer":{"id":"unique-essencial"},
          "pix":{"qrCode":"0"*100,"user_journey":None}}
    item.update(mutation)
    assert not verified_pix_creation(item,"unique-essencial",2990)

def test_valid_pix_qr_creation_is_not_approval():
    item={"id":"synthetic-order-id","paymentMethod":"pix",
          "status":"waiting_payment","amount":"29.90",
          "offer":{"id":"unique-essencial"},
          "pix":{"qrCode":"0"*100,"user_journey":None}}
    assert verified_pix_creation(item,"unique-essencial",2990)

def fake_receipt():
    token="synthetic-hmac-owner-proof"
    return (
        {"event_type":"purchase_approved","external_order_id":"synthetic-order",
         "external_offer_id":"unique-essencial","currency":"BRL",
         "amount_cents":2990,"external_subscription_id":None,
         "callback_hash":hashlib.sha256(token.encode()).hexdigest()},
        {"id":"synthetic-order","status":"paid","type":"unique",
         "offer_type":"main","paymentMethod":"pix",
         "amount":"29.90","currency":"BRL",
         "offer":{"id":"unique-essencial"},"sck":token},
        {"external_order_id":"synthetic-order","offer_id":"unique-essencial",
         "callback_hash":hashlib.sha256(token.encode()).hexdigest(),
         "expected_cents":2990}
    )

def test_signed_receipt_plus_provider_get_and_matching_owner_intent_are_required():
    assert authenticated_pix_order(*fake_receipt())

@pytest.mark.parametrize("who,value",[
    ("order.status","pending"),("order.paymentMethod","pix_auto"),
    ("order.type","subscription"),("order.amount","89.90"),
    ("order.sck","forged"),("event.external_offer_id","different"),
    ("event.callback_hash","000000"),("event.external_subscription_id","sub1"),
    ("record.expected_cents",8990),
])
def test_forged_or_recurring_orders_cannot_activate_prepaid_plan(who,value):
    data=list(fake_receipt()); key,field=who.split(".")
    data[{"event":0,"order":1,"record":2}[key]][field]=value
    assert not authenticated_pix_order(*data),who

def snapshot(*, now, prepaid=None,manual=None,commercial=None):
    return {"tenant_id":"00000000-0000-4000-8000-000000000100",
            "tenant_status":"active","active_professionals":0,
            "manual":manual,"commercial":commercial,"trial":None,"prepaid":prepaid}

def test_exact_30_days_access_expires_without_manual_action():
    now=datetime(2026,11,3,12,0,tzinfo=timezone.utc)
    paid=now-timedelta(days=29)
    grant={"plan_code":"essencial","paid_until":now+timedelta(days=1),
           "last_verified_payment_at":paid}
    result=derive_plan(snapshot(now=now,prepaid=grant),now=now)
    assert result["active"] is True and result["source"]=="pix_prepaid"
    assert "services.manage" in result["features"]
    assert result["valid_until"]==grant["paid_until"]
    later=derive_plan(snapshot(now=now,prepaid=grant),now=now+timedelta(days=2))
    assert later["active"] is False and later["status"]=="expired"
    assert "services.manage" not in later["features"]

def test_unverified_pix_never_grants_any_feature():
    now=datetime(2026,11,3,12,0,tzinfo=timezone.utc)
    grant={"plan_code":"elite","paid_until":now+timedelta(days=30),
           "last_verified_payment_at":None}
    state=derive_plan(snapshot(now=now,prepaid=grant),now=now)
    assert not state["active"]

def test_manual_plan_priority_is_unchanged():
    now=datetime(2026,11,3,12,0,tzinfo=timezone.utc)
    grant={"plan_code":"elite","paid_until":now+timedelta(days=30),
           "last_verified_payment_at":now}
    manual={"plan_code":"essencial","started_at":now-timedelta(days=1),"expires_at":None}
    state=derive_plan(snapshot(now=now,prepaid=grant,manual=manual),now=now)
    assert state["code"]=="essencial" and state["source"]=="manual"

def test_pix_feature_flag_and_secret_are_required(monkeypatch):
    for k in ("ORION_PIX_PREPAID_ENABLED","CAKTO_PIX_WEBHOOK_SECRET","CAKTO_CLIENT_ID","CAKTO_CLIENT_SECRET"):
        monkeypatch.delenv(k,raising=False)
    assert not pix_environment_ready()
    monkeypatch.setenv("ORION_PIX_PREPAID_ENABLED","1")
    monkeypatch.setenv("CAKTO_CLIENT_ID","synthetic")
    monkeypatch.setenv("CAKTO_CLIENT_SECRET","synthetic")
    monkeypatch.setenv("CAKTO_PIX_WEBHOOK_SECRET","short")
    assert not pix_environment_ready()
    monkeypatch.setenv("CAKTO_PIX_WEBHOOK_SECRET","temporary-synthetic-secret-at-least-32-chars")
    assert pix_environment_ready()

class FakeProvider:
    def __init__(self,wrong=False):
        self.wrong=wrong
    async def list_offers(self):return offers()
    async def token(self):return "synthetic-oauth"
    def granted_scopes(self,needed):return True
    async def _get(self,path,**kw):
        if path.startswith("/products/"):
            return {"id":"product-prepaid","type":"unique","status":"active",
                    "paymentMethods":["pix"]}
        if path=="/webhook/":
            secret="temporary-synthetic-secret-at-least-32-chars"
            return {"results":[{
                "status":"active","url":"https://orionagenda.pro/webhooks/cakto",
                "products":[{"id":"product-prepaid"}],
                "fields":{"secret":secret if not self.wrong else "wrong"},
                "events":[{"custom_id":x} for x in (
                    "purchase_approved","purchase_refused","refund","chargeback")]
            }],"next":None}
        raise AssertionError(path)

@pytest.mark.asyncio
async def test_provider_approved_pix_catalog_is_required(monkeypatch):
    monkeypatch.setenv("ORION_PIX_PREPAID_ENABLED","1")
    monkeypatch.setenv("CAKTO_PIX_WEBHOOK_SECRET","temporary-synthetic-secret-at-least-32-chars")
    monkeypatch.setenv("CAKTO_CLIENT_ID","synthetic")
    monkeypatch.setenv("CAKTO_CLIENT_SECRET","synthetic")
    assert set(await verified_pix_catalog(FakeProvider()))==set(CODES)
    with pytest.raises(HTTPException) as exc:await verified_pix_catalog(FakeProvider(wrong=True))
    assert exc.value.status_code==409


def test_prepaid_access_starts_at_verified_payment_not_webhook_arrival():
    now=datetime(2026,11,3,16,tzinfo=timezone.utc)
    created=now-timedelta(minutes=25)
    record={"created_at":created,"expires_at":created+timedelta(days=1)}
    paid=created+timedelta(minutes=12)
    assert verified_paid_at({"paidAt":paid.isoformat()},record,now)==paid
    assert verified_paid_at({"paidAt":(now+timedelta(days=2)).isoformat()},record,now) is None
    assert verified_paid_at({"paidAt":(created-timedelta(days=1)).isoformat()},record,now) is None
    assert verified_paid_at({"paidAt":"2026-11-03T15:35:00"},record,now) is None
    assert verified_paid_at({"paidAt":"not-a-date"},record,now) is None
    assert verified_paid_at({"status":"paid"},record,now) is None


def test_old_paid_order_cannot_be_used_to_create_fresh_thirty_days():
    now=datetime(2026,11,3,16,tzinfo=timezone.utc)
    record={"created_at":now-timedelta(days=33),
            "expires_at":now-timedelta(days=32)}
    assert verified_paid_at({"paidAt":now.isoformat()},record,now) is None
