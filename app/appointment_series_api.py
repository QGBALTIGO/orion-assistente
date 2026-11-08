"""Séries finitas de clientes fixos, sempre usando o motor e a transação da Agenda.

Não há lançamentos financeiros antecipados nem reservas paralelas. Cada ocorrência
é um agendamento confirmado normal, com histórico, notificações e Google Calendar.
"""
import hashlib
import json
from datetime import date, datetime, time, timedelta, timezone
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field, model_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.appointment_lifecycle import panel_actor
from app.booking_service import (
    acquire_professional_lock, amounts, bounds, check_slot, expire_holds,
    history, load_bundle, protocol,
)
from app.catalog_api import Actor, audit, authenticate, authorize
from app.catalog_schemas import StrictModel
from app.customer_identity import resolve_customer
from app.database import get_session_factory, tenant_transaction
from app.http_json import json_response
from app.onboarding_service import require_operational_ready
from app.plan_permissions import require_feature

router = APIRouter(prefix="/api/v1/tenants/{tenant_id}/appointment-series",
                   tags=["Agenda — clientes fixos"])
UTC = timezone.utc
MAX_HORIZON_DAYS = 365
MAX_OCCURRENCES = 52


class FixedSeriesRequest(StrictModel):
    customer_name: str = Field(min_length=1, max_length=120)
    customer_phone: str = Field(pattern=r"^(?:\+55[1-9][0-9]{9,10}|\+5959[0-9]{8})$")
    professional_id: UUID
    service_ids: list[UUID] = Field(min_length=1, max_length=6)
    first_date: date
    weekday: int = Field(ge=0, le=6)  # Python: segunda=0, domingo=6
    local_time: time
    interval_weeks: int = Field(default=1)
    occurrence_count: int = Field(ge=2, le=MAX_OCCURRENCES)
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=120,
                                         pattern=r"^[a-zA-Z0-9._:-]+$")

    @model_validator(mode="after")
    def valid_recurrence(self):
        if self.interval_weeks not in (1, 2, 4):
            raise ValueError("Use repetição semanal, quinzenal ou a cada quatro semanas")
        if self.first_date.year < 2020 or self.first_date.year > 2100:
            raise ValueError("Selecione uma primeira data válida")
        if self.first_date.weekday() != self.weekday:
            raise ValueError("A primeira data deve corresponder ao dia da semana escolhido")
        if self.local_time.tzinfo is not None or self.local_time.second or self.local_time.microsecond:
            raise ValueError("Informe somente hora e minuto locais do estabelecimento")
        if len(set(self.service_ids)) != len(self.service_ids):
            raise ValueError("Não repita o mesmo serviço")
        return self


class CancelFutureRequest(StrictModel):
    from_index: int = Field(ge=1, le=MAX_OCCURRENCES)
    reason: str | None = Field(default=None, max_length=500)


def series_dates(payload: FixedSeriesRequest) -> list[date]:
    """Calendar-day arithmetic is DST-safe; never add 24-hour UTC intervals."""
    return [payload.first_date + timedelta(weeks=payload.interval_weeks * i)
            for i in range(payload.occurrence_count)]


def local_datetime(day: date, hour: time, zone: ZoneInfo) -> datetime:
    naive = datetime.combine(day, hour)
    local = naive.replace(tzinfo=zone, fold=0)
    if (local.utcoffset() != local.replace(fold=1).utcoffset()
            or local.astimezone(UTC).astimezone(zone).replace(tzinfo=None) != naive):
        raise HTTPException(422, f"Horário inexistente ou ambíguo em {day:%d/%m/%Y}")
    return local


def validate_horizon(days: list[date], now: datetime, zone: ZoneInfo):
    today = now.astimezone(zone).date()
    if days[0] < today or days[-1] > today + timedelta(days=MAX_HORIZON_DAYS):
        raise HTTPException(422, "Cliente fixo: escolha datas futuras dentro dos próximos 365 dias")


def request_fingerprint(payload: FixedSeriesRequest) -> str:
    body = payload.model_dump(mode="json", exclude={"idempotency_key"})
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


