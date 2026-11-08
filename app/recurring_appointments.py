"""Agendamento de clientes fixos usando o mesmo motor e agenda do Orion.

Cada visita é uma reserva confirmada real. Criação e encerramento usam transações
únicas, lock por profissional e a exclusão de sobreposição do PostgreSQL.
"""
import hashlib
import json
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field, field_validator, model_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.appointment_lifecycle import panel_actor
from app.booking_service import (
    acquire_professional_lock, amounts, check_slot, expire_holds, fingerprint,
    history, load_bundle, protocol,
)
from app.catalog_api import Actor, audit, authenticate, authorize
from app.catalog_schemas import StrictModel
from app.customer_identity import resolve_customer
from app.database import get_session_factory, tenant_transaction
from app.http_json import json_response
from app.onboarding_service import require_operational_ready
from app.plan_permissions import require_feature

router = APIRouter(prefix="/api/v1/tenants/{tenant_id}/appointments/series",
                   tags=["Agenda — clientes fixos"])
UTC = timezone.utc
PHONE = r"^(?:\+55[1-9][0-9]{9,10}|\+5959[0-9]{8})$"
WEEKDAYS = ("segunda-feira", "terça-feira", "quarta-feira",
            "quinta-feira", "sexta-feira", "sábado", "domingo")


class SeriesDraft(StrictModel):
    professional_id: UUID
    service_ids: list[UUID] = Field(min_length=1, max_length=6)
    customer_name: str = Field(min_length=1, max_length=120)
    customer_phone: str = Field(pattern=PHONE)
    first_date: date
    weekday: int = Field(ge=0, le=6)
    local_time: time
    interval_weeks: Literal[1, 2, 4] = 1
    occurrence_count: int = Field(ge=2, le=52)

    @field_validator("service_ids")
    @classmethod
    def unique_services(cls, values):
        if len(values) != len(set(values)):
            raise ValueError("Escolha serviços diferentes")
        return values

    @field_validator("local_time")
    @classmethod
    def plain_clock(cls, value):
        if value.tzinfo is not None or value.second or value.microsecond:
            raise ValueError("Informe horário local em horas e minutos")
        return value

    @model_validator(mode="after")
    def first_matches_weekday(self):
        if self.first_date.weekday() != self.weekday:
            raise ValueError("A primeira data deve corresponder ao dia da semana selecionado")
        return self


class SeriesCreate(SeriesDraft):
    idempotency_key: str = Field(min_length=8, max_length=120,
                                 pattern=r"^[a-zA-Z0-9._:-]+$")


class SeriesStop(StrictModel):
    from_date: date | None = None


def series_days(draft: SeriesDraft, tz_name: str, now: datetime):
    """Contagem inclui a primeira data; não reservar indefinidamente nem além de um ano."""
    zone = ZoneInfo(tz_name)
    today = now.astimezone(zone).date()
    end = draft.first_date + timedelta(weeks=draft.interval_weeks * (draft.occurrence_count - 1))
    if draft.first_date < today or end > today + timedelta(days=365):
        raise HTTPException(422, "Selecione datas futuras dentro dos próximos 365 dias")
    dates = []
    for i in range(draft.occurrence_count):
        day = draft.first_date + timedelta(weeks=draft.interval_weeks * i)
        naive = datetime.combine(day, draft.local_time)
        instant = naive.replace(tzinfo=zone, fold=0)
        if (instant.utcoffset() != naive.replace(tzinfo=zone, fold=1).utcoffset()
                or instant.astimezone(UTC).astimezone(zone).replace(tzinfo=None) != naive):
            raise HTTPException(422, f"Horário local inexistente ou ambíguo em {day.isoformat()}")
        dates.append((i + 1, day, instant))
    return dates


