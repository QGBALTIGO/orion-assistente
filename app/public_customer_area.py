"""Customer area for published Orion sites.

Barbershops accept the existing customer's phone and complete birth date,
with strict attempt limits and a real prior booking. Other business types
retain private-link verification. All access remains scoped to the tenant.
"""
import hashlib
import hmac
import json
import os
import re
import secrets
from datetime import date, datetime, timezone
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import Field, StrictBool, field_validator
from redis.exceptions import RedisError
from sqlalchemy import text

from app import appointment_access as access
from app.appointment_errors import sanitized_database_errors
from app.booking_service import read_appointments
from app.catalog_schemas import StrictModel
from app.database import get_session_factory, tenant_transaction
from app.management_schemas import normalize_phone
from app.panel_auth import _redis
from app.plan_permissions import has_feature, lock_plan
from app.public_origins import canonical_public_origin, legacy_public_origins
from app.public_site_api import public_limit, published_guard, resolve_public
from app.short_appointment_api import validate_origin

router = APIRouter(prefix="/api/v1/public/sites/{slug}/account",
                   tags=["Área privada do cliente"])
PRIVATE = {
    "Cache-Control": "private, no-store, max-age=0",
    "Pragma": "no-cache",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Robots-Tag": "noindex, nofollow, noarchive",
}
TOKEN = re.compile(r"^[A-Za-z0-9_-]{43}$")
DIGEST = re.compile(r"^[a-f0-9]{64}$")
CODE = re.compile(r"^AG-[A-HJ-NP-Z2-9]{6}(?:-[A-F0-9]{8})?$")
PROOF = re.compile(r"^[A-Za-z0-9_-]{27}$")
PHONE = re.compile(r"^(?:\+55[1-9][0-9]{9,10}|\+5959[0-9]{8})$")
SCOPE = "orion:customer-area:v1:"
REMEMBER_SECONDS = 30 * 24 * 3600
BROWSER_SECONDS = 12 * 3600
PAGE_SIZE = 20
NEEDS_LINK = {"authenticated": False, "proof_required": True}
UNVERIFIED = "Não foi possível validar o acesso. Confira seu telefone e o link privado de um agendamento deste estabelecimento."


def reply(value, status=200):
    return JSONResponse(jsonable_encoder(value), status_code=status, headers=PRIVATE)


def fail(status, message):
    raise HTTPException(status, message, headers=PRIVATE)


def enabled():
    return (os.getenv("ORION_CUSTOMER_PORTAL_ENABLED", "1") == "1"
            and len(os.getenv("INTEGRATION_FERNET_KEY", "")) >= 32
            and bool(os.getenv("REDIS_URL")))


def require_enabled():
    if not enabled():
        fail(503, "A área do cliente está temporariamente indisponível.")


class PhoneInput(StrictModel):
    phone: str = Field(min_length=10, max_length=40)

    @field_validator("phone")
    @classmethod
    def valid_phone(cls, value):
        result = normalize_phone(value)
        if not result or not PHONE.fullmatch(result):
            raise ValueError("Confira seu telefone.")
        return result


class EnterInput(PhoneInput):
    birth_date: date | None = None
    remember: StrictBool = True

    @field_validator("birth_date")
    @classmethod
    def valid_birth(cls, value):
        if value is not None and not date(1900, 1, 1) <= value <= datetime.now(timezone.utc).date():
            raise ValueError("Confira a data de nascimento.")
        return value


class LinkInput(PhoneInput):
    booking_link: str = Field(min_length=20, max_length=650)
    remember: StrictBool = True


def phone_fingerprint(phone):
    secret = os.environ.get("INTEGRATION_FERNET_KEY", "")
    if len(secret) < 32:
        fail(503, "A área do cliente está temporariamente indisponível.")
    return hmac.new(secret.encode(), ("orion:customer-area:phone:v1:" + phone).encode(),
                    hashlib.sha256).hexdigest()


