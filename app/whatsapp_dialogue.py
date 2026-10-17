"""Shared factual presentation and consent helpers for the signed Meta channel."""
import os
import re
from datetime import datetime, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import text

from app.booking_service import appointment_snapshot, read_appointment
from app.database import get_session_factory, tenant_transaction
from app.qr_dialogue import interpret_contextual_response, contextual_response
from app.qr_presentation import BookingSummary, escape_label, fits_message
from app.qr_understanding import build_plan, normalized


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


async def owned_future_bookings(tenant, sender):
    """Read-only candidate facts; every join stays inside the signed contact scope."""
    async with get_session_factory()() as db:
        async with tenant_transaction(db, tenant):
            rows = (await db.execute(text("""
                SELECT a.id,a.protocol,a.start_at,t.timezone,p.name AS professional,
                    COALESCE((SELECT jsonb_agg(i.name_snapshot ORDER BY i.item_order)
                        FROM appointment_items i
                        WHERE i.tenant_id=a.tenant_id AND i.appointment_id=a.id), '[]'::jsonb) AS services
                FROM appointments a
                JOIN customers c ON c.tenant_id=a.tenant_id AND c.id=a.customer_id
                JOIN tenants t ON t.id=a.tenant_id
                JOIN professionals p ON p.tenant_id=a.tenant_id AND p.id=a.professional_id
                WHERE a.tenant_id=:tenant AND c.whatsapp_e164=:phone
                    AND a.status='confirmed' AND a.start_at>now()
                ORDER BY a.start_at,a.id LIMIT 51
            """), {"tenant": tenant, "phone": sender})).mappings().all()
    # Never infer uniqueness from a truncated result set.
    return [dict(row) for row in rows] if len(rows) <= 50 else None


_REFERENCE_DATES = re.compile(r"\b(?:depois de amanha|amanha|hoje|\d{4}-\d{2}-\d{2}|"
    r"\d{1,2}/\d{1,2}(?:/\d{4})?|segunda(?:-feira)?|terca(?:-feira)?|quarta(?:-feira)?|"
    r"quinta(?:-feira)?|sexta(?:-feira)?|sabado|domingo)\b")
_REFERENCE_CLOCKS = re.compile(r"\b(?:as|pelas|pras)\s+(?:\d{1,2}|uma|um|duas|dois|tres|quatro|cinco|seis|sete|oito|nove|dez|onze|doze|treze|catorze|quatorze|quinze|dezesseis|dezessete|dezoito|dezenove|vinte)"
    r"(?::\d{2}|h\d{0,2}| e meia)?(?:\s+horas?)?(?:\s+da (?:manha|tarde|noite))?\b|"
    r"(?<![\d/-])\d{1,2}[:h]\d{2}(?!\d)")
_REFERENCE_GLUE = frozenset("quero queria cancelar cancela cancele desmarcar desmarca desmarque "
    "remarcar remarca remarque reagendar reagenda reagende pode poderia o a os as um uma de da do "
    "dos das no na com pelo pela meu minha meus minhas reserva reservas agendamento agendamentos "
    "horario atendimento servico profissional barbeiro barbeira dia marcado marcada confirmado "
    "confirmada confirmo cancelamento remarcacao por favor esse essa este esta aquele aquela que tenho para e opcao escolher escolho".split())


