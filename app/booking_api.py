"""Rotas internas da FATIA 2 (sem bot público)."""
from datetime import date, datetime, time, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from pydantic import Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.catalog_api import Actor, authenticate, authorize
from app.catalog_schemas import StrictModel
from app.booking_service import (
    book_appointment, cancel_appointment, confirm_hold, list_slots,
    read_appointment, reschedule_appointment,
)
from app.database import get_session_factory, tenant_transaction

router = APIRouter(prefix="/api/v1/tenants/{tenant_id}", tags=["Agendamentos internos"])


class HoldRequest(StrictModel):
    professional_id: UUID
    service_ids: list[UUID] = Field(min_length=1, max_length=6)
    customer_name: str = Field(min_length=1, max_length=120)
    customer_phone: str = Field(pattern=r"^\+55[1-9][0-9]{9,10}$")
    start_at: datetime
    idempotency_key: str = Field(min_length=8, max_length=120, pattern=r"^[a-zA-Z0-9._:-]+$")


class RescheduleRequest(StrictModel):
    start_at: datetime


def safe_conflict(exc: IntegrityError) -> HTTPException:
    # Não revelar SQL, IDs de outras empresas ou informações pessoais.
    return HTTPException(409, "Horário ocupado ou registro duplicado")


@router.get("/availability")
async def availability(
    tenant_id: UUID, professional_id: UUID, day: date,
    service_ids: list[UUID] = Query(),
    actor: Actor = Depends(authenticate),
):
    authorize(actor, tenant_id)
    async with get_session_factory()() as session:
        result = await list_slots(session, tenant_id, professional_id, service_ids, day)
        return jsonable_encoder(result)


@router.post("/appointments/hold", status_code=201)
async def create_hold(
    tenant_id: UUID, payload: HoldRequest, actor: Actor = Depends(authenticate),
):
    authorize(actor, tenant_id, write=True)
    async with get_session_factory()() as session:
        try:
            result = await book_appointment(
                session, tenant_id, payload.professional_id, payload.service_ids,
                payload.customer_phone, payload.customer_name, payload.start_at,
                payload.idempotency_key,
            )
        except IntegrityError as exc:
            raise safe_conflict(exc) from exc
        return jsonable_encoder(result)


@router.post("/appointments/{appointment_id}/confirm")
async def confirm(
    tenant_id: UUID, appointment_id: UUID, actor: Actor = Depends(authenticate),
):
    authorize(actor, tenant_id, write=True)
    async with get_session_factory()() as session:
        try:
            result = await confirm_hold(session, tenant_id, appointment_id)
        except IntegrityError as exc:
            raise safe_conflict(exc) from exc
        return jsonable_encoder(result)


@router.post("/appointments/{appointment_id}/cancel")
async def cancel(
    tenant_id: UUID, appointment_id: UUID, actor: Actor = Depends(authenticate),
):
    authorize(actor, tenant_id, write=True)
    async with get_session_factory()() as session:
        return jsonable_encoder(await cancel_appointment(session, tenant_id, appointment_id))


@router.post("/appointments/{appointment_id}/reschedule")
async def reschedule(
    tenant_id: UUID, appointment_id: UUID, payload: RescheduleRequest,
    actor: Actor = Depends(authenticate),
):
    authorize(actor, tenant_id, write=True)
    async with get_session_factory()() as session:
        try:
            result = await reschedule_appointment(
                session, tenant_id, appointment_id, payload.start_at
            )
        except IntegrityError as exc:
            raise safe_conflict(exc) from exc
        return jsonable_encoder(result)


@router.get("/appointments/{appointment_id}")
async def detail(
    tenant_id: UUID, appointment_id: UUID, actor: Actor = Depends(authenticate),
):
    authorize(actor, tenant_id)
    async with get_session_factory()() as session:
        async with tenant_transaction(session, tenant_id):
            return jsonable_encoder(await read_appointment(session, tenant_id, appointment_id))


@router.get("/appointments")
async def list_appointments(
    tenant_id: UUID, day: date, actor: Actor = Depends(authenticate),
):
    authorize(actor, tenant_id)
    async with get_session_factory()() as session:
        async with tenant_transaction(session, tenant_id):
            tz = (await session.execute(text(
                "SELECT timezone FROM tenants WHERE id=:tenant"
            ), {"tenant": tenant_id})).scalar_one_or_none()
            if not tz:
                raise HTTPException(404, "Barbearia não encontrada")
            zone = ZoneInfo(tz)
            begin = datetime.combine(day, time.min, tzinfo=zone).astimezone(timezone.utc)
            end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone).astimezone(timezone.utc)
            rows = (await session.execute(text("""
                SELECT id FROM appointments WHERE tenant_id=:tenant
                  AND start_at >= :begin AND start_at < :end
                  ORDER BY start_at LIMIT 200
            """), {"tenant": tenant_id, "begin": begin, "end": end})).scalars().all()
            result = []
            for row in rows:
                result.append(await read_appointment(session, tenant_id, row))
            return jsonable_encoder(result)
