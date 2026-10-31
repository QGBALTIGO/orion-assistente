"""Customer portal access with no SMS or external verification provider.

Returning visitors use an HttpOnly verified cookie. New browsers can prove
possession of an appointment's existing private management link once.
A typed telephone alone is never a credential: other people's visit history,
contact data and booking mutations remain inaccessible.
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from uuid import UUID

from cryptography.fernet import Fernet
from fastapi import HTTPException
from redis.asyncio import from_url
from redis.exceptions import RedisError

COOKIE = "__Host-orion_public_customer"
SESSION_SECONDS = 7 * 24 * 60 * 60
SCOPE = "orion:public-customer:v1:"
PROOF_REQUIRED = {"authenticated": False, "proof_required": True}
NOT_AVAILABLE = "A conta do cliente ainda não está disponível neste estabelecimento."


def enabled():
    return (os.getenv("ORION_CUSTOMER_PORTAL_ENABLED", "0") == "1"
            and bool(os.getenv("INTEGRATION_FERNET_KEY"))
            and bool(os.getenv("REDIS_URL")))


def require_enabled():
    if not enabled():
        raise HTTPException(503, NOT_AVAILABLE)


def _cache():
    return from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"),
                    decode_responses=True, socket_timeout=3, max_connections=15)


def cipher():
    return Fernet(os.environ["INTEGRATION_FERNET_KEY"].encode())


def encrypt_phone(phone):
    return cipher().encrypt(phone.encode()).decode()


def decrypt_phone(payload):
    return cipher().decrypt(payload.encode()).decode()


def _digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def phone_fingerprint(value):
    # E.164 phone space is enumerable. An HMAC prevents offline matching of
    # Redis session records to real customers after an isolated cache leak.
    key = os.environ["INTEGRATION_FERNET_KEY"].encode()
    return hmac.new(key, ("orion:phone-proof:v1:"+value).encode(), hashlib.sha256).hexdigest()


def _session_key(raw):
    return SCOPE + "session:" + _digest(raw)


def _token():
    return secrets.token_urlsafe(32)


async def issue_session(tenant, customer, version, phone, response, *, previous=None, verified_at=None):
    raw = _token()
    claims = {"tenant": str(tenant), "customer": str(customer),
              "version": int(version), "phone_hash": phone_fingerprint(phone),
              "verified_at": verified_at if verified_at is not None else int(time.time())}
    redis = _cache()
    try:
        await redis.set(_session_key(raw), json.dumps(claims, separators=(",", ":")),
                        ex=SESSION_SECONDS)
        if previous and re.fullmatch(r"[A-Za-z0-9_-]{43}", previous):
            await redis.delete(_session_key(previous))
    except (RedisError, OSError):
        raise HTTPException(503, "Sessão temporariamente indisponível") from None
    finally:
        await redis.aclose()
    response.set_cookie(COOKIE, raw, secure=True, httponly=True,
        samesite="lax", path="/", max_age=SESSION_SECONDS)
    return claims


async def revoke_session(raw):
    if not raw or not re.fullmatch(r"[A-Za-z0-9_-]{43}", raw):
        return
    redis = _cache()
    try:
        await redis.delete(_session_key(raw))
    except (RedisError, OSError):
        raise HTTPException(503, "Não foi possível encerrar a sessão") from None
    finally:
        await redis.aclose()


async def session_claims(raw, tenant):
    if not raw or not re.fullmatch(r"[A-Za-z0-9_-]{43}", raw):
        return None
    redis = _cache()
    try:
        value = await redis.get(_session_key(raw))
    except (RedisError, OSError):
        raise HTTPException(503, "Não foi possível conferir a sessão") from None
    finally:
        await redis.aclose()
    if not value:
        return None
    try:
        state = json.loads(value)
        if (state["tenant"] != str(tenant) or
                not isinstance(state["version"], int) or state["version"] < 1 or
                not isinstance(state.get("verified_at"), int) or
                state["verified_at"] > int(time.time()) or
                not re.fullmatch(r"[0-9a-f]{64}", state["phone_hash"])):
            return None
        UUID(state["customer"])
        return state
    except (TypeError, ValueError, KeyError):
        return None


def parse_booking_proof(provided):
    """Accept a private /a#... URL or its opaque fragment, never a protocol."""
    from urllib.parse import urlsplit
    from app.public_origins import canonical_public_origin
    from app.appointment_access import parse_token

    if not isinstance(provided, str) or len(provided) > 650:
        raise HTTPException(403, "Não foi possível confirmar este link de agendamento")
    candidate = provided.strip()
    if candidate.startswith("https://"):
        parts = urlsplit(candidate)
        allowed = urlsplit(canonical_public_origin())
        if (parts.scheme != "https" or parts.netloc != allowed.netloc
                or parts.path != "/a" or parts.query or parts.username or parts.password):
            raise HTTPException(403, "Não foi possível confirmar este link de agendamento")
        candidate = parts.fragment
    try:
        return parse_token(candidate)
    except HTTPException:
        raise HTTPException(403, "Não foi possível confirmar este link de agendamento") from None


async def customer_for_proof(db, tenant, phone, digest):
    """Database-backed old link verification without the optional QR pilot flag.

    Links are valid only for the same tenant, phone, customer version and
    non-blocked appointment; the 90-day expiry and revocation still apply.
    """
    from sqlalchemy import text
    return (await db.execute(text("""
        SELECT c.id,c.name,c.whatsapp_e164,c.email,c.instagram,c.birth_date,
               c.neighborhood,c.city,c.version
        FROM appointment_access l
        JOIN appointments a ON a.tenant_id=l.tenant_id
          AND a.id=l.appointment_id AND a.customer_id=l.customer_id
        JOIN customers c ON c.tenant_id=l.tenant_id AND c.id=l.customer_id
        JOIN tenants t ON t.id=l.tenant_id
        WHERE l.tenant_id=:t AND l.token_hash=:digest
          AND c.whatsapp_e164=:phone AND c.active AND c.deleted_at IS NULL
          AND c.version=l.customer_version AND t.status='active'
          AND l.revoked_at IS NULL AND l.expires_at>clock_timestamp()
          AND NOT a.customer_access_blocked
        LIMIT 1
    """), {"t": tenant, "phone": phone, "digest": digest})).mappings().one_or_none()
