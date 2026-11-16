"""Synthetic payment provider contracts and first-order confirmation.

No network, personal data, payment credentials, or real provider charges.
"""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.orion_direct_checkout import provider_request, verified_creation
from app.plan_management import first_cakto_subscription_confirmed

OFFER="synthetic-offer-essential"
INTENT={"offer_id":OFFER,"expected_cents":2990}
ORDER={"id":"synthetic-initial-order","subscription":"synthetic-subscription"}
EVENT={"external_offer_id":OFFER,"amount_cents":2990}
SUBSCRIPTION={
    "id":"synthetic-subscription",
    "parent_order":"synthetic-initial-order",
    "status":"active",
    "recurrence_period":30,
    "offer":OFFER,
    "paymentMethod":"pix_auto",
    "amount":"29.90",
}


def data(method):
    payer=SimpleNamespace(name="Synthetic Tester",email="synthetic@example.invalid",
        phone="5511999999999",doc_type="cpf",doc_number="12345678909",
        fingerprint="orion_synthetic_checkout_abcdef012345")
    return SimpleNamespace(method=method,payer=payer,card_token="card_synthetictoken000000",
        three_ds={"cavv":"synthetic-cavv","eci":"05",
                  "referenceId":"synthetic-reference","version":"2.2.0"},
        antifraud_reference="orion_antifraud_synthetic_ref")


def test_pix_auto_is_recurring_not_a_one_time_pix():
    payload=provider_request(INTENT,data("pix"),"synthetic-callback")
    assert payload["paymentMethod"]=="pix_auto"
    assert payload["pixExpiresIn"]==900
    assert payload["items"]==[{"offerId":OFFER,"quantity":1,"offerType":"main"}]
    assert payload["metadata"]["sck"]=="synthetic-callback"
    assert "card" not in payload and "threeDSecure" not in payload
    assert "docNumber" in payload["customer"]


def test_card_three_ds_uses_token_only_and_never_pan_or_cvv():
    payload=provider_request(INTENT,data("card"),"synthetic-callback")
    assert payload["paymentMethod"]=="threeDs"
    assert payload["installments"]==1
    assert payload["card"]=={"token":"card_synthetictoken000000"}
    assert payload["threeDSecure"]["referenceId"]=="synthetic-reference"
    assert payload["antifraud_profiling_attempt_reference"]=="orion_antifraud_synthetic_ref"
    serialized=str(payload)
    assert "cardNumber" not in serialized and "cvv" not in serialized
    assert "card_number" not in serialized


def test_pix_creation_requires_valid_authorization_journey():
    provider={
        "id":"synthetic-order-id","paymentMethod":"pix_auto",
        "offer":{"id":OFFER},"amount":"29.90",
        "status":"waiting_payment",
        "pix":{"qrCode":"synthetic-pix-code-"+"A"*85,"user_journey":"JORNADA_1"},
    }
    assert verified_creation(provider,INTENT,"pix")
    invalid=deepcopy(provider)
    invalid["pix"]["user_journey"]="one_time_pix"
    assert not verified_creation(invalid,INTENT,"pix")


@pytest.mark.parametrize("field,wrong",[
    ("offer",{"id":"foreign-offer"}),("amount","29.91"),
    ("paymentMethod","pix"),("status","error")
])
def test_unverified_pix_provider_responses_are_rejected(field,wrong):
    provider={
        "id":"synthetic-order-id","paymentMethod":"pix_auto",
        "offer":{"id":OFFER},"amount":"29.90",
        "status":"waiting_payment",
        "pix":{"qrCode":"synthetic-pix-code-"+"A"*85,"user_journey":"JORNADA_1"},
    }
    provider[field]=wrong
    assert not verified_creation(provider,INTENT,"pix")


def test_first_subscription_requires_active_recurring_processor_contract():
    assert first_cakto_subscription_confirmed(EVENT,ORDER,SUBSCRIPTION)


@pytest.mark.parametrize("field,wrong",[
    ("id","foreign-subscription"),("parent_order","other-order"),
    ("status","pending"),("recurrence_period",0),
    ("offer","foreign-offer"),("paymentMethod","pix"),
    ("amount","29.91"),
])
def test_first_subscription_rejects_any_inconsistent_contract(field,wrong):
    sub={**SUBSCRIPTION,field:wrong}
    assert not first_cakto_subscription_confirmed(EVENT,ORDER,sub)


def test_first_subscription_requires_proper_webhook_amount():
    assert not first_cakto_subscription_confirmed(
        {**EVENT,"amount_cents":8990},ORDER,SUBSCRIPTION)


def test_no_processor_subscriptions_are_never_activated():
    assert not first_cakto_subscription_confirmed(EVENT,ORDER,None)
