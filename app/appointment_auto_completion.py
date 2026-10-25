"""Tenant-opted-in appointment auto-completion.

The job is read-only until it enters tenant RLS. It reuses the exact management
completion transition to snapshot commission, award loyalty and enqueue Google.
Ending the appointment is NOT proof that cash was collected; a pending
appointment-linked receivable is recorded instead. No WhatsApp messages sent.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid5, NAMESPACE_URL

from sqlalchemy import text

from app.catalog_api import support_session_factory
from app.database import get_session_factory, tenant_transaction

SCAN_BATCH = 100


def auto_completion_key(tenant: UUID, appointment: UUID) -> str:
    return str(uuid5(NAMESPACE_URL, f"orion:auto-completion:{tenant}:{appointment}"))


def receivable_id(tenant: UUID, appointment: UUID) -> UUID:
    return uuid5(NAMESPACE_URL, f"orion:auto-receivable:{tenant}:{appointment}")


def policy_enabled(policy: dict | None, end_at: datetime, now: datetime) -> bool:
    if not isinstance(policy, dict) or policy.get("enabled") is not True:
        return False
    stamp = policy.get("enabled_at")
    if not isinstance(stamp, str):
        return False
    try:
        enabled_at = datetime.fromisoformat(stamp)
    except ValueError:
        return False
    return (enabled_at.tzinfo is not None and enabled_at.utcoffset() is not None
            and enabled_at <= end_at <= now)


async def process_auto_completion(ctx: dict, tenant_id: str, appointment_id: str, expected_end: str):
    """Perform one atomic, idempotent completion with conservative guards."""
    from app.booking_service import acquire_professional_lock
    from app.management_models import transition_appointment
    from app.management_schemas import AppointmentTransition

    tenant, appointment = UUID(tenant_id), UUID(appointment_id)
    expected_time = datetime.fromisoformat(expected_end)
    if expected_time.tzinfo is None or expected_time.utcoffset() is None:
        return "invalid_end_time"
    async with get_session_factory()() as db:
        async with tenant_transaction(db, tenant):
            pro = (await db.execute(text("""
                SELECT professional_id FROM appointments
                WHERE tenant_id=:tenant AND id=:id
            """), {"tenant": tenant, "id": appointment})).scalar_one_or_none()
            if pro is None:
                return "not_found"
            # Same professional->appointment lock order as cancellation,
            # rescheduling and manual completion.
            await acquire_professional_lock(db, tenant, pro)
            row = (await db.execute(text("""
                SELECT id, professional_id, status, end_at
                FROM appointments WHERE tenant_id=:tenant AND id=:id FOR UPDATE
            """), {"tenant": tenant, "id": appointment})).mappings().one_or_none()
            if row is None or row["professional_id"] != pro:
                return "changed"
            if row["status"] != "confirmed":
                return "already_processed"
            if row["end_at"] != expected_time:
                return "rescheduled"
            now = datetime.now(timezone.utc)
            if row["end_at"] > now:
                return "not_finished"
            raw = (await db.execute(text("""
                SELECT preferences->'auto_completion'
                FROM management_preferences
                WHERE tenant_id=:tenant
            """), {"tenant": tenant})).scalar_one_or_none()
            if not policy_enabled(raw, row["end_at"], now):
                return "not_enabled"
            # Reuses the manual completion path: row CAS, audit, history,
            # earned commission snapshot, loyalty and one-way Google outbox.
            result = await transition_appointment(
                db, tenant, appointment, "completed",
                AppointmentTransition(idempotency_key=auto_completion_key(tenant, appointment),
                                      expected_status="confirmed"),
                "system",
            )
            value = int(result.get("total_cents") or 0)
            if value > 0:
                # A completed service is a receivable, not an automatically
                # paid Pix/cash transaction. Existing linked payments win.
                await db.execute(text("""
                    INSERT INTO financial_entries (
                      id,tenant_id,kind,description,amount_cents,category,
                      payment_method,status,entry_type,occurred_at,
                      customer_id,professional_id,appointment_id,notes
                    )
                    SELECT :id,:tenant,'income','Serviço concluído automaticamente',
                      :amount,'Serviços','other','pending','general',
                      :occurred_at,a.customer_id,a.professional_id,a.id,
                      'Recebimento pendente de confirmação. Não equivale a pagamento.'
                    FROM appointments a
                    WHERE a.tenant_id=:tenant AND a.id=:appointment
                      AND NOT EXISTS (
                        SELECT 1 FROM financial_entries f
                        WHERE f.tenant_id=a.tenant_id AND f.appointment_id=a.id
                          AND f.kind='income' AND f.status IN ('pending','paid')
                      )
                    ON CONFLICT (tenant_id,id) DO NOTHING
                """), {"id": receivable_id(tenant, appointment), "tenant": tenant,
                        "appointment": appointment, "amount": value,
                        "occurred_at": row["end_at"]})
            return "completed"


async def scan_auto_completions(ctx: dict):
    """Bounded recent-work scanner; only tenant+booking IDs leave the support role.

    Runtime validates opt-in per tenant. We never query the private management
    preferences through app_support or auto-complete long-ago appointments.
    """
    async with support_session_factory()() as db:
        rows = (await db.execute(text("""
            SELECT a.tenant_id,a.id,a.end_at FROM appointments a
            WHERE a.status='confirmed' AND a.end_at<=clock_timestamp()
              AND a.end_at >= clock_timestamp()-interval '24 hours'
            ORDER BY a.end_at DESC,a.id LIMIT :limit
        """), {"limit": SCAN_BATCH})).mappings().all()
    now = datetime.now(timezone.utc)
    queued = 0
    for row in rows:
        end = row["end_at"]
        # Minute-scoped ARQ IDs permit retry after a transient job failure;
        # DB CAS + professional lock prevents duplicate finalizations.
        job = await ctx["redis"].enqueue_job(
            "process_auto_completion", str(row["tenant_id"]), str(row["id"]), end.isoformat(),
            _job_id=f"completion:{row['id']}:{now.strftime('%Y%m%d%H%M')}",
        )
        queued += job is not None
    return queued
