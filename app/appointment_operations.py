"""Authenticated exceptions and added services on the existing appointment ledger.

Public booking never calls these operations. Each mutation owns a tenant
transaction and takes the same professional lock as the ordinary booking
engine. Original service/price/commission snapshots are append-only here.
"""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import AwareDatetime, Field, StrictBool, StrictInt, field_validator, model_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.appointment_lifecycle import (
    LifecycleActor, check_expected_updated_at, operation_payload,
    operation_replayed, record_operation,
)
from app.booking_service import (
    acquire_professional_lock, amounts, bounds, expire_holds, history,
    load_bundle, protocol, read_appointment, validate_local,
)
from app.catalog_api import audit
from app.database import tenant_transaction
from app.management_schemas import CreateOperation, normalize_phone

UTC = timezone.utc
MAX_ITEMS = 6
STAFF_ROLES = frozenset({"owner", "editor", "professional", "admin", "support"})


class ManualBooking(CreateOperation):
    """Professional contract deliberately has no tenant/customer/professional ID."""
    mode: Literal["exceptional", "retroactive"]
    service_ids: list[UUID] = Field(min_length=1, max_length=MAX_ITEMS)
    customer_name: str = Field(min_length=2, max_length=120)
    customer_phone: str | None = Field(default=None, max_length=40)
    walk_in: StrictBool = False
    start_at: AwareDatetime
    final_total_cents: StrictInt | None = Field(default=None, ge=0, le=100_000_000)
    actual_duration_min: StrictInt | None = Field(default=None, ge=1, le=1440)
    appointment_messages_opt_in: StrictBool = False

    @field_validator("customer_phone")
    @classmethod
    def valid_phone(cls, value):
        return normalize_phone(value)

    @field_validator("service_ids")
    @classmethod
    def distinct_services(cls, values):
        if len(values) != len(set(values)):
            raise ValueError("Escolha serviços diferentes")
        return values

    @model_validator(mode="after")
    def consistent_mode(self):
        if self.walk_in != (self.customer_phone is None):
            raise ValueError("Informe o WhatsApp ou selecione cliente avulso sem telefone")
        if self.mode == "exceptional" and (
                self.final_total_cents is not None or self.actual_duration_min is not None):
            raise ValueError("O valor e a duração realizados pertencem ao registro de atendimento passado")
        if (self.mode == "retroactive" or self.walk_in) and self.appointment_messages_opt_in:
            raise ValueError("Atendimentos passados ou sem telefone não autorizam envio de mensagens")
        return self


class CompanyManualBooking(ManualBooking):
    professional_id: UUID
    customer_id: UUID | None = None


class ExtraServicesRequest(CreateOperation):
    service_ids: list[UUID] = Field(min_length=1, max_length=MAX_ITEMS)
    expected_updated_at: AwareDatetime
    preserve_reserved_time: StrictBool = False
    actual_duration_min: StrictInt | None = Field(default=None, ge=1, le=1440)

    @field_validator("service_ids")
    @classmethod
    def distinct_services(cls, values):
        if len(values) != len(set(values)):
            raise ValueError("Escolha serviços diferentes")
        return values


def require_staff_context(context):
    if (not isinstance(context, LifecycleActor) or context.source != "panel"
            or context.role not in STAFF_ROLES):
        raise HTTPException(403, "Esta operação exige a área autenticada da equipe")
    if context.role not in {"admin", "support"} and not isinstance(context.account_id, UUID):
        raise HTTPException(403, "Identidade da equipe não confirmada")