async def _series_detail(db, tenant, identity):
    record = (await db.execute(text("""
        SELECT s.id,s.customer_id,s.professional_id,s.service_ids,s.services_label,
               s.first_date,s.weekday,s.local_time,s.interval_weeks,s.occurrence_count,
               s.timezone,s.created_at,s.closed_at,
               c.name AS customer_name,c.whatsapp_e164 AS customer_phone,
               p.name AS professional_name
        FROM appointment_series s
        JOIN customers c ON c.tenant_id=s.tenant_id AND c.id=s.customer_id
        JOIN professionals p ON p.tenant_id=s.tenant_id AND p.id=s.professional_id
        WHERE s.tenant_id=:tenant AND s.id=:id
    """), {"tenant": tenant, "id": identity})).mappings().first()
    if record is None:
        raise HTTPException(404, "Cliente fixo não encontrado nesta empresa")
    rows = (await db.execute(text("""
        SELECT id,series_index,protocol,start_at,end_at,status,total_cents,updated_at
        FROM appointments WHERE tenant_id=:tenant AND series_id=:id
        ORDER BY series_index
    """), {"tenant": tenant, "id": identity})).mappings().all()
    upcoming = [r for r in rows if r["status"] == "confirmed" and r["start_at"] > datetime.now(UTC)]
    return {**dict(record),
            "weekday_label": WEEKDAYS[record["weekday"]],
            "appointments": [dict(row) for row in rows],
            "confirmed_count": sum(r["status"] == "confirmed" for r in rows),
            "cancelled_count": sum(r["status"] == "cancelled" for r in rows),
            "next_start_at": upcoming[0]["start_at"] if upcoming else None}


async def _check_occurrences(db, tenant, draft, now, *, collect=False):
    shop, items = await load_bundle(db, tenant, draft.professional_id, draft.service_ids)
    dates = series_days(draft, shop["timezone"], now)
    duration, prep, cleanup, price = amounts(items)
    # A recorrência é um acordo manual da empresa. O horizonte do site público
    # continua intacto; o limite administrativo de um ano é aplicado acima.
    staff_shop = {**shop, "booking_horizon_days": 365}
    result = []
    errors = []
    for index, day, start in dates:
        try:
            bounds = await check_slot(db, tenant, draft.professional_id, staff_shop, items, start, now)
            result.append({"index": index, "day": day, "start_at": start, "available": True,
                           "end_at": bounds["end_at"], "total_cents": price, "bounds": bounds})
        except HTTPException as exc:
            if not collect:
                raise HTTPException(409, f"{day.isoformat()}: {exc.detail}. Nada foi reservado") from exc
            errors.append({"index": index, "day": day, "start_at": start,
                           "available": False, "reason": str(exc.detail)})
    return shop, items, result, errors, (duration, prep, cleanup, price)


@router.post("/preview")
async def preview_series(tenant_id: UUID, payload: SeriesDraft,
                         actor: Actor = Depends(authenticate)):
    authorize(actor, tenant_id, write=True)
    async with get_session_factory()() as db:
        async with tenant_transaction(db, tenant_id):
            await require_feature(db, tenant_id, "agenda.manage")
            await require_operational_ready(db, tenant_id, "appointments")
            now = datetime.now(UTC)
            shop, items, available, conflicts, totals = await _check_occurrences(
                db, tenant_id, payload, now, collect=True)
            occurrences = [
                {key: value for key, value in row.items() if key != "bounds"}
                for row in available
            ] + conflicts
            occurrences.sort(key=lambda row: row["index"])
            return json_response({
                "timezone": shop["timezone"],
                "count": payload.occurrence_count,
                "available_count": len(available),
                "conflict_count": len(conflicts),
                "duration_min": totals[0], "prep_min": totals[1], "cleanup_min": totals[2],
                "price_cents": totals[3],
                "services": [{"id": item["id"], "name": item["name"]} for item in items],
                "occurrences": occurrences,
                "can_create": not conflicts,
            })


