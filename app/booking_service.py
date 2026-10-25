"""Motor único de reserva: fonte da verdade é o PostgreSQL (FATIA 2).

Nenhum canal externo grava diretamente em appointments. Operações de escrita
recebem tenant já autenticado pelo operador (futuramente por webhook assinado).
"""
import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import tenant_transaction
from app.appointment_lifecycle import (
    CAS_SQL, LifecycleActor, SYSTEM_ACTOR, check_expected_updated_at,
    metadata, operation_payload, operation_replayed, public_history, record_operation, resolved_actor,
)
from app.customer_names import useful_name

UTC = timezone.utc
PROTOCOL_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
HOLD_TTL = timedelta(minutes=5)


def fail(status: int, message: str):
    raise HTTPException(status, message)


async def acquire_professional_lock(s: AsyncSession, tenant: UUID, professional: UUID):
    # Todas as mutações de agenda adquirem este lock dentro da transação.
    await s.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"),
                    {"scope": str(tenant) + ":" + str(professional)})


# tenants intentionally grants runtime SELECT only. Read the whole contract in
# one statement snapshot; recheck this exact query in the final UPDATE instead
# of using FOR SHARE (which would require UPDATE privilege on tenants).
BUNDLE_SQL = """
    SELECT jsonb_build_object(
        'timezone', t.timezone, 'slot_step_minutes', t.slot_step_minutes,
        'min_notice_minutes', t.min_notice_minutes,
        'booking_horizon_days', t.booking_horizon_days, 'status', t.status,
        'professional_name', (SELECT p.name FROM professionals p
            WHERE p.tenant_id=t.id AND p.id=:bundle_professional
              AND p.active AND p.accepts_appointments)
    ) AS shop,
    EXISTS (SELECT 1 FROM professionals p
        WHERE p.tenant_id=t.id AND p.id=:bundle_professional
          AND p.active AND p.accepts_appointments
    ) AS professional_available,
    COALESCE((SELECT jsonb_agg(jsonb_build_object(
        'id', s.id, 'name', s.name, 'duration_min', s.duration_min,
        'price_cents', s.price_cents, 'prep_min', s.prep_min,
        'cleanup_min', s.cleanup_min
    ) ORDER BY array_position(CAST(:bundle_ids AS uuid[]), s.id))
        FROM services s JOIN professional_services ps
          ON ps.tenant_id=s.tenant_id AND ps.service_id=s.id
        WHERE s.tenant_id=t.id AND ps.professional_id=:bundle_professional
          AND s.active AND s.id=ANY(CAST(:bundle_ids AS uuid[]))
    ), '[]'::jsonb) AS items
    FROM tenants t WHERE t.id=:bundle_tenant
"""
BUNDLE_MATCH_SQL = """EXISTS (SELECT 1 FROM (""" + BUNDLE_SQL + """) AS catalog_contract
    WHERE catalog_contract.professional_available
      AND catalog_contract.shop=CAST(:expected_shop AS jsonb)
      AND catalog_contract.items=CAST(:expected_items AS jsonb))"""
CUSTOMER_MATCH_SQL = """(CAST(:customer_phone AS text) IS NULL OR EXISTS (
    SELECT 1 FROM customers c WHERE c.tenant_id=appointments.tenant_id
      AND c.id=appointments.customer_id AND c.whatsapp_e164=CAST(:customer_phone AS text)))"""


def bundle_parameters(tenant, professional, ids):
    return {"bundle_tenant": tenant, "bundle_professional": professional, "bundle_ids": ids}


def bundle_guard_parameters(tenant, professional, ids, shop, items, customer_phone):
    return {**bundle_parameters(tenant, professional, ids),
            "expected_shop": json.dumps(shop), "expected_items": json.dumps(items, default=str),
            "customer_phone": customer_phone}


async def load_bundle(s: AsyncSession, tenant: UUID, professional: UUID, ids: list[UUID]):
    if not ids or len(ids) > 6 or len(set(ids)) != len(ids):
        fail(422, "Escolha de 1 a 6 serviços diferentes")
    contract = (await s.execute(text(BUNDLE_SQL), bundle_parameters(tenant, professional, ids))).mappings().first()
    if contract is None or contract["shop"]["status"] != "active":
        fail(404, "Estabelecimento indisponível")
    if not contract["professional_available"]:
        fail(422, "Profissional indisponível")
    found = {UUID(r["id"]): {**r, "id": UUID(r["id"])} for r in contract["items"]}
    if len(found) != len(ids):
        fail(422, "Serviço inativo ou não realizado pelo profissional")
    ordered = [found[x] for x in ids]
    return dict(contract["shop"]), ordered


def amounts(items: list[dict]):
    duration = sum(i["duration_min"] for i in items)
    prep = sum(i["prep_min"] for i in items)
    cleanup = sum(i["cleanup_min"] for i in items)
    price = sum(i["price_cents"] for i in items)
    return duration, prep, cleanup, price


def bounds(start: datetime, items: list[dict]):
    duration, prep, cleanup, price = amounts(items)
    utc_start = start.astimezone(UTC)
    end = utc_start + timedelta(minutes=duration)
    return {
        "start_at": utc_start,
        "end_at": end,
        "occupied_from": utc_start - timedelta(minutes=prep),
        "occupied_until": end + timedelta(minutes=cleanup),
        "total_cents": price,
    }


def validate_local(start: datetime, tz: ZoneInfo):
    if start.tzinfo is None or start.utcoffset() is None:
        fail(422, "Envie data e hora com fuso explícito")
    local = start.astimezone(tz)
    # Não aceitar horário inexistente, nem offset incompatível com o fuso real.
    submitted = start.replace(tzinfo=None)
    if start.utcoffset() != local.utcoffset() or submitted != local.replace(tzinfo=None):
        fail(422, "Horário ou offset incompatível com o fuso da barbearia")
    # Horário ambíguo em transição de DST não é oferecido no piloto.
    if local.replace(fold=0).utcoffset() != local.replace(fold=1).utcoffset():
        fail(422, "Horário ambíguo de mudança de fuso não disponível")
    return local


