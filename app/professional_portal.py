"""Private individual workspace for verified professional accounts.

Every request derives tenant/professional from support-only links and holds the
membership read locks until its tenant-scoped operation finishes. No caller can
supply another professional ID or use the company-wide management endpoints.
"""
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, AwareDatetime, StrictBool, field_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.catalog_api import Actor, authenticate, support_session_factory
from app.database import get_session_factory, tenant_transaction
from app.plan_permissions import require_feature
from app import management_models as management
from app.booking_service import (read_appointments, read_appointment,
    cancel_appointment, reschedule_appointment, acquire_professional_lock,
    book_appointment, confirm_hold, list_slots)
from app.booking_api import CancellationRequest, safe_conflict
from app.appointment_lifecycle import LifecycleActor, allowed_actions
from app.management_schemas import AppointmentTransition
from app.appointment_operations import (
    ManualBooking, ExtraServicesRequest, create_manual_appointment,
    extra_services_catalogue, preview_extra_services, add_extra_services,
)

router = APIRouter(prefix="/api/v1/professional", tags=["Área do profissional"])


@asynccontextmanager
async def individual_access(actor: Actor):
    if actor.role != "member" or not isinstance(actor.account_id, UUID) or actor.maintenance:
        raise HTTPException(403, "Entre com sua conta profissional")
    async with support_session_factory()() as db:
        async with db.begin():
            rows = (await db.execute(text("""SELECT l.tenant_id,l.professional_id,l.updated_at AS link_version
                FROM professional_portal_links l
                JOIN tenant_memberships m ON m.tenant_id=l.tenant_id
                    AND m.account_id=l.account_id AND m.active AND m.role='professional'
                JOIN panel_accounts a ON a.id=l.account_id AND a.verified_at IS NOT NULL
                WHERE l.account_id=:a AND l.active
                ORDER BY l.tenant_id LIMIT 2 FOR SHARE OF l,m"""),
                {"a": actor.account_id})).mappings().all()
            if len(rows) != 1:
                raise HTTPException(403, "Acesso profissional indisponível ou suspenso")
            tenant, professional = rows[0]["tenant_id"], rows[0]["professional_id"]
            # A legacy multiple-business account is never assigned a random company.
            count = (await db.execute(text("""SELECT count(*) FROM (
                SELECT tenant_id FROM tenant_memberships
                WHERE account_id=:a AND active LIMIT 2) companies"""),
                {"a": actor.account_id})).scalar_one()
            if count != 1:
                raise HTTPException(403, "Vínculo de empresa não confirmado")
            # Runtime RLS protects operational state; a historical active
            # membership never survives a disable/reactivate staff cycle.
            async with get_session_factory()() as operational:
                async with tenant_transaction(operational,tenant):
                    status=(await operational.execute(text("""SELECT active,last_archived_at
                        FROM professionals WHERE tenant_id=:t AND id=:p"""),
                        {"t":tenant,"p":professional})).mappings().first()
                    if (not status or not status["active"] or
                            status["last_archived_at"] is not None and
                            rows[0]["link_version"] <= status["last_archived_at"]):
                        raise HTTPException(403,"Acesso profissional desativado; solicite reativação ao proprietário")
            yield tenant, professional


@router.get("/dental/context")
async def dental_professional_context(actor: Actor = Depends(authenticate)):
    """The dental tab is advertised only to a verified, explicitly granted dentist.

    Never accepts a client-chosen company or professional ID.
    """
    from app.plan_permissions import plan_state
    async with individual_access(actor) as (tenant, professional):
        async with get_session_factory()() as db:
            async with tenant_transaction(db,tenant):
                plan=await plan_state(db,tenant)
                if not plan.get("dental_clinical_enabled") or not plan.get("permissions",{}).get("clients.manage"):
                    return {"enabled":False}
                granted=(await db.execute(text("""
                    SELECT 1 FROM dental_clinicians
                    WHERE tenant_id=:tenant AND account_id=:account
                      AND professional_id=:professional AND level='dentist' AND enabled
                """),{"tenant":tenant,"account":actor.account_id,
                       "professional":professional})).scalar_one_or_none()
                return {"enabled":granted is not None,
                        "tenant_id":tenant if granted else None}


