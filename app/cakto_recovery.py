"""Bounded authenticated GET recovery for server-bound recurring contracts.

No payment/subscription mutation, provider resend, raw body or customer storage.
The ordinary verifier still rechecks every receipt before changing access.
"""
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import hmac

import httpx
from sqlalchemy import text

from app.catalog_api import support_session_factory
from app.cakto_api import ORDER_ID, _subscription_id, normalized_events
from app.plan_catalog import CODES

COOLDOWN_SECONDS = 600
MAX_ORDERS = 100


def instant(value):
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result if result.tzinfo else None
    except ValueError:
        return None


def money(value, cents):
    try:
        result = Decimal(str(value)) * 100
        return result.is_finite() and result == result.to_integral_value() and int(result) == cents
    except (ValueError, TypeError, InvalidOperation):
        return False


def order_matches(order, *, order_id, offer_id, cents, subscription_id=None, method=None):
    if (not isinstance(order, Mapping) or order.get("id") != order_id
            or order.get("type") != "subscription" or order.get("offer_type") != "main"
            or order.get("currency") != "BRL" or not money(order.get("amount"), cents)):
        return False
    offer = order.get("offer")
    if offer is not None and (not isinstance(offer, Mapping) or offer.get("id") != offer_id):
        return False
    if subscription_id is not None and _subscription_id(order.get("subscription")) != subscription_id:
        return False
    if method is not None and order.get("paymentMethod") != method:
        return False
    return True


def receipt(event_type, order, *, offer_id, callback_hash=None):
    # Explicit allowlist: customer/card/secret/raw response never enters storage.
    safe = {name:order.get(name) for name in (
        "id", "status", "subscription", "amount", "currency", "paidAt",
        "refundedAt", "chargedbackAt", "canceledAt")}
    safe.update({"offer_type":"main", "offer":{"id":offer_id}})
    result = normalized_events({"event":event_type,"data":safe})[0]
    result["callback_hash"] = callback_hash
    return result


async def claim_direct(limit):
    async with support_session_factory()() as db:
        async with db.begin():
            rows = (await db.execute(text("""
                SELECT a.id,a.tenant_id,a.account_id,a.external_order_id,a.method,
                       i.offer_id,i.expected_cents,i.callback_hash,i.plan_code
                FROM orion_direct_payment_attempts a
                JOIN orion_cakto_checkout_intents i ON i.id=a.intent_id
                  AND i.tenant_id=a.tenant_id AND i.account_id=a.account_id
                WHERE a.state IN ('created','needs_review','unknown')
                  AND a.external_order_id IS NOT NULL AND i.state='pending'
                  AND (a.provider_checked_at IS NULL
                       OR a.provider_checked_at<=now()-interval '10 minutes')
                ORDER BY a.provider_checked_at NULLS FIRST,a.created_at,a.id
                LIMIT :limit FOR UPDATE OF a SKIP LOCKED
            """), {"limit":limit})).mappings().all()
            for row in rows:
                await db.execute(text("UPDATE orion_direct_payment_attempts SET provider_checked_at=now() WHERE id=:id"),
                                 {"id":row["id"]})
            return rows


async def claim_bindings(limit):
    async with support_session_factory()() as db:
        async with db.begin():
            rows = (await db.execute(text("""
                SELECT b.tenant_id,b.external_subscription_id,b.plan_code,b.last_order_id,
                       s.status AS local_status,s.last_verified_payment_at,p.monthly_cents
                FROM orion_cakto_bindings b
                JOIN subscriptions s ON s.id=b.subscription_id AND s.tenant_id=b.tenant_id
                JOIN plans p ON p.id=s.plan_id
                WHERE (s.status IN ('active','past_due','suspended')
                       OR (s.status='canceled' AND s.grace_until>now()))
                  AND (b.provider_checked_at IS NULL
                       OR b.provider_checked_at<=now()-interval '10 minutes')
                ORDER BY b.provider_checked_at NULLS FIRST,b.tenant_id
                LIMIT :limit FOR UPDATE OF b SKIP LOCKED
            """), {"limit":limit})).mappings().all()
            for row in rows:
                await db.execute(text("UPDATE orion_cakto_bindings SET provider_checked_at=now() WHERE tenant_id=:tenant"),
                                 {"tenant":row["tenant_id"]})
            return rows


async def original_intent(binding, subscription):
    first = subscription.get("parent_order")
    if not isinstance(first, str) or not ORDER_ID.fullmatch(first):
        return None
    async with support_session_factory()() as db:
        rows = (await db.execute(text("""
            SELECT tenant_id,account_id,plan_code,offer_id,expected_cents,external_order_id,state
            FROM orion_cakto_checkout_intents
            WHERE tenant_id=:tenant AND plan_code=:plan AND external_order_id=:first
              AND state='verified' LIMIT 2
        """), {"tenant":binding["tenant_id"],"plan":binding["plan_code"],"first":first})).mappings().all()
    if len(rows) != 1:
        return None
    row = rows[0]
    plan = CODES.get(binding["plan_code"])
    if (not plan or row["tenant_id"] != binding["tenant_id"] or row["account_id"] is None
            or row["state"] != "verified" or row["plan_code"] != binding["plan_code"]
            or row["external_order_id"] != first or row["expected_cents"] != plan["price_cents"]
            or binding["monthly_cents"] != plan["price_cents"]
            or subscription.get("offer") != row["offer_id"]
            or subscription.get("recurrence_period") not in (30,"30")
            or subscription.get("paymentMethod") not in ("pix_auto","credit_card","threeDs")
            or not money(subscription.get("amount"),row["expected_cents"])):
        return None
    return row


