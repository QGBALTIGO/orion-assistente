"""Idempotent prepaid Pix entitlements from signed receipts plus Cakto GET orders.

Never infers tenant from customer PII. Manual grants and old subscriptions win.
Refund/MED only adjusts the last paid period, with prior paid days preserved.
"""
from __future__ import annotations
import hashlib
from datetime import datetime,timedelta,timezone
from sqlalchemy import text
from app.catalog_api import support_session_factory
from app.plan_catalog import CODES
from app.plan_permissions import lock_plan
from app.prepaid_pix_api import cents
from app.plan_management import _ensure_owner
from fastapi import HTTPException


def authenticated_pix_order(event,order,record):
    if not isinstance(order,dict) or not isinstance(record,dict):
        return False
    if (event.get("event_type")!="purchase_approved"
        or event.get("external_order_id")!=record["external_order_id"]
        or order.get("id")!=record["external_order_id"]
        or order.get("status")!="paid" or order.get("type")!="unique"
        or order.get("paymentMethod")!="pix"
        or order.get("offer_type") not in ("main",None)
        or order.get("currency") not in ("BRL",None)
        or event.get("currency") not in ("BRL",None)
        or cents(order.get("amount"))!=record["expected_cents"]
        or event.get("external_offer_id")!=record["offer_id"]
        or event.get("external_subscription_id") is not None
        or event.get("amount_cents") not in (None,record["expected_cents"])):
        return False
    remote=order.get("offer")
    if isinstance(remote,dict) and remote.get("id")!=record["offer_id"]:
        return False
    event_hash=event.get("callback_hash")
    if event_hash is not None and event_hash!=record["callback_hash"]:
        return False
    token=order.get("sck")
    if token is not None:
        if not isinstance(token,str) or hashlib.sha256(token.encode()).hexdigest()!=record["callback_hash"]:
            return False
    return True


async def _record(db,order_id,lock=False):
    suffix=" FOR UPDATE" if lock else ""
    return (await db.execute(text("""
        SELECT id,tenant_id,account_id,plan_code,offer_id,expected_cents,
               callback_hash,external_order_id,state,previous_paid_until
        FROM orion_prepaid_pix_orders WHERE external_order_id=:order
    """+suffix),{"order":order_id})).mappings().first()


async def reconcile_prepaid_receipt(event,order):
    """Process a persisted, HMAC-authenticated purchase_approved receipt."""
    order_id=event.get("external_order_id")
    if not isinstance(order_id,str):
        return "not_prepaid"
    async with support_session_factory()() as db:
        async with db.begin():
            rec=await _record(db,order_id)
            if not rec:
                return "not_prepaid"
            if event.get("event_type")!="purchase_approved":
                return "manual_review"
            await lock_plan(db,rec["tenant_id"],exclusive=True)
            rec=await _record(db,order_id,lock=True)
            if rec["state"]=="paid":
                return "already_verified"
            if rec["state"]=="reserved":
                return "retry_later"
            if rec["state"]!="created" or not authenticated_pix_order(event,order,rec):
                return "manual_review"
            if CODES.get(rec["plan_code"],{}).get("price_cents")!=rec["expected_cents"]:
                return "manual_review"
            # A pending Pix must not grant access when the verified purchaser
            # no longer owns this business; membership changes are serialized
            # by the existing plan lock.
            try:
                await _ensure_owner(db,rec["tenant_id"],rec["account_id"])
            except HTTPException:
                return "manual_review"
            # Do not grant when a manual plan or legacy recurring bill exists.
            manual=(await db.execute(text("""
                SELECT manual_plan_code,manual_started_at,manual_expires_at
                  FROM tenant_plan_controls WHERE tenant_id=:tenant
            """),{"tenant":rec["tenant_id"]})).mappings().first()
            now=datetime.now(timezone.utc)
            if manual and manual["manual_plan_code"] and manual["manual_started_at"] and (
                manual["manual_expires_at"] is None or manual["manual_expires_at"]>now):
                return "manual_review"
            if (await db.execute(text("""
                SELECT 1 FROM subscriptions WHERE tenant_id=:tenant
            """),{"tenant":rec["tenant_id"]})).scalar_one_or_none():
                return "manual_review"
            grant=(await db.execute(text("""
                SELECT plan_code,paid_until FROM orion_prepaid_pix_access
                WHERE tenant_id=:tenant FOR UPDATE
            """),{"tenant":rec["tenant_id"]})).mappings().first()
            if grant and grant["paid_until"]>now and grant["plan_code"]!=rec["plan_code"]:
                return "manual_review"
            previous=grant["paid_until"] if grant else None
            start=max(now,previous) if previous else now
            end=start+timedelta(days=30)
            await db.execute(text("""
                INSERT INTO orion_prepaid_pix_access
                    (tenant_id,plan_code,paid_until,last_verified_payment_at,last_order_id)
                VALUES(:tenant,:plan,:end,now(),:order)
                ON CONFLICT(tenant_id) DO UPDATE SET
                    plan_code=EXCLUDED.plan_code,
                    paid_until=EXCLUDED.paid_until,
                    last_verified_payment_at=EXCLUDED.last_verified_payment_at,
                    last_order_id=EXCLUDED.last_order_id,
                    updated_at=now()
            """),{"tenant":rec["tenant_id"],"plan":rec["plan_code"],
                 "end":end,"order":order_id})
            await db.execute(text("""
                UPDATE orion_prepaid_pix_orders SET
                    state='paid',paid_at=now(),previous_paid_until=:old,
                    updated_at=now()
                WHERE id=:id AND state='created'
            """),{"id":rec["id"],"old":previous})
            return "activated"