def birth_fingerprint(phone, birth):
    secret = os.environ.get("INTEGRATION_FERNET_KEY", "")
    if len(secret) < 32:
        fail(503, "A área do cliente está temporariamente indisponível.")
    return hmac.new(secret.encode(),
                    ("orion:customer-area:birth:v1:" + phone + "|" + birth.isoformat()).encode(),
                    hashlib.sha256).hexdigest()


BIRTH_FAILURE = "Não foi possível acessar. Confira seu telefone e data de nascimento cadastrados. Se ainda não agendou, faça seu primeiro agendamento. Para corrigir dados antigos, fale com o estabelecimento."


async def barber_access(db, tenant):
    """Allow simplified login only for an explicitly identified barbershop.

    Match the existing onboarding/profile source of truth; never infer from
    the public site title or let a client select its own business type.
    """
    await lock_plan(db, tenant)
    return (await db.execute(text("""
        SELECT CASE WHEN b.tenant_id IS NOT NULL
                    THEN b.configuration->>'niche' = 'barbearia'
                    ELSE p.profile->'categories' = '["barbearia"]'::jsonb END
        FROM (VALUES (1)) AS anchor(value)
        LEFT JOIN business_onboarding b ON b.tenant_id=:t
        LEFT JOIN business_profile_state s ON s.tenant_id=:t
        LEFT JOIN business_profile_versions p
          ON p.tenant_id=s.tenant_id AND p.id=s.published_version_id
    """), {"t": tenant})).scalar_one() is True


async def login_method(slug, tenant):
    async with get_session_factory()() as db:
        async with tenant_transaction(db, tenant):
            await published_guard(db, tenant, slug)
            if await barber_access(db, tenant):
                return "birth_date"
    return "private_link" if access.enabled() else None


async def birth_attempt_limits(request, tenant, phone):
    fingerprint = phone_fingerprint(phone)
    await public_limit(request, str(tenant), "customer-birth-ip", 20, 300)
    await public_limit(request, str(tenant), "customer-birth-phone", 5, 86400,
                       subject=fingerprint)
    # The same telephone cannot be guessed without limit using site aliases,
    # other company slugs, a new cookie, or the booking convenience path.
    await public_limit(request, "all", "customer-birth-phone-global", 10, 86400,
                       subject=fingerprint)


async def birth_customer(db, tenant, phone, birth):
    """Only existing non-deleted customers with a real accepted appointment.

    A cancelled hold is not a completed first booking. A public booking may
    initialize a new customer's DOB, but never overwrites an existing DOB.
    """
    return (await db.execute(text("""
        SELECT c.id,c.name,c.whatsapp_e164,c.birth_date,c.version,a.id AS appointment_id
        FROM customers c JOIN appointments a
          ON a.tenant_id=c.tenant_id AND a.customer_id=c.id
        WHERE c.tenant_id=:t AND c.whatsapp_e164=:phone AND c.birth_date=:birth
          AND c.active AND c.deleted_at IS NULL AND NOT a.customer_access_blocked
          AND (a.status IN ('confirmed','completed','no_show') OR
               (a.status='cancelled' AND EXISTS(
                   SELECT 1 FROM appointment_history h
                   WHERE h.tenant_id=a.tenant_id AND h.appointment_id=a.id
                     AND h.event='confirmed')))
        ORDER BY a.start_at DESC,a.id DESC LIMIT 1 FOR SHARE OF a,c
    """), {"t": tenant, "phone": phone, "birth": birth})).mappings().one_or_none()


def cookie_name(tenant):
    # Independent host-only cookie for each company, including slug aliases.
    return "__Host-orion_customer_" + hashlib.sha256(str(tenant).encode()).hexdigest()[:20]


def session_key(raw):
    return SCOPE + "session:" + hashlib.sha256(raw.encode()).hexdigest()


async def cache_call(operation):
    cache = _redis()
    try:
        return await operation(cache)
    except (RedisError, OSError):
        fail(503, "Não foi possível consultar sua sessão agora. Tente novamente.")
    finally:
        await cache.aclose()


