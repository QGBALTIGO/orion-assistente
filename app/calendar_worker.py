"""Tenant-isolated, one-way Google synchronization with bounded retries."""
import asyncio
import os
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import text

from app.calendar_provider import CalendarProviderError, GoogleCalendar, deterministic_event_id
from app.calendar_sync import calendar_lock
from app.catalog_api import support_session_factory
from app.database import get_session_factory, tenant_transaction

CONNECTION_BUSY_DELAY_SECONDS = 30


def worker_configured():
    key = os.getenv("INTEGRATION_FERNET_KEY", "")
    if not key or not os.getenv("GOOGLE_CLIENT_ID") or not os.getenv("GOOGLE_CLIENT_SECRET"):
        return False
    try:
        Fernet(key.encode())
    except (ValueError, TypeError):
        return False
    return True


async def record_failure(db, tenant, item, failure):
    await db.execute(text("""
        UPDATE calendar_outbox SET attempts=attempts+1,updated_at=clock_timestamp(),
            next_attempt_at=now()+make_interval(
                secs=>LEAST(3600,15*power(2,LEAST(attempts,8))::int)),
            status=CASE WHEN :retryable AND attempts<7 THEN 'pending' ELSE 'dead' END,
            last_error=:error
        WHERE tenant_id=:tenant AND id=:id
    """), {"tenant": tenant, "id": item, "retryable": failure.retryable, "error": failure.code})
    await db.execute(text("""
        UPDATE google_connections SET last_error=:error,
            status=CASE WHEN :reauthorize THEN 'error' ELSE status END
        WHERE tenant_id=:tenant
    """), {"tenant": tenant, "error": failure.code, "reauthorize": failure.reauthorize})


