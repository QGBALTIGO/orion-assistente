"""Performance by the tenant's local hour or day, with an adjacent comparison."""
from datetime import date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import text

from app import commercial_service as commerce
from app.delivery_payments import RECEIPT_LINKS
from app.management_models import report_period


Period = Literal["today", "yesterday", "7d", "30d", "90d", "custom"]
FIELDS = ("orders", "fulfilled", "cancelled", "sales_cents", "income_cents", "refunded_cents")


async def performance(db, tenant, *, preset: Period, start: date | None, end: date | None):
    timezone = await db.scalar(text("SELECT timezone FROM tenants WHERE id=:t"), {"t":tenant})
    zone = ZoneInfo(timezone)
    today = commerce.now_utc().astimezone(zone).date()
    if preset == "custom":
        if start is None or end is None:
            raise HTTPException(422,"Informe as duas datas para comparar períodos.")
    elif start is not None or end is not None:
        raise HTTPException(422,"Use o período personalizado para informar datas.")
    else:
        end = today - timedelta(days=int(preset == "yesterday"))
        days = {"7d":7, "30d":30, "90d":90}.get(preset,1)
        start = end - timedelta(days=days-1)
    _, lower, upper = report_period(start,end,timezone)
    days = (end-start).days+1
    previous = start-timedelta(days=days)
    hourly = days == 1
    rows = (await db.execute(text(f"""
        WITH links AS ({RECEIPT_LINKS}), events AS (
            SELECT o.created_at AS stamp,'order' AS event,0::bigint AS cents
            FROM commercial_orders o JOIN delivery_order_details d ON (d.tenant_id,d.order_id)=(o.tenant_id,o.id)
            WHERE o.tenant_id=:t AND o.origin='delivery' AND o.created_at>=:previous AND o.created_at<:upper
            UNION ALL
            SELECT e.created_at,e.action,q.total_cents
            FROM commercial_events e JOIN delivery_order_details d ON (d.tenant_id,d.order_id)=(e.tenant_id,e.entity_id)
            JOIN commercial_orders o ON (o.tenant_id,o.id)=(d.tenant_id,d.order_id)
            JOIN commercial_quotes q ON (q.tenant_id,q.id)=(o.tenant_id,o.quote_id)
            WHERE e.tenant_id=:t AND e.entity='order' AND e.action IN ('fulfilled','cancelled','returned')
                AND e.created_at>=:previous AND e.created_at<:upper
            UNION ALL
            SELECT coalesce(f.received_at,f.occurred_at),'income',f.amount_cents
            FROM financial_entries f JOIN links l ON (l.tenant_id,l.financial_entry_id)=(f.tenant_id,f.id)
            WHERE f.tenant_id=:t AND f.kind='income' AND f.status='paid'
                AND coalesce(f.received_at,f.occurred_at)>=:previous AND coalesce(f.received_at,f.occurred_at)<:upper
            UNION ALL
            SELECT f.occurred_at,'refund',f.amount_cents FROM financial_entries f JOIN delivery_order_details d
                ON (d.tenant_id,d.return_financial_entry_id)=(f.tenant_id,f.id)
            WHERE f.tenant_id=:t AND f.kind='expense' AND f.status='paid'
                AND f.occurred_at>=:previous AND f.occurred_at<:upper
        ) SELECT stamp>=:lower AS current_period, CAST(stamp AT TIME ZONE :zone AS date) AS day,
            CASE WHEN :hourly THEN extract(hour FROM stamp AT TIME ZONE :zone)::int ELSE 0 END AS hour,
            count(*) FILTER (WHERE event='order') AS orders,
            count(*) FILTER (WHERE event='fulfilled') AS fulfilled,
            count(*) FILTER (WHERE event IN ('cancelled','returned')) AS cancelled,
            coalesce(sum(cents) FILTER (WHERE event='fulfilled'),0) AS sales_cents,
            coalesce(sum(cents) FILTER (WHERE event='income'),0) AS income_cents,
            coalesce(sum(cents) FILTER (WHERE event='refund'),0) AS refunded_cents
        FROM events GROUP BY 1,2,3
    """), {"t":tenant,"zone":timezone,"hourly":hourly,"lower":lower,"upper":upper,
           "previous":datetime.combine(previous,time.min,zone)})).mappings().all()
    series = [dict.fromkeys(FIELDS,0) for _ in range(24 if hourly else days)]
    current, prior = dict.fromkeys(FIELDS,0), dict.fromkeys(FIELDS,0)
    for row in rows:
        totals = current if row["current_period"] else prior
        for key in FIELDS:
            amount = int(row[key])
            totals[key] += amount
            if row["current_period"]:
                series[row["hour"] if hourly else (row["day"]-start).days][key] += amount
    def with_ticket(totals):
        count = totals["fulfilled"]
        return {**totals, "average_ticket_cents": (totals["sales_cents"]+count//2)//count if count else None}

    points = [{**item, "date":start if hourly else start+timedelta(days=index), **({"hour":index} if hourly else {})}
              for index,item in enumerate(series)]
    return {"start":start,"end":end,"previous_start":previous,"previous_end":start-timedelta(days=1),
            "timezone":timezone,"granularity":"hour" if hourly else "day","current":with_ticket(current),"previous":with_ticket(prior),"series":points}
