"""Rotas internas da FATIA 2 (sem bot público)."""
from datetime import date, datetime, time, timedelta, timezone
import re
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from pydantic import AwareDatetime, Field, field_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.catalog_api import Actor, authenticate, authorize
from app.catalog_schemas import StrictModel
from app.appointment_lifecycle import allowed_actions, panel_actor
from app.booking_service import (
    book_appointment, cancel_appointment, confirm_hold, list_slots,
    read_appointment, read_appointments, reschedule_appointment,
)
from app.database import get_session_factory, tenant_transaction
from app.http_json import json_response

router = APIRouter(prefix="/api/v1/tenants/{tenant_id}", tags=["Agendamentos internos"])


class HoldRequest(StrictModel):
    professional_id: UUID
    service_ids: list[UUID] = Field(min_length=1, max_length=6)
    customer_name: str = Field(min_length=1, max_length=120)
    customer_phone: str = Field(pattern=r"^\+55[1-9][0-9]{9,10}$")
    start_at: AwareDatetime
    idempotency_key: str = Field(min_length=8, max_length=120, pattern=r"^[a-zA-Z0-9._:-]+$")


class MutationRequest(StrictModel):
    # Optional only for compatibility with already authenticated legacy callers.
    # The current panel supplies both fields from a freshly fetched detail.
    expected_updated_at: AwareDatetime | None = None
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=120, pattern=r"^[a-zA-Z0-9._:-]+$")


class CancellationRequest(MutationRequest):
    requested_by: Literal["customer", "professional", "business"] | None = None
    reason: str | None = Field(default=None, max_length=1000)

    @field_validator("reason", mode="before")
    @classmethod
    def clean_reason(cls, value):
        if isinstance(value, str):
            if "\x00" in value:
                raise ValueError("Caractere inválido no motivo")
            return value.strip() or None
        return value


class RescheduleRequest(MutationRequest):
    start_at: AwareDatetime


def can_write(actor, tenant_id):
    try:
        authorize(actor, tenant_id, write=True)
        return True
    except HTTPException:
        return False


def appointment_payload(result, actor, tenant_id):
    return {**result, "allowed_actions": allowed_actions(
        result, can_write=can_write(actor, tenant_id))}


def appointment_response(result, actor, tenant_id):
    return jsonable_encoder(appointment_payload(result, actor, tenant_id))


def safe_conflict(exc: IntegrityError) -> HTTPException:
    # Não revelar SQL, IDs de outras empresas ou informações pessoais.
    return HTTPException(409, "Horário ocupado ou registro duplicado")


@router.get("/availability")
async def availability(
    tenant_id: UUID, professional_id: UUID, day: date,
    service_ids: list[UUID] = Query(),
    appointment_id: UUID | None = None,
    actor: Actor = Depends(authenticate),
):
    authorize(actor, tenant_id)
    async with get_session_factory()() as session:
        result = await list_slots(session, tenant_id, professional_id, service_ids, day,
                                  appointment_id=appointment_id, explain_reason=True)
        return json_response(result)


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
                payload.idempotency_key, context=panel_actor(actor),
            )
        except IntegrityError as exc:
            raise safe_conflict(exc) from exc
        return json_response(appointment_payload(result, actor, tenant_id), status_code=201)


@router.post("/appointments/{appointment_id}/confirm")
async def confirm(
    tenant_id: UUID, appointment_id: UUID, payload: MutationRequest | None = Body(default=None),
    actor: Actor = Depends(authenticate),
):
    authorize(actor, tenant_id, write=True)
    async with get_session_factory()() as session:
        try:
            result = await confirm_hold(session, tenant_id, appointment_id,
                **(payload or MutationRequest()).model_dump(), context=panel_actor(actor))
        except IntegrityError as exc:
            raise safe_conflict(exc) from exc
        return json_response(appointment_payload(result, actor, tenant_id))


@router.post("/appointments/{appointment_id}/cancel")
async def cancel(
    tenant_id: UUID, appointment_id: UUID, payload: CancellationRequest | None = Body(default=None),
    actor: Actor = Depends(authenticate),
):
    authorize(actor, tenant_id, write=True)
    async with get_session_factory()() as session:
        try:
            result = await cancel_appointment(session, tenant_id, appointment_id,
                **(payload or CancellationRequest()).model_dump(), context=panel_actor(actor))
        except IntegrityError as exc:
            raise safe_conflict(exc) from exc
        return json_response(appointment_payload(result, actor, tenant_id))