async def process_calendar_outbox(ctx: dict, tenant_id: str, outbox_id: str):
    if os.getenv("GOOGLE_LIVE_SYNC") != "1":
        return "disabled_by_default"
    if not worker_configured():
        return "not_configured"
    tenant, item = UUID(tenant_id), UUID(outbox_id)
    async with get_session_factory()() as db:
        async with tenant_transaction(db, tenant):
            # Credential changes and revoke use this same lock. A successful
            # disconnect cannot race an in-flight request using the old token.
            if not await calendar_lock(db, tenant):
                # Give a different due time to this unsent item. The scanner's
                # job ID then changes even while ARQ retains this job's result.
                # Skip an already claimed outbox instead of waiting on it.
                await db.execute(text("""
                    UPDATE calendar_outbox SET next_attempt_at=clock_timestamp()+make_interval(secs=>:busy_delay)
                    WHERE tenant_id=:tenant AND id IN (
                        SELECT id FROM calendar_outbox
                        WHERE tenant_id=:tenant AND id=:id AND status='pending'
                          AND next_attempt_at<=now()
                        FOR UPDATE SKIP LOCKED
                    )
                """), {"tenant": tenant, "id": item, "busy_delay": CONNECTION_BUSY_DELAY_SECONDS})
                return "connection_busy"
            connection = (await db.execute(text("""
                SELECT calendar_id,refresh_token_enc,status,last_error FROM google_connections
                WHERE tenant_id=:tenant FOR UPDATE
            """), {"tenant": tenant})).mappings().first()
            row = (await db.execute(text("""
                SELECT id,appointment_id,action FROM calendar_outbox
                WHERE tenant_id=:tenant AND id=:id AND status='pending'
                  AND next_attempt_at<=now()
                FOR UPDATE SKIP LOCKED
            """), {"tenant": tenant, "id": item})).mappings().first()
            if row is None:
                return "not_pending"
            if not connection or connection["status"] != "active" or not connection["refresh_token_enc"]:
                error = connection["last_error"] if connection and connection["status"] == "error" else "calendar_not_connected"
                # Move disconnected work behind other due tenants. It must not
                # exhaust retries or monopolize the scanner's first 100 rows.
                await db.execute(text("""
                    UPDATE calendar_outbox SET last_error=:error,
                        next_attempt_at=now()+interval '15 minutes'
                    WHERE tenant_id=:tenant AND id=:id
                """), {"tenant": tenant, "id": item, "error": error or "calendar_not_connected"})
                return "not_connected"
            if connection["calendar_id"] == "primary":
                failure = CalendarProviderError("google_calendar_selection_required", reauthorize=True)
                await record_failure(db, tenant, item, failure)
                return "needs_calendar_selection"
            appointment = (await db.execute(text("""
                SELECT a.id,a.tenant_id,a.protocol,a.start_at,a.end_at,a.status,t.timezone,
                       c.name AS customer_name,p.name AS professional_name,
                       (SELECT string_agg(i.name_snapshot, ', ' ORDER BY i.item_order)
                        FROM appointment_items i
                        WHERE i.tenant_id=a.tenant_id AND i.appointment_id=a.id) AS service_names
                FROM appointments a JOIN tenants t ON t.id=a.tenant_id
                JOIN customers c ON c.tenant_id=a.tenant_id AND c.id=a.customer_id
                JOIN professionals p ON p.tenant_id=a.tenant_id AND p.id=a.professional_id
                WHERE a.tenant_id=:tenant AND a.id=:id
            """), {"tenant": tenant, "id": row["appointment_id"]})).mappings().one()
            action = "delete" if appointment["status"] == "cancelled" else row["action"]
            if appointment["status"] in {"confirmed", "completed", "no_show"}:
                # Coalesced intentions always publish the latest committed state.
                action = "create" if row["action"] == "create" else "update"
            try:
                try:
                    refresh = Fernet(os.environ["INTEGRATION_FERNET_KEY"].encode()).decrypt(
                        connection["refresh_token_enc"]).decode()
                except (InvalidToken, UnicodeError):
                    raise CalendarProviderError("google_credential_unreadable", reauthorize=True) from None
                provider = GoogleCalendar()
                async with asyncio.timeout(25):
                    token = await provider.refresh_access_token(refresh)
                    external = await provider.apply(token, connection["calendar_id"],
                                                    deterministic_event_id(appointment["id"]), action,
                                                    dict(appointment))
                await db.execute(text("""
                    UPDATE calendar_outbox SET status='sent',external_event_id=:external,
                        updated_at=clock_timestamp(),attempts=attempts+1,last_error=NULL
                    WHERE tenant_id=:tenant AND id=:id
                """), {"tenant": tenant, "id": item, "external": external})
                await db.execute(text("""
                    UPDATE google_connections SET last_error=NULL WHERE tenant_id=:tenant
                """), {"tenant": tenant})
                return "sent"
            except CalendarProviderError as failure:
                await record_failure(db, tenant, item, failure)
                return "retry" if failure.retryable else "failed"
            except TimeoutError:
                await record_failure(db, tenant, item, CalendarProviderError("google_unavailable", retryable=True))
                return "retry"
            except Exception:
                # Do not serialize exceptions: they can contain token URLs or
                # event data. The outbox exposes only this finite error code.
                await record_failure(db, tenant, item, CalendarProviderError("google_worker_failure", retryable=True))
                return "retry"


async def scan_calendar_outbox(ctx: dict):
    configured = worker_configured()
    enabled = os.getenv("GOOGLE_LIVE_SYNC") == "1"
    # Short-lived worker heartbeat, configuration state only. Never store
    # tokens or user data in Redis; the API can diagnose "paused" versus
    # an unavailable worker instead of guessing from its own environment.
    worker_health = "ready" if configured and enabled else "paused" if not enabled else "missing_configuration"
    try:
        await ctx["redis"].set("orion:google:worker-health", worker_health, ex=780)
    except (OSError, RuntimeError):
        pass
    if not enabled or not configured:
        return 0
    async with support_session_factory()() as db:
        pending = (await db.execute(text("""
            SELECT id,tenant_id,next_attempt_at FROM calendar_outbox
            WHERE status='pending' AND next_attempt_at<=now()
            ORDER BY next_attempt_at,id LIMIT 100
        """))).mappings().all()
    queued = 0
    for row in pending:
        job = await ctx["redis"].enqueue_job(
            "process_calendar_outbox", str(row["tenant_id"]), str(row["id"]),
            _job_id=f"calendar:{row['id']}:{row['next_attempt_at'].isoformat()}")
        queued += job is not None
    return queued
