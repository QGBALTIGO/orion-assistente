"""Motor único de reserva: fonte da verdade é o PostgreSQL (FATIA 2).

Nenhum canal externo grava diretamente em appointments. Operações de escrita
recebem tenant já autenticado pelo operador (futuramente por webhook assinado).
"""
import hashlib
import json
import secrets
from datetime import date, datetime, time, timedelta, timezone
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import tenant_transaction

UTC = timezone.utc
PROTOCOL_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
HOLD_TTL = timedelta(minutes=5)


def fail(status: int, message: str):
    raise HTTPException(status, message)


async def acquire_professional_lock(s: AsyncSession, tenant: UUID, professional: UUID):
    # Todas as mutações de agenda adquirem este lock dentro da transação.
    await s.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"),
                    {"scope": str(tenant) + ":" + str(professional)})


async def load_bundle(s: AsyncSession, tenant: UUID, professional: UUID, ids: list[UUID]):
    if not ids or len(ids) > 6 or len(set(ids)) != len(ids):
        fail(422, "Escolha de 1 a 6 serviços diferentes")
    shop = (await s.execute(text("""
        SELECT timezone, slot_step_minutes, min_notice_minutes, booking_horizon_days, status
        FROM tenants WHERE id=:tenant
    """), {"tenant": tenant})).mappings().first()
    if shop is None or shop["status"] != "active":
        fail(404, "Estabelecimento indisponível")
    person = (await s.execute(text("""
        SELECT id FROM professionals WHERE tenant_id=:tenant AND id=:professional AND active
    """), {"tenant": tenant, "professional": professional})).scalar_one_or_none()
    if person is None:
        fail(422, "Profissional indisponível")
    rows = (await s.execute(text("""
        SELECT s.id, s.name, s.duration_min, s.price_cents, s.prep_min, s.cleanup_min
        FROM services s
        JOIN professional_services ps ON ps.tenant_id=s.tenant_id
          AND ps.service_id=s.id AND ps.professional_id=:professional
        WHERE s.tenant_id=:tenant AND s.active AND s.id = ANY(:ids)
    """), {"tenant": tenant, "professional": professional, "ids": ids})).mappings().all()
    found = {r["id"]: dict(r) for r in rows}
    if len(found) != len(ids):
        fail(422, "Serviço inativo ou não realizado pelo profissional")
    ordered = [found[x] for x in ids]
    return dict(shop), ordered


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