@dataclass(frozen=True)
class _SlotConstraints:
    tenant: UUID
    professional: UUID
    day: date
    hours: dict | None
    pauses: tuple
    blocks: tuple
    reservations: tuple


async def _read_slot_constraints(s, tenant, professional, target, tz, now, ignored):
    """Request-local inputs for a read-only grid; never reused by a mutation."""
    hours = (await s.execute(text("""
        SELECT id, start_time, end_time FROM working_hours
        WHERE tenant_id=:tenant AND professional_id=:professional AND weekday=:weekday
    """), {"tenant": tenant, "professional": professional, "weekday": target.weekday()})).mappings().first()
    if hours is None:
        return _SlotConstraints(tenant, professional, target, None, (), (), ())
    pauses = (await s.execute(text("""
        SELECT start_time, end_time FROM working_breaks
        WHERE tenant_id=:tenant AND working_hour_id=:working_hour
    """), {"tenant": tenant, "working_hour": hours["id"]})).mappings().all()
    # A valid candidate's complete occupied interval must fit inside this
    # shift, so intervals outside it cannot reject any candidate in the grid.
    begin = datetime.combine(target, hours["start_time"], tzinfo=tz).astimezone(UTC)
    stop = datetime.combine(target, hours["end_time"], tzinfo=tz).astimezone(UTC)
    parameters = {"tenant": tenant, "professional": professional, "begin": begin, "stop": stop}
    blocks = []
    for table in ("time_off", "schedule_blocks"):
        blocks.extend((await s.execute(text(f"""
            SELECT starts_at, ends_at FROM {table}
            WHERE tenant_id=:tenant AND professional_id=:professional
              AND starts_at < :stop AND ends_at > :begin
        """), parameters)).mappings().all())
    reservations = (await s.execute(text("""
        SELECT occupied_from AS starts_at, occupied_until AS ends_at FROM appointments
        WHERE tenant_id=:tenant AND professional_id=:professional
          AND id != :ignored AND status IN ('hold','confirmed')
          AND occupied_from < :stop AND occupied_until > :begin
          AND (status <> 'hold' OR hold_expires_at > :now)
    """), {**parameters, "ignored": ignored or UUID(int=0), "now": now})).mappings().all()
    return _SlotConstraints(tenant, professional, target, dict(hours), tuple(pauses), tuple(blocks), tuple(reservations))


async def check_slot(s: AsyncSession, tenant: UUID, professional: UUID,
                     shop: dict, items: list[dict], start: datetime, now: datetime,
                     ignore_appointment: UUID | None = None, *, _constraints: _SlotConstraints | None = None):
    tz = ZoneInfo(shop["timezone"])
    local = validate_local(start, tz)
    if start.astimezone(UTC) < now + timedelta(minutes=shop["min_notice_minutes"]):
        fail(409, "Horário sem antecedência mínima")
    if local.date() > now.astimezone(tz).date() + timedelta(days=shop["booking_horizon_days"]):
        fail(409, "Data ultrapassa o horizonte de agendamento")
    if local.date() < now.astimezone(tz).date():
        fail(409, "Não é possível reservar no passado")
    minute = local.hour * 60 + local.minute
    if local.second or local.microsecond or minute % shop["slot_step_minutes"]:
        fail(422, "Horário fora da grade configurada")
    b = bounds(start, items)
    if _constraints is not None and (_constraints.tenant != tenant or _constraints.professional != professional or _constraints.day != local.date()):
        raise ValueError("Slot constraints belong to another request scope")
    hours = _constraints.hours if _constraints is not None else (await s.execute(text("""
        SELECT id, start_time, end_time FROM working_hours
        WHERE tenant_id=:tenant AND professional_id=:professional AND weekday=:weekday
    """), {"tenant": tenant, "professional": professional, "weekday": local.weekday()})).mappings().first()
    if hours is None:
        fail(409, "Profissional não trabalha nesse dia")
    work_start = datetime.combine(local.date(), hours["start_time"], tzinfo=tz).astimezone(UTC)
    work_end = datetime.combine(local.date(), hours["end_time"], tzinfo=tz).astimezone(UTC)
    if b["occupied_from"] < work_start or b["occupied_until"] > work_end:
        fail(409, "Atendimento completo e buffers não cabem no expediente")
    pauses = _constraints.pauses if _constraints is not None else (await s.execute(text("""
        SELECT start_time, end_time FROM working_breaks
        WHERE tenant_id=:tenant AND working_hour_id=:working_hour
    """), {"tenant": tenant, "working_hour": hours["id"]})).mappings().all()
    for pause in pauses:
        lo = datetime.combine(local.date(), pause["start_time"], tzinfo=tz).astimezone(UTC)
        hi = datetime.combine(local.date(), pause["end_time"], tzinfo=tz).astimezone(UTC)
        if lo < b["occupied_until"] and hi > b["occupied_from"]:
            fail(409, "Horário conflita com pausa do profissional")
    if _constraints is not None:
        for entry in _constraints.blocks:
            if entry["starts_at"] < b["occupied_until"] and entry["ends_at"] > b["occupied_from"]:
                fail(409, "Horário bloqueado ou em folga")
        for entry in _constraints.reservations:
            if entry["starts_at"] < b["occupied_until"] and entry["ends_at"] > b["occupied_from"]:
                fail(409, "Horário já reservado")
        return b
    for table in ("time_off", "schedule_blocks"):
        conflict = (await s.execute(text(f"""
            SELECT 1 FROM {table} WHERE tenant_id=:tenant AND professional_id=:professional
            AND starts_at < :stop AND ends_at > :begin LIMIT 1
        """), {"tenant": tenant, "professional": professional,
               "begin": b["occupied_from"], "stop": b["occupied_until"]})).first()
        if conflict:
            fail(409, "Horário bloqueado ou em folga")
    conflict = (await s.execute(text("""
        SELECT 1 FROM appointments
        WHERE tenant_id=:tenant AND professional_id=:professional
          AND id != :ignored AND status IN ('hold','confirmed')
          AND occupied_from < :stop AND occupied_until > :begin
          AND (status <> 'hold' OR hold_expires_at > :now)
        LIMIT 1
    """), {"tenant": tenant, "professional": professional,
           "ignored": ignore_appointment or UUID(int=0), "now": now,
           "begin": b["occupied_from"], "stop": b["occupied_until"]})).first()
    if conflict:
        fail(409, "Horário já reservado")
    return b