async def may_manage_company_blocks(tenant, professional, account_id):
    """Only an owner-authorized override may remove company-origin blocks."""
    async with support_session_factory()() as db:
        return bool((await db.execute(text("""SELECT can_manage_owner_blocks
            FROM professional_portal_links WHERE tenant_id=:t
            AND professional_id=:p AND account_id=:a AND active"""),
            {"t":tenant,"p":professional,"a":account_id})).scalar_one_or_none())


async def profile(db, tenant, professional):
    row = (await db.execute(text("""SELECT p.id,p.name,p.specialty,p.active,
        t.name AS business_name,t.timezone FROM professionals p JOIN tenants t
        ON t.id=p.tenant_id WHERE p.tenant_id=:t AND p.id=:p AND p.active
        AND t.status='active'"""), {"t":tenant,"p":professional})).mappings().first()
    if row is None:
        raise HTTPException(403, "Profissional inativo ou não encontrado")
    return dict(row)


def project_appointment(row):
    # Never return raw customer credentials or another company's financial data.
    return {key: row.get(key) for key in (
        "id","protocol","start_at","end_at","status","updated_at",
        "customer_name","total_cents","price_mode","cancellation",
        "booking_mode","created_at","actual_duration_min","services_duration_min",
        "reserved_duration_min","timezone","original_total_cents","discount_cents",
    )} | {"services": [
        {"name": item.get("name_snapshot"),"duration_min":item.get("duration_snapshot_min")}
        for item in row.get("items", [])
    ], "items": [{key: item.get(key) for key in (
        "service_id","name_snapshot","duration_snapshot_min","price_snapshot_cents",
        "price_mode_snapshot","item_origin","added_at","added_duration_min",
    )} for item in row.get("items", [])],
        "history": [{key: entry.get(key) for key in (
            "id","event","occurred_at","requested_by","reason","source","previous",
        )} | {"actor": {"role": (entry.get("actor") or {}).get("role")}}
        for entry in row.get("history", [])],
        "history_total": row.get("history_total", 0),
        "allowed_actions": allowed_actions(row, can_write=True),
    }


async def day_period(db, tenant, requested=None):
    tzname = (await db.execute(text("SELECT timezone FROM tenants WHERE id=:t"),
        {"t":tenant})).scalar_one()
    tz = ZoneInfo(tzname)
    today = datetime.now(tz).date()
    day = requested or today
    if abs((day-today).days) > 366:
        raise HTTPException(422, "Selecione uma data em até 12 meses")
    start = datetime.combine(day,time.min,tzinfo=tz).astimezone(timezone.utc)
    end = datetime.combine(day+timedelta(days=1),time.min,tzinfo=tz).astimezone(timezone.utc)
    return day, start, end, tz


@router.get("/me")
async def my_identity(actor: Actor = Depends(authenticate)):
    async with individual_access(actor) as (tenant, professional):
        async with get_session_factory()() as db:
            async with tenant_transaction(db,tenant):
                p = await profile(db,tenant,professional)
                return {**p, "role":"professional"}


@router.get("/dashboard")
async def my_dashboard(actor: Actor = Depends(authenticate)):
    async with individual_access(actor) as (tenant, professional):
        async with get_session_factory()() as db:
            async with tenant_transaction(db,tenant):
                p = await profile(db,tenant,professional)
                await require_feature(db,tenant,"agenda.manage")
                today, lower, upper, tz = await day_period(db,tenant)
                stats = (await db.execute(text("""SELECT
                    count(*) FILTER(WHERE a.status='confirmed') AS confirmed,
                    count(*) FILTER(WHERE a.status='completed') AS completed,
                    count(*) FILTER(WHERE a.status='cancelled') AS cancelled,
                    COALESCE(sum(a.total_cents) FILTER(WHERE a.status='completed'),0) AS revenue_cents,
                    COALESCE(sum(COALESCE(a.commission_cents,
                        round(a.total_cents*p.commission_percent/100)::bigint))
                        FILTER(WHERE a.status='completed'),0) AS commission_cents
                    FROM appointments a
                    JOIN professionals p ON p.tenant_id=a.tenant_id
                        AND p.id=a.professional_id
                    WHERE a.tenant_id=:t AND a.professional_id=:p
                    AND a.start_at>=:lo AND a.start_at<:hi"""),
                    {"t":tenant,"p":professional,"lo":lower,"hi":upper})).mappings().one()
                goals = await management.goals(db,tenant,today.replace(day=1),today)
                personal_goals = [item for item in goals["items"] if item["professional_id"]==professional]
                return {"profile":p,"date":today,"today":dict(stats),"goals":personal_goals}


