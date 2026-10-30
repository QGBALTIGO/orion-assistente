"""Verified customer self-service for every published Orion booking site.

Only a tenant-scoped, phone-verified cookie can read historic bookings or
modify a customer profile. Public pages and phone-entry responses expose no
customer existence, appointment IDs or secrets. No new customer is created on
login; the existing (tenant_id, phone) identity is reused.
"""
from datetime import date, datetime, timezone
from uuid import UUID
import os
import re
import time

from fastapi import APIRouter, HTTPException, Request, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import AwareDatetime, Field, field_validator, model_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.catalog_schemas import StrictModel
from app.management_schemas import normalize_phone, CleanModel
from app.database import get_session_factory, tenant_transaction
from app.public_site_api import public_limit, resolve_public, published_guard
from app.panel_auth import _same_origin
from app.booking_service import (read_appointments, read_appointment, list_slots,
                                  cancel_appointment, reschedule_appointment)
from app.appointment_lifecycle import customer_can_change, customer_actor
from app.public_customer_auth import (
    COOKIE, GENERIC_SENT, enabled, require_enabled, issue_session, revoke_session,
    session_claims, enqueue_code, provider_request, phone_fingerprint,
)

router = APIRouter(prefix="/api/v1/public/sites/{slug}/account",
                   tags=["Conta do cliente no site público"])
PRIVATE = {"Cache-Control": "no-store,private", "Referrer-Policy": "no-referrer",
           "X-Content-Type-Options": "nosniff"}


def reply(value, status=200):
    return JSONResponse(jsonable_encoder(value), status_code=status, headers=PRIVATE)


class PhoneInput(CleanModel):
    phone: str = Field(min_length=10, max_length=40)

    @field_validator("phone")
    @classmethod
    def phone_format(cls, value):
        return normalize_phone(value)


class CodeInput(PhoneInput):
    code: str = Field(min_length=4, max_length=10, pattern=r"^\d{4,10}$")


class ProfilePatch(CleanModel):
    expected_version: int = Field(ge=1, strict=True)
    name: str | None = Field(default=None, min_length=2, max_length=120)
    email: str | None = Field(default=None, max_length=320)
    instagram: str | None = Field(default=None, max_length=35)
    birth_date: date | None = None
    neighborhood: str | None = Field(default=None, max_length=120)
    city: str | None = Field(default=None, max_length=120)

    @field_validator("email")
    @classmethod
    def email_format(cls, value):
        if value in ("", None):
            return None
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
            raise ValueError("Informe um e-mail válido")
        return value.lower()

    @field_validator("instagram")
    @classmethod
    def instagram_format(cls, value):
        if value in ("", None):
            return None
        value = value.lstrip("@")
        if not re.fullmatch(r"[a-zA-Z0-9._]{1,30}", value):
            raise ValueError("Informe apenas o usuário do Instagram")
        return "@" + value

    @field_validator("birth_date")
    @classmethod
    def birth_date_format(cls, value):
        if value and not date(1900, 1, 1) <= value <= datetime.now(timezone.utc).date():
            raise ValueError("Data de nascimento inválida")
        return value

    @model_validator(mode="after")
    def has_changes(self):
        if not self.model_fields_set.intersection(
                {"name", "email", "instagram", "birth_date", "neighborhood", "city"}):
            raise ValueError("Nenhum dado foi alterado")
        if "name" in self.model_fields_set and self.name is None:
            raise ValueError("Informe o nome completo")
        return self


class BookingMutation(StrictModel):
    expected_updated_at: AwareDatetime
    idempotency_key: UUID


class BookingReschedule(BookingMutation):
    start_at: AwareDatetime


async def identity(slug, request):
    """Re-check site, tenant, current phone and version on every private action."""
    tenant = await resolve_public(slug)
    state = await session_claims(request.cookies.get(COOKIE), tenant)
    if not state:
        raise HTTPException(401, "Confirme seu telefone para acessar a conta")
    customer_id = UUID(state["customer"])
    async with get_session_factory()() as db:
        async with tenant_transaction(db, tenant):
            await published_guard(db, tenant, slug)
            customer = (await db.execute(text("""
                SELECT id,name,whatsapp_e164,instagram,email,birth_date,neighborhood,city,version
                FROM customers WHERE tenant_id=:t AND id=:customer
                  AND active AND deleted_at IS NULL
            """), {"t": tenant, "customer": customer_id})).mappings().one_or_none()
            if (not customer or customer["version"] != state["version"]
                    or phone_fingerprint(customer["whatsapp_e164"]) != state["phone_hash"]):
                raise HTTPException(401, "Sua sessão foi atualizada. Confirme o telefone novamente.")
            return tenant, dict(customer), state


