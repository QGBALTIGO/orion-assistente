"""Scoped Orion plan catalogue, manual 3-day trial and Cakto checkout intents.

Provider webhook verification remains the only payment authority. No entitlement
is awarded by a redirect, an email address, a trial request or a checkout click.
"""
from __future__ import annotations

import hashlib
import os
import re
import secrets
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.catalog_api import Actor, authenticate, support_session_factory
from app.database import get_session_factory, tenant_transaction
from app.cakto_api import CaktoClient
from app.account_business import account_membership_role
from app.tenant_access import resolve_tenant_access
from app.admin_accounts import require_platform_owner

router = APIRouter(tags=["Planos Orion"])
CODES = {
    "essencial": {"name": "Orion Essencial", "price_cents": 2990, "max_professionals": 2,
                  "db_code": "orion_essencial", "offer_names": ("Orion Essencial",),
                  "checkout_url": "https://pay.cakto.com.br/3bdhdkc_1219844"},
    "profissional": {"name": "Orion Profissional", "price_cents": 8990, "max_professionals": 6,
                    "db_code": "orion_profissional",
                    "offer_names": ("Orion Profissional", "Orion Pro"),
                    "checkout_url": "https://pay.cakto.com.br/3652fm4"},
    "elite": {"name": "Orion Elite", "price_cents": 13990, "max_professionals": 10,
              "db_code": "orion_elite", "offer_names": ("Orion Elite",),
              "checkout_url": "https://pay.cakto.com.br/v4md8n3"},
}
BRAZIL_DDDS = frozenset({
    "11","12","13","14","15","16","17","18","19","21","22","24","27","28",
    "31","32","33","34","35","37","38","41","42","43","44","45","46","47","48","49",
    "51","53","54","55","61","62","63","64","65","66","67","68","69",
    "71","73","74","75","77","79","81","82","83","84","85","86","87","88","89",
    "91","92","93","94","95","96","97","98","99",
})
TRIAL_SCOPE = (
    "Agenda, clientes, serviços e site de agendamento",
    "Financeiro básico e central de atendimento manual",
    "Recursos de WhatsApp somente após a conexão e configuração necessárias",
)


class TrialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    full_name: str = Field(min_length=3, max_length=120)
    company_name: str = Field(min_length=2, max_length=160)
    whatsapp: str = Field(min_length=10, max_length=35)
    privacy_ack: bool

    @field_validator("full_name", "company_name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        name = " ".join(value.split())
        if len(name) < 2 or any(ord(ch) < 32 for ch in name):
            raise ValueError("Informe um nome válido")
        return name

    @field_validator("whatsapp")
    @classmethod
    def normalize_whatsapp(cls, value: str) -> str:
        if not re.fullmatch(r"[\d\s()+.-]{10,35}", value):
            raise ValueError("WhatsApp inválido")
        digits = re.sub(r"\D", "", value)
        if digits.startswith("55") and len(digits) == 13:
            digits = digits[2:]
        if (len(digits) != 11 or digits[:2] not in BRAZIL_DDDS
                or digits[2] != "9" or digits[3] == "0"):
            raise ValueError("Informe um celular brasileiro com DDD e 11 dígitos")
        return "+55" + digits


class CheckoutChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan_code: str


class TrialDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)
    action: str
    reason: str | None = Field(default=None, max_length=500)


def _owner_account(access, actor) -> UUID:
    if (actor.role != "member" or access.role != "owner"
            or not isinstance(access.account_id, UUID)):
        raise HTTPException(403, "Somente o proprietário da empresa pode solicitar este serviço")
    return access.account_id


async def _ensure_owner(db, tenant_id: UUID, account_id: UUID):
    # Run within the same transaction holding the account membership share lock.
    role = await account_membership_role(db, account_id, tenant_id, lock=True)
    if role != "owner":
        raise HTTPException(403, "O vínculo de proprietário não está mais ativo")


def _catalog():
    return [{"code": key, "name": plan["name"], "monthly_cents": plan["price_cents"],
             "max_professionals": plan["max_professionals"]} for key, plan in CODES.items()]