@router.get("/agenda")
async def my_agenda(day: date | None = None, actor: Actor = Depends(authenticate)):
    async with individual_access(actor) as (tenant, professional):
        async with get_session_factory()() as db:
            async with tenant_transaction(db,tenant):
                await profile(db,tenant,professional)
                await require_feature(db,tenant,"agenda.manage")
                chosen, lower, upper, _ = await day_period(db,tenant,day)
                ids = (await db.execute(text("""SELECT id FROM appointments
                    WHERE tenant_id=:t AND professional_id=:p AND start_at>=:lo
                    AND start_at<:hi AND (status<>'hold' OR hold_expires_at>now())
                    ORDER BY start_at,id LIMIT 150"""),
                    {"t":tenant,"p":professional,"lo":lower,"hi":upper})).scalars().all()
                details = await read_appointments(db,tenant,list(ids))
                permission=await may_manage_company_blocks(tenant,professional,actor.account_id)
                blocks = (await db.execute(text("""SELECT id,starts_at,ends_at,reason,origin,
                      (origin='professional' AND created_by_account_id=:actor
                       OR :permission) AS can_remove
                    FROM schedule_blocks
                    WHERE tenant_id=:t AND professional_id=:p AND starts_at<:hi AND ends_at>:lo
                    ORDER BY starts_at,id LIMIT 100"""),
                    {"t":tenant,"p":professional,"actor":actor.account_id,
                     "permission":permission,"lo":lower,"hi":upper})).mappings().all()
                return {"date":chosen,"appointments":[project_appointment(r) for r in details],
                        "blocks":[dict(b) for b in blocks]}


