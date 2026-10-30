"""Opaque, non-enumerable short links with WhatsApp step-up verification.

Public GETs are identical empty shells; only verified sessions can view or
modify private appointment data. No UUID, OTP, or capability appears in URLs.
"""
import os
from datetime import date
from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse
from pydantic import AwareDatetime, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app import appointment_access as access
from app.appointment_access_api import PRIVATE_HEADERS, customer_view
from app.appointment_lifecycle import LifecycleActor, customer_can_change
from app.appointment_short_links import (
    CUSTOMER_COOKIE, STAFF_COOKIE, GENERIC_REQUEST, checked_code,
    resolve_global, response_cookie, belongs,
)
from app.appointment_short_auth import redeem_otp, verify_staff
from app.booking_service import read_appointment, list_slots, cancel_appointment, reschedule_appointment
from app.catalog_schemas import StrictModel
from app.database import get_session_factory, tenant_transaction
from app.panel_auth import _same_origin
from app.public_site_api import public_limit

router = APIRouter(tags=["Links curtos de agendamentos"])


def enabled():
    return access.enabled() and os.getenv("APPOINTMENT_SHORT_LINKS_ENABLED", "0") == "1"


def require_enabled():
    if not enabled():
        raise HTTPException(404, "Página indisponível")


class DeliveryRequest(StrictModel):
    recipient_kind: Literal["customer", "professional"]


class VerifyRequest(DeliveryRequest):
    otp: str = Field(pattern=r"^[0-9]{6}$")


class StaffAction(StrictModel):
    action: Literal["view", "slots", "cancel", "reschedule"]
    day: date | None = None
    start_at: AwareDatetime | None = None
    expected_updated_at: AwareDatetime | None = None
    idempotency_key: UUID | None = None


def private_json(body, status_code=200):
    return JSONResponse(jsonable_encoder(body), status_code=status_code, headers=PRIVATE_HEADERS)


async def enqueue_request(code, role):
    """Public POST never queries whether a code exists, only queues a bounded job."""
    from arq import create_pool
    from arq.connections import RedisSettings
    from uuid import uuid4
    redis = await create_pool(RedisSettings.from_dsn(os.environ["REDIS_URL"]))
    try:
        await redis.enqueue_job("request_short_otp", code, role,
                _job_id="short-link-request:" + str(uuid4()))
    finally:
        await redis.aclose()


@router.post("/api/short-appointments/{code}/request-code", status_code=202)
async def request_code(code: str, payload: DeliveryRequest, request: Request):
    require_enabled()
    code = checked_code(code)
    _same_origin(request)
    # Redis limits fail closed and use only peer IP / opaque protocol keys.
    await public_limit(request, "short-links", "short-request-ip", 10, 3600)
    await public_limit(request, "short-links", "short-request-code", 5, 1800, subject=code)
    # Queued for *all* syntactically valid codes, including unknown ones.
    # Lookup and delivery happen behind the private worker; HTTP response
    # timing and body do not depend on the existence of an appointment.
    await enqueue_request(code, payload.recipient_kind)
    return private_json(GENERIC_REQUEST,status_code=202)


@router.post("/api/short-appointments/{code}/verify-code")
async def verify_code(code: str, payload: VerifyRequest, request: Request):
    require_enabled()
    code = checked_code(code)
    _same_origin(request)
    await public_limit(request, "short-links", "short-verify-ip", 25, 600)
    await public_limit(request, "short-links", "short-verify-code", 20, 600, subject=code)
    resolved = await resolve_global(code)
    if resolved is None:
        raise HTTPException(403, "Código inválido ou expirado. Solicite um novo código.")
    raw = await redeem_otp(resolved["tenant_id"], resolved["appointment_id"],
                           payload.recipient_kind, payload.otp)
    reply = private_json({"verified": True})
    response_cookie(reply, CUSTOMER_COOKIE if payload.recipient_kind == "customer" else STAFF_COOKIE, raw)
    return reply


