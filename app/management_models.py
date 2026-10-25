"""Tenant-scoped SQL operations for the management panel.

Callers must enter tenant_transaction after resolving account membership.
No function sends messages, creates charges, or contacts an external service.
"""
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
import re
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy import text

from app.catalog_api import audit
from app.management_schemas import MAX_REPORT_DAYS, SitePreferences
from app.customer_names import display_customer

WRITE_FIELDS = {
    "customers": frozenset({"name", "whatsapp_e164", "instagram", "neighborhood", "city", "email", "birth_date", "birth_month", "birth_day", "notes", "active", "deleted_at"}),
    "financial_entries": frozenset({"kind", "description", "amount_cents", "category", "payment_method", "status", "entry_type", "occurred_at", "customer_id", "professional_id", "appointment_id", "notes"}),
    "loyalty_rules": frozenset({"name", "earning_mode", "points_per_visit", "spend_step_cents", "points_per_step", "reward_points", "reward_description", "active",
                              "reward_type", "reward_percent", "reward_value_cents", "reward_service_id", "reward_valid_days"}),
    "loyalty_transactions": frozenset({"customer_id", "rule_id", "appointment_id", "points_delta", "reason"}),
    "professional_goals": frozenset({"professional_id", "metric", "start_date", "end_date", "target_value", "active"}),
    "campaigns": frozenset({"name", "message", "audience", "scheduled_at", "template_name", "status"}),
}


def checked_fields(table, values):
    if table not in WRITE_FIELDS or not values or not set(values) <= WRITE_FIELDS[table]:
        raise ValueError("Unsupported internal management columns")


def clean_row(row):
    if row is None:
        return None
    data = dict(row)
    data.pop("tenant_id", None)
    if "whatsapp_e164" in data:
        data["phone"] = data.pop("whatsapp_e164")
        data = display_customer(data)
    return data


def report_period(start_date: date | None, end_date: date | None, tz: str, now=None):
    zone = ZoneInfo(tz)
    today = (now or datetime.now(timezone.utc)).astimezone(zone).date()
    end = end_date or today
    start = start_date or end.replace(day=1)
    if not 0 <= (end - start).days < MAX_REPORT_DAYS or start.year < 1900 or end.year > 2100:
        raise HTTPException(422, "Selecione um período de até 366 dias, com início anterior ou igual ao fim")
    lower = datetime.combine(start, time.min, zone).astimezone(timezone.utc)
    upper = datetime.combine(end + timedelta(days=1), time.min, zone).astimezone(timezone.utc)
    return {"start_date": start, "end_date": end, "timezone": tz}, lower, upper


async def period_for(db, tenant_id, start_date=None, end_date=None):
    tz = (await db.execute(text("SELECT timezone FROM tenants WHERE id=:tenant"), {"tenant": tenant_id})).scalar_one_or_none()
    if tz is None:
        raise HTTPException(404, "Empresa não encontrada")
    return report_period(start_date, end_date, tz)


async def request_identity(db, tenant_id, operation, key, payload, *, entity_id=None):
    fingerprint = hashlib.sha256(json.dumps(jsonable_encoder(payload), ensure_ascii=False, sort_keys=True,
                                           separators=(",", ":")).encode()).hexdigest()
    identity = entity_id or uuid4()
    inserted = (await db.execute(text("""
        INSERT INTO management_requests (tenant_id,operation,idempotency_key,fingerprint,entity_id)
        VALUES (:tenant,:operation,:key,:fingerprint,:id)
        ON CONFLICT (tenant_id,operation,idempotency_key) DO NOTHING RETURNING entity_id
    """), {"tenant": tenant_id, "operation": operation, "key": key, "fingerprint": fingerprint, "id": identity})).scalar_one_or_none()
    if inserted is not None:
        return identity, False
    existing = (await db.execute(text("""
        SELECT fingerprint,entity_id FROM management_requests
        WHERE tenant_id=:tenant AND operation=:operation AND idempotency_key=:key
    """), {"tenant": tenant_id, "operation": operation, "key": key})).mappings().one()
    if existing["fingerprint"] != fingerprint:
        raise HTTPException(409, "Esta chave de operação já foi utilizada com outros dados")
    return existing["entity_id"], True


async def get_row(db, tenant_id, table, identity, *, lock=False):
    if table not in WRITE_FIELDS:
        raise ValueError("Unknown internal management table")
    result = (await db.execute(text(f"SELECT * FROM {table} WHERE tenant_id=:tenant AND id=:id" + (" FOR UPDATE" if lock else "")),
                               {"tenant": tenant_id, "id": identity})).mappings().one_or_none()
    if result is None:
        raise HTTPException(404, "Registro não encontrado nesta empresa")
    return dict(result)


async def insert_row(db, tenant_id, table, values, key, operation=None):
    checked_fields(table, values)
    if table == "financial_entries":
        from app.financial_settlements import lock_professionals
        await lock_professionals(db, tenant_id, [values.get("professional_id")])
    identity, replay = await request_identity(db, tenant_id, operation or table, key, values)
    if replay:
        return await get_row(db, tenant_id, table, identity)
    fields = list(values)
    result = (await db.execute(text(f"INSERT INTO {table} (tenant_id,id,{','.join(fields)}) "
        f"VALUES (:tenant,:id,{','.join(':'+field for field in fields)}) RETURNING *"),
        {"tenant": tenant_id, "id": identity, **values})).mappings().one()
    await audit(db, tenant_id, "management_created", table, identity)
    return dict(result)


