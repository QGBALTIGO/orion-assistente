"""Automatic completion from durable appointment end times (UTC).

The scanner sees only opaque IDs through a least-privilege DB function.
Every mutation happens under the usual tenant RLS transaction and the same
professional locks, audit events, commission/loyalty calculation, and Google
outbox as a manual completion. It never assumes that the customer paid.

Off by default: only the worker process with ORION_AUTO_COMPLETE_APPOINTMENTS=1 may run.
The schema activation timestamp prevents historical backfills.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text

from app.catalog_api import support_session_factory
from app.database import get_session_factory, tenant_transaction
from app.management_models import transition_appointment


def enabled() -> bool:
    return os.getenv("ORION_AUTO_COMPLETE_APPOINTMENTS") == "1"


async def scan_due_auto_completions(ctx: dict) -> int:
    """Bounded scan; support role sees only tenant/booking UUIDs, never PII."""
    if not enabled():
        return 0
    async with support_session_factory()() as session:
        # Schema function owns the cross-tenant query. The support role has no
        # direct access to appointments and cannot mutate them.
        candidates = (await session.execute(text(
            "SELECT tenant_id,appointment_id "
            "FROM public.orion_due_auto_completions(:limit)"
        ), {"limit": 100})).mappings().all()
    slot = int(datetime.now(timezone.utc).timestamp() // 10)
    queued = 0
    for row in candidates:
        appointment_id = row["appointment_id"]
        job = await ctx["redis"].enqueue_job(
            "complete_due_appointment", str(row["tenant_id"]),
            str(appointment_id),
            _job_id=f"auto-complete:{appointment_id}:{slot}",
        )
        queued += job is not None
    return queued


async def complete_due_appointment(ctx: dict, tenant_id: str, appointment_id: str) -> str:
    """At-most-once financial/audit effect even if jobs are duplicated or retried."""
    if not enabled():
        return "disabled"
    tenant, identity = UUID(tenant_id), UUID(appointment_id)
    try:
        async with get_session_factory()() as session:
            async with tenant_transaction(session, tenant):
                row = (await session.execute(text("""
                    SELECT status,end_at,updated_at FROM appointments
                    WHERE tenant_id=:tenant AND id=:id
                """), {"tenant": tenant, "id": identity})).mappings().first()
                if row is None or row["status"] != "confirmed":
                    return "not_confirmed"
                variable = (await session.execute(text("""
                    SELECT EXISTS (SELECT 1 FROM appointment_items
                        WHERE tenant_id=:tenant AND appointment_id=:id
                          AND price_mode_snapshot='from')
                """), {"tenant": tenant, "id": identity})).scalar_one()
                if variable:
                    return "requires_actual_price"
                # Recheck against the tenant's durable activation boundary.
                allowed = (await session.execute(text("""
                    SELECT (t.status='active' AND a.end_at >= t.auto_complete_after
                            AND a.end_at<=clock_timestamp()) AS allowed
                    FROM appointments a JOIN tenants t ON t.id=a.tenant_id
                    WHERE a.tenant_id=:tenant AND a.id=:id
                """), {"tenant": tenant, "id": identity})).scalar_one_or_none()
                if not allowed:
                    return "not_due"
                payload = SimpleNamespace(
                    expected_status="confirmed", expected_updated_at=row["updated_at"],
                    coupon_id=None, final_total_cents=None,
                    idempotency_key="auto-completion-" + str(identity),
                )
                await transition_appointment(
                    session, tenant, identity, "completed", payload, "system", automated=True,
                )
                return "completed"
    except HTTPException as exc:
        # A manual no-show, cancellation or reschedule won the same lock.
        # They must never be undone by this worker's stale read.
        if exc.status_code in {404, 409}:
            return "changed_before_completion"
        raise