async def staff_view(db, tenant, appointment, pro):
    row = await read_appointment(db, tenant, appointment)
    if row["professional_id"] != pro:
        raise HTTPException(403, "Acesso ao atendimento não autorizado")
    from types import SimpleNamespace
    identity = SimpleNamespace(tenant_id=tenant,appointment_id=appointment,customer_id=row["customer_id"])
    data = await customer_view(db, identity)
    now = (await db.execute(text("SELECT clock_timestamp()"))).scalar_one()
    data.update({"can_change": customer_can_change(row, now),
                 "can_review": False, "review": None, "pending_change": None})
    return data


@router.post("/api/short-appointments/{code}/professional-action")
async def professional_action(code: str, payload: StaffAction, request: Request):
    require_enabled()
    code = checked_code(code)
    _same_origin(request)
    await public_limit(request, "short-links", "short-staff-ip", 120, 300)
    resolved = await resolve_global(code)
    if resolved is None or not access.tenant_enabled(resolved["tenant_id"]):
        raise HTTPException(403, "Acesso ao atendimento não autorizado")
    tenant, appointment = resolved["tenant_id"], resolved["appointment_id"]
    factory = get_session_factory()
    async with factory() as db:
        async with tenant_transaction(db, tenant):
            pro = await verify_staff(db, tenant, appointment, request.cookies.get(STAFF_COOKIE))
            if not await belongs(db, tenant, appointment, code):
                raise HTTPException(403, "Acesso ao atendimento não autorizado")
            row = await read_appointment(db, tenant, appointment)
            if row["professional_id"] != pro:
                raise HTTPException(403, "Acesso ao atendimento não autorizado")
            now = (await db.execute(text("SELECT clock_timestamp()"))).scalar_one()
            if payload.action == "view":
                return private_json(await staff_view(db, tenant, appointment, pro))
            if payload.action == "slots":
                if payload.day is None:
                    raise HTTPException(422, "Escolha uma data")
                if not customer_can_change(row, now):
                    raise HTTPException(409, "Este agendamento não pode mais ser alterado")
                services = [item["service_id"] for item in row["items"]]
            elif not customer_can_change(row, now):
                raise HTTPException(409, "Este agendamento não pode mais ser alterado")
    if payload.action == "slots":
        async with factory() as db:
            data = await list_slots(db, tenant, pro, services, payload.day, appointment_id=appointment)
        return private_json(data)
    if payload.expected_updated_at is None or payload.idempotency_key is None:
        raise HTTPException(422, "Atualize o agendamento antes de confirmar")
    actor = LifecycleActor(role="professional",source="public_site",professional_id=pro)
    async with factory() as db:
        try:
            if payload.action == "cancel":
                await cancel_appointment(db, tenant, appointment,
                    expected_updated_at=payload.expected_updated_at,
                    idempotency_key=str(payload.idempotency_key),
                    context=actor, requested_by="professional")
            elif payload.action == "reschedule":
                if payload.start_at is None:
                    raise HTTPException(422, "Escolha o horário")
                from app.appointment_changes import propose_if_required
                proposal = await propose_if_required(db, tenant, appointment, payload, actor)
                if proposal is None:
                    await reschedule_appointment(db, tenant, appointment, payload.start_at,
                        expected_updated_at=payload.expected_updated_at,
                        idempotency_key=str(payload.idempotency_key),context=actor)
        except IntegrityError:
            raise HTTPException(409, "O horário não está mais disponível") from None
    async with factory() as db:
        async with tenant_transaction(db, tenant):
            result = await staff_view(db, tenant, appointment, pro)
    if payload.action == "reschedule" and proposal is not None:
        result["change_status"] = "pending_customer_acceptance"
    return private_json(result)


@router.get("/p/{code}", include_in_schema=False)
async def professional_page(code: str):
    require_enabled()
    code = checked_code(code)
    return FileResponse(Path(__file__).parent / "static" / "orion-appointment.html", headers=PRIVATE_HEADERS)


@router.get("/{code}", include_in_schema=False)
async def customer_page(code: str):
    require_enabled()
    code = checked_code(code)
    # No database lookup, redirect, OpenGraph details, or status distinction.
    return FileResponse(Path(__file__).parent / "static" / "orion-appointment.html", headers=PRIVATE_HEADERS)
