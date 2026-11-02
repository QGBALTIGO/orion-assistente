"""Read-only diagnostic: is a dashboard-created Cakto V2 webhook addressable by API?

The provider's /webhook/ inventory excludes some dashboard V2 apps. A GET by
the unique ID is harmless; never use PUT/POST/DELETE from this diagnostic.
Return booleans only: no webhook IDs, authentication fields, or buyer details.
"""
from __future__ import annotations

import httpx
from scripts.test_cakto_webhook_rotation_once import eligible_app_id
from scripts.probe_orion_payment_methods_read_only import product_reference
from scripts.probe_orion_webhooks_read_only import _exact_target


async def check(client, offers: list[dict]) -> str:
    product=product_reference(offers)
    if not product:
        return "product_unverified"
    history=[]
    for page in range(1,5):
        data=await client._get("/webhook/event_history/",page=page,limit=100,
                               search="orionagenda.pro")
        if not isinstance(data,dict) or not isinstance(data.get("results"),list):
            return "history_unavailable"
        history.extend(data["results"])
        if len(history)>400:
            return "history_over_limit"
        if not data.get("next"):
            break
    else:
        return "history_incomplete"
    app_id=eligible_app_id(history,product)
    if app_id is None:
        return "app_ambiguous"
    try:
        result=await client._get("/webhook/"+str(app_id)+"/")
    except httpx.HTTPStatusError as err:
        if err.response.status_code==404:
            return "not_in_public_webhook_api"
        if err.response.status_code in (401,403):
            return "permission_missing"
        return "provider_error"
    except (ValueError,TypeError,RuntimeError,httpx.HTTPError):
        return "provider_error"
    if not isinstance(result,dict) or not _exact_target(result.get("url")):
        return "not_same_webhook"
    if result.get("status")!="active":
        return "not_active"
    if str(result.get("id"))!=str(app_id):
        return "id_mismatch"
    return "readable_by_public_api"
