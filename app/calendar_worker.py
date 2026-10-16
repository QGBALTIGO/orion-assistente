"""Outbox Google: isolamento multiempresa, retry exponencial e falha não cancela booking."""
import os
from uuid import UUID

from sqlalchemy import text
from cryptography.fernet import Fernet

from app.calendar_provider import GoogleCalendar,deterministic_event_id
from app.catalog_api import support_session_factory
from app.database import get_session_factory,tenant_transaction


async def process_calendar_outbox(ctx:dict,tenant_id:str,outbox_id:str):
    if os.getenv("GOOGLE_LIVE_SYNC")!="1":
        return "disabled_by_default"
    key=os.getenv("INTEGRATION_FERNET_KEY")
    if not key:
        return "missing_fernet_key"
    tenant,item=UUID(tenant_id),UUID(outbox_id)
    async with get_session_factory()() as s:
        async with tenant_transaction(s,tenant):
            row=(await s.execute(text("""
                SELECT id,appointment_id,action FROM calendar_outbox
                WHERE tenant_id=:t AND id=:id AND status='pending'
                  AND next_attempt_at<=now()
                FOR UPDATE SKIP LOCKED
            """),{"t":tenant,"id":item})).mappings().first()
            if not row:return "not_pending"
            connection=(await s.execute(text("""
                SELECT calendar_id,refresh_token_enc FROM google_connections
                WHERE tenant_id=:t AND status='active'
            """),{"t":tenant})).mappings().first()
            if not connection or not connection["refresh_token_enc"]:
                # Estado revogado não cancela reserva; alerta operável por status.
                await s.execute(text("""
                    UPDATE calendar_outbox SET last_error='calendar_not_connected'
                    WHERE tenant_id=:t AND id=:id
                """),{"t":tenant,"id":item})
                return "not_connected"
            appt=(await s.execute(text("""
                SELECT id,protocol,start_at,end_at FROM appointments
                WHERE tenant_id=:t AND id=:id
            """),{"t":tenant,"id":row["appointment_id"]})).mappings().one()
            try:
                refresh=Fernet(key.encode()).decrypt(connection["refresh_token_enc"]).decode()
                provider=GoogleCalendar()
                token=await provider.refresh_access_token(refresh)
                external=await provider.apply(token,connection["calendar_id"],
                    deterministic_event_id(appt["id"]),row["action"],dict(appt))
                await s.execute(text("""
                    UPDATE calendar_outbox
                    SET status='sent',external_event_id=:external,updated_at=now(),
                        attempts=attempts+1,last_error=NULL
                    WHERE tenant_id=:t AND id=:id
                """),{"external":external,"t":tenant,"id":item})
                return "sent"
            except Exception:
                await s.execute(text("""
                    UPDATE calendar_outbox SET attempts=attempts+1,updated_at=now(),
                        next_attempt_at=now()+make_interval(
                          secs=>LEAST(3600,15*power(2,LEAST(attempts,8))::int)),
                        status=CASE WHEN attempts>=7 THEN 'dead' ELSE 'pending' END,
                        last_error='google_provider_failure'
                    WHERE tenant_id=:t AND id=:id
                """),{"t":tenant,"id":item})
                return "retry"


async def scan_calendar_outbox(ctx:dict):
    async with support_session_factory()() as s:
        pending=(await s.execute(text("""
            SELECT id,tenant_id FROM calendar_outbox
            WHERE status='pending' AND next_attempt_at<=now() LIMIT 100
        """))).mappings().all()
    for row in pending:
        await ctx["redis"].enqueue_job("process_calendar_outbox",
                                       str(row["tenant_id"]),str(row["id"]))
    return len(pending)