async def expire_holds(s: AsyncSession, tenant: UUID, professional: UUID, now: datetime):
    rows = (await s.execute(text("""
        UPDATE appointments SET status='cancelled', updated_at=GREATEST(:now,updated_at+interval '1 microsecond')
        WHERE tenant_id=:tenant AND professional_id=:professional AND status='hold'
          AND hold_expires_at <= :now RETURNING id
    """), {"tenant": tenant, "professional": professional, "now": now})).scalars().all()
    for item_id in rows:
        await history(s, tenant, item_id, "hold_expired", context=SYSTEM_ACTOR)


async def history(s: AsyncSession, tenant: UUID, appointment: UUID, event: str, *, calendar: bool = True,
                  context: LifecycleActor | None = None, requested_by=None, reason=None, previous=None):
    details = metadata(context, requested_by=requested_by, reason=reason, previous=previous)
    await s.execute(text("""
        INSERT INTO appointment_history (tenant_id,appointment_id,event,operator,details,occurred_at)
        VALUES (:tenant,:appointment,:event,:operator,CAST(:details AS jsonb),clock_timestamp())
    """), {"tenant": tenant, "appointment": appointment, "event": event,
           "operator": details["actor"]["role"], "details": json.dumps(details, ensure_ascii=False)})
    if calendar:
        from app.calendar_sync import enqueue_calendar_sync
        await enqueue_calendar_sync(s, tenant, appointment, event)


def protocol():
    return "AG-" + "".join(secrets.choice(PROTOCOL_ALPHABET) for _ in range(6))


def fingerprint(professional: UUID, services: list[UUID], start: datetime, phone: str):
    value = json.dumps({
        "professional": str(professional), "services": [str(x) for x in services],
        "start": start.astimezone(UTC).isoformat(), "phone": phone,
    }, sort_keys=True)
    return hashlib.sha256(value.encode()).hexdigest()


async def read_appointments(s: AsyncSession, tenant: UUID, item_ids: list[UUID]):
    """Read one bounded page with two queries; item snapshots keep their shape."""
    if not item_ids:
        return []
    if len(item_ids) > 200:
        raise ValueError("Read appointment pages of at most 200 records")
    rows = (await s.execute(text("""
        SELECT a.id, a.tenant_id, a.customer_id, a.professional_id, a.protocol,
               a.start_at, a.end_at, a.status, a.total_cents, a.hold_expires_at, a.updated_at,
               a.original_total_cents, a.discount_cents,
               a.commission_percent_snapshot, a.commission_cents,
               c.name AS customer_name, c.whatsapp_e164 AS customer_phone,
               p.name AS professional_name, p.color AS professional_color, t.timezone,
               cancellation.id AS cancellation_history_id,
               cancellation.event AS cancellation_event,
               cancellation.occurred_at AS cancellation_occurred_at,
               cancellation.details AS cancellation_details
        FROM appointments a JOIN customers c ON c.id=a.customer_id AND c.tenant_id=a.tenant_id
        JOIN professionals p ON p.id=a.professional_id AND p.tenant_id=a.tenant_id
        JOIN tenants t ON t.id=a.tenant_id
        LEFT JOIN LATERAL (
            SELECT h.id,h.event,h.occurred_at,h.details FROM appointment_history h
            WHERE a.status='cancelled' AND h.tenant_id=a.tenant_id AND h.appointment_id=a.id
              AND h.event IN ('cancelled','hold_expired')
            ORDER BY h.occurred_at DESC,h.id DESC LIMIT 1
        ) cancellation ON true
        WHERE a.tenant_id=:tenant AND a.id=ANY(CAST(:ids AS uuid[]))
    """), {"tenant": tenant, "ids": item_ids})).mappings().all()
    items = (await s.execute(text("""
        SELECT appointment_id, service_id, name_snapshot, duration_snapshot_min, price_snapshot_cents,
               prep_snapshot_min, cleanup_snapshot_min
        FROM appointment_items WHERE tenant_id=:tenant AND appointment_id=ANY(CAST(:ids AS uuid[]))
        ORDER BY appointment_id,item_order
    """), {"tenant": tenant, "ids": item_ids})).mappings().all()
    by_appointment = {}
    for item in items:
        value = dict(item)
        by_appointment.setdefault(value.pop("appointment_id"), []).append(value)
    results = {}
    for row in rows:
        result = {**dict(row), "items": by_appointment.get(row["id"], [])}
        result["customer_display_name"] = useful_name(result["customer_name"]) or result["customer_phone"]
        cancellation = public_history({
            "id": result.pop("cancellation_history_id"), "event": result.pop("cancellation_event"),
            "occurred_at": result.pop("cancellation_occurred_at"), "details": result.pop("cancellation_details"),
        })
        result["cancellation"] = {key: cancellation[key] for key in
            ("event", "requested_by", "reason", "source", "occurred_at", "actor")} if result["status"] == "cancelled" else None
        results[row["id"]] = result
    return [results[identity] for identity in item_ids if identity in results]


