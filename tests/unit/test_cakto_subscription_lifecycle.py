"""Synthetic lifecycle decisions, no Cakto requests or real money."""
from datetime import datetime, timedelta, timezone
from copy import deepcopy
import pytest

from app.cakto_subscription_lifecycle import lifecycle_decision
from app.plan_catalog import CODES

NOW=datetime(2026,11,2,12,0,tzinfo=timezone.utc)
PAID_AT=NOW.isoformat()


def base():
    plan=CODES["essencial"]
    event={
        "external_order_id":"synthetic-order-1",
        "external_subscription_id":"synthetic-sub-1",
        "external_offer_id":"synthetic-offer-1",
        "event_type":"subscription_renewed",
        "currency":"BRL",
        "amount_cents":plan["price_cents"],
    }
    order={
        "id":"synthetic-order-1",
        "status":"paid","type":"subscription",
        "offer_type":"main","amount":"29.90",
        "currency":"BRL","paidAt":PAID_AT,"createdAt":PAID_AT,
        "subscription":"synthetic-sub-1",
    }
    subscription={
        "id":"synthetic-sub-1","offer":"synthetic-offer-1",
        "recurrence_period":30,"paymentMethod":"pix_auto",
        "amount":"29.90","status":"active",
    }
    binding={
        "external_subscription_id":"synthetic-sub-1",
        "plan_code":"essencial",
        "plan_db_code":plan["db_code"],
        "monthly_cents":plan["price_cents"],
        "last_order_id":"synthetic-order-before",
        "last_verified_payment_at":NOW-timedelta(days=32),
        "local_status":"active",
    }
    return event,order,subscription,binding


def decision(*, mutate=None):
    ev,order,sub,binding=base()
    if mutate:
        mutate(ev,order,sub,binding)
    return lifecycle_decision(ev,order,sub,binding,expected_offer="synthetic-offer-1")


def test_monthly_renewal_is_proven_by_latest_paid_order_and_contract():
    assert decision()=="renewed"


def test_same_order_is_idempotent():
    assert decision(mutate=lambda e,o,s,b:b.update(last_order_id=o["id"]))=="already_verified"


def test_old_renewal_can_never_extend_a_subscription():
    assert decision(mutate=lambda e,o,s,b:b.update(last_verified_payment_at=NOW))=="manual_review"


def test_canceled_subscription_cannot_be_resurrected_by_renewal():
    assert decision(mutate=lambda e,o,s,b:b.update(local_status="canceled"))=="manual_review"


@pytest.mark.parametrize("status",["refunded","MED","chargedback"])
def test_bad_payment_state_cannot_renew(status):
    assert decision(mutate=lambda e,o,s,b:o.update(status=status))=="manual_review"


@pytest.mark.parametrize("field,value",[
    ("id","other-order"),
    ("amount","39.90"),
    ("type","one_time"),
    ("offer_type","bump"),
    ("currency","USD"),
    ("subscription","other-subscription"),
])
def test_forged_or_mismatched_orders_never_renew(field,value):
    assert decision(mutate=lambda e,o,s,b:o.update({field:value}))=="manual_review"


@pytest.mark.parametrize("field,value",[
    ("offer","other-offer"),("amount","89.90"),
    ("recurrence_period",0),("paymentMethod","pix"),
    ("status","canceled")
])
def test_unverified_subscription_terms_never_renew(field,value):
    assert decision(mutate=lambda e,o,s,b:s.update({field:value}))=="manual_review"


def test_verified_cancel_blocks_future_access():
    def cancel(e,o,s,b):
        e["event_type"]="subscription_canceled"
        s["status"]="canceled"
    assert decision(mutate=cancel)=="canceled"


def test_cancel_requires_provider_status_not_just_webhook():
    assert decision(mutate=lambda e,o,s,b:e.update(event_type="subscription_canceled"))=="manual_review"


@pytest.mark.parametrize("event_type,payment_status",[
    ("refund","refunded"),("chargeback","MED"),("chargeback","chargedback"),
])
def test_refund_and_chargeback_suspend_only_latest_paid_order(event_type,payment_status):
    def change(e,o,s,b):
        e["event_type"]=event_type
        o["status"]=payment_status
        b["last_order_id"]=o["id"]
    assert decision(mutate=change)=="suspended"


def test_old_refund_must_not_suspend_newer_renewal():
    def change(e,o,s,b):
        e["event_type"]="refund"
        o["status"]="refunded"
        b["last_order_id"]="newer-order"
    assert decision(mutate=change)=="manual_review"


def test_declined_payment_can_be_past_due_if_remote_is_inactive():
    def change(e,o,s,b):
        e["event_type"]="purchase_refused"
        o["status"]="refused"
        s["status"]="inactive"
    assert decision(mutate=change)=="past_due"


def test_declined_event_cannot_revoke_a_still_active_subscription():
    def change(e,o,s,b):
        e["event_type"]="purchase_refused"
        o["status"]="refused"
        s["status"]="active"
    assert decision(mutate=change)=="manual_review"


def test_wrong_plan_is_never_accepted():
    assert decision(mutate=lambda e,o,s,b:b.update(plan_db_code="orion_elite"))=="manual_review"
