"""Read-only audit of Orion's active Cakto webhooks, without leaking secrets.

Does NOT create, update, send, replay or delete financial events. Status from a
configured webhook is not proof of HMAC delivery: real test validation is separate.
"""
from __future__ import annotations

import hmac
import os
from urllib.parse import urlsplit

from app.cakto_api import match_plans

TARGET = "https://orionagenda.pro/webhooks/cakto"
REQUIRED_EVENTS = frozenset({
    "purchase_approved", "purchase_refused", "subscription_renewed",
    "subscription_canceled", "refund", "chargeback",
})


def _product_id(value):
    if isinstance(value, str):
        return value if 1 <= len(value) <= 80 else None
    if isinstance(value, dict):
        ident = value.get("id")
        return ident if isinstance(ident, str) and 1 <= len(ident) <= 80 else None
    return None


def _exact_target(value):
    if not isinstance(value, str):
        return False
    try:
        parts = urlsplit(value)
        return (parts.scheme == "https" and parts.hostname == "orionagenda.pro"
                and parts.port is None and parts.path == "/webhooks/cakto"
                and not parts.query and not parts.fragment and not parts.username
                and not parts.password)
    except ValueError:
        return False


def detailed_coverage(offers: list, hooks: list, secret: str) -> dict:
    """Conservative per-offer coverage; expose counts only, never IDs or credentials.

    One product may own all three subscription offers. Its single active webhook
    can cover all three offers when URL, configured events and secret also match.
    Read-only diagnostics separate these independent conditions.
    """
    matched = match_plans(offers)
    found = {entry["code"]: str(entry["offer_id"])
             for entry in matched if entry["found"] and entry["offer_id"]}
    product_for = {}
    for offer in offers:
        if not isinstance(offer, dict):
            continue
        for code, offer_id in found.items():
            if str(offer.get("id")) == offer_id:
                product_for[code] = _product_id(offer.get("product"))

    target = [hook for hook in hooks
              if isinstance(hook, dict) and hook.get("status") == "active"
              and _exact_target(hook.get("url"))]
    links = 0
    linked = 0
    secret_ok = 0
    event_ok = 0
    ready = 0
    for code in found:
        product_id = product_for.get(code)
        if not product_id:
            continue
        links += 1
        applicable = [
            hook for hook in target if isinstance(hook.get("products"), list)
            and product_id in {_product_id(item) for item in hook["products"]}
        ]
        if not applicable:
            continue
        linked += 1
        matching = []
        for hook in applicable:
            details = hook.get("fields")
            remote_secret = details.get("secret") if isinstance(details, dict) else None
            if (isinstance(secret, str) and len(secret) >= 32
                    and isinstance(remote_secret, str)
                    and hmac.compare_digest(secret, remote_secret)):
                matching.append(hook)
        if matching:
            secret_ok += 1
        subscribed = set()
        for hook in applicable:
            event_list = hook.get("events")
            if not isinstance(event_list, list):
                continue
            subscribed.update(event.get("custom_id") for event in event_list
                              if isinstance(event, dict)
                              and isinstance(event.get("custom_id"), str))
        if REQUIRED_EVENTS <= subscribed:
            event_ok += 1
        verified_events = set()
        for hook in matching:
            event_list = hook.get("events")
            if isinstance(event_list, list):
                verified_events.update(event.get("custom_id") for event in event_list
                                       if isinstance(event, dict)
                                       and isinstance(event.get("custom_id"), str))
        if REQUIRED_EVENTS <= verified_events:
            ready += 1

    return {
        "offer_matches": len(found), "webhook_ready": ready, "expected": 3,
        "offer_product_links": links,
        "distinct_products": len(set(value for value in product_for.values() if value)),
        "active_target_hooks": len(target),
        "product_coverage": linked,
        "secret_coverage": secret_ok,
        "event_coverage": event_ok,
    }


def coverage(offers: list, hooks: list, secret: str) -> dict:
    """Preserve the stable three-field validation contract."""
    result = detailed_coverage(offers, hooks, secret)
    return {field: result[field] for field in ("offer_matches", "webhook_ready", "expected")}


async def probe(client, offers: list, *, detailed: bool = False):
    """Read-only provider calls; never include provider payloads in error logs."""
    hooks = []
    for page in range(1, 11):
        data = await client._get("/webhook/", page=page, limit=100)
        if not isinstance(data, dict) or not isinstance(data.get("results"), list):
            raise ValueError("unexpected webhook read contract")
        hooks.extend(data["results"])
        if not data.get("next"):
            verifier = detailed_coverage if detailed else coverage
            return verifier(offers, hooks, os.getenv("CAKTO_WEBHOOK_SECRET", ""))
    raise ValueError("webhook pagination safety limit reached")