async def read_appointment(s: AsyncSession, tenant: UUID, item_id: UUID, *, include_history=False):
    rows = await read_appointments(s, tenant, [item_id])
    if not rows:
        fail(404, "Agendamento não encontrado")
    result = rows[0]
    if include_history:
        history_rows = (await s.execute(text("""
            SELECT id,event,occurred_at,details FROM appointment_history
            WHERE tenant_id=:tenant AND appointment_id=:id
            ORDER BY occurred_at DESC,id DESC LIMIT 100
        """), {"tenant": tenant, "id": item_id})).mappings().all()
        total = (await s.execute(text("""
            SELECT count(*) FROM appointment_history WHERE tenant_id=:tenant AND appointment_id=:id
        """), {"tenant": tenant, "id": item_id})).scalar_one()
        result.update(history=[public_history(entry) for entry in history_rows],
                      history_total=total, history_has_more=total > len(history_rows))
    return result


async def _hold_proposal(s: AsyncSession, tenant: UUID, item_id: UUID):
    detail = await read_appointment(s, tenant, item_id)
    return {**detail, "expected_snapshot": appointment_snapshot(detail)}


async def _owned_hold(s: AsyncSession, tenant: UUID, item_id: UUID, *,
                      customer_phone: str, professional: UUID | None = None,
                      expected_snapshot: str | None = None, lock: bool = False):
    if not customer_phone:
        fail(403, "A consulta exige o contato autenticado")
    row = (await s.execute(text("""
        SELECT * FROM appointments WHERE tenant_id=:tenant AND id=:id
    """ + (" FOR UPDATE" if lock else "")), {"tenant": tenant, "id": item_id})).mappings().first()
    if row is None:
        fail(404, "Reserva temporária indisponível para este contato")
    await verify_customer_snapshot(s, tenant, row, customer_phone, expected_snapshot)
    if row["status"] != "hold" or (professional is not None and row["professional_id"] != professional):
        fail(409, "Somente a própria reserva temporária desse profissional pode ser substituída")
    return row


async def preview_hold(s: AsyncSession, tenant: UUID, item_id: UUID, *, customer_phone: str):
    """Read the owned temporary proposal and its real deadline without extending it."""
    async with tenant_transaction(s, tenant):
        await _owned_hold(s, tenant, item_id, customer_phone=customer_phone)
        return await _hold_proposal(s, tenant, item_id)


async def _replacement_hold(s, tenant, professional, item_id, phone, expected, now, *, lock=False):
    if not isinstance(expected, str) or not expected:
        fail(409, "Confira a reserva temporária antes de substituir o horário")
    row = await _owned_hold(s, tenant, item_id, customer_phone=phone,
                            professional=professional, expected_snapshot=expected, lock=lock)
    if row["hold_expires_at"] is None or row["hold_expires_at"] <= now:
        fail(409, "A reserva temporária expirou. Consulte um novo horário")
    return row


async def verified_slot_grid(s: AsyncSession, tenant: UUID, professional: UUID,
                             shop: dict, items: list[dict], target: date, now: datetime,
                             *, ignored: UUID | None = None, strict_errors: bool = False):
    """Read one fresh request-scoped set of constraints and validate every local candidate.

    Both WhatsApp/management and public booking use the exact same slot rules.
    This is a read-only query. Writes still recheck the slot inside their transaction.
    """
    tz = ZoneInfo(shop["timezone"])
    constraints = await _read_slot_constraints(s, tenant, professional, target, tz, now, ignored)
    if constraints.hours is None:
        return [], "no_working_hours"
    result = []
    for minute in range(0, 24 * 60, shop["slot_step_minutes"]):
        naive = datetime.combine(target, time.min) + timedelta(minutes=minute)
        local = naive.replace(tzinfo=tz)
        start = local.astimezone(UTC).astimezone(tz)
        if start.replace(tzinfo=None) != naive:
            continue  # Never offer a clock that does not exist in this timezone.
        try:
            await check_slot(s, tenant, professional, shop, items, start, now,
                             ignore_appointment=ignored, _constraints=constraints)
            result.append(start.isoformat())
        except HTTPException as exc:
            if strict_errors and exc.status_code not in {409, 422}:
                raise
    return result, "available" if result else "no_matching_slots"


async def list_slots(s: AsyncSession, tenant: UUID, professional: UUID,
                     ids: list[UUID], target: date, now: datetime | None = None, *,
                     replace_hold_id: UUID | None = None, customer_phone: str | None = None,
                     expected_snapshot: str | None = None, appointment_id: UUID | None = None,
                      explain_reason: bool = False):
    now = now or datetime.now(UTC)
    async with tenant_transaction(s, tenant):
        if replace_hold_id is not None:
            await _replacement_hold(s, tenant, professional, replace_hold_id, customer_phone,
                                    expected_snapshot, now)
        elif customer_phone is not None or expected_snapshot is not None:
            fail(422, "A consulta de substituição exige a reserva temporária")
        if appointment_id is not None:
            if replace_hold_id is not None:
                fail(422, "Selecione apenas uma operação de agenda")
            current = (await s.execute(text("""
                SELECT professional_id,status FROM appointments WHERE tenant_id=:tenant AND id=:id
            """), {"tenant": tenant, "id": appointment_id})).mappings().one_or_none()
            if current is None:
                fail(404, "Agendamento não encontrado")
            service_ids = list((await s.execute(text("""
                SELECT service_id FROM appointment_items WHERE tenant_id=:tenant AND appointment_id=:id ORDER BY item_order
            """), {"tenant": tenant, "id": appointment_id})).scalars().all())
            if current["status"] != "confirmed" or current["professional_id"] != professional or service_ids != ids:
                fail(409, "A consulta de remarcação exige o profissional e os serviços do agendamento confirmado")
        shop, items = await load_bundle(s, tenant, professional, ids)
        local_now = now.astimezone(ZoneInfo(shop["timezone"]))
        if target < local_now.date() or target > local_now.date() + timedelta(days=shop["booking_horizon_days"]):
            fail(422, "Data fora do horizonte de agendamento")
        result, availability_reason = await verified_slot_grid(s, tenant, professional,
            shop, items, target, now, ignored=appointment_id or replace_hold_id)
        duration, prep, cleanup, price = amounts(items)
        return {"slots": result, "duration_min": duration, "prep_min": prep,
                "cleanup_min": cleanup, "price_cents": price, "timezone": shop["timezone"],
                **({"availability_reason": availability_reason} if explain_reason else {})}


