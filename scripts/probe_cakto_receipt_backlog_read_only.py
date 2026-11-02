"""Read-only aggregate status for financial receipts before changing billing flags.

No customer, company, order or subscription identifiers are returned or logged.
The app_support role retains RLS and queries only aggregate counts.
"""
from __future__ import annotations

from sqlalchemy import text
from app.catalog_api import support_session_factory

LIFECYCLE = frozenset({
    "subscription_renewed", "subscription_canceled",
    "purchase_refused", "refund", "chargeback"
})


async def totals() -> dict[str, int]:
    async with support_session_factory()() as db:
        async with db.begin():
            await db.execute(text("SET LOCAL statement_timeout='8000ms'"))
            rows=(await db.execute(text("""
                SELECT event_type, verification_state, count(*) AS n
                  FROM cakto_webhook_events
                 GROUP BY event_type,verification_state
            """))).all()
            active=(await db.execute(text("""
                SELECT count(*) FROM orion_cakto_bindings
            """))).scalar_one()
            pending_intents=(await db.execute(text("""
                SELECT count(*) FROM orion_cakto_checkout_intents
                 WHERE state='pending' AND expires_at>now()
            """))).scalar_one()
    output={
        "pending_first":0,"pending_lifecycle":0,"manual_review":0,
        "failed":0,"verified":0,"bindings":int(active),
        "pending_intents":int(pending_intents)
    }
    for event,state,count in rows:
        if state=="pending":
            bucket="pending_lifecycle" if event in LIFECYCLE else "pending_first"
            output[bucket]+=int(count)
        elif state in ("manual_review","failed","verified"):
            output[state]+=int(count)
    return output
