"""Read a professional's complete appointment history for a report period.

The caller supplies an authenticated tenant transaction. Reading this report
never expires holds, completes appointments or changes financial records.
"""
from datetime import date
from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text

from app.booking_service import read_appointments
from app.management_models import report_period


HistoryStatus = Literal["completed", "confirmed", "cancelled", "no_show", "hold", "expired_hold"]
HISTORY_STATUSES = ("completed", "confirmed", "cancelled", "no_show", "hold", "expired_hold")


async def read_professional_history(
    db, tenant_id: UUID, professional_id: UUID,
    start_date: date | None = None, end_date: date | None = None,
    limit: int = 50, offset: int = 0, status: HistoryStatus | None = None,
):
    if type(limit) is not int or not 1 <= limit <= 200:
        raise HTTPException(422, "Selecione entre 1 e 200 atendimentos por página")
    if type(offset) is not int or not 0 <= offset <= 2_147_483_647:
        raise HTTPException(422, "Posição de página inválida")
    if status is not None and (not isinstance(status, str) or status not in HISTORY_STATUSES):
        raise HTTPException(422, "Situação de atendimento inválida")

    professional = (await db.execute(text("""
        SELECT p.id,p.name,p.active,t.timezone
        FROM professionals p JOIN tenants t ON t.id=p.tenant_id
        WHERE p.tenant_id=:tenant AND p.id=:professional
    """), {"tenant": tenant_id, "professional": professional_id})).mappings().one_or_none()
    if professional is None:
        raise HTTPException(404, "Profissional não encontrado nesta empresa")
    period, lower, upper = report_period(start_date, end_date, professional["timezone"])
    parameters = {
        "tenant": tenant_id, "professional": professional_id,
        "lower": lower, "upper": upper, "limit": limit, "offset": offset,
    }
    status_filter = ""
    if status is not None:
        status_filter = " WHERE history_status=:status"
        parameters["status"] = status

    # Counts and page IDs share a statement and the same period snapshot. An
    # expired hold remains visible whether or not the expiry worker ran yet.
    page = (await db.execute(text("""
        WITH scoped AS MATERIALIZED (
            SELECT a.id,a.start_at,
                CASE WHEN a.status='hold' AND a.hold_expires_at<=now()
                       OR a.status='cancelled' AND cancellation.event='hold_expired'
                     THEN 'expired_hold' ELSE a.status END AS history_status
            FROM appointments a
            LEFT JOIN LATERAL (
                SELECT h.event FROM appointment_history h
                WHERE a.status='cancelled' AND h.tenant_id=a.tenant_id
                  AND h.appointment_id=a.id AND h.event IN ('cancelled','hold_expired')
                ORDER BY h.occurred_at DESC,h.id DESC LIMIT 1
            ) cancellation ON true
            WHERE a.tenant_id=:tenant AND a.professional_id=:professional
              AND a.start_at>=:lower AND a.start_at<:upper
        ), filtered AS MATERIALIZED (
            SELECT * FROM scoped
    """ + status_filter + """
        ), page AS MATERIALIZED (
            SELECT * FROM filtered ORDER BY start_at DESC,id DESC LIMIT :limit OFFSET :offset
        )
        SELECT (SELECT count(*) FROM filtered) AS total,
            ARRAY(SELECT id FROM page ORDER BY start_at DESC,id DESC) AS ids,
            (SELECT jsonb_object_agg(id::text,history_status) FROM page) AS states,
            (SELECT jsonb_object_agg(history_status,amount) FROM (
                SELECT history_status,count(*) AS amount FROM scoped GROUP BY history_status
            ) grouped) AS counts
    """), parameters)).mappings().one()

    items = await read_appointments(db, tenant_id, page["ids"])
    deleted_customers = set()
    if items:
        # The shared appointment reader serves the agenda too. Redact deleted
        # identities here without changing that API or making a query per row.
        deleted_customers = set((await db.execute(text("""
            SELECT id FROM customers WHERE tenant_id=:tenant AND deleted_at IS NOT NULL
              AND id=ANY(CAST(:ids AS uuid[]))
        """), {"tenant": tenant_id, "ids": list({item["customer_id"] for item in items})})).scalars().all())
    states = page["states"] or {}
    for item in items:
        item.pop("tenant_id", None)
        item["history_status"] = states[str(item["id"])]
        item["customer_deleted"] = item["customer_id"] in deleted_customers
        if item["customer_deleted"]:
            item.update(customer_name=None, customer_display_name=None, customer_phone=None)
        item["allowed_actions"] = []

    counts = {key: int((page["counts"] or {}).get(key, 0)) for key in HISTORY_STATUSES}
    counts["all"] = sum(counts.values())
    return {
        "professional": {key: professional[key] for key in ("id", "name", "active")},
        "period": period, "items": items, "total": page["total"],
        "limit": limit, "offset": offset, "has_more": offset + len(items) < page["total"],
        "counts": counts,
    }
