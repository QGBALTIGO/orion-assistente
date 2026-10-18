"""Server-owned receiving contracts; no scheduling or payment mutations."""
import json
import os
import re
from datetime import datetime
from uuid import UUID

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy import text

from app.booking_service import appointment_snapshot, read_appointment
from app.business_profile_schemas import BusinessProfile
from app.commercial_schemas import FulfillmentSelection

EMPTY = {"fulfillment_mode": None, "pickup_appointment_id": None, "fulfillment_snapshot": None}
WRITE_GUARD = """(CAST(:fulfillment_guard_id AS uuid) IS NULL OR EXISTS(SELECT 1 FROM appointments fa
    JOIN customers fc ON fc.tenant_id=fa.tenant_id AND fc.id=fa.customer_id
    WHERE fa.tenant_id=:t AND fa.id=CAST(:fulfillment_guard_id AS uuid)
      AND fc.whatsapp_e164=:fulfillment_guard_contact AND fa.status='confirmed'
      AND fa.start_at>clock_timestamp()))"""


def operational_enabled():
    return os.getenv("COMMERCIAL_FULFILLMENT_ENABLED", "0") == "1"


def conflict(message="O recebimento ou agendamento mudou. Prepare um novo resumo antes de continuar"):
    raise HTTPException(409, message)


async def options(db, tenant, *, lock=False):
    result = {"enabled": False, "enabled_modes": [], "supported_modes": ["pickup", "delivery"],
        "profile_version_id": None, "profile_revision": 0, "timezone": None, "business_name": None,
        "delivery_requires_team_review": True}
    if not operational_enabled() or os.getenv("BUSINESS_PROFILES_ENABLED", "0") != "1":
        return result
    shop = (await db.execute(text("SELECT name,timezone FROM tenants WHERE id=:t"), {"t": tenant})).mappings().first()
    if not shop:
        return result
    result.update(timezone=shop["timezone"], business_name=shop["name"])
    version = (await db.execute(text("SELECT published_version_id FROM business_profile_state WHERE tenant_id=:t" +
        (" FOR SHARE" if lock else "")), {"t": tenant})).scalar_one_or_none()
    if version is None:
        return result
    row = (await db.execute(text("SELECT revision,profile FROM business_profile_versions WHERE tenant_id=:t AND id=:id"),
        {"t": tenant, "id": version})).mappings().one()
    profile = BusinessProfile.model_validate(row["profile"])
    modes = profile.fulfillment.enabled_modes if "product_orders" in profile.capabilities else []
    result.update(enabled=bool(modes), enabled_modes=list(modes), profile_version_id=str(version),
        profile_revision=row["revision"], business_name=profile.business_name or shop["name"])
    result["_pickup_location"] = profile.address
    return result


def public_options(value):
    return {key: item for key, item in value.items() if not key.startswith("_")}


async def _owned_appointment(db, tenant, contact, appointment_id, *, future=True, lock=False):
    if not isinstance(contact, str) or re.fullmatch(r"\+[1-9][0-9]{7,14}", contact) is None:
        raise HTTPException(422, "Informe o contato do próprio agendamento antes de associar a retirada")
    row = (await db.execute(text("""SELECT a.id,a.protocol,a.start_at,a.status,a.updated_at,
        t.timezone,p.name AS professional FROM appointments a
        JOIN customers c ON c.tenant_id=a.tenant_id AND c.id=a.customer_id
        JOIN tenants t ON t.id=a.tenant_id
        JOIN professionals p ON p.tenant_id=a.tenant_id AND p.id=a.professional_id
        WHERE a.tenant_id=:t AND a.id=:id AND c.whatsapp_e164=:contact
        """ + (" AND a.status='confirmed' AND a.start_at>clock_timestamp()" if future else "") +
        (" FOR SHARE OF a,c" if lock else "")),
        {"t": tenant, "id": UUID(str(appointment_id)), "contact": contact})).mappings().first()
    return dict(row) if row else None


async def pickup_appointments(db, tenant, contact):
    policy = await options(db, tenant)
    if not policy["enabled"] or "pickup" not in policy["enabled_modes"]:
        raise HTTPException(423, "A retirada de produtos não está habilitada no perfil publicado desta empresa")
    if not isinstance(contact, str) or re.fullmatch(r"\+[1-9][0-9]{7,14}", contact) is None:
        raise HTTPException(422, "Informe o contato com DDI")
    rows = (await db.execute(text("""SELECT a.id,a.protocol,a.start_at,t.timezone,p.name AS professional
        FROM appointments a JOIN customers c ON c.tenant_id=a.tenant_id AND c.id=a.customer_id
        JOIN tenants t ON t.id=a.tenant_id
        JOIN professionals p ON p.tenant_id=a.tenant_id AND p.id=a.professional_id
        WHERE a.tenant_id=:t AND c.whatsapp_e164=:contact AND a.status='confirmed'
          AND a.start_at>clock_timestamp() ORDER BY a.start_at,a.id LIMIT 15"""),
        {"t": tenant, "contact": contact})).mappings().all()
    result = []
    for row in rows:
        services = (await db.execute(text("SELECT name_snapshot FROM appointment_items WHERE tenant_id=:t AND appointment_id=:id ORDER BY item_order"),
            {"t": tenant, "id": row["id"]})).scalars().all()
        result.append({**dict(row), "professional_name": row["professional"], "services": list(services)})
    return result