def validate_manual_period(payload, shop, items, now):
    tz = ZoneInfo(shop["timezone"])
    # A staff API may send an absolute UTC timestamp; interpret it in the
    # company's zone, without imposing the public grid on an exception.
    local = validate_local(payload.start_at.astimezone(tz), tz)
    current_day = now.astimezone(ZoneInfo(shop["timezone"])).date()
    if abs((local.date() - current_day).days) > 366:
        raise HTTPException(422, "Selecione uma data em até 12 meses")
    duration = amounts(items)[0]
    if not 1 <= duration <= 1440:
        raise HTTPException(422, "A duração total deve ser de até 24 horas")
    interval = bounds(payload.start_at, items)
    if payload.mode == "exceptional":
        if interval["start_at"] < now.replace(second=0, microsecond=0):
            raise HTTPException(422, "Para horários passados, use Registrar atendimento realizado")
    else:
        if payload.actual_duration_min is not None:
            interval["end_at"] = interval["start_at"] + timedelta(minutes=payload.actual_duration_min)
            interval["occupied_until"] = interval["end_at"] + timedelta(minutes=amounts(items)[2])
        if interval["end_at"] > now:
            raise HTTPException(422, "O atendimento realizado deve ter terminado antes do horário atual")
    return interval


async def _lock_catalogue(db, tenant, professional, service_ids):
    """Hold catalogue facts through the direct insert/append, after the pro lock."""
    await db.execute(text("""SELECT id FROM professionals
        WHERE tenant_id=:t AND id=:p FOR SHARE"""), {"t": tenant, "p": professional})
    await db.execute(text("""SELECT id FROM services WHERE tenant_id=:t
        AND id=ANY(CAST(:ids AS uuid[])) ORDER BY id FOR SHARE"""),
        {"t": tenant, "ids": service_ids})
    await db.execute(text("""SELECT id FROM professional_services
        WHERE tenant_id=:t AND professional_id=:p
        AND service_id=ANY(CAST(:ids AS uuid[])) ORDER BY service_id FOR SHARE"""),
        {"t": tenant, "p": professional, "ids": service_ids})


async def check_private_conflicts(db, tenant, professional, interval, now, *,
                                  ignored=None, historical=False):
    parameters = {"t": tenant, "p": professional, "begin": interval["occupied_from"],
                  "stop": interval["occupied_until"], "ignored": ignored or UUID(int=0),
                  "now": now}
    for table in ("schedule_blocks", "time_off"):
        found = (await db.execute(text(f"""SELECT 1 FROM {table}
            WHERE tenant_id=:t AND professional_id=:p AND starts_at<:stop
              AND ends_at>:begin LIMIT 1"""), parameters)).first()
        if found:
            raise HTTPException(409, "Este período tem um bloqueio ou uma folga. Revise a agenda antes de continuar")
    statuses = "('hold','confirmed','completed')" if historical else "('hold','confirmed')"
    overlap = (await db.execute(text("""SELECT 1 FROM appointments
        WHERE tenant_id=:t AND professional_id=:p AND id<>:ignored
          AND status IN """ + statuses + """
          AND (status<>'hold' OR hold_expires_at>:now)
          AND occupied_from<:stop AND occupied_until>:begin LIMIT 1"""), parameters)).first()
    if overlap:
        message = ("Já existe um atendimento neste período. Confira o histórico para não registrar o mesmo serviço duas vezes"
                   if historical else "Este período já tem outro agendamento")
        raise HTTPException(409, message)


async def _manual_customer(db, tenant, payload):
    customer_id = getattr(payload, "customer_id", None)
    if customer_id is not None:
        row = (await db.execute(text("""SELECT * FROM customers
            WHERE tenant_id=:t AND id=:id FOR SHARE"""),
            {"t": tenant, "id": customer_id})).mappings().one_or_none()
        if row is None:
            raise HTTPException(404, "Cliente não encontrado nesta empresa")
        if row["whatsapp_e164"] != payload.customer_phone:
            raise HTTPException(409, "O telefone deste cliente mudou. Atualize o cadastro selecionado")
        created = False
    elif payload.customer_phone is not None:
        from app.customer_identity import resolve_customer
        row, created = await resolve_customer(db, tenant, payload.customer_phone, payload.customer_name, lock=True)
    else:
        # NULL is a real walk-in contact. Never forge a telephone or silently
        # merge unrelated people because they happen to share a name.
        row = (await db.execute(text("""INSERT INTO customers
            (tenant_id,name,whatsapp_e164) VALUES (:t,:name,NULL) RETURNING *"""),
            {"t": tenant, "name": payload.customer_name})).mappings().one()
        created = True
    if row.get("deleted_at") is not None or row.get("active") is False:
        raise HTTPException(409, "Este cliente está inativo. Revise o cadastro antes de agendar")
    if created:
        await audit(db, tenant, "management_created", "customers", row["id"])
    return row["id"]


