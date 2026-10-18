"""Atomic recurring closures, using the booking engine's professional lock."""
from datetime import date, datetime, time, timedelta, timezone
import json
from typing import Literal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import text

from app.booking_service import acquire_professional_lock
from app.catalog_api import Actor, authenticate, audit
from app.management_api import management_session
from app.management_models import request_identity

router = APIRouter(prefix="/api/v1/tenants/{tenant_id}/management", tags=["Agenda recorrente"])


class BlockSeries(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    professional_id: UUID
    start_date: date
    until_date: date
    frequency: Literal["daily", "weekly", "weekdays"]
    weekdays: list[int] = Field(default_factory=list, max_length=7)
    kind: Literal["hours", "day_off"]
    start_time: time | None = None
    end_time: time | None = None
    reason: str | None = Field(default=None, max_length=200)
    idempotency_key: str = Field(min_length=8, max_length=120)

    @field_validator("weekdays")
    @classmethod
    def valid_weekdays(cls, values):
        if any(day not in range(7) for day in values) or len(values) != len(set(values)):
            raise ValueError("Selecione dias diferentes entre segunda (0) e domingo (6)")
        return sorted(values)

    @model_validator(mode="after")
    def valid_period(self):
        if self.start_date.year < 1900 or self.until_date.year > 2100 or not 0 <= (self.until_date-self.start_date).days < 366:
            raise ValueError("A repetição permite um intervalo de até 366 dias")
        if self.frequency == "weekdays" and not self.weekdays:
            raise ValueError("Selecione pelo menos um dia da semana")
        if self.kind == "hours":
            if self.start_time is None or self.end_time is None or self.start_time >= self.end_time:
                raise ValueError("Informe o início e o fim do bloqueio no mesmo dia")
            if self.start_time.tzinfo is not None or self.end_time.tzinfo is not None:
                raise ValueError("Use o horário local da empresa, sem fuso no campo de hora")
        elif self.start_time is not None or self.end_time is not None:
            raise ValueError("Uma folga de dia inteiro não recebe horários parciais")
        return self


def local_instant(day, hour, zone):
    naive = datetime.combine(day, hour)
    a, b = naive.replace(tzinfo=zone, fold=0), naive.replace(tzinfo=zone, fold=1)
    if a.utcoffset() != b.utcoffset() or a.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) != naive:
        raise HTTPException(422, "A série cruza um horário local ambíguo ou inexistente. Ajuste o horário neste dia")
    return a.astimezone(timezone.utc)


def expand_occurrences(payload, timezone_name):
    zone = ZoneInfo(timezone_name)
    days = set(payload.weekdays) if payload.frequency == "weekdays" else {payload.start_date.weekday()}
    result = []
    for offset in range((payload.until_date - payload.start_date).days + 1):
        day = payload.start_date + timedelta(days=offset)
        if payload.frequency != "daily" and day.weekday() not in days:
            continue
        start = local_instant(day, payload.start_time or time.min, zone)
        end = local_instant(day + timedelta(days=payload.kind == "day_off"), payload.end_time or time.min, zone)
        result.append({"occurrence_date": day, "start_at": start, "end_at": end})
    if not result:
        raise HTTPException(422, "Nenhum dos dias selecionados está dentro do período")
    return result


