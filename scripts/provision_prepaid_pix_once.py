"""One-shot Cakto prepaid Pix product, offers and webhook provisioning.

PROVISIONING DOES NOT CREATE PAYMENTS OR CUSTOMER ACCOUNTS. Run only via
ORION_PIX_PROVISION_ONCE=1 on the API pre-deploy; all provider writes are
owner-authorized and never automatically repeated on ambiguous outcomes.
Never log provider bodies, IDs, credentials or webhook secrets.
"""
from __future__ import annotations

import base64
import os
from decimal import Decimal,InvalidOperation
from typing import Any

import httpx
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import padding
from fastapi import HTTPException

from app.cakto_api import CaktoClient,BASE
from app.plan_catalog import CODES
from app.prepaid_pix_api import PIX_NAMES,prepaid_offers,product_id,cents

TARGET="https://orionagenda.pro/webhooks/cakto"
EVENTS=("purchase_approved","purchase_refused","refund","chargeback","checkout_abandonment")
ENVELOPE_LABEL=b"orion-prepaid-pix-secret-2026-11"
PUBLIC_KEY=b"""-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAqh9dmB/epXLSYWHZSTBQ
clgbiImG4oODvfC5GGgDNoaQBnbbXTAd8xh26vEvStJGP+30a4HIUY1IUU7VPPsi
sjD8b7NX/Z9cJc/BjLeWDMxJUS3YHJWGqbj37zScRdsu6I535CpGuHPfU7lrK8bV
JI+xo9lnGZRvquCJTY7SAA4bbnHZwb6e8X5PMyMQnixAv6u3WgEauEiJ6cpGvDN/
zy+8ZrzmY7XRNM966B+RmIp6dQ988BRK9EvqYcO0QgBZuwNJ4fXvUpp63SIHi2p4
TN032WjDUxboz2UpA4zSk0wRSG9hk5GUqa8TZaOHUoW+BL5tiydA+arCUrC83rFZ
qQIDAQAB
-----END PUBLIC KEY-----"""


async def _post(client:CaktoClient,path:str,data:dict):
    bearer=await client.token()
    # Idempotency is provider-specific for product and hook creation.
    # Never retry a failed POST: the next deployment first queries remote state.
    async with httpx.AsyncClient(timeout=25,follow_redirects=False) as http:
        response=await http.post(BASE+path,json=data,headers={
            "Authorization":"Bearer "+bearer,
            "Content-Type":"application/json",
        })
        response.raise_for_status()
        return response.json()


async def _paged(client,path):
    values=[]
    for page in range(1,11):
        response=await client._get(path,page=page,limit=100)
        if not isinstance(response,dict) or not isinstance(response.get("results"),list):
            raise RuntimeError("provider_read_contract_invalid")
        values.extend(response["results"])
        if not response.get("next"):
            return values
    raise RuntimeError("provider_pagination_limit")


def _sane_product(product):
    return (isinstance(product,dict) and product.get("name")==PIX_NAMES["essencial"]
            and product.get("type")=="unique"
            and product.get("status")=="active"
            and isinstance(product.get("id"),str)
            and len(product["id"])>=8
            and "pix" in (product.get("paymentMethods") or []))


async def _product(client):
    all_products=await _paged(client,"/products/")
    candidates=[item for item in all_products if isinstance(item,dict)
                and item.get("name")==PIX_NAMES["essencial"]]
    if len(candidates)>1:
        raise RuntimeError("multiple_prepaid_products")
    if candidates:
        product=candidates[0]
        if not _sane_product(product):
            raise RuntimeError("prepaid_product_not_active_unique_pix")
        return product
    created=await _post(client,"/products/",{
        "name":PIX_NAMES["essencial"],
        "description":"Acesso pré-pago Orion por 30 dias. Sem renovação automática.",
        "price":"29.90",
        "salesPage":"https://orionagenda.pro/",
        "emailAccessLink":"https://orionagenda.pro/painel",
        "contentDeliveries":[],
        "paymentMethods":["pix"],
    })
    if not _sane_product(created):
        raise RuntimeError("prepaid_product_create_not_confirmed")
    return created