def make_claims(tenant, customer, appointment, legacy_digest=None, *, auth_method="private_link"):
    claims = {
        "tenant": str(tenant), "customer": str(customer["id"]),
        "version": int(customer["version"]),
        "phone_hash": phone_fingerprint(customer["whatsapp_e164"]),
        "appointment": str(appointment),
        "legacy_digest": legacy_digest,
        "auth_method": auth_method,
    }
    if auth_method == "birth_date":
        claims["birth_hash"] = birth_fingerprint(customer["whatsapp_e164"], customer["birth_date"])
    return claims


async def read_session(request, tenant):
    raw = request.cookies.get(cookie_name(tenant), "")
    if not TOKEN.fullmatch(raw):
        return None
    value = await cache_call(lambda cache: cache.get(session_key(raw)))
    try:
        claims = json.loads(value) if value else None
        if (not isinstance(claims, dict) or claims["tenant"] != str(tenant)
                or type(claims["version"]) is not int or claims["version"] < 1
                or not DIGEST.fullmatch(claims["phone_hash"])):
            return None
        UUID(claims["customer"])
        UUID(claims["appointment"])
        method = claims.get("auth_method", "private_link")
        if method not in {"private_link", "birth_date"}:
            return None
        if method == "birth_date" and not DIGEST.fullmatch(claims.get("birth_hash", "")):
            return None
        legacy = claims.get("legacy_digest")
        if legacy is not None and not DIGEST.fullmatch(legacy):
            return None
        return claims
    except (ValueError, KeyError, TypeError):
        return None


async def issue_session(request, tenant, claims, response, remember):
    raw = secrets.token_urlsafe(32)
    seconds = REMEMBER_SECONDS if remember else BROWSER_SECONDS
    packed = json.dumps(claims, separators=(",", ":"))
    previous = request.cookies.get(cookie_name(tenant), "")

    async def store(cache):
        saved = await cache.set(session_key(raw), packed, ex=seconds, nx=True)
        if saved and TOKEN.fullmatch(previous):
            await cache.delete(session_key(previous))
        return saved

    if not await cache_call(store):
        fail(503, "Não foi possível iniciar sua sessão. Tente novamente.")
    response.set_cookie(cookie_name(tenant), raw, path="/", secure=True,
                        httponly=True, samesite="lax",
                        max_age=seconds if remember else None)


def parse_booking_link(value):
    """Accept only a trusted private URL; public protocols grant no access."""
    try:
        parts = urlsplit(value.strip())
        origin = parts.scheme + "://" + parts.netloc.lower()
        if (parts.scheme != "https" or parts.username or parts.password or
                parts.query or origin not in {canonical_public_origin(),
                                               *legacy_public_origins()}):
            raise ValueError()
        if parts.path == "/a":
            tenant, digest = access.parse_token(parts.fragment)
            return ("legacy", tenant, digest)
        code = parts.path.removeprefix("/")
        if CODE.fullmatch(code) and PROOF.fullmatch(parts.fragment):
            return ("direct", code, parts.fragment)
    except (ValueError, TypeError, HTTPException):
        pass
    fail(403, UNVERIFIED)


async def customer_from_appointment(db, tenant, appointment, customer=None):
    return (await db.execute(text("""
        SELECT c.id,c.name,c.whatsapp_e164,c.birth_date,c.version
        FROM appointments a
        JOIN customers c ON c.tenant_id=a.tenant_id AND c.id=a.customer_id
        WHERE a.tenant_id=:t AND a.id=:a
          AND (CAST(:customer AS uuid) IS NULL OR c.id=:customer)
          AND c.active AND c.deleted_at IS NULL
          AND a.status<>'hold' AND NOT a.customer_access_blocked
        FOR SHARE OF c,a
    """), {"t": tenant, "a": appointment, "customer": customer})).mappings().one_or_none()


