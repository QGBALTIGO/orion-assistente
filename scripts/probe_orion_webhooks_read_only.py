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
    parts = urlsplit(value)
    return (parts.scheme == "https" and parts.hostname == "orionagenda.pro"
            and parts.port is None and parts.path == "/webhooks/cakto"
            and not parts.query and not parts.fragment and not parts.username
            and not parts.password)


def coverage(offers: list, hooks: list, secret: str) -> dict:
    """Pure, fail-closed testable validator. Returns only generic counts."""
    matched = match_plans(offers)
    found = {item["code"]: item["offer_id"] for item in matched if item["found"]}
    product_for = {}
    for offer in offers:
        if not isinstance(offer, dict):
            continue
        for code, offer_id in found.items():
            if str(offer.get("id")) == offer_id:
                product_for[code] = _product_id(offer.get("product"))
    verified = 0
    if not secret or len(secret) < 32:
        return {"offer_matches": len(found), "webhook_ready": 0, "expected": 3}
    for code in found:
        product_id = product_for.get(code)
        if not product_id:
            continue
        events = set()
        for hook in hooks:
            if not isinstance(hook, dict) or hook.get("status") != "active" or not _exact_target(hook.get("url")):
                continue
            remote_secret = hook.get("fields")
            remote_secret = remote_secret.get("secret") if isinstance(remote_secret, dict) else None
            if not (isinstance(remote_secret, str) and
                    hmac.compare_digest(secret, remote_secret)):
                continue
            products = hook.get("products")
            if not isinstance(products, list) or product_id not in {
                _product_id(value) for value in products
            }:
                continue
            remote_events = hook.get("events")
            if not isinstance(remote_events, list):
                continue
            events.update(item.get("custom_id") for item in remote_events
                          if isinstance(item, dict) and isinstance(item.get("custom_id"), str))
        if REQUIRED_EVENTS <= events:
            verified += 1
    return {"offer_matches": len(found), "webhook_ready": verified, "expected": 3}


async def probe(client, offers: list):
    """Read-only provider calls; never include provider payloads in error logs."""
    hooks = []
    for page in range(1, 11):
        data = await client._get("/webhook/", page=page, limit=100)
        if not isinstance(data, dict) or not isinstance(data.get("results"), list):
            raise ValueError("unexpected webhook read contract")
        hooks.extend(data["results"])
        if not data.get("next"):
            return coverage(offers, hooks, os.getenv("CAKTO_WEBHOOK_SECRET", ""))
    raise ValueError("webhook pagination safety limit reached")