def _subscription_name(row):
    if not row:
        return None
    return {"id": str(row["id"]), "status": row["status"], "name": row["name"],
            "code": row["code"], "monthly_cents": row["monthly_cents"],
            "max_professionals": row["max_professionals"],
            "trial_ends_at": row["trial_ends_at"],
            "grace_until": row["grace_until"],
            "last_verified_payment_at": row["last_verified_payment_at"],
            "next_renewal_at": None}


@router.get("/api/v1/tenants/{tenant_id}/management/plan-overview")
async def plan_overview(tenant_id: UUID, actor: Actor = Depends(authenticate)):
    access = await resolve_tenant_access(actor, tenant_id, "read")
    async with support_session_factory()() as db:
        async with db.begin():
            row = (await db.execute(text("""
                SELECT s.id,s.status,s.trial_ends_at,s.grace_until,
                       s.last_verified_payment_at,p.name,p.code,p.monthly_cents,p.max_professionals
                FROM subscriptions s JOIN plans p ON p.id=s.plan_id
                WHERE s.tenant_id=:tenant
            """), {"tenant": tenant_id})).mappings().first()
            trial = (await db.execute(text("""
                SELECT id,CASE WHEN status='active' AND ends_at<=now() THEN 'expired' ELSE status END AS status,
                       requested_at,started_at,ends_at,version
                FROM orion_plan_trial_requests WHERE tenant_id=:tenant
            """), {"tenant": tenant_id})).mappings().first()
    async with get_session_factory()() as runtime:
        async with tenant_transaction(runtime, tenant_id):
            used = (await runtime.execute(text("""
                SELECT count(*) FROM professionals
                WHERE tenant_id=:tenant AND active
            """), {"tenant": tenant_id})).scalar_one()
    if trial and trial["status"] == "active" and trial["ends_at"] and (
        trial["ends_at"] <= datetime.now(timezone.utc)
    ):
        trial = {**trial, "status": "expired"}
    return {
        "plans": _catalog(),
        "subscription": _subscription_name(row),
        "trial": dict(trial) if trial else None,
        "active_professionals": used,
        "can_manage_plan": actor.role == "member" and access.role == "owner",
        "checkout_enabled": os.getenv("CAKTO_BILLING_LIVE_ENABLED", "0") == "1",
        "trial_scope": TRIAL_SCOPE,
        "trial_requires_team_activation": True,
    }


@router.post("/api/v1/tenants/{tenant_id}/management/plan-trial", status_code=201)
async def request_plan_trial(tenant_id: UUID, payload: TrialRequest,
                             actor: Actor = Depends(authenticate)):
    access = await resolve_tenant_access(actor, tenant_id, "publish")
    account_id = _owner_account(access, actor)
    if payload.privacy_ack is not True:
        raise HTTPException(422, "É necessário aceitar o uso do contato para o teste")
    async with support_session_factory()() as db:
        try:
            async with db.begin():
                await _ensure_owner(db, tenant_id, account_id)
                tenant = (await db.execute(text("""
                    SELECT name FROM tenants WHERE id=:id FOR SHARE
                """), {"id": tenant_id})).scalar_one_or_none()
                if tenant is None:
                    raise HTTPException(404, "Empresa não encontrada")
                current = (await db.execute(text("""
                    SELECT 1 FROM subscriptions WHERE tenant_id=:tenant
                    AND status IN ('active','trialing') LIMIT 1
                """), {"tenant": tenant_id})).scalar_one_or_none()
                if current:
                    raise HTTPException(409, "Sua empresa já possui uma assinatura ou teste")
                existing = (await db.execute(text("""
                    SELECT status FROM orion_plan_trial_requests WHERE tenant_id=:tenant
                    FOR UPDATE
                """), {"tenant": tenant_id})).scalar_one_or_none()
                if existing is not None:
                    raise HTTPException(409, "Esta empresa já possui uma solicitação de teste registrada")
                # Consent and contact are held in a separate support-only table,
                # never copied to customers or sent automatically to WhatsApp.
                await db.execute(text("""
                    INSERT INTO orion_plan_trial_requests
                      (tenant_id,account_id,full_name,company_name,whatsapp_e164)
                    VALUES (:tenant,:account,:full,:company,:phone)
                """), {"tenant": tenant_id, "account": account_id,
                       "full": payload.full_name, "company": payload.company_name,
                       "phone": payload.whatsapp})
        except IntegrityError:
            raise HTTPException(409, "Solicitação já registrada para esta empresa") from None
    return {"status": "pending", "message": "Solicitação recebida! Nossa equipe entrará em contato pelo WhatsApp informado para ajudar você a começar a utilizar o Orion."}


