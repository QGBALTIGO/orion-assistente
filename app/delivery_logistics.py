"""Tenant-owned coverage, delivery windows and courier registration."""
from datetime import datetime, timedelta, timezone
import json
from typing import Literal
import unicodedata
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import Field, StrictBool, StrictInt, field_validator, model_validator
from sqlalchemy import text

from app import commercial_service as commerce
from app.business_profile_schemas import ProfileModel
from app.catalog_api import audit
from app.commercial_schemas import IdempotencyKey
from app.delivery_catalog import LIMIT, run_once
from app.management_schemas import MAX_MONEY, normalize_phone
from app.brazilian_fields import normalize_state
from app.onboarding_schemas import Address


def normalize_place(value):
    decomposed = unicodedata.normalize("NFKD", value).casefold()
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(stripped.split())


def place_key(location):
    return location.state, normalize_place(location.city), normalize_place(location.neighborhood)


class Area(ProfileModel):
    city: str = Field(min_length=1, max_length=120)
    state: str = Field(pattern=r"^[A-Z]{2}$")
    neighborhood: str = Field(min_length=1, max_length=120)
    kind: Literal["included", "excluded"] = "included"
    fee_cents: StrictInt = Field(default=0, ge=0, le=MAX_MONEY)
    minimum_cents: StrictInt = Field(default=0, ge=0, le=MAX_MONEY)
    free_from_cents: StrictInt | None = Field(default=None, gt=0, le=MAX_MONEY)
    eta_minutes: StrictInt | None = Field(default=None, ge=1, le=1440)

    _state = field_validator("state", mode="before")(normalize_state)

    @model_validator(mode="after")
    def validate_area(self):
        if not all(place_key(self)):
            raise ValueError("Informe a cidade, a UF e o bairro da área")
        if self.kind == "excluded" and any((self.fee_cents, self.minimum_cents, self.free_from_cents, self.eta_minutes)):
            raise ValueError("Remova as taxas e condições do bairro excluído")
        return self


