"""Lifecycle authority regressions; synthetic SQL/GETs, no financial operations."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app import cakto_api, cakto_subscription_lifecycle as lifecycle
from app.plan_catalog import CODES


def case(event_type="subscription_renewed"):
    plan = CODES["essencial"]
    paid_at = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)
    event = {
        "delivery_digest": "synthetic-digest", "callback_hash": None,
        "event_type": event_type, "external_order_id": "synthetic-renewal",
        "external_subscription_id": "synthetic-subscription",
        "external_offer_id": "synthetic-original-offer",
        "amount_cents": plan["price_cents"], "currency": "BRL",
    }
    order = {
        "id": event["external_order_id"], "status": "paid",
        "type": "subscription", "offer_type": "main",
        "offer": {"id": event["external_offer_id"]},
        "subscription": event["external_subscription_id"],
        "amount": str(plan["price_cents"] / 100), "currency": "BRL",
        "paidAt": paid_at.isoformat(), "createdAt": paid_at.isoformat(),
    }
    subscription = {
        "id": event["external_subscription_id"], "status": "active",
        "parent_order": "synthetic-first-order", "offer": event["external_offer_id"],
        "amount": order["amount"], "recurrence_period": 30, "paymentMethod": "pix_auto",
    }
    binding = {
        "tenant_id": uuid4(), "subscription_id": uuid4(), "plan_code": "essencial",
        "plan_db_code": plan["db_code"], "monthly_cents": plan["price_cents"],
        "external_subscription_id": subscription["id"], "local_status": "active",
        "last_order_id": "synthetic-previous-renewal", "last_verified_at": paid_at,
        "last_verified_payment_at": paid_at - timedelta(days=30),
    }
    intent = {
        "tenant_id": binding["tenant_id"], "account_id": uuid4(),
        "state": "verified", "plan_code": "essencial",
        "offer_id": event["external_offer_id"], "expected_cents": plan["price_cents"],
        "external_order_id": subscription["parent_order"],
    }
    offer = {
        "id": event["external_offer_id"], "name": plan["name"], "status": "active",
        "type": "subscription", "recurrence_period": 30, "price": order["amount"],
    }
    return SimpleNamespace(event=event, order=order, subscription=subscription,
                           binding=binding, intents=[intent], offers=[offer])


class Result:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value

    def scalar_one_or_none(self):
        return self.value

    def mappings(self):
        return self

    def all(self):
        return self.value

    def first(self):
        return self.value


class Database:
    def __init__(self, data):
        self.data = data
        self.writes = []
        self.intent_reads = []
        self.claimed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        pass

    def begin(self):
        return self

    async def execute(self, statement, params=None):
        query = str(statement)
        if "to_regclass" in query:
            return Result(True)
        if "SELECT" in query and "FROM cakto_webhook_events" in query:
            return Result([] if self.claimed else [self.data.event])
        if "SELECT tenant_id FROM orion_cakto_bindings" in query:
            return Result(self.data.binding["tenant_id"])
        if "SELECT tenant_id,last_order_id FROM orion_cakto_bindings" in query:
            return Result(self.data.binding)
        if "SELECT b.tenant_id" in query:
            return Result(self.data.binding)
        if "FROM orion_cakto_checkout_intents" in query:
            self.intent_reads.append((query, params))
            return Result(self.data.intents)
        if "UPDATE" in query:
            self.writes.append((query, params or {}))
            if "SET attempts=attempts+1" in query:
                self.claimed = True
            return Result(None)
        raise AssertionError("Unexpected synthetic SQL shape")


def runtime(monkeypatch, data):
    db = Database(data)
    client = SimpleNamespace(
        configured=True, get_order=AsyncMock(return_value=data.order),
        _get=AsyncMock(return_value=data.subscription),
        list_offers=AsyncMock(return_value=data.offers),
    )
    monkeypatch.setattr(cakto_api, "cached_cakto_client", lambda: client)
    monkeypatch.setattr(cakto_api, "support_session_factory", lambda: lambda: db)
    monkeypatch.setattr(lifecycle, "support_session_factory", lambda: lambda: db)
    monkeypatch.setattr(lifecycle, "lock_plan", AsyncMock())
    monkeypatch.setattr(cakto_api, "recover_provider_receipts", AsyncMock(return_value=0))
    monkeypatch.setenv("ORION_CAKTO_LIFECYCLE_ENABLED", "1")
    return db, client


@pytest.mark.asyncio
async def test_worker_preserves_order_offer_mismatch_without_renewing(monkeypatch):
    data = case()
    data.order["offer"] = {"id": "synthetic-foreign-offer"}
    db, client = runtime(monkeypatch, data)
    assert cakto_api.verify_order_event(data.event, data.order) == "mismatch"
    assert await cakto_api.scan_pending_cakto({}) == 1
    assert not any("UPDATE subscriptions" in query for query, _ in db.writes)
    assert db.writes[-1][1]["state"] == "mismatch"
    client._get.assert_not_awaited()


@pytest.mark.parametrize("offer", [{"id": "foreign"}, "foreign", {}])
def test_classifier_rejects_present_inconsistent_order_offer(offer):
    data = case()
    data.order["offer"] = offer
    assert lifecycle.lifecycle_decision(
        data.event, data.order, data.subscription, data.binding,
        expected_offer=data.event["external_offer_id"],
    ) == "manual_review"


@pytest.mark.asyncio
@pytest.mark.parametrize("event_type,order_status,sub_status", [
    ("subscription_renewed", "paid", "active"),
    ("subscription_canceled", "paid", "canceled"),
    ("refund", "refunded", "active"),
    ("chargeback", "MED", "active"),
    ("purchase_refused", "refused", "inactive"),
])
@pytest.mark.parametrize("catalog", ["archived", "removed", "renamed"])
async def test_existing_contract_does_not_depend_on_sellable_offer(
    monkeypatch, event_type, order_status, sub_status, catalog,
):
    data = case(event_type)
    data.order["status"] = order_status
    data.subscription["status"] = sub_status
    if event_type in {"refund", "chargeback"}:
        data.binding["last_order_id"] = data.order["id"]
    if catalog == "archived":
        data.offers[0]["status"] = "inactive"
    elif catalog == "removed":
        data.offers = []
    else:
        data.offers[0]["name"] = "Renamed offer"
    db, client = runtime(monkeypatch, data)
    result = await lifecycle.reconcile_existing_cakto_subscription(data.event, data.order, client)
    assert result == "reconciled"
    expected = {"subscription_renewed": "active", "subscription_canceled": "canceled",
                "refund": "suspended", "chargeback": "suspended", "purchase_refused": "past_due"}[event_type]
    assert next(params["status"] for query, params in db.writes
                if "UPDATE subscriptions" in query) == expected
    client.list_offers.assert_not_awaited()
    assert len(db.intent_reads) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["missing", "duplicate", "tenant", "owner", "plan", "amount", "order", "state", "offer"])
async def test_missing_or_inconsistent_historical_proof_never_changes_access(monkeypatch, change):
    data = case()
    intent = data.intents[0]
    if change == "missing":
        data.intents = []
    elif change == "duplicate":
        data.intents.append(deepcopy(intent))
    else:
        key, value = {
            "tenant": ("tenant_id", uuid4()), "plan": ("plan_code", "elite"),
            "owner": ("account_id", None),
            "amount": ("expected_cents", 1), "order": ("external_order_id", "foreign-order"),
            "state": ("state", "pending"), "offer": ("offer_id", "foreign-offer"),
        }[change]
        intent[key] = value
    db, client = runtime(monkeypatch, data)
    assert await lifecycle.reconcile_existing_cakto_subscription(
        data.event, data.order, client,
    ) == "manual_review"
    assert not db.writes


@pytest.mark.asyncio
@pytest.mark.parametrize("parent", [None, "", "../foreign", 12])
async def test_unproven_first_order_never_chooses_an_intent(monkeypatch, parent):
    data = case()
    data.subscription["parent_order"] = parent
    db, client = runtime(monkeypatch, data)
    assert await lifecycle.reconcile_existing_cakto_subscription(
        data.event, data.order, client,
    ) == "manual_review"
    assert not db.writes


@pytest.mark.parametrize("event,status", [("refund","refunded"),("chargeback","MED")])
def test_reversal_never_erases_terminal_cancellation_before_stale_renewal(event,status):
    data = case(event)
    data.order["status"] = status
    data.binding.update(local_status="canceled",last_order_id=data.order["id"])
    result = lifecycle.lifecycle_decision(data.event,data.order,data.subscription,data.binding,
                                         expected_offer=data.event["external_offer_id"])
    assert result == "canceled"
    data.binding["local_status"] = result
    data.event["event_type"] = "subscription_renewed"
    data.order.update(id="later-stale-order",status="paid")
    data.event["external_order_id"] = data.order["id"]
    assert lifecycle.lifecycle_decision(data.event,data.order,data.subscription,data.binding,
                                         expected_offer=data.event["external_offer_id"]) == "manual_review"


@pytest.mark.parametrize("date", ["missing","old","equal","NaN","future","naive"])
def test_late_or_unproven_refusal_never_revokes_a_newer_paid_cycle(date):
    data = case("purchase_refused")
    paid = datetime.now(timezone.utc)
    data.binding["last_verified_payment_at"] = paid
    data.order["status"] = "refused"
    data.subscription["status"] = "inactive"  # Snapshot fetched before the newer payment.
    if date == "missing": data.order.pop("createdAt")
    elif date == "old": data.order["createdAt"] = (paid-timedelta(days=1)).isoformat()
    elif date == "equal": data.order["createdAt"] = paid.isoformat()
    elif date == "future": data.order["createdAt"] = (paid+timedelta(minutes=6)).isoformat()
    elif date == "naive": data.order["createdAt"] = paid.replace(tzinfo=None).isoformat()
    else: data.order["createdAt"] = date
    assert lifecycle.lifecycle_decision(data.event,data.order,data.subscription,data.binding,
                                         expected_offer=data.event["external_offer_id"]) == "manual_review"