async def update_row(db, tenant_id, table, identity, version, values):
    checked_fields(table, values)
    if table == "loyalty_transactions":
        raise ValueError("Point history is append-only")
    if table == "financial_entries":
        from app.financial_settlements import ensure_editable_entry, lock_professionals
        previous = await get_row(db, tenant_id, table, identity)
        await lock_professionals(db, tenant_id, [previous.get("professional_id"), values.get("professional_id")])
        await get_row(db, tenant_id, table, identity, lock=True)
        await ensure_editable_entry(db, tenant_id, identity)
    assignments = ",".join(f"{column}=:{column}" for column in values)
    row = (await db.execute(text(f"""
        UPDATE {table} SET {assignments},version=version+1,updated_at=now()
        WHERE tenant_id=:tenant AND id=:id AND version=:expected RETURNING *
    """), {"tenant": tenant_id, "id": identity, "expected": version, **values})).mappings().one_or_none()
    if row is None:
        await get_row(db, tenant_id, table, identity)
        raise HTTPException(409, "O registro foi alterado. Atualize os dados antes de salvar novamente")
    await audit(db, tenant_id, "management_updated", table, identity)
    return dict(row)


async def validate_links(db, tenant_id, values):
    for key, table in (("customer_id", "customers"), ("professional_id", "professionals"), ("rule_id", "loyalty_rules")):
        if values.get(key) is not None:
            found = (await db.execute(text(f"SELECT id FROM {table} WHERE tenant_id=:tenant AND id=:id"),
                {"tenant": tenant_id, "id": values[key]})).scalar_one_or_none()
            if found is None:
                raise HTTPException(404, "O vínculo informado não pertence a esta empresa")
    if values.get("appointment_id") is not None:
        appointment = (await db.execute(text("""
            SELECT customer_id,professional_id FROM appointments WHERE tenant_id=:tenant AND id=:id
        """), {"tenant": tenant_id, "id": values["appointment_id"]})).mappings().one_or_none()
        if appointment is None:
            raise HTTPException(404, "Agendamento não encontrado nesta empresa")
        for key in ("customer_id", "professional_id"):
            if values.get(key) is not None and values[key] != appointment[key]:
                raise HTTPException(422, "Cliente ou profissional não corresponde ao agendamento")


CUSTOMER_SELECT = """
    SELECT c.*,
       (SELECT count(*) FROM appointments a WHERE a.tenant_id=c.tenant_id AND a.customer_id=c.id
          AND (a.status <> 'hold' OR a.hold_expires_at > now())) AS total_appointments,
       (SELECT max(a.start_at) FROM appointments a WHERE a.tenant_id=c.tenant_id AND a.customer_id=c.id
          AND a.status='completed') AS last_visit_at,
       (SELECT COALESCE(sum(a.total_cents),0) FROM appointments a WHERE a.tenant_id=c.tenant_id
          AND a.customer_id=c.id AND a.status='completed') AS total_spent_cents,
       (SELECT COALESCE(sum(l.points_delta),0) FROM loyalty_transactions l
          WHERE l.tenant_id=c.tenant_id AND l.customer_id=c.id) AS loyalty_points
    FROM customers c
"""


def customer_filter(tenant_id, q="", status="all", birthday_month=None, risk_days=30, name_phones=None):
    if risk_days not in (30, 60, 90):
        raise HTTPException(422, "O filtro de risco permite 30, 60 ou 90 dias")
    clauses = ["c.tenant_id=:tenant", "c.deleted_at IS NULL"]
    parameters = {"tenant": tenant_id}
    if q:
        # Treat SQL wildcard characters as literal search text.
        escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        search = "c.name ILIKE :search OR c.whatsapp_e164 ILIKE :search OR c.email ILIKE :search"
        if re.fullmatch(r"[+0-9\s().-]+", q):
            digits = re.sub(r"[^0-9]", "", q)
            if digits:
                search += " OR c.whatsapp_e164 ILIKE :phone_search"
                parameters["phone_search"] = "%" + digits + "%"
        if name_phones:
            search += " OR c.whatsapp_e164=ANY(CAST(:name_phones AS text[]))"
            parameters["name_phones"] = name_phones
        clauses.append("(" + search + ")")
        parameters["search"] = "%" + escaped + "%"
    if status in ("active", "inactive"):
        clauses.append("c.active=:active")
        parameters["active"] = status == "active"
    elif status == "at_risk":
        clauses.append("""c.active AND (SELECT max(a.start_at) FROM appointments a
            WHERE a.tenant_id=c.tenant_id AND a.customer_id=c.id AND a.status='completed')
            < now() - CAST(:risk_days AS integer) * interval '1 day'
            AND NOT EXISTS (SELECT 1 FROM appointments future WHERE future.tenant_id=c.tenant_id
                AND future.customer_id=c.id AND future.start_at>now()
                AND (future.status='confirmed' OR (future.status='hold' AND future.hold_expires_at>now())))""")
        parameters["risk_days"] = risk_days
    elif status == "birthday" and birthday_month is None:
        clauses.append("""coalesce(lpad(c.birth_month::text,2,'0')||'-'||lpad(c.birth_day::text,2,'0'),to_char(c.birth_date,'MM-DD')) IN (
            SELECT to_char(date(now() AT TIME ZONE t.timezone)
                - EXTRACT(DOW FROM now() AT TIME ZONE t.timezone)::integer + days.day_offset,'MM-DD')
            FROM tenants t CROSS JOIN generate_series(0,6) AS days(day_offset) WHERE t.id=:tenant)""")
    if birthday_month is not None:
        clauses.append("coalesce(c.birth_month,EXTRACT(MONTH FROM c.birth_date))=:birthday_month")
        parameters["birthday_month"] = birthday_month
    return " AND ".join(clauses), parameters