async def verified_customer(db, tenant, claims):
    birth_login = claims.get("auth_method") == "birth_date"
    if birth_login:
        if not await barber_access(db, tenant):
            return None
    else:
        try:
            access.require_tenant(tenant)
        except HTTPException:
            return None
    row = await customer_from_appointment(
        db, tenant, UUID(claims["appointment"]), UUID(claims["customer"]))
    if (not row or not row["whatsapp_e164"] or row["version"] != claims["version"]
            or not hmac.compare_digest(phone_fingerprint(row["whatsapp_e164"]),
                                       claims["phone_hash"])):
        return None
    if birth_login:
        if not row["birth_date"] or not hmac.compare_digest(
                birth_fingerprint(row["whatsapp_e164"], row["birth_date"]), claims["birth_hash"]):
            return None
    if claims.get("legacy_digest"):
        try:
            identity = await access.verify(db, tenant, claims["legacy_digest"])
            if (identity.customer_id != row["id"] or
                    str(identity.appointment_id) != claims["appointment"]):
                return None
        except HTTPException:
            return None
    return row


async def proof_customer(db, tenant, parsed, phone):
    """All CRM queries use the resolved published tenant and a valid proof."""
    try:
        access.require_tenant(tenant)
    except HTTPException:
        return None
    mode, first, last = parsed
    if mode == "legacy":
        if first != tenant:
            return None
        try:
            identity = await access.verify(db, tenant, last)
        except HTTPException:
            return None
        row = await customer_from_appointment(db, tenant, identity.appointment_id,
                                              identity.customer_id)
        if row and hmac.compare_digest(row["whatsapp_e164"] or "", phone):
            return row, identity.appointment_id, last
        return None
    if not access.short_links_enabled() or not await has_feature(db, tenant, "notifications.basic"):
        return None
    # Match and verify the actual per-customer HMAC inside the tenant transaction.
    # A public AG code, professional proof, cross-company link or old phone
    # cannot establish an account session. Restrict new sessions to recent links.
    row = (await db.execute(text("""
        SELECT a.id AS appointment_id,c.id,c.name,c.whatsapp_e164,c.birth_date,c.version
        FROM appointment_short_codes s
        JOIN appointments a ON a.tenant_id=s.tenant_id AND a.id=s.appointment_id
        JOIN customers c ON c.tenant_id=a.tenant_id AND c.id=a.customer_id
        WHERE s.tenant_id=:t AND s.code=:code
          AND c.active AND c.deleted_at IS NULL AND NOT a.customer_access_blocked
          AND a.status<>'hold' AND a.end_at>clock_timestamp()-interval '90 days'
        FOR SHARE OF a,c
    """), {"t": tenant, "code": first})).mappings().one_or_none()
    if not row or not hmac.compare_digest(row["whatsapp_e164"] or "", phone):
        return None
    try:
        expected = access.direct_link_proof(first, "customer", tenant,
            row["appointment_id"], row["id"], row["whatsapp_e164"], row["version"])
    except (RuntimeError, ValueError):
        return None
    if not hmac.compare_digest(expected, last):
        return None
    return row, row["appointment_id"], None


def profile_data(row):
    return {"name": row["name"], "phone": row["whatsapp_e164"],
            "birth_date": row["birth_date"]}


def booking_item(row):
    """Explicit whitelist: never leak internal notes, IDs or commissions."""
    return {
        "status": row["status"], "start_at": row["start_at"], "end_at": row["end_at"],
        "professional": row["professional_name"],
        "services": [item["name_snapshot"] for item in row["items"]],
        "duration_min": row["reserved_duration_min"],
        "price_cents": row["total_cents"], "price_mode": row["price_mode"],
        "timezone": row["timezone"],
    }


