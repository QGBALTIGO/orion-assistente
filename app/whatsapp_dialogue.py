"""Shared factual presentation and consent helpers for the signed Meta channel."""
import os
from datetime import datetime
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text

from app.booking_service import appointment_snapshot, read_appointment
from app.database import get_session_factory, tenant_transaction
from app.qr_dialogue import interpret_contextual_response, contextual_response
from app.qr_presentation import BookingSummary, fits_message


async def preferences(tenant):
    if os.getenv("BUSINESS_PROFILES_ENABLED") != "1":
        return None
    from app.business_profile_service import published_profile
    async with get_session_factory()() as db:
        profile = await published_profile(db, tenant)
    return profile.style_rules.model_dump() if profile else None


async def consent(body, *, pending, action="booking", semantic_allowed=False):
    local=contextual_response(body,awaiting_confirmation=pending,confirmation_action=action)
    if not semantic_allowed or local.reason!="unresolved":
        return local
    return await interpret_contextual_response(message=body, awaiting_confirmation=pending,
        confirmation_action=action, key=os.getenv("GROQ_API_KEY", ""),
        model=os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"))


def checked(text):
    if not fits_message(text):
        raise HTTPException(422, "O resumo completo precisa ser dividido antes de continuar")
    return text


def summary(record):
    start = record["start_at"]
    if isinstance(start, str):
        start = datetime.fromisoformat(start)
    return BookingSummary(services=tuple(record.get("services") or [x["name_snapshot"] for x in record["items"]]),
        starts_at=start, timezone=record["timezone"], total_cents=record["total_cents"],
        professional=record.get("professional"), duration_min=record.get("duration_min") or
        sum(x["duration_snapshot_min"] for x in record.get("items", [])) or None,
        protocol=record.get("protocol"))


async def owned_booking(tenant, sender, *, protocol=None, item_id=None, statuses=("confirmed",)):
    """Ownership is part of the SQL predicate, even when ID comes from state."""
    async with get_session_factory()() as db:
        async with tenant_transaction(db, tenant):
            row = (await db.execute(text("""SELECT a.id,a.updated_at,t.timezone,p.name AS professional
                FROM appointments a JOIN customers c ON c.id=a.customer_id AND c.tenant_id=a.tenant_id
                JOIN tenants t ON t.id=a.tenant_id
                JOIN professionals p ON p.id=a.professional_id AND p.tenant_id=a.tenant_id
                WHERE a.tenant_id=:t AND c.whatsapp_e164=:phone
                  AND (CAST(:id AS uuid) IS NULL OR a.id=CAST(:id AS uuid))
                  AND (CAST(:protocol AS text) IS NULL OR a.protocol=CAST(:protocol AS text))
                  AND a.status=ANY(:statuses) AND a.end_at>now()"""),
                {"t": tenant, "phone": sender, "id": UUID(str(item_id)) if item_id else None,
                 "protocol": protocol, "statuses": list(statuses)})).mappings().first()
            if row is None:
                return None
            detail = {**await read_appointment(db, tenant, row["id"]), **dict(row)}
            detail["expected_snapshot"] = appointment_snapshot(detail)
            return detail
