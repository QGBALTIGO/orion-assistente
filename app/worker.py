"""Arq Worker: inbox durável, fila por tenant e outbox.

Em produção, só é permitido enviar após ativação explícita de META_LIVE_SEND,
canal conectado, token por canal decifrado e janela Meta permitida. Não há
broadcast nem envio automático de templates fora da janela nesta versão.
"""
import os
from datetime import datetime, timedelta, timezone
from uuid import UUID

from arq import cron
from arq.connections import RedisSettings
from sqlalchemy import text

from app.catalog_api import support_session_factory
from app.database import get_session_factory, tenant_transaction
from app.messaging import CloudApiProvider
from app.whatsapp_bot import reply


async def infrastructure_ping(ctx: dict) -> str:
    return "ok"


async def process_inbound_event(ctx: dict, tenant_id: str, event_id: str):
    tenant, event = UUID(tenant_id), UUID(event_id)
    factory = get_session_factory()
    async with factory() as session:
        async with tenant_transaction(session, tenant):
            claim = (await session.execute(text("""
                UPDATE inbound_events SET status='processing',attempts=attempts+1,
                    processing_at=now()
                WHERE tenant_id=:t AND id=:id AND status='pending'
                RETURNING id, channel_id, sender_e164, message_text
            """), {"t": tenant, "id": event})).mappings().first()
    if claim is None:
        return "already_processed_or_processing"
    try:
        answer = await reply(tenant, claim["channel_id"], claim["sender_e164"],
                             claim["message_text"], ctx["redis"])
        async with factory() as session:
            async with tenant_transaction(session, tenant):
                if answer:
                    await session.execute(text("""
                        INSERT INTO message_outbox
                        (tenant_id, channel_id, event_id, to_e164, body)
                        VALUES (:tenant,:channel,:event,:recipient,:body)
                        ON CONFLICT (tenant_id,event_id) DO NOTHING
                    """), {"tenant":tenant,"channel":claim["channel_id"],
                            "event":event,"recipient":claim["sender_e164"],"body":answer[:1500]})
                await session.execute(text("""
                    UPDATE inbound_events SET status='processed',processed_at=now(),
                        message_text='',processing_at=NULL WHERE tenant_id=:t AND id=:id
                """), {"t":tenant,"id":event})
        return "processed"
    except Exception:
        async with factory() as session:
            async with tenant_transaction(session, tenant):
                await session.execute(text("""
                    UPDATE inbound_events SET status=CASE WHEN attempts >= 5
                        THEN 'failed' ELSE 'pending' END, processing_at=NULL
                    WHERE tenant_id=:t AND id=:id AND status='processing'
                """), {"t":tenant,"id":event})
        raise


async def process_message_outbox(ctx: dict, tenant_id: str, outbox_id: str):
    tenant, item_id = UUID(tenant_id), UUID(outbox_id)
    # Não enviar sem consentimento operacional de canal e credenciais válidas.
    if os.getenv("META_LIVE_SEND") != "1":
        return "not_enabled"
    key = os.getenv("INTEGRATION_FERNET_KEY")
    if not key:
        return "missing_encryption_key"
    from cryptography.fernet import Fernet
    factory = get_session_factory()
    async with factory() as session:
        async with tenant_transaction(session, tenant):
            item = (await session.execute(text("""
                SELECT o.id, o.to_e164, o.body, o.attempts,
                       c.phone_number_id, c.token_enc
                FROM message_outbox o JOIN whatsapp_channels c
                ON c.id=o.channel_id AND c.tenant_id=o.tenant_id
                WHERE o.tenant_id=:tenant AND o.id=:id AND o.status='pending'
                  AND c.status='connected' AND o.next_attempt_at<=now()
                FOR UPDATE OF o SKIP LOCKED
            """), {"tenant":tenant,"id":item_id})).mappings().first()
            if item is None:
                return "unavailable"
            # Lock e operação externa são acoplados para reduzir envio duplicado.
            # Idempotência absoluta entre commit e API Meta não é garantível.
            try:
                token = Fernet(key.encode()).decrypt(item["token_enc"]).decode()
                message_id = await CloudApiProvider().send_text(
                    {"phone_number_id":item["phone_number_id"],"access_token":token},
                    item["to_e164"],item["body"])
                await session.execute(text("""
                    UPDATE message_outbox SET status='sent',
                        external_delivery_id=:external,attempts=attempts+1
                    WHERE tenant_id=:t AND id=:id
                """), {"t":tenant,"id":item_id,"external":message_id})
                return "sent"
            except Exception:
                await session.execute(text("""
                    UPDATE message_outbox SET attempts=attempts+1,
                        next_attempt_at=now() + make_interval(
                            secs=>LEAST(3600, (power(2, LEAST(attempts, 8)) * 15)::int)),
                        status=CASE WHEN attempts>=7 THEN 'dead' ELSE 'pending' END,
                        last_error='provider_failure'
                    WHERE tenant_id=:t AND id=:id
                """), {"t":tenant,"id":item_id})
                return "retry"


async def recover_events(ctx: dict):
    # SELECT restrito a IDs de fila; nunca lê mensagem nem clientes de outros tenants.
    support = support_session_factory()
    async with support() as session:
        pending = (await session.execute(text("""
            SELECT tenant_id,id FROM inbound_events
            WHERE status='pending'
                OR (status='processing' AND processing_at < now()-interval '5 minutes')
            ORDER BY id LIMIT 100
        """))).mappings().all()
    for row in pending:
        tenant_id, event_id = str(row["tenant_id"]), str(row["id"])
        # Reabrir processamentos interrompidos dentro do tenant; corrida
        # posterior é protegida pela transição atômica pendente→processing.
        factory = get_session_factory()
        async with factory() as session:
            async with tenant_transaction(session, UUID(tenant_id)):
                await session.execute(text("""
                    UPDATE inbound_events SET status='pending',processing_at=NULL
                    WHERE id=:id AND status='processing'
                    AND processing_at < now()-interval '5 minutes'
                """), {"id":UUID(event_id)})
        await ctx["redis"].enqueue_job("process_inbound_event",tenant_id,event_id,
                                        _job_id="wa:retry:" + event_id)
    return len(pending)


class WorkerSettings:
    functions = [infrastructure_ping,process_inbound_event,process_message_outbox]
    cron_jobs = [cron(recover_events, minute={0,5,10,15,20,25,30,35,40,45,50,55}, second=0)]
    redis_settings = RedisSettings.from_dsn(os.getenv("REDIS_URL", "redis://localhost:6379/0"))