@router.post("", status_code=201)
async def create_series(tenant_id: UUID, payload: SeriesCreate,
                        actor: Actor = Depends(authenticate)):
    authorize(actor, tenant_id, write=True)
    digest = hashlib.sha256(json.dumps(
        payload.model_dump(mode="json", exclude={"idempotency_key"}),
        sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    async with get_session_factory()() as db:
        try:
            async with tenant_transaction(db, tenant_id):
                await require_feature(db, tenant_id, "agenda.manage")
                await acquire_professional_lock(db, tenant_id, payload.professional_id)
                prior = (await db.execute(text("""
                    SELECT id,request_fingerprint FROM appointment_series
                    WHERE tenant_id=:tenant AND idempotency_key=:key
                """), {"tenant": tenant_id, "key": payload.idempotency_key})).mappings().first()
                if prior:
                    if prior["request_fingerprint"] != digest:
                        raise HTTPException(409, "Esta chave já foi usada com outros dados")
                    return json_response({**await _series_detail(db, tenant_id, prior["id"]),
                                          "replayed": True})
                await require_operational_ready(db, tenant_id, "appointments")
                now = datetime.now(UTC)
                await expire_holds(db, tenant_id, payload.professional_id, now)
                shop, items, proposed, _, totals = await _check_occurrences(
                    db, tenant_id, payload, now)
                customer, _ = await resolve_customer(
                    db, tenant_id, payload.customer_phone, payload.customer_name)
                identity = uuid4()
                await db.execute(text("""
                    INSERT INTO appointment_series(
                        id,tenant_id,customer_id,professional_id,service_ids,services_label,
                        first_date,weekday,local_time,interval_weeks,occurrence_count,
                        timezone,idempotency_key,request_fingerprint)
                    VALUES(:id,:tenant,:customer,:professional,:services,:label,
                        :first_date,:weekday,:local_time,:interval,:count,:timezone,:key,:fp)
                """), {
                    "id": identity, "tenant": tenant_id, "customer": customer["id"],
                    "professional": payload.professional_id, "services": payload.service_ids,
                    "label": " + ".join(item["name"] for item in items)[:750],
                    "first_date": payload.first_date, "weekday": payload.weekday,
                    "local_time": payload.local_time, "interval": payload.interval_weeks,
                    "count": payload.occurrence_count, "timezone": shop["timezone"],
                    "key": payload.idempotency_key, "fp": digest,
                })
                for occurrence in proposed:
                    index = occurrence["index"]
                    item_id = uuid4()
                    # UUID scoped idempotency is independent from the frontend key.
                    child_key = f"fixed:{identity}:{index:02d}"
                    for attempt in range(5):
                        try:
                            async with db.begin_nested():
                                await db.execute(text("""
                                    INSERT INTO appointments(
                                        id,tenant_id,customer_id,professional_id,protocol,
                                        idempotency_key,request_fingerprint,start_at,end_at,
                                        occupied_from,occupied_until,status,hold_expires_at,
                                        total_cents,series_id,series_index)
                                    VALUES(:id,:tenant,:customer,:professional,:protocol,
                                        :key,:fp,:start_at,:end_at,:occupied_from,:occupied_until,
                                        'confirmed',NULL,:total_cents,:series_id,:series_index)
                                """), {
                                    "id": item_id, "tenant": tenant_id, "customer": customer["id"],
                                    "professional": payload.professional_id, "protocol": protocol(),
                                    "key": child_key,
                                    "fp": fingerprint(payload.professional_id, payload.service_ids,
                                                      occurrence["start_at"], payload.customer_phone),
                                    "series_id": identity, "series_index": index,
                                    **occurrence["bounds"],
                                })
                            break
                        except IntegrityError as exc:
                            if "uq_appointment_protocol" not in str(exc):
                                raise
                    else:
                        raise HTTPException(503, "Não foi possível criar um protocolo de agendamento")
                    for order, service in enumerate(items):
                        await db.execute(text("""
                            INSERT INTO appointment_items(
                                tenant_id,appointment_id,service_id,name_snapshot,
                                duration_snapshot_min,price_snapshot_cents,price_mode_snapshot,
                                prep_snapshot_min,cleanup_snapshot_min,item_order)
                            VALUES(:tenant,:appointment,:service,:name,
                                :duration,:price,:price_mode,:prep,:cleanup,:item_order)
                        """), {
                            "tenant": tenant_id, "appointment": item_id,
                            "service": service["id"], "name": service["name"],
                            "duration": service["duration_min"], "price": service["price_cents"],
                            "price_mode": service["price_mode"], "prep": service["prep_min"],
                            "cleanup": service["cleanup_min"], "item_order": order,
                        })
                    # Um único aviso inicial ao profissional; lembretes individuais seguem ativos.
                    await history(db, tenant_id, item_id, "confirmed",
                                  context=panel_actor(actor), bulk=index > 1)
                await db.execute(text("""
                    INSERT INTO usage_counters(tenant_id,period,bookings)
                    VALUES(:tenant,date_trunc('month',CURRENT_DATE)::date,:count)
                    ON CONFLICT(tenant_id,period) DO UPDATE
                    SET bookings=usage_counters.bookings+EXCLUDED.bookings
                """), {"tenant": tenant_id, "count": payload.occurrence_count})
                await audit(db, tenant_id, "appointment_series_created",
                            "appointment_series", identity)
                return json_response({**await _series_detail(db, tenant_id, identity),
                                      "replayed": False}, status_code=201)
        except IntegrityError as exc:
            raise HTTPException(409, "Um dos horários ficou indisponível. Nenhum agendamento foi criado") from exc


@router.get("")
async def list_series(tenant_id: UUID, limit: int = Query(default=100, ge=1, le=200),
                      actor: Actor = Depends(authenticate)):
    authorize(actor, tenant_id)
    async with get_session_factory()() as db:
        async with tenant_transaction(db, tenant_id):
            identities = (await db.execute(text("""
                SELECT id FROM appointment_series WHERE tenant_id=:tenant
                ORDER BY (closed_at IS NULL) DESC,created_at DESC,id DESC LIMIT :limit
            """), {"tenant": tenant_id, "limit": limit})).scalars().all()
            return json_response({"items": [await _series_detail(db, tenant_id, identity)
                                            for identity in identities]})


@router.get("/{series_id}")
async def read_series(tenant_id: UUID, series_id: UUID,
                      actor: Actor = Depends(authenticate)):
    authorize(actor, tenant_id)
    async with get_session_factory()() as db:
        async with tenant_transaction(db, tenant_id):
            return json_response(await _series_detail(db, tenant_id, series_id))


@router.post("/{series_id}/stop")
async def stop_series(tenant_id: UUID, series_id: UUID, payload: SeriesStop,
                      actor: Actor = Depends(authenticate)):
    authorize(actor, tenant_id, write=True)
    async with get_session_factory()() as db:
        async with tenant_transaction(db, tenant_id):
            await require_feature(db, tenant_id, "agenda.manage")
            row = (await db.execute(text("""
                SELECT professional_id FROM appointment_series
                WHERE tenant_id=:tenant AND id=:id
            """), {"tenant": tenant_id, "id": series_id})).mappings().first()
            if row is None:
                raise HTTPException(404, "Cliente fixo não encontrado")
            await acquire_professional_lock(db, tenant_id, row["professional_id"])
            now = datetime.now(UTC)
            future = (await db.execute(text("""
                SELECT a.* FROM appointments a
                WHERE a.tenant_id=:tenant AND a.series_id=:series
                  AND a.status='confirmed' AND a.start_at>:now
                  AND (CAST(:from_date AS date) IS NULL OR
                       (a.start_at AT TIME ZONE
                         (SELECT timezone FROM appointment_series
                          WHERE tenant_id=:tenant AND id=:series))::date >= CAST(:from_date AS date))
                ORDER BY a.start_at FOR UPDATE
            """), {"tenant": tenant_id, "series": series_id,
                   "now": now, "from_date": payload.from_date})).mappings().all()
            for entry in future:
                await db.execute(text("""
                    UPDATE appointments SET status='cancelled',hold_expires_at=NULL,
                        updated_at=GREATEST(clock_timestamp(),updated_at+interval '1 microsecond')
                    WHERE tenant_id=:tenant AND id=:id AND status='confirmed'
                """), {"tenant": tenant_id, "id": entry["id"]})
                await history(db, tenant_id, entry["id"], "cancelled",
                              context=panel_actor(actor), requested_by="business",
                              reason="Encerramento do agendamento fixo",
                              previous=entry, bulk=True)
            remaining = (await db.execute(text("""
                SELECT 1 FROM appointments WHERE tenant_id=:tenant AND series_id=:id
                  AND status='confirmed' AND start_at>clock_timestamp() LIMIT 1
            """), {"tenant": tenant_id, "id": series_id})).first()
            if not remaining:
                await db.execute(text("""
                    UPDATE appointment_series SET closed_at=COALESCE(closed_at,clock_timestamp())
                    WHERE tenant_id=:tenant AND id=:id
                """), {"tenant": tenant_id, "id": series_id})
            if future:
                await audit(db, tenant_id, "appointment_series_stopped",
                            "appointment_series", series_id)
            return json_response({"series": await _series_detail(db, tenant_id, series_id),
                                  "cancelled_count": len(future)})