async def resolve(db, tenant, contact, selection, *, lock=False):
    if selection is None:
        return dict(EMPTY)
    selection = selection if isinstance(selection, FulfillmentSelection) else FulfillmentSelection.model_validate(selection)
    policy = await options(db, tenant, lock=lock)
    if not policy["enabled"] or selection.mode not in policy["enabled_modes"]:
        raise HTTPException(423, "Esta forma de recebimento não está habilitada no perfil publicado desta empresa")
    appointment = None
    if selection.pickup_appointment_id is not None:
        row = await _owned_appointment(db, tenant, contact, selection.pickup_appointment_id, lock=lock)
        if row is None:
            raise HTTPException(404, "Agendamento futuro confirmado não encontrado para este contato")
        detail = {**await read_appointment(db, tenant, row["id"]), **row}
        appointment = {key: row[key] for key in ("id", "protocol", "start_at", "timezone", "professional")}
        appointment["expected_snapshot"] = appointment_snapshot(detail)
    snapshot = jsonable_encoder({"schema_version": 1, "mode": selection.mode,
        "profile_version_id": policy["profile_version_id"], "profile_revision": policy["profile_revision"],
        "pickup_appointment": appointment, "pickup_location": policy.get("_pickup_location") if selection.mode == "pickup" else None,
        "requires_team_review": selection.mode == "delivery"})
    return {"fulfillment_mode": selection.mode, "pickup_appointment_id": selection.pickup_appointment_id,
            "fulfillment_snapshot": snapshot}


def selection_from(quote):
    if quote.get("fulfillment_mode") is None:
        return None
    return FulfillmentSelection(mode=quote["fulfillment_mode"], pickup_appointment_id=quote.get("pickup_appointment_id"))


def same_selection(quote, selection):
    expected = selection_from(quote)
    selected = None if selection is None else FulfillmentSelection.model_validate(selection)
    return expected == selected


def apply(facts, contract):
    result = {**facts, **contract}
    if contract.get("fulfillment_mode") == "delivery":
        result["confirmation_requirements_met"] = False
        result["limitations"] = sorted(set(facts.get("limitations", [])) | {"delivery_requires_team_review"})
    return result


async def validate(db, tenant, quote, *, confirming=False):
    # Called by actual order creation/confirmation, never a catalog consultation.
    from app.onboarding_service import require_operational_ready
    await require_operational_ready(db, tenant, "product_orders")
    if quote.get("fulfillment_mode") is None:
        return dict(EMPTY)
    current = await resolve(db, tenant, quote.get("contact_e164"), selection_from(quote), lock=True)
    if current["fulfillment_snapshot"] != quote.get("fulfillment_snapshot"):
        conflict()
    if confirming and current["fulfillment_mode"] == "delivery":
        conflict("A entrega precisa ser analisada pela equipe. Nenhum pedido foi confirmado ou estoque reservado")
    return current


async def fence(db, tenant, contact, contract):
    """Check the database clock at the final write boundary under held row locks."""
    appointment_id = contract.get("pickup_appointment_id")
    if appointment_id is None:
        return
    valid = (await db.execute(text("""SELECT EXISTS(SELECT 1 FROM appointments a
        JOIN customers c ON c.tenant_id=a.tenant_id AND c.id=a.customer_id
        WHERE a.tenant_id=:t AND a.id=:id AND c.whatsapp_e164=:contact
          AND a.status='confirmed' AND a.start_at>clock_timestamp())"""),
        {"t": tenant, "id": UUID(str(appointment_id)), "contact": contact})).scalar_one()
    if not valid:
        conflict()


async def current_appointment(db, tenant, quote):
    if quote.get("pickup_appointment_id") is None:
        return None
    row = await _owned_appointment(db, tenant, quote.get("contact_e164"), quote["pickup_appointment_id"], future=False)
    if row is None:
        return {"available": False}
    return {"available": True, **{key: row[key] for key in ("id", "protocol", "start_at", "status", "professional")},
            "future": row["start_at"] > datetime.now(row["start_at"].tzinfo)}


def insert_params(contract):
    return {"fulfillment_mode": contract["fulfillment_mode"], "pickup_appointment_id": contract["pickup_appointment_id"],
            "fulfillment_snapshot": json.dumps(contract["fulfillment_snapshot"]) if contract["fulfillment_snapshot"] is not None else None}


def guard_params(contract, contact):
    value = contract.get("pickup_appointment_id")
    return {"fulfillment_guard_id": UUID(str(value)) if value else None, "fulfillment_guard_contact": contact}
