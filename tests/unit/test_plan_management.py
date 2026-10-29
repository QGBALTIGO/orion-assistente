"""Synthetic plan and Cakto contracts, no real customers or payment requests."""
import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app import plan_management as plans
from app import cakto_api


def test_offered_plan_prices_and_staff_limits_never_drift():
    assert [(code,item["price_cents"],item["max_professionals"])
            for code,item in plans.CODES.items()] == [
       ("essencial",2990,2),("profissional",8990,6),("elite",13990,10)]
    assert "79.90" not in str(plans._catalog())
    assert list(plans._catalog()[1].values())[:2] == ["profissional", "Orion Profissional"]


@pytest.mark.parametrize("value,expected",[
    ("(67) 99999-1111","+5567999991111"),
    ("+55 (11) 98888-1234","+5511988881234"),
])
def test_trial_phone_formatter_accepts_brazilian_cellphones(value,expected):
    result=plans.TrialRequest(full_name="Pessoa Sintética",
                             company_name="Empresa Exemplo",whatsapp=value,privacy_ack=True)
    assert result.whatsapp==expected


@pytest.mark.parametrize("value",[
    "00000000000", "(00) 99999-1111","(67) 12345-1111",
    "(67) 89999-1111", "12345", "+1 (123) 123 1234",
    "<svg onload=alert(1)>",
])
def test_trial_rejects_invalid_whatsapp(value):
    with pytest.raises(ValidationError):
        plans.TrialRequest(full_name="Pessoa Sintética",
                           company_name="Empresa Exemplo",whatsapp=value,privacy_ack=True)


def test_trial_rejects_extra_fields_and_blank_or_unsafe_names():
    with pytest.raises(ValidationError):
        plans.TrialRequest(full_name="Correto Exemplo",company_name="Empresa",
                           whatsapp="(67) 99999-1111",privacy_ack=True,tenant_id=str(uuid4()))
    with pytest.raises(ValidationError):
        plans.TrialRequest(full_name="  ",company_name="Empresa",
                           whatsapp="(67) 99999-1111",privacy_ack=True)


def test_offer_requires_matching_name_cents_monthly_type_and_unique_match():
    offer={"id":"offer-fake", "name":"Orion Pro", "price":89.9,
           "status":"active","type":"subscription","recurrence_period":30}
    assert plans._exact_offer([offer],plans.CODES["profissional"])==offer
    for change in ({"price":79.9},{"status":"inactive"},
                   {"recurrence_period":365},{"type":"unique"},
                   {"name":"Orion Elite"},{"recurrence_period":0}):
        assert plans._exact_offer([{**offer,**change}],plans.CODES["profissional"]) is None
    assert plans._exact_offer([offer,dict(offer,id="second")],plans.CODES["profissional"]) is None


def test_cakto_callbacks_are_hashed_without_buyers_card_data_or_order_bump():
    payload={"event":"purchase_approved","data":[
        {"id":"fake-order","status":"paid","offer_type":"main",
         "offer":{"id":"fake-offer"},"sck":"callback_synthetic_012345",
         "amount":89.9,"currency":"BRL",
         "customer":{"email":"secret@example.test"},"card":{"lastDigits":"1234"}},
        {"id":"bump","status":"paid","offer_type":"orderbump",
         "sck":"callback_synthetic_012345"}]}
    rows=cakto_api.normalized_events(payload)
    assert len(rows)==1
    assert rows[0]["callback_hash"]==hashlib.sha256(b"callback_synthetic_012345").hexdigest()
    assert rows[0]["amount_cents"]==8990
    assert rows[0]["currency"]=="BRL"
    assert rows==cakto_api.normalized_events(payload)
    assert "secret@example.test" not in str(rows)
    assert "1234" not in str(rows)
    assert "callback_synthetic_012345" not in str(rows)


@pytest.mark.asyncio
async def test_unverified_payment_cannot_activate_tenant():
    assert await plans.apply_verified_cakto_payment(
        {"event_type":"purchase_approved","external_order_id":"fake-order"},
        {"id":"fake-order","status":"paid"}) == "unlinked"


@pytest.mark.asyncio
async def test_checkout_denied_when_billing_flag_is_disabled(monkeypatch):
    from app.catalog_api import Actor
    account_id=uuid4()
    tenant=uuid4()
    async def granted(*args,**kwargs):
        return SimpleNamespace(role="owner",account_id=account_id)
    monkeypatch.setattr(plans, "resolve_tenant_access", granted)
    monkeypatch.delenv("CAKTO_BILLING_LIVE_ENABLED",raising=False)
    with pytest.raises(HTTPException) as caught:
        await plans.start_plan_checkout(tenant,plans.CheckoutChoice(plan_code="profissional"),
                                        Actor("member",account_id=account_id))
    assert caught.value.status_code==409
    assert "homologação" in caught.value.detail


@pytest.mark.asyncio
async def test_professional_checkout_requires_exact_active_offer_even_when_flag_enabled(monkeypatch):
    from app.catalog_api import Actor
    account_id=uuid4()
    tenant=uuid4()
    async def granted(*args,**kwargs):
        return SimpleNamespace(role="owner",account_id=account_id)
    class ReadOnlyCakto:
        async def list_offers(self):
            return [{"id":"synthetic-offer","name":"Orion Pro","price":79.90,
                     "status":"active","type":"subscription","recurrence_period":30}]
    monkeypatch.setattr(plans,"resolve_tenant_access",granted)
    monkeypatch.setattr(plans,"CaktoClient",ReadOnlyCakto)
    monkeypatch.setenv("CAKTO_BILLING_LIVE_ENABLED","1")
    
    with pytest.raises(HTTPException) as error:
        await plans.start_plan_checkout(
            tenant,plans.CheckoutChoice(plan_code="profissional"),
            Actor("member",account_id=account_id))
    assert error.value.status_code==409
    assert "valor apresentado" in error.value.detail


def test_recurring_monthly_cakto_offer_uses_30_day_period_not_unique_interval():
    offer={"id":"monthly-synthetic","name":"Orion Profissional","price":"89.90",
           "status":"active","type":"subscription","recurrence_period":30,
           "intervalType":"week","interval":7}
    assert plans._exact_offer([offer],plans.CODES["profissional"])==offer
    assert plans._exact_offer([{**offer,"recurrence_period":365}],
                              plans.CODES["profissional"]) is None


def test_cakto_uses_documented_sck_instead_of_arbitrary_checkout_callback():
    payload={"event":"purchase_approved","data":{"id":"synthetic-order","offer_type":"main",
        "status":"paid","callback":"fake_unused_checkout_param_2134",
        "sck":"synthetic_sck_key_0123456","amount":89.90,"offer":{"id":"CIProOffer"}}}
    event=cakto_api.normalized_events(payload)[0]
    assert event["callback_hash"]==hashlib.sha256(b"synthetic_sck_key_0123456").hexdigest()
    assert event["amount_cents"]==8990
    assert event["currency"] is None