async def customers(db, tenant_id, q="", status="all", birthday_month=None, limit=50, offset=0, risk_days=30, name_phones=None):
    where, parameters = customer_filter(tenant_id, q, status, birthday_month, risk_days, name_phones)
    total = (await db.execute(text("SELECT count(*) FROM customers c WHERE " + where), parameters)).scalar_one()
    rows = (await db.execute(text(CUSTOMER_SELECT + " WHERE " + where + " ORDER BY c.name,c.id LIMIT :limit OFFSET :offset"),
                            {**parameters, "limit": limit, "offset": offset})).mappings().all()
    return {"items": [clean_row(row) for row in rows], "total": total, "limit": limit, "offset": offset}


async def customer_detail(db, tenant_id, customer_id):
    row = (await db.execute(text(CUSTOMER_SELECT + " WHERE c.tenant_id=:tenant AND c.id=:id AND c.deleted_at IS NULL"),
        {"tenant": tenant_id, "id": customer_id})).mappings().one_or_none()
    if row is None:
        raise HTTPException(404, "Cliente não encontrado")
    return clean_row(row)


def finance_filter(tenant_id, lower, upper, kind=None, status=None, category=None, payment_method=None, entry_type=None, professional_id=None):
    clauses = ["e.tenant_id=:tenant", "e.occurred_at>=:lower", "e.occurred_at<:upper"]
    parameters = {"tenant": tenant_id, "lower": lower, "upper": upper}
    for name, value in (("kind", kind), ("status", status), ("category", category), ("payment_method", payment_method),
                        ("entry_type", entry_type), ("professional_id", professional_id)):
        if value is not None:
            clauses.append(f"e.{name}=:{name}")
            parameters[name] = value
    return " AND ".join(clauses), parameters


FINANCE_TOTALS = """
    count(*) AS count,
    COALESCE(sum(amount_cents) FILTER (WHERE kind='income' AND status='paid'),0) AS income_cents,
    COALESCE(sum(amount_cents) FILTER (WHERE kind='expense' AND status='paid'),0) AS expense_cents,
    COALESCE(sum(amount_cents) FILTER (WHERE kind='income' AND status='pending'),0) AS pending_income_cents,
    COALESCE(sum(amount_cents) FILTER (WHERE kind='expense' AND status='pending'),0) AS pending_expense_cents
"""


async def finance_summary(db, where, parameters, tz):
    result = dict((await db.execute(text("SELECT " + FINANCE_TOTALS + " FROM financial_entries e WHERE " + where), parameters)).mappings().one())
    for key in result:
        result[key] = int(result[key])
    result["balance_cents"] = result["income_cents"] - result["expense_cents"]
    ticket_scope = " AND professional_id=:professional_id" if parameters.get("professional_id") else ""
    ticket = (await db.execute(text("""
        SELECT count(*) AS completed,COALESCE(sum(total_cents),0) AS revenue_cents FROM appointments
        WHERE tenant_id=:tenant AND status='completed' AND start_at>=:lower AND start_at<:upper
    """ + ticket_scope), parameters)).mappings().one()
    result["completed"] = ticket["completed"]
    result["average_ticket_cents"] = int(ticket["revenue_cents"]) // ticket["completed"] if ticket["completed"] else 0
    result["ticket_basis"] = "completed_appointments_in_period"
    for column, target in (("category", "by_category"), ("payment_method", "by_payment_method")):
        result[target] = [dict(row) for row in (await db.execute(text(f"SELECT {column}," + FINANCE_TOTALS +
            " FROM financial_entries e WHERE " + where + f" GROUP BY {column} ORDER BY {column}"), parameters)).mappings().all()]
    result["daily"] = [dict(row) for row in (await db.execute(text("SELECT date(occurred_at AT TIME ZONE :tz) AS date," + FINANCE_TOTALS +
        " FROM financial_entries e WHERE " + where + " GROUP BY date(occurred_at AT TIME ZONE :tz) ORDER BY date"),
        {**parameters, "tz": tz})).mappings().all()]
    return result


async def finance(db, tenant_id, start_date=None, end_date=None, kind=None, status=None, category=None, payment_method=None, limit=50, offset=0, entry_type=None, professional_id=None):
    period, lower, upper = await period_for(db, tenant_id, start_date, end_date)
    where, parameters = finance_filter(tenant_id, lower, upper, kind, status, category, payment_method, entry_type, professional_id)
    summary = await finance_summary(db, where, parameters, period["timezone"])
    rows = (await db.execute(text("""
        SELECT e.*,c.name AS customer_name,p.name AS professional_name
        FROM financial_entries e
        LEFT JOIN customers c ON c.tenant_id=e.tenant_id AND c.id=e.customer_id
        LEFT JOIN professionals p ON p.tenant_id=e.tenant_id AND p.id=e.professional_id
    """ + " WHERE " + where + " ORDER BY e.occurred_at DESC,e.id LIMIT :limit OFFSET :offset"),
        {**parameters, "limit": limit, "offset": offset})).mappings().all()
    return {"items": [clean_row(row) for row in rows], "total": summary["count"], "limit": limit, "offset": offset,
            "summary": summary, "period": period}


APPOINTMENT_RANGE = "a.tenant_id=:tenant AND a.start_at>=:lower AND a.start_at<:upper AND (a.status <> 'hold' OR a.hold_expires_at>now())"