async def appointment_page(db, tenant, customer, kind, status="all", offset=0, limit=PAGE_SIZE):
    condition = ("a.status='confirmed' AND a.start_at>=clock_timestamp()"
                 if kind == "upcoming" else
                 "NOT(a.status='confirmed' AND a.start_at>=clock_timestamp())"
                 if kind == "history" else "TRUE")
    direction = "ASC" if kind == "upcoming" else "DESC"
    # These SQL fragments come exclusively from the fixed enumeration above.
    query = """
        SELECT a.id FROM appointments a
        WHERE a.tenant_id=:t AND a.customer_id=:c
          AND a.status<>'hold' AND NOT a.customer_access_blocked AND (""" + condition + """)
          AND (:status='all' OR a.status=:status)
        ORDER BY a.start_at """ + direction + ",a.id " + direction + """
        LIMIT :limit OFFSET :offset
    """
    ids = (await db.execute(text(query), {"t": tenant, "c": customer,
        "status": status, "limit": limit + 1, "offset": offset})).scalars().all()
    rows = await read_appointments(db, tenant, list(ids[:limit]))
    return {"items": [booking_item(row) for row in rows],
            "has_more": len(ids) > limit, "next_offset": offset + len(rows)}


async def dashboard(db, tenant, customer, zone):
    stats = (await db.execute(text("""
        SELECT count(*) AS appointments,
               count(*) FILTER (WHERE a.status='completed') AS completed,
               count(*) FILTER (WHERE a.status='cancelled') AS cancelled,
               count(*) FILTER (WHERE a.status='no_show') AS no_show,
               COALESCE(sum(a.total_cents) FILTER (WHERE a.status='completed'),0)::bigint AS completed_cents,
               max(a.start_at) FILTER (WHERE a.status='completed') AS last_visit
        FROM appointments a
        WHERE a.tenant_id=:t AND a.customer_id=:c
          AND a.status<>'hold' AND NOT a.customer_access_blocked
    """), {"t": tenant, "c": customer["id"]})).mappings().one()
    next_bookings = await appointment_page(db, tenant, customer["id"], "upcoming", limit=3)
    return {"available": True, "authenticated": True,
            "profile": profile_data(customer), "stats": dict(stats),
            "upcoming": next_bookings, "timezone": zone}


async def existing_dashboard(request, slug, tenant, phone=None):
    claims = await read_session(request, tenant)
    if claims is None:
        return None
    async with get_session_factory()() as db:
        async with tenant_transaction(db, tenant, repeatable_read=True):
            site = await published_guard(db, tenant, slug)
            customer = await verified_customer(db, tenant, claims)
            if not customer or (phone and not hmac.compare_digest(customer["whatsapp_e164"], phone)):
                return None
            return await dashboard(db, tenant, customer, site["timezone"])


@router.get("")
@sanitized_database_errors
async def state(slug: str, request: Request):
    tenant = await resolve_public(slug)
    if not enabled():
        return reply({"available": False, "authenticated": False})
    await public_limit(request, str(tenant), "customer-state", 120, 300)
    result = await existing_dashboard(request, slug, tenant)
    if result:
        return reply(result)
    method = await login_method(slug, tenant)
    if method == "birth_date":
        return reply({"available": True, "authenticated": False, "login_method": method})
    return reply({"available": method is not None, "authenticated": False})


async def birth_login_claims(slug, request, tenant, phone, birth, *, include_dashboard):
    """One shared verifier for manual and opt-in post-booking login."""
    await birth_attempt_limits(request, tenant, phone)
    async with get_session_factory()() as db:
        async with tenant_transaction(db, tenant, repeatable_read=True):
            site = await published_guard(db, tenant, slug)
            if not await barber_access(db, tenant):
                fail(403, BIRTH_FAILURE)
            customer = await birth_customer(db, tenant, phone, birth)
            if not customer:
                fail(403, BIRTH_FAILURE)
            claims = make_claims(tenant, customer, customer["appointment_id"],
                                 auth_method="birth_date")
            data = await dashboard(db, tenant, customer, site["timezone"]) if include_dashboard else None
    return claims, data


