"""Cakto billing intake. Only records verified-origin events; never grants a plan.

No customer data, complete request bodies or credentials are persisted.
Entitlements remain explicitly disabled until checkout-to-account binding is built.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time
from decimal import Decimal, InvalidOperation
from functools import lru_cache

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.catalog_api import Actor, authenticate, support_only, support_session_factory
from app.cakto_readiness import direct_environment_ready, webhook_secret_valid
from app.plan_catalog import CODES

router = APIRouter(tags=["Cakto - infraestrutura"])
BASE = "https://api.cakto.com.br/public_api"
ORDER_ID = re.compile(r"^[A-Za-z0-9_-]{1,255}$")
EVENTS = frozenset({
    "purchase_approved", "purchase_refused", "subscription_renewed",
    "subscription_canceled", "refund", "chargeback",
})
# Cakto public labels, prices and checkout URLs use the single Orion catalog.
# Only the read-only offer matcher sees offer aliases such as "Orion Pro".
PLANS = tuple(
    {"code": code, "name": plan["name"], "monthly_cents": plan["price_cents"],
     "checkout_url": plan["checkout_url"], "offer_names": plan["offer_names"]}
    for code, plan in CODES.items()
)


@router.get("/api/public/cakto/plans")
async def public_cakto_plans():
    """Only a static catalog; this endpoint NEVER asserts that payment is active."""
    return {
        "plans": [{key: plan[key] for key in ("code", "name", "monthly_cents")} for plan in PLANS],
        # Environment configuration is only the first gate. A checkout also
        # requires live provider webhook and recurring payment verification.
        "subscription_activation_enabled": direct_environment_ready(),
    }


def verify_cakto_signature(raw_body: bytes, timestamp: str | None,
                           signature: str | None, secret: str) -> bool:
    """Only accept current HMAC-SHA256 v1 over timestamp.raw_body."""
    if (not isinstance(secret, str) or len(secret) < 32 or not timestamp
            or not isinstance(signature, str)
            or re.fullmatch(r"v1=[a-f0-9]{64}", signature) is None):
        return False
    if not timestamp.isascii() or not timestamp.isdecimal() or len(timestamp) > 12:
        return False
    if abs(time.time() - int(timestamp)) > 300:
        return False
    expected = hmac.new(secret.encode(), timestamp.encode() + b"." + raw_body,
                        hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, "v1=" + expected)


def _subscription_id(value):
    if isinstance(value, str):
        return value[:255]
    if isinstance(value, dict) and isinstance(value.get("id"), str):
        return value["id"][:255]
    return None


def normalized_events(payload: dict) -> list[dict]:
    """Supports Cakto V1 dict and V2 list, excluding bumps and personal data."""
    event = payload.get("event")
    if not isinstance(event, str) or len(event) > 64:
        raise HTTPException(400, "Evento inválido")
    if event not in EVENTS:
        return []
    data = payload.get("data")
    items = data if isinstance(data, list) else [data]
    if len(items) > 32 or any(not isinstance(item, dict) for item in items):
        raise HTTPException(400, "Estrutura de pedidos inválida")
    found = []
    for item in items:
        if item.get("offer_type") != "main":
            continue
        order_id, status = item.get("id"), item.get("status")
        if not isinstance(order_id, str) or not ORDER_ID.fullmatch(order_id):
            raise HTTPException(400, "ID de pedido inválido")
        if not isinstance(status, str) or not 1 <= len(status) <= 40:
            raise HTTPException(400, "Status do pedido inválido")
        offer = item.get("offer")
        offer_id = offer.get("id") if isinstance(offer, dict) else None
        if offer_id is not None and (
            not isinstance(offer_id, str) or not 1 <= len(offer_id) <= 80
        ):
            raise HTTPException(400, "Oferta inválida")
        subscription_id = _subscription_id(item.get("subscription"))
        # Cakto supplies the signed checkout callback and optional sck tracking.
        # For newly issued links both carry the same opaque tenant-bound token.
        callback, tracking = item.get("callback"), item.get("sck")
        if callback and tracking and callback != tracking:
            callback_hash = None  # Conflict requires manual review, never activation.
        else:
            token = callback or tracking
            callback_hash = (hashlib.sha256(token.encode()).hexdigest()
                             if isinstance(token, str) and
                             re.fullmatch(r"[A-Za-z0-9._~-]{12,255}", token)
                             else None)
        amount = item.get("amount")
        try:
            amount_decimal = Decimal(str(amount))
            amount_cents = int(amount_decimal * 100) if amount_decimal == Decimal(int(amount_decimal * 100)) / 100 else None
        except (InvalidOperation, ValueError, TypeError):
            amount_cents = None
        currency = item.get("currency") if item.get("currency") == "BRL" else None
        # Stable across retries and new delivery signatures. No PII in the key.
        identity = {
            "event": event, "order_id": order_id, "status": status,
            "offer_id": offer_id, "subscription_id": subscription_id,
            "paid_at": item.get("paidAt"), "refunded_at": item.get("refundedAt"),
            "charged_back_at": item.get("chargedbackAt"),
            "canceled_at": item.get("canceledAt"),
        }
        digest = hashlib.sha256(
            json.dumps(identity, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=True).encode()
        ).hexdigest()
        found.append({
            "digest": digest, "event": event, "order_id": order_id,
            "status": status, "offer_id": offer_id,
            "subscription_id": subscription_id,
            "callback_hash": callback_hash, "amount_cents": amount_cents,
            "currency": currency,
        })
    return found


async def persist_events(events: list[dict]) -> None:
    if not events:
        return
    async with support_session_factory()() as db:
        async with db.begin():
            for event in events:
                await db.execute(text("""
                    INSERT INTO cakto_webhook_events
                      (delivery_digest,external_order_id,event_type,reported_status,
                       external_offer_id,external_subscription_id,callback_hash,amount_cents,currency)
                    VALUES (:digest,:order_id,:event,:status,:offer_id,:subscription_id,
                            :callback_hash,:amount_cents,:currency)
                    ON CONFLICT (delivery_digest) DO NOTHING
                """), event)


@router.post("/webhooks/cakto")
async def cakto_webhook(request: Request):
    secret = os.getenv("CAKTO_WEBHOOK_SECRET", "")
    if len(secret) < 32:
        # Deliberately never log the secret or its actual length.
        import logging
        logging.getLogger("orion.cakto").error("cakto_webhook_503_secret_not_configured")
        raise HTTPException(503, "Integração Cakto não configurada")
    # Enforce the limit WHILE consuming the body, not after Starlette has
    # already buffered arbitrarily large unauthenticated requests.
    max_bytes = 131072
    claimed = request.headers.get("content-length")
    if claimed is not None:
        try:
            size = int(claimed)
            if size < 0:
                raise ValueError()
        except (TypeError, ValueError):
            raise HTTPException(400, "Tamanho do evento inválido") from None
        if size > max_bytes:
            raise HTTPException(413, "Evento muito grande")
    parts = []
    size = 0
    async for part in request.stream():
        size += len(part)
        if size > max_bytes:
            raise HTTPException(413, "Evento muito grande")
        parts.append(part)
    raw = b"".join(parts)
    if not verify_cakto_signature(
        raw, request.headers.get("x-cakto-timestamp"),
        request.headers.get("x-cakto-signature"), secret
    ):
        raise HTTPException(401, "Origem do webhook não autenticada")
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise HTTPException(400, "JSON inválido") from None
    if not isinstance(payload, dict):
        raise HTTPException(400, "Evento inválido")
    body_secret = payload.get("secret")
    if not isinstance(body_secret, str) or not hmac.compare_digest(body_secret, secret):
        raise HTTPException(401, "Segredo do webhook inválido")
    events = normalized_events(payload)
    try:
        await persist_events(events)
    except (SQLAlchemyError, RuntimeError):
        # Distinguish storage unavailability from a missing HMAC secret.
        # Never log the exception (it may contain financial identifiers).
        import logging
        logging.getLogger("orion.cakto").error("cakto_webhook_503_receipt_store_unavailable")
        # Cakto will retry on non-2xx; never ACK a lost financial event.
        raise HTTPException(503, "Recebimento temporariamente indisponível") from None
    return {"received": True}


class CaktoClient:
    """Server-only OAuth2 and read-only orders/offers, bounded token reuse."""
    def __init__(self):
        self.client_id = os.getenv("CAKTO_CLIENT_ID", "")
        self.client_secret = os.getenv("CAKTO_CLIENT_SECRET", "")
        self._token = None
        self._expires = 0.0

    @property
    def configured(self):
        return bool(self.client_id and self.client_secret)

    async def token(self):
        if self._token and time.monotonic() < self._expires:
            return self._token
        if not self.configured:
            raise RuntimeError("Cakto não configurada")
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
            response = await client.post(
                BASE + "/token/",
                data={"client_id": self.client_id, "client_secret": self.client_secret},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            response.raise_for_status()
            payload = response.json()
        token = payload.get("access_token")
        if not isinstance(token, str) or not token:
            raise RuntimeError("Token Cakto inválido")
        self._token = token
        self._expires = time.monotonic() + min(
            max(int(payload.get("expires_in", 3600)) - 120, 30), 35000
        )
        return token

    async def _get(self, path: str, **params):
        bearer = await self.token()
        async with httpx.AsyncClient(timeout=12, follow_redirects=False) as client:
            response = await client.get(
                BASE + path, params=params,
                headers={"Authorization": "Bearer " + bearer},
            )
            response.raise_for_status()
            return response.json()

    async def get_order(self, order_id: str):
        if not ORDER_ID.fullmatch(order_id):
            raise ValueError("ID de pedido inválido")
        return await self._get("/orders/" + order_id + "/")

    async def list_offers(self):
        result = []
        for page in range(1, 11):
            data = await self._get("/offers/", page=page, limit=100)
            if not isinstance(data, dict) or not isinstance(data.get("results"), list):
                raise RuntimeError("Resposta de ofertas inválida")
            result.extend(data["results"])
            if not data.get("next"):
                return result
        raise RuntimeError("Paginação de ofertas ultrapassou o limite seguro")


@lru_cache(maxsize=1)
def cached_cakto_client():
    return CaktoClient()


def match_plans(offers: list) -> list[dict]:
    """Match only exact offer names and cents; never infer an ID from a checkout URL."""
    matches = []
    for plan in PLANS:
        ids = []
        for offer in offers:
            if not isinstance(offer, dict):
                continue
            name = offer.get("name")
            if not isinstance(name, str) or name.strip().casefold() not in {
                    alias.casefold() for alias in plan["offer_names"]}:
                continue
            if offer.get("type") != "subscription" or offer.get("recurrence_period") not in {30, "30"}:
                continue
            try:
                raw_cents = Decimal(str(offer.get("price"))) * 100
                if raw_cents != raw_cents.to_integral_value():
                    continue
                cents = int(raw_cents)
            except (ValueError, TypeError, InvalidOperation):
                continue
            offer_id = offer.get("id")
            if (cents == plan["monthly_cents"] and offer.get("status") == "active"
                    and isinstance(offer_id, str) and 1 <= len(offer_id) <= 120):
                ids.append(offer_id)
        matches.append({"code": plan["code"], "found": len(ids) == 1,
                        "offer_id": ids[0] if len(ids) == 1 else None,
                        "ambiguous": len(ids) > 1})
    return matches


@router.get("/api/v1/admin/cakto/connection")
async def admin_cakto_connection(actor: Actor = Depends(authenticate)):
    support_only(actor)
    client = cached_cakto_client()
    result = {
        "api_connected": False,
        "webhook_secret_configured": webhook_secret_valid(),
        "offers": [],
        "webhook_offer_coverage": 0,
        "webhook_status": "not_verified",
        # A positive provider inventory is NOT proof of a signed delivery.
        "billing_ready": False,
    }
    if not client.configured:
        return result
    try:
        offers = await client.list_offers()
    except (httpx.HTTPError, ValueError, RuntimeError):
        return result
    result["api_connected"] = True
    result["offers"] = match_plans(offers)
    if not result["webhook_secret_configured"]:
        return result
    try:
        from scripts.probe_orion_webhooks_read_only import probe
        coverage = await probe(client, offers)
    except (httpx.HTTPError, ValueError, RuntimeError):
        result["webhook_status"] = "provider_check_unavailable"
        return result
    result["webhook_offer_coverage"] = coverage["webhook_ready"]
    result["webhook_status"] = (
        "registered" if coverage["webhook_ready"] == len(CODES) else "incomplete"
    )
    if direct_environment_ready():
        try:
            # Live payments need provider proof beyond the environment flags,
            # including recurrent methods and validated webhook coverage.
            from app.orion_direct_checkout import require_verified_webhooks
            await require_verified_webhooks(client)
        except (HTTPException, httpx.HTTPError, ValueError, RuntimeError):
            pass
        else:
            result["billing_ready"] = True
    return result


def verify_order_event(event: dict, order: dict) -> str:
    """A payment receipt is not a subscription, and never activates a tenant."""
    if not isinstance(order, dict) or str(order.get("id")) != event["external_order_id"]:
        return "mismatch"
    if order.get("offer_type") not in ("main", None):
        return "mismatch"
    remote_offer = order.get("offer")
    remote_offer_id = remote_offer.get("id") if isinstance(remote_offer, dict) else None
    if remote_offer_id and event["external_offer_id"] and (
        str(remote_offer_id) != event["external_offer_id"]
    ):
        return "mismatch"
    expected = {
        "purchase_approved": {"paid"},
        "subscription_renewed": {"paid"},
        "purchase_refused": {"refused"},
        "refund": {"refunded"},
        "chargeback": {"chargedback", "MED"},
    }
    if event["event_type"] not in expected:
        # Subscription cancellation must be checked against the subscriptions API,
        # which is NOT covered by the credential scopes supplied.
        return "manual_review"
    if order.get("status") in expected[event["event_type"]]:
        return "verified"
    if order.get("status") in {"refunded", "chargedback", "canceled", "MED"}:
        return "superseded"
    return "mismatch"


async def scan_pending_cakto(_ctx):
    """Worker leases pending receipts; failures retry without acknowledging verification."""
    client = cached_cakto_client()
    if not client.configured:
        return 0
    async with support_session_factory()() as db:
        async with db.begin():
            # No event can arrive until the API migration finishes. A parallel
            # worker deployment must not try to read a non-existent table.
            ready = (await db.execute(text("""
                SELECT to_regclass('public.cakto_webhook_events') IS NOT NULL
                 AND to_regclass('public.orion_direct_payment_attempts') IS NOT NULL
                 AND (SELECT count(*) FROM information_schema.columns
                      WHERE table_schema='public' AND table_name='cakto_webhook_events'
                      AND column_name IN ('callback_hash','amount_cents','currency'))=3
            """))).scalar_one()
            if not ready:
                return 0
            rows = (await db.execute(text("""
                SELECT delivery_digest,external_order_id,external_offer_id,event_type,
                       external_subscription_id,callback_hash,amount_cents,currency
                FROM cakto_webhook_events
                WHERE verification_state='pending' AND next_attempt_at<=now()
                  AND (:lifecycle_enabled
                       OR event_type NOT IN (
                          'subscription_renewed','subscription_canceled',
                          'purchase_refused','refund','chargeback'))
                ORDER BY received_at LIMIT 30 FOR UPDATE SKIP LOCKED
            """), {"lifecycle_enabled":
                    os.getenv("ORION_CAKTO_LIFECYCLE_ENABLED", "0") == "1"}
            )).mappings().all()
            for row in rows:
                await db.execute(text("""
                    UPDATE cakto_webhook_events
                    SET attempts=attempts+1,
                        next_attempt_at=now()+make_interval(
                            secs=>LEAST(3600,20*power(2,LEAST(attempts,7))::int)),
                        verification_state=CASE WHEN attempts>=7 THEN 'failed'
                                                ELSE 'pending' END
                    WHERE delivery_digest=:digest
                """), {"digest": row["delivery_digest"]})
    for row in rows:
        try:
            order = await client.get_order(row["external_order_id"])
            state = verify_order_event(row, order)
            if state == "verified" and row["callback_hash"] and row["event_type"] == "purchase_approved":
                from app.plan_management import apply_verified_cakto_payment
                # No tenancy is derived from buyer details. The callback must
                # match a server-side owner intent and the exact verified offer.
                subscription_id = _subscription_id(order.get("subscription"))
                if subscription_id and ORDER_ID.fullmatch(subscription_id):
                    subscription = await client._get("/subscriptions/"+subscription_id+"/")
                    linked = await apply_verified_cakto_payment(
                        row, order, subscription=subscription)
                else:
                    linked = "manual_review"
                if linked == "retry_later":
                    # A direct PSP response may still be recording its order ID.
                    # Preserve pending receipt; the bounded worker will retry.
                    continue
                if linked == "manual_review":
                    state = "manual_review"
            if os.getenv("ORION_CAKTO_LIFECYCLE_ENABLED", "0") == "1" and row["event_type"] in {
                    "subscription_renewed", "subscription_canceled",
                    "purchase_refused", "refund", "chargeback"}:
                from app.cakto_subscription_lifecycle import (
                    reconcile_existing_cakto_subscription,
                )
                outcome = await reconcile_existing_cakto_subscription(row,order,client)
                state = "verified" if outcome in ("reconciled","already_verified") else "manual_review"
        except (httpx.HTTPError, RuntimeError, ValueError):
            continue
        async with support_session_factory()() as db:
            async with db.begin():
                await db.execute(text("""
                    UPDATE cakto_webhook_events SET
                      verification_state=:state,verified_at=CASE
                        WHEN :state='verified' THEN now() ELSE verified_at END
                    WHERE delivery_digest=:digest
                """), {"state": state, "digest": row["delivery_digest"]})
    return len(rows)
