"""Read-only verification of the Orion subscription product payment methods.

No payment is created. Only non-sensitive, aggregate booleans are returned.
The product is identified from three exactly matched monthly offers; a single
product must own all three offers before payment readiness is considered.
"""
from __future__ import annotations

from app.cakto_api import match_plans
from scripts.probe_orion_webhooks_read_only import _product_id


def product_reference(offers: list) -> str | None:
    matched = match_plans(offers)
    if len(matched) != 3 or any(not m["found"] or m["ambiguous"] for m in matched):
        return None
    found = {str(m["offer_id"]) for m in matched}
    products = {
        _product_id(offer.get("product"))
        for offer in offers
        if isinstance(offer, dict) and str(offer.get("id")) in found
    }
    if len(products) != 1 or None in products:
        return None
    return next(iter(products))


def check_product(product: object) -> dict:
    if not isinstance(product, dict):
        return {"methods_confirmed": False, "pix_auto": False, "card": False}
    methods = product.get("paymentMethods")
    if not isinstance(methods, list):
        return {"methods_confirmed": False, "pix_auto": False, "card": False}
    active = product.get("status") == "active"
    accepted = {m for m in methods if isinstance(m, str)}
    pix = "pix_auto" in accepted
    card = "credit_card" in accepted or "threeDs" in accepted
    return {"methods_confirmed": bool(active and pix and card),
            "pix_auto": bool(active and pix),
            "card": bool(active and card)}


async def probe(client, offers: list) -> dict:
    product_id = product_reference(offers)
    if not product_id:
        return {"offers_one_product": False, "methods_confirmed": False,
                "pix_auto": False, "card": False}
    # Cakto public API product retrieval is GET only.
    product = await client._get("/products/" + product_id + "/")
    return {"offers_one_product": True, **check_product(product)}
