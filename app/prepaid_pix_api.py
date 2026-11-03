"""One-time Cakto Pix for prepaid 30-day Orion plans, no subscriptions."""
from __future__ import annotations
import base64
import hashlib
import io
import os
from datetime import datetime,timedelta,timezone
from decimal import Decimal,InvalidOperation
from uuid import UUID,uuid4

import httpx
import qrcode
from fastapi import APIRouter,Depends,HTTPException
from pydantic import BaseModel,ConfigDict,model_validator
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.catalog_api import Actor,authenticate,support_session_factory
from app.tenant_access import resolve_site_access
from app.cakto_api import CaktoClient,BASE,ORDER_ID
from app.orion_direct_checkout import Payer
from app.plan_catalog import CODES
from app.plan_permissions import lock_plan
from app.plan_management import (_owner_account,_ensure_owner,
    _replayable_checkout_token,require_checkout_available)

router=APIRouter(tags=["Orion Pix pré-pago"])
PIX_NAMES={
    "essencial":"Orion Essencial - Pix 30 dias",
    "profissional":"Orion Profissional - Pix 30 dias",
    "elite":"Orion Elite - Pix 30 dias",
}
PIX_EVENTS=frozenset({"purchase_approved","purchase_refused","refund","chargeback"})
TARGET="https://orionagenda.pro/webhooks/cakto"


def pix_environment_ready():
    return (os.getenv("ORION_PIX_PREPAID_ENABLED","0")=="1"
        and len(os.getenv("CAKTO_PIX_WEBHOOK_SECRET",""))>=32
        and bool(os.getenv("CAKTO_CLIENT_ID"))
        and bool(os.getenv("CAKTO_CLIENT_SECRET")))


def cents(value):
    try:
        number=Decimal(str(value))*100
        return int(number) if number.is_finite() and number==number.to_integral_value() else None
    except (TypeError,ValueError,InvalidOperation):
        return None


def product_id(obj):
    if isinstance(obj,dict):
        obj=obj.get("id")
    return obj if isinstance(obj,str) and 1<=len(obj)<=80 else None


def prepaid_offers(offers):
    found={}
    for code,plan in CODES.items():
        candidates=[offer for offer in offers if isinstance(offer,dict)
            and offer.get("status")=="active" and offer.get("type")=="unique"
            and offer.get("name")==PIX_NAMES[code]
            and cents(offer.get("price"))==plan["price_cents"]
            and isinstance(offer.get("id"),str) and 1<=len(offer["id"])<=80
            and product_id(offer.get("product"))]
        if len(candidates)!=1:
            raise HTTPException(409,"As ofertas Pix pré-pagas ainda não estão prontas.")
        found[code]=candidates[0]
    if len({o["id"] for o in found.values()})!=3:
        raise HTTPException(409,"Ofertas Pix duplicadas ou ambíguas.")
    return found


async def verified_pix_catalog(client):
    """Real provider read-only checks before EVERY payment POST."""
    if not pix_environment_ready():
        raise HTTPException(409,"Pix comum temporariamente indisponível; nenhuma cobrança criada.")
    try:
        offers=prepaid_offers(await client.list_offers())
        await client.token()
        if client.granted_scopes(frozenset({"read","write","orders","payments"})) is not True:
            raise HTTPException(409,"API da Cakto sem autorização para cobranças Pix.")
        products={product_id(o["product"]) for o in offers.values()}
        for ref in products:
            p=await client._get("/products/"+ref+"/")
            if (not isinstance(p,dict) or p.get("id")!=ref
                or p.get("type")!="unique" or p.get("status")!="active"
                or "pix" not in (p.get("paymentMethods") or [])):
                raise HTTPException(409,"Produto Pix não habilitado na Cakto.")
        hooks=[]
        for page in range(1,11):
            page_data=await client._get("/webhook/",page=page,limit=100)
            if not isinstance(page_data,dict) or not isinstance(page_data.get("results"),list):
                raise HTTPException(503,"Cadastro de webhook Pix não verificado.")
            hooks.extend(page_data["results"])
            if not page_data.get("next"):
                break
        else:
            raise HTTPException(503,"Paginação de webhooks Pix incompleta.")
        from hmac import compare_digest
        secret=os.getenv("CAKTO_PIX_WEBHOOK_SECRET","")
        ready=False
        for hook in hooks:
            if (not isinstance(hook,dict) or hook.get("url")!=TARGET
                or hook.get("status")!="active"):
                continue
            fields=hook.get("fields")
            remote=fields.get("secret") if isinstance(fields,dict) else None
            if not isinstance(remote,str) or not compare_digest(remote,secret):
                continue
            events=hook.get("events")
            targets=hook.get("products")
            ids={x.get("custom_id") for x in events if isinstance(x,dict)} if isinstance(events,list) else set()
            linked={product_id(x) for x in targets} if isinstance(targets,list) else set()
            if PIX_EVENTS.issubset(ids) and products.issubset(linked):
                ready=True
                break
        if not ready:
            raise HTTPException(409,"Confirmação do webhook dos planos Pix não verificada.")
        return offers
    except HTTPException:
        raise
    except (httpx.HTTPError,ValueError,TypeError,RuntimeError):
        raise HTTPException(503,"Cakto indisponível para verificar pagamento Pix.") from None