@router.get("/finance")
async def my_finance(month: str | None = Query(default=None,pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
                     actor: Actor = Depends(authenticate)):
    async with individual_access(actor) as (tenant, professional):
        async with get_session_factory()() as db:
            async with tenant_transaction(db,tenant):
                p = await profile(db,tenant,professional)
                await require_feature(db,tenant,"finance.basic")
                today, _, _, _ = await day_period(db,tenant)
                try:
                    first = date.fromisoformat((month or today.strftime("%Y-%m"))+"-01")
                except ValueError:
                    raise HTTPException(422,"Mês inválido") from None
                if abs((first-today).days)>400:
                    raise HTTPException(422,"Mês fora do período permitido")
                last = (first.replace(day=28)+timedelta(days=4)).replace(day=1)-timedelta(days=1)
                report = await management.commission_report(
                    db,tenant,first,last,professional_id=professional)
                mine = report["items"][0] if report["items"] else None
                # Never include other professionals, company totals, expenses or transfers.
                return {"professional":p["name"],"period":report["period"],
                    "summary":{key:mine.get(key,0) if mine else 0 for key in (
                       "revenue_cents","earned_cents","scheduled_cents",
                       "advances_cents","payments_cents","balance_cents",
                       "payable_cents","lifetime_earned_cents",
                       "lifetime_advances_cents","lifetime_payments_cents")},
                    "legacy_rate_source":report["legacy_rate_source"]}


@router.get("/goals")
async def my_goals(actor: Actor = Depends(authenticate)):
    async with individual_access(actor) as (tenant, professional):
        async with get_session_factory()() as db:
            async with tenant_transaction(db,tenant):
                await profile(db,tenant,professional)
                await require_feature(db,tenant,"agenda.manage")
                return {"items":[g for g in (await management.goals(db,tenant))["items"]
                    if g["professional_id"]==professional]}


@asynccontextmanager
async def _own_appointment(actor, appointment_id, action):
    """Audit-safe authority: bound ID is checked again inside every locked mutation."""
    async with individual_access(actor) as (tenant, professional):
        async with get_session_factory()() as db:
            async with tenant_transaction(db,tenant):
                await profile(db,tenant,professional)
                await require_feature(db,tenant,"agenda.manage")
                belongs = (await db.execute(text("""SELECT 1 FROM appointments
                    WHERE tenant_id=:t AND professional_id=:p AND id=:id"""),
                    {"t":tenant,"p":professional,"id":appointment_id})).scalar_one_or_none()
                if not belongs:
                    raise HTTPException(404,"Agendamento não encontrado na sua agenda")
        yield tenant,professional


@router.post("/appointments/{appointment_id}/cancel")
async def cancel_my_appointment(
    appointment_id: UUID, payload: CancellationRequest,
    actor: Actor = Depends(authenticate),
):
    async with _own_appointment(actor,appointment_id,"cancel") as (tenant,professional):
        async with get_session_factory()() as db:
            result = await cancel_appointment(db,tenant,appointment_id,
                expected_updated_at=payload.expected_updated_at,
                idempotency_key=payload.idempotency_key,
                requested_by="professional",reason=payload.reason,
                context=LifecycleActor(role="professional",source="panel",
                    account_id=actor.account_id),authorized_professional_id=professional)
            return project_appointment(result)


class ProfessionalReschedule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    day: date
    clock: time
    expected_updated_at: AwareDatetime
    idempotency_key: str = Field(min_length=8,max_length=120,pattern=r"^[A-Za-z0-9._:-]+$")


@router.post("/appointments/{appointment_id}/reschedule")
async def reschedule_my_appointment(
    appointment_id: UUID, payload: ProfessionalReschedule,
    actor: Actor = Depends(authenticate),
):
    async with _own_appointment(actor,appointment_id,"reschedule") as (tenant,professional):
        async with get_session_factory()() as db:
            async with tenant_transaction(db,tenant):
                p = await profile(db,tenant,professional)
                new_start = datetime.combine(payload.day,payload.clock,
                    tzinfo=ZoneInfo(p["timezone"]))
            result = await reschedule_appointment(db,tenant,appointment_id,new_start,
                expected_updated_at=payload.expected_updated_at,
                idempotency_key=payload.idempotency_key,
                context=LifecycleActor(role="professional",source="panel",
                    account_id=actor.account_id),authorized_professional_id=professional)
            return project_appointment(result)


@router.get("/appointments/{appointment_id}")
async def my_appointment_detail(appointment_id: UUID, actor: Actor = Depends(authenticate)):
    async with _own_appointment(actor, appointment_id, "read") as (tenant, professional):
        async with get_session_factory()() as db:
            async with tenant_transaction(db, tenant):
                result = await read_appointment(db, tenant, appointment_id, include_history=True)
                if result["professional_id"] != professional:
                    raise HTTPException(404, "Agendamento não encontrado na sua agenda")
                return project_appointment(result)


@router.get("/appointments/{appointment_id}/extras-catalog")
async def my_extra_catalogue(appointment_id: UUID, actor: Actor = Depends(authenticate)):
    async with _own_appointment(actor, appointment_id, "extras") as (tenant, professional):
        async with get_session_factory()() as db:
            result = await extra_services_catalogue(db, tenant, appointment_id,
                                                    authorized_professional_id=professional)
            result["appointment"] = project_appointment(result["appointment"])
            return result


@router.post("/appointments/{appointment_id}/extras/preview")
async def my_extra_preview(appointment_id: UUID, payload: ExtraServicesRequest,
                           actor: Actor = Depends(authenticate)):
    async with _own_appointment(actor, appointment_id, "extras") as (tenant, professional):
        async with get_session_factory()() as db:
            result = await preview_extra_services(db, tenant, appointment_id, payload,
                                                  authorized_professional_id=professional)
            result["appointment"] = project_appointment(result["appointment"])
            return result


@router.post("/appointments/{appointment_id}/extras")
async def add_my_extra_services(appointment_id: UUID, payload: ExtraServicesRequest,
                                actor: Actor = Depends(authenticate)):
    async with _own_appointment(actor, appointment_id, "extras") as (tenant, professional):
        async with get_session_factory()() as db:
            try:
                result = await add_extra_services(db, tenant, appointment_id, payload,
                    context=LifecycleActor(role="professional", source="panel", account_id=actor.account_id),
                    authorized_professional_id=professional)
                return project_appointment(result)
            except IntegrityError as exc:
                raise safe_conflict(exc) from None


@router.post("/appointments/manual", status_code=201)
async def create_my_manual_appointment(payload: ManualBooking, actor: Actor = Depends(authenticate)):
    async with individual_access(actor) as (tenant, professional):
        async with get_session_factory()() as db:
            async with tenant_transaction(db, tenant):
                await profile(db, tenant, professional)
        async with get_session_factory()() as db:
            try:
                result = await create_manual_appointment(db, tenant, professional, payload,
                    context=LifecycleActor(role="professional", source="panel", account_id=actor.account_id))
                return project_appointment(result)
            except IntegrityError as exc:
                raise safe_conflict(exc) from None


@router.post("/appointments/{appointment_id}/{outcome}")
async def finish_my_appointment(
    appointment_id: UUID, outcome: str, payload: AppointmentTransition,
    actor: Actor = Depends(authenticate),
):
    if outcome not in {"complete","no-show"}:
        raise HTTPException(404,"Ação indisponível")
    async with individual_access(actor) as (tenant,professional):
        async with get_session_factory()() as db:
            async with tenant_transaction(db,tenant):
                await profile(db,tenant,professional)
                db.info["management_actor_account_id"] = actor.account_id
                result = await management.transition_appointment(db,tenant,appointment_id,
                    "completed" if outcome=="complete" else "no_show",
                    payload,"professional",authorized_professional_id=professional)
                return project_appointment(result)


class BlockRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    day: date
    start_time: time
    end_time: time
    reason: str = Field(default="Bloqueio de agenda",min_length=3,max_length=120)
    idempotency_key: str = Field(min_length=8,max_length=120,pattern=r"^[A-Za-z0-9._:-]+$")


@router.post("/blocks", status_code=201)
async def create_my_block(payload: BlockRequest, actor: Actor = Depends(authenticate)):
    async with individual_access(actor) as (tenant,professional):
        async with get_session_factory()() as db:
            async with tenant_transaction(db,tenant):
                p = await profile(db,tenant,professional)
                tz = ZoneInfo(p["timezone"])
                start = datetime.combine(payload.day,payload.start_time,tzinfo=tz).astimezone(timezone.utc)
                end = datetime.combine(payload.day,payload.end_time,tzinfo=tz).astimezone(timezone.utc)
                now = datetime.now(timezone.utc)
                if start<now or end<=start or end-start>timedelta(hours=12) or start>now+timedelta(days=90):
                    raise HTTPException(422,"Escolha um período futuro de até 12 horas")
                await require_feature(db,tenant,"agenda.manage")
                await acquire_professional_lock(db,tenant,professional)
                fingerprint={"professional_id":str(professional),"start":start.isoformat(),
                             "end":end.isoformat(),"reason":payload.reason}
                identity,replay=await management.request_identity(db,tenant,
                    "professional_block",payload.idempotency_key,fingerprint)
                if replay:
                    row=(await db.execute(text("""SELECT id,starts_at,ends_at,reason
                        FROM schedule_blocks WHERE tenant_id=:t AND professional_id=:p AND id=:id"""),
                        {"t":tenant,"p":professional,"id":identity})).mappings().first()
                    if row: return dict(row)
                    raise HTTPException(409,"Bloqueio anterior foi removido")
                overlap=(await db.execute(text("""SELECT 1 FROM appointments
                    WHERE tenant_id=:t AND professional_id=:p AND status IN ('confirmed','hold')
                    AND (status <> 'hold' OR hold_expires_at>now())
                    AND occupied_from<:end AND occupied_until>:start LIMIT 1"""),
                    {"t":tenant,"p":professional,"start":start,"end":end})).scalar_one_or_none()
                if overlap: raise HTTPException(409,"Existem agendamentos neste período")
                overlap=(await db.execute(text("""SELECT 1 FROM schedule_blocks
                    WHERE tenant_id=:t AND professional_id=:p AND starts_at<:end
                    AND ends_at>:start LIMIT 1"""),
                    {"t":tenant,"p":professional,"start":start,"end":end})).scalar_one_or_none()
                if overlap: raise HTTPException(409,"Horário já bloqueado")
                row=(await db.execute(text("""INSERT INTO schedule_blocks
                    (id,tenant_id,professional_id,starts_at,ends_at,reason,origin,created_by_account_id)
                    VALUES(:id,:t,:p,:start,:end,:reason,'professional',:actor)
                    RETURNING id,starts_at,ends_at,reason,origin"""),
                    {"id":identity,"t":tenant,"p":professional,"start":start,
                     "end":end,"reason":payload.reason,"actor":actor.account_id})).mappings().one()
                await management.audit(db,tenant,"professional_block_created","schedule_blocks",identity)
                return dict(row)


@router.delete("/blocks/{block_id}")
async def remove_my_block(block_id: UUID, actor: Actor = Depends(authenticate)):
    async with individual_access(actor) as (tenant,professional):
        async with get_session_factory()() as db:
            async with tenant_transaction(db,tenant):
                await profile(db,tenant,professional)
                await require_feature(db,tenant,"agenda.manage")
                await acquire_professional_lock(db,tenant,professional)
                owner_override=await may_manage_company_blocks(
                    tenant,professional,actor.account_id)
                result=(await db.execute(text("""DELETE FROM schedule_blocks
                    WHERE tenant_id=:t AND professional_id=:p AND id=:id
                    AND ((origin='professional' AND created_by_account_id=:actor)
                         OR :override)
                    RETURNING id"""),
                    {"t":tenant,"p":professional,"id":block_id,
                     "actor":actor.account_id,"override":owner_override})).scalar_one_or_none()
                if result is None: raise HTTPException(404,"Bloqueio não encontrado ou protegido pela empresa")
                await management.audit(db,tenant,"professional_block_removed","schedule_blocks",block_id)
                return {"ok":True}



class ProfessionalBooking(BaseModel):
    """No tenant or professional ID is accepted from the browser."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    service_ids: list[UUID] = Field(min_length=1,max_length=6)
    customer_name: str = Field(min_length=2,max_length=120)
    customer_phone: str = Field(pattern=r"^(?:\+55[1-9][0-9]{9,10}|\+5959[0-9]{8})$")
    start_at: AwareDatetime
    idempotency_key: str = Field(min_length=8,max_length=120,
        pattern=r"^[A-Za-z0-9._:-]+$")
    appointment_messages_opt_in: StrictBool = False

    @field_validator("service_ids")
    @classmethod
    def distinct_services(cls, values):
        if len(set(values)) != len(values):
            raise ValueError("Não repita serviços")
        return values


@router.get("/services")
async def my_services(actor: Actor = Depends(authenticate)):
    async with individual_access(actor) as (tenant,professional):
        async with get_session_factory()() as db:
            async with tenant_transaction(db,tenant):
                await profile(db,tenant,professional)
                await require_feature(db,tenant,"services.manage")
                rows=(await db.execute(text("""SELECT s.id,s.name,s.duration_min,
                    s.price_cents,s.price_mode FROM professional_services ps
                    JOIN services s ON s.tenant_id=ps.tenant_id AND s.id=ps.service_id
                    WHERE ps.tenant_id=:t AND ps.professional_id=:p AND s.active
                      AND COALESCE(s.catalog_kind,'base') <> 'variant'
                    ORDER BY s.name,s.id LIMIT 150"""),
                    {"t":tenant,"p":professional})).mappings().all()
                return {"items":[dict(item) for item in rows]}


@router.get("/availability")
async def my_availability(day: date, service_ids: list[UUID] = Query(),
                          actor: Actor = Depends(authenticate)):
    if not 1<=len(service_ids)<=6 or len(set(service_ids))!=len(service_ids):
        raise HTTPException(422,"Selecione de um a seis serviços diferentes")
    async with individual_access(actor) as (tenant,professional):
        async with get_session_factory()() as db:
            async with tenant_transaction(db,tenant):
                await profile(db,tenant,professional)
                await require_feature(db,tenant,"agenda.manage")
        async with get_session_factory()() as db:
            result=await list_slots(db,tenant,professional,service_ids,day)
            return {"slots":result["slots"],"duration_min":result["duration_min"],
                    "price_cents":result["price_cents"],"timezone":result["timezone"]}


@router.post("/appointments",status_code=201)
async def book_my_appointment(payload: ProfessionalBooking,actor: Actor = Depends(authenticate)):
    """Reuse booking hold/confirmation; never manufacture a parallel appointment."""
    from app.appointment_lifecycle import LifecycleActor
    context=LifecycleActor(role="professional",source="panel",account_id=actor.account_id)
    async with individual_access(actor) as (tenant,professional):
        async with get_session_factory()() as db:
            async with tenant_transaction(db,tenant):
                await profile(db,tenant,professional)
                await require_feature(db,tenant,"agenda.manage")
        async with get_session_factory()() as db:
            try:
                hold=await book_appointment(
                    db,tenant,professional,payload.service_ids,payload.customer_phone,
                    payload.customer_name,payload.start_at,payload.idempotency_key,
                    context=context,
                )
                result=await confirm_hold(
                    db,tenant,UUID(str(hold["id"])),
                    idempotency_key="professional-confirm:"+payload.idempotency_key,
                    appointment_messages_opt_in=payload.appointment_messages_opt_in,
                    context=context,
                )
                return project_appointment(result)
            except IntegrityError as exc:
                raise safe_conflict(exc) from None