async def direct_receipts(client, row):
    order_id = row["external_order_id"]
    if not isinstance(order_id, str) or not ORDER_ID.fullmatch(order_id):
        return []
    order = await client.get_order(order_id)
    method = "pix_auto" if row["method"] == "pix" else "threeDs"
    if not order_matches(order,order_id=order_id,offer_id=row["offer_id"],
                         cents=row["expected_cents"],method=method):
        return []
    if not isinstance(order.get("status"),str):
        return []
    for name in ("sck","callback"):
        tracking = order.get(name)
        if tracking is not None and (not isinstance(tracking,str)
                or not hmac.compare_digest(hashlib.sha256(tracking.encode()).hexdigest(),row["callback_hash"])):
            return []
    event = {"paid":"purchase_approved","refused":"purchase_refused",
             "refunded":"refund","chargedback":"chargeback","MED":"chargeback"}.get(order.get("status"))
    if event is None:
        return []
    if event == "purchase_approved":
        subscription_id = _subscription_id(order.get("subscription"))
        if (instant(order.get("paidAt")) is None or not isinstance(subscription_id,str)
                or not ORDER_ID.fullmatch(subscription_id)):
            return []
    return [receipt(event,order,offer_id=row["offer_id"],callback_hash=row["callback_hash"])]


async def binding_receipts(client, row):
    sub_id = row["external_subscription_id"]
    if not isinstance(sub_id, str) or not ORDER_ID.fullmatch(sub_id):
        return []
    subscription = await client._get("/subscriptions/"+sub_id+"/")
    if not isinstance(subscription, Mapping) or subscription.get("id") != sub_id:
        return []
    intent = await original_intent(row,subscription)
    if intent is None:
        return []
    ids = subscription.get("orders")
    if (not isinstance(ids,list) or len(ids)>MAX_ORDERS or
            any(not isinstance(value,str) or not ORDER_ID.fullmatch(value) for value in ids)):
        return []
    ids = set(ids) | {row["last_order_id"]}
    if not ids or len(ids)>MAX_ORDERS or any(not isinstance(i,str) or not ORDER_ID.fullmatch(i) for i in ids):
        return []
    result = await client._get("/orders/",id=",".join(sorted(ids)),currency="BRL",limit=MAX_ORDERS,page=1)
    orders = result.get("results") if isinstance(result,Mapping) else None
    if (not isinstance(orders,list) or result.get("next") or len(orders)!=len(ids)
            or any(not isinstance(order,Mapping) or not isinstance(order.get("id"),str)
                   or not isinstance(order.get("status"),str) for order in orders)
            or {order.get("id") for order in orders} != ids):
        return []
    if any(_subscription_id(order.get("subscription")) != sub_id
           or not isinstance(order.get("id"),str) for order in orders):
        return []
    if subscription.get("status") == "canceled":
        order = next(order for order in orders if order["id"]==row["last_order_id"])
        event = {"refunded":"refund","chargedback":"chargeback","MED":"chargeback"}.get(
            order.get("status"),"subscription_canceled")
        return ([receipt(event,order,offer_id=intent["offer_id"])]
                if order_matches(order,order_id=order["id"],offer_id=intent["offer_id"],
                                 cents=intent["expected_cents"],subscription_id=sub_id) else [])
    if subscription.get("status") in ("late","inactive"):
        refused = [order for order in orders if order.get("status")=="refused"
                   and instant(order.get("createdAt")) is not None]
        if refused:
            latest = max(refused,key=lambda order:instant(order["createdAt"]))
            previous = row.get("last_verified_payment_at")
            if (isinstance(previous,datetime) and instant(latest["createdAt"])>previous
                    and order_matches(latest,order_id=latest["id"],offer_id=intent["offer_id"],
                                      cents=intent["expected_cents"],subscription_id=sub_id)):
                return [receipt("purchase_refused",latest,offer_id=intent["offer_id"])]
    settled = [order for order in orders if instant(order.get("paidAt")) is not None]
    if not settled:
        return []
    latest = max(settled,key=lambda order:instant(order["paidAt"]))
    if not order_matches(latest,order_id=latest["id"],offer_id=intent["offer_id"],
                         cents=intent["expected_cents"],subscription_id=sub_id):
        return []
    event = {"paid":"subscription_renewed","refunded":"refund",
             "chargedback":"chargeback","MED":"chargeback"}.get(latest.get("status"))
    if event == "subscription_renewed" and (subscription.get("status") != "active"
            or latest["id"]==row["last_order_id"]):
        return []
    return [receipt(event,latest,offer_id=intent["offer_id"])] if event else []


async def recover_recurring_provider_receipts(client, limit=10, *, on_receipt=None):
    if not isinstance(limit,int) or isinstance(limit,bool) or not 1<=limit<=10:
        raise ValueError("Invalid recovery limit")
    receipts = []
    async def emit(events):
        for event in events:
            if on_receipt is not None:
                await on_receipt(event)
            receipts.append(event)

    for _ in range(limit):
        rows = await claim_direct(1)
        if not rows:
            break
        try:
            events = await direct_receipts(client,rows[0])
        except (httpx.HTTPError,ValueError,RuntimeError,TypeError):
            continue
        await emit(events)
    for _ in range(limit):
        rows = await claim_bindings(1)
        if not rows:
            break
        try:
            events = await binding_receipts(client,rows[0])
        except (httpx.HTTPError,ValueError,RuntimeError,TypeError):
            continue
        await emit(events)
    return receipts
