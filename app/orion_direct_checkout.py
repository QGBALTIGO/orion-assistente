"""Orion-branded direct billing via Cakto API: fail-closed, opt-in, owner scoped.

This module never receives PAN/CVV and never grants entitlements. A signed webhook,
provider GET order, exact offer, exact amount, and owner intent remain authoritative.
ORION_DIRECT_PAYMENTS_ENABLED is OFF by default, until banking, payment/tokenization
scopes, recurring orders and signed webhooks have been confirmed in provider staging.
"""
from __future__ import annotations
import base64
import io
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone
import os
import re
from typing import Literal
from uuid import UUID, uuid4

import httpx
import qrcode
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.catalog_api import Actor, authenticate, support_session_factory
from app.tenant_access import resolve_site_access
from app.cakto_api import CaktoClient, BASE, ORDER_ID
from app.plan_management import (
    CheckoutChoice, CODES, _ensure_owner, _owner_account, _replayable_checkout_token,
    _exact_offer, start_plan_checkout,
)

router = APIRouter(tags=["Checkout Orion"])
EMAIL = re.compile(r"^[^\s@<>]{1,64}@[^\s@<>]{1,190}\.[^\s@<>]{2,30}$")
PHONE = re.compile(r"^55[1-9][0-9]9[0-9]{8}$")
TOKEN = re.compile(r"^[A-Za-z0-9._-]{12,512}$")
SAFE_FINGERPRINT = re.compile(r"^[A-Za-z0-9._:-]{12,255}$")
STATES = {"waiting_payment", "pending", "processing", "paid", "declined", "refused"}


def checkout_enabled() -> bool:
    return (os.getenv("ORION_DIRECT_PAYMENTS_ENABLED", "0") == "1"
            and os.getenv("ORION_DIRECT_RECURRING_VERIFIED", "0") == "1"
            and os.getenv("CAKTO_BILLING_LIVE_ENABLED", "0") == "1"
            and len(os.getenv("CAKTO_WEBHOOK_SECRET", "")) >= 32
            and bool(os.getenv("CAKTO_CLIENT_ID"))
            and bool(os.getenv("CAKTO_CLIENT_SECRET")))


def require_enabled():
    if not checkout_enabled():
        raise HTTPException(409, "Pagamento no Orion ainda não liberado. Nenhuma cobrança foi criada.")


def _valid_document(doc_type: str, number: str) -> bool:
    if not number.isdecimal() or len(set(number)) == 1:
        return False
    if doc_type == "cpf" and len(number) == 11:
        digits = [int(c) for c in number]
        for size in (9, 10):
            check = sum(digits[i] * (size + 1 - i) for i in range(size))
            check = (check * 10) % 11
            if check == 10: check = 0
            if check != digits[size]: return False
        return True
    if doc_type == "cnpj" and len(number) == 14:
        digits = [int(c) for c in number]
        for size, weights in (
            (12, [5,4,3,2,9,8,7,6,5,4,3,2]),
            (13, [6,5,4,3,2,9,8,7,6,5,4,3,2]),
        ):
            rem = sum(digits[i]*weights[i] for i in range(size)) % 11
            check = 0 if rem < 2 else 11-rem
            if check != digits[size]: return False
        return True
    return False


class Payer(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=3, max_length=120)
    email: str = Field(min_length=6, max_length=254)
    phone: str = Field(min_length=11, max_length=35)
    doc_type: Literal["cpf","cnpj"]
    doc_number: str = Field(min_length=11, max_length=22)
    fingerprint: str = Field(min_length=12, max_length=255)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value):
        if len(value.split()) < 2 or any(ord(c) < 32 for c in value):
            raise ValueError("Informe nome e sobrenome")
        return " ".join(value.split())

    @field_validator("email")
    @classmethod
    def clean_email(cls, value):
        if not EMAIL.fullmatch(value): raise ValueError("E-mail inválido")
        return value.lower()

    @field_validator("phone")
    @classmethod
    def clean_phone(cls, value):
        digits = re.sub(r"\D", "", value)
        if len(digits) == 11: digits = "55" + digits
        if not PHONE.fullmatch(digits): raise ValueError("Telefone inválido")
        return digits

    @field_validator("doc_number")
    @classmethod
    def clean_document(cls, value):
        return re.sub(r"\D", "", value)

    @field_validator("fingerprint")
    @classmethod
    def clean_fingerprint(cls, value):
        if not SAFE_FINGERPRINT.fullmatch(value):
            raise ValueError("Sessão de pagamento inválida")
        return value

    @model_validator(mode="after")
    def validate_document(self):
        if not _valid_document(self.doc_type, self.doc_number):
            raise ValueError("CPF/CNPJ inválido")
        return self


