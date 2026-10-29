"""Read-only Cakto delivery history audit for panel-managed V2 webhooks.

The public webhook list may not expose every panel-managed delivery. Query
provider history rather than trusting a single webhook inventory endpoint.
Never log or persist raw history: it may contain customer or payment details.
No financial mutations, no provider POST, and no real charges.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hmac
import os

from app.cakto_api import match_plans
from scripts.probe_orion_webhooks_read_only import (
    REQUIRED_EVENTS, _exact_target, _product_id,
)


def _recent(value: object, now: datetime) -> bool:
    if not isinstance(value, str):
        return False
    try:
        recorded = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if recorded.tzinfo is None:
            return False
        return now - timedelta(hours=24) <= recorded <= now + timedelta(minutes=5)
    except (ValueError, TypeError, OverflowError):
        return False


def history_coverage(offers: list, history: list, secret: str,
                     *, now: datetime | None = None) -> dict:
    """Only counts. A positive result needs recent, signed, provider-recorded 2xx.

    A V2 webhook is registered for a product, not once per offer. Three offers
    attached to one product can all be covered by the same real delivery.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("timezone-aware reference is required")
    matched = match_plans(offers)
    offer_ids = {entry["code"]: str(entry["offer_id"]) for entry in matched
                 if entry["found"] and entry["offer_id"]}
    product_for = {}
    for offer in offers:
        if not isinstance(offer, dict):
            continue
        for code, offer_id in offer_ids.items():
            if str(offer.get("id")) == offer_id:
                product_for[code] = _product_id(offer.get("product"))

    valid = set()
    observed_events = {}
    targeted = 0
    success = 0
    secret_matched = 0
    recent = 0
    for item in history:
        if not isinstance(item, dict):
            continue
        app = item.get("app")
        if not isinstance(app, dict):
            continue
        if not (_exact_target(item.get("url"))
                and _exact_target(app.get("url"))):
            continue
        targeted += 1
        if app.get("status") != "active":
            continue
        code = item.get("event_status")
        if type(code) is not int or not 200 <= code < 300:
            continue
        success += 1
        payload = item.get("payload")
        if not isinstance(payload, dict):
            continue
        field = app.get("fields")
        app_secret = field.get("secret") if isinstance(field, dict) else None
        body_secret = payload.get("secret")
        if (not isinstance(secret, str) or len(secret) < 32
                or not isinstance(app_secret, str)
                or not isinstance(body_secret, str)
                or not hmac.compare_digest(secret, app_secret)
                or not hmac.compare_digest(secret, body_secret)):
            continue
        secret_matched += 1
        if not (_recent(item.get("sentAt"), now)
                or _recent(item.get("dispatchedAt"), now)):
            continue
        recent += 1
        event = item.get("event_id")
        if (not isinstance(event, str) or event not in REQUIRED_EVENTS
                or payload.get("event") != event):
            continue
        products = app.get("products")
        if not isinstance(products, list):
            continue
        linked = {_product_id(x) for x in products}
        for code, product in product_for.items():
            if product and product in linked:
                valid.add(code)
                observed_events.setdefault(code, set()).add(event)
    complete = sum(1 for c in offer_ids
                   if REQUIRED_EVENTS <= observed_events.get(c, set()))
    return {
        "offer_matches": len(offer_ids),
        "targeted_deliveries": targeted,
        "http_2xx_deliveries": success,
        "secret_matched_deliveries": secret_matched,
        "recent_verified_deliveries": recent,
        "product_coverage": len(valid),
        "required_event_coverage": complete,
        "webhook_ready": complete,
        "expected": 3,
    }


async def probe(client, offers: list, *, now: datetime | None = None):
    """Fetch only history for our webhook target, discard provider payloads."""
    history = []
    for page in range(1, 5):
        data = await client._get(
            "/webhook/event_history/", page=page, limit=100,
            search="orionagenda.pro",
        )
        if not isinstance(data, dict) or not isinstance(data.get("results"), list):
            raise ValueError("Cakto history response not recognized")
        history.extend(data["results"])
        if len(history) > 400:
            raise ValueError("Cakto history exceeded safety bound")
        if not data.get("next"):
            return history_coverage(
                offers, history, os.getenv("CAKTO_WEBHOOK_SECRET", ""), now=now,
            )
    raise ValueError("Cakto history pagination exceeded safety bound")