async def book_appointment(s: AsyncSession, tenant: UUID, professional: UUID,
                           ids: list[UUID], phone: str, customer_name: str,
                           start: datetime, idempotency_key: str,
                           now: datetime | None = None, *, replace_hold_id: UUID | None = None,
                           expected_snapshot: str | None = None,
                           context: LifecycleActor | None = None, source: str = "unknown"):
    """ÚNICA função para criar reservas/holds de todos os canais futuros."""
    supplied_clock = now is not None
    now = now or datetime.now(UTC)
    fp = fingerprint(professional, ids, start, phone)
    if replace_hold_id is not None:
        if not isinstance(expected_snapshot, str) or not expected_snapshot:
            fail(409, "Confira a reserva temporária antes de substituir o horário")
        fp = hashlib.sha256(json.dumps({"request": fp, "replace_hold": str(replace_hold_id),
                                       "snapshot": expected_snapshot}, sort_keys=True).encode()).hexdigest()
    elif expected_snapshot is not None:
        fail(422, "A substituição exige a reserva temporária")
    async with tenant_transaction(s, tenant):
        await acquire_professional_lock(s, tenant, professional)
        if not supplied_clock:
            now = datetime.now(UTC)
        existing = (await s.execute(text("""
            SELECT id, request_fingerprint FROM appointments
            WHERE tenant_id=:tenant AND idempotency_key=:key
        """), {"tenant": tenant, "key": idempotency_key})).mappings().first()
        if existing:
            if existing["request_fingerprint"] != fp:
                fail(409, "Chave de idempotência já usada em outra solicitação")
            return await _hold_proposal(s, tenant, existing["id"])
        from app.onboarding_service import require_operational_ready
        await require_operational_ready(s, tenant, "appointments")
        if replace_hold_id is not None:
            previous = await _replacement_hold(s, tenant, professional, replace_hold_id, phone,
                                               expected_snapshot, now, lock=True)
            if not supplied_clock:
                now = datetime.now(UTC)
                if previous["hold_expires_at"] <= now:
                    fail(409, "A reserva temporária expirou. Consulte um novo horário")
        await expire_holds(s, tenant, professional, now)
        shop, items = await load_bundle(s, tenant, professional, ids)
        b = await check_slot(s, tenant, professional, shop, items, start, now,
                             ignore_appointment=replace_hold_id)
        if replace_hold_id is not None:
            changed = (await s.execute(text("""
                UPDATE appointments SET status='cancelled',hold_expires_at=NULL,
                    updated_at=GREATEST(:now,updated_at+interval '1 microsecond')
                WHERE tenant_id=:tenant AND id=:id AND professional_id=:professional AND status='hold'
                  AND hold_expires_at>:now AND """ + CUSTOMER_MATCH_SQL + " RETURNING id"),
                {"tenant": tenant, "id": replace_hold_id, "professional": professional,
                 "now": now, "customer_phone": phone})).scalar_one_or_none()
            if changed is None:
                fail(409, "A reserva temporária mudou. Confira um novo resumo")
            # This cancels an unconfirmed proposal, not a published calendar event.
            await history(s, tenant, replace_hold_id, "cancelled", calendar=False, previous=previous,
                          context=resolved_actor(context, source, previous["customer_id"], verified_customer=True))
        await s.execute(text("""
            INSERT INTO customers (tenant_id, whatsapp_e164, name)
            VALUES (:tenant,:phone,:name)
            ON CONFLICT (tenant_id,whatsapp_e164) DO NOTHING
        """), {"tenant": tenant, "phone": phone, "name": customer_name})
        customer = (await s.execute(text("""
            SELECT id FROM customers WHERE tenant_id=:tenant AND whatsapp_e164=:phone
        """), {"tenant": tenant, "phone": phone})).scalar_one()
        item_id = uuid4()
        # Retry com SAVEPOINT para colisão estatisticamente improvável do protocolo.
        from sqlalchemy.exc import IntegrityError
        for attempt in range(5):
            candidate = protocol()
            try:
                async with s.begin_nested():
                    await s.execute(text("""
                        INSERT INTO appointments (
                            id, tenant_id, customer_id, professional_id, protocol,
                            idempotency_key, request_fingerprint,
                            start_at,end_at,occupied_from,occupied_until,
                            status,hold_expires_at,total_cents
                        ) VALUES (
                            :id,:tenant,:customer,:professional,:protocol,
                            :key,:fp,:start_at,:end_at,:occupied_from,:occupied_until,
                            'hold',:expires,:total_cents
                        )
                    """), {
                        "id": item_id, "tenant": tenant, "customer": customer,
                        "professional": professional, "protocol": candidate,
                        "key": idempotency_key, "fp": fp,
                        "expires": (now if supplied_clock else datetime.now(UTC)) + HOLD_TTL, **b,
                    })
                break
            except IntegrityError as exc:
                # Apenas colisões de protocolo recebem retry; conflito de agenda
                # e duplicação de chave são erros de integridade.
                if "uq_appointment_protocol" not in str(exc):
                    raise
        else:
            fail(503, "Não foi possível gerar protocolo único")
        for order, item in enumerate(items):
            await s.execute(text("""
                INSERT INTO appointment_items (
                    tenant_id,appointment_id,service_id,name_snapshot,
                    duration_snapshot_min,price_snapshot_cents,
                    prep_snapshot_min,cleanup_snapshot_min,item_order
                ) VALUES (:tenant,:appointment,:service,:name,:duration,:price,:prep,:cleanup,:order)
            """), {"tenant": tenant, "appointment": item_id, "service": item["id"],
                   "name": item["name"], "duration": item["duration_min"],
                   "price": item["price_cents"], "prep": item["prep_min"],
                   "cleanup": item["cleanup_min"], "order": order})
        await history(s, tenant, item_id, "hold_created",
                      context=resolved_actor(context, source, customer, verified_customer=True))
        await s.execute(text("""
            INSERT INTO usage_counters (tenant_id,period,bookings)
            VALUES (:tenant,date_trunc('month',CURRENT_DATE)::date,1)
            ON CONFLICT (tenant_id,period) DO UPDATE
            SET bookings=usage_counters.bookings+1
        """),{"tenant":tenant})
        return await _hold_proposal(s, tenant, item_id)