def profile_data(row):
    return {key: row.get(key) for key in
            ("name", "whatsapp_e164", "email", "instagram", "birth_date",
             "neighborhood", "city", "version")}


def summarized(row, now):
    return {"id": row["id"], "protocol": row["protocol"], "status": row["status"],
            "start_at": row["start_at"], "end_at": row["end_at"],
            "updated_at": row["updated_at"], "professional": row["professional_name"],
            "services": [item["name_snapshot"] for item in row["items"]],
            "duration_min": sum(int(item["duration_snapshot_min"]) for item in row["items"]),
            "price_cents": row["total_cents"], "price_mode": row.get("price_mode", "fixed"),
            "can_change": customer_can_change(row, now)}


async def owned_booking(db, tenant, customer_id, appointment_id):
    row = (await db.execute(text("""
        SELECT id FROM appointments WHERE tenant_id=:t AND id=:a
          AND customer_id=:c AND status<>'hold'
    """), {"t": tenant, "a": appointment_id, "c": customer_id})).scalar_one_or_none()
    if row is None:
        # The response doesn't tell an attacker whether another booking exists.
        raise HTTPException(404, "Agendamento indisponível")
    return await read_appointment(db, tenant, appointment_id)


@router.get("")
async def state(slug: str, request: Request):
    # Both anonymous and authenticated states always use no-store.
    tenant = await resolve_public(slug)
    if not enabled():
        return reply({"available": False, "authenticated": False})
    claims = await session_claims(request.cookies.get(COOKIE), tenant)
    if not claims:
        return reply({"available": True, "authenticated": False})
    try:
        _, customer, _ = await identity(slug, request)
    except HTTPException as error:
        if error.status_code in (401, 403):
            return reply({"available": True, "authenticated": False})
        raise
    return reply({"available": True, "authenticated": True, "profile": profile_data(customer)})


@router.post("/request-code", status_code=202)
async def request_code(slug: str, payload: PhoneInput, request: Request):
    require_enabled()
    _same_origin(request)
    await public_limit(request, slug, "customer-account-ip", 8, 3600)
    # Rate-limit by target phone across *all* sites, stopping provider abuse.
    await public_limit(request, "all", "customer-account-phone", 3, 1800,
                       subject=payload.phone)
    await resolve_public(slug)
    await enqueue_code(slug, payload.phone)
    return reply(GENERIC_SENT, 202)


@router.post("/verify-code")
async def verify_code(slug: str, payload: CodeInput, request: Request):
    require_enabled()
    _same_origin(request)
    await public_limit(request, slug, "customer-account-verify-ip", 20, 900)
    await public_limit(request, "all", "customer-account-verify-phone", 8, 900,
                       subject=payload.phone)
    tenant = await resolve_public(slug)
    if not await provider_request(payload.phone, payload.code):
        raise HTTPException(403, "Código inválido ou expirado. Solicite um novo código.")
    async with get_session_factory()() as db:
        async with tenant_transaction(db, tenant):
            await published_guard(db, tenant, slug)
            customer = (await db.execute(text("""
                SELECT id,name,whatsapp_e164,email,instagram,birth_date,neighborhood,city,version
                FROM customers WHERE tenant_id=:t AND whatsapp_e164=:phone
                  AND active AND deleted_at IS NULL LIMIT 1
            """), {"t": tenant, "phone": payload.phone})).mappings().one_or_none()
    if customer is None:
        # After proving control of the number, it is safe to explain that no
        # booking exists here. Before proof, response is generic.
        raise HTTPException(404, "Não encontramos agendamentos neste número. Você pode agendar pelo site.")
    response = reply({"authenticated": True, "profile": profile_data(dict(customer))})
    await issue_session(tenant, customer["id"], customer["version"],
                        payload.phone, response, previous=request.cookies.get(COOKIE))
    return response


