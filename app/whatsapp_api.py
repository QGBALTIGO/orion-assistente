"""Webhook Meta: verifica assinatura ANTES de ler JSON, persiste antes de ACK."""
import json
import os
import secrets
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.catalog_api import support_session_factory
from app.database import get_session_factory, tenant_transaction
from app.messaging import extract_messages, verify_meta_signature
from app.customer_names import merge_whatsapp_name

router = APIRouter(prefix="/webhooks/whatsapp", tags=["WhatsApp Meta"])


@router.get("")
async def handshake(request: Request):
    params = request.query_params
    verify = os.getenv("META_VERIFY_TOKEN", "")
    if (len(verify) < 20 or params.get("hub.mode") != "subscribe" or
        not secrets.compare_digest(params.get("hub.verify_token", ""), verify)):
        raise HTTPException(403, "Verificação recusada")
    challenge = params.get("hub.challenge", "")
    if not challenge.isdigit() or len(challenge) > 64:
        raise HTTPException(400, "Challenge inválido")
    return PlainTextResponse(challenge)


@router.post("")
async def receive_meta(request: Request):
    raw = await request.body()
    if len(raw) > 256_000:
        raise HTTPException(413, "Payload excede limite")
    if not verify_meta_signature(
        raw, request.headers.get("x-hub-signature-256"), os.getenv("META_APP_SECRET")
    ):
        raise HTTPException(401, "Assinatura inválida")
    try:
        payload = json.loads(raw)
        events = extract_messages(payload)
    except (ValueError, TypeError, KeyError, AttributeError):
        raise HTTPException(400, "Payload inválido") from None
    if len(events) > 100:
        raise HTTPException(413, "Lote excede limite")
    # Lookup de número assinado só consulta IDs, não lê conteúdo de clientes.
    support = support_session_factory()
    factory = get_session_factory()
    recorded = []
    for item in events:
        async with support() as session:
            result = (await session.execute(text("""
                SELECT tenant_id, id FROM whatsapp_channels
                WHERE phone_number_id=:number AND status IN ('testing','connected')
            """), {"number": item["phone_number_id"]})).mappings().first()
        if result is None:
            # Sem tenant associado, nunca tentar adivinhar o roteamento.
            continue
        event_id = uuid4()
        try:
            async with factory() as session:
                async with tenant_transaction(session, result["tenant_id"]):
                    out = await session.execute(text("""
                        INSERT INTO inbound_events
                        (id, tenant_id, channel_id, external_id, sender_e164, message_text)
                        VALUES (:id, :tenant, :channel, :external, :sender, :body)
                        ON CONFLICT (channel_id, external_id) DO NOTHING RETURNING id
                    """), dict(id=event_id, tenant=result["tenant_id"], channel=result["id"],
                               external=item["id"], sender=item["sender_e164"], body=item["text"]))
                    persisted = out.scalar_one_or_none()
                    if persisted and item.get("whatsapp_name"):
                        # The Meta signature and channel lookup already chose
                        # this tenant; persist the profile in the same commit as
                        # the inbound event, before the worker can book for it.
                        await merge_whatsapp_name(session, result["tenant_id"],
                                                  item["sender_e164"], item["whatsapp_name"])
            if persisted:
                recorded.append((str(result["tenant_id"]), str(persisted)))
        except IntegrityError:
            raise HTTPException(503, "Falha de persistência") from None
    # Responder 200 só depois que eventos foram COMMITados.
    # Se Redis estiver fora, a recuperação do worker varre pendências no banco.
    if recorded:
        try:
            from arq import create_pool
            from arq.connections import RedisSettings
            redis = await create_pool(RedisSettings.from_dsn(os.getenv("REDIS_URL", "redis://localhost:6379/0")))
            try:
                for tenant_id, event_id in recorded:
                    await redis.enqueue_job("process_inbound_event", tenant_id, event_id,
                                            _job_id="wa:" + event_id)
            finally:
                await redis.aclose()
        except (ConnectionError, TimeoutError, OSError, __import__('redis').exceptions.RedisError):
            pass
    return {"received": True}