async def _offers(client,product):
    known=await client.list_offers()
    product_ref=product["id"]
    for code in CODES:
        price=CODES[code]["price_cents"]
        exact=[o for o in known if isinstance(o,dict)
               and o.get("name")==PIX_NAMES[code]]
        if exact:
            if (len(exact)!=1 or exact[0].get("status")!="active"
                    or exact[0].get("type")!="unique"
                    or product_id(exact[0].get("product"))!=product_ref
                    or cents(exact[0].get("price"))!=price):
                raise RuntimeError("prepaid_offer_conflict")
            continue
        # Do not automatically create if the new product itself did not
        # provide a default Essential offer until we know its actual state.
        if code=="essencial":
            default_candidates=[
                o for o in product.get("offers",[])
                if isinstance(o,dict) and o.get("name")==PIX_NAMES[code]
            ]
            if len(default_candidates)!=1:
                raise RuntimeError("prepaid_default_offer_not_ready")
            default=default_candidates[0]
            if (default.get("type")!="unique"
                    or cents(default.get("price"))!=price
                    or default.get("status")!="active"):
                raise RuntimeError("prepaid_default_offer_invalid")
            # A newly created default offer can take a moment to appear in
            # GET /offers; do not create an unwanted duplicate.
            known.append(default)
            continue
        created=await _post(client,"/offers/",{
            "name":PIX_NAMES[code],
            "price":float(Decimal(price)/100),
            "product":product_ref,
            "units":1,
        })
        if (not isinstance(created,dict)
                or created.get("name")!=PIX_NAMES[code]
                or created.get("type")!="unique"
                or created.get("status")!="active"
                or cents(created.get("price"))!=price
                or product_id(created.get("product"))!=product_ref):
            raise RuntimeError("prepaid_offer_create_not_confirmed")
        known.append(created)
    # Refresh if possible, but don't ignore a newly minted offer not yet in
    # the index (provider eventual consistency).
    verified=prepaid_offers(known)
    if set(verified)!=set(CODES):
        raise RuntimeError("prepaid_catalog_incomplete")
    if {product_id(offer.get("product")) for offer in verified.values()}!={product_ref}:
        raise RuntimeError("prepaid_multiple_products")
    return verified


def _hooks_for_product(hooks,ref):
    return [h for h in hooks if isinstance(h,dict)
            and h.get("url")==TARGET
            and isinstance(h.get("products"),list)
            and ref in {product_id(p) for p in h["products"]}]


def _validate_webhook(hook,ref):
    if not isinstance(hook,dict) or hook.get("status")!="active":
        raise RuntimeError("prepaid_webhook_inactive")
    if hook.get("url")!=TARGET:
        raise RuntimeError("prepaid_webhook_wrong_target")
    linked=hook.get("products")
    if not isinstance(linked,list) or ref not in {product_id(p) for p in linked}:
        raise RuntimeError("prepaid_webhook_wrong_product")
    subscribed=hook.get("events")
    enabled={x.get("custom_id") for x in subscribed if isinstance(x,dict)} if isinstance(subscribed,list) else set()
    if not set(EVENTS).issubset(enabled):
        raise RuntimeError("prepaid_webhook_event_coverage_incomplete")
    fields=hook.get("fields")
    secret=fields.get("secret") if isinstance(fields,dict) else None
    if not isinstance(secret,str) or not 32<=len(secret)<=128:
        raise RuntimeError("prepaid_webhook_key_not_readable")
    return secret


async def _webhook(client,ref):
    hooks=await _paged(client,"/webhook/")
    candidates=_hooks_for_product(hooks,ref)
    if len(candidates)>1:
        raise RuntimeError("prepaid_duplicate_webhook_target")
    if candidates:
        return _validate_webhook(candidates[0],ref)
    result=await _post(client,"/webhook/",{
        "name":"Orion Pix Unico - Acesso 30 Dias",
        "url":TARGET,
        "products":[ref],
        "events":list(EVENTS),
    })
    return _validate_webhook(result,ref)


async def provision():
    if os.getenv("ORION_PIX_PROVISION_ONCE","0")!="1":
        return
    # The live Pix channel remains disabled during catalog provisioning.
    if os.getenv("ORION_PIX_PREPAID_ENABLED","0")=="1":
        raise RuntimeError("prepaid_channel_must_be_disabled_during_provisioning")
    client=CaktoClient()
    if not client.configured:
        raise RuntimeError("cakto_oauth_not_configured")
    await client.token()
    if client.granted_scopes(frozenset({"read","write","payments","products","offers","webhooks"})) is not True:
        raise RuntimeError("cakto_product_and_webhook_write_scopes_not_approved")
    product=await _product(client)
    matched=await _offers(client,product)
    if len(matched)!=3:
        raise RuntimeError("prepaid_catalog_incomplete")
    secret=await _webhook(client,product["id"])
    key=serialization.load_pem_public_key(PUBLIC_KEY)
    payload=key.encrypt(secret.encode(),padding.OAEP(
        mgf=padding.MGF1(algorithm=hashes.SHA256()),
        algorithm=hashes.SHA256(),label=ENVELOPE_LABEL))
    print("orion_pix_encrypted_secret_v1:"+base64.urlsafe_b64encode(payload).decode(),flush=True)
    print("orion_pix_catalog_and_webhook_ready:3/3",flush=True)


if __name__=="__main__":
    import asyncio
    asyncio.run(provision())