async def owner(tenant_id,actor):
    access=await resolve_site_access(actor,tenant_id)
    return _owner_account(access,actor)


class PixPurchase(BaseModel):
    model_config=ConfigDict(extra="forbid")
    plan_code:str
    payer:Payer
    consent_one_time:bool
    @model_validator(mode="after")
    def validate(self):
        if self.plan_code not in CODES or not self.consent_one_time:
            raise ValueError("Confirme o plano e a cobrança Pix única.")
        return self


def payment_request(offer_id,payer,tracking):
    return {"paymentMethod":"pix",
       "customer":{"name":payer.name,"email":payer.email,"phone":payer.phone,
                   "docType":payer.doc_type,"docNumber":payer.doc_number,
                   "fingerprint":payer.fingerprint},
       "items":[{"offerId":offer_id,"quantity":1,"offerType":"main"}],
       "pixExpiresIn":900,"metadata":{"sck":tracking}}


def verified_pix_creation(order,offer_id,price):
    if not isinstance(order,dict):
        return False
    if (not isinstance(order.get("id"),str) or not ORDER_ID.fullmatch(order["id"])
        or order.get("paymentMethod")!="pix"
        or order.get("status") not in ("waiting_payment","pending","processing")
        or cents(order.get("amount"))!=price):
        return False
    offer=order.get("offer")
    pix=order.get("pix")
    return bool(isinstance(offer,dict) and offer.get("id")==offer_id
        and isinstance(pix,dict) and isinstance(pix.get("qrCode"),str)
        and 60<=len(pix["qrCode"])<=4096
        and pix.get("user_journey") in (None,""))


def png_uri(code):
    out=io.BytesIO()
    qrcode.make(code).save(out,format="PNG")
    return "data:image/png;base64,"+base64.b64encode(out.getvalue()).decode("ascii")


async def set_attempt(order_id,state,external_id=None):
    async with support_session_factory()() as db:
        async with db.begin():
            await db.execute(text("""
              UPDATE orion_prepaid_pix_orders
                 SET state=:state,
                     external_order_id=COALESCE(:external,external_order_id),
                     updated_at=now()
               WHERE id=:id AND state IN ('reserved','created','needs_review')
            """),{"id":order_id,"state":state,"external":external_id})


@router.get("/api/v1/tenants/{tenant_id}/management/plan-pix-config")
async def pix_config(tenant_id:UUID,actor:Actor=Depends(authenticate)):
    await owner(tenant_id,actor)
    if not pix_environment_ready():
        return {"available":False,"methods":[],"mode":"manual_30_days"}
    try:
        await verified_pix_catalog(CaktoClient())
    except HTTPException:
        return {"available":False,"methods":[],"mode":"manual_30_days"}
    return {"available":True,"methods":["pix"],"mode":"manual_30_days"}


