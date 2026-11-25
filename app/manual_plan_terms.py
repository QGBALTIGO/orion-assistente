"""Calendar terms for a manually issued Orion subscription."""
from calendar import monthrange
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import text

from app.plan_catalog import CODES
from app.workspace_policy import STANDARD_WORKSPACE

MANUAL_MONTHS = (1, 3, 6, 12)


def calendar_end(anchor, duration, zone):
    local = anchor.astimezone(ZoneInfo(zone))
    year, month = divmod(local.year * 12 + local.month - 1 + duration, 12)
    month += 1
    return local.replace(year=year, month=month,
                         day=min(local.day, monthrange(year, month)[1])).astimezone(timezone.utc)


def term_in_zone(previous, plan_code, duration, now, zone):
    current = previous.get("manual_assignment") or {}
    if previous.get("scheduled_manual_assignment"):
        raise HTTPException(409, "Já existe uma troca agendada. Revise-a antes de confirmar uma nova vigência.")
    if previous.get("source") in {"cakto", "pix_prepaid"} and previous.get("active"):
        raise HTTPException(409, "Existe um período pago vigente. Confira a assinatura antes de ativar um período manual.")
    if previous.get("status") == "suspended":
        raise HTTPException(409, "Regularize a suspensão financeira antes de liberar um plano.")
    end = current.get("expires_at")
    if isinstance(end, str):
        end = datetime.fromisoformat(end.replace("Z", "+00:00"))
    start = now
    anchor = now
    kind = "activation"
    if current.get("active") and end and end > now:
        anchor = end
        if current["plan_code"] == plan_code:
            start = current["started_at"]
            if isinstance(start, str):
                start = datetime.fromisoformat(start.replace("Z", "+00:00"))
            kind = "renewal"
        elif CODES[plan_code]["max_professionals"] < CODES[current["plan_code"]]["max_professionals"]:
            start = end
            kind = "downgrade"
        else:
            kind = "upgrade"
    return {"starts_at": start, "added_period_start": anchor, "ends_at": calendar_end(anchor, duration, zone),
            "duration_months": duration, "timezone": zone, "kind": kind, "source": "manual"}


async def preview(db, tenant_id, previous, plan_code, duration):
    if previous["platform_workspace"] or previous["workspace_profile"] != STANDARD_WORKSPACE:
        raise HTTPException(409, "Este espaço ainda não tem um catálogo de assinaturas manuais disponível.")
    clock = (await db.execute(text("""
        SELECT timezone,clock_timestamp() AS now,
            EXISTS(SELECT 1 FROM orion_prepaid_pix_orders
                WHERE tenant_id=:tenant AND state IN ('reserved','created','needs_review'))
            OR EXISTS (SELECT 1 FROM orion_cakto_checkout_intents
                WHERE tenant_id=:tenant AND (state='needs_review' OR state='pending' AND expires_at>now()))
            AS payment_in_progress
        FROM tenants WHERE id=:tenant
    """), {"tenant": tenant_id})).mappings().one()
    if clock["payment_in_progress"]:
        raise HTTPException(409, "Confira a contratação que já está em andamento antes de liberar uma vigência manual.")
    return term_in_zone(previous, plan_code, duration, clock["now"], clock["timezone"])
