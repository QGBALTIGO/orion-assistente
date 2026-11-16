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
KNOWN_EVENTS = LIFECYCLE | {"purchase_approved"}


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
            purchase_links=(await db.execute(text("""
                SELECT
                    count(DISTINCT e.delivery_digest) FILTER
                        (WHERE e.callback_hash IS NOT NULL) AS has_callback,
                    count(DISTINCT e.delivery_digest) FILTER
                        (WHERE i.id IS NOT NULL AND i.state='pending'
                               AND i.expires_at>now()) AS matches_valid_intent,
                    count(DISTINCT e.delivery_digest) FILTER
                        (WHERE i.id IS NOT NULL AND
                               (i.state!='pending' OR i.expires_at<=now())) AS stale_intent,
                    count(DISTINCT e.delivery_digest) FILTER
                        (WHERE i.id IS NULL) AS no_matching_intent
                  FROM cakto_webhook_events e
                  LEFT JOIN orion_cakto_checkout_intents i
                    ON i.callback_hash=e.callback_hash
                 WHERE e.event_type='purchase_approved'
                   AND e.verification_state='failed'
            """))).one()
    output={
        "pending_first":0,"pending_lifecycle":0,"manual_review":0,
        "failed":0,"verified":0,"bindings":int(active),
        "pending_intents":int(pending_intents),
        # Only aggregate counts. In particular, never expose callback hashes.
        "failed_purchase_with_callback":int(purchase_links.has_callback or 0),
        "failed_purchase_valid_intent":int(purchase_links.matches_valid_intent or 0),
        "failed_purchase_stale_intent":int(purchase_links.stale_intent or 0),
        "failed_purchase_no_matching_intent":int(purchase_links.no_matching_intent or 0)
    }
    # Only a fixed allowlist of event types. Counts cannot expose customer
    # data or external identifiers, even if unexpected event labels exist.
    for event in sorted(KNOWN_EVENTS):
        output["failed_"+event]=0
        output["pending_"+event]=0
        output["review_"+event]=0
    for event,state,count in rows:
        if event in KNOWN_EVENTS and state in ("failed", "pending", "manual_review"):
            prefix={"failed":"failed_", "pending":"pending_",
                    "manual_review":"review_"}[state]
            output[prefix+event]+=int(count)
        if state=="pending":
            bucket="pending_lifecycle" if event in LIFECYCLE else "pending_first"
            output[bucket]+=int(count)
        elif state in ("manual_review","failed","verified"):
            output[state]+=int(count)
    return output