@router.get("/api/v1/admin/plan-trials")
async def admin_list_plan_trials(admin: UUID = Depends(require_platform_owner),
                                 status: str = "all"):
    if status not in {"all","pending","active","expired","rejected"}:
        raise HTTPException(422, "Filtro inválido")
    async with support_session_factory()() as db:
        records = (await db.execute(text("""
            SELECT r.id,r.account_id,r.tenant_id,r.full_name,r.company_name,
                   r.whatsapp_e164,CASE WHEN r.status='active' AND r.ends_at<=now()
                     THEN 'expired' ELSE r.status END AS status,
                   r.requested_at,r.started_at,r.ends_at,
                   r.version,t.name AS registered_company
            FROM orion_plan_trial_requests r JOIN tenants t ON t.id=r.tenant_id
            WHERE :status='all' OR r.status=:status
            ORDER BY r.requested_at DESC LIMIT 100
        """), {"status": status})).mappings().all()
    return {"items": [dict(row) for row in records]}


@router.post("/api/v1/admin/plan-trials/{trial_id}")
async def admin_decide_trial(trial_id: UUID, decision: TrialDecision,
                             admin: UUID = Depends(require_platform_owner)):
    if decision.action not in {"activate", "reject"}:
        raise HTTPException(422, "Decisão inválida")
    async with support_session_factory()() as db:
        async with db.begin():
            row = (await db.execute(text("""
                SELECT id,tenant_id,status,version FROM orion_plan_trial_requests
                WHERE id=:id FOR UPDATE
            """), {"id": trial_id})).mappings().first()
            if not row:
                raise HTTPException(404, "Solicitação não encontrada")
            if row["status"] != "pending" or row["version"] != decision.expected_version:
                raise HTTPException(409, "Solicitação alterada; atualize a lista")
            existing = (await db.execute(text("""
                SELECT 1 FROM subscriptions WHERE tenant_id=:tenant
                  AND status IN ('active','trialing') LIMIT 1
            """), {"tenant": row["tenant_id"]})).scalar_one_or_none()
            if existing and decision.action == "activate":
                raise HTTPException(409, "Empresa já possui assinatura ativa")
            result = (await db.execute(text("""
                UPDATE orion_plan_trial_requests
                SET status=CASE WHEN :action='activate' THEN 'active' ELSE 'rejected' END,
                    started_at=CASE WHEN :action='activate' THEN now() ELSE NULL END,
                    ends_at=CASE WHEN :action='activate' THEN now()+INTERVAL '3 days' ELSE NULL END,
                    reviewed_by=:admin,reviewed_at=now(),version=version+1
                WHERE id=:id RETURNING status,started_at,ends_at,version
            """), {"action": decision.action, "admin": admin, "id": trial_id})).mappings().one()
    return dict(result)


def _exact_offer(offers: list, plan: dict):
    matched = []
    for offer in offers:
        if not isinstance(offer, dict) or offer.get("status") != "active":
            continue
        if offer.get("type") != "subscription":
            continue
        # Cakto subscription uses a 30-day recurrence_period. intervalType
        # describes one-time offers and must not be used to claim monthly billing.
        if offer.get("recurrence_period") not in {30, "30"}:
            continue
        if not isinstance(offer.get("name"), str):
            continue
        if offer["name"].strip().casefold() not in {n.casefold() for n in plan["offer_names"]}:
            continue
        try:
            raw_cents = Decimal(str(offer.get("price"))) * 100
            if raw_cents != raw_cents.to_integral_value():
                continue
            cents = int(raw_cents)
        except (ValueError, InvalidOperation, TypeError):
            continue
        if cents == plan["price_cents"] and isinstance(offer.get("id"), str):
            matched.append(offer)
    return matched[0] if len(matched) == 1 else None


