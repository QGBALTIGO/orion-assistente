"""One-time sealed transfer of Cakto's existing V2 webhook key.

NO plaintext key in source, logs, model tools, or GitHub. The public RSA key
below encrypts the provider secret; its private half stays outside Railway.
Runs only as part of the existing read-only Cakto pre-deploy probe.
Does not create webhooks, payments, orders, or subscriptions.
"""
from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timedelta, timezone

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from app.cakto_api import match_plans
from scripts.probe_orion_webhooks_read_only import _exact_target, _product_id

PUBLIC_KEY = b"""-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAmxzSmoFsWRQLCK0pvNrW
YuAaKei2QlofNg0Oj/eRHdKLGyUMdqDAGCTIh9JmDOujbaimgV3XU9rmpIBgZRqU
QuGLPfDANQF8crvMkays72rSAT/WkYOhtklbTZphWmkmSdwExjZAx/cfyExhG6zg
fDdo4oehnAc4g0LMrFXxlg3DCCZIxatEWXdErjppNh6URhMdYUTCfLwCSZJWQzNq
NeNS29N3xnUO6eR7dokGJh7hg5r0wGF4oSSVDJsMitrgqlypZCOAX+WToXi9OWKh
hRotAhf7AP9cZQa+t9hCVbkueFKiI6beSriaLWycg+H0tWj6uU4v5N1exTHg1VUB
xQIDAQAB
-----END PUBLIC KEY-----"""
LABEL = b"orion-cakto-2026-11-provider-key"


def select_secret(offers: list, history: list, now: datetime) -> str:
    matched = {str(v["offer_id"]) for v in match_plans(offers)
               if v["found"] and v.get("offer_id")}
    if len(matched) != 3:
        raise RuntimeError("orion_offers_not_unique")
    products = {_product_id(v.get("product")) for v in offers
                if isinstance(v, dict) and str(v.get("id")) in matched}
    products.discard(None)
    if len(products) != 1:
        raise RuntimeError("orion_product_not_unique")
    keys_by_app: dict[str,set[str]] = {}
    ids=set()
    cutoff = now - timedelta(days=90)
    for row in history:
        if not isinstance(row,dict):
            continue
        app=row.get("app")
        if (not isinstance(app,dict) or app.get("status")!="active"
                or not _exact_target(app.get("url"))
                or not _exact_target(row.get("url"))
                or not isinstance(app.get("products"),list)
                or not any(_product_id(x) in products for x in app["products"])):
            continue
        timestamp = row.get("sentAt") or row.get("dispatchedAt")
        if not isinstance(timestamp,str):
            continue
        try:
            observed=datetime.fromisoformat(timestamp.replace("Z","+00:00"))
        except ValueError:
            continue
        if observed.tzinfo is None or not cutoff<=observed<=now+timedelta(minutes=5):
            continue
        ident=app.get("id")
        if type(ident) not in (int,str):
            raise RuntimeError("unidentifiable_app")
        secret=app.get("fields",{}).get("secret") if isinstance(app.get("fields"),dict) else None
        if not isinstance(secret,str) or not 32<=len(secret)<=200:
            raise RuntimeError("provider_key_unavailable")
        ident=str(ident)
        ids.add(ident)
        keys_by_app.setdefault(ident,set()).add(secret)
    if len(ids)!=1 or len(keys_by_app)!=1:
        raise RuntimeError("ambiguous_provider_app")
    keys=next(iter(keys_by_app.values()))
    if len(keys)!=1:
        raise RuntimeError("rotated_provider_key_ambiguous")
    return next(iter(keys))


async def export():
    from app.cakto_api import CaktoClient
    client=CaktoClient()
    if not client.configured:
        print("cakto_provider_key_sealed_unavailable:oauth",flush=True)
        return
    try:
        offers=await client.list_offers()
        history=[]
        for page in range(1,5):
            page_data=await client._get(
                "/webhook/event_history/",page=page,limit=100,
                search="orionagenda.pro",
            )
            if not isinstance(page_data,dict) or not isinstance(page_data.get("results"),list):
                raise RuntimeError("history_contract")
            history.extend(page_data["results"])
            if len(history)>400:
                raise RuntimeError("history_limit")
            if not page_data.get("next"):
                break
        else:
            raise RuntimeError("pagination_incomplete")
        secret=select_secret(offers,history,datetime.now(timezone.utc))
        key=serialization.load_pem_public_key(PUBLIC_KEY)
        ciphertext=key.encrypt(
            secret.encode(),
            padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()),
                         algorithm=hashes.SHA256(),label=LABEL),
        )
        print("cakto_sealed_provider_key_v1:"
              + base64.urlsafe_b64encode(ciphertext).decode(),flush=True)
        print("cakto_provider_key_sealed_success",flush=True)
    except Exception:
        # No exception, order identifiers or provider responses in logs.
        print("cakto_provider_key_sealed_unavailable:preconditions",flush=True)


if __name__ == "__main__":
    asyncio.run(export())