@router.post("/api/v1/tenants/{tenant_id}/management/plan-pix-charge",status_code=201)
async def create_pix_charge(tenant_id:UUID,payload:PixPurchase,
                            actor:Actor=Depends(authenticate)):
    account=await owner(tenant_id,actor)
    client=CaktoClient()
    matched=await verified_pix_catalog(client)
    plan=CODES[payload.plan_code]
    offer_id=matched[payload.plan_code]["id"]
    intent=uuid4()
    token=_replayable_checkout_token(tenant_id,intent,payload.plan_code)
    digest=hashlib.sha256(token.encode()).hexdigest()
    async with support_session_factory()() as db:
        async with db.begin():
            await lock_plan(db,tenant_id,exclusive=True)
            await require_checkout_available(db,tenant_id,account)
            ongoing=(await db.execute(text("""
              SELECT 1 FROM subscriptions WHERE tenant_id=:tenant
            """),{"tenant":tenant_id})).scalar_one_or_none()
            if ongoing:
                raise HTTPException(409,"Empresa possui assinatura anterior. Regularize com o suporte.")
            recent=(await db.execute(text("""
              SELECT id FROM orion_prepaid_pix_orders
              WHERE tenant_id=:tenant AND state IN ('reserved','created','needs_review')
              FOR UPDATE
            """),{"tenant":tenant_id})).scalar_one_or_none()
            if recent:
                raise HTTPException(409,"Já existe um Pix em andamento. Consulte seu pagamento antes de gerar outro.")
            grant=(await db.execute(text("""
              SELECT plan_code,paid_until FROM orion_prepaid_pix_access
              WHERE tenant_id=:tenant FOR UPDATE
            """),{"tenant":tenant_id})).mappings().first()
            if grant and grant["paid_until"]>datetime.now(timezone.utc) and grant["plan_code"]!=payload.plan_code:
                raise HTTPException(409,"Para trocar o plano durante o período pago, contate o suporte.")
            await db.execute(text("""
              INSERT INTO orion_prepaid_pix_orders
                (id,tenant_id,account_id,plan_code,offer_id,expected_cents,callback_hash,expires_at)
              VALUES (:id,:tenant,:account,:plan,:offer,:cents,:hash,now()+interval '24 hours')
            """),{"id":intent,"tenant":tenant_id,"account":account,
                "plan":payload.plan_code,"offer":offer_id,
                "cents":plan["price_cents"],"hash":digest})
    try:
        bearer=await client.token()
        async with httpx.AsyncClient(timeout=25,follow_redirects=False) as http:
            response=await http.post(BASE+"/payments/",
                json=payment_request(offer_id,payload.payer,token),
                headers={"Authorization":"Bearer "+bearer,
                         "Content-Type":"application/json",
                         "X-Idempotency-Key":str(intent)})
            response.raise_for_status()
            charge=response.json()
        if not verified_pix_creation(charge,offer_id,plan["price_cents"]):
            await set_attempt(intent,"needs_review")
            raise HTTPException(502,"Pagamento em análise. Não gere outro Pix.")
        await set_attempt(intent,"created",charge["id"])
    except HTTPException:
        raise
    except (httpx.HTTPError,ValueError,TypeError,RuntimeError,SQLAlchemyError):
        try:
            await set_attempt(intent,"needs_review")
        except SQLAlchemyError:
            pass
        raise HTTPException(503,"Pagamento não confirmado. Não gere outro Pix; consulte o suporte.") from None
    return {"intent_id":str(intent),"status":"pending_confirmation",
            "mode":"manual_30_days","plan_code":payload.plan_code,
            "monthly_cents":plan["price_cents"],
            "pix_code":charge["pix"]["qrCode"],
            "pix_qr_data_uri":png_uri(charge["pix"]["qrCode"]),
            "expires_at":charge["pix"].get("expirationDate")}


@router.get("/api/v1/tenants/{tenant_id}/management/plan-pix-status")
async def pix_status(tenant_id:UUID,intent_id:UUID,
                     actor:Actor=Depends(authenticate)):
    account=await owner(tenant_id,actor)
    async with support_session_factory()() as db:
        row=(await db.execute(text("""
          SELECT state FROM orion_prepaid_pix_orders
          WHERE id=:id AND tenant_id=:tenant AND account_id=:account
        """),{"id":intent_id,"tenant":tenant_id,"account":account})).mappings().first()
        if not row:
            raise HTTPException(404,"Pagamento não encontrado")
        valid=(await db.execute(text("""
          SELECT paid_until FROM orion_prepaid_pix_access WHERE tenant_id=:tenant
        """),{"tenant":tenant_id})).scalar_one_or_none()
    state=row["state"]
    if state=="paid":
        return {"status":"active","paid_until":valid}
    if state=="refunded":
        return {"status":"refunded"}
    if state=="declined":
        return {"status":"declined"}
    if state in ("reserved","needs_review"):
        return {"status":"review_required"}
    return {"status":"pending"}