def booking_reference(body, records, *, presented_ids=(), now=None):
    """A reference narrows owned facts, never authorizes an operation.

    Ordinals apply only to the exact previously displayed order. All supplied
    constraints must match; unknown names, mixed requests and conditions fail closed.
    """
    value = normalized(body).strip(" .!?")
    if not records or not value or len(value) > 1500 or re.search(
            r"\b(?:nao|nem|sem|ou|se|caso|talvez|como|quanto|politica|taxa|multa|"
            r"desde que|a menos que|melhor|na verdade|depois|antes)\b", value):
        return "ambiguous", None
    ordinal_words = {"primeiro": 0, "primeira": 0, "segundo": 1, "segunda": 1,
        "terceiro": 2, "terceira": 2, "ultimo": -1, "ultima": -1}
    ordinal = re.fullmatch(r"(?:(?:quero|cancelar|cancela|cancele|desmarcar|remarcar|reagendar|o|a|opcao)\s+)*"
        r"(primeir[oa]|segund[oa]|terceir[oa]|ultim[oa]|\d{1,2})(?:\s+(?:reserva|agendamento|opcao|da lista))?", value)
    if ordinal and ordinal.group(1) == "segunda" and not re.search(r"\ba segunda\b|\bsegunda (?:reserva|agendamento|opcao|da lista)\b", value):
        ordinal = None  # An unqualified weekday must not silently become index two.
    if ordinal:
        if not presented_ids:
            return "ambiguous", None
        word = ordinal.group(1)
        index = int(word)-1 if word.isdigit() else ordinal_words[word]
        if (word.isdigit() and index < 0) or not -len(presented_ids) <= index < len(presented_ids):
            return "ambiguous", None
        selected = str(presented_ids[index])
        return ("selected", selected) if any(str(row["id"]) == selected for row in records) else ("unavailable", None)
    candidates = list(records)
    referenced = False
    protocols = re.findall(r"\bag-[a-z0-9-]+\b", value)
    if protocols:
        if len(set(protocols)) != 1:
            return "ambiguous", None
        candidates = [row for row in candidates if normalized(row["protocol"]) == protocols[0]]
        value = value.replace(protocols[0], " ")
        referenced = True
    zone = ZoneInfo(records[0]["timezone"])
    parse_value = re.sub(r"(?<!\d)(\d{1,2})h\b", r"\1:00", value)
    plan = build_plan(message=parse_value.strip() or "reserva", catalog=[], reference_date=(now or datetime.now(timezone.utc)).astimezone(zone).date())
    if set(plan.ambiguities) - {"professional", "service"} or plan.time_relation not in {"none", "exact"}:
        return "ambiguous", None
    if plan.requested_date or plan.requested_time:
        candidates = [row for row in candidates if
            (not plan.requested_date or row["start_at"].astimezone(zone).date().isoformat() == plan.requested_date) and
            (not plan.requested_time or row["start_at"].astimezone(zone).strftime("%H:%M") == plan.requested_time)]
        referenced = True
    residual = _REFERENCE_CLOCKS.sub(" ", _REFERENCE_DATES.sub(" ", parse_value))
    words = set(re.findall(r"\w+", residual)) - _REFERENCE_GLUE
    # Meaningful words must all be present in the selected factual labels. This
    # intersects service/professional clues and never discards an unknown name.
    for word in words:
        matching = [row for row in candidates if word in set(re.findall(r"\w+", normalized(
            " ".join(row.get("services", [])) + " " + row.get("professional", ""))))]
        if not matching:
            return "ambiguous", None
        candidates = matching
        referenced = True
    if not referenced and len(presented_ids) == 1:
        selected = str(presented_ids[0])
        return ("selected", selected) if any(str(row["id"]) == selected for row in candidates) else ("unavailable", None)
    if len(candidates) == 1 and (referenced or len(records) == 1):
        return "selected", str(candidates[0]["id"])
    return "ambiguous", None


def booking_listing(records, *, action=None):
    shown = records[:8]
    prompt = ("Qual dessas reservas você quer " + ("cancelar?" if action == "cancel" else "remarcar?")) if action else "Qual dessas reservas você quer cancelar ou remarcar?"
    lines = ["*Suas reservas confirmadas*", ""]
    for row in shown:
        when = row["start_at"].astimezone(ZoneInfo(row["timezone"]))
        labels = " e ".join(escape_label(name) for name in row["services"])
        lines.append(f"• {when:%d/%m/%Y às %H:%M} · {labels} · {escape_label(row['professional'])} · {escape_label(row['protocol'])}")
    if len(records) > len(shown):
        lines.append("Estas são as próximas oito reservas. Posso localizar outra pela data, horário ou serviço.")
    return checked("\n".join(lines) + "\n\n" + prompt), [str(row["id"]) for row in shown]