async def dashboard(db, tenant_id, start_date=None, end_date=None):
    period, lower, upper = await period_for(db, tenant_id, start_date, end_date)
    params = {"tenant": tenant_id, "lower": lower, "upper": upper, "tz": period["timezone"]}
    status_rows = (await db.execute(text("SELECT a.status,count(*) AS count FROM appointments a WHERE " +
        APPOINTMENT_RANGE + " GROUP BY a.status"), params)).mappings().all()
    counts = {status: 0 for status in ("hold", "confirmed", "completed", "cancelled", "no_show")}
    counts.update({row["status"]: row["count"] for row in status_rows})
    revenue = (await db.execute(text("""
        SELECT COALESCE(sum(a.total_cents) FILTER (WHERE a.status IN ('confirmed','completed')),0) AS projected,
               COALESCE(sum(a.total_cents) FILTER (WHERE a.status='completed'),0) AS completed
        FROM appointments a WHERE """ + APPOINTMENT_RANGE), params)).mappings().one()
    customer_counts = (await db.execute(text("""
        SELECT count(*) FILTER (WHERE active) AS customers,
               count(*) FILTER (WHERE created_at>=:lower AND created_at<:upper) AS new_customers
        FROM customers WHERE tenant_id=:tenant AND deleted_at IS NULL
    """), params)).mappings().one()
    # These milestones describe stored facts across the business's lifetime.
    # A cancelled appointment still proves a first confirmation when its audit
    # event exists; a transient hold does not. Configuration is not a promise
    # that a particular future slot is available or that a public site is live.
    onboarding = (await db.execute(text("""
        SELECT (
            EXISTS (SELECT 1 FROM appointments WHERE tenant_id=:tenant
                AND status IN ('confirmed','completed','no_show'))
            OR EXISTS (SELECT 1 FROM appointment_history WHERE tenant_id=:tenant AND event='confirmed')
        ) AS has_appointment,
        EXISTS (
            SELECT 1 FROM services s
            JOIN professional_services ps ON ps.tenant_id=s.tenant_id AND ps.service_id=s.id
            JOIN professionals p ON p.tenant_id=ps.tenant_id AND p.id=ps.professional_id
            JOIN working_hours w ON w.tenant_id=p.tenant_id AND w.professional_id=p.id
            WHERE s.tenant_id=:tenant AND s.active AND s.catalog_kind='base'
                AND p.active AND p.accepts_appointments AND w.start_time<w.end_time
        ) AS scheduling_ready,
        (SELECT count(*) FROM customers WHERE tenant_id=:tenant AND active
            AND deleted_at IS NULL AND whatsapp_e164 IS NOT NULL) AS customers_with_phone
    """), {"tenant": tenant_id})).mappings().one()
    service_rows = (await db.execute(text("""
        WITH item_values AS (
            SELECT i.tenant_id,i.service_id,i.appointment_id,i.item_order,i.name_snapshot,i.price_snapshot_cents,a.status,a.total_cents,
                COALESCE(floor(i.price_snapshot_cents::numeric*a.total_cents/NULLIF(COALESCE(a.original_total_cents,a.total_cents),0)),0) AS net_floor
            FROM appointment_items i JOIN appointments a ON a.tenant_id=i.tenant_id AND a.id=i.appointment_id
            WHERE """ + APPOINTMENT_RANGE + """ AND a.status IN ('confirmed','completed')
        ), distributed AS (
            SELECT *,CASE WHEN row_number() OVER (PARTITION BY appointment_id ORDER BY item_order DESC)=1 THEN
                total_cents-COALESCE(sum(net_floor) OVER (PARTITION BY appointment_id ORDER BY item_order ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING),0)
                ELSE net_floor END AS net_value FROM item_values
        )
        SELECT d.service_id AS id,COALESCE(s.name,max(d.name_snapshot)) AS name,count(DISTINCT d.appointment_id) AS bookings,
            COALESCE(sum(d.net_value) FILTER (WHERE d.status='completed'),0) AS revenue_cents,
            COALESCE(sum(d.price_snapshot_cents) FILTER (WHERE d.status='completed'),0) AS gross_revenue_cents,
            COALESCE(sum(d.price_snapshot_cents-d.net_value) FILTER (WHERE d.status='completed'),0) AS discount_cents
        FROM distributed d LEFT JOIN services s ON s.tenant_id=d.tenant_id AND s.id=d.service_id
        GROUP BY d.service_id,s.name ORDER BY bookings DESC,name LIMIT 20
    """), params)).mappings().all()
    day_rows = (await db.execute(text("""
        SELECT date(a.start_at AT TIME ZONE :tz) AS date,count(*) AS appointments,
               COALESCE(sum(a.total_cents) FILTER (WHERE a.status='completed'),0) AS revenue_cents
        FROM appointments a WHERE """ + APPOINTMENT_RANGE + " GROUP BY date(a.start_at AT TIME ZONE :tz) ORDER BY date"), params)).mappings().all()
    days = {row["date"]: dict(row) for row in day_rows}
    day = period["start_date"]
    daily = []
    while day <= period["end_date"]:
        daily.append(days.get(day, {"date": day, "appointments": 0, "revenue_cents": 0}))
        day += timedelta(days=1)
    staff = await performance(db, tenant_id, start_date, end_date)
    summary = {"appointments": sum(counts.values()), **{key: counts[key] for key in ("confirmed", "completed", "cancelled", "no_show")},
        "projected_revenue_cents": int(revenue["projected"]), "completed_revenue_cents": int(revenue["completed"]),
        "average_ticket_cents": int(revenue["completed"]) // counts["completed"] if counts["completed"] else 0,
        **dict(customer_counts)}
    return {"period": period, "summary": summary, "appointments_by_status": [{"status": key, "count": value} for key, value in counts.items()],
        "services": [dict(row) for row in service_rows], "daily": daily, "professionals": staff["items"],
        "onboarding": dict(onboarding)}