@router.post("/logout")
async def logout(slug: str, request: Request):
    _same_origin(request)
    await revoke_session(request.cookies.get(COOKIE))
    response = reply({"authenticated": False})
    response.delete_cookie(COOKIE, secure=True, httponly=True,
                           samesite="lax", path="/")
    return response


@router.get("/appointments")
async def appointments(slug: str, request: Request,
                       kind: str = Query("upcoming", pattern=r"^(upcoming|history)$"),
                       offset: int = Query(0, ge=0, le=10000)):
    tenant, customer, _ = await identity(slug, request)
    where = ("a.status='confirmed' AND a.start_at>=clock_timestamp()"
             if kind == "upcoming" else
             "a.status<>'hold' AND NOT(a.status='confirmed' AND a.start_at>=clock_timestamp())")
    async with get_session_factory()() as db:
        async with tenant_transaction(db, tenant):
            rows = (await db.execute(text("""
                SELECT a.id FROM appointments a
                WHERE a.tenant_id=:t AND a.customer_id=:customer AND """ + where + """
                ORDER BY a.start_at DESC,a.id DESC
                LIMIT 26 OFFSET :offset
            """), {"t": tenant, "customer": customer["id"], "offset": offset})).scalars().all()
            details = await read_appointments(db, tenant, list(rows[:25]))
    clock = datetime.now(timezone.utc)
    return reply({"items": [summarized(row, clock) for row in details],
                  "has_more": len(rows) > 25, "next_offset": offset + len(details),
                  "timezone": details[0]["timezone"] if details else None})


@router.patch("/profile")
async def update_profile(slug: str, payload: ProfilePatch, request: Request):
    _same_origin(request)
    tenant, customer, claims = await identity(slug, request)
    changes = payload.model_dump(exclude_unset=True, exclude={"expected_version"})
    # Empty optional inputs are explicitly treated as removal. No notes,
    # staff flags, customer IDs or other company-private fields are writable.
    if "birth_date" in changes:
        birth = changes["birth_date"]
        changes.update(birth_month=birth.month if birth else None,
                       birth_day=birth.day if birth else None)
    sets = ", ".join(key + "=:" + key for key in changes)
    async with get_session_factory()() as db:
        try:
            async with tenant_transaction(db, tenant):
                updated = (await db.execute(text("""
                    UPDATE customers SET """ + sets + """,
                      version=version+1,updated_at=clock_timestamp()
                    WHERE tenant_id=:t AND id=:customer AND version=:expected
                    RETURNING id,name,whatsapp_e164,email,instagram,birth_date,neighborhood,city,version
                """), {**changes, "t": tenant, "customer": customer["id"],
                       "expected": payload.expected_version})).mappings().one_or_none()
        except IntegrityError:
            raise HTTPException(409, "Não foi possível salvar. Confira os dados informados.") from None
    if updated is None:
        raise HTTPException(409, "O cadastro mudou. Atualize os dados antes de salvar.")
    response = reply({"profile": profile_data(dict(updated))})
    await issue_session(tenant, customer["id"], updated["version"],
                        customer["whatsapp_e164"], response,
                        previous=request.cookies.get(COOKIE),
                        verified_at=claims["verified_at"])
    return response


@router.post("/phone/request-code")
async def request_new_phone(slug: str, payload: PhoneInput, request: Request):
    _same_origin(request)
    require_enabled()
    tenant, customer, claims = await identity(slug, request)
    if int(time.time()) - claims["verified_at"] > 15 * 60:
        raise HTTPException(403, "Confirme seu telefone atual novamente antes de alterá-lo.")
    if payload.phone == customer["whatsapp_e164"]:
        raise HTTPException(409, "Este já é seu telefone cadastrado.")
    await public_limit(request, str(customer["id"]), "customer-change-phone", 2, 3600)
    await public_limit(request, "all", "customer-account-phone", 3, 1800,
                       subject=payload.phone)
    if not await provider_request(payload.phone):
        raise HTTPException(503, "Não foi possível enviar o código. Tente novamente mais tarde.")
    return reply({"message": "Enviamos um código para o novo telefone."}, 202)


