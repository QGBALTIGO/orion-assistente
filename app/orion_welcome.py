"""Durable administrative contact authorized specifically for onboarding."""
import asyncio
import hashlib
import os
import re

import httpx
from fastapi import HTTPException
from sqlalchemy import text

from app.catalog_api import support_session_factory


def enabled():
    return os.environ.get("ORION_WELCOME_WHATSAPP", "0") == "1"


async def ready(db):
    return enabled() and (await db.execute(text(
        "SELECT to_regclass('public.orion_welcome_outbox') IS NOT NULL"
    ))).scalar_one()


def valid_phone(phone):
    return bool(phone and re.fullmatch(r"\+55[1-9][0-9]{9,10}", phone))


async def enqueue(db, tenant, account, phone):
    if valid_phone(phone) and await ready(db):
        await db.execute(text("""
            INSERT INTO orion_welcome_outbox (tenant_id,account_id,recipient)
            VALUES (:tenant,:account,:phone) ON CONFLICT (tenant_id) DO NOTHING
        """), {"tenant": tenant, "account": account, "phone": phone})


def message(first_name, company):
    first = first_name.split()[0] if first_name and first_name.split() else None
    greeting = f"Olá, {first}!" if first else "Olá!"
    business = f"da {company}" if company and company.strip() else "da sua barbearia"
    return (
        f"{greeting} Tudo bem? 🤗\n\n"
        f"Aqui é da equipe Orion. Recebemos o cadastro {business} e queremos ajudar você nos primeiros passos! 💈\n\n"
        "O Orion reúne agenda, clientes, financeiro e agendamento online para facilitar a rotina da sua barbearia.\n\n"
        "Podemos te orientar a cadastrar os serviços, organizar os horários dos barbeiros e preparar seu site de agendamento.\n\n"
        "Quer ajuda para começar?"
    )


# Live account, current registration phone and original opt-in must all agree.
_LIVE_CONSENT = """
    o.configuration->>'niche'='barbearia'
    AND o.configuration->>'contact_phone'=w.recipient
    AND w.revoked_at IS NULL
    AND a.verified_at IS NOT NULL AND NOT a.registration_pending
    AND EXISTS (SELECT 1 FROM tenant_memberships m WHERE m.tenant_id=w.tenant_id
                AND m.account_id=w.account_id AND m.role='owner' AND m.active)
"""


async def consent(db, tenant, account, phone):
    if not await ready(db):
        raise HTTPException(503, "Preferência de contato indisponível")
    if phone is not None and not valid_phone(phone):
        raise HTTPException(422, "Informe um WhatsApp comercial válido")
    result = await db.execute(text("""
        WITH input AS (SELECT CAST(:phone AS text) AS phone)
        UPDATE orion_welcome_outbox w SET
          recipient=CASE WHEN w.status='queued' THEN coalesce(input.phone,recipient) ELSE recipient END,
          consented_at=CASE WHEN input.phone IS NOT NULL AND w.status='queued' THEN now() ELSE consented_at END,
          revoked_at=CASE WHEN input.phone IS NULL THEN now() ELSE revoked_at END,
          status=CASE WHEN input.phone IS NULL AND w.status='queued' THEN 'revoked' ELSE status END
        FROM input
        WHERE w.tenant_id=:tenant AND w.account_id=:account
          AND (input.phone IS NULL OR (w.status='queued' AND w.revoked_at IS NULL AND EXISTS (
            SELECT 1 FROM business_onboarding o WHERE o.tenant_id=w.tenant_id
              AND o.configuration->>'contact_phone'=input.phone)))
    """), {"tenant": tenant, "account": account, "phone": phone})
    return result.rowcount == 1


async def _claim(db, channel):
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtext('orion-welcome-rate-v1'))"))
    row = (await db.execute(text(f"""
        SELECT w.id,w.recipient,a.name,t.name AS business
        FROM orion_welcome_outbox w JOIN business_onboarding o ON o.tenant_id=w.tenant_id
        JOIN panel_accounts a ON a.id=w.account_id JOIN tenants t ON t.id=w.tenant_id
        WHERE w.status='queued' AND {_LIVE_CONSENT}
          AND NOT EXISTS (SELECT 1 FROM orion_welcome_outbox
                          WHERE started_at>now()-interval '1 minute')
        ORDER BY w.consented_at LIMIT 1 FOR UPDATE OF w SKIP LOCKED
    """))).mappings().one_or_none()
    if not row:
        return None
    duplicate = (await db.execute(text("""
        SELECT EXISTS(SELECT 1 FROM orion_welcome_outbox WHERE recipient=:phone
          AND started_at>now()-interval '7 days' AND status IN ('sending','accepted','delivered','uncertain'))
    """), {"phone": row["recipient"]})).scalar_one()
    body = message(row["name"], row["business"])
    await db.execute(text("""
        UPDATE orion_welcome_outbox SET status=:status,started_at=now(),checked_at=now(),
            channel_id=:channel,channel_tenant_id=:admin,body_digest=:digest WHERE id=:id
    """), {"id": row["id"], "status": "coalesced" if duplicate else "sending",
           "channel": channel["id"], "admin": channel["tenant_id"],
           "digest": hashlib.sha256(body.encode()).hexdigest()})
    return None if duplicate else (row["id"], row["recipient"], body)