async def loyalty(db, tenant_id):
    rules = (await db.execute(text("SELECT * FROM loyalty_rules WHERE tenant_id=:tenant ORDER BY active DESC,name,id"),
                              {"tenant": tenant_id})).mappings().all()
    summary = dict((await db.execute(text("""
        SELECT COALESCE(sum(points_delta) FILTER (WHERE points_delta>0),0) AS points_issued,
               COALESCE(-sum(points_delta) FILTER (WHERE points_delta<0),0) AS points_redeemed,
               COALESCE(sum(points_delta),0) AS points_balance,count(DISTINCT customer_id) AS customers
        FROM loyalty_transactions WHERE tenant_id=:tenant
    """), {"tenant": tenant_id})).mappings().one())
    coupons = dict((await db.execute(text("""
        SELECT count(*) AS coupons_generated,count(*) FILTER (WHERE status='redeemed') AS coupons_used,
            count(*) FILTER (WHERE status='available' AND expires_at>now()) AS coupons_available
        FROM loyalty_coupons WHERE tenant_id=:tenant
    """), {"tenant": tenant_id})).mappings().one())
    summary.update(coupons)
    summary["redemption_rate_percent"] = round(coupons["coupons_used"] * 100 / coupons["coupons_generated"], 2) if coupons["coupons_generated"] else 0
    return {"rules": [clean_row(row) for row in rules], "summary": summary,
            "automatic_accrual_enabled": any(row["active"] for row in rules),
            "automatic_coupon_issuance_enabled": any(row["active"] and row["reward_type"] != "manual" for row in rules),
            "max_automatic_coupons_per_appointment": 1, "excess_points_preserved": True}


async def point_history(db, tenant_id, customer_id=None, limit=50, offset=0):
    where = "l.tenant_id=:tenant" + (" AND l.customer_id=:customer" if customer_id else "")
    parameters = {"tenant": tenant_id, "customer": customer_id}
    total = (await db.execute(text("SELECT count(*) FROM loyalty_transactions l WHERE " + where), parameters)).scalar_one()
    rows = (await db.execute(text("""
        SELECT l.*,c.name AS customer_name,r.name AS rule_name FROM loyalty_transactions l
        JOIN customers c ON c.tenant_id=l.tenant_id AND c.id=l.customer_id
        LEFT JOIN loyalty_rules r ON r.tenant_id=l.tenant_id AND r.id=l.rule_id
        WHERE """ + where + " ORDER BY l.created_at DESC,l.id LIMIT :limit OFFSET :offset"),
        {**parameters, "limit": limit, "offset": offset})).mappings().all()
    return {"items": [clean_row(row) for row in rows], "total": total, "limit": limit, "offset": offset}


async def record_points(db, tenant_id, values, key):
    await validate_links(db, tenant_id, values)
    # Serialize credits and redemptions for one customer across concurrent workers.
    customer = await get_row(db, tenant_id, "customers", values["customer_id"], lock=True)
    if customer.get("deleted_at") is not None:
        raise HTTPException(404, "Cliente não encontrado no diretório")
    identity, replay = await request_identity(db, tenant_id, "loyalty_transactions", key, values)
    balance = (await db.execute(text("SELECT COALESCE(sum(points_delta),0) FROM loyalty_transactions WHERE tenant_id=:tenant AND customer_id=:customer"),
                              {"tenant": tenant_id, "customer": values["customer_id"]})).scalar_one()
    if replay:
        return {**await get_row(db, tenant_id, "loyalty_transactions", identity), "balance": balance}
    if balance + values["points_delta"] < 0:
        raise HTTPException(409, "O cliente não possui pontos suficientes para este resgate")
    fields = list(values)
    checked_fields("loyalty_transactions", values)
    row = (await db.execute(text("INSERT INTO loyalty_transactions (tenant_id,id," + ",".join(fields) + ") VALUES (:tenant,:id," +
        ",".join(":"+field for field in fields) + ") RETURNING *"), {"tenant": tenant_id, "id": identity, **values})).mappings().one()
    await audit(db, tenant_id, "points_recorded", "loyalty_transactions", identity)
    return {**dict(row), "balance": balance + values["points_delta"]}


async def goals(db, tenant_id, start_date=None, end_date=None):
    period, _, _ = await period_for(db, tenant_id, start_date, end_date)
    rows = (await db.execute(text("""
        SELECT g.*,p.name AS professional_name,
          (SELECT CASE WHEN g.metric='appointments' THEN count(*) ELSE COALESCE(sum(a.total_cents),0) END
           FROM appointments a WHERE a.tenant_id=g.tenant_id AND a.professional_id=g.professional_id
           AND a.status='completed'
           AND a.start_at >= (g.start_date::timestamp AT TIME ZONE :tz)
           AND a.start_at < ((g.end_date+1)::timestamp AT TIME ZONE :tz)) AS actual_value
        FROM professional_goals g JOIN professionals p ON p.tenant_id=g.tenant_id AND p.id=g.professional_id
        WHERE g.tenant_id=:tenant AND g.end_date>=:start_date AND g.start_date<=:end_date
        ORDER BY g.active DESC,g.start_date DESC,g.id
    """), {"tenant": tenant_id, "start_date": period["start_date"], "end_date": period["end_date"], "tz": period["timezone"]})).mappings().all()
    items = []
    for row in rows:
        item = clean_row(row)
        item["actual_value"] = int(item["actual_value"])
        item["progress_percent"] = round(item["actual_value"] * 100 / item["target_value"], 2)
        items.append(item)
    return {"items": items, "period": period}