class DeliveryWindow(ProfileModel):
    weekday: StrictInt = Field(ge=0, le=6)
    start: str = Field(pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")
    end: str = Field(pattern=r"^(?:(?:[01][0-9]|2[0-3]):[0-5][0-9]|24:00)$")

    @model_validator(mode="after")
    def validate_window(self):
        if self.start >= self.end:
            raise ValueError("A janela deve abrir e fechar no mesmo dia. Use duas janelas quando passar de meia-noite.")
        return self


class LogisticsPolicy(ProfileModel):
    coverage_mode: Literal["manual", "areas"] = "manual"
    areas: list[Area] = Field(default_factory=list, max_length=500)
    scheduling_enabled: StrictBool = False
    lead_minutes: StrictInt = Field(default=60, ge=0, le=1440)
    horizon_days: StrictInt = Field(default=7, ge=1, le=90)
    windows: list[DeliveryWindow] = Field(default_factory=list, max_length=70)

    @model_validator(mode="after")
    def validate_policy(self):
        keys = [place_key(area) for area in self.areas]
        if len(set(keys)) != len(keys):
            raise ValueError("Cadastre cada bairro uma vez por cidade e UF")
        if self.coverage_mode == "areas" and not any(area.kind == "included" for area in self.areas):
            raise ValueError("Cadastre pelo menos um bairro atendido para ativar a cobertura")
        if self.scheduling_enabled and not self.windows:
            raise ValueError("Informe ao menos uma janela para permitir agendamentos")
        windows = sorted(self.windows, key=lambda window: (window.weekday, window.start))
        if any(before.weekday == after.weekday and before.end > after.start
               for before, after in zip(windows, windows[1:])):
            raise ValueError("As janelas do mesmo dia não podem se sobrepor")
        return self


class PolicyUpdate(LogisticsPolicy):
    expected_revision: StrictInt = Field(ge=0)


class Courier(ProfileModel):
    name: str = Field(min_length=1, max_length=120)
    phone: str | None = Field(default=None, max_length=40)
    active: StrictBool = True

    _phone = field_validator("phone")(normalize_phone)


class CourierCreate(Courier):
    idempotency_key: IdempotencyKey


class CourierUpdate(Courier):
    expected_revision: StrictInt = Field(ge=1)


async def lock_policy(db, tenant, *, exclusive=False):
    function = "pg_advisory_xact_lock" if exclusive else "pg_advisory_xact_lock_shared"
    await db.execute(text(f"SELECT {function}(hashtextextended(:scope,0))"), {"scope": f"delivery-logistics:{tenant}"})


async def get_policy(db, tenant, *, include_offering=False):
    row = (await db.execute(text("""
        SELECT t.timezone,p.configuration,p.area_terms,coalesce(p.revision,0) AS revision,
               b.configuration->>'delivery_offering' AS offering
        FROM tenants t LEFT JOIN delivery_logistics_policies p ON p.tenant_id=t.id
        LEFT JOIN business_onboarding b ON b.tenant_id=t.id
        WHERE t.id=:tenant
    """), {"tenant": tenant})).mappings().first()
    if row is None:
        raise HTTPException(404, "Estabelecimento indisponível")
    config = dict(row["configuration"] or {})
    terms = {tuple(item["key"]): item["terms"] for item in row["area_terms"] or []}
    config["areas"] = [{**area, **terms.get(place_key(Area.model_validate(area)), {})} for area in config.get("areas", [])]
    return {**LogisticsPolicy.model_validate(config).model_dump(),
            "revision": row["revision"], "timezone": row["timezone"],
            **({"offering": row["offering"]} if include_offering else {})}


async def save_policy(db, tenant, access, payload):
    await lock_policy(db, tenant, exclusive=True)
    existing = await get_policy(db, tenant)
    if existing["revision"] != payload.expected_revision:
        commerce.conflict("A configuração mudou. Reabra a página e confira antes de salvar.")
    config = payload.model_dump(exclude={"expected_revision"})
    if existing["revision"] and all(existing[key] == value for key, value in config.items()):
        return existing
    term_fields = {"minimum_cents", "free_from_cents", "eta_minutes"}
    legacy_config = {**config, "areas": [area.model_dump(exclude=term_fields) for area in payload.areas]}
    terms = [{"key": place_key(area), "terms": area.model_dump(include=term_fields, exclude_defaults=True)}
             for area in payload.areas if area.minimum_cents or area.free_from_cents or area.eta_minutes]
    revision = (await db.execute(text("""
        INSERT INTO delivery_logistics_policies (tenant_id,configuration,area_terms,revision)
        VALUES (:tenant,CAST(:configuration AS jsonb),CAST(:terms AS jsonb),1)
        ON CONFLICT (tenant_id) DO UPDATE SET configuration=EXCLUDED.configuration,
            area_terms=EXCLUDED.area_terms,
            revision=delivery_logistics_policies.revision+1,updated_at=now()
        RETURNING revision
    """), {"tenant": tenant, "configuration": json.dumps(legacy_config), "terms": json.dumps(terms)})).scalar_one()
    await audit(db, tenant, "delivery_logistics_updated", "delivery_logistics", tenant)
    return {**config, "revision": revision, "timezone": existing["timezone"]}


async def public_delivery_available(db, tenant):
    await lock_policy(db, tenant)
    policy = await get_policy(db, tenant)
    return policy["coverage_mode"] == "areas"


def check_schedule(policy, scheduled_at, now):
    if scheduled_at is None:
        return None
    if not policy["scheduling_enabled"]:
        raise HTTPException(422, "Este estabelecimento ainda não aceita entregas agendadas")
    if scheduled_at.tzinfo is None or scheduled_at.utcoffset() is None:
        raise HTTPException(422, "Informe a data e a hora do agendamento com fuso horário")
    instant = scheduled_at.astimezone(timezone.utc)
    if instant <= now or instant < now + timedelta(minutes=policy["lead_minutes"]):
        raise HTTPException(422, "O agendamento está fora da antecedência mínima")
    if instant > now + timedelta(days=policy["horizon_days"]):
        raise HTTPException(422, "A data ultrapassa o limite de dias para agendamento")
    local = instant.astimezone(ZoneInfo(policy["timezone"]))
    hour = local.strftime("%H:%M")
    if not any(window["weekday"] == local.weekday() and window["start"] <= hour < window["end"]
               for window in policy["windows"]):
        raise HTTPException(422, "Selecione um horário em uma janela de entrega")
    return instant


async def quote_delivery(db, tenant, address: Address | None, scheduled_at: datetime | None = None,
                         *, requested_fee_cents=0, require_coverage=False, lock=False, now=None):
    """Resolve a delivery fee and accepted appointment at the time of ordering."""
    if lock:
        await lock_policy(db, tenant)
    policy = await get_policy(db, tenant)
    fee = requested_fee_cents
    selection = None
    if policy["coverage_mode"] == "areas":
        if address is None:
            raise HTTPException(422, "Informe o endereço para calcular a taxa de entrega")
        key = place_key(address)
        selection = next((area for row in policy["areas"]
                          if place_key(area := Area.model_validate(row)) == key), None)
        if selection is None or selection.kind != "included":
            raise HTTPException(422, "Este bairro está fora da área de entrega")
        fee = selection.fee_cents
    elif require_coverage:
        raise HTTPException(422, "O estabelecimento ainda não configurou a cobertura de entrega")
    return {
        "delivery_fee_cents": fee,
        "policy_revision": policy["revision"],
        "area": ({**selection.model_dump(exclude={"minimum_cents", "free_from_cents", "eta_minutes"}),
                  **selection.model_dump(include={"minimum_cents", "free_from_cents", "eta_minutes"}, exclude_defaults=True)}
                 if selection else None),
        "scheduled_at": check_schedule(policy, scheduled_at, now or commerce.now_utc()),
    }


async def save_order_logistics(db, tenant, order_id, logistics):
    if logistics is None:
        return
    saved = (await db.execute(text("""
        UPDATE delivery_order_details SET scheduled_at=:scheduled_at,logistics_snapshot=CAST(:snapshot AS jsonb)
        WHERE tenant_id=:tenant AND order_id=:id AND mode='delivery' RETURNING order_id
    """), {"tenant": tenant, "id": order_id, "scheduled_at": logistics["scheduled_at"],
             "snapshot": json.dumps(jsonable_encoder(logistics))})).scalar_one_or_none()
    if saved is None:
        commerce.conflict("O pedido mudou. Confira a logística antes de confirmar.")


async def reschedule_delivery(db, tenant, order, scheduled_at, *, returned_to_stock=False,
                              recipient_absent=False, note=None, now=None):
    """Amend the open order after the caller locks it for a transition."""
    dispatched = order["status"] == "dispatched"
    if order["mode"] != "delivery" or order["status"] not in {"confirmed", "dispatched"}:
        commerce.conflict("Somente entregas em aberto podem ser reprogramadas")
    if recipient_absent and not dispatched:
        commerce.conflict("Somente uma entrega em rota pode registrar ausência do destinatário")
    if dispatched and not returned_to_stock:
        raise HTTPException(422, "Confirme que o pedido voltou ao estoque antes de retirar o entregador")
    if scheduled_at is None and not recipient_absent:
        raise HTTPException(422, "Informe a nova data e hora da entrega")
    instant = None
    if scheduled_at is not None:
        await lock_policy(db, tenant)
        policy = await get_policy(db, tenant)
        instant = check_schedule(policy, scheduled_at, now or commerce.now_utc())
    await db.execute(text("""
        UPDATE delivery_order_details SET scheduled_at=:scheduled_at,
            courier_id=NULL,courier_name=NULL,dispatched_at=NULL
        WHERE tenant_id=:tenant AND order_id=:id AND mode='delivery'
    """), {"tenant": tenant, "id": order["id"], "scheduled_at": instant})
    return {"previous_scheduled_at": order["scheduled_at"], "scheduled_at": instant,
            "previous_courier_id": order["courier_id"], "courier_name": order["courier_name"],
            "returned_to_stock": dispatched, "note": note}


COURIER_FIELDS = "id,name,phone,active,revision"


async def list_couriers(db, tenant, *, search="", active_only=False, offset=0):
    rows = (await db.execute(text(f"""
        SELECT {COURIER_FIELDS} FROM delivery_couriers
        WHERE tenant_id=:tenant AND (NOT :active_only OR active)
          AND (:search='' OR position(lower(:search) IN lower(concat_ws(' ',name,phone)))>0)
        ORDER BY lower(name),id LIMIT :limit OFFSET :offset
    """), {"tenant": tenant, "active_only": active_only, "search": search.strip(),
             "limit": LIMIT+1, "offset": offset})).mappings().all()
    return {"items": [dict(row) for row in rows[:LIMIT]], "limit": LIMIT,
            "offset": offset, "has_more": len(rows) > LIMIT}


async def active_courier(db, tenant, courier_id, *, lock=False):
    row = (await db.execute(text(f"""
        SELECT {COURIER_FIELDS} FROM delivery_couriers
        WHERE tenant_id=:tenant AND id=:id AND active
    """ + (" FOR SHARE" if lock else "")),
        {"tenant": tenant, "id": courier_id})).mappings().first()
    if row is None:
        raise HTTPException(404, "Entregador ativo não encontrado")
    return dict(row)


async def create_courier(db, tenant, access, payload):
    async def write():
        row = (await db.execute(text(f"""
            INSERT INTO delivery_couriers (tenant_id,id,name,phone,active)
            VALUES (:tenant,:id,:name,:phone,:active) RETURNING {COURIER_FIELDS}
        """), {"tenant": tenant, "id": uuid4(),
                 **payload.model_dump(exclude={"idempotency_key"})})).mappings().one()
        await audit(db, tenant, "delivery_courier_created", "delivery_courier", row["id"])
        return dict(row)
    return await run_once(db, tenant, payload, "courier_create", None, write)


async def update_courier(db, tenant, access, courier_id, payload):
    await lock_policy(db,tenant,exclusive=True)
    row = (await db.execute(text(f"""
        UPDATE delivery_couriers SET name=:name,phone=:phone,active=:active,revision=revision+1,updated_at=now()
        WHERE tenant_id=:tenant AND id=:id AND revision=:expected_revision
        RETURNING {COURIER_FIELDS}
    """), {"tenant": tenant, "id": courier_id, **payload.model_dump()})).mappings().first()
    if row is None:
        current = (await db.execute(text("SELECT revision FROM delivery_couriers WHERE tenant_id=:tenant AND id=:id"),
                                    {"tenant": tenant, "id": courier_id})).scalar_one_or_none()
        if current is None:
            raise HTTPException(404, "Entregador não encontrado")
        commerce.conflict("O cadastro do entregador mudou. Atualize a tela e confira.")
    if not row["active"]:
        from app import delivery_route_batches
        await delivery_route_batches.release_reservations(db,tenant,courier_id)
        await db.execute(text("""UPDATE delivery_driver_access SET shift_started_at=NULL,available=false,
            latitude=NULL,longitude=NULL,location_at=NULL,sharing_key=NULL,revision=revision+1
            WHERE tenant_id=:tenant AND courier_id=:id"""),{"tenant":tenant,"id":courier_id})
    await audit(db, tenant, "delivery_courier_updated", "delivery_courier", row["id"])
    return dict(row)
