"""Private, fail-closed grants for transactional notices sent by the Orion admin QR.

The bridge is allowed to bypass its ten-number *inbound* pilot allowlist only
after the support-role database proves an exact tenant-owned appointment
outbox attempt. No arbitrary URL, tenant, recipient or text grants are accepted.
"""
import hashlib
import json
import os
import re

from fastapi import HTTPException
from sqlalchemy import text

from app.catalog_api import support_session_factory
from app.qr_channels import authenticated_channel
from app.qr_legacy import channel_id as legacy_channel_id, configured_tenant

_NUMBER = re.compile(r"^(?:55[1-9][0-9]{9,10}|5959[0-9]{8})$")


def enabled():
    return (os.getenv("ORION_CENTRAL_NOTIFICATIONS_ENABLED", "0") == "1"
            and os.getenv("ORION_CENTRAL_APPOINTMENT_WHATSAPP_ENABLED", "0") == "1"
            and os.getenv("APPOINTMENT_COMMUNICATIONS_ENABLED", "0") == "1")


async def _grant(channel_id, payload, request):
    if not enabled() or payload.source != "appointment" or payload.control_revision is not None:
        raise HTTPException(403, "Notificação central indisponível")
    if not _NUMBER.fullmatch(payload.phone) or not payload.text:
        raise HTTPException(403, "Destino ou mensagem indisponível")
    identity = await authenticated_channel(request)
    admin = configured_tenant()
    if (identity.tenant_id != admin or identity.channel_id != channel_id
            or identity.channel_id != legacy_channel_id(admin)
            or not identity.legacy_transport or not identity.central_adopted):
        raise HTTPException(403, "Canal central não autorizado")
    fingerprint = hashlib.sha256(json.dumps(
        [payload.phone, payload.text], ensure_ascii=False,
        separators=(",", ":")).encode()).hexdigest()
    if fingerprint != payload.fingerprint:
        raise HTTPException(403, "Conteúdo diferente do preparado")
    async with support_session_factory()() as db:
        permitted = (await db.execute(text("""
          SELECT public.orion_authorize_central_appointment_notification(
            :operation,:admin,:channel,:phone,:recipient,:body)
        """), {"operation": payload.operation_id, "admin": admin, "channel": channel_id,
               "phone": payload.phone,
               "recipient": hashlib.sha256(("+" + payload.phone).encode()).hexdigest(),
               "body": hashlib.sha256(payload.text.encode()).hexdigest()})).scalar_one()
    if permitted is not True:
        raise HTTPException(403, "Notificação sem autorização atual")
    return payload.operation_id


async def authorize(channel_id, payload, request):
    operation = await _grant(channel_id, payload, request)
    return {"authorized": True, "operation_id": operation}


async def finish(channel_id, payload, request):
    operation = await _grant(channel_id, payload, request)
    return {"released": True, "operation_id": operation}
