"""Conservative lifecycle reconciliation for existing Cakto subscriptions.

Only a provider GET for the exact order and subscription, a signed persisted
receipt and an existing Orion tenant binding may change the local state.
No customer, token, raw payload or card details are persisted or logged.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy import text

from app.catalog_api import support_session_factory
from app.cakto_api import ORDER_ID, _subscription_id
from app.plan_management import CODES, _exact_offer
from app.plan_permissions import lock_plan


LIFECYCLE_EVENTS = frozenset({
    "subscription_renewed", "subscription_canceled",
    "purchase_refused", "refund", "chargeback",
})


def amount_matches(value: object, expected_cents: int) -> bool:
    try:
        cents = Decimal(str(value))*100
        return cents == cents.to_integral_value() and int(cents) == expected_cents
    except (ValueError, TypeError, InvalidOperation):
        return False


def _order_paid_at(order: dict) -> datetime | None:
    paid = order.get("paidAt")
    if not isinstance(paid, str):
        return None
    try:
        value = datetime.fromisoformat(paid.replace("Z","+00:00"))
        return value if value.tzinfo else None
    except ValueError:
        return None


def lifecycle_decision(event: dict, order: dict, subscription: dict,
                       binding: dict, *, expected_offer: str) -> str:
    """Pure state classifier: never grants access on a webhook alone."""
    if not isinstance(order, dict) or not isinstance(subscription, dict):
        return "manual_review"
    order_id, subscription_id = event.get("external_order_id"), binding.get("external_subscription_id")
    if not (isinstance(order_id, str) and ORDER_ID.fullmatch(order_id)
            and isinstance(subscription_id, str) and ORDER_ID.fullmatch(subscription_id)
            and order.get("id") == order_id
            and order.get("type") == "subscription"
            and order.get("offer_type") == "main"
            and subscription.get("id") == subscription_id
            and _subscription_id(order.get("subscription")) == subscription_id
            and event.get("external_offer_id") == expected_offer
            and event.get("external_subscription_id") in (None, subscription_id)
            and subscription.get("offer") == expected_offer
            and subscription.get("recurrence_period") in (30, "30")
            and subscription.get("paymentMethod") in ("pix_auto", "credit_card", "threeDs")):
        return "manual_review"

    code = binding.get("plan_code")
    plan = CODES.get(code)
    if (not plan or binding.get("plan_db_code") != plan["db_code"]
            or binding.get("monthly_cents") != plan["price_cents"]
            or not amount_matches(subscription.get("amount"), plan["price_cents"])):
        return "manual_review"
    if order.get("currency") != "BRL":
        return "manual_review"
    event_type = event.get("event_type")
    if event_type not in LIFECYCLE_EVENTS:
        return "manual_review"

    if event_type == "subscription_canceled":
        return "canceled" if subscription.get("status") == "canceled" else "manual_review"

    # For all transactions other than cancellation, require exact signed amount.
    if (event.get("currency") != "BRL"
            or event.get("amount_cents") != plan["price_cents"]
            or not amount_matches(order.get("amount"), plan["price_cents"])):
        return "manual_review"

    if event_type == "subscription_renewed":
        paid_at = _order_paid_at(order)
        if (order.get("status") != "paid"
                or subscription.get("status") != "active"
                or paid_at is None):
            return "manual_review"
        if binding.get("last_order_id") == order_id:
            return "already_verified"
        previous_paid = binding.get("last_verified_payment_at")
        if isinstance(previous_paid, datetime) and paid_at <= previous_paid:
            return "manual_review"  # Prevent replay of an older renewal.
        if binding.get("local_status") == "canceled":
            return "manual_review"  # Never reactivate a cancelled tenant from old receipt.
        return "renewed"

    if event_type in ("refund", "chargeback"):
        expected = {"refund":{"refunded"}, "chargeback":{"chargedback","MED"}}
        if (order.get("status") not in expected[event_type]
                or binding.get("last_order_id") != order_id):
            return "manual_review"  # Old refunded order must not suspend a newer one.
        return "suspended"

    if event_type == "purchase_refused":
        if (order.get("status") != "refused"
                or subscription.get("status") not in ("late","inactive")
                or binding.get("local_status") == "canceled"):
            return "manual_review"
        return "past_due"

    return "manual_review"


async def reconcile_existing_cakto_subscription(event: dict, order: dict, client) -> str:
    """Reconcile only a subscription already linked to one verified Orion tenant."""
    if event.get("event_type") not in LIFECYCLE_EVENTS:
        return "unlinked"
    subscription_id = _subscription_id(order.get("subscription")) if isinstance(order,dict) else None
    if not subscription_id or not ORDER_ID.fullmatch(subscription_id):
        return "manual_review"

    # These provider calls are strictly GET and happen outside the DB lock.
    remote_subscription = await client._get("/subscriptions/" + subscription_id + "/")
    offers = await client.list_offers()
    async with support_session_factory()() as db:
        async with db.begin():
            tenant = (await db.execute(text("SELECT tenant_id FROM orion_cakto_bindings WHERE external_subscription_id=:subscription"),
                                       {"subscription":subscription_id})).scalar_one_or_none()
            if tenant is None:
                return "unlinked"
            await lock_plan(db, tenant, exclusive=True)
            binding = (await db.execute(text("""
                SELECT b.tenant_id,b.subscription_id,b.external_subscription_id,
                       b.plan_code,b.last_order_id,b.last_verified_at,
                       s.status AS local_status,s.last_verified_payment_at,
                       p.code AS plan_db_code,p.monthly_cents
                FROM orion_cakto_bindings b
                JOIN subscriptions s ON s.id=b.subscription_id
                  AND s.tenant_id=b.tenant_id
                JOIN plans p ON p.id=s.plan_id
                WHERE b.external_subscription_id=:subscription
                FOR UPDATE OF b,s
            """),{"subscription":subscription_id})).mappings().first()
            if not binding:
                return "unlinked"
            if binding["tenant_id"] != tenant:
                return "manual_review"
            plan = CODES.get(binding["plan_code"])
            if not plan:
                return "manual_review"
            offer = _exact_offer(offers,plan)
            if not offer:
                return "manual_review"
            action = lifecycle_decision(event,order,remote_subscription,binding,
                                        expected_offer=str(offer["id"]))
            if action == "already_verified":
                return "already_verified"
            if action not in ("renewed","canceled","suspended","past_due"):
                return "manual_review"

            new_status = {"renewed":"active","canceled":"canceled",
                          "suspended":"suspended","past_due":"past_due"}[action]
            await db.execute(text("""
                UPDATE subscriptions SET status=CAST(:status AS varchar(20)), updated_at=now(),
                    last_verified_payment_at=CASE
                        WHEN CAST(:status AS varchar(20))='active' THEN :paid_at
                        ELSE last_verified_payment_at END
                WHERE id=:id AND tenant_id=:tenant
            """),{
                "id":binding["subscription_id"],"tenant":binding["tenant_id"],
                "status":new_status,
                "paid_at":_order_paid_at(order) if action=="renewed" else None,
            })
            if action == "renewed":
                await db.execute(text("""
                    UPDATE orion_cakto_bindings
                    SET last_order_id=:order_id,last_verified_at=now()
                    WHERE tenant_id=:tenant AND external_subscription_id=:subscription
                """),{
                    "tenant":binding["tenant_id"],"subscription":subscription_id,
                    "order_id":event["external_order_id"],
                })
            return "reconciled"