@router.post("/api/v1/tenants/{tenant_id}/management/plan-checkout")
async def start_plan_checkout(tenant_id: UUID, payload: CheckoutChoice,
                              actor: Actor = Depends(authenticate)):
    access = await resolve_tenant_access(actor, tenant_id, "publish")
    account_id = _owner_account(access, actor)
    plan = CODES.get(payload.plan_code)
    if not plan:
        raise HTTPException(422, "Escolha um plano válido")
    if os.getenv("CAKTO_BILLING_LIVE_ENABLED", "0") != "1":
        raise HTTPException(409, "Contratações em preparação. A integração de pagamentos ainda está em homologação.")
    try:
        offers = await CaktoClient().list_offers()
    except (httpx.HTTPError, ValueError, RuntimeError):
        raise HTTPException(503, "Não foi possível confirmar as ofertas na Cakto") from None
    offer = _exact_offer(offers, plan)
    if offer is None:
        raise HTTPException(409, "A oferta mensal deste plano ainda não corresponde ao valor apresentado. Nenhuma cobrança foi iniciada.")
    # An explicit allowlist is checked after the offer name, price and recurrence.
    checkout = plan["checkout_url"]
    # Only approved checkout host and path are redirectable.
    parts = urlsplit(checkout)
    if (parts.scheme != "https" or parts.netloc != "pay.cakto.com.br"
            or not parts.path.startswith("/") or parts.query or parts.fragment):
        raise HTTPException(503, "Checkout seguro não configurado")
    async with support_session_factory()() as db:
        async with db.begin():
            await _ensure_owner(db, tenant_id, account_id)
            await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
                             {"key": "orion-checkout:" + str(tenant_id)})
            subscription = (await db.execute(text("""
                SELECT status FROM subscriptions WHERE tenant_id=:tenant FOR UPDATE
            """), {"tenant": tenant_id})).scalar_one_or_none()
            if subscription is not None:
                raise HTTPException(409, "Esta empresa já possui um registro de assinatura. Regularização ou troca exige confirmação específica.")
            previous = (await db.execute(text("""
                SELECT 1 FROM orion_cakto_checkout_intents
                WHERE tenant_id=:tenant AND state='pending' AND expires_at>now()
                LIMIT 1 FOR UPDATE
            """), {"tenant": tenant_id})).scalar_one_or_none()
            if previous:
                raise HTTPException(409, "Já existe uma tentativa de contratação em andamento. Aguarde ou entre em contato com o suporte.")
            token = secrets.token_urlsafe(24)
            digest = hashlib.sha256(token.encode()).hexdigest()
            await db.execute(text("""
                INSERT INTO orion_cakto_checkout_intents
                  (tenant_id,account_id,plan_code,offer_id,expected_cents,callback_hash,expires_at)
                VALUES (:tenant,:account,:plan,:offer,:cents,:digest,now()+INTERVAL '1 hour')
            """), {"tenant": tenant_id, "account": account_id, "plan": payload.plan_code,
                   "offer": offer["id"], "cents": plan["price_cents"], "digest": digest})
    return {"checkout_url": checkout + "?callback=" + token,
            "plan_code": payload.plan_code, "monthly_cents": plan["price_cents"],
            "status": "pending_payment"}


