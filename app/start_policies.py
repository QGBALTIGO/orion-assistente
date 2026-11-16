"""Optional, tenant-scoped allowlists of appointment start times.

The policy belongs to a professional and weekday, not to a service or booking.
It only chooses candidate starts; booking_service still validates occupancy.
"""
import re
from datetime import time
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import text

from app.catalog_api import Actor, authenticate, audit, authorize, ensure_tenant
from app.database import get_session_factory, tenant_transaction

router = APIRouter(prefix="/api/v1/tenants/{tenant_id}/professionals/{professional_id}",
                   tags=["Horários de início"])
TIME_PATTERN = re.compile(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]\Z")


class StartPolicyUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["automatic", "manual"]
    start_times: list[str] | None = Field(default=None, max_length=1440)
    expected_version: int = Field(ge=0, strict=True)

    @field_validator("start_times")
    @classmethod
    def validate_times(cls, values):
        if values is None:
            return None
        if any(not TIME_PATTERN.fullmatch(value) for value in values):
            raise ValueError("Informe cada horário no formato HH:mm, sem segundos")
        if len(set(values)) != len(values):
            raise ValueError("Não repita horários na lista")
        return sorted(values)


def local_time(value):
    return time.fromisoformat(value) if isinstance(value, str) else value


def policy_response(weekday, row=None):
    return {"weekday": weekday, "mode": row["mode"] if row else "automatic",
            "start_times": sorted(local_time(value).strftime("%H:%M")
                                  for value in (row["start_times"] if row else ())),
            "version": row["version"] if row else 0}


async def read_start_policy(db, tenant, professional, weekday):
    return (await db.execute(text("""
        SELECT id,mode,start_times,version FROM professional_start_policies
        WHERE tenant_id=:tenant AND professional_id=:professional AND weekday=:weekday
    """), {"tenant": tenant, "professional": professional, "weekday": weekday})).mappings().first()


async def read_working_day(db, tenant, professional, weekday):
    parameters = {"tenant": tenant, "professional": professional, "weekday": weekday}
    hours = (await db.execute(text("""
        SELECT h.id,h.start_time,h.end_time,p.mode AS start_mode,
               p.start_times AS allowed_starts,p.version AS start_policy_version
        FROM working_hours h LEFT JOIN professional_start_policies p
          ON p.tenant_id=h.tenant_id AND p.professional_id=h.professional_id AND p.weekday=h.weekday
        WHERE h.tenant_id=:tenant AND h.professional_id=:professional AND h.weekday=:weekday
        ORDER BY h.start_time,h.end_time,h.id
    """), parameters)).mappings().all()
    pauses = (await db.execute(text("""
        SELECT b.working_hour_id,b.start_time,b.end_time FROM working_breaks b
        JOIN working_hours h ON h.tenant_id=b.tenant_id AND h.id=b.working_hour_id
        WHERE h.tenant_id=:tenant AND h.professional_id=:professional AND h.weekday=:weekday
        ORDER BY b.start_time,b.end_time,b.id
    """), parameters)).mappings().all() if hours else ()
    return tuple(hours), tuple(pauses)


def policy_from_hours(hours):
    # All periods of the same professional/weekday share the one policy. Read
    # them in one SQL snapshot, retaining the existing constant grid query count.
    if not hours or hours[0].get("start_mode") is None:
        return None
    return {"mode": hours[0]["start_mode"], "start_times": hours[0]["allowed_starts"],
            "version": hours[0]["start_policy_version"]}


def open_periods(hours, pauses):
    """Union of the day's shifts after subtracting each shift's own pauses."""
    by_hour = {}
    for pause in pauses:
        by_hour.setdefault(str(pause["working_hour_id"]), []).append(
            (local_time(pause["start_time"]), local_time(pause["end_time"])))
    periods = []
    for hour in hours:
        begin, end = local_time(hour["start_time"]), local_time(hour["end_time"])
        cursor = begin
        for pause_start, pause_end in sorted(by_hour.get(str(hour["id"]), ())):
            pause_start, pause_end = max(begin, pause_start), min(end, pause_end)
            if pause_start >= pause_end:
                continue
            if cursor < pause_start:
                periods.append((cursor, pause_start))
            cursor = max(cursor, pause_end)
        if cursor < end:
            periods.append((cursor, end))
    merged = []
    for begin, end in sorted(periods):
        if merged and begin <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((begin, end))
    return tuple(merged)


def validate_manual_times(values, hours, pauses, *, status=422):
    if not values:
        raise HTTPException(status, "Adicione pelo menos um horário antes de ativar o modo manual")
    periods = open_periods(hours, pauses)
    invalid = [local_time(value).strftime("%H:%M") for value in values
               if not any(begin <= local_time(value) < end for begin, end in periods)]
    if invalid:
        raise HTTPException(status, "Horários manuais fora do expediente ou dentro de uma pausa: "
                            + ", ".join(invalid[:12])
                            + ". Ajuste a lista ou use o modo automático antes de alterar o expediente")


async def validate_active_policies(db, tenant, scopes):
    """Run after an hours/break edit in its transaction; errors roll it back."""
    for professional, weekday in sorted(scopes, key=lambda value: (str(value[0]), value[1])):
        policy = await read_start_policy(db, tenant, professional, weekday)
        if policy is not None and policy["mode"] == "manual":
            hours, pauses = await read_working_day(db, tenant, professional, weekday)
            if not hours:
                raise HTTPException(409, "Este dia usa horários manuais. Volte ao modo automático "
                                    "antes de remover o último expediente")
            validate_manual_times(policy["start_times"], hours, pauses, status=409)