class DirectCharge(BaseModel):
    model_config = ConfigDict(extra="forbid")
    intent_id: UUID
    method: Literal["pix", "card"]
    payer: Payer
    confirmed_monthly: bool
    consent_to_processor: bool
    card_token: str | None = Field(default=None, max_length=512)
    antifraud_reference: str | None = Field(default=None, max_length=255)
    three_ds: dict[str, str] | None = None

    @model_validator(mode="after")
    def check_mode(self):
        if not self.confirmed_monthly or not self.consent_to_processor:
            raise ValueError("Aceite a assinatura mensal e o processamento seguro")
        if self.method == "pix":
            if self.card_token or self.antifraud_reference or self.three_ds:
                raise ValueError("Pix não recebe dados de cartão")
        else:
            if not (self.card_token and TOKEN.fullmatch(self.card_token)):
                raise ValueError("Token de cartão ausente")
            if not (self.antifraud_reference and SAFE_FINGERPRINT.fullmatch(self.antifraud_reference)):
                raise ValueError("Perfil antifraude ausente")
            if not isinstance(self.three_ds, dict) or not all(
                isinstance(self.three_ds.get(k), str) and 1 <= len(self.three_ds[k]) <= 255
                for k in ("cavv", "eci", "xid", "referenceId", "version")
            ):
                raise ValueError("Autenticação bancária 3DS não concluída")
            if set(self.three_ds) - {"cavv","eci","xid","referenceId","version"}:
                raise ValueError("Autenticação 3DS contém campos desconhecidos")
        return self