async def performance(db, tenant_id, start_date=None, end_date=None):
    period, lower, upper = await period_for(db, tenant_id, start_date, end_date)
    rows = (await db.execute(text("""
        SELECT p.id,p.name,p.active,count(a.id) AS appointments,
          count(a.id) FILTER (WHERE a.status='completed') AS completed,
          count(a.id) FILTER (WHERE a.status='cancelled') AS cancelled,
          count(a.id) FILTER (WHERE a.status='no_show') AS no_show,
          COALESCE(sum(a.total_cents) FILTER (WHERE a.status='completed'),0) AS revenue_cents
        FROM professionals p LEFT JOIN appointments a ON a.tenant_id=p.tenant_id AND a.professional_id=p.id
          AND a.start_at>=:lower AND a.start_at<:upper AND (a.status <> 'hold' OR a.hold_expires_at>now())
        WHERE p.tenant_id=:tenant GROUP BY p.id,p.name,p.active ORDER BY revenue_cents DESC,p.name
    """), {"tenant": tenant_id, "lower": lower, "upper": upper})).mappings().all()
    targets = (await goals(db, tenant_id, start_date, end_date))["items"]
    items = []
    for row in rows:
        item = dict(row)
        item["revenue_cents"] = int(item["revenue_cents"])
        item["average_ticket_cents"] = item["revenue_cents"] // item["completed"] if item["completed"] else 0
        item["goals"] = [goal for goal in targets if goal["professional_id"] == item["id"]]
        items.append(item)
    return {"period": period, "items": items}


async def site(db, tenant_id):
    # This local import avoids the public router's management-session dependency
    # forming an import cycle. Both endpoints use the same approved whitelist.
    from app.public_site_api import PUBLIC_FIELDS
    row = (await db.execute(text("SELECT * FROM management_site_preferences WHERE tenant_id=:tenant"),
                           {"tenant": tenant_id})).mappings().one_or_none()
    result = clean_row(row) if row else {**SitePreferences().model_dump(), "version": 0}
    snapshot = result.pop("public_snapshot", None)
    published = bool(result.get("public_enabled", False))
    draft = {field: result.get(field) for field in PUBLIC_FIELDS}
    return {**result, "published": published, "public_enabled": published,
            "public_url": f"/s/{result['public_slug']}" if published and result.get("public_slug") else None,
            "preview_url": f"/app/site-preview/{tenant_id}",
            "has_unpublished_changes": bool(row and draft != snapshot)}


async def save_site(db, tenant_id, values, expected_version):
    allowed = set(SitePreferences.model_fields)
    if set(values) != allowed:
        raise ValueError("Incomplete site preference values")
    columns = list(values)
    if expected_version == 0:
        row = (await db.execute(text("INSERT INTO management_site_preferences (tenant_id," + ",".join(columns) +
            ") VALUES (:tenant," + ",".join(":"+name for name in columns) + ") ON CONFLICT (tenant_id) DO NOTHING RETURNING *"),
            {"tenant": tenant_id, **values})).mappings().one_or_none()
    else:
        row = (await db.execute(text("UPDATE management_site_preferences SET " + ",".join(name+"=:"+name for name in columns) +
            ",version=version+1,updated_at=now() WHERE tenant_id=:tenant AND version=:expected RETURNING *"),
            {"tenant": tenant_id, "expected": expected_version, **values})).mappings().one_or_none()
    if row is None:
        raise HTTPException(409, "As preferências foram alteradas. Atualize antes de salvar")
    await audit(db, tenant_id, "site_preferences_saved", "management_site_preferences", tenant_id)
    return await site(db, tenant_id)


async def marketing(db, tenant_id, limit=50, offset=0):
    total = (await db.execute(text("SELECT count(*) FROM campaigns WHERE tenant_id=:tenant"), {"tenant": tenant_id})).scalar_one()
    rows = (await db.execute(text("SELECT * FROM campaigns WHERE tenant_id=:tenant ORDER BY created_at DESC,id LIMIT :limit OFFSET :offset"),
                            {"tenant": tenant_id, "limit": limit, "offset": offset})).mappings().all()
    from app.automation_runtime import execution_enabled, execution_approved, settings_for
    active = execution_enabled() and execution_approved(await settings_for(db, tenant_id))
    return {"items": [clean_row(row) for row in rows], "total": total, "limit": limit, "offset": offset, "delivery_enabled": active}


