"""Performance by the tenant's local hour or day, with an adjacent comparison."""
from datetime import date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import text

from app import commercial_service as commerce
from app.delivery_payments import RECEIPT_LINKS
from app.management_models import report_period


Period = Literal["today", "yesterday", "7d", "30d", "90d", "custom", "all"]
FIELDS = ("orders", "fulfilled", "cancelled", "sales_cents", "income_cents", "refunded_cents")


async def performance(db, tenant, *, preset: Period, start: date | None, end: date | None):
    timezone = await db.scalar(text("SELECT timezone FROM tenants WHERE id=:t"), {"t":tenant})
    zone = ZoneInfo(timezone)
    today = commerce.now_utc().astimezone(zone).date()
    all_time = preset == "all"
    if all_time:
        if start is not None or end is not None:
            raise HTTPException(422, "O histórico completo não aceita datas.")
        end = today
    elif preset == "custom":
        if start is None or end is None:
            raise HTTPException(422,"Informe as duas datas para comparar períodos.")
    elif start is not None or end is not None:
        raise HTTPException(422,"Use o período personalizado para informar datas.")
    else:
        end = today - timedelta(days=int(preset == "yesterday"))
        days = {"7d":7, "30d":30, "90d":90}.get(preset,1)
        start = end - timedelta(days=days-1)
    if all_time:
        # Unbounded historical lower limit; exclude future local dates, as in
        # the ordinary periods. Monthly buckets bound the chart payload.
        lower = datetime(1, 1, 1, tzinfo=ZoneInfo("UTC"))
        upper = datetime.combine(end+timedelta(days=1), time.min, zone)
        previous = None
        scan_from = lower
        hourly = False
    else:
        _, lower, upper = report_period(start,end,timezone)
        days = (end-start).days+1
        previous = start-timedelta(days=days)
        scan_from = datetime.combine(previous,time.min,zone)
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
        ) SELECT stamp>=:lower AS current_period,
            CASE WHEN :all_time THEN CAST(date_trunc('month', stamp AT TIME ZONE :zone) AS date)
                 ELSE CAST(stamp AT TIME ZONE :zone AS date) END AS day,
            CASE WHEN :hourly THEN extract(hour FROM stamp AT TIME ZONE :zone)::int ELSE 0 END AS hour,
            min(CAST(stamp AT TIME ZONE :zone AS date)) AS first_day,
            count(*) FILTER (WHERE event='order') AS orders,
            count(*) FILTER (WHERE event='fulfilled') AS fulfilled,
            count(*) FILTER (WHERE event IN ('cancelled','returned')) AS cancelled,
            coalesce(sum(cents) FILTER (WHERE event='fulfilled'),0) AS sales_cents,
            coalesce(sum(cents) FILTER (WHERE event='income'),0) AS income_cents,
            coalesce(sum(cents) FILTER (WHERE event='refund'),0) AS refunded_cents
        FROM events GROUP BY 1,2,3
    """), {"t":tenant,"zone":timezone,"hourly":hourly,"all_time":all_time,"lower":lower,"upper":upper,
           "previous":scan_from})).mappings().all()
    if all_time:
        start = min((row["first_day"] for row in rows), default=today)
        month_start = start.replace(day=1)
        days = (end.year-month_start.year)*12+end.month-month_start.month+1
        bucket_dates = [date(month_start.year+(month_start.month-1+i)//12,
                             (month_start.month-1+i)%12+1, 1) for i in range(days)]
        bucket_index = {day: index for index, day in enumerate(bucket_dates)}
    else:
        bucket_dates = [start if hourly else start+timedelta(days=i)
                        for i in range(24 if hourly else days)]
    series = [dict.fromkeys(FIELDS,0) for _ in bucket_dates]
    current, prior = dict.fromkeys(FIELDS,0), dict.fromkeys(FIELDS,0)
    for row in rows:
        totals = current if row["current_period"] else prior
        for key in FIELDS:
            amount = int(row[key])
            totals[key] += amount
            if row["current_period"]:
                index = bucket_index[row["day"]] if all_time else row["hour"] if hourly else (row["day"]-start).days
                series[index][key] += amount
    def with_ticket(totals):
        count = totals["fulfilled"]
        return {**totals, "average_ticket_cents": (totals["sales_cents"]+count//2)//count if count else None}

    points = [{**item, "date":bucket_dates[index], **({"hour":index} if hourly else {})}
              for index,item in enumerate(series)]
    # Status is the current state of orders created within the selected
    # cohort, not a subtraction of completion/cancellation events that
    # may belong to orders created in another period.
    cohort = (await db.execute(text("""
        SELECT o.status,extract(hour FROM o.created_at AT TIME ZONE :zone)::int AS hour,
            count(*) AS total
        FROM commercial_orders o
        JOIN delivery_order_details d ON (d.tenant_id,d.order_id)=(o.tenant_id,o.id)
        WHERE o.tenant_id=:t AND o.origin='delivery'
          AND o.created_at>=:lower AND o.created_at<:upper
        GROUP BY o.status,2
    """),{"t":tenant,"zone":timezone,"lower":lower,"upper":upper})).mappings().all()
    statuses = dict.fromkeys(("confirmed","dispatched","fulfilled","cancelled","other"),0)
    hourly_orders = [{"hour":hour,"orders":0} for hour in range(24)]
    for row in cohort:
        status = row["status"] if row["status"] in statuses else "other"
        statuses[status] += int(row["total"])
        hourly_orders[row["hour"]]["orders"] += int(row["total"])
    # Gross product sales use the immutable quote lines, and the completion
    # timestamp (same basis as gross revenue), not today's catalog prices.
    # EXISTS avoids multiplying a quote's items by historical event rows.
    products = (await db.execute(text("""
        SELECT CAST(i.variant_id AS text) AS id,
            max(i.snapshot->>'product_name') AS product,
            max(i.snapshot->>'variant_name') AS variant,
            sum(i.quantity) AS quantity,
            sum(CAST(i.snapshot->>'total_cents' AS bigint)) AS revenue_cents
        FROM commercial_orders o
        JOIN delivery_order_details d ON (d.tenant_id,d.order_id)=(o.tenant_id,o.id)
        JOIN commercial_quote_items i ON (i.tenant_id,i.quote_id)=(o.tenant_id,o.quote_id)
        WHERE o.tenant_id=:t AND o.origin='delivery'
          AND EXISTS(SELECT 1 FROM commercial_events e WHERE e.tenant_id=o.tenant_id
             AND e.entity='order' AND e.entity_id=o.id AND e.action='fulfilled'
             AND e.created_at>=:lower AND e.created_at<:upper)
        GROUP BY i.variant_id
        ORDER BY quantity DESC,revenue_cents DESC,i.variant_id LIMIT 5
    """),{"t":tenant,"lower":lower,"upper":upper})).mappings().all()
    return {"start":start,"end":end,"previous_start":previous,"previous_end":None if all_time else start-timedelta(days=1),
            "timezone":timezone,"granularity":"month" if all_time else "hour" if hourly else "day",
            "current":with_ticket(current),"previous":None if all_time else with_ticket(prior),"series":points,
            "order_status":statuses,"hourly_orders":hourly_orders,
            "top_products":[{"id":row["id"],"product":row["product"],"variant":row["variant"],
                "quantity":int(row["quantity"]),"revenue_cents":int(row["revenue_cents"])} for row in products]}