@router.post("/phone/confirm")
async def confirm_new_phone(slug: str, payload: CodeInput, request: Request):
    _same_origin(request)
    require_enabled()
    tenant, customer, claims = await identity(slug, request)
    if int(time.time()) - claims["verified_at"] > 15 * 60:
        raise HTTPException(403, "Confirme seu telefone atual novamente antes de alterá-lo.")
    await public_limit(request, str(customer["id"]), "customer-change-phone-check", 6, 900)
    if payload.phone == customer["whatsapp_e164"] or not await provider_request(payload.phone, payload.code):
        raise HTTPException(403, "Não foi possível confirmar o novo telefone.")
    async with get_session_factory()() as db:
        try:
            async with tenant_transaction(db, tenant):
                updated = (await db.execute(text("""
                    UPDATE customers SET whatsapp_e164=:new,version=version+1,
                      updated_at=clock_timestamp()
                    WHERE tenant_id=:t AND id=:customer AND version=:expected
                      AND NOT EXISTS(
                        SELECT 1 FROM customers other WHERE other.tenant_id=:t
                          AND other.whatsapp_e164=:new AND other.id<>:customer)
                    RETURNING id,name,whatsapp_e164,email,instagram,birth_date,neighborhood,city,version
                """), {"t": tenant, "customer": customer["id"], "new": payload.phone,
                       "expected": customer["version"]})).mappings().one_or_none()
        except IntegrityError:
            raise HTTPException(409, "Este telefone já está em uso. Solicite ajuda ao estabelecimento.") from None
    if updated is None:
        raise HTTPException(409, "Não foi possível trocar o telefone. Ele pode pertencer a outro cadastro.")
    response = reply({"profile": profile_data(dict(updated))})
    await issue_session(tenant, customer["id"], updated["version"],
                        payload.phone, response, previous=request.cookies.get(COOKIE),
                        verified_at=claims["verified_at"])
    return response


@router.get("/appointments/{appointment_id}/slots")
async def available_reschedule_slots(slug: str, appointment_id: UUID, day: date,
                                     request: Request):
    tenant, customer, _ = await identity(slug, request)
    await public_limit(request, slug, "customer-account-slots", 60, 300)
    async with get_session_factory()() as db:
        async with tenant_transaction(db, tenant):
            row = await owned_booking(db, tenant, customer["id"], appointment_id)
            if not customer_can_change(row, datetime.now(timezone.utc)):
                raise HTTPException(409, "O prazo para remarcar terminou.")
        # The engine owns its own tenant transaction and independently checks
        # the same customer ID against the current appointment row.
        data = await list_slots(db, tenant, row["professional_id"],
            [item["service_id"] for item in row["items"]], day,
            appointment_id=appointment_id, customer_id=customer["id"],
            explain_reason=True)
    return reply(data)


@router.post("/appointments/{appointment_id}/cancel")
async def cancel_own_booking(slug: str, appointment_id: UUID, payload: BookingMutation,
                             request: Request):
    _same_origin(request)
    tenant, customer, _ = await identity(slug, request)
    await public_limit(request, slug, "customer-account-writes", 20, 300)
    async with get_session_factory()() as db:
        async with tenant_transaction(db, tenant):
            await owned_booking(db, tenant, customer["id"], appointment_id)
        # The engine rechecks phone ownership, deadline and CAS *atomically*.
        result = await cancel_appointment(
            db, tenant, appointment_id, customer_phone=customer["whatsapp_e164"],
            context=customer_actor(customer["id"], "public_site"),
            requested_by="customer", expected_updated_at=payload.expected_updated_at,
            idempotency_key=str(payload.idempotency_key))
    return reply({"item": summarized(result, datetime.now(timezone.utc))})


@router.post("/appointments/{appointment_id}/reschedule")
async def reschedule_own_booking(slug: str, appointment_id: UUID,
                                 payload: BookingReschedule, request: Request):
    _same_origin(request)
    tenant, customer, _ = await identity(slug, request)
    await public_limit(request, slug, "customer-account-writes", 20, 300)
    async with get_session_factory()() as db:
        async with tenant_transaction(db, tenant):
            await owned_booking(db, tenant, customer["id"], appointment_id)
        result = await reschedule_appointment(
            db, tenant, appointment_id, payload.start_at,
            customer_phone=customer["whatsapp_e164"],
            context=customer_actor(customer["id"], "public_site"),
            expected_updated_at=payload.expected_updated_at,
            idempotency_key=str(payload.idempotency_key))
    return reply({"item": summarized(result, datetime.now(timezone.utc))})
