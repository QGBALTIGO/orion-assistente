"""Read-only Cakto reconciliation for durable Orion Pix purchase intents.

Never creates/retries a payment, infers a tenant from payer PII, or treats an
empty/incomplete history page as proof that a charge did not happen.
"""
from datetime import timedelta
import hashlib
import json

import httpx
from sqlalchemy import text

from app.catalog_api import support_session_factory
from app.cakto_api import ORDER_ID
from app.plan_permissions import lock_plan
from app.prepaid_pix_api import verified_pix_order_binding


async def _unknown_order(client, record):
    """At most two provider pages; exact server tracking proof is mandatory."""
    matches={}
    for page in range(1,3):
        data=await client._get("/orders/",page=page,limit=100,
            offer=record["offer_id"],type="unique",paymentMethod="pix",
            createdAt__gte=(record["created_at"]-timedelta(minutes=5)).isoformat(),
            createdAt__lt=(record["expires_at"]+timedelta(minutes=5)).isoformat(),
            ordering="createdAt")
        if not isinstance(data,dict) or not isinstance(data.get("results"),list):
            return None
        for item in data["results"]:
            if not isinstance(item,dict):
                continue
            ref=item.get("id")
            token=item.get("sck")
            if (not isinstance(ref,str) or not ORDER_ID.fullmatch(ref)
                or not isinstance(token,str)
                or hashlib.sha256(token.encode()).hexdigest()!=record["callback_hash"]):
                continue
            if verified_pix_order_binding(item,{**record,"external_order_id":ref}):
                matches[ref]=item
        if not data.get("next"):
            break
    else:
        # More pages could contain a second order with the same tracking token.
        return None
    if len(matches)!=1:
        return None
    ref=next(iter(matches))
    order=await client.get_order(ref)
    if not verified_pix_order_binding(order,{**record,"external_order_id":ref}):
        return None
    # A concurrent original POST may already have saved the same external ID.
    # Exclusive plan admission plus row locking prevents binding another charge.
    async with support_session_factory()() as db:
        async with db.begin():
            await lock_plan(db,record["tenant_id"],exclusive=True)
            saved=(await db.execute(text("""
                UPDATE orion_prepaid_pix_orders
                SET external_order_id=:external,state='created',updated_at=now()
                WHERE id=:id AND callback_hash=:hash
                  AND state IN ('reserved','created','needs_review')
                  AND (external_order_id IS NULL OR external_order_id=:external)
                RETURNING id
            """),{"external":ref,"id":record["id"],"hash":record["callback_hash"]})).scalar_one_or_none()
    return order if saved else None


def provider_receipt(order,record):
    """Minimal internal receipt; provenance is persisted by the worker."""
    if not verified_pix_order_binding(order,record):
        return None
    event={"paid":"purchase_approved","refused":"purchase_refused",
           "refunded":"refund","chargedback":"chargeback","MED":"chargeback"}.get(order.get("status"))
    if event is None:
        return None
    identity={"origin":"provider_read","event":event,"order":order["id"],
              "status":order["status"],"paid_at":order.get("paidAt"),
              "refund_at":order.get("refundedAt"),"chargeback_at":order.get("chargedbackAt")}
    digest=hashlib.sha256(json.dumps(identity,sort_keys=True,separators=(",",":"),ensure_ascii=True).encode()).hexdigest()
    return {"digest":digest,"event":event,"order_id":order["id"],
            "status":order["status"],"offer_id":record["offer_id"],
            "subscription_id":None,"callback_hash":record["callback_hash"],
            "amount_cents":record["expected_cents"],"currency":"BRL"}


async def _claim_one():
    """Lease only the next read, never an entire batch that may time out."""
    async with support_session_factory()() as db:
        async with db.begin():
            rows=(await db.execute(text("""
                SELECT o.id,o.tenant_id,o.account_id,o.plan_code,o.offer_id,o.expected_cents,
                       o.callback_hash,o.external_order_id,o.state,o.created_at,o.expires_at
                FROM orion_prepaid_pix_orders o
                WHERE (o.provider_checked_at IS NULL OR o.provider_checked_at<now()-interval '10 minutes')
                  AND o.created_at<now()-interval '1 minute'
                  AND (o.state IN ('reserved','created','needs_review') OR
                       (o.state='paid' AND EXISTS (
                           SELECT 1 FROM orion_prepaid_pix_access a
                           WHERE a.tenant_id=o.tenant_id AND a.last_order_id=o.external_order_id)))
                ORDER BY o.provider_checked_at NULLS FIRST,o.created_at,o.id
                LIMIT 1 FOR UPDATE OF o SKIP LOCKED
            """))).mappings().first()
            if rows:
                row=rows
                await db.execute(text("UPDATE orion_prepaid_pix_orders SET provider_checked_at=now() WHERE id=:id"),{"id":row["id"]})
            return rows


async def recover_prepaid_provider_receipts(client,*,limit=10,on_receipt=None):
    """Fair, bounded polling with incremental durable receipt consumption."""
    if not isinstance(limit,int) or isinstance(limit,bool) or not 1<=limit<=30:
        raise ValueError("Invalid reconciliation batch size")
    receipts=[]
    for _ in range(limit):
        row=await _claim_one()
        if row is None:
            break
        try:
            if row["external_order_id"]:
                order=await client.get_order(row["external_order_id"])
            else:
                order=await _unknown_order(client,row)
            if order is None:
                continue
            bound=row if row["external_order_id"] else {**row,"external_order_id":order.get("id")}
            receipt=provider_receipt(order,bound)
        except (httpx.HTTPError,ValueError,TypeError,RuntimeError):
            # No customer identifiers or response bodies enter logs. A failed
            # read leaves the intent blocked and eligible for the next scan.
            continue
        if receipt:
            # Persist each completed proof before fetching another order. A
            # timeout later in the scan cannot discard already completed reads.
            if on_receipt is not None:
                await on_receipt(receipt)
            receipts.append(receipt)
    return receipts
