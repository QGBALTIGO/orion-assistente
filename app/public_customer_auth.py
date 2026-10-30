"""Public customer portal authentication.

A phone number is only a login identifier, never proof of ownership.
Twilio Verify is a *platform* SMS provider, independent of each tenant's
WhatsApp connection or subscription. No passwords, anonymous data lookup,
customer records, or bearer capabilities are exposed on the public site.
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from uuid import UUID

import httpx
from cryptography.fernet import Fernet
from fastapi import HTTPException
from redis.asyncio import from_url
from redis.exceptions import RedisError

COOKIE = "__Host-orion_public_customer"
SESSION_SECONDS = 7 * 24 * 60 * 60
SCOPE = "orion:public-customer:v1:"
GENERIC_SENT = {"message": "Se este número tiver cadastro neste estabelecimento, enviaremos um código por SMS."}
NOT_AVAILABLE = "O acesso por telefone ainda não está habilitado neste estabelecimento."


def enabled():
    sid = os.getenv("ORION_CUSTOMER_VERIFY_SERVICE_SID", "")
    account = os.getenv("ORION_CUSTOMER_VERIFY_ACCOUNT_SID", "")
    secret = os.getenv("ORION_CUSTOMER_VERIFY_AUTH_TOKEN", "")
    return (os.getenv("ORION_CUSTOMER_PORTAL_ENABLED", "0") == "1"
            and bool(re.fullmatch(r"VA[0-9a-fA-F]{32}", sid))
            and bool(re.fullmatch(r"AC[0-9a-fA-F]{32}", account))
            and len(secret) >= 20
            and bool(os.getenv("INTEGRATION_FERNET_KEY")))


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


async def provider_request(phone, code=None):
    """Fixed HTTPS origin; credentials never reach logs, client or frontend."""
    require_enabled()
    base = ("https://verify.twilio.com/v2/Services/" +
            os.environ["ORION_CUSTOMER_VERIFY_SERVICE_SID"])
    path = "/VerificationCheck" if code is not None else "/Verifications"
    body = {"To": phone, "Code": code} if code is not None else {"To": phone, "Channel": "sms"}
    auth = httpx.BasicAuth(os.environ["ORION_CUSTOMER_VERIFY_ACCOUNT_SID"],
                           os.environ["ORION_CUSTOMER_VERIFY_AUTH_TOKEN"])
    try:
        async with httpx.AsyncClient(timeout=8, follow_redirects=False) as client:
            reply = await client.post(base + path, data=body, auth=auth,
                                      headers={"Accept": "application/json"})
        # Never include provider status/body, phone, or OTP in the public error.
        if reply.status_code not in (200, 201):
            raise ValueError("provider_refused")
        result = reply.json()
        return result.get("status") == ("approved" if code else "pending")
    except (httpx.HTTPError, ValueError, KeyError):
        return False


async def enqueue_code(slug, phone, *, job_prefix="login"):
    """All valid input numbers take the same public path; worker checks CRM."""
    from arq import create_pool
    from arq.connections import RedisSettings
    redis = await create_pool(RedisSettings.from_dsn(os.environ["REDIS_URL"]))
    try:
        await redis.enqueue_job("send_public_customer_code", slug, encrypt_phone(phone),
                                _job_id="orion-customer-code:" + secrets.token_hex(16))
    except (RedisError, OSError):
        raise HTTPException(503, "Não foi possível solicitar o código") from None
    finally:
        await redis.aclose()


async def send_public_customer_code(ctx, slug, encrypted_phone):
    """Worker lookup is tenant-scoped; no SMS for unknown or deleted customers."""
    from app.public_site_api import resolve_public, published_guard
    from app.database import get_session_factory, tenant_transaction
    from sqlalchemy import text
    if not enabled():
        return "disabled"
    try:
        phone = decrypt_phone(encrypted_phone)
        tenant = await resolve_public(slug)
        async with get_session_factory()() as db:
            async with tenant_transaction(db, tenant):
                await published_guard(db, tenant, slug)
                found = (await db.execute(text("""
                    SELECT 1 FROM customers WHERE tenant_id=:t AND whatsapp_e164=:p
                      AND active AND deleted_at IS NULL LIMIT 1
                """), {"t": tenant, "p": phone})).scalar_one_or_none()
        if found:
            return "accepted" if await provider_request(phone) else "failed"
        return "not_registered"
    except Exception:
        # No plaintext phone, session, external provider response or customer data
        # is ever logged by this job.
        return "not_available"