async def _insert_items(db, tenant, appointment, items, *, first_order=0, context=None,
                         extra=False):
    for order, item in enumerate(items, first_order):
        await db.execute(text("""INSERT INTO appointment_items
            (tenant_id,appointment_id,service_id,name_snapshot,duration_snapshot_min,
             price_snapshot_cents,price_mode_snapshot,prep_snapshot_min,cleanup_snapshot_min,
             item_order,item_origin,added_at,added_by_account_id,added_duration_min)
            VALUES (:t,:appointment,:service,:name,:duration,:price,:price_mode,:prep,:cleanup,
                    :position,:origin,CASE WHEN :extra THEN clock_timestamp() ELSE NULL END,
                    :actor,:added_duration)"""),
            {"t": tenant, "appointment": appointment, "service": item["id"],
             "name": item["name"], "duration": item["duration_min"], "price": item["price_cents"],
             "price_mode": item["price_mode"], "prep": item["prep_min"], "cleanup": item["cleanup_min"],
             "position": order, "origin": "extra" if extra else "booked", "extra": extra,
             "actor": context.account_id if extra and context else None,
             "added_duration": item["added_duration_min"] if extra else None})


async def create_manual_appointment(db, tenant, professional, payload, *, context):
    require_staff_context(context)
    request = {**payload.model_dump(exclude={"idempotency_key", "professional_id"}),
               "professional_id": professional, "actor": context.account_id, "role": context.role}
    fingerprint = hashlib.sha256(json.dumps(jsonable_encoder(request), sort_keys=True,
                                separators=(",", ":")).encode()).hexdigest()
    async with tenant_transaction(db, tenant):
        from app.plan_permissions import require_feature
        await require_feature(db, tenant, "agenda.manage")
        await acquire_professional_lock(db, tenant, professional)
        existing = (await db.execute(text("""SELECT id,request_fingerprint FROM appointments
            WHERE tenant_id=:t AND idempotency_key=:key"""),
            {"t": tenant, "key": payload.idempotency_key})).mappings().one_or_none()
        if existing:
            if existing["request_fingerprint"] != fingerprint:
                raise HTTPException(409, "Chave de operação já utilizada com outros dados")
            result = await read_appointment(db, tenant, existing["id"], include_history=True)
            if result["professional_id"] != professional:
                raise HTTPException(404, "Este atendimento não pertence mais à sua agenda")
            return result
        from app.onboarding_service import require_operational_ready
        await require_operational_ready(db, tenant, "appointments")
        await _lock_catalogue(db, tenant, professional, payload.service_ids)
        shop, items = await load_bundle(db, tenant, professional, payload.service_ids)
        now = (await db.execute(text("SELECT clock_timestamp()"))).scalar_one()
        interval = validate_manual_period(payload, shop, items, now)
        retrospective = payload.mode == "retroactive"
        if not retrospective:
            await expire_holds(db, tenant, professional, now)
        await check_private_conflicts(db, tenant, professional, interval, now, historical=retrospective)
        if retrospective:
            if payload.final_total_cents is not None:
                interval["total_cents"] = payload.final_total_cents
            elif any(item["price_mode"] == "from" for item in items):
                raise HTTPException(422, "Informe o valor final do atendimento realizado")
        customer = await _manual_customer(db, tenant, payload)
        appointment = uuid4()
        for attempt in range(5):
            candidate = protocol()
            try:
                async with db.begin_nested():
                    await db.execute(text("""INSERT INTO appointments
                        (id,tenant_id,customer_id,professional_id,protocol,idempotency_key,
                         request_fingerprint,start_at,end_at,occupied_from,occupied_until,status,
                         total_cents,booking_mode,created_by_account_id,actual_duration_min)
                        VALUES (:id,:t,:c,:p,:protocol,:key,:fingerprint,:start_at,:end_at,
                                :occupied_from,:occupied_until,:status,:total_cents,:mode,:actor,:actual)"""),
                        {"id": appointment, "t": tenant, "c": customer, "p": professional,
                         "protocol": candidate, "key": payload.idempotency_key, "fingerprint": fingerprint,
                         "status": "completed" if retrospective else "confirmed", "mode": payload.mode,
                         "actor": context.account_id, "actual": payload.actual_duration_min, **interval})
                break
            except IntegrityError as error:
                if "uq_appointment_protocol" not in str(error):
                    raise
        else:
            raise HTTPException(503, "Não foi possível gerar um protocolo único")
        await _insert_items(db, tenant, appointment, items)
        if retrospective:
            from app.professional_commissions import commission_on_completion
            commission = await commission_on_completion(db, tenant, professional, appointment,
                interval["total_cents"], gross_cents=interval["total_cents"])
            saved = (await db.execute(text("""UPDATE appointments SET
                original_total_cents=total_cents,discount_cents=0,
                commission_mode_snapshot=:mode,commission_percent_snapshot=:percent,
                commission_cents=:commission WHERE tenant_id=:t AND id=:id RETURNING *"""),
                {"t": tenant, "id": appointment, "mode": commission["mode"],
                 "percent": commission["percent"], "commission": commission["commission_cents"]})).mappings().one()
            await history(db, tenant, appointment, "retroactive_created", context=context,
                          calendar=False, notifications=False)
            await history(db, tenant, appointment, "completed", context=context,
                          calendar=False, notifications=False)
            from app.appointment_finance import record_service_receivable
            from app.loyalty_rewards import award_completion
            await record_service_receivable(db, tenant, saved)
            # Historical service credits remain available for redemption, but
            # must not issue a new automatic coupon/message from an old visit.
            await award_completion(db, tenant, saved, now, issue_rewards=False)
        else:
            if payload.appointment_messages_opt_in:
                from app.appointment_consent import grant_scoped_consent, staff_consent_actor
                from app.appointment_messages import APPOINTMENT_MESSAGE_CONSENT_STATEMENT
                consent_actor = staff_consent_actor(context)
                if consent_actor is None:
                    raise HTTPException(403, "A autorização de mensagens exige a conta identificada da equipe")
                await grant_scoped_consent(db, tenant, appointment, source=consent_actor[0],
                    actor=consent_actor[1], evidence=APPOINTMENT_MESSAGE_CONSENT_STATEMENT)
            await history(db, tenant, appointment, "exceptional_created", context=context,
                          calendar=False, notifications=False)
            await history(db, tenant, appointment, "confirmed", context=context)
        await audit(db, tenant, "appointment_" + payload.mode + "_created", "appointments", appointment)
        await db.execute(text("""INSERT INTO usage_counters (tenant_id,period,bookings)
            VALUES (:t,date_trunc('month',CURRENT_DATE)::date,1)
            ON CONFLICT (tenant_id,period) DO UPDATE SET bookings=usage_counters.bookings+1"""), {"t": tenant})
        return await read_appointment(db, tenant, appointment, include_history=True)


