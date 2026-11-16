"""Provider-only V2 webhook diagnosis. Read-only and secret-free output.

Runs inside the existing Railway pre-deploy probe with its protected Cakto OAuth
credential. This never mutates webhooks or emits buyer/secret/offer IDs.
"""
from __future__ import annotations
import hmac
import os
from app.cakto_api import match_plans
from scripts.probe_orion_webhooks_read_only import (
    REQUIRED_EVENTS, _exact_target, _product_id,
)


def analyze(offers: list[dict], history: list[dict], configured_secret: str) -> dict:
    matched={str(x["offer_id"]) for x in match_plans(offers) if x["found"] and x["offer_id"]}
    product_ids={_product_id(x.get("product")) for x in offers
                 if isinstance(x,dict) and str(x.get("id")) in matched}
    product_ids.discard(None)
    active=[]
    seen=set()
    for record in history:
        if not isinstance(record,dict):
            continue
        app=record.get("app")
        if (not isinstance(app,dict) or app.get("status")!="active"
                or not _exact_target(app.get("url"))
                or not _exact_target(record.get("url"))):
            continue
        products=app.get("products")
        if not isinstance(products,list) or not any(_product_id(p) in product_ids for p in products):
            continue
        ident=app.get("id")
        # Each active app can occur in many delivery records.
        key=str(ident) if isinstance(ident,(str,int)) else str(id(app))
        if key in seen:
            continue
        seen.add(key)
        secret=app.get("fields",{}).get("secret") if isinstance(app.get("fields"),dict) else None
        keys_ready=isinstance(secret,str) and len(secret)>=32
        events=app.get("events")
        included={e.get("custom_id") for e in events if isinstance(e,dict)} if isinstance(events,list) else set()
        active.append({
            "secret_available":keys_ready,
            "matches_env":bool(keys_ready and isinstance(configured_secret,str)
                               and hmac.compare_digest(secret,configured_secret)),
            "required_events":len(REQUIRED_EVENTS & included),
            "all_events":REQUIRED_EVENTS <= included,
        })
    return {
        "active_target_product_apps":len(active),
        "remote_secret_readable":sum(x["secret_available"] for x in active),
        "matches_current_env":sum(x["matches_env"] for x in active),
        "full_six_event_app_coverage":sum(x["all_events"] for x in active),
        "max_event_count":max([x["required_events"] for x in active],default=0),
    }


async def probe(client, offers: list[dict]) -> dict:
    history=[]
    for page in range(1,5):
        data=await client._get(
            "/webhook/event_history/",page=page,limit=100,search="orionagenda.pro",
        )
        if not isinstance(data,dict) or not isinstance(data.get("results"),list):
            raise ValueError("unrecognized_history_contract")
        history.extend(data["results"])
        if len(history)>400:
            raise ValueError("history_limit")
        if not data.get("next"):
            return analyze(offers,history,os.getenv("CAKTO_WEBHOOK_SECRET",""))
    raise ValueError("history_pagination_limit")