async def check_slot(s: AsyncSession, tenant: UUID, professional: UUID,
                     shop: dict, items: list[dict], start: datetime, now: datetime,
                     ignore_appointment: UUID | None = None):
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
    hours = (await s.execute(text("""
        SELECT id, start_time, end_time FROM working_hours
        WHERE tenant_id=:tenant AND professional_id=:professional AND weekday=:weekday
    """), {"tenant": tenant, "professional": professional, "weekday": local.weekday()})).mappings().first()
    if hours is None:
        fail(409, "Profissional não trabalha nesse dia")
    work_start = datetime.combine(local.date(), hours["start_time"], tzinfo=tz).astimezone(UTC)
    work_end = datetime.combine(local.date(), hours["end_time"], tzinfo=tz).astimezone(UTC)
    if b["occupied_from"] < work_start or b["occupied_until"] > work_end:
        fail(409, "Atendimento completo e buffers não cabem no expediente")
    pauses = (await s.execute(text("""
        SELECT start_time, end_time FROM working_breaks
        WHERE tenant_id=:tenant AND working_hour_id=:working_hour
    """), {"tenant": tenant, "working_hour": hours["id"]})).mappings().all()
    for pause in pauses:
        lo = datetime.combine(local.date(), pause["start_time"], tzinfo=tz).astimezone(UTC)
        hi = datetime.combine(local.date(), pause["end_time"], tzinfo=tz).astimezone(UTC)
        if lo < b["occupied_until"] and hi > b["occupied_from"]:
            fail(409, "Horário conflita com pausa do profissional")
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
        UPDATE appointments SET status='cancelled', updated_at=:now
        WHERE tenant_id=:tenant AND professional_id=:professional AND status='hold'
          AND hold_expires_at <= :now RETURNING id
    """), {"tenant": tenant, "professional": professional, "now": now})).scalars().all()
    for item_id in rows:
        await history(s, tenant, item_id, "hold_expired")


async def history(s: AsyncSession, tenant: UUID, appointment: UUID, event: str):
    await s.execute(text("""
        INSERT INTO appointment_history (tenant_id,appointment_id,event,operator)
        VALUES (:tenant,:appointment,:event,'support')
    """), {"tenant": tenant, "appointment": appointment, "event": event})


def protocol():
    return "AG-" + "".join(secrets.choice(PROTOCOL_ALPHABET) for _ in range(6))


def fingerprint(professional: UUID, services: list[UUID], start: datetime, phone: str):
    value = json.dumps({
        "professional": str(professional), "services": [str(x) for x in services],
        "start": start.astimezone(UTC).isoformat(), "phone": phone,
    }, sort_keys=True)
    return hashlib.sha256(value.encode()).hexdigest()


async def read_appointment(s: AsyncSession, tenant: UUID, item_id: UUID):
    row = (await s.execute(text("""
        SELECT a.id, a.tenant_id, a.customer_id, a.professional_id, a.protocol,
               a.start_at, a.end_at, a.status, a.total_cents, a.hold_expires_at,
               c.name AS customer_name
        FROM appointments a JOIN customers c ON c.id=a.customer_id AND c.tenant_id=a.tenant_id
        WHERE a.tenant_id=:tenant AND a.id=:id
    """), {"tenant": tenant, "id": item_id})).mappings().first()
    if not row:
        fail(404, "Agendamento não encontrado")
    items = (await s.execute(text("""
        SELECT service_id, name_snapshot, duration_snapshot_min, price_snapshot_cents,
               prep_snapshot_min, cleanup_snapshot_min
        FROM appointment_items WHERE tenant_id=:tenant AND appointment_id=:id
        ORDER BY item_order
    """), {"tenant": tenant, "id": item_id})).mappings().all()
    return {**dict(row), "items": [dict(x) for x in items]}


async def list_slots(s: AsyncSession, tenant: UUID, professional: UUID,
                     ids: list[UUID], target: date, now: datetime | None = None):
    now = now or datetime.now(UTC)
    async with tenant_transaction(s, tenant):
        shop, items = await load_bundle(s, tenant, professional, ids)
        local_now = now.astimezone(ZoneInfo(shop["timezone"]))
        if target < local_now.date() or target > local_now.date() + timedelta(days=shop["booking_horizon_days"]):
            fail(422, "Data fora do horizonte de agendamento")
        result = []
        for minute in range(0, 24 * 60, shop["slot_step_minutes"]):
            naive = datetime.combine(target, time.min) + timedelta(minutes=minute)
            local = naive.replace(tzinfo=ZoneInfo(shop["timezone"]))
            try:
                # Fornecer offset explícito do próprio fuso na validação.
                start = local.astimezone(UTC).astimezone(ZoneInfo(shop["timezone"]))
                if start.replace(tzinfo=None) != naive:
                    continue  # horário inexistente por mudança de fuso
                await check_slot(s, tenant, professional, shop, items, start, now)
                result.append(start.isoformat())
            except HTTPException:
                continue
        duration, prep, cleanup, price = amounts(items)
        return {"slots": result, "duration_min": duration, "prep_min": prep,
                "cleanup_min": cleanup, "price_cents": price, "timezone": shop["timezone"]}


async def book_appointment(s: AsyncSession, tenant: UUID, professional: UUID,
                           ids: list[UUID], phone: str, customer_name: str,
                           start: datetime, idempotency_key: str,
                           now: datetime | None = None):
    """ÚNICA função para criar reservas/holds de todos os canais futuros."""
    now = now or datetime.now(UTC)
    fp = fingerprint(professional, ids, start, phone)
    async with tenant_transaction(s, tenant):
        await acquire_professional_lock(s, tenant, professional)
        existing = (await s.execute(text("""
            SELECT id, request_fingerprint FROM appointments
            WHERE tenant_id=:tenant AND idempotency_key=:key
        """), {"tenant": tenant, "key": idempotency_key})).mappings().first()
        if existing:
            if existing["request_fingerprint"] != fp:
                fail(409, "Chave de idempotência já usada em outra solicitação")
            return await read_appointment(s, tenant, existing["id"])
        await expire_holds(s, tenant, professional, now)
        shop, items = await load_bundle(s, tenant, professional, ids)
        b = await check_slot(s, tenant, professional, shop, items, start, now)
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
                        "expires": now + HOLD_TTL, **b,
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
        await history(s, tenant, item_id, "hold_created")
        return await read_appointment(s, tenant, item_id)


async def confirm_hold(s: AsyncSession, tenant: UUID, item_id: UUID,
                       now: datetime | None = None):
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
        if hold["status"] == "confirmed":
            return await read_appointment(s, tenant, item_id)  # replay sem duplicação
        if hold["status"] != "hold":
            fail(409, "Esta reserva não pode mais ser confirmada")
        if hold["hold_expires_at"] <= now:
            expired = True
            await s.execute(text("""
                UPDATE appointments SET status='cancelled',updated_at=:now
                WHERE id=:id AND tenant_id=:tenant
            """), {"tenant": tenant, "id": item_id, "now": now})
            await history(s, tenant, item_id, "hold_expired")
        else:
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
            await s.execute(text("""
                UPDATE appointments SET status='confirmed',hold_expires_at=NULL,updated_at=:now
                WHERE id=:id AND tenant_id=:tenant
            """), {"tenant": tenant, "id": item_id, "now": now})
            await history(s, tenant, item_id, "confirmed")
            result = await read_appointment(s, tenant, item_id)
    if expired:
        fail(409, "Hold expirado; selecione outro horário")
    return result


async def cancel_appointment(s: AsyncSession, tenant: UUID, item_id: UUID):
    async with tenant_transaction(s, tenant):
        row = (await s.execute(text("""
            SELECT professional_id FROM appointments WHERE tenant_id=:tenant AND id=:id
        """), {"tenant": tenant, "id": item_id})).mappings().first()
        if not row:
            fail(404, "Agendamento não encontrado")
        await acquire_professional_lock(s, tenant, row["professional_id"])
        entry = (await s.execute(text("""
            SELECT status FROM appointments WHERE tenant_id=:tenant AND id=:id FOR UPDATE
        """), {"tenant": tenant, "id": item_id})).scalar_one()
        if entry == "cancelled":
            return await read_appointment(s, tenant, item_id)
        if entry not in ("confirmed", "hold"):
            fail(409, "Agendamento não pode ser cancelado neste estado")
        await s.execute(text("""
            UPDATE appointments SET status='cancelled', hold_expires_at=NULL,
                updated_at=now() WHERE tenant_id=:tenant AND id=:id
        """), {"tenant": tenant, "id": item_id})
        await history(s, tenant, item_id, "cancelled")
        return await read_appointment(s, tenant, item_id)


async def reschedule_appointment(s: AsyncSession, tenant: UUID, item_id: UUID,
                                 new_start: datetime, now: datetime | None = None):
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
        if row["status"] != "confirmed":
            fail(409, "Somente reservas confirmadas podem ser remarcadas")
        ids = list((await s.execute(text("""
            SELECT service_id FROM appointment_items WHERE tenant_id=:tenant
            AND appointment_id=:id ORDER BY item_order
        """), {"tenant": tenant, "id": item_id})).scalars().all())
        shop, items = await load_bundle(s, tenant, row["professional_id"], ids)
        b = await check_slot(s, tenant, row["professional_id"], shop, items, new_start,
                             now, ignore_appointment=item_id)
        await s.execute(text("""
            UPDATE appointments SET start_at=:start_at,end_at=:end_at,
                occupied_from=:occupied_from,occupied_until=:occupied_until,
                total_cents=:total_cents,updated_at=:now
            WHERE tenant_id=:tenant AND id=:id
        """), {"tenant": tenant, "id": item_id, "now": now, **b})
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
        await history(s, tenant, item_id, "rescheduled")
        return await read_appointment(s, tenant, item_id)