async def reconcile_prepaid_reversal(event,order):
    """A past refunded payment cannot revoke a newer paid period."""
    event_type=event.get("event_type")
    if event_type not in {"refund","chargeback","purchase_refused"}:
        return "not_prepaid"
    order_id=event.get("external_order_id")
    if not isinstance(order_id,str):
        return "not_prepaid"
    async with support_session_factory()() as db:
        async with db.begin():
            rec=await _record(db,order_id)
            if not rec:
                return "not_prepaid"
            await lock_plan(db,rec["tenant_id"],exclusive=True)
            rec=await _record(db,order_id,lock=True)
            if rec["state"]=="refunded":
                return "already_verified"
            if (order.get("id")!=order_id or order.get("type")!="unique"
                or order.get("paymentMethod")!="pix"
                or event.get("external_offer_id")!=rec["offer_id"]
                or cents(order.get("amount"))!=rec["expected_cents"]
                or event.get("currency") not in ("BRL",None)
                or event.get("amount_cents") not in (None,rec["expected_cents"])):
                return "manual_review"
            if event_type=="purchase_refused":
                if order.get("status")!="refused" or rec["state"]!="created":
                    return "manual_review"
                await db.execute(text("""
                    UPDATE orion_prepaid_pix_orders SET state='declined',updated_at=now()
                    WHERE id=:id
                """),{"id":rec["id"]})
                return "reconciled"
            if order.get("status") not in {
                    "refund":{"refunded"},"chargeback":{"chargedback","MED"}
            }[event_type] or rec["state"]!="paid":
                return "manual_review"
            access=(await db.execute(text("""
                SELECT last_order_id,paid_until FROM orion_prepaid_pix_access
                WHERE tenant_id=:tenant FOR UPDATE
            """),{"tenant":rec["tenant_id"]})).mappings().first()
            if not access or access["last_order_id"]!=order_id:
                return "manual_review"
            now=datetime.now(timezone.utc)
            previous=rec["previous_paid_until"]
            fallback=now-timedelta(seconds=1)
            until=max(previous,fallback) if previous else fallback
            await db.execute(text("""
                UPDATE orion_prepaid_pix_access SET paid_until=:until,
                updated_at=now() WHERE tenant_id=:tenant AND last_order_id=:order
            """),{"until":until,"tenant":rec["tenant_id"],"order":order_id})
            await db.execute(text("""
                UPDATE orion_prepaid_pix_orders SET state='refunded',updated_at=now()
                WHERE id=:id
            """),{"id":rec["id"]})
            return "reconciled"