async def validate_free(db, tenant_id, professional_id, occurrences, *, ignored=None):
    raw = json.dumps(jsonable_encoder(occurrences))
    params = {"tenant": tenant_id, "pro": professional_id, "items": raw, "ignored": ignored or UUID(int=0)}
    conflict = (await db.execute(text("""
        WITH proposed AS (SELECT * FROM jsonb_to_recordset(CAST(:items AS jsonb)) AS x(start_at timestamptz,end_at timestamptz))
        SELECT true FROM proposed p WHERE EXISTS (
          SELECT 1 FROM appointments a WHERE a.tenant_id=:tenant AND a.professional_id=:pro
            AND (a.status='confirmed' OR (a.status='hold' AND a.hold_expires_at>now()))
            AND a.occupied_from<p.end_at AND a.occupied_until>p.start_at)
        OR EXISTS (SELECT 1 FROM schedule_blocks b WHERE b.tenant_id=:tenant AND b.professional_id=:pro
          AND b.id<>:ignored AND b.starts_at<p.end_at AND b.ends_at>p.start_at)
        OR EXISTS (SELECT 1 FROM time_off b WHERE b.tenant_id=:tenant AND b.professional_id=:pro
          AND b.id<>:ignored AND b.starts_at<p.end_at AND b.ends_at>p.start_at) LIMIT 1
    """), params)).scalar_one_or_none()
    if conflict:
        raise HTTPException(409, "Uma ocorrência coincide com reserva ou bloqueio existente. Nenhum bloqueio foi criado")


async def series_detail(db, tenant_id, identity):
    row = (await db.execute(text("SELECT * FROM management_block_series WHERE tenant_id=:tenant AND id=:id"),
                           {"tenant": tenant_id, "id": identity})).mappings().one_or_none()
    if row is None:
        raise HTTPException(404, "Série de bloqueios não encontrada")
    occurrences = (await db.execute(text("""SELECT id,block_id,occurrence_date,start_at,end_at,active
        FROM management_block_occurrences WHERE tenant_id=:tenant AND series_id=:id ORDER BY occurrence_date"""),
        {"tenant": tenant_id, "id": identity})).mappings().all()
    return {key: value for key, value in row.items() if key != "tenant_id"} | {"occurrences": [dict(x) for x in occurrences]}


@router.get("/block-series")
async def list_series(tenant_id: UUID, start_date: date, end_date: date, actor: Actor = Depends(authenticate)):
    if not 0 <= (end_date-start_date).days < 366:
        raise HTTPException(422, "Selecione até 366 dias")
    async with management_session(tenant_id, actor) as db:
        ids = (await db.execute(text("""SELECT id FROM management_block_series WHERE tenant_id=:tenant
            AND start_date<=:stop AND until_date>=:start ORDER BY start_date,id LIMIT 250"""),
            {"tenant": tenant_id, "start": start_date, "stop": end_date})).scalars().all()
        return {"items": [await series_detail(db, tenant_id, identity) for identity in ids]}


@router.post("/block-series", status_code=201)
async def create_series(tenant_id: UUID, payload: BlockSeries, actor: Actor = Depends(authenticate)):
    async with management_session(tenant_id, actor, write=True) as db:
        await acquire_professional_lock(db, tenant_id, payload.professional_id)
        professional = (await db.execute(text("SELECT id FROM professionals WHERE tenant_id=:tenant AND id=:id"),
            {"tenant": tenant_id, "id": payload.professional_id})).scalar_one_or_none()
        if professional is None:
            raise HTTPException(404, "Profissional não encontrado nesta empresa")
        values = payload.model_dump(exclude={"idempotency_key"})
        identity, replay = await request_identity(db, tenant_id, "block_series", payload.idempotency_key, values)
        if replay:
            detail = await series_detail(db, tenant_id, identity)
            return {"id": identity, "count": len(detail["occurrences"]), "blocks": detail["occurrences"], "replayed": True}
        tz = (await db.execute(text("SELECT timezone FROM tenants WHERE id=:tenant"), {"tenant": tenant_id})).scalar_one()
        occurrences = expand_occurrences(payload, tz)
        await validate_free(db, tenant_id, payload.professional_id, occurrences)
        await db.execute(text("""INSERT INTO management_block_series
            (id,tenant_id,professional_id,kind,frequency,start_date,until_date,weekdays,start_time,end_time,reason)
            VALUES(:id,:tenant,:professional_id,:kind,:frequency,:start_date,:until_date,CAST(:weekdays AS jsonb),:start_time,:end_time,:reason)"""),
            {**values, "id": identity, "tenant": tenant_id, "weekdays": json.dumps(payload.weekdays)})
        table = "time_off" if payload.kind == "day_off" else "schedule_blocks"
        for item in occurrences:
            block_id = uuid4()
            params = {"tenant": tenant_id, "id": block_id, "series": identity, "pro": payload.professional_id, "reason": payload.reason, **item}
            await db.execute(text(f"""INSERT INTO {table}(id,tenant_id,professional_id,starts_at,ends_at,reason)
                VALUES(:id,:tenant,:pro,:start_at,:end_at,:reason)"""), params)
            await db.execute(text("""INSERT INTO management_block_occurrences(tenant_id,series_id,block_id,occurrence_date,start_at,end_at)
                VALUES(:tenant,:series,:id,:occurrence_date,:start_at,:end_at)"""), params)
        await audit(db, tenant_id, "block_series_created", "management_block_series", identity)
        detail = await series_detail(db, tenant_id, identity)
        return {"id": identity, "count": len(occurrences), "blocks": detail["occurrences"], "replayed": False}