@router.post("/appointments/{appointment_id}/reschedule")
async def reschedule(
    tenant_id: UUID, appointment_id: UUID, payload: RescheduleRequest,
    actor: Actor = Depends(authenticate),
):
    authorize(actor, tenant_id, write=True)
    async with get_session_factory()() as session:
        try:
            from app.appointment_changes import propose_if_required
            proposal=await propose_if_required(session,tenant_id,appointment_id,payload,panel_actor(actor))
            if proposal is not None:
                return json_response(appointment_payload(proposal,actor,tenant_id))
            result = await reschedule_appointment(
                session, tenant_id, appointment_id, payload.start_at,
                expected_updated_at=payload.expected_updated_at, idempotency_key=payload.idempotency_key,
                context=panel_actor(actor),
            )
        except IntegrityError as exc:
            raise safe_conflict(exc) from exc
        return json_response(appointment_payload(result, actor, tenant_id))


@router.get("/appointments/{appointment_id}")
async def detail(
    tenant_id: UUID, appointment_id: UUID, actor: Actor = Depends(authenticate),
):
    authorize(actor, tenant_id)
    async with get_session_factory()() as session:
        async with tenant_transaction(session, tenant_id):
            result = await read_appointment(session, tenant_id, appointment_id, include_history=True)
            content = appointment_payload(result, actor, tenant_id)
    return json_response(content)


@router.get("/appointments")
async def list_appointments(
    tenant_id: UUID, day: date, paged: bool = False,
    q: str | None = Query(default=None, max_length=120), professional_id: UUID | None = None,
    status: Literal["hold", "confirmed", "cancelled", "completed", "no_show"] | None = None,
    cancellation_requested_by: Literal["customer", "professional", "business", "system", "unknown"] | None = None,
    limit: int | None = Query(default=None, ge=1, le=200), offset: int = Query(default=0, ge=0),
    actor: Actor = Depends(authenticate),
):
    authorize(actor, tenant_id)
    if not 1900 <= day.year <= 2100:
        raise HTTPException(422, "Selecione uma data válida para a agenda")
    page_limit = limit or (50 if paged else 200)
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
            conditions = ["a.tenant_id=:tenant", "a.start_at>=:begin", "a.start_at<:end"]
            parameters = {"tenant": tenant_id, "begin": begin, "end": end, "limit": page_limit, "offset": offset}
            if professional_id is not None:
                conditions.append("a.professional_id=:professional")
                parameters["professional"] = professional_id
            if status is not None:
                conditions.append("a.status=:status")
                parameters["status"] = status
            if cancellation_requested_by is not None:
                conditions.append("""a.status='cancelled' AND COALESCE((
                    SELECT CASE WHEN h.details->>'v'='1' THEN h.details->>'requested_by' END
                    FROM appointment_history h WHERE h.tenant_id=a.tenant_id AND h.appointment_id=a.id
                      AND h.event IN ('cancelled','hold_expired')
                    ORDER BY h.occurred_at DESC,h.id DESC LIMIT 1
                ),'unknown')=:requester""")
                parameters["requester"] = cancellation_requested_by
            query = q.strip() if q is not None else ""
            if "\x00" in query:
                raise HTTPException(422, "Busca inválida")
            if query:
                parameters["search"] = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
                search = "(c.name ILIKE :search OR c.whatsapp_e164 ILIKE :search OR a.protocol ILIKE :search"
                if re.fullmatch(r"[+\d\s().-]+", query) and re.search(r"\d", query):
                    parameters["phone_search"] = "%" + re.sub(r"\D", "", query) + "%"
                    search += " OR c.whatsapp_e164 ILIKE :phone_search"
                conditions.append(search + ")")
            page = (await session.execute(text("""
                WITH filtered AS MATERIALIZED (
                    SELECT a.id,a.start_at FROM appointments a
                    JOIN customers c ON c.tenant_id=a.tenant_id AND c.id=a.customer_id
                    WHERE """ + " AND ".join(conditions) + """
                ) SELECT (SELECT count(*) FROM filtered) AS total,
                    ARRAY(SELECT id FROM filtered ORDER BY start_at,id LIMIT :limit OFFSET :offset) AS ids
            """), parameters)).mappings().one()
            result = [appointment_payload(row, actor, tenant_id)
                      for row in await read_appointments(session, tenant_id, page["ids"])]
            content = {"items": result, "total": page["total"], "limit": page_limit, "offset": offset,
                       "has_more": offset + len(result) < page["total"]} if paged else result
    return json_response(content)
