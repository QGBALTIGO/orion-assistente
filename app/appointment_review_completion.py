"""One completion policy for review scheduling and the customer's review page.

Completion is trusted only when the appointment motor recorded its actor. An
automatic completion becomes reviewable after its scheduled end; it does not
claim that a member of staff verified attendance or establish return intervals.
"""
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text


STAFF_COMPLETION = """(h.details->>'source'='panel'
    AND h.details->'actor'->>'role' IN ('owner','editor','admin','support','professional'))"""
AUTOMATIC_COMPLETION = """(h.details->>'source'='system'
    AND h.details->'actor'->>'role'='system'
    AND h.details->>'requested_by'='system'
    AND h.details->'previous'->>'status'='confirmed'
    AND a.end_at<=clock_timestamp() AND h.occurred_at>=a.end_at)"""


@dataclass(frozen=True)
class CompletionEvidence:
    review_completed_at: datetime | None
    staff_completed_at: datetime | None

    @property
    def can_review(self):
        return self.review_completed_at is not None


async def completion_evidence(db, tenant, appointment):
    result = (await db.execute(text("""SELECT
        max(h.occurred_at) FILTER (WHERE """ + STAFF_COMPLETION + """) AS staff_completed_at,
        max(h.occurred_at) FILTER (WHERE """ + STAFF_COMPLETION + """ OR """ + AUTOMATIC_COMPLETION + """) AS review_completed_at
        FROM appointments a JOIN appointment_history h
          ON h.tenant_id=a.tenant_id AND h.appointment_id=a.id
        WHERE a.tenant_id=:t AND a.id=:id AND a.status='completed'
          AND a.start_at<=clock_timestamp() AND h.event='completed'
          AND h.details->>'v'='1' AND h.occurred_at<=clock_timestamp()"""),
        {'t': tenant, 'id': appointment})).mappings().one()
    return CompletionEvidence(**dict(result))