async def check_occurrences(db, tenant_id, payload, shop, items, *, now):
    zone = ZoneInfo(shop["timezone"])
    dates = series_dates(payload)
    validate_horizon(dates, now, zone)
    result = []
    for day in dates:
        start = local_datetime(day, payload.local_time, zone)
        try:
            await check_slot(db, tenant_id, payload.professional_id, shop, items, start, now,
                             max_horizon_days=MAX_HORIZON_DAYS)
            result.append({"day": day, "start_at": start, "available": True})
        except HTTPException as exc:
            if exc.status_code not in (409, 422):
                raise
            result.append({"day": day, "start_at": start,
                           "available": False, "reason": str(exc.detail)})
    return result


async def series_detail(db, tenant_id, series_id):
    row = (await db.execute(text("""
        SELECT s.*, c.name AS customer_name, c.whatsapp_e164 AS customer_phone,
               p.name AS professional_name
        FROM appointment_series s
        JOIN customers c ON c.tenant_id=s.tenant_id AND c.id=s.customer_id
        JOIN professionals p ON p.tenant_id=s.tenant_id AND p.id=s.professional_id
        WHERE s.tenant_id=:tenant AND s.id=:id
    """), {"tenant": tenant_id, "id": series_id})).mappings().one_or_none()
    if row is None:
        raise HTTPException(404, "Cliente fixo não encontrado nesta empresa")
    appointments = (await db.execute(text("""
        SELECT id,series_index,start_at,end_at,status,protocol,total_cents,updated_at
        FROM appointments WHERE tenant_id=:tenant AND series_id=:id
        ORDER BY series_index
    """), {"tenant": tenant_id, "id": series_id})).mappings().all()
    return {key: value for key, value in row.items() if key not in
            ("tenant_id", "request_fingerprint", "idempotency_key")} | {
                "appointments": [dict(a) for a in appointments],
                "confirmed_future": sum(a["status"] == "confirmed" and a["start_at"] > datetime.now(UTC)
                                        for a in appointments),
            }


@router.get("")
async def list_fixed_clients(tenant_id: UUID, limit: int = Query(default=100, ge=1, le=200),
                             actor: Actor = Depends(authenticate)):
    authorize(actor, tenant_id)
    async with get_session_factory()() as db:
        async with tenant_transaction(db, tenant_id):
            rows = (await db.execute(text("""
                SELECT s.id,s.customer_id,s.professional_id,s.services_label,s.first_date,
                       s.weekday,s.local_time,s.interval_weeks,s.occurrence_count,s.timezone,
                       s.created_at,s.closed_at,c.name AS customer_name,
                       p.name AS professional_name,
                       (SELECT count(*) FROM appointments a
                        WHERE a.tenant_id=s.tenant_id AND a.series_id=s.id
                          AND a.status='confirmed' AND a.start_at>clock_timestamp()) AS confirmed_future
                FROM appointment_series s
                JOIN customers c ON c.tenant_id=s.tenant_id AND c.id=s.customer_id
                JOIN professionals p ON p.tenant_id=s.tenant_id AND p.id=s.professional_id
                WHERE s.tenant_id=:tenant ORDER BY s.created_at DESC,s.id DESC LIMIT :limit
            """), {"tenant": tenant_id, "limit": limit})).mappings().all()
            return json_response({"items": [dict(row) for row in rows]})


@router.get("/{series_id}")
async def get_fixed_client(tenant_id: UUID, series_id: UUID,
                           actor: Actor = Depends(authenticate)):
    authorize(actor, tenant_id)
    async with get_session_factory()() as db:
        async with tenant_transaction(db, tenant_id):
            return json_response(await series_detail(db, tenant_id, series_id))


@router.post("/preview")
async def preview_fixed_client(tenant_id: UUID, payload: FixedSeriesRequest,
                               actor: Actor = Depends(authenticate)):
    authorize(actor, tenant_id, write=True)
    async with get_session_factory()() as db:
        async with tenant_transaction(db, tenant_id):
            await require_feature(db, tenant_id, "agenda.manage")
            shop, items = await load_bundle(db, tenant_id, payload.professional_id, payload.service_ids)
            result = await check_occurrences(db, tenant_id, payload, shop, items, now=datetime.now(UTC))
            duration, prep, cleanup, price = amounts(items)
            return json_response({
                "occurrences": result, "all_available": all(row["available"] for row in result),
                "timezone": shop["timezone"], "duration_min": duration,
                "prep_min": prep, "cleanup_min": cleanup, "price_cents": price,
                "price_mode": "from" if any(item["price_mode"] == "from" for item in items) else "fixed",
            })


