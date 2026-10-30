"""Global appointment aliases: identifiers only, never authorization tokens."""
import hashlib
import hmac
import os
import re
import secrets
import base64
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text

from app.public_origins import canonical_public_origin

ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_RE = re.compile(r"^AG[A-HJ-NP-Z2-9]{6,12}$")
CUSTOMER_COOKIE = "__Host-orion-short-customer"
STAFF_COOKIE = "__Host-orion-short-staff"
OTP_TTL_SECONDS = 300
SESSION_TTL_SECONDS = 7200
GENERIC_REQUEST = {"message": "Se o agendamento estiver elegível, enviaremos um código ao WhatsApp cadastrado."}
GENERIC_ERROR = "Código inválido ou expirado. Solicite um novo código e tente novamente."


def checked_code(value):
    value = value.upper() if isinstance(value, str) else ""
    if CODE_RE.fullmatch(value) is None:
        raise HTTPException(404, "Agendamento indisponível")
    return value


def natural_code(protocol):
    value = str(protocol or "").upper().replace("-", "")
    return value if CODE_RE.fullmatch(value) else None


def random_code():
    return "AG" + "".join(secrets.choice(ALPHABET) for _ in range(10))


async def get_or_create(db, tenant: UUID, appointment: UUID, protocol):
    """Race-safe, globally unique, persistent alias without changing protocol."""
    existing = (await db.execute(text("""
        SELECT code FROM appointment_short_codes
        WHERE tenant_id=:tenant AND appointment_id=:appointment
    """), {"tenant": tenant, "appointment": appointment})).scalar_one_or_none()
    if existing:
        return existing
    first = natural_code(protocol)
    for attempt in range(12):
        candidate = first if attempt == 0 and first else random_code()
        inserted = (await db.execute(text("""
            INSERT INTO appointment_short_codes(code,tenant_id,appointment_id)
            VALUES (:code,:tenant,:appointment) ON CONFLICT DO NOTHING RETURNING code
        """), {"code": candidate, "tenant": tenant, "appointment": appointment})).scalar_one_or_none()
        if inserted:
            return inserted
        existing = (await db.execute(text("""
            SELECT code FROM appointment_short_codes WHERE tenant_id=:tenant
              AND appointment_id=:appointment
        """), {"tenant": tenant, "appointment": appointment})).scalar_one_or_none()
        if existing:
            return existing
    raise ValueError("public_code_uniqueness_exhausted")


async def resolve_global(code):
    """Server-only lookup as app_support; caller must authorize in tenant scope."""
    from app.catalog_api import support_session_factory
    checked_code(code)
    async with support_session_factory()() as db:
        row = (await db.execute(text("""
            SELECT tenant_id,appointment_id FROM appointment_short_codes
            WHERE code=:code
        """), {"code": code})).mappings().first()
    return dict(row) if row else None


async def belongs(db, tenant, appointment, code):
    return (await db.execute(text("""
        SELECT 1 FROM appointment_short_codes
        WHERE tenant_id=:tenant AND appointment_id=:appointment AND code=:code
    """), {"tenant": tenant, "appointment": appointment, "code": code})).scalar_one_or_none() is not None


def customer_url(code):
    return canonical_public_origin() + "/" + checked_code(code)


def staff_url(code):
    return canonical_public_origin() + "/p/" + checked_code(code)


def recipient_hash(phone):
    return hashlib.sha256(phone.encode("utf-8")).hexdigest()


def otp_digest(challenge_id, otp):
    """Domain-separated MAC: a stolen DB hash is not an offline 6-digit oracle."""
    key = base64.urlsafe_b64decode(os.environ["INTEGRATION_FERNET_KEY"].encode())
    payload = b"orion:short-link:otp:v1:" + str(challenge_id).encode() + b":" + str(otp).encode()
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


def opaque_token():
    return secrets.token_urlsafe(32)


def token_digest(raw):
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def response_cookie(response, name, raw):
    response.set_cookie(name, raw, max_age=SESSION_TTL_SECONDS,
                        secure=True, httponly=True, samesite="lax", path="/")