async def _appointment_for_extras(db, tenant, appointment, professional=None, *, lock=False):
    if lock:
        identity = (await db.execute(text("""SELECT professional_id FROM appointments
            WHERE tenant_id=:t AND id=:id"""), {"t": tenant, "id": appointment})).scalar_one_or_none()
        if identity is None or professional is not None and identity != professional:
            raise HTTPException(404, "Agendamento não encontrado")
        await acquire_professional_lock(db, tenant, identity)
    row = (await db.execute(text("SELECT * FROM appointments WHERE tenant_id=:t AND id=:id" +
                               (" FOR UPDATE" if lock else "")),
                           {"t": tenant, "id": appointment})).mappings().one_or_none()
    if row is None or professional is not None and row["professional_id"] != professional:
        raise HTTPException(404, "Agendamento não encontrado na sua agenda")
    if lock and identity != row["professional_id"]:
        raise HTTPException(409, "O profissional mudou. Atualize os detalhes")
    return row


async def _components(db, tenant, ids):
    return set((await db.execute(text("""WITH RECURSIVE components(service_id) AS (
        SELECT unnest(CAST(:ids AS uuid[]))
        UNION
        SELECT i.service_id FROM components c
        JOIN service_packages p ON p.tenant_id=:t AND p.synthetic_service_id=c.service_id
        JOIN service_package_items i ON i.tenant_id=p.tenant_id AND i.package_id=p.id
    ) SELECT service_id FROM components"""), {"t": tenant, "ids": ids})).scalars().all())