class DirectPrepare(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan_code: Literal["essencial","profissional","elite"]


async def owner(tenant_id: UUID, actor: Actor):
    access = await resolve_site_access(actor,tenant_id)
    return _owner_account(access,actor)


async def require_verified_webhooks(client: CaktoClient) -> None:
    # Read-only check: verify that every actual Orion offer has an active
    # HMAC-matching subscription webhook to the exact HTTPS callback URL.
    # Never display the provider's secret, webhook body or customer data.
    from scripts.probe_orion_webhooks_read_only import probe
    try:
        offers = await client.list_offers()
        result = await probe(client, offers)
    except (httpx.HTTPError, ValueError, RuntimeError):
        raise HTTPException(503, "A confirmação segura de pagamentos ainda não foi verificada.") from None
    if result.get("offer_matches") != 3 or result.get("webhook_ready") != 3:
        raise HTTPException(409, "A confirmação segura de pagamentos ainda não está configurada.")


@router.get("/api/v1/tenants/{tenant_id}/management/plan-direct-config")
async def direct_config(tenant_id: UUID, actor: Actor = Depends(authenticate)):
    await owner(tenant_id,actor)
    available = checkout_enabled()
    if available:
        try:
            await require_verified_webhooks(CaktoClient())
        except HTTPException:
            available = False
    return {"available": available,
            "sdk_client_id": os.getenv("CAKTO_CLIENT_ID", "") if available else None,
            "methods": ["pix","card"] if available else [],
            "pix_recurring": available}


@router.post("/api/v1/tenants/{tenant_id}/management/plan-direct-prepare")
async def direct_prepare(tenant_id: UUID, payload: DirectPrepare,
                         actor: Actor = Depends(authenticate)):
    require_enabled()
    await owner(tenant_id, actor)
    await require_verified_webhooks(CaktoClient())
    # Existing reviewed endpoint checks monthly offer, price, owner, current
    # subscription and ensures only one tenant/account checkout intent.
    internal = await start_plan_checkout(tenant_id,
                                         CheckoutChoice(plan_code=payload.plan_code),
                                         actor)
    from urllib.parse import urlsplit, parse_qs
    token = parse_qs(urlsplit(internal["checkout_url"]).query).get("sck", [None])[0]
    if not token:
        raise HTTPException(503, "Não foi possível preparar o pagamento")
    import hashlib
    digest = hashlib.sha256(token.encode()).hexdigest()
    account_id = await owner(tenant_id, actor)
    async with support_session_factory()() as db:
        record = (await db.execute(text("""
            SELECT id FROM orion_cakto_checkout_intents
            WHERE tenant_id=:tenant AND account_id=:account
              AND callback_hash=:hash AND state='pending' AND expires_at>now()
        """), {"tenant":tenant_id,"account":account_id,"hash":digest})).scalar_one_or_none()
    if record is None:
        raise HTTPException(409, "A tentativa anterior não está mais disponível")
    return {"intent_id": str(record), "plan_code":payload.plan_code,
            "monthly_cents":internal["monthly_cents"],
            "status":"ready", "sdk_client_id":os.getenv("CAKTO_CLIENT_ID")}


def provider_request(intent, data: DirectCharge, callback: str) -> dict:
    # Orion plans renew monthly; a one-time Pix must never promise recurring access.
    method = "pix_auto" if data.method == "pix" else "threeDs"
    payment = {
        "paymentMethod":method,
        "customer":{
            "name":data.payer.name, "email":data.payer.email,
            "phone":data.payer.phone,"docType":data.payer.doc_type,
            "docNumber":data.payer.doc_number,
            "fingerprint":data.payer.fingerprint,
        },
        "items":[{"offerId":intent["offer_id"],"quantity":1,"offerType":"main"}],
        "metadata":{"sck":callback},
    }
    if method == "pix":
        payment["pixExpiresIn"] = 900
    else:
        payment["card"] = {"token":data.card_token}
        payment["threeDSecure"] = data.three_ds
        payment["installments"] = 1
        payment["antifraud_profiling_attempt_reference"] = data.antifraud_reference
    return payment


def verified_creation(result: dict, intent: dict, method: str) -> bool:
    if not isinstance(result,dict) or not isinstance(result.get("id"),str):
        return False
    if not ORDER_ID.fullmatch(result["id"]):
        return False
    if result.get("paymentMethod") != ("pix_auto" if method=="pix" else "threeDs"):
        return False
    offer = result.get("offer")
    if not isinstance(offer,dict) or str(offer.get("id")) != intent["offer_id"]:
        return False
    try:
        cents = Decimal(str(result["amount"])) * 100
        if cents != cents.to_integral_value() or int(cents) != intent["expected_cents"]:
            return False
    except (KeyError,TypeError,ValueError,InvalidOperation):
        return False
    if result.get("status") not in STATES:
        return False
    if method=="pix":
        pix = result.get("pix")
        if not isinstance(pix,dict) or not isinstance(pix.get("qrCode"),str):
            return False
        if not 60 <= len(pix["qrCode"]) <= 4096: return False
        # A Pix Auto authorization declares the bank-specific authorization journey.
        if pix.get("user_journey") not in {"JORNADA_1","JORNADA_2","JORNADA_3","JORNADA_4"}:
            return False
    return True


async def _set_attempt_state(attempt_id: UUID, state: str, order_id: str | None = None):
    async with support_session_factory()() as db:
        async with db.begin():
            await db.execute(text("""
                UPDATE orion_direct_payment_attempts
                SET state=:state,external_order_id=:order_id,updated_at=now()
                WHERE id=:id AND state='reserved'
            """), {"id":attempt_id,"state":state,"order_id":order_id})


@router.post("/api/v1/tenants/{tenant_id}/management/plan-direct-charge",
             status_code=201)
async def direct_charge(tenant_id: UUID, data: DirectCharge,
                        actor: Actor = Depends(authenticate)):
    require_enabled()
    account_id = await owner(tenant_id,actor)
    await require_verified_webhooks(CaktoClient())
    # The same tenant lock serializes embedded attempts with hosted intent creation.
    # No account details or provider token are persisted.
    async with support_session_factory()() as db:
        async with db.begin():
            await _ensure_owner(db,tenant_id,account_id)
            await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
                             {"key":"orion-checkout:"+str(tenant_id)})
            intent = (await db.execute(text("""
                SELECT id,account_id,tenant_id,plan_code,offer_id,expected_cents,
                       state,expires_at
                FROM orion_cakto_checkout_intents
                WHERE id=:id AND tenant_id=:tenant AND account_id=:account
                FOR UPDATE
            """), {"id":data.intent_id,"tenant":tenant_id,"account":account_id})).mappings().first()
            if not intent or intent["state"]!="pending" or intent["expires_at"]<=datetime.now(timezone.utc):
                raise HTTPException(409,"Tentativa indisponível. Nenhuma nova cobrança foi criada.")
            active = (await db.execute(text("""
                SELECT 1 FROM subscriptions WHERE tenant_id=:tenant LIMIT 1 FOR UPDATE
            """),{"tenant":tenant_id})).scalar_one_or_none()
            if active: raise HTTPException(409,"Esta empresa já possui uma assinatura.")
            existing = (await db.execute(text("""
                SELECT id FROM orion_direct_payment_attempts WHERE intent_id=:intent
            """), {"intent":data.intent_id})).scalar_one_or_none()
            if existing:
                raise HTTPException(409,"Pagamento já iniciado. Aguarde a confirmação antes de tentar novamente.")
            attempt_id = uuid4()
            await db.execute(text("""
                INSERT INTO orion_direct_payment_attempts(id,intent_id,tenant_id,account_id,method)
                VALUES(:id,:intent,:tenant,:account,:method)
            """),{"id":attempt_id,"intent":data.intent_id,
                  "tenant":tenant_id,"account":account_id,"method":data.method})
    # Read-only confirmation of exact subscription offer, after durable reservation.
    try:
        client = CaktoClient()
        offers = await client.list_offers()
        plan = CODES[intent["plan_code"]]
        offer = _exact_offer(offers,plan)
        if offer is None or str(offer["id"]) != intent["offer_id"]:
            await _set_attempt_state(attempt_id,"needs_review")
            raise HTTPException(409,"Oferta mensal indisponível. Nenhuma cobrança foi enviada.")
        # Token is derived from our private HMAC secret. Only its hash is persisted.
        tracking = _replayable_checkout_token(tenant_id,data.intent_id,intent["plan_code"])
        payload = provider_request(intent,data,tracking)
        token = await client.token()
        async with httpx.AsyncClient(timeout=25,follow_redirects=False) as http:
            response = await http.post(BASE+"/payments/",json=payload,
                                       headers={"Authorization":"Bearer "+token,
                                                "Content-Type":"application/json",
                                                "X-Idempotency-Key":str(attempt_id)})
            response.raise_for_status()
            result = response.json()
        if not verified_creation(result,intent,data.method):
            await _set_attempt_state(attempt_id,"needs_review")
            raise HTTPException(502,"Pagamento recebido para revisão. Não repita a cobrança.")
        await _set_attempt_state(attempt_id,"created",result["id"])
    except HTTPException:
        raise
    except (httpx.HTTPError,ValueError,TypeError,RuntimeError,SQLAlchemyError):
        # A timeout may mean the PSP created the charge. Never automatically retry:
        # the durable reservation and one-time idempotency ID prevent duplicates.
        try:
            await _set_attempt_state(attempt_id,"unknown")
        except SQLAlchemyError:
            pass
        raise HTTPException(503,"Resultado não confirmado. Não gere outra cobrança; consulte o suporte.") from None
    response_data = {"status":"pending_confirmation",
                     "intent_id":str(data.intent_id),"method":data.method}
    if data.method=="pix":
        response_data["pix_code"]=result["pix"]["qrCode"]
        # QR is generated locally; the Pix code is never transmitted to a third-party renderer.
        pixels = qrcode.make(result["pix"]["qrCode"])
        buffer = io.BytesIO()
        pixels.save(buffer, format="PNG")
        response_data["pix_qr_data_uri"] = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
        response_data["expires_at"]=result["pix"].get("expirationDate")
    return response_data


@router.get("/api/v1/tenants/{tenant_id}/management/plan-direct-status")
async def direct_status(tenant_id: UUID, intent_id: UUID,
                        actor: Actor = Depends(authenticate)):
    account_id=await owner(tenant_id,actor)
    async with support_session_factory()() as db:
        row=(await db.execute(text("""
            SELECT a.state,a.method,i.state AS intent_state
            FROM orion_cakto_checkout_intents i
            LEFT JOIN orion_direct_payment_attempts a ON a.intent_id=i.id
            WHERE i.id=:id AND i.tenant_id=:tenant AND i.account_id=:account
        """),{"id":intent_id,"tenant":tenant_id,"account":account_id})).mappings().first()
    if not row:
        raise HTTPException(404,"Pagamento não encontrado")
    if row["intent_state"]=="verified":
        return {"status":"active"}
    if row["state"] in ("unknown","needs_review"):
        return {"status":"review_required"}
    if row["state"]=="declined":
        return {"status":"declined"}
    return {"status":"pending"}