@router.delete("/block-series/{series_id}")
async def remove_series(tenant_id: UUID, series_id: UUID, scope: Literal["one", "following", "all"],
                        occurrence_date: date | None = None, actor: Actor = Depends(authenticate)):
    if scope != "all" and occurrence_date is None:
        raise HTTPException(422, "Informe a data da ocorrência")
    async with management_session(tenant_id, actor, write=True) as db:
        detail = await series_detail(db, tenant_id, series_id)
        await acquire_professional_lock(db, tenant_id, detail["professional_id"])
        relation = {"one": "=", "following": ">=", "all": None}[scope]
        predicate = " AND occurrence_date" + relation + ":day" if relation else ""
        rows = (await db.execute(text("""UPDATE management_block_occurrences SET active=false,removed_at=now()
            WHERE tenant_id=:tenant AND series_id=:id AND active""" + predicate + " RETURNING block_id"),
            {"tenant": tenant_id, "id": series_id, "day": occurrence_date})).scalars().all()
        table = "time_off" if detail["kind"] == "day_off" else "schedule_blocks"
        for identity in rows:
            await db.execute(text(f"DELETE FROM {table} WHERE tenant_id=:tenant AND id=:id"), {"tenant": tenant_id, "id": identity})
        if rows:
            await audit(db, tenant_id, "block_series_removed", "management_block_series", series_id)
        return {"id": series_id, "scope": scope, "removed_count": len(rows)}


async def guard_catalog_block(db, tenant_id, resource, *, values=None, item_id=None, deleting=False):
    """Keep legacy one-off controls consistent with recurring operations."""
    if resource not in {"schedule_blocks", "time_off"}:
        return
    prior = None
    if item_id is not None:
        prior = (await db.execute(text(f"SELECT professional_id FROM {resource} WHERE tenant_id=:tenant AND id=:id"),
            {"tenant": tenant_id, "id": item_id})).mappings().one_or_none()
    # Stable order prevents deadlocks if a one-off closure is moved to another professional.
    identities = {prior["professional_id"]} if prior else set()
    if values:
        identities.add(values["professional_id"])
    for identity in sorted(identities, key=str):
        await acquire_professional_lock(db, tenant_id, identity)
    if item_id is not None:
        series = (await db.execute(text("SELECT series_id FROM management_block_occurrences WHERE tenant_id=:tenant AND block_id=:id AND active"),
            {"tenant": tenant_id, "id": item_id})).scalar_one_or_none()
        if series:
            raise HTTPException(409, "Este bloqueio pertence a uma repetição. Escolha remover a ocorrência ou a série na agenda")
    if not deleting and values:
        await validate_free(db, tenant_id, values["professional_id"],
            [{"start_at": values["starts_at"], "end_at": values["ends_at"]}], ignored=item_id)