async def connect_after_booking(slug, request, response, phone, birth):
    """Only on an explicitly opted-in, committed reservation, with matching DOB.

    A failed account connection must never change a successful booking.
    """
    if not enabled() or birth is None:
        return False
    tenant = await resolve_public(slug)
    if await login_method(slug, tenant) != "birth_date":
        return False
    claims, _ = await birth_login_claims(slug, request, tenant, phone, birth, include_dashboard=False)
    await issue_session(request, tenant, claims, response, True)
    return True


@router.post("/enter")
@sanitized_database_errors
async def enter(slug: str, payload: EnterInput, request: Request):
    require_enabled()
    validate_origin(request)
    tenant = await resolve_public(slug)
    if payload.birth_date is not None:
        claims, data = await birth_login_claims(slug, request, tenant, payload.phone,
                                               payload.birth_date, include_dashboard=True)
        response = reply(data)
        await issue_session(request, tenant, claims, response, payload.remember)
        return response
    await public_limit(request, str(tenant), "customer-enter-ip", 15, 300)
    await public_limit(request, "all", "customer-enter-phone", 12, 300,
                       subject=phone_fingerprint(payload.phone))
    result = await existing_dashboard(request, slug, tenant, payload.phone)
    if result:
        return reply(result)
    if await login_method(slug, tenant) == "birth_date":
        return reply({"authenticated": False, "birth_date_required": True})
    return reply(NEEDS_LINK)


@router.post("/link-login")
@sanitized_database_errors
async def link_login(slug: str, payload: LinkInput, request: Request):
    require_enabled()
    validate_origin(request)
    tenant = await resolve_public(slug)
    await public_limit(request, str(tenant), "customer-link-ip", 8, 300)
    await public_limit(request, "all", "customer-link-phone", 6, 300,
                       subject=phone_fingerprint(payload.phone))
    parsed = parse_booking_link(payload.booking_link)
    async with get_session_factory()() as db:
        async with tenant_transaction(db, tenant, repeatable_read=True):
            site = await published_guard(db, tenant, slug)
            verified = await proof_customer(db, tenant, parsed, payload.phone)
            if not verified:
                fail(403, UNVERIFIED)
            customer, appointment, legacy_digest = verified
            claims = make_claims(tenant, customer, appointment, legacy_digest)
            data = await dashboard(db, tenant, customer, site["timezone"])
    response = reply(data)
    await issue_session(request, tenant, claims, response, payload.remember)
    return response


@router.get("/appointments")
@sanitized_database_errors
async def appointments(slug: str, request: Request,
                       kind: Literal["upcoming", "history", "all"] = "history",
                       status: Literal["all", "confirmed", "completed", "cancelled", "no_show"] = "all",
                       offset: int = Query(0, ge=0, le=10000)):
    require_enabled()
    tenant = await resolve_public(slug)
    await public_limit(request, str(tenant), "customer-history", 90, 300)
    claims = await read_session(request, tenant)
    if not claims:
        fail(401, "Acesse sua área do cliente para continuar.")
    async with get_session_factory()() as db:
        async with tenant_transaction(db, tenant, repeatable_read=True):
            site = await published_guard(db, tenant, slug)
            customer = await verified_customer(db, tenant, claims)
            if not customer:
                fail(401, "Sua sessão expirou. Acesse novamente.")
            result = await appointment_page(db, tenant, customer["id"], kind, status, offset)
    return reply({**result, "timezone": site["timezone"]})


@router.post("/logout")
@sanitized_database_errors
async def logout(slug: str, request: Request):
    validate_origin(request)
    tenant = await resolve_public(slug)
    raw = request.cookies.get(cookie_name(tenant), "")
    if TOKEN.fullmatch(raw):
        await cache_call(lambda cache: cache.delete(session_key(raw)))
    response = reply({"authenticated": False})
    response.delete_cookie(cookie_name(tenant), path="/", secure=True, httponly=True,
                           samesite="lax")
    return response
