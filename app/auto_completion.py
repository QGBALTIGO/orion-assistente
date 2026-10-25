"""Durable scheduled completion, isolated per business and guarded against backfills.

The Redis activation fence is created on the first enabled scan, so enabling
this feature cannot turn historical confirmed bookings into earned revenue.
The worker can recover eligible appointments after a temporary outage.
"""
import os
from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text

from app.catalog_api import support_session_factory
from app.database import get_session_factory, tenant_transaction
from app.management_models import transition_appointment
from app.management_schemas import AppointmentTransition

ENABLED_SINCE_KEY = "orion:appointments:auto-complete:enabled-since"
SCAN_LIMIT = 100


def auto_completion_enabled():
    return os.getenv("ORION_AUTO_COMPLETE_APPOINTMENTS") == "1"


async def activation_time(redis):
    await redis.set(ENABLED_SINCE_KEY, datetime.now(timezone.utc).isoformat(), nx=True)
    raw = await redis.get(ENABLED_SINCE_KEY)
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    when = datetime.fromisoformat(raw)
    if when.tzinfo is None or when.utcoffset() is None:
        raise RuntimeError("Invalid auto-completion activation fence")
    return when.astimezone(timezone.utc)


async def scan_due_appointments(ctx):
    if not auto_completion_enabled():
        return 0
    since = await activation_time(ctx["redis"])
    async with support_session_factory()() as db:
        due = (await db.execute(text("""
            SELECT tenant_id,id FROM appointments
            WHERE status='confirmed' AND end_at<=clock_timestamp()
              AND end_at>=:since
            ORDER BY end_at,id LIMIT :limit
        """), {"since": since, "limit": SCAN_LIMIT})).mappings().all()
    queued = 0
    minute = int(datetime.now(timezone.utc).timestamp() // 60)
    for row in due:
        job = await ctx["redis"].enqueue_job(
            "process_due_appointment", str(row["tenant_id"]), str(row["id"]),
            _job_id=f"orion:complete:{row['id']}:{minute}")
        queued += job is not None
    return queued


async def process_due_appointment(ctx, tenant_id: str, appointment_id: str):
    if not auto_completion_enabled():
        return "disabled"
    since = await activation_time(ctx["redis"])
    tenant, appointment = UUID(tenant_id), UUID(appointment_id)
    try:
        async with get_session_factory()() as db:
            async with tenant_transaction(db, tenant):
                record = (await db.execute(text("""
                    SELECT status,end_at FROM appointments
                    WHERE tenant_id=:tenant AND id=:appointment
                """), {"tenant": tenant, "appointment": appointment})).mappings().one_or_none()
                if (not record or record["status"] != "confirmed"
                        or record["end_at"] < since
                        or record["end_at"] > datetime.now(timezone.utc)):
                    return "skipped"
                payload = AppointmentTransition(
                    expected_status="confirmed", coupon_id=None,
                    idempotency_key="auto-complete-" + appointment.hex)
                await transition_appointment(
                    db, tenant, appointment, "completed", payload, "system",
                    automated=True)
        return "completed"
    except HTTPException as exc:
        if exc.status_code in {404, 409}:
            # A manual cancellation, reschedule or completion won the race.
            return "skipped"
        raise