def _outcome(receipt):
    state = receipt.get("state")
    if state == "accepted" and receipt.get("accepted") is True:
        return "delivered" if receipt.get("delivery") in {"delivered", "read"} else "accepted"
    return "failed" if state == "refused" and receipt.get("delivery") == "not_sent" else "uncertain"


async def _record(operation, state):
    async with support_session_factory()() as db:
        async with db.begin():
            await db.execute(text("""
                UPDATE orion_welcome_outbox SET status=:status,checked_at=now()
                WHERE id=:id AND status IN ('sending','accepted','uncertain')
                  AND (status<>'accepted' OR :status IN ('accepted','delivered'))
            """), {"id": operation, "status": state})


async def scan_welcome_outbox(_ctx):
    if not enabled():
        return
    from app.automation_transport import _request
    from app.plan_support import administrative_channel

    async with support_session_factory()() as db:
        async with db.begin():
            if not await ready(db):
                return
            receipt = (await db.execute(text("""
                SELECT id,channel_tenant_id,channel_id FROM orion_welcome_outbox
                WHERE status IN ('sending','accepted','uncertain')
                  AND checked_at<now()-interval '2 minutes' AND started_at>now()-interval '7 days'
                ORDER BY checked_at LIMIT 1 FOR UPDATE SKIP LOCKED
            """))).mappings().one_or_none()
            if receipt:
                await db.execute(text("UPDATE orion_welcome_outbox SET checked_at=now() WHERE id=:id"),
                                 {"id": receipt["id"]})
    if receipt:
        try:
            result = await _request(receipt["channel_tenant_id"], receipt["channel_id"], receipt["id"])
            await _record(receipt["id"], _outcome(result))
        except (HTTPException, httpx.HTTPError, OSError, ValueError):
            await _record(receipt["id"], "uncertain")
    try:
        async with asyncio.timeout(2):
            selected = await administrative_channel()
    except (HTTPException, httpx.HTTPError, OSError, TimeoutError, ValueError):
        return
    if not selected:
        return
    channel, _ = selected
    async with support_session_factory()() as db:
        async with db.begin():
            job = await _claim(db, channel)
    if not job:
        return
    operation, phone, body = job
    try:
        result = await _request(channel["tenant_id"], channel["id"], operation, message={
            "to": phone.lstrip("+"), "text": body, "operation_id": str(operation), "source": "welcome",
        })
    except (HTTPException, httpx.HTTPError, OSError, ValueError):
        result = {}
    await _record(operation, _outcome(result))


async def authorize(channel_id, payload, request):
    from app.inbox_runtime import fingerprint
    from app.qr_channels import authenticated_channel
    from app.qr_legacy import configured_tenant

    if not enabled() or not payload.text or payload.control_revision is not None:
        raise HTTPException(403, "Boas-vindas indisponíveis")
    identity = await authenticated_channel(request)
    if identity.tenant_id != configured_tenant() or identity.channel_id != channel_id:
        raise HTTPException(403, "Canal administrativo não autorizado")
    if fingerprint(payload.phone, payload.text) != payload.fingerprint:
        raise HTTPException(403, "Conteúdo diferente do aprovado")
    async with support_session_factory()() as db:
        allowed = (await db.execute(text(f"""
            SELECT EXISTS (
                SELECT 1 FROM orion_welcome_outbox w JOIN business_onboarding o ON o.tenant_id=w.tenant_id
                JOIN panel_accounts a ON a.id=w.account_id JOIN tenants admin ON admin.id=w.channel_tenant_id
                WHERE w.id=:id AND w.channel_id=:channel AND w.channel_tenant_id=:admin
                  AND w.channel_id=admin.whatsapp_channel_id
                  AND (public.orion_plan_snapshot(admin.id)->>'platform_workspace')='true'
                  AND w.recipient=:phone AND w.body_digest=:digest AND w.status='sending'
                  AND w.started_at>now()-interval '2 minutes' AND {_LIVE_CONSENT}
            )
        """), {"id": payload.operation_id, "channel": channel_id, "admin": identity.tenant_id,
               "phone": "+" + payload.phone,
               "digest": hashlib.sha256(payload.text.encode()).hexdigest()})).scalar_one()
    if not allowed:
        raise HTTPException(403, "Envio sem autorização atual")
    return {"authorized": True, "operation_id": payload.operation_id}