async def confirm_hold(s: AsyncSession, tenant: UUID, item_id: UUID,
                       now: datetime | None = None, *, customer_phone: str | None = None,
                       expected_snapshot: str | None = None, expected_updated_at: datetime | None = None,
                       idempotency_key: str | None = None,
                       context: LifecycleActor | None = None, source: str = "unknown"):
    supplied_clock = now is not None
    now = now or datetime.now(UTC)
    expired = False
    result = None
    async with tenant_transaction(s, tenant):
        row = (await s.execute(text("""
            SELECT professional_id FROM appointments
            WHERE tenant_id=:tenant AND id=:id
        """), {"tenant": tenant, "id": item_id})).mappings().first()
        if not row:
            fail(404, "Agendamento não encontrado")
        await acquire_professional_lock(s, tenant, row["professional_id"])
        hold = (await s.execute(text("""
            SELECT * FROM appointments WHERE tenant_id=:tenant AND id=:id FOR UPDATE
        """), {"tenant": tenant, "id": item_id})).mappings().one()
        if hold["professional_id"] != row["professional_id"]:
            fail(409, "O profissional mudou. Atualize o agendamento")
        if not supplied_clock:
            now = datetime.now(UTC)
        if customer_phone is not None or expected_snapshot is not None:
            await verify_customer_snapshot(s, tenant, hold, customer_phone, expected_snapshot)
        context = resolved_actor(context, source, hold["customer_id"], verified_customer=customer_phone is not None)
        receipt = operation_payload(item_id, expected_updated_at, context)
        if await operation_replayed(s, tenant, "agenda_confirm", idempotency_key, receipt):
            return await read_appointment(s, tenant, item_id)
        check_expected_updated_at(hold, expected_updated_at)
        if hold["status"] == "confirmed":
            if expected_updated_at is not None:
                fail(409, "Este agendamento já está confirmado. Atualize os detalhes")
            return await read_appointment(s, tenant, item_id)  # replay sem duplicação
        if hold["status"] != "hold":
            fail(409, "Esta reserva não pode mais ser confirmada")
        if hold["hold_expires_at"] <= now:
            expired = True
            changed = (await s.execute(text("""
                UPDATE appointments SET status='cancelled',updated_at=GREATEST(:now,updated_at+interval '1 microsecond')
                WHERE id=:id AND tenant_id=:tenant
                  AND """ + CUSTOMER_MATCH_SQL + " AND " + CAS_SQL + " RETURNING id"),
                {"tenant": tenant, "id": item_id, "now": now, "customer_phone": customer_phone,
                 "expected_updated_at": expected_updated_at})).scalar_one_or_none()
            if changed is None:
                fail(409, "A reserva mudou. Confira um novo resumo antes de continuar")
            await history(s, tenant, item_id, "hold_expired", context=SYSTEM_ACTOR, previous=hold)
        else:
            from app.onboarding_service import require_operational_ready
            await require_operational_ready(s, tenant, "appointments")
            ids = (await s.execute(text("""
                SELECT service_id FROM appointment_items WHERE tenant_id=:tenant
                AND appointment_id=:id ORDER BY item_order
            """), {"tenant": tenant, "id": item_id})).scalars().all()
            shop, items = await load_bundle(s, tenant, hold["professional_id"], list(ids))
            fresh = bounds(hold["start_at"], items)
            # Evitar confirmação usando preço/tempo antigo se catálogo mudou
            if (fresh["end_at"] != hold["end_at"] or
                fresh["occupied_from"] != hold["occupied_from"] or
                fresh["occupied_until"] != hold["occupied_until"] or
                fresh["total_cents"] != hold["total_cents"]):
                fail(409, "O serviço mudou; escolha o horário novamente")
            await check_slot(s, tenant, hold["professional_id"], shop, items,
                             hold["start_at"].astimezone(ZoneInfo(shop["timezone"])), now, ignore_appointment=item_id)
            # READ COMMITTED takes a fresh snapshot for this statement. A
            # catalog edit committed after validation but before UPDATE rejects
            # the operation; no history/outbox is emitted for a rejected write.
            changed = (await s.execute(text("""
                UPDATE appointments SET status='confirmed',hold_expires_at=NULL,
                    updated_at=GREATEST(:now,updated_at+interval '1 microsecond')
                WHERE id=:id AND tenant_id=:tenant
                  AND status='hold'
                  AND hold_expires_at>COALESCE(CAST(:expiry_clock AS timestamptz),clock_timestamp())
                  AND """ + BUNDLE_MATCH_SQL + " AND " + CUSTOMER_MATCH_SQL + " AND " + CAS_SQL + " RETURNING id"),
                {"tenant": tenant, "id": item_id, "now": now,
                 "expiry_clock": now if supplied_clock else None, "expected_updated_at": expected_updated_at,
                 **bundle_guard_parameters(tenant, hold["professional_id"], list(ids), shop, items, customer_phone)})).scalar_one_or_none()
            if changed is None:
                fail(409, "A reserva ou os serviços mudaram. Confira um novo resumo antes de confirmar")
            await history(s, tenant, item_id, "confirmed", context=context, previous=hold)
            await record_operation(s, tenant, item_id, "agenda_confirm", idempotency_key, receipt)
            result = await read_appointment(s, tenant, item_id)
    if expired:
        fail(409, "Hold expirado; selecione outro horário")
    return result