async def _schedule_edit_scopes(db, tenant, resource, values, item_id):
    scopes = set()
    if item_id is not None:
        if resource == "working_hours":
            statement = "SELECT professional_id,weekday FROM working_hours WHERE tenant_id=:tenant AND id=:id"
        else:
            statement = """SELECT h.professional_id,h.weekday FROM working_breaks b
                JOIN working_hours h ON h.tenant_id=b.tenant_id AND h.id=b.working_hour_id
                WHERE b.tenant_id=:tenant AND b.id=:id"""
        prior = (await db.execute(text(statement), {"tenant": tenant, "id": item_id})).mappings().first()
        if prior:
            scopes.add((prior["professional_id"], prior["weekday"]))
    if values:
        if resource == "working_hours":
            scopes.add((values["professional_id"], values["weekday"]))
        else:
            following = (await db.execute(text("""SELECT professional_id,weekday FROM working_hours
                WHERE tenant_id=:tenant AND id=:id"""),
                {"tenant": tenant, "id": values["working_hour_id"]})).mappings().first()
            if following:
                scopes.add((following["professional_id"], following["weekday"]))
    return scopes


async def lock_schedule_edit(db, tenant, resource, *, values=None, item_id=None):
    """Acquire booking's professional locks BEFORE catalog row locks.

    Re-read after waiting: a concurrent writer may have moved the weekday/pause.
    If a pause moved to an unlocked professional, retry instead of acquiring locks
    out of order. The caller validates the resulting schedule before committing.
    """
    if resource not in {"working_hours", "working_breaks"}:
        return set()
    scopes = await _schedule_edit_scopes(db, tenant, resource, values, item_id)
    locked_professionals = {value[0] for value in scopes}
    from app.booking_service import acquire_professional_lock
    for professional in sorted(locked_professionals, key=str):
        await acquire_professional_lock(db, tenant, professional)
    current = await _schedule_edit_scopes(db, tenant, resource, values, item_id)
    if not {value[0] for value in current}.issubset(locked_professionals):
        raise HTTPException(409, "O expediente ou a pausa foi alterado. Atualize antes de salvar")
    return current


@router.get("/start-policies")
async def get_start_policies(tenant_id: UUID, professional_id: UUID,
                             actor: Actor = Depends(authenticate)):
    authorize(actor, tenant_id)
    async with get_session_factory()() as db:
        async with tenant_transaction(db, tenant_id):
            await ensure_tenant(db, tenant_id)
            professional = (await db.execute(text("""SELECT id FROM professionals
                WHERE tenant_id=:tenant AND id=:professional"""),
                {"tenant": tenant_id, "professional": professional_id})).scalar_one_or_none()
            if professional is None:
                raise HTTPException(404, "Profissional não encontrado nesta empresa")
            rows = (await db.execute(text("""SELECT weekday,mode,start_times,version
                FROM professional_start_policies
                WHERE tenant_id=:tenant AND professional_id=:professional ORDER BY weekday"""),
                {"tenant": tenant_id, "professional": professional_id})).mappings().all()
            by_day = {row["weekday"]: row for row in rows}
            return {"professional_id": str(professional_id),
                    "policies": [policy_response(day, by_day.get(day)) for day in range(7)]}


@router.put("/start-policies/{weekday}")
async def put_start_policy(tenant_id: UUID, professional_id: UUID, payload: StartPolicyUpdate,
                           weekday: int = Path(ge=0, le=6), actor: Actor = Depends(authenticate)):
    authorize(actor, tenant_id, write=True)
    from app.booking_service import acquire_professional_lock
    async with get_session_factory()() as db:
        async with tenant_transaction(db, tenant_id):
            await ensure_tenant(db, tenant_id)
            await acquire_professional_lock(db, tenant_id, professional_id)
            # Serialize against onboarding's final catalog snapshot and deletion.
            professional = (await db.execute(text("""SELECT id FROM professionals
                WHERE tenant_id=:tenant AND id=:professional FOR SHARE"""),
                {"tenant": tenant_id, "professional": professional_id})).scalar_one_or_none()
            if professional is None:
                raise HTTPException(404, "Profissional não encontrado nesta empresa")
            current = await read_start_policy(db, tenant_id, professional_id, weekday)
            current_version = current["version"] if current else 0
            if payload.expected_version != current_version:
                raise HTTPException(409, "Os horários de início foram alterados. Atualize antes de salvar")
            values = ([time.fromisoformat(value) for value in payload.start_times]
                      if payload.start_times is not None else list(current["start_times"] if current else ()))
            if payload.mode == "manual":
                hours, pauses = await read_working_day(db, tenant_id, professional_id, weekday)
                validate_manual_times(values, hours, pauses)
            row = (await db.execute(text("""
                INSERT INTO professional_start_policies
                    (tenant_id,professional_id,weekday,mode,start_times,version)
                VALUES (:tenant,:professional,:weekday,:mode,CAST(:times AS time[]),1)
                ON CONFLICT (tenant_id,professional_id,weekday) DO UPDATE
                SET mode=EXCLUDED.mode,start_times=EXCLUDED.start_times,
                    version=professional_start_policies.version+1,updated_at=clock_timestamp()
                RETURNING id,weekday,mode,start_times,version
            """), {"tenant": tenant_id, "professional": professional_id, "weekday": weekday,
                   "mode": payload.mode, "times": values})).mappings().one()
            await audit(db, tenant_id, "updated", "professional_start_policies", row["id"])
            return policy_response(weekday, row)