@router.post("", status_code=201)
async def create_fixed_client(tenant_id: UUID, payload: FixedSeriesRequest,
                              actor: Actor = Depends(authenticate)):
    authorize(actor, tenant_id, write=True)
    if not payload.idempotency_key:
        raise HTTPException(422, "Chave de confirmação obrigatória")
    fp = request_fingerprint(payload)
    try:
        async with get_session_factory()() as db:
            async with tenant_transaction(db, tenant_id):
                await require_feature(db, tenant_id, "agenda.manage")
                await acquire_professional_lock(db, tenant_id, payload.professional_id)
                existing = (await db.execute(text("""
                    SELECT id,request_fingerprint FROM appointment_series
                    WHERE tenant_id=:tenant AND idempotency_key=:key
                """), {"tenant": tenant_id, "key": payload.idempotency_key})).mappings().first()
                if existing:
                    if existing["request_fingerprint"] != fp:
                        raise HTTPException(409, "Esta confirmação já foi utilizada com outros dados")
                    return json_response({**await series_detail(db, tenant_id, existing["id"]), "replayed": True})
                await require_operational_ready(db, tenant_id, "appointments")
                now = datetime.now(UTC)
                await expire_holds(db, tenant_id, payload.professional_id, now)
                shop, items = await load_bundle(db, tenant_id, payload.professional_id, payload.service_ids)
                reviewed = await check_occurrences(db, tenant_id, payload, shop, items, now=now)
                unavailable = [row for row in reviewed if not row["available"]]
                if unavailable:
                    dates = ", ".join(row["day"].strftime("%d/%m/%Y") for row in unavailable[:5])
                    raise HTTPException(409, f"{len(unavailable)} horário(s) indisponível(is): {dates}. Nenhuma reserva foi criada.")
                customer, _ = await resolve_customer(db, tenant_id, payload.customer_phone, payload.customer_name)
                customer_id = customer["id"]
                series_id = uuid4()
                names = " + ".join(item["name"] for item in items)
                await db.execute(text("""
                    INSERT INTO appointment_series
                      (id,tenant_id,customer_id,professional_id,service_ids,services_label,first_date,
                       weekday,local_time,interval_weeks,occurrence_count,timezone,idempotency_key,request_fingerprint)
                    VALUES (:id,:tenant,:customer,:professional,:services,:label,:first,:weekday,:hour,
                            :interval,:count,:tz,:key,:fp)
                """), {"id": series_id, "tenant": tenant_id, "customer": customer_id,
                       "professional": payload.professional_id, "services": payload.service_ids,
                       "label": names, "first": payload.first_date, "weekday": payload.weekday,
                       "hour": payload.local_time, "interval": payload.interval_weeks,
                       "count": payload.occurrence_count, "tz": shop["timezone"],
                       "key": payload.idempotency_key, "fp": fp})
                for idx, entry in enumerate(reviewed, start=1):
                    # Revalidation under the existing professional advisory lock.
                    start = entry["start_at"]
                    slot = await check_slot(db, tenant_id, payload.professional_id, shop, items,
                                            start, now, max_horizon_days=MAX_HORIZON_DAYS)
                    appointment_id = uuid4()
                    for attempt in range(5):
                        try:
                            async with db.begin_nested():
                                await db.execute(text("""
                                    INSERT INTO appointments
                                      (id,tenant_id,customer_id,professional_id,protocol,
                                       idempotency_key,request_fingerprint,start_at,end_at,
                                       occupied_from,occupied_until,total_cents,status,hold_expires_at,
                                       series_id,series_index)
                                    VALUES (:id,:tenant,:customer,:professional,:protocol,:key,:fp,
                                            :start_at,:end_at,:occupied_from,:occupied_until,:total_cents,
                                            'confirmed',NULL,:series,:idx)
                                """), {"id": appointment_id, "tenant": tenant_id, "customer": customer_id,
                                       "professional": payload.professional_id, "protocol": protocol(),
                                       "key": f"fixed:{series_id}:{idx}",
                                       "fp": hashlib.sha256(f"{fp}:{idx}".encode()).hexdigest(),
                                       "series": series_id, "idx": idx, **slot})
                            break
                        except IntegrityError as exc:
                            if "uq_appointment_protocol" not in str(exc):
                                raise
                    else:
                        raise HTTPException(503, "Não foi possível gerar protocolos únicos")
                    for order, item in enumerate(items):
                        await db.execute(text("""
                            INSERT INTO appointment_items
                              (tenant_id,appointment_id,service_id,name_snapshot,duration_snapshot_min,
                               price_snapshot_cents,price_mode_snapshot,prep_snapshot_min,
                               cleanup_snapshot_min,item_order)
                            VALUES (:tenant,:appointment,:service,:name,:duration,:price,:price_mode,
                                    :prep,:cleanup,:order)
                        """), {"tenant": tenant_id, "appointment": appointment_id,
                               "service": item["id"], "name": item["name"],
                               "duration": item["duration_min"], "price": item["price_cents"],
                               "price_mode": item["price_mode"], "prep": item["prep_min"],
                               "cleanup": item["cleanup_min"], "order": order})
                    # Each booking remains eligible for its own 1h reminder and Calendar sync.
                    # The notification subsystem suppresses repeated immediate team notices.
                    await history(db, tenant_id, appointment_id, "confirmed", context=panel_actor(actor))
                await db.execute(text("""
                    INSERT INTO usage_counters (tenant_id,period,bookings)
                    VALUES (:tenant,date_trunc('month',CURRENT_DATE)::date,:count)
                    ON CONFLICT (tenant_id,period) DO UPDATE
                    SET bookings=usage_counters.bookings+EXCLUDED.bookings
                """), {"tenant": tenant_id, "count": len(reviewed)})
                await audit(db, tenant_id, "fixed_series_created", "appointment_series", series_id)
                return json_response({**await series_detail(db, tenant_id, series_id), "replayed": False}, status_code=201)
    except IntegrityError as exc:
        raise HTTPException(409, "Um dos horários foi ocupado durante a confirmação. Nenhuma reserva foi criada") from exc


