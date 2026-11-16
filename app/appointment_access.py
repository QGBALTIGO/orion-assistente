"""Appointment-scoped customer capabilities. Protocols never grant access.

The secret lives in a URL fragment and a JSON POST, never in a request URL.
A preview crawler only receives the empty shell. Revocation is checked in
all engine transactions, including the final check before commit.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import timedelta
import base64
import hashlib
import hmac
import os
import re
import secrets
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text

from app.public_origins import canonical_public_origin

_SCOPE = ContextVar('appointment_customer_access', default=None)
UNAVAILABLE = 'Link indisponível ou expirado. Solicite um novo link à empresa.'


def enabled():
    return os.getenv('APPOINTMENT_COMMUNICATIONS_ENABLED', '0') == '1'


def pilot_only():
    return os.getenv('APPOINTMENT_COMMUNICATIONS_PILOT_ONLY','1') == '1'


def tenant_enabled(tenant):
    if not enabled():return False
    if not pilot_only():return True
    try:
        expected=UUID(os.environ['APPOINTMENT_COMMUNICATIONS_PILOT_TENANT_ID'])
        return UUID(str(tenant))==expected
    except (KeyError,ValueError,TypeError):return False


def recipient_allowed(phone):
    if not pilot_only():return True
    if not isinstance(phone,str):return False
    configured=os.getenv('APPOINTMENT_COMMUNICATIONS_PILOT_PHONE','')
    # The connected self PN remains a separate HMAC/session binding. Explicit
    # test customers never replace it or widen a channel's transport allowlist.
    customers=os.getenv('APPOINTMENT_COMMUNICATIONS_PILOT_CUSTOMER_PHONES','').split(',')
    if len(customers)>10:return False
    allowed={configured,*[p.strip() for p in customers if p.strip()]}
    return phone.lstrip('+') in allowed and bool(re.fullmatch(r'55[1-9][0-9]{9,10}',phone.lstrip('+')))


def require_tenant(tenant):
    if tenant_enabled(tenant):return
    # An actual, revocable appointment token is valid for any company served
    # by the admin's centralized booking reception. This does not enable the
    # AI bot, automatic broadcast, or the pilot tenant's QR send permissions.
    if (enabled() and os.getenv('ORION_CENTRAL_APPOINTMENT_WHATSAPP_ENABLED')=='1'
            and (os.getenv('ORION_CENTRAL_CONFIRMATION_REPLY_ENABLED')=='1'
                 or os.getenv('ORION_CENTRAL_NOTIFICATIONS_ENABLED')=='1')):return
    raise HTTPException(404,'Recurso indisponível para esta empresa')


def require_enabled():
    if not enabled():
        raise HTTPException(404, 'Página indisponível')


def parse_token(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f-]{36}\.[A-Za-z0-9_-]{43}', value):
        raise HTTPException(404, UNAVAILABLE)
    try:
        tenant = UUID(value[:36])
    except ValueError:
        raise HTTPException(404, UNAVAILABLE) from None
    return tenant, hashlib.sha256(value.encode()).hexdigest()


@dataclass(frozen=True)
class AccessIdentity:
    tenant_id: UUID
    appointment_id: UUID
    customer_id: UUID
    token_hash: str


@contextmanager
def bind(identity):
    token = _SCOPE.set(identity)
    try:
        yield
    finally:
        _SCOPE.reset(token)


async def verify(db, tenant, digest):
    require_tenant(tenant)
    row = (await db.execute(text('''SELECT l.appointment_id,l.customer_id
        FROM appointment_access l JOIN customers c ON c.tenant_id=l.tenant_id AND c.id=l.customer_id
        JOIN appointments a ON a.tenant_id=l.tenant_id AND a.id=l.appointment_id AND a.customer_id=l.customer_id
        JOIN tenants t ON t.id=l.tenant_id
        WHERE l.tenant_id=:t AND l.token_hash=:hash AND l.revoked_at IS NULL
          AND l.expires_at>clock_timestamp() AND NOT a.customer_access_blocked AND c.version=l.customer_version
          AND c.active AND c.deleted_at IS NULL AND t.status='active'
        FOR SHARE OF l'''), {'t': tenant, 'hash': digest})).mappings().first()
    if row is None:
        raise HTTPException(404, UNAVAILABLE)
    return AccessIdentity(tenant, row['appointment_id'], row['customer_id'], digest)


async def guard(db, tenant):
    identity = _SCOPE.get()
    if identity is None:
        return
    require_enabled()
    if identity.tenant_id != tenant or await verify(db, tenant, identity.token_hash) != identity:
        raise HTTPException(404, UNAVAILABLE)


async def issue(db, tenant, appointment):
    require_tenant(tenant)
    row = (await db.execute(text('''SELECT a.customer_id,a.end_at,c.version FROM appointments a
        JOIN customers c ON c.tenant_id=a.tenant_id AND c.id=a.customer_id
        WHERE a.tenant_id=:t AND a.id=:id AND NOT a.customer_access_blocked AND c.active AND c.deleted_at IS NULL'''),
        {'t': tenant, 'id': appointment})).mappings().first()
    if row is None:
        raise HTTPException(404, UNAVAILABLE)
    raw = str(tenant) + '.' + secrets.token_urlsafe(32)
    digest = hashlib.sha256(raw.encode()).hexdigest()
    # Keep links through the post-service review period. Old links are never
    # rotated merely because a customer requests confirmation again.
    await db.execute(text('''INSERT INTO appointment_access
        (tenant_id,appointment_id,customer_id,customer_version,token_hash,expires_at)
        VALUES(:t,:id,:customer,:version,:hash,GREATEST(:end,clock_timestamp())+interval '90 days')'''),
        {'t': tenant, 'id': appointment, 'customer': row['customer_id'], 'version': row['version'],
         'hash': digest, 'end': row['end_at']})
    return canonical_public_origin() + '/a#' + raw


def short_links_enabled():
    """Only activate after 0054 backup, schema checks and QR ingress are live."""
    return enabled() and os.getenv("ORION_SHORT_APPOINTMENT_LINKS_ENABLED") == "1"


def direct_link_proof(code, audience, tenant, appointment, identity, phone, version=None):
    """Role-bound, unguessable URL-fragment proof (160-bit HMAC).

    The public AG code alone never authenticates anyone.  This signing key
    must be shared by API and notification worker, and unrelated to other
    Orion signing/encryption keys. A phone change, customer version bump or
    professional reassignment invalidates the corresponding old link.
    """
    key = os.getenv("ORION_DIRECT_APPOINTMENT_KEY", "")
    if len(key) < 43:
        raise RuntimeError("orion_direct_appointment_key_missing")
    if audience not in ("customer", "professional") or not isinstance(phone, str) or not phone:
        raise ValueError("invalid_direct_appointment_identity")
    message = "|".join(("orion-one-click-v1", code, audience, str(tenant),
        str(appointment), str(identity), phone, str(version) if audience == "customer" else ""))
    digest = hmac.new(key.encode(), message.encode(), hashlib.sha256).digest()[:20]
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


async def shared_link(db, tenant, appointment, *, audience="customer"):
    """Issue a one-click link; the short protocol is never a bearer secret.

    A per-role 160-bit HMAC is placed in the URL fragment, so the server's
    access logs, link previews and referer headers never receive it. Links
    for customer and professional are not interchangeable.
    """
    if audience not in {"customer", "professional"}:
        raise ValueError("invalid_shared_link_audience")
    if not short_links_enabled():
        if audience == "customer":
            return await issue(db, tenant, appointment)
        return (canonical_public_origin() + "/painel?aba=agenda&agendamento="
                + str(appointment))
    row = (await db.execute(text("""
        SELECT short.code,a.customer_id,a.professional_id,a.customer_access_blocked,
            c.version AS customer_version,c.whatsapp_e164 AS customer_phone,
            c.active AS customer_active,c.deleted_at AS customer_deleted,
            p.phone_e164 AS professional_phone,p.active AS professional_active
        FROM public.appointment_short_codes short
        JOIN public.appointments a ON a.tenant_id=short.tenant_id
            AND a.id=short.appointment_id
        JOIN public.customers c ON c.tenant_id=a.tenant_id AND c.id=a.customer_id
        JOIN public.professionals p ON p.tenant_id=a.tenant_id AND p.id=a.professional_id
        WHERE short.tenant_id=:tenant AND short.appointment_id=:appointment
    """), {"tenant":tenant, "appointment":appointment})).mappings().first()
    if not row or not re.fullmatch(r"AG-[A-HJ-NP-Z2-9]{6}(?:-[A-F0-9]{8})?", row["code"]):
        raise ValueError("short_link_alias_unavailable")
    if row["customer_access_blocked"] or not row["customer_active"] or row["customer_deleted"]:
        raise ValueError("short_link_access_unavailable")
    if audience == "professional" and not row["professional_active"]:
        raise ValueError("professional_link_unavailable")
    identity = row["professional_id"] if audience == "professional" else row["customer_id"]
    phone = row["professional_phone"] if audience == "professional" else row["customer_phone"]
    if not phone:
        raise ValueError("short_link_contact_missing")
    proof = direct_link_proof(row["code"], audience, tenant, appointment,
        identity, phone, row["customer_version"] if audience == "customer" else None)
    prefix = "/p/" if audience == "professional" else "/"
    return canonical_public_origin() + prefix + row["code"] + "#" + proof
