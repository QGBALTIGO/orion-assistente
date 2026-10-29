"""Opt-in, one-time synthetic Cakto webhook test. NEVER creates a payment.

Only execute when ORION_CAKTO_SYNTHETIC_WEBHOOK_TEST='1'. Calls the Cakto
event_test endpoint for checkout_abandonment, an event without a charge/order.
Requires a unique active provider V2 app with exact Orion URL and Orion product.
Never logs payload, app ID, client identity, token, secrets or customer data.
"""
from __future__ import annotations
import asyncio
import hmac
import os
from datetime import datetime, timezone, timedelta

import httpx
from app.cakto_api import BASE, CaktoClient
from scripts.probe_orion_payment_methods_read_only import product_reference
from scripts.probe_orion_webhooks_read_only import _exact_target, _product_id

TEST_EVENT = "checkout_abandonment"
MAX_HISTORY_PAGES = 4
MAX_HISTORY_ENTRIES = 400


async def _history(client: CaktoClient) -> list:
    result = []
    for page in range(1, MAX_HISTORY_PAGES+1):
        response = await client._get(
            "/webhook/event_history/",page=page,limit=100,
            search="orionagenda.pro",
        )
        if not isinstance(response,dict) or not isinstance(response.get("results"),list):
            raise ValueError("Unexpected Cakto history response")
        result.extend(response["results"])
        if len(result)>MAX_HISTORY_ENTRIES:
            raise ValueError("Cakto history exceeded safety bound")
        if not response.get("next"):
            return result
    raise ValueError("Cakto history exceeded page limit")


def eligible_app_id(history: list, product_id: str) -> int | None:
    """Choose exactly one existing V2 app, never create another webhook."""
    candidates=set()
    for record in history:
        if not isinstance(record,dict):
            continue
        app=record.get("app")
        if not isinstance(app,dict):
            continue
        if (app.get("status")!="active"
                or not _exact_target(app.get("url"))
                or not _exact_target(record.get("url"))):
            continue
        products=app.get("products")
        if not isinstance(products,list):
            continue
        if product_id not in {_product_id(x) for x in products}:
            continue
        ident=app.get("id")
        if type(ident) is int and 0 < ident < 2147483648:
            candidates.add(ident)
    return next(iter(candidates)) if len(candidates)==1 else None


def acknowledged_test_delivery(history: list, app_id: int,
                               secret: str, since: datetime) -> bool:
    """The provider confirms HTTP 2xx and the rotated secret for a new event."""
    if not isinstance(secret,str) or len(secret)<32 or since.tzinfo is None:
        return False
    for record in history:
        if not isinstance(record,dict) or record.get("event_id")!=TEST_EVENT:
            continue
        app=record.get("app")
        payload=record.get("payload")
        if not isinstance(app,dict) or not isinstance(payload,dict):
            continue
        if (app.get("id")!=app_id or app.get("status")!="active"
                or not _exact_target(app.get("url"))
                or not _exact_target(record.get("url"))
                or type(record.get("event_status")) is not int
                or not 200<=record["event_status"]<300):
            continue
        fields=app.get("fields")
        app_secret=fields.get("secret") if isinstance(fields,dict) else None
        body_secret=payload.get("secret")
        if (not isinstance(app_secret,str) or not isinstance(body_secret,str)
                or not hmac.compare_digest(secret,app_secret)
                or not hmac.compare_digest(secret,body_secret)
                or payload.get("event")!=TEST_EVENT):
            continue
        sent=record.get("sentAt") or record.get("dispatchedAt")
        if not isinstance(sent,str):
            continue
        try:
            received=datetime.fromisoformat(sent.replace("Z","+00:00"))
        except ValueError:
            continue
        if received.tzinfo and since-timedelta(seconds=5)<=received<=datetime.now(timezone.utc)+timedelta(minutes=5):
            return True
    return False


async def run() -> int:
    if os.getenv("ORION_CAKTO_SYNTHETIC_WEBHOOK_TEST","0")!="1":
        print("cakto_synthetic_webhook_test_not_requested",flush=True)
        return 0
    # Do not run on unconfigured or client-only stacks.
    try:
        client=CaktoClient()
        offers=await client.list_offers()
        product=product_reference(offers)
        if not product:
            print("cakto_synthetic_webhook_test_product_unverified",flush=True)
            return 0
        initial=await _history(client)
        app_id=eligible_app_id(initial,product)
        if app_id is None:
            print("cakto_synthetic_webhook_test_target_not_unique",flush=True)
            return 0
        since=datetime.now(timezone.utc)
        bearer=await client.token()
        async with httpx.AsyncClient(timeout=16,follow_redirects=False) as http:
            response=await http.post(
                BASE+"/webhook/event_test/"+str(app_id)+"/",
                params={"event_id":TEST_EVENT},
                headers={"Authorization":"Bearer "+bearer},
            )
            response.raise_for_status()
        # Only the explicit synthetic abandonment event is dispatched. No
        # customer order, Pix QR, card token, payment or subscription is created.
        for _ in range(6):
            await asyncio.sleep(2)
            fresh=await _history(client)
            if acknowledged_test_delivery(
                    fresh,app_id,os.getenv("CAKTO_WEBHOOK_SECRET",""),since):
                print("cakto_rotated_webhook_synthetic_delivery_verified",flush=True)
                return 0
        print("cakto_rotated_webhook_synthetic_delivery_not_verified",flush=True)
    except Exception:
        # Provider's error body may contain PII. Do not print it.
        print("cakto_rotated_webhook_synthetic_test_unavailable",flush=True)
    return 0


if __name__=="__main__":
    raise SystemExit(asyncio.run(run()))
