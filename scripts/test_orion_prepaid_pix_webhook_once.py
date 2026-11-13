"""One-time synthetic test for Orion prepaid Pix V1 webhook.

No real payments, customer records or subscriptions. Sends only a provider
checkout_abandonment TEST event to the already-owned, unique dedicated webhook.
All output is aggregate status, no IDs, PII or provider bodies.
"""
from __future__ import annotations

import asyncio
from datetime import datetime,timedelta,timezone
import hmac
import os
import httpx

from app.cakto_api import CaktoClient,BASE
from app.prepaid_pix_api import (
    PIX_NAMES,prepaid_offers,product_id,PIX_EVENTS,TARGET,
)

TEST="checkout_abandonment"


async def _find_hook(client,products):
    hooks=[]
    for page in range(1,11):
        result=await client._get("/webhook/",page=page,limit=100)
        if not isinstance(result,dict) or not isinstance(result.get("results"),list):
            raise ValueError("webhook_list_invalid")
        hooks.extend(result["results"])
        if not result.get("next"):
            break
    else:
        raise ValueError("webhook_list_incomplete")
    secret=os.getenv("CAKTO_PIX_WEBHOOK_SECRET","")
    found=[]
    for hook in hooks:
        if (not isinstance(hook,dict) or hook.get("url")!=TARGET
                or hook.get("status")!="active"):
            continue
        fields=hook.get("fields")
        remote=fields.get("secret") if isinstance(fields,dict) else None
        if (not isinstance(remote,str) or len(remote)<32
                or not hmac.compare_digest(secret,remote)):
            continue
        linked={product_id(x) for x in hook.get("products",[])
                } if isinstance(hook.get("products"),list) else set()
        subscribed={x.get("custom_id") for x in hook.get("events",[])
                    if isinstance(x,dict)} if isinstance(hook.get("events"),list) else set()
        ident=hook.get("id")
        if products.issubset(linked) and PIX_EVENTS.issubset(subscribed) and type(ident) is int and ident>0:
            found.append(ident)
    if len(found)!=1:
        raise ValueError("prepaid_webhook_target_ambiguous")
    return found[0]


async def _event_delivered(client,hook_id,started):
    secret=os.getenv("CAKTO_PIX_WEBHOOK_SECRET","")
    for page in range(1,5):
        data=await client._get("/webhook/event_history/",page=page,limit=100,
                               search="orionagenda.pro")
        if not isinstance(data,dict) or not isinstance(data.get("results"),list):
            raise ValueError("history_unavailable")
        for rec in data["results"]:
            if not isinstance(rec,dict) or rec.get("event_id")!=TEST:
                continue
            app=rec.get("app")
            payload=rec.get("payload")
            if not isinstance(app,dict) or not isinstance(payload,dict):
                continue
            if (app.get("id")!=hook_id or app.get("url")!=TARGET
                    or rec.get("url")!=TARGET or app.get("status")!="active"
                    or type(rec.get("event_status")) is not int
                    or not 200<=rec["event_status"]<300):
                continue
            fields=app.get("fields")
            first=fields.get("secret") if isinstance(fields,dict) else None
            second=payload.get("secret")
            if (not isinstance(first,str) or not isinstance(second,str)
                    or not hmac.compare_digest(secret,first)
                    or not hmac.compare_digest(secret,second)
                    or payload.get("event")!=TEST):
                continue
            raw_time=rec.get("sentAt") or rec.get("dispatchedAt")
            if not isinstance(raw_time,str):
                continue
            try: when=datetime.fromisoformat(raw_time.replace("Z","+00:00"))
            except ValueError: continue
            if when.tzinfo and started-timedelta(seconds=5)<=when<=datetime.now(timezone.utc)+timedelta(minutes=5):
                return True
        if not data.get("next"):
            return False
    return False


async def run():
    if os.getenv("ORION_PIX_SYNTHETIC_TEST_ONCE","0")!="1":
        return
    if os.getenv("ORION_PIX_PREPAID_ENABLED","0")=="1":
        print("orion_pix_test_refused_live_checkout",flush=True)
        return
    stage="initial"
    try:
        client=CaktoClient()
        offers=prepaid_offers(await client.list_offers())
        product_refs={product_id(v.get("product")) for v in offers.values()}
        if None in product_refs or len(product_refs)!=1:
            raise ValueError("prepaid_product_not_unique")
        stage="find"
        webhook_id=await _find_hook(client,product_refs)
        started=datetime.now(timezone.utc)
        stage="token"
        bearer=await client.token()
        stage="post_synthetic"
        async with httpx.AsyncClient(timeout=16,follow_redirects=False) as http:
            response=await http.post(
                BASE+"/webhook/event_test/"+str(webhook_id)+"/",
                params={"event":"checkout_abandonment"},
                headers={"Authorization":"Bearer "+bearer},
            )
            response.raise_for_status()
        stage="verify_delivery"
        for attempt in range(8):
            await asyncio.sleep(2)
            if await _event_delivered(client,webhook_id,started):
                print("orion_pix_synthetic_webhook_verified",flush=True)
                return
        print("orion_pix_synthetic_webhook_not_verified",flush=True)
    except httpx.HTTPStatusError as exc:
        # Never log provider bodies or webhook IDs.
        print("orion_pix_synthetic_webhook_http_error="
              +stage+":"+str(exc.response.status_code),flush=True)
    except Exception as exc:
        # Only exception classes, never strings containing provider data.
        print("orion_pix_synthetic_webhook_unavailable="
              +stage+":"+type(exc).__name__,flush=True)


if __name__=="__main__":
    asyncio.run(run())