async def _extra_catalogue(db, tenant, row, *, lock=False):
    current = await read_appointment(db, tenant, row["id"], include_history=True)
    components = await _components(db, tenant, [item["service_id"] for item in current["items"]])
    if lock:
        await db.execute(text("""SELECT base_service_id,addon_service_id FROM service_addons
            WHERE tenant_id=:t AND base_service_id=ANY(CAST(:ids AS uuid[]))
            ORDER BY base_service_id,addon_service_id FOR SHARE"""), {"t": tenant, "ids": list(components)})
    rows = (await db.execute(text("""SELECT s.id,s.name,s.duration_min,s.price_cents,s.price_mode,
            s.prep_min,s.cleanup_min,s.catalog_kind,
            COALESCE(options.recommended,false) AS recommended,
            COALESCE(options.add_duration,true) AS add_duration
        FROM services s JOIN professional_services ps
          ON ps.tenant_id=s.tenant_id AND ps.service_id=s.id
        LEFT JOIN LATERAL (
            SELECT true AS recommended,bool_or(a.add_duration) AS add_duration
            FROM service_addons a WHERE a.tenant_id=s.tenant_id
              AND a.addon_service_id=s.id AND a.base_service_id=ANY(CAST(:ids AS uuid[]))
            HAVING count(*)>0
        ) options ON true
        WHERE s.tenant_id=:t AND ps.professional_id=:p AND s.active
          AND s.catalog_kind<>'variant' AND NOT (s.id=ANY(CAST(:ids AS uuid[])))
        ORDER BY COALESCE(options.recommended,false) DESC,s.name,s.id LIMIT 200"""),
        {"t": tenant, "p": row["professional_id"], "ids": list(components)})).mappings().all()
    return current, [dict(value) for value in rows], components


async def extra_services_catalogue(db, tenant, appointment, *, authorized_professional_id=None):
    async with tenant_transaction(db, tenant):
        from app.plan_permissions import require_feature
        await require_feature(db, tenant, "agenda.manage")
        row = await _appointment_for_extras(db, tenant, appointment, authorized_professional_id)
        if row["status"] != "confirmed":
            raise HTTPException(409, "Adicionais só podem ser registrados em atendimentos confirmados ainda não concluídos")
        current, services, _ = await _extra_catalogue(db, tenant, row)
        return {"appointment": current, "services": services, "max_items": MAX_ITEMS}


def extra_interval(row, current_items, extras):
    duration = sum(item["added_duration_min"] if item.get("added_duration_min") is not None
                   else item["duration_snapshot_min"] for item in current_items)
    extra_minutes = sum(item["added_duration_min"] for item in extras)
    duration += extra_minutes
    if not 1 <= duration <= 1440:
        raise HTTPException(422, "A duração total dos serviços deve ser de até 24 horas")
    # A previous explicit choice to retain the reserved slot remains valid.
    # A later no-time add-on cannot silently reserve that skipped time again.
    end = row["end_at"] + timedelta(minutes=extra_minutes)
    if end - row["start_at"] > timedelta(hours=24):
        raise HTTPException(422, "O horário reservado deve ser de até 24 horas")
    return {"start_at": row["start_at"], "end_at": end,
            "occupied_from": row["occupied_from"] - timedelta(minutes=sum(item["prep_min"] for item in extras)),
            "occupied_until": end + (row["occupied_until"] - row["end_at"]) +
                timedelta(minutes=sum(item["cleanup_min"] for item in extras)),
            "services_duration_min": duration}


