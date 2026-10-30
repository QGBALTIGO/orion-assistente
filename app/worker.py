"""Arq Worker: inbox durável, fila por tenant e outbox.

Em produção, só é permitido enviar após ativação explícita de META_LIVE_SEND,
canal conectado, token por canal decifrado e janela Meta permitida. Não há
broadcast nem envio automático de templates fora da janela nesta versão.
"""
import os
import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

from arq import cron,func
from arq.connections import RedisSettings
from sqlalchemy import text

from app.catalog_api import support_session_factory
from app.database import get_session_factory, tenant_transaction
from app.messaging import CloudApiProvider
from app.whatsapp_bot import reply
from app.calendar_worker import scan_calendar_outbox,process_calendar_outbox
from app.auto_completion import scan_due_appointments,process_due_appointment
from app.billing_worker import reconcile_asaas_job,scan_pending_billing
from app.cakto_api import scan_pending_cakto
from app.automation_runtime import interaction,process_automation_job,reconcile_automation_job,scan_automations
from app.inbox_runtime import process_inbox_message,scan_inbox
from app.email_outbox import process_email_outbox,scan_email_outbox
from app.account_erasure import erase_due_account_logins
from app.appointment_notifications import process_notification as process_appointment_notification, scan_notifications as scan_appointment_notifications, cleanup_notification_payloads
from app.public_customer_auth import send_public_customer_code


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
                RETURNING id, channel_id, sender_e164, message_text,external_id,envelope
            """), {"t": tenant, "id": event})).mappings().first()
    if claim is None:
        return "already_processed_or_processing"
    try:
        await interaction(tenant, claim["sender_e164"])
        from app import inbox_control as control
        if control.enabled():
            return await _central_inbound(ctx,tenant,event,claim,factory)
        answer = await _event_answer(tenant,event,claim,ctx['redis'])
        async with factory() as session:
            async with tenant_transaction(session, tenant):
                if answer:
                    await session.execute(text("""
                        INSERT INTO message_outbox
                        (tenant_id, channel_id, event_id, to_e164, body)
                        VALUES (:tenant,:channel,:event,:recipient,:body)
                        ON CONFLICT (tenant_id,event_id) DO NOTHING
                    """), {"tenant":tenant,"channel":claim["channel_id"],
                            "event":event,"recipient":claim["sender_e164"],"body":bounded_answer(answer)})
                await session.execute(text("""
                    UPDATE inbound_events SET status='processed',processed_at=now(),
                        message_text='',processing_at=NULL WHERE tenant_id=:t AND id=:id
                """), {"t":tenant,"id":event})
                await session.execute(text("""
                    INSERT INTO usage_counters (tenant_id,period,inbound_messages)
                    VALUES (:t,date_trunc('month',CURRENT_DATE)::date,1)
                    ON CONFLICT (tenant_id,period) DO UPDATE
                    SET inbound_messages=usage_counters.inbound_messages+1
                """),{"t":tenant})
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
    from app.legacy_meta_outbox import process
    return await process(ctx,tenant_id,outbox_id)


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


async def startup_plan_permissions(ctx):
    # ARQ awaits this before consuming jobs; API predeploy performs the only
    # privileged migration/bootstrap. Existing workers continue normally.
    from scripts.wait_for_agenda_schema import Settings
    from scripts.wait_for_plan_permissions_schema import wait_for_schema
    await wait_for_schema(Settings.from_environment())
    ctx['plan_permissions_ready'] = True
    logging.getLogger(__name__).info('orion_plan_permissions_0050_worker_ready')


class WorkerSettings:
    on_startup = startup_plan_permissions
    functions = [func(send_public_customer_code,name='send_public_customer_code'),func(process_appointment_notification,name='process_appointment_notification'),infrastructure_ping,process_inbound_event,process_message_outbox,process_calendar_outbox,process_due_appointment,reconcile_asaas_job,process_automation_job,reconcile_automation_job,process_inbox_message,process_email_outbox]
    cron_jobs = [cron(cleanup_notification_payloads, minute={13}, second={10}),cron(scan_appointment_notifications, minute=set(range(60)), second={0,15,30,45}),
                 cron(recover_events, minute={0,5,10,15,20,25,30,35,40,45,50,55}, second=0),
                 cron(scan_calendar_outbox, minute=set(range(60)), second=10),
                 cron(scan_due_appointments, minute=set(range(60)), second={0,10,20,30,40,50}),
                 cron(scan_pending_billing, minute={3,13,23,33,43,53}, second=0),
                 cron(scan_pending_cakto, minute={4,14,24,34,44,54}, second=0),
                 cron(scan_automations, minute=set(range(60)), second=20),
                 cron(scan_inbox,minute=set(range(60)),second={5,35}),
                 cron(scan_email_outbox,minute=set(range(60)),second={0,15,30,45}),
                 # Retention-aware, irreversible login removal after 30 days.
                 cron(erase_due_account_logins,minute={17},second=12)]
    redis_settings = RedisSettings.from_dsn(os.getenv("REDIS_URL", "redis://localhost:6379/0"))


async def _central_inbound(ctx,tenant,event,claim,factory):
    from app import inbox_control as control,inbox_runtime as runtime
    from app.whatsapp_bot import state_key
    channel,phone=claim["channel_id"],claim["sender_e164"]
    # A signed event may have been queued before central activation. Journal
    # its original text before clearing the transient queue, without inventing
    # a provider timestamp or granting a new Meta service window.
    async with factory() as db:
        async with tenant_transaction(db,tenant):
            await runtime.record_inbound(db,tenant,channel,phone,claim['external_id'],claim['message_text'])
    async with control.assistant_turn(tenant,channel,phone,ctx["redis"],state_key(str(tenant),str(channel),phone)) as revision:
        async with factory() as db:
            async with tenant_transaction(db,tenant):
                observed=(await db.execute(text("""SELECT m.control_revision FROM inbox_messages m JOIN inbox_controls c
                  ON c.tenant_id=m.tenant_id AND c.id=m.control_id WHERE m.tenant_id=:t AND c.channel_id=:c
                  AND c.contact_e164=:phone AND m.direction='in' AND m.external_id=:external"""),
                  {'t':tenant,'c':channel,'phone':phone,'external':claim['external_id']})).scalar_one_or_none()
        answer=None if revision is False or observed!=revision else await _event_answer(tenant,event,claim,ctx['redis'])
        # A customer handoff changed the generation during reply. Mark the
        # inbound processed without binding the obsolete AI authority.
        token=control._assistant_scope.set(None)
        try:
            async with factory() as db:
                async with tenant_transaction(db,tenant):
                    row=await control.lock_control(db,tenant,channel,phone)
                    if answer and row["mode"]=="ai" and row["revision"]==revision and row["pending_operation"] is None:
                        from uuid import uuid5,NAMESPACE_URL
                        operation=uuid5(NAMESPACE_URL,"orion:meta:"+str(event))
                        body=bounded_answer(answer)
                        await runtime.record_outbound(db,tenant,row,operation,"assistant",body,runtime.fingerprint(phone,body))
                    await db.execute(text("""UPDATE inbound_events SET status='processed',processed_at=now(),
                      message_text='',processing_at=NULL WHERE tenant_id=:t AND id=:id"""),{"t":tenant,"id":event})
                    await db.execute(text("""INSERT INTO usage_counters(tenant_id,period,inbound_messages)
                      VALUES(:t,date_trunc('month',CURRENT_DATE)::date,1) ON CONFLICT(tenant_id,period)
                      DO UPDATE SET inbound_messages=usage_counters.inbound_messages+1"""),{"t":tenant})
        finally:
            control._assistant_scope.reset(token)
    return "processed"


def bounded_answer(answer):
    from app.qr_presentation import fits_message
    if fits_message(answer): return answer
    return 'Preciso revisar o resumo completo antes de continuar. Nenhuma nova confirmação foi enviada. Pode indicar qual pedido deseja conferir?'


async def _event_answer(tenant,event,claim,redis):
    from app.plan_permissions import tenant_has_feature
    if not await tenant_has_feature(tenant, "reception.ai"):
        return None
    from app.communication_events import bind_meta,event_reply,needs_review,invalidate_proposal
    from app.whatsapp_bot import state_key
    from fastapi import HTTPException
    meta=claim.get('envelope') or {}
    # Domain writes and transaction completion both recheck the authenticated
    # event; another inbound cannot authorize a result prepared for this one.
    try:
        async with redis.lock(f'orion:meta:turn:{tenant}:{claim["channel_id"]}:{claim["sender_e164"]}',timeout=30,blocking_timeout=3):
            prepared=False
            try:
                with bind_meta(tenant,claim['channel_id'],claim['sender_e164'],event):
                    async with get_session_factory()() as db:
                        async with tenant_transaction(db,tenant):pass
                    prepared=True
                    repair=event_reply(meta,claim['message_text'])
                    if needs_review(meta) or repair:
                        await invalidate_proposal(redis,state_key(str(tenant),str(claim['channel_id']),claim['sender_e164']))
                    answer=repair if repair is not None else await reply(tenant,claim['channel_id'],claim['sender_e164'],claim['message_text'],redis,message_id=str(event))
                    if not await tenant_has_feature(tenant, "reception.ai"):
                        return None
                    async with get_session_factory()() as db:
                        async with tenant_transaction(db,tenant):pass
                    return answer or None
            except HTTPException as exc:
                if exc.status_code==409 and prepared:
                    await invalidate_proposal(redis,state_key(str(tenant),str(claim['channel_id']),claim['sender_e164']))
                raise
    except HTTPException as exc:
        if exc.status_code==409: return None
        if (exc.status_code==403 and isinstance(exc.detail,dict)
                and exc.detail.get("code")=="PLAN_FEATURE_UNAVAILABLE"):
            return None
        raise
