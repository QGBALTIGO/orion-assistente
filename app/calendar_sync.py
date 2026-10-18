"""Transactional calendar intent and tenant-scoped integration lock."""
from sqlalchemy import text

OUTSIDE_INITIAL_SYNC = "google_before_connection"

CALENDAR_ACTIONS = {
    "confirmed": "create", "rescheduled": "update", "cancelled": "delete",
    "completed": "update", "no_show": "update",
}


async def enqueue_calendar_sync(db, tenant_id, appointment_id, event):
    """Join the caller's transaction. Never commit, send, or read Google."""
    action = CALENDAR_ACTIONS.get(event)
    if action is None:
        return
    await db.execute(text("""
        INSERT INTO calendar_outbox (tenant_id,appointment_id,action)
        VALUES (:tenant,:appointment,:action)
        ON CONFLICT (tenant_id,appointment_id) DO UPDATE
        SET action=EXCLUDED.action,status='pending',attempts=0,
            next_attempt_at=now(),updated_at=now(),last_error=NULL
        WHERE calendar_outbox.last_error IS DISTINCT FROM :outside
           OR calendar_outbox.external_event_id IS NOT NULL
           OR EXISTS (SELECT 1 FROM appointments a
                      WHERE a.tenant_id=:tenant AND a.id=:appointment AND a.end_at>=now())
    """), {"tenant": tenant_id, "appointment": appointment_id, "action": action,
            "outside": OUTSIDE_INITIAL_SYNC})


async def calendar_lock(db, tenant_id) -> bool:
    """Nonblocking lock shared by the worker, credential changes, and revoke."""
    return bool((await db.execute(text("""
        SELECT pg_try_advisory_xact_lock(hashtextextended(:key, 0))
    """), {"key": "orion:google:" + str(tenant_id)})).scalar_one())
