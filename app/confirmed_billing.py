"""Record provider-verified payment periods and financial holds."""
import os
from datetime import timedelta
from decimal import Decimal, InvalidOperation

import httpx
from sqlalchemy import text

from app.catalog_api import support_session_factory


def enabled():
    return os.getenv("ORION_BARBER_PLAN_CYCLE", "0") == "1"


async def tracked(db, tenant_id):
    return enabled() and (await db.execute(text("""
        SELECT EXISTS (SELECT 1 FROM business_onboarding
            WHERE tenant_id=:tenant AND configuration->>'niche'='barbearia')
    """), {"tenant": tenant_id})).scalar_one()


async def current_order(db, tenant_id, order_id, channel):
    return (await db.execute(text("""
        SELECT period_ends_at,hold,paid_at FROM orion_confirmed_billing_state
        WHERE tenant_id=:tenant AND order_id=:order AND payment_channel=:channel
    """), {"tenant": tenant_id, "order": order_id, "channel": channel})).mappings().one_or_none()


async def record(db, tenant_id, order_id, channel, paid_at, amount, *, start=None, end=None, hold=None, next_payment=None):
    await db.execute(text("""
        INSERT INTO orion_confirmed_billing_state AS b
            (tenant_id,order_id,payment_channel,paid_at,amount_cents,period_starts_at,period_ends_at,hold,next_payment_at)
        VALUES (:tenant,:order,:channel,:paid,:amount,:start,:end,:hold,:next_payment)
        ON CONFLICT (tenant_id) DO UPDATE SET order_id=EXCLUDED.order_id,
            payment_channel=EXCLUDED.payment_channel,paid_at=EXCLUDED.paid_at,amount_cents=EXCLUDED.amount_cents,
            period_starts_at=CASE WHEN b.order_id=EXCLUDED.order_id
                THEN coalesce(b.period_starts_at,EXCLUDED.period_starts_at) ELSE EXCLUDED.period_starts_at END,
            period_ends_at=CASE WHEN b.order_id=EXCLUDED.order_id
                THEN coalesce(b.period_ends_at,EXCLUDED.period_ends_at) ELSE EXCLUDED.period_ends_at END,
            hold=EXCLUDED.hold,next_payment_at=EXCLUDED.next_payment_at,checked_at=now()
    """), {"tenant": tenant_id, "order": order_id, "channel": channel, "paid": paid_at,
           "amount": amount, "start": start, "end": end, "hold": hold, "next_payment": next_payment})


async def recover_withdrawn_refunds(client):
    """Only an authoritative paid snapshot of the held order can release its request."""
    if not enabled():
        return
    from app.cakto_api import ORDER_ID
    from app.cakto_subscription_lifecycle import _order_paid_at
    from app.plan_permissions import lock_plan

    async with support_session_factory()() as db:
        async with db.begin():
            rows = (await db.execute(text("""
                SELECT tenant_id,order_id,paid_at,amount_cents,hold FROM orion_confirmed_billing_state
                WHERE hold IN ('refund_requested','chargeback') AND checked_at<now()-interval '5 minutes'
                  AND paid_at>now()-interval '120 days'
                ORDER BY checked_at LIMIT 3 FOR UPDATE SKIP LOCKED
            """))).mappings().all()
            current_proofs = []
            for row in rows:
                checked = (await db.execute(text("""
                    UPDATE orion_confirmed_billing_state SET checked_at=clock_timestamp()
                    WHERE tenant_id=:tenant AND order_id=:order
                    RETURNING checked_at
                """), {"tenant": row["tenant_id"], "order": row["order_id"]})).scalar_one()
                current_proofs.append({**row, "checked_at": checked})
    for row in current_proofs:
        if not ORDER_ID.fullmatch(row["order_id"]):
            continue
        try:
            order = await client.get_order(row["order_id"])
            amount = Decimal(str(order.get("amount"))) * 100
            if (order.get("id") != row["order_id"] or order.get("currency") != "BRL"
                    or order.get("status") != "paid" or not amount.is_finite()
                    or amount != row["amount_cents"] or _order_paid_at(order) != row["paid_at"]):
                continue
            if row["hold"] == "chargeback":
                dispute = await client._get("/disputes/" + row["order_id"] + "/")
                if dispute.get("status") != "won":
                    continue
        except (httpx.HTTPError, RuntimeError, InvalidOperation, TypeError, ValueError, AttributeError):
            continue
        async with support_session_factory()() as db:
            async with db.begin():
                await lock_plan(db, row["tenant_id"], exclusive=True)
                await db.execute(text("""
                    UPDATE orion_confirmed_billing_state SET hold=NULL,checked_at=now()
                    WHERE tenant_id=:tenant AND order_id=:order AND paid_at=:paid AND hold=:hold
                      AND checked_at=:checked
                """), {"tenant": row["tenant_id"], "order": row["order_id"],
                       "paid": row["paid_at"], "hold": row["hold"], "checked": row["checked_at"]})