async def _extension_conflict(db, tenant, row, interval, shop, now):
    # A no-time add-on must not revalidate/reschedule the existing reservation.
    if all(interval[key] == row[key] for key in ("end_at", "occupied_from", "occupied_until")):
        return None
    if row["booking_mode"] != "exceptional":
        from app.start_policies import open_periods, read_working_day
        tz = ZoneInfo(shop["timezone"])
        local = row["start_at"].astimezone(tz)
        hours, pauses = await read_working_day(db, tenant, row["professional_id"], local.weekday())
        fits = any(datetime.combine(local.date(), start, tzinfo=tz).astimezone(UTC) <= interval["start_at"]
                   and interval["occupied_until"] <= datetime.combine(local.date(), end, tzinfo=tz).astimezone(UTC)
                   for start, end in open_periods(hours, pauses))
        if not fits:
            return "A duração dos serviços ultrapassa o expediente ou uma pausa da agenda"
    try:
        await check_private_conflicts(db, tenant, row["professional_id"], interval, now, ignored=row["id"])
    except HTTPException as error:
        if error.status_code == 409:
            return error.detail
        raise
    return None


async def _extra_plan(db, tenant, row, payload, *, lock=False):
    if row["status"] != "confirmed":
        raise HTTPException(409, "Atendimentos concluídos, cancelados ou não confirmados não aceitam adicionais")
    check_expected_updated_at(row, payload.expected_updated_at)
    if lock:
        await _lock_catalogue(db, tenant, row["professional_id"], payload.service_ids)
    shop, selected = await load_bundle(db, tenant, row["professional_id"], payload.service_ids)
    current, available, components = await _extra_catalogue(db, tenant, row, lock=lock)
    if len(current["items"]) + len(selected) > MAX_ITEMS:
        raise HTTPException(422, "O atendimento pode ter até seis serviços ou combos")
    catalogue = {item["id"]: item for item in available}
    extras = []
    expanded = set(components)
    for item in selected:
        option = catalogue.get(item["id"])
        if option is None:
            raise HTTPException(422, "Escolha um serviço ativo que ainda não esteja neste atendimento")
        identities = await _components(db, tenant, [item["id"]])
        if identities & expanded:
            raise HTTPException(422, "O serviço ou um componente desse combo já está no atendimento")
        expanded.update(identities)
        adds_time = bool(option["add_duration"])
        extras.append({**item, "added_duration_min": item["duration_min"] if adds_time else 0,
                       "prep_min": item["prep_min"] if adds_time else 0,
                       "cleanup_min": item["cleanup_min"] if adds_time else 0})
    total = int(row["total_cents"]) + sum(item["price_cents"] for item in extras)
    if total > 100_000_000:
        raise HTTPException(422, "O total do atendimento ultrapassa o limite permitido")
    now = (await db.execute(text("SELECT clock_timestamp()"))).scalar_one()
    if payload.actual_duration_min is not None and row["start_at"] > now:
        raise HTTPException(422, "A duração realizada só pode ser informada depois do início do atendimento")
    interval = extra_interval(row, current["items"], extras)
    conflict = await _extension_conflict(db, tenant, row, interval, shop, now)
    plan = {"appointment": current, "total_cents": total,
            "services_duration_min": interval["services_duration_min"],
            "reserved_duration_min": int((row["end_at"] - row["start_at"]).total_seconds() // 60),
            "new_end_at": interval["end_at"], "duration_conflict": conflict is not None,
            "conflict_message": conflict}
    return plan, extras, interval, now


async def preview_extra_services(db, tenant, appointment, payload, *, authorized_professional_id=None):
    async with tenant_transaction(db, tenant):
        from app.plan_permissions import require_feature
        await require_feature(db, tenant, "agenda.manage")
        row = await _appointment_for_extras(db, tenant, appointment, authorized_professional_id)
        plan, _, _, _ = await _extra_plan(db, tenant, row, payload)
        return plan


async def add_extra_services(db, tenant, appointment, payload, *, context,
                             authorized_professional_id=None):
    require_staff_context(context)
    async with tenant_transaction(db, tenant):
        from app.plan_permissions import require_feature
        await require_feature(db, tenant, "agenda.manage")
        row = await _appointment_for_extras(db, tenant, appointment, authorized_professional_id, lock=True)
        receipt = operation_payload(appointment, payload.expected_updated_at, context,
            **payload.model_dump(exclude={"idempotency_key", "expected_updated_at"}))
        if await operation_replayed(db, tenant, "agenda_add_services", payload.idempotency_key, receipt):
            return await read_appointment(db, tenant, appointment, include_history=True)
        plan, extras, interval, now = await _extra_plan(db, tenant, row, payload, lock=True)
        if plan["duration_conflict"] and not payload.preserve_reserved_time:
            raise HTTPException(409, {"code": "extra_duration_conflict",
                "message": plan["conflict_message"] + ". Confirme se deseja registrar os serviços mantendo o horário reservado.",
                "services_duration_min": plan["services_duration_min"],
                "reserved_duration_min": plan["reserved_duration_min"]})
        if payload.preserve_reserved_time:
            interval = {**interval, **{key: row[key] for key in ("end_at", "occupied_from", "occupied_until")}}
        else:
            # Expired holds no longer block the GiST write. This runs only on a
            # committed mutation, never on the read-only preview.
            await expire_holds(db, tenant, row["professional_id"], now)
        changed = (await db.execute(text("""UPDATE appointments SET total_cents=:total,
            end_at=:end_at,occupied_from=:occupied_from,occupied_until=:occupied_until,
            actual_duration_min=COALESCE(CAST(:actual AS integer),actual_duration_min),
            updated_at=GREATEST(clock_timestamp(),updated_at+interval '1 microsecond')
            WHERE tenant_id=:t AND id=:id AND status='confirmed' AND updated_at=:expected
            RETURNING updated_at"""), {"t": tenant, "id": appointment, "total": plan["total_cents"],
            "end_at": interval["end_at"], "occupied_from": interval["occupied_from"],
            "occupied_until": interval["occupied_until"], "actual": payload.actual_duration_min,
            "expected": payload.expected_updated_at})).scalar_one_or_none()
        if changed is None:
            raise HTTPException(409, "O atendimento mudou. Atualize os detalhes antes de continuar")
        next_order = (await db.execute(text("""SELECT COALESCE(max(item_order),-1)+1
            FROM appointment_items WHERE tenant_id=:t AND appointment_id=:id"""),
            {"t": tenant, "id": appointment})).scalar_one()
        await _insert_items(db, tenant, appointment, extras, first_order=next_order, context=context, extra=True)
        await db.execute(text("""UPDATE appointment_change_proposals SET status='superseded'
            WHERE tenant_id=:t AND appointment_id=:id AND status='pending'"""), {"t": tenant, "id": appointment})
        note = ("Horário reservado mantido por escolha da equipe. " if payload.preserve_reserved_time else "")
        note += "Serviços adicionados: " + ", ".join(item["name"] for item in extras)
        await history(db, tenant, appointment, "services_added", context=context, previous=row,
                      reason=note[:1000], calendar=False)
        if interval["end_at"] > now:
            from app.calendar_sync import enqueue_calendar_sync
            await enqueue_calendar_sync(db, tenant, appointment, "rescheduled")
        await audit(db, tenant, "appointment_services_added", "appointments", appointment)
        await record_operation(db, tenant, appointment, "agenda_add_services", payload.idempotency_key, receipt)
        return await read_appointment(db, tenant, appointment, include_history=True)
