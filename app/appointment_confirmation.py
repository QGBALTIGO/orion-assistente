"""Authenticated ingress adapter: protocol + matching contact, never protocol alone."""
import asyncio
import os
import re
from urllib.parse import quote

from sqlalchemy import text
from app import appointment_access as access
from app.appointment_errors import sanitized_database_errors
from app.appointment_notifications import cipher
from app.appointment_messages import compose, compose_prefill, generic_response
from app.booking_service import read_appointment
from app.database import get_session_factory, tenant_transaction

GENERIC = generic_response()
PROTOCOL=re.compile(r'(?<![A-Z0-9])AG-[A-HJ-NP-Z2-9]{6}(?![A-Z0-9])',re.I)


def detected_protocol(body):
    if not isinstance(body,str) or len(body)>1500:return None
    if re.search(r'\b(cancel\w*|remarc\w*|reagend\w*|desmarc\w*)\b',body,re.I):return None
    matches=PROTOCOL.findall(body)
    return matches[0].upper() if len(set(m.upper() for m in matches))==1 else None


@sanitized_database_errors
async def confirmation_reply(tenant,phone,body,channel_key,message_id,*,central=False):
    if central:
        # This path is entered only through the authenticated server-side QR
        # confirmation endpoint, after a unique protocol/phone lookup.
        if not (access.enabled() and central_admin_whatsapp_enabled()
                and os.getenv('ORION_CENTRAL_CONFIRMATION_REPLY_ENABLED')=='1'):return None
    elif not access.tenant_enabled(tenant):return None
    protocol=detected_protocol(body)
    if protocol is None:return None
    # A recognized protocol must never fall through to a generic AI reply.
    # Reject unapproved senders *before* touching the appointment database.
    if not central and not access.recipient_allowed(phone):return GENERIC
    if not isinstance(message_id,str) or not 1<=len(message_id)<=200:return GENERIC
    phone='+'+phone.lstrip('+')
    async with get_session_factory()() as db:
        async with tenant_transaction(db,tenant):
            # Serializes repeated library callbacks without storing plaintext secrets.
            await db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key,0))'),
                {'key':str(tenant)+':appointment-confirmation:'+channel_key+':'+message_id})
            settings=(await db.execute(text('''SELECT enabled,confirmation,signature
                FROM appointment_communication_settings WHERE tenant_id=:t'''),{'t':tenant})).mappings().first()
            signature=(settings['signature'] if settings else None) or 'Orion Agenda'
            if not central and not access.recipient_allowed(phone):return generic_response(signature)
            row=(await db.execute(text('''SELECT a.id FROM appointments a JOIN customers c
                ON c.tenant_id=a.tenant_id AND c.id=a.customer_id
                WHERE a.tenant_id=:t AND a.protocol=:protocol AND c.whatsapp_e164=:phone
                  AND c.active AND c.deleted_at IS NULL'''),{'t':tenant,'protocol':protocol,'phone':phone})).mappings().first()
            if row is None:return generic_response(signature)
            if not central and (not settings or not settings['enabled'] or not settings['confirmation']):return ''
            # Membership, channel fencing and any human pause remain guarded by
            # the existing ingress's transaction context around this operation.
            appointment=await read_appointment(db,tenant,row['id'])
            previous=(await db.execute(text('''SELECT body_encrypted,appointment_version,appointment_id FROM appointment_confirmation_receipts
                WHERE tenant_id=:t AND channel_key=:channel AND message_id=:message'''),
                {'t':tenant,'channel':channel_key,'message':message_id})).mappings().first()
            if previous:
                return cipher().decrypt(previous['body_encrypted'].encode()).decode() if previous['appointment_version']==appointment['updated_at'] and previous['appointment_id']==appointment['id'] else ''
            company=(await db.execute(text('SELECT name FROM tenants WHERE id=:t'),{'t':tenant})).scalar_one()
            link=await access.issue(db,tenant,row['id'])
            if appointment['status']=='confirmed':
                answer=compose('confirmation',appointment,company=company,customer_link=link,signature=signature)
            else:
                answer=compose('status',appointment,company=company,customer_link=link,signature=signature)
            await db.execute(text('''INSERT INTO appointment_confirmation_receipts(tenant_id,channel_key,message_id,appointment_id,appointment_version,body_encrypted)
                VALUES(:t,:channel,:message,:id,:version,:body)'''),{'t':tenant,'channel':channel_key,'message':message_id,
                'id':appointment['id'],'version':appointment['updated_at'],'body':cipher().encrypt(answer.encode()).decode()})
            return answer


def central_admin_whatsapp_enabled():
    """Independent rollout: do not redirect other companies before hub routing is ready."""
    return os.getenv('ORION_CENTRAL_APPOINTMENT_WHATSAPP_ENABLED', '0') == '1'


async def central_admin_phone():
    """Never infer the platform number from a customer's company/site settings."""
    from app.qr_legacy import configured_tenant
    try:
        tenant = configured_tenant()
    except Exception:
        return None
    return await connected_channel_phone(tenant)


async def public_followup(slug,result,phone):
    """Called only after the engine and public receipt transactions committed."""
    if not access.enabled():return {}
    from app.public_site_api import resolve_public
    tenant=await resolve_public(slug)
    central=central_admin_whatsapp_enabled()
    if not central and not access.tenant_enabled(tenant):return {}
    output={}
    async with get_session_factory()() as db:
        async with tenant_transaction(db,tenant):
            appointment=(await db.execute(text('''SELECT a.id FROM appointments a JOIN customers c
                ON c.tenant_id=a.tenant_id AND c.id=a.customer_id WHERE a.tenant_id=:t
                AND a.protocol=:protocol AND c.whatsapp_e164=:phone AND a.status='confirmed' '''),
                {'t':tenant,'protocol':result['protocol'],'phone':phone})).scalar_one_or_none()
            if appointment is None:return {}
            # An anonymous booking can use an existing customer's phone.
            # Only a verified inbound contact may receive a capability; never
            # return it to the anonymous booking caller.
            if not central:
                requested=(await db.execute(text('SELECT enabled AND confirmation FROM appointment_communication_settings WHERE tenant_id=:t'),{'t':tenant})).scalar_one_or_none()
                if requested is not True:return output
    # For the platform-wide path, always resolve the WhatsApp authenticated on
    # the admin tenant. Never use a company's own QR session or site number.
    # Existing tenant-scoped links are left untouched until this rollout is ready.
    number=await (central_admin_phone() if central else connected_channel_phone(tenant))
    if number:
        message=compose_prefill(result['protocol'])
        output['whatsapp_confirmation_url']='https://wa.me/'+number+'?text='+quote(message)
    else:
        output['communication_state']='channel_unavailable'
    return output


async def connected_channel_phone(tenant):
    """Only the currently verified channel supplies a WhatsApp destination.

    Site appearance numbers, professional phones and request IDs never select
    this contact. An offline or unverified channel fails closed within 2 s.
    """
    try:
        from app.catalog_api import support_session_factory
        from app.qr_channels import _canonical,_bridge_control,_control_result
        async with asyncio.timeout(2):
            async with support_session_factory()() as registry:
                channel,state=await _canonical(registry,tenant)
            if state!='ready' or channel is None:return None
            raw=await _bridge_control(channel,'status','GET')
            status=_control_result(channel,raw,'status')
            number=status.get('phone') if status.get('phone_verified') else None
            return number
    except Exception:
        return None