async def transition_appointment(db, tenant_id, appointment_id, target, payload, operator, *, automated=False):
    from app.booking_service import acquire_professional_lock, history, read_appointment
    from app.appointment_lifecycle import CAS_SQL, LifecycleActor, allowed_actions, check_expected_updated_at, operation_payload
    if target not in ("completed", "no_show"):
        raise ValueError("Unsupported management appointment transition")
    if target == "no_show" and payload.coupon_id is not None:
        raise HTTPException(422, "Cupons só podem ser usados na conclusão de um atendimento")
    params = {"tenant": tenant_id, "id": appointment_id}
    professional = (await db.execute(text("SELECT professional_id FROM appointments WHERE tenant_id=:tenant AND id=:id"), params)).scalar_one_or_none()
    if professional is None:
        raise HTTPException(404, "Agendamento não encontrado")
    # Match booking_service's lock order to prevent deadlocks with confirmation,
    # cancellation and rescheduling running on another worker.
    await acquire_professional_lock(db, tenant_id, professional)
    row = (await db.execute(text("SELECT * FROM appointments WHERE tenant_id=:tenant AND id=:id FOR UPDATE"), params)).mappings().one()
    if row["professional_id"] != professional:
        raise HTTPException(409, "O profissional mudou. Atualize o agendamento")
    context = LifecycleActor(role="system" if automated else operator,
                             source="system" if automated else "panel",
                             account_id=None if automated else db.info.get("management_actor_account_id"))
    identity_payload = {"appointment_id": appointment_id, "expected_status": payload.expected_status,
                        "target": target, "coupon_id": payload.coupon_id}
    if payload.expected_updated_at is not None:
        identity_payload = operation_payload(appointment_id, payload.expected_updated_at, context,
            expected_status=payload.expected_status, target=target, coupon_id=payload.coupon_id)
    _, replay = await request_identity(db, tenant_id, "appointment_" + target, payload.idempotency_key,
                                      identity_payload, entity_id=appointment_id)
    if replay:
        result = await read_appointment(db, tenant_id, appointment_id)
        return {**clean_row(result), "allowed_actions": allowed_actions(result, can_write=True)}
    check_expected_updated_at(row, payload.expected_updated_at)
    now = datetime.now(timezone.utc)
    if row["status"] != payload.expected_status:
        raise HTTPException(409, "Somente agendamentos confirmados podem receber este resultado")
    if row["start_at"] > now:
        raise HTTPException(409, "Aguarde o horário de início para registrar o resultado")
    if automated and row["end_at"] > now:
        raise HTTPException(409, "Aguarde o término previsto do atendimento")
    percent = None
    discount = 0
    gross = row["total_cents"]
    if target == "completed":
        from app.loyalty_rewards import completion_discount
        discount = await completion_discount(db, tenant_id, row, payload.coupon_id, now)
        percent = (await db.execute(text("""
            SELECT commission_percent FROM professionals WHERE tenant_id=:tenant AND id=:professional FOR SHARE
        """), {"tenant": tenant_id, "professional": professional})).scalar_one()
    changed = (await db.execute(text("""
        UPDATE appointments SET status=CAST(:target AS varchar),
            updated_at=GREATEST(:now,updated_at+interval '1 microsecond'),hold_expires_at=NULL,
            commission_percent_snapshot=:percent,
            commission_cents=CASE WHEN CAST(:target AS varchar)='completed' THEN round(CAST(:net AS numeric) * CAST(:percent AS numeric) / 100)::bigint ELSE NULL END,
            total_cents=CAST(:net AS integer),original_total_cents=:gross,discount_cents=:discount
        WHERE tenant_id=:tenant AND id=:id AND status=:expected AND start_at<=clock_timestamp()
          AND (NOT :automated OR end_at<=clock_timestamp())
          AND """ + CAS_SQL + " RETURNING *"),
        {**params, "target": target, "now": now, "percent": percent, "expected": payload.expected_status,
         "expected_updated_at": payload.expected_updated_at, "net": gross-discount,
         "gross": gross if target == "completed" else None, "discount": discount,
         "automated": automated})).mappings().one_or_none()
    if changed is None:
        raise HTTPException(409, "O agendamento mudou. Atualize os detalhes antes de continuar")
    await history(db, tenant_id, appointment_id, target, context=context, previous=row)
    await audit(db, tenant_id, "appointment_" + target, "appointments", appointment_id)
    result = clean_row(await read_appointment(db, tenant_id, appointment_id))
    result["allowed_actions"] = allowed_actions(result, can_write=True)
    if target == "completed":
        from app.loyalty_rewards import award_completion
        from app.appointment_finance import record_service_receivable
        result["loyalty"] = await award_completion(db, tenant_id, changed, now)
        await record_service_receivable(db, tenant_id, changed)
    return result


# Legacy completed bookings have no snapshot. These rows are explicitly counted
# in reports so estimates using the current profile rate are never disguised as
# a historical rate. New completions always persist both snapshot values.
COMMISSION_VALUE = "COALESCE(a.commission_cents,round(a.total_cents*p.commission_percent/100)::bigint)"


async def daily_performance(db, tenant_id, start_date=None, end_date=None, professional_id=None):
    period, lower, upper = await period_for(db, tenant_id, start_date, end_date)
    condition = " AND a.professional_id=:professional" if professional_id else ""
    rows = (await db.execute(text("""
        SELECT date(a.start_at AT TIME ZONE :tz) AS date,p.id AS professional_id,p.name AS professional_name,
            count(*) AS completed,COALESCE(sum(a.total_cents),0) AS revenue_cents,
            COALESCE(sum(""" + COMMISSION_VALUE + """),0) AS commission_cents,
            count(*) FILTER (WHERE a.commission_cents IS NULL) AS legacy_rate_estimates
        FROM appointments a JOIN professionals p ON p.tenant_id=a.tenant_id AND p.id=a.professional_id
        WHERE a.tenant_id=:tenant AND a.status='completed' AND a.start_at>=:lower AND a.start_at<:upper
    """ + condition + " GROUP BY date(a.start_at AT TIME ZONE :tz),p.id,p.name ORDER BY date,p.name,p.id"),
        {"tenant": tenant_id, "lower": lower, "upper": upper, "tz": period["timezone"], "professional": professional_id})).mappings().all()
    return {"period": period, "items": [dict(row) for row in rows], "legacy_rate_source": "current_profile"}