@router.post("/{series_id}/cancel-future")
async def cancel_fixed_future(tenant_id: UUID, series_id: UUID, payload: CancelFutureRequest,
                              actor: Actor = Depends(authenticate)):
    authorize(actor, tenant_id, write=True)
    async with get_session_factory()() as db:
        async with tenant_transaction(db, tenant_id):
            await require_feature(db, tenant_id, "agenda.manage")
            series = (await db.execute(text("""
                SELECT id,professional_id FROM appointment_series
                WHERE tenant_id=:tenant AND id=:id
            """), {"tenant": tenant_id, "id": series_id})).mappings().first()
            if not series:
                raise HTTPException(404, "Série não encontrada")
            await acquire_professional_lock(db, tenant_id, series["professional_id"])
            items = (await db.execute(text("""
                SELECT * FROM appointments
                WHERE tenant_id=:tenant AND series_id=:id AND series_index>=:idx
                  AND status='confirmed' AND start_at>clock_timestamp()
                ORDER BY series_index FOR UPDATE
            """), {"tenant": tenant_id, "id": series_id, "idx": payload.from_index})).mappings().all()
            for i, item in enumerate(items):
                await db.execute(text("""
                    UPDATE appointments SET status='cancelled',
                      updated_at=GREATEST(clock_timestamp(),updated_at+interval '1 microsecond')
                    WHERE tenant_id=:tenant AND id=:id AND status='confirmed'
                """), {"tenant": tenant_id, "id": item["id"]})
                await history(db, tenant_id, item["id"], "cancelled",
                              context=panel_actor(actor), requested_by="business",
                              reason=payload.reason or "Cliente fixo: próximas reservas canceladas",
                              previous=item, notify=(i == 0))
            remaining = (await db.execute(text("""
                SELECT EXISTS(SELECT 1 FROM appointments
                  WHERE tenant_id=:tenant AND series_id=:id
                    AND status='confirmed' AND start_at>clock_timestamp())
            """), {"tenant": tenant_id, "id": series_id})).scalar_one()
            if not remaining:
                await db.execute(text("""
                    UPDATE appointment_series SET closed_at=COALESCE(closed_at,clock_timestamp())
                    WHERE tenant_id=:tenant AND id=:id
                """), {"tenant": tenant_id, "id": series_id})
            if items:
                await audit(db, tenant_id, "fixed_series_cancelled", "appointment_series", series_id)
            return json_response({"series_id": series_id, "cancelled_count": len(items),
                                  "series": await series_detail(db, tenant_id, series_id)})