async def apply_verified_cakto_payment(event: dict, order: dict) -> str:
    """Fail-closed first-payment binding, scoped by a signed callback and API order."""
    token_hash = event.get("callback_hash")
    if not token_hash or event.get("event_type") != "purchase_approved":
        return "unlinked"
    order_id = event.get("external_order_id")
    subscription_id = order.get("subscription")
    if (not isinstance(subscription_id, str) or not subscription_id or len(subscription_id) > 120
            or not isinstance(order_id, str) or order.get("status") != "paid"
            or order.get("type") != "subscription" or order.get("offer_type") != "main"):
        return "manual_review"
    remote_offer = order.get("offer")
    remote_offer_id = remote_offer.get("id") if isinstance(remote_offer, dict) else None
    # Cakto GET /orders/{id}/ may omit the offer object entirely.
    # The event offer ID is signed and must match the intent; if the GET
    # also supplies an offer ID it must corroborate the signed event.
    if remote_offer_id is not None and remote_offer_id != event.get("external_offer_id"):
        return "manual_review"
    try:
        raw_cents = Decimal(str(order.get("amount"))) * 100
        if raw_cents != raw_cents.to_integral_value():
            return "manual_review"
        amount = int(raw_cents)
    except (InvalidOperation, ValueError, TypeError):
        return "manual_review"
    # Require both webhook and GET to match the agreed full BRL monthly amount.
    async with support_session_factory()() as db:
        async with db.begin():
            intent = (await db.execute(text("""
                SELECT id,tenant_id,account_id,plan_code,offer_id,expected_cents,
                       state,expires_at FROM orion_cakto_checkout_intents
                WHERE callback_hash=:hash FOR UPDATE
            """), {"hash": token_hash})).mappings().first()
            if not intent:
                return "unlinked"
            if intent["state"] == "verified":
                return "already_verified"
            if intent["state"] != "pending" or intent["expires_at"] < datetime.now(timezone.utc):
                return "manual_review"
            expected_checkout = CODES[intent["plan_code"]]["checkout_url"]
            actual_checkout = order.get("checkoutUrl")
            if not isinstance(actual_checkout, str) or not expected_checkout:
                return "manual_review"
            expected_parts, actual_parts = urlsplit(expected_checkout), urlsplit(actual_checkout)
            if (expected_parts.scheme!="https" or actual_parts.scheme!="https"
                    or expected_parts.netloc!="pay.cakto.com.br"
                    or actual_parts.netloc!=expected_parts.netloc
                    or expected_parts.path!=actual_parts.path):
                return "manual_review"
            if (intent["offer_id"] != event.get("external_offer_id")
                    or (event.get("external_subscription_id") is not None
                        and event["external_subscription_id"] != subscription_id)
                    or amount != intent["expected_cents"]
                    or event.get("amount_cents") != intent["expected_cents"]
                    or event.get("currency") != "BRL"
                    or order.get("currency") != "BRL"):
                return "manual_review"
            await _ensure_owner(db, intent["tenant_id"], intent["account_id"])
            other = (await db.execute(text("""
                SELECT tenant_id FROM orion_cakto_bindings
                WHERE external_subscription_id=:external FOR UPDATE
            """), {"external": subscription_id})).scalar_one_or_none()
            if other is not None:
                return "manual_review"
            current = (await db.execute(text("""
                SELECT id,status,external_subscription_id
                FROM subscriptions WHERE tenant_id=:tenant FOR UPDATE
            """), {"tenant": intent["tenant_id"]})).mappings().first()
            # Never overwrite a legacy Asaas subscription or another tenant's bill.
            if current:
                return "manual_review"
            db_code = CODES[intent["plan_code"]]["db_code"]
            plan_id = (await db.execute(text("""
                SELECT id FROM plans WHERE code=:code AND active
            """), {"code": db_code})).scalar_one_or_none()
            if plan_id is None:
                return "manual_review"
            subscription_row = (await db.execute(text("""
                INSERT INTO subscriptions
                  (tenant_id,plan_id,external_subscription_id,status,last_verified_payment_at)
                VALUES (:tenant,:plan,:external,'active',now())
                RETURNING id
            """), {"tenant": intent["tenant_id"], "plan": plan_id,
                   "external": subscription_id})).scalar_one()
            await db.execute(text("""
                INSERT INTO orion_cakto_bindings
                  (tenant_id,subscription_id,external_subscription_id,plan_code,last_order_id)
                VALUES (:tenant,:sub,:external,:plan,:order_id)
            """), {"tenant": intent["tenant_id"], "sub": subscription_row,
                   "external": subscription_id, "plan": intent["plan_code"],
                   "order_id": order_id})
            await db.execute(text("""
                UPDATE orion_cakto_checkout_intents
                SET state='verified',external_order_id=:order_id,verified_at=now()
                WHERE id=:id
            """), {"id": intent["id"], "order_id": order_id})
    return "activated"