def appointment_snapshot(appointment):
    fields = {key: appointment[key] for key in ("id", "customer_id", "professional_id", "status",
              "start_at", "end_at", "total_cents", "updated_at", "items")}
    return hashlib.sha256(json.dumps(fields, sort_keys=True, default=str).encode()).hexdigest()


async def verify_customer_snapshot(s, tenant, row, phone, expected):
    owns = (await s.execute(text("SELECT 1 FROM customers WHERE tenant_id=:t AND id=:id AND whatsapp_e164=:phone"),
        {"t": tenant, "id": row["customer_id"], "phone": phone})).scalar_one_or_none()
    if not owns:
        fail(404, "Reserva indisponível para este contato")
    if expected is not None:
        detail = await read_appointment(s, tenant, row["id"])
        actual = appointment_snapshot({**detail, "updated_at": row["updated_at"]})
        if not isinstance(expected, str) or not secrets.compare_digest(actual, expected):
            fail(409, "A reserva mudou. Confira um novo resumo antes de continuar")


async def cancel_appointment(s: AsyncSession, tenant: UUID, item_id: UUID, *,
                             customer_phone: str | None = None, expected_snapshot: str | None = None,
                             future_only: bool = False, expected_updated_at: datetime | None = None,
                             idempotency_key: str | None = None, requested_by: str | None = None,
                             reason: str | None = None, context: LifecycleActor | None = None,
                             source: str = "unknown"):
    async with tenant_transaction(s, tenant):
        row = (await s.execute(text("""
            SELECT professional_id FROM appointments WHERE tenant_id=:tenant AND id=:id
        """), {"tenant": tenant, "id": item_id})).mappings().first()
        if not row:
            fail(404, "Agendamento não encontrado")
        await acquire_professional_lock(s, tenant, row["professional_id"])
        entry = (await s.execute(text("""
            SELECT * FROM appointments WHERE tenant_id=:tenant AND id=:id FOR UPDATE
        """), {"tenant": tenant, "id": item_id})).mappings().one()
        if entry["professional_id"] != row["professional_id"]:
            fail(409, "O profissional mudou. Atualize o agendamento")
        if customer_phone is not None or expected_snapshot is not None:
            await verify_customer_snapshot(s, tenant, entry, customer_phone, expected_snapshot)
        context = resolved_actor(context, source, entry["customer_id"], verified_customer=customer_phone is not None)
        if context.role == "customer":
            requested_by = "customer"
        details = metadata(context, requested_by=requested_by, reason=reason, previous=entry)
        receipt = operation_payload(item_id, expected_updated_at, context,
                                    requested_by=details["requested_by"], reason=details["reason"])
        if await operation_replayed(s, tenant, "agenda_cancel", idempotency_key, receipt):
            return await read_appointment(s, tenant, item_id)
        check_expected_updated_at(entry, expected_updated_at)
        if entry["status"] == "cancelled":
            if expected_updated_at is not None:
                fail(409, "Este agendamento já foi cancelado. Atualize os detalhes")
            return await read_appointment(s, tenant, item_id)
        if entry["status"] not in ("confirmed", "hold"):
            fail(409, "Agendamento não pode ser cancelado neste estado")
        changed = (await s.execute(text("""
            UPDATE appointments SET status='cancelled', hold_expires_at=NULL,
                updated_at=GREATEST(clock_timestamp(),updated_at+interval '1 microsecond') WHERE tenant_id=:tenant AND id=:id
                AND (:future_only=false OR start_at>clock_timestamp())
                AND """ + CUSTOMER_MATCH_SQL + " AND " + CAS_SQL + " RETURNING id"),
            {"tenant": tenant, "id": item_id, "customer_phone": customer_phone,
             "future_only": future_only, "expected_updated_at": expected_updated_at})).scalar_one_or_none()
        if changed is None:
            fail(409, "A reserva mudou. Confira um novo resumo antes de cancelar")
        await history(s, tenant, item_id, "cancelled", context=context, requested_by=details["requested_by"],
                      reason=details["reason"], previous=entry)
        await record_operation(s, tenant, item_id, "agenda_cancel", idempotency_key, receipt)
        return await read_appointment(s, tenant, item_id)


def reschedule_snapshot(current, items, new_start, shop):
    """Bind consent to both the original booking and the factual new contract."""
    original = {key: current[key] for key in ("id", "customer_id", "professional_id", "status",
                "start_at", "end_at", "total_cents", "updated_at")}
    payload = {"original": original, "items": items, "start": new_start.astimezone(UTC), "shop": shop}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