async def commission_report(db, tenant_id, start_date=None, end_date=None):
    period, lower, upper = await period_for(db, tenant_id, start_date, end_date)
    params = {"tenant": tenant_id, "lower": lower, "upper": upper}
    rows = (await db.execute(text("""
        SELECT p.id,p.name,p.commission_percent,
            count(a.id) FILTER (WHERE a.status='completed' AND a.start_at>=:lower AND a.start_at<:upper) AS completed,
            COALESCE(sum(a.total_cents) FILTER (WHERE a.status='completed' AND a.start_at>=:lower AND a.start_at<:upper),0) AS revenue_cents,
            COALESCE(sum(""" + COMMISSION_VALUE + """) FILTER (WHERE a.status='completed' AND a.start_at>=:lower AND a.start_at<:upper),0) AS earned_cents,
            COALESCE(sum(""" + COMMISSION_VALUE + """) FILTER (WHERE a.status='completed'),0) AS lifetime_earned_cents,
            count(a.id) FILTER (WHERE a.status='completed' AND a.commission_cents IS NULL AND a.start_at>=:lower AND a.start_at<:upper) AS legacy_rate_estimates,
            COALESCE(sum(round(a.total_cents*p.commission_percent/100)) FILTER
                (WHERE a.status='confirmed' AND a.start_at>=:lower AND a.start_at<:upper AND a.start_at>=now()),0) AS scheduled_cents
        FROM professionals p LEFT JOIN appointments a ON a.tenant_id=p.tenant_id AND a.professional_id=p.id
        WHERE p.tenant_id=:tenant GROUP BY p.id,p.name,p.commission_percent ORDER BY p.name,p.id
    """), params)).mappings().all()
    movements = (await db.execute(text("""
        SELECT professional_id,
          COALESCE(sum(amount_cents) FILTER (WHERE entry_type='advance'),0) AS lifetime_advances_cents,
          COALESCE(sum(amount_cents) FILTER (WHERE entry_type='commission_payment'),0) AS lifetime_payments_cents,
          COALESCE(sum(amount_cents) FILTER (WHERE entry_type='advance' AND occurred_at>=:lower AND occurred_at<:upper),0) AS advances_cents,
          COALESCE(sum(amount_cents) FILTER (WHERE entry_type='commission_payment' AND occurred_at>=:lower AND occurred_at<:upper),0) AS payments_cents
        FROM financial_entries WHERE tenant_id=:tenant AND kind='expense' AND status='paid'
            AND entry_type IN ('advance','commission_payment') GROUP BY professional_id
    """), params)).mappings().all()
    by_professional = {row["professional_id"]: row for row in movements}
    today = datetime.now(timezone.utc).astimezone(ZoneInfo(period["timezone"])).date()
    period_days = (period["end_date"] - period["start_date"]).days + 1
    elapsed_days = max(1, min(period_days, (today - period["start_date"]).days + 1))
    items = []
    for row in rows:
        item = dict(row)
        movement = by_professional.get(item["id"], {})
        for key in ("lifetime_advances_cents", "lifetime_payments_cents", "advances_cents", "payments_cents"):
            item[key] = int(movement.get(key, 0))
        for key in ("revenue_cents", "earned_cents", "lifetime_earned_cents", "scheduled_cents"):
            item[key] = int(item[key])
        item["balance_cents"] = item["lifetime_earned_cents"] - item["lifetime_advances_cents"] - item["lifetime_payments_cents"]
        item["payable_cents"] = max(0, item["balance_cents"])
        item["daily_average_cents"] = item["earned_cents"] // elapsed_days
        item["projected_cents"] = item["earned_cents"] * period_days // elapsed_days
        items.append(item)
    return {"period": period, "items": items, "projection_basis": "completed_daily_average",
            "balance_basis": "all_recorded_completions_minus_paid_advances_and_commissions",
            "legacy_rate_source": "current_profile", "transfers_enabled": False}


async def income_statement(db, tenant_id, start_date=None, end_date=None):
    period, lower, upper = await period_for(db, tenant_id, start_date, end_date)
    params = {"tenant": tenant_id, "lower": lower, "upper": upper}
    service = dict((await db.execute(text("""
        SELECT COALESCE(sum(a.total_cents),0) AS service_revenue_cents,
            COALESCE(sum(""" + COMMISSION_VALUE + """),0) AS commissions_cents,
            count(*) FILTER (WHERE a.commission_cents IS NULL) AS legacy_rate_estimates
        FROM appointments a JOIN professionals p ON p.tenant_id=a.tenant_id AND p.id=a.professional_id
        WHERE a.tenant_id=:tenant AND a.status='completed' AND a.start_at>=:lower AND a.start_at<:upper
    """), params)).mappings().one())
    ledger = dict((await db.execute(text("""
        SELECT COALESCE(sum(amount_cents) FILTER (WHERE kind='income' AND appointment_id IS NULL),0) AS other_income_cents,
            COALESCE(sum(amount_cents) FILTER (WHERE kind='expense' AND entry_type='general'),0) AS operating_expenses_cents,
            COALESCE(sum(amount_cents) FILTER (WHERE entry_type='advance'),0) AS advances_cents,
            COALESCE(sum(amount_cents) FILTER (WHERE entry_type='commission_payment'),0) AS commission_payments_cents
        FROM financial_entries WHERE tenant_id=:tenant AND status='paid' AND occurred_at>=:lower AND occurred_at<:upper
    """), params)).mappings().one())
    summary = {key: int(value) for key, value in {**service, **ledger}.items()}
    summary["gross_revenue_cents"] = summary["service_revenue_cents"] + summary["other_income_cents"]
    summary["net_result_cents"] = summary["gross_revenue_cents"] - summary["commissions_cents"] - summary["operating_expenses_cents"]
    return {"period": period, "summary": summary,
            "revenue_basis": "completed_appointments_plus_unlinked_paid_income",
            "expense_basis": "earned_commissions_and_paid_general_expenses",
            "legacy_rate_source": "current_profile"}
