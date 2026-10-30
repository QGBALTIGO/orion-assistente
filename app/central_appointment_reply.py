"""Authenticated central WhatsApp protocol confirmation, independent of the AI bot.

The bridge supplies the resolved WhatsApp PN; the protocol and PN must match
exactly one booking. This endpoint never accepts a tenant, a target phone, or
a link from its caller and never creates/reschedules/cancels an appointment.
"""
import hashlib
import hmac
import os
import re
import secrets

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.catalog_api import support_session_factory
from app.catalog_schemas import StrictModel
from app.appointment_confirmation import (
    GENERIC, central_admin_whatsapp_enabled, confirmation_reply, detected_protocol,
)
from app import appointment_access as access
from app.qr_legacy import configured_tenant

router=APIRouter(tags=['Confirmação central por WhatsApp'])


class IncomingProtocol(StrictModel):
    phone: str=Field(pattern=r'^(?:55[1-9][0-9]{9,10}|5959[0-9]{8})$',min_length=12,max_length=13)
    message_id: str=Field(pattern=r'^[a-zA-Z0-9._:-]{1,157}$',max_length=157)
    text: str=Field(min_length=1,max_length=1500)


def central_reply_active():
    return (access.enabled() and central_admin_whatsapp_enabled()
            and os.getenv('ORION_CENTRAL_CONFIRMATION_REPLY_ENABLED','0')=='1')


async def _limit_protocol_lookup(request, phone):
    """Rate-limit a privileged identity lookup, including a stolen bridge key.

    Reuse the existing Redis-atomic limiter. It stores only SHA-256 rate keys,
    never the customer's number, token, protocol, or message content.
    """
    from app.public_site_api import public_limit
    # The public limiter hashes its subject once more. HMAC here prevents a
    # leaked Redis keyspace from being brute-forced into telephone numbers.
    secret=os.getenv('QR_BRIDGE_TOKEN','').encode()
    digest=hmac.new(secret,phone.encode(),hashlib.sha256).hexdigest()
    await public_limit(request, 'orion-central-qr', 'qr-protocol-phone',
                       8, 300, subject=digest)
    await public_limit(request, 'orion-central-qr', 'qr-protocol-global',
                       120, 60, subject='all')


@router.post('/internal/qr/appointment-confirmation',include_in_schema=False)
async def confirm_inbound_protocol(payload:IncomingProtocol,request:Request):
    key=os.getenv('QR_BRIDGE_TOKEN','')
    supplied=request.headers.get('X-Orion-Bridge-Key','')
    if len(key)<32 or not secrets.compare_digest(key,supplied):
        raise HTTPException(401,'Acesso negado')
    if not central_reply_active():
        return {'state':'disabled','answer':None}
    protocol=detected_protocol(payload.text)
    if protocol is None:
        return {'state':'not_protocol','answer':None}
    # Enforce the same atomic Redis quotas used by the public booking engine.
    # A leaked transport credential must not become an unlimited lookup oracle.
    await _limit_protocol_lookup(request, payload.phone)
    try:
        # Admin tenant is server-owned. No tenant ID may come from WhatsApp text.
        configured_tenant()
        async with support_session_factory()() as registry:
            result=(await registry.execute(text(
                'SELECT tenant_id,appointment_id FROM public.orion_resolve_central_appointment(:protocol,:phone)'
            ),{'protocol':protocol,'phone':'+'+payload.phone})).mappings().first()
    except (SQLAlchemyError,ValueError,RuntimeError,HTTPException):
        # A migration or database failure cannot become an unverified answer.
        return {'state':'unavailable','answer':None}

    if result is None:
        return {'state':'unrecognized','answer':GENERIC}
    # A valid protocol and phone do not grant a paid feature on their own.
    # Only a company with active Essencial-or-higher notifications may use
    # the platform WhatsApp. Fail closed on plan-store outages.
    try:
        from app.plan_permissions import tenant_has_feature
        authorized = await tenant_has_feature(result['tenant_id'], 'notifications.basic')
    except Exception:
        return {'state':'unavailable','answer':None}
    if not authorized:
        return {'state':'unrecognized','answer':GENERIC}
    answer=await confirmation_reply(result['tenant_id'],payload.phone,payload.text,
                                    'central-admin-qr',payload.message_id,central=True)
    if not answer:
        return {'state':'unavailable','answer':None}
    if len(answer)>1200:
        return {'state':'unavailable','answer':None}
    return {'state':'confirmed','answer':answer}