async def preview_reschedule(s: AsyncSession, tenant: UUID, item_id: UUID, new_start: datetime,
                             *, customer_phone: str):
    """Read-only customer proposal; no hold, cancellation or movement occurs."""
    async with tenant_transaction(s, tenant):
        current = (await s.execute(text("""SELECT a.* FROM appointments a JOIN customers c
            ON c.tenant_id=a.tenant_id AND c.id=a.customer_id
            WHERE a.tenant_id=:t AND a.id=:id AND c.whatsapp_e164=:phone
              AND a.status='confirmed' AND a.end_at>now()"""),
            {"t": tenant, "id": item_id, "phone": customer_phone})).mappings().first()
        if current is None:
            fail(404, "Reserva indisponível para este contato")
        ids = list((await s.execute(text("SELECT service_id FROM appointment_items WHERE tenant_id=:t AND appointment_id=:id ORDER BY item_order"),
            {"t": tenant, "id": item_id})).scalars())
        shop, items = await load_bundle(s, tenant, current["professional_id"], ids)
        contract = await check_slot(s, tenant, current["professional_id"], shop, items, new_start,
                                   datetime.now(UTC), ignore_appointment=item_id)
        return {"id": item_id, "protocol": current["protocol"], "start_at": contract["start_at"],
                "timezone": shop["timezone"], "total_cents": contract["total_cents"],
                "duration_min": amounts(items)[0], "professional": shop["professional_name"], "services": [item["name"] for item in items],
                "expected_snapshot": reschedule_snapshot(current, items, new_start, shop)}


async def reschedule_appointment(s: AsyncSession, tenant: UUID, item_id: UUID,
                                 new_start: datetime, now: datetime | None = None, *,
                                 expected_snapshot: str | None = None, customer_phone: str | None = None,
                                 expected_updated_at: datetime | None = None, idempotency_key: str | None = None,
                                 context: LifecycleActor | None = None, source: str = "unknown"):
    supplied_clock = now is not None
    now = now or datetime.now(UTC)
    async with tenant_transaction(s, tenant):
        current = (await s.execute(text("""
            SELECT professional_id FROM appointments WHERE tenant_id=:tenant AND id=:id
        """), {"tenant": tenant, "id": item_id})).mappings().first()
        if not current:
            fail(404, "Agendamento não encontrado")
        await acquire_professional_lock(s, tenant, current["professional_id"])
        row = (await s.execute(text("""
            SELECT * FROM appointments WHERE tenant_id=:tenant AND id=:id FOR UPDATE
        """), {"tenant": tenant, "id": item_id})).mappings().one()
        if row["professional_id"] != current["professional_id"]:
            fail(409, "O profissional mudou. Atualize o agendamento")
        if not supplied_clock:
            now = datetime.now(UTC)
        if customer_phone is not None:
            await verify_customer_snapshot(s, tenant, row, customer_phone, None)
        context = resolved_actor(context, source, row["customer_id"], verified_customer=customer_phone is not None)
        receipt = operation_payload(item_id, expected_updated_at, context, start_at=new_start.astimezone(UTC))
        if await operation_replayed(s, tenant, "agenda_reschedule", idempotency_key, receipt):
            return await read_appointment(s, tenant, item_id)
        check_expected_updated_at(row, expected_updated_at)
        if row["status"] != "confirmed":
            fail(409, "Somente reservas confirmadas podem ser remarcadas")
        from app.onboarding_service import require_operational_ready
        await require_operational_ready(s, tenant, "rescheduling")
        ids = list((await s.execute(text("""
            SELECT service_id FROM appointment_items WHERE tenant_id=:tenant
            AND appointment_id=:id ORDER BY item_order
        """), {"tenant": tenant, "id": item_id})).scalars().all())
        shop, items = await load_bundle(s, tenant, row["professional_id"], ids)
        if expected_snapshot is not None:
            if not customer_phone:
                fail(403, "Confirmação exige o contato autenticado")
            owns = (await s.execute(text("SELECT 1 FROM customers WHERE tenant_id=:t AND id=:id AND whatsapp_e164=:phone"),
                {"t": tenant, "id": row["customer_id"], "phone": customer_phone})).scalar_one_or_none()
            actual = reschedule_snapshot(row, items, new_start, shop)
            if not owns:
                fail(404, "Reserva indisponível para este contato")
            if not isinstance(expected_snapshot, str) or not secrets.compare_digest(actual, expected_snapshot):
                fail(409, "A reserva ou os serviços mudaram. Confira um novo resumo antes de remarcar")
        b = await check_slot(s, tenant, row["professional_id"], shop, items, new_start,
                             now, ignore_appointment=item_id)
        changed = (await s.execute(text("""
            UPDATE appointments SET start_at=:start_at,end_at=:end_at,
                occupied_from=:occupied_from,occupied_until=:occupied_until,
                total_cents=:total_cents,updated_at=GREATEST(:now,updated_at+interval '1 microsecond')
            WHERE tenant_id=:tenant AND id=:id
              AND """ + BUNDLE_MATCH_SQL + " AND " + CUSTOMER_MATCH_SQL + " AND " + CAS_SQL + " RETURNING id"),
            {"tenant": tenant, "id": item_id, "now": now, "expected_updated_at": expected_updated_at, **b,
             **bundle_guard_parameters(tenant, row["professional_id"], ids, shop, items, customer_phone)})).scalar_one_or_none()
        if changed is None:
            fail(409, "A reserva ou os serviços mudaram. Confira um novo resumo antes de remarcar")
        # Snapshot acompanha o novo contrato da remarcação.
        for order, item in enumerate(items):
            await s.execute(text("""
                UPDATE appointment_items SET name_snapshot=:name,
                    duration_snapshot_min=:duration,price_snapshot_cents=:price,
                    prep_snapshot_min=:prep,cleanup_snapshot_min=:cleanup
                WHERE tenant_id=:tenant AND appointment_id=:id AND item_order=:position
            """), {"tenant": tenant, "id": item_id, "position": order,
                   "name": item["name"], "duration": item["duration_min"],
                   "price": item["price_cents"], "prep": item["prep_min"],
                   "cleanup": item["cleanup_min"]})
        await history(s, tenant, item_id, "rescheduled", context=context, previous=row)
        await record_operation(s, tenant, item_id, "agenda_reschedule", idempotency_key, receipt)
        return await read_appointment(s, tenant, item_id)
