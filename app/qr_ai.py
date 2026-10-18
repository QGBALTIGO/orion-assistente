"""Motor conversacional ligado ao WhatsApp QR, autenticado e isolado por tenant.

O LLM apenas classifica; agenda, preço, profissional e confirmação vêm do Orion.
O preview usa estado isolado e nunca grava reserva nem envia mensagens externas.
"""
import asyncio
import hashlib
import json
import math
import os
import re
import secrets
from contextvars import ContextVar
from datetime import date, datetime, timedelta
from time import monotonic
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request, Query, Response
from pydantic import Field, model_validator
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.booking_service import book_appointment, confirm_hold, list_slots, reschedule_appointment, preview_reschedule, fingerprint as booking_fingerprint
from app.catalog_api import Actor, authenticate, authorize, support_only
from app.catalog_schemas import StrictModel
from app.database import get_session_factory, tenant_transaction
from app.groq_ai import GroqServiceError, Intention, interpret
from app.qr_customer import supplied_name, customer_name, remember_name, customer_appointments, appointment_services
from app.customer_names import WhatsAppProfile, remember_whatsapp_name, useful_name
from app.qr_behaviors import control_intent, catalog_match, norm, catalog_question_services, availability_question
from app.business_profile_schemas import StyleRules
from app import qr_profile
from app import qr_presentation as presentation
from app import qr_commerce
from app import qr_intake_revision
from app import qr_cancellation
from app.qr_service_context import resolve_service_reply
from app.groq_understanding import interpret_plan
from app.qr_understanding import UnderstandingPlan, build_plan, clarification_for, unique_service_reply
from app.qr_information import info as compose_information, faq_catalog
from app.qr_dialogue import (contextual_response, interpret_contextual_response, choose_time, choose_professional,
    compound_candidate, interpret_compound_response, summary_review_requested)

router = APIRouter(tags=["Orion IA conversacional"])
router.include_router(qr_intake_revision.router)
STATE_TTL = 7 * 24 * 3600  # contexto temporário até 7 dias; agendamentos ficam no PostgreSQL
PHONE_RE = re.compile(r"^55[1-9][0-9]{9,10}$")
TIME_RE = re.compile(r"(?<!\d)([01]?\d|2[0-3])(?:[:h]([0-5]\d))?(?!\d)")
YES = {"sim", "sm", "s", "confirmo", "pode confirmar", "confirmar", "ok pode confirmar"}
NO = {"não", "nao", "n", "cancelar", "desistir"}
RESET = {"menu", "reiniciar", "começar", "recomecar", "recomeçar"}
REVIEW_STEPS = frozenset({"confirm", "reschedule_confirm", "cancel_confirm"})
AVAILABILITY_ERRORS = (HTTPException, TimeoutError, ConnectionError, SQLAlchemyError)
_reply_preferences: ContextVar[dict | None] = ContextVar("orion_qr_reply_preferences", default=None)
_reply_facts: ContextVar[tuple | None] = ContextVar("orion_qr_reply_facts", default=None)
_message_identity: ContextVar[str | None] = ContextVar("orion_qr_message_identity", default=None)
_recent_operation: ContextVar[dict | None] = ContextVar("orion_qr_recent_operation", default=None)


def _preferences(business: dict) -> dict | None:
    return (business.get("profile") or {}).get("style_rules")


def _summary_requested(body: str) -> bool:
    return summary_review_requested(body)


def _service_quote(ids, catalog, business):
    """Use the same verified service prices as this installation's booking engine."""
    selected = {str(value) for value in ids}
    items = [presentation.QuoteItem(row["name"], row["price_cents"], duration_min=row["duration_min"])
             for row in catalog if str(row["id"]) in selected]
    if not items or len(items) != len(selected):
        raise ValueError("Current catalog services required")
    return presentation.render_quote(items, _preferences(business),
        context=presentation.InteractionContext(information_only=True))


def _remaining_state_ttl(state):
    """Retain conversation facts; explicit deadlines alone bound consent."""
    return STATE_TTL


def _recent_availability(state, catalog):
    value = state.get("last_availability")
    if not isinstance(value, dict):
        return None
    try:
        recent = 0 <= datetime.now().timestamp() - float(value.get("queried_at", 0)) <= 900
        day = date.fromisoformat(value["day"])
    except (KeyError, TypeError, ValueError):
        return None
    ids = value.get("services")
    if not recent or not isinstance(ids, list) or not ids or not set(ids) <= {str(row["id"]) for row in catalog}:
        return None
    return value


def _assistant_resume_requested(body: str) -> bool:
    text = norm(body)
    if "?" in text or re.search(r"\b(?:se|caso|desde que|talvez|sera|ou)\b", text):
        return False
    # A final explicit correction can withdraw the earlier human request; other
    # negation or a final request for a person keeps the pause in place.
    guarded = re.sub(r"^nao\s*[,;]\s*(?=(?:melhor|prefiro|quero)\b)", "", text)
    if re.search(r"\b(?:nao|nem|nunca)\b", guarded) or control_intent(body) in {"human", "human_clarify"}:
        return False
    return bool(re.search(r"\b(?:quero\s+|prefiro\s+|gostaria\s+de\s+|vamos\s+|pode\s+)?"
        r"(?:continuar|voltar|retomar|seguir)\s+(?:com\s+(?:(?:o|a)\s+)?(?:voce|assistente|bot|ia)|"
        r"(?:ao|para\s+o)\s+atendimento\s+automatico)\b", text))


def _summary(state: dict, when: str | datetime, business: dict, catalog: list[dict], *,
             protocol: str | None = None, total_cents: int | None = None) -> presentation.BookingSummary:
    return presentation.BookingSummary(
        tuple(item["name"] for item in catalog if str(item["id"]) in state["services"]),
        datetime.fromisoformat(when) if isinstance(when, str) else when, business["timezone"],
        total_cents if total_cents is not None else state["price"],
        professional=state.get("professional_name"), duration_min=state.get("duration"), protocol=protocol)


class Incoming(StrictModel):
    phone: str = Field(pattern=r"^55[1-9][0-9](?:9[0-9]{8}|[2-5][0-9]{7})$")
    message_id: str = Field(min_length=1, max_length=160, pattern=r"^[a-zA-Z0-9._:-]+$")
    text: str = Field(min_length=1, max_length=1500)
    message_ids: list[str] | None = Field(default=None, min_length=1, max_length=6)
    intake_revision: int | None = Field(default=None, ge=1, le=9007199254740991, strict=True)
    require_summary_review: bool = Field(default=False, strict=True)
    profile: WhatsAppProfile | None = None

    @model_validator(mode="after")
    def ordered_ids(self):
        if self.message_ids is not None:
            if (self.message_ids[0] != self.message_id or len(set(self.message_ids)) != len(self.message_ids)
                    or any(not re.fullmatch(r"[a-zA-Z0-9._:-]{1,160}", x) for x in self.message_ids)):
                raise ValueError("Identificadores do grupo inválidos")
        return self


class Simulate(StrictModel):
    session: str = Field(min_length=8, max_length=64, pattern=r"^[a-zA-Z0-9-]+$")
    text: str = Field(min_length=1, max_length=1500)
    reset: bool = False
    request_id: UUID | None = None
    profile_version: UUID | None = None
    style_rules: StyleRules | None = None


class StyleExamples(StrictModel):
    profile_version: UUID | None = None
    style_rules: StyleRules | None = None


def requested_day(value: str, tz_name: str, now: datetime | None = None) -> date | None:
    today = (now or datetime.now(ZoneInfo(tz_name))).astimezone(ZoneInfo(tz_name)).date()
    lower = value.strip().lower()
    normalized = norm(value)
    weekdays = {"segunda": 0, "terca": 1, "quarta": 2, "quinta": 3,
                "sexta": 4, "sabado": 5, "domingo": 6}
    matches = list(re.finditer(r"\b(segunda|terca|quarta|quinta|sexta|sabado|domingo)(?:[ -]feira)?\b", normalized))
    matches = [m for m in matches if not re.match(r"\s+(?:opcao|vez|pessoa|alternativa|parcela|unidade)\b", normalized[m.end():])]
    if matches:
        if (len({m.group(1) for m in matches}) != 1
                or re.search(r"\b\d{1,4}[-/]\d{1,2}\b|\b(?:hoje|amanha)\b", normalized)):
            return None  # Never silently select the first of competing days.
        delta = (weekdays[matches[-1].group(1)] - today.weekday()) % 7
        if delta == 0 and re.search(r"\bproxim[ao]\b", normalized):
            delta = 7
        return today + timedelta(days=delta)
    if "depois de amanhã" in lower or "depois de amanha" in lower:
        return today + timedelta(days=2)
    if "amanhã" in lower or "amanha" in lower:
        return today + timedelta(days=1)
    if "hoje" in lower:
        return today
    match = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", lower)
    if match:
        try:
            return date(*map(int, match.groups()))
        except ValueError:
            return None
    match = re.search(r"\b([0-3]?\d)/([01]?\d)(?:/(\d{4}))?\b", lower)
    if match:
        day, month, year = match.groups()
        try:
            candidate = date(int(year) if year else today.year, int(month), int(day))
            if not year and candidate < today:
                return date(today.year + 1, int(month), int(day))
            return candidate
        except ValueError:
            return None
    return None


def requested_time(value: str) -> str | None:
    lower = value.strip().lower()
    if "duas da tarde" in lower or "duas horas da tarde" in lower:
        return "14:00"
    match = TIME_RE.search(lower)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or "0")
    if "tarde" in lower and hour < 12:
        hour += 12
    if "noite" in lower and hour < 12:
        hour += 12
    if hour >= 24:
        return None
    return f"{hour:02d}:{minute:02d}"


async def tenant_data(tenant: UUID):
    async with get_session_factory()() as db:
        async with tenant_transaction(db, tenant):
            business = (await db.execute(text("""
                SELECT name,timezone FROM tenants WHERE id=:t AND status='active'
            """), {"t": tenant})).mappings().first()
            if not business:
                raise HTTPException(404, "Estabelecimento não disponível")
            services = (await db.execute(text("""
                SELECT id,name,price_cents,duration_min FROM services
                WHERE tenant_id=:t AND active ORDER BY name LIMIT 60
            """), {"t": tenant})).mappings().all()
            pros = (await db.execute(text("""
                SELECT id,name FROM professionals
                WHERE tenant_id=:t AND active ORDER BY name LIMIT 30
            """), {"t": tenant})).mappings().all()
    business = dict(business)
    business["use_working_hours"] = True
    snapshot = await qr_profile.runtime_profile(tenant)
    if snapshot:
        business.update(profile=snapshot["profile"].model_dump(mode="json"),
                        profile_id=str(snapshot["id"]) if snapshot["id"] else None,
                        profile_revision=snapshot["revision"])
        if snapshot["profile"].business_name:
            business["name"] = snapshot["profile"].business_name
    return business, [dict(row) for row in services], [dict(row) for row in pros]


async def professionals_for(tenant: UUID, service_ids: list[UUID]):
    async with get_session_factory()() as db:
        async with tenant_transaction(db, tenant):
            rows = (await db.execute(text("""
                SELECT p.id,p.name FROM professionals p
                WHERE p.tenant_id=:t AND p.active
                AND NOT EXISTS (
                    SELECT 1 FROM unnest(CAST(:services AS uuid[])) AS requested(service_id)
                    WHERE NOT EXISTS (
                        SELECT 1 FROM professional_services ps
                        WHERE ps.tenant_id=p.tenant_id AND ps.professional_id=p.id
                        AND ps.service_id=requested.service_id
                    )
                ) ORDER BY p.name LIMIT 20
            """), {"t": tenant, "services": [str(x) for x in service_ids]})).mappings().all()
    return [dict(row) for row in rows]


async def working_hours_for(tenant: UUID, professional_ids: list[UUID], day: date | None):
    """Read registered work, never occupied/free slots, within the tenant scope."""
    if not professional_ids or len(professional_ids) > 30:
        return []
    async with get_session_factory()() as db:
        async with tenant_transaction(db, tenant):
            rows = (await db.execute(text("""SELECT h.start_time,h.end_time FROM working_hours h
                JOIN professionals p ON p.tenant_id=h.tenant_id AND p.id=h.professional_id
                WHERE h.tenant_id=:tenant AND p.active AND h.professional_id=ANY(:professionals)
                  AND (CAST(:weekday AS integer) IS NULL OR h.weekday=:weekday) ORDER BY h.start_time,h.end_time LIMIT 210"""),
                {"tenant": tenant, "professionals": professional_ids, "weekday": day.weekday() if day else None})).mappings().all()
    intervals = sorted({(row["start_time"].strftime("%H:%M"), row["end_time"].strftime("%H:%M")) for row in rows})
    return [{"opens": opens, "closes": closes} for opens, closes in intervals] if len(intervals) <= 21 else []


async def _time_context(tenant, body, state, business, catalog, professionals):
    inquiry = state.get("availability_query") or {}
    previous = state.get("last_availability") or {}
    try:
        recent = 0 <= datetime.now().timestamp() - float(previous.get("queried_at", 0)) <= 900
    except (TypeError, ValueError):
        recent = False
    base = inquiry or (previous if recent else {})
    context = {"service_ids": inquiry.get("services") or state.get("services") or base.get("services", []),
        "step": state.get("step", "menu")}
    profile = business.get("profile") or {}
    if not business.get("use_working_hours"):
        hours = [{"opens": row["opens"][:5], "closes": row["closes"][:5]} for row in profile.get("hours", [])
            if profile.get("timezone") in {None, business["timezone"]}]
        if hours:
            context["hours"] = hours
        return context
    local = build_plan(message=body, catalog=catalog, professionals=professionals, context=context,
        reference_date=datetime.now(ZoneInfo(business["timezone"])).date())
    valid = {str(row["id"]): UUID(str(row["id"])) for row in professionals}
    person = str(local.professional_id) if local.professional_id else state.get("professional") if "professional" not in local.ambiguities else None
    day = local.requested_date or (inquiry.get("day") or state.get("day") or base.get("day") if "date" not in local.ambiguities else None)
    try:
        day = date.fromisoformat(day) if day else None
        if day and day < datetime.now(ZoneInfo(business["timezone"])).date():
            day = None
    except (TypeError, ValueError):
        day = None
    context["hours"] = await working_hours_for(tenant, [valid[person]] if person in valid else list(valid.values()), day)
    return context


def _plan_clarification(plan, context, business):
    question = clarification_for(plan)
    hours = context.get("hours", [])
    if business.get("use_working_hours") and "time" in plan.ambiguities and 0 < len(hours) <= 3:
        def clock(value):
            hour, minute = value.split(":")
            return f"{int(hour)}h" + (minute if minute != "00" else "")
        intervals = "; ".join(clock(row["opens"]) + "–" + clock(row["closes"]) for row in hours)
        question = "Expediente cadastrado para este pedido: " + intervals + ". Qual horário você prefere?"
    return question


def format_money(cents: int):
    return f"R$ {cents // 100},{cents % 100:02d}"


def capability_denial(business: dict, capability: str) -> dict | None:
    if business.get("profile_id") and capability not in business.get("profile", {}).get("capabilities", []):
        return {"answer": "Este atendimento não está habilitado para essa operação. Posso deixar a conversa para atendimento humano.",
                "step": "capability_unavailable"}
    return None


async def offer_slots(tenant: UUID, state: dict, preview: bool):
    try:
        async with get_session_factory()() as db:
            slots = _slot_result(await list_slots(
                db, tenant, UUID(state["professional"]),
                [UUID(x) for x in state["services"]], date.fromisoformat(state["day"]),
            ), pricing=True)
    except AVAILABILITY_ERRORS:
        return "Não consegui consultar a agenda agora. Podemos tentar novamente em instantes?", {**state, "step": "date"}
    options = slots["slots"][:8]
    if not options:
        return "Não encontrei horários livres nessa data. Informe outra data.", {**state, "step": "date"}
    state.update(step="time", slots=options, price=slots["price_cents"], duration=slots["duration_min"])
    facts = _reply_facts.get()
    if facts:
        business, catalog = facts
        state["slot_menu_numbered"] = False
        return presentation.render_slots([x["name"] for x in catalog if str(x["id"]) in state["services"]], options,
            _preferences(business), timezone=business["timezone"], day=state["day"], total_cents=state["price"],
            duration_min=state["duration"], simulation=preview, professional=state.get("professional_name")), state
    state["slot_menu_numbered"] = True
    listing = "\n".join(f"{i}. {datetime.fromisoformat(when):%H:%M}" for i, when in enumerate(options, 1))
    return (f"Encontrei estes horários livres para {date.fromisoformat(state['day']):%d/%m}:\n"
            f"{listing}\nQual horário prefere?"), state


async def begin_reschedule(tenant: UUID, phone: str, business: dict, redis, key: str, preview: bool):
    if denial := capability_denial(business, "rescheduling"):
        return denial
    if preview:
        return {"answer": "Simulação: uma remarcação real exige uma reserva fictícia de homologação; a prévia não altera agendamentos.", "step": "preview_reschedule"}
    now = datetime.now(ZoneInfo(business["timezone"]))
    records = await customer_appointments(tenant, phone, limit=15)
    future = sorted((x for x in records if x["status"] == "confirmed"
                     and x["start_at"] > now), key=lambda x: x["start_at"])
    if not future:
        return {"answer": "Não encontrei agendamento futuro confirmado neste número. Posso chamar um atendente?", "step": "no_reservation"}
    if len(future) > 1:
        details = await qr_cancellation.owned_candidates(tenant, phone)
        state = {"step": "reschedule_pick", "candidate_ids": [str(x["id"]) for x in future[:8]],
                 "candidate_details": details}
        await redis.set(key, json.dumps(state), ex=STATE_TTL)
        listing = "\n".join(
            f"{i}. {x['start_at'].astimezone(ZoneInfo(business['timezone'])):%d/%m às %H:%M} · {x['protocol']}"
            for i, x in enumerate(future[:8], 1))
        return {"answer": "Qual agendamento gostaria de remarcar?\n" + listing, "step": "reschedule_pick"}
    return await selected_reschedule(tenant, phone, future[0]["id"], redis, key)


async def selected_reschedule(tenant: UUID, phone: str, appointment_id: UUID, redis, key: str):
    appointment = await appointment_services(tenant, appointment_id, phone)
    if not appointment or appointment["status"] != "confirmed":
        return {"answer": "Não encontrei agendamento válido neste número.", "step": "no_reservation"}
    state = {"step": "reschedule_day", "appointment": str(appointment_id),
             "professional": str(appointment["professional_id"]),
             "services": [str(x) for x in appointment["services"]]}
    await redis.set(key, json.dumps(state), ex=STATE_TTL)
    return {"answer": "Para qual nova data gostaria de remarcar? Ex.: amanhã ou 20/10.", "step": "reschedule_day"}


def _weekday_restriction(body: str) -> str | None:
    """A whole final day correction, not a cancellation or general consent."""
    value = norm(body).strip(" .!")
    match = re.fullmatch(r"nao quero mais\s*[,;]?\s*so posso\s+(?:(?:na|no|para|pra)\s+)?"
        r"((?:proxima?\s+)?(?:segunda|terca|quarta|quinta|sexta|sabado|domingo)(?:[ -]feira)?)", value)
    return match.group(1) if match else None


async def _day_restriction_turn(tenant, phone, body, state, business, catalog, redis, key, preview):
    weekday = _weekday_restriction(body)
    if weekday is None or state.get("availability_query"):
        return None
    day = requested_day(weekday, business["timezone"])
    if day is None:
        return None
    step = state.get("step")
    active_booking = step in {"date", "professional", "time", "confirm"}
    active_move = step in {"reschedule_day", "reschedule_time", "reschedule_confirm"}
    if active_booking or active_move:
        if step in REVIEW_STEPS:
            deadline = state.get("confirmation_expires_at")
            if type(deadline) not in {int, float} or not math.isfinite(deadline) or deadline <= datetime.now().timestamp():
                return {"answer": "O resumo anterior expirou. Vamos conferir o pedido e a disponibilidade novamente?", "step": "expired"}
        plan = UnderstandingPlan(intents=["reschedule" if active_move else "booking"], requested_date=day.isoformat())
        # A date amendment cannot carry the earlier chosen clock or consent.
        return await _planned_slots(tenant, phone, plan, {name: value for name, value in state.items()
            if name not in {"pending_clock", "pending_relation"}}, business, catalog, redis, key, preview, "", rescheduling=active_move)
    if step not in {None, "menu", "done", "confirmed", "rescheduled", "reschedule_pick", *qr_cancellation.STEPS}:
        return None
    if denial := capability_denial(business, "rescheduling"):
        return denial
    if preview:
        return await begin_reschedule(tenant, phone, business, redis, key, preview=True)
    from app import qr_operation_receipt
    receipt = _recent_operation.get()
    if receipt is None:
        raw = await redis.get(qr_operation_receipt.key(tenant, phone))
        receipt = qr_operation_receipt._read(raw, tenant, phone) if raw else None
    # The receipt is only a factual reference. Ownership/status/time are read
    # again; its answer, latch and protocol never authorize a write.
    receipt = qr_operation_receipt._read(json.dumps(receipt), tenant, phone) if receipt else None
    now = datetime.now(ZoneInfo(business["timezone"]))
    if (step in {None, "menu", "done", "confirmed", "rescheduled"} and not state.get("appointment")
            and receipt and receipt.get("step") in {"confirmed", "rescheduled"} and receipt.get("protocol")
            and _message_identity.get() and _message_identity.get() != receipt["message_id"]):
        records = await customer_appointments(tenant, phone, limit=15)
        matches = [row for row in records if row.get("protocol") == receipt["protocol"]
            and row.get("status") == "confirmed" and row["start_at"] > now]
        if len(matches) == 1:
            result = await selected_reschedule(tenant, phone, matches[0]["id"], redis, key)
        else:
            result = await begin_reschedule(tenant, phone, business, redis, key, preview=False)
    elif step == "reschedule_pick":
        result = {"answer": "Qual agendamento você quer remarcar para essa nova data?", "step": "reschedule_pick"}
    else:
        result = await begin_reschedule(tenant, phone, business, redis, key, preview=False)
    if result["step"] not in {"reschedule_day", "reschedule_pick"}:
        return result
    current = json.loads(await redis.get(key) or "{}")
    plan = UnderstandingPlan(intents=["reschedule"], requested_date=day.isoformat())
    if result["step"] == "reschedule_pick":
        current["deferred_reschedule"] = plan.model_dump(mode="json")
        await redis.set(key, json.dumps(current), ex=STATE_TTL)
        return result
    return await _planned_slots(tenant, phone, plan, current, business, catalog, redis, key, preview, "", rescheduling=True)


async def continue_reschedule(tenant: UUID, phone: str, body: str, state: dict,
                              business: dict, redis, key: str, preview: bool):
    if denial := capability_denial(business, "rescheduling"):
        return denial
    step = state.get("step")
    text_clean = body.strip().lower()
    if step == "reschedule_pick":
        ids = state.get("candidate_ids", [])
        details = state.get("candidate_details")
        if details is None:
            details = [row for row in await qr_cancellation.owned_candidates(tenant, phone) if row["id"] in ids]
            state["candidate_details"] = details
            await redis.set(key, json.dumps(state), ex=STATE_TTL)
        kind, selected = qr_cancellation.choose_candidate(body, details, business["timezone"])
        if kind != "selected" or selected not in ids:
            listing = "\n".join(f"• {datetime.fromisoformat(row['start_at']).astimezone(ZoneInfo(business['timezone'])):%d/%m/%Y às %H:%M}"
                + (" · " + presentation.escape_label(row["professional"]) if row.get("professional") else "")
                + " · " + presentation.escape_label(row["protocol"]) for row in details)
            return {"answer": "Qual agendamento você quer remarcar? Pode indicar a data, o horário, o profissional ou o protocolo.\n\n" + listing, "step": step}
        result = await selected_reschedule(tenant, phone, UUID(selected), redis, key)
        if state.get("deferred_reschedule"):
            try:
                plan = UnderstandingPlan.model_validate(state["deferred_reschedule"])
            except ValueError:
                return result
            current = json.loads(await redis.get(key) or "{}")
            if current.get("appointment"):
                _, catalog, _ = await tenant_data(tenant)
                return await _planned_slots(tenant, phone, plan, current, business, catalog, redis, key, preview, "", rescheduling=True)
        return result
    if step == "reschedule_day":
        day = requested_day(body, business["timezone"])
        if not day:
            return {"answer": "Informe uma data válida, por exemplo: amanhã ou 20/10.", "step": step}
        state["day"] = day.isoformat()
        answer, new_state = await offer_slots(tenant, state, preview)
        new_state["step"] = "reschedule_day" if new_state.get("step") == "date" else "reschedule_time"
        await redis.set(key, json.dumps(new_state), ex=STATE_TTL)
        return {"answer": answer, "step": new_state["step"]}
    if step == "reschedule_time":
        options = state.get("slots", [])
        selection = requested_time(body)
        chosen = (options[int(text_clean)-1]
                  if text_clean.isdigit() and 1 <= int(text_clean) <= len(options)
                  else next((x for x in options if selection and
                             datetime.fromisoformat(x).strftime("%H:%M") == selection), None))
        if not chosen:
            return {"answer": presentation.render_prompt("time", _preferences(business)), "step": step}
        _, catalog, _ = await tenant_data(tenant)
        return await prepare_reschedule_summary(tenant, phone, state, chosen, business, catalog, redis, key, preview)
    if step == "reschedule_confirm":
        if text_clean in NO:
            await redis.delete(key)
            return {"answer": "Remarcação cancelada. O horário original permanece.", "step": "reschedule_cancelled"}
        if text_clean not in YES:
            return {"answer": presentation.render_prompt("confirmation", _preferences(business)), "step": step}
        if preview:
            await redis.delete(key)
            return {"answer": "Simulação encerrada; nenhuma reserva alterada.", "step": "preview_complete"}
        original = await appointment_services(tenant, UUID(state["appointment"]), phone)
        if not original or original["status"] != "confirmed":
            await redis.delete(key)
            return {"answer": "O agendamento original não está disponível para remarcação.", "step": "invalid_reservation"}
        if not state.get("expected_snapshot") or not state.get("reschedule_facts"):
            _, catalog, _ = await tenant_data(tenant)
            return await prepare_reschedule_summary(tenant, phone, state, state["new_start"], business, catalog, redis, key, preview)
        try:
            async with get_session_factory()() as s:
                await qr_intake_revision.check_before_write()
                updated = await reschedule_appointment(
                    s, tenant, UUID(state["appointment"]),
                    datetime.fromisoformat(state["new_start"]), expected_snapshot=state["expected_snapshot"], customer_phone="+" + phone,
                    source="whatsapp_qr")
        except HTTPException:
            for field in ("new_start", "expected_snapshot", "reschedule_facts", "confirmation_summary", "confirmation_presentation",
                          "confirmation_message_id", "confirmation_expires_at", "confirmation_requires_review", "slots"):
                state.pop(field, None)
            state["step"] = "reschedule_time"
            state["slots"] = []
            state["reschedule_conflict_review"] = True
            await redis.set(key, json.dumps(state), ex=STATE_TTL)
            return {"answer": "O novo horário ficou indisponível. O original permanece. Quer consultar outros horários para esse dia ou prefere outra data?", "step": "reschedule_conflict"}
        await redis.delete(key)
        when = updated["start_at"].astimezone(ZoneInfo(business["timezone"]))
        facts = state["reschedule_facts"]
        summary = presentation.BookingSummary(tuple(facts["services"]), when, facts["timezone"], facts["total_cents"],
            professional=facts["professional"], duration_min=facts["duration_min"], protocol=updated["protocol"])
        return {"answer": presentation.render_summary(summary, _preferences(business), confirmed=True, operation="reschedule"),
                "step": "rescheduled", "_verified_operation": True}
    return {"answer": "Qual nova data ou horário você quer consultar para sua reserva?", "step": "unknown"}


async def _retry_reschedule_query(tenant, body, state, business, redis, key, preview):
    """A new customer message may request one fresh read, never auto-retry a write."""
    if not state.get("reschedule_conflict_review"):
        return None
    text = norm(body)
    requested = bool(re.search(r"\b(?:outros?|mais|diferentes?)\s+horarios?\b|\b(?:ver|consultar|mostrar)\b.*\b(?:horarios|opcoes)\b", text))
    agreement = contextual_response(body, awaiting_confirmation=True).kind == "affirm"
    chosen_day = requested_day(body, business["timezone"]) if simple_schedule_reply(body, "date") else None
    if not (requested or agreement or chosen_day):
        return None
    if denial := capability_denial(business, "rescheduling"):
        return denial
    if chosen_day:
        state["day"] = chosen_day.isoformat()
    state.pop("reschedule_conflict_review", None)
    answer, current = await offer_slots(tenant, state, preview)
    current["step"] = "reschedule_day" if current.get("step") == "date" else "reschedule_time"
    await redis.set(key, json.dumps(current), ex=STATE_TTL)
    return {"answer": answer, "step": current["step"]}


def catalog_menu(business: dict, catalog: list[dict], greeting: str = "") -> str:
    return presentation.render_service_menu(
        business["name"], catalog, _preferences(business), greeting=greeting,
    )


def is_service_request(value: str) -> bool:
    return any(word in value.lower() for word in ("quero", "agendar", "preciso", "gostaria", "reservar"))


def catalog_question(value: str) -> bool:
    return bool(re.search(
        r"\b(?:quanto\s+(?:que\s+)?(?:custa|custam|fica|ficam|e|sai|tempo)|"
        r"(?:custa|custam|fica|ficam|sai)\s+quanto|precos?|valores?|"
        r"duracao|demora|quais\s+(?:os\s+)?servicos|quais\s+servicos\s+tem)\b", norm(value)))


def professional_choice(value: str, candidates: list[dict]) -> dict | None:
    return choose_professional(value, candidates)


def resume_prompt(state: dict) -> str:
    inquiry = state.get("availability_query")
    if inquiry:
        return {
            "service": "Para continuar a consulta de disponibilidade, diga qual serviço deseja consultar.",
            "date": "Para continuar a consulta de disponibilidade, informe o dia desejado.",
            "professional": "Para continuar a consulta de disponibilidade, diga qual profissional prefere.",
        }.get(inquiry.get("waiting_for"), "A consulta de disponibilidade não confirma reservas.")
    if state.get("confirmation_requires_review"):
        return "O pedido anterior continua sem confirmação. Quer conferir o resumo antes de continuar?"
    step = state.get("step", "")
    if step == "professional":
        listing = "\n".join(f"• {presentation.escape_label(p['name'])}" for p in state.get("professionals", []))
        return "Quando quiser continuar, qual profissional você prefere?\n" + listing
    return {
        "date": "Quando quiser continuar, informe o dia desejado.",
        "time": "Quando quiser continuar, escolha um dos horários que listei.",
        "confirm": "O pedido ainda não foi confirmado. Você quer confirmar os dados apresentados ou ajustar alguma coisa?",
        "reschedule_day": "A reserva original permanece. Para continuar a remarcação, informe a nova data.",
        "reschedule_time": "A reserva original permanece. Para continuar, escolha um horário listado.",
        "reschedule_confirm": "A reserva original permanece. Você quer confirmar a remarcação apresentada ou ajustar algum detalhe?",
        "cancel_confirm": "A reserva permanece confirmada. Quer conferir o resumo de cancelamento antes de continuar?",
    }.get(step, "Se quiser agendar, diga qual serviço deseja.")


def conversation_context(state: dict, catalog: list[dict]) -> dict:
    selected = set(state.get("services", []))
    return {"etapa": state.get("step", "menu"), "data": state.get("day"),
            "servicos_selecionados": [item["name"] for item in catalog if str(item["id"]) in selected]}


def asks_current_price(body: str) -> bool:
    value = norm(body).strip(" ?!.,")
    question = r"(?:quanto (?:custa|fica|e|sai)|qual (?:e )?(?:o )?(?:valor|preco))"
    reference = r"(?:isso|disso|tudo isso|(?:esse|esses|este|estes) servicos?|(?:o )?total)"
    return bool(re.fullmatch(r"(?:e )?" + question + r"(?: total| " + reference + r"(?: no total)?)?", value)
                or re.fullmatch(r"(?:e )?(?:o )?total", value))


def simple_schedule_reply(body: str, kind: str) -> bool:
    value = norm(body).strip(" ?!.,")
    prefix = r"(?:(?:pode ser|para|no dia|dia|as|pelas)\s+)?"
    if kind == "date":
        weekday = r"(?:(?:quero|prefiro|pode ser)\s+)?(?:(?:para|pra|na|no|para a|para o)\s+)?(?:proxima?\s+)?(?:segunda|terca|quarta|quinta|sexta|sabado|domingo)(?:[ -]feira)?"
        return bool(re.fullmatch(prefix + r"(?:hoje|amanha|depois de amanha|\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}(?:/\d{4})?)", value)
                    or re.fullmatch(weekday, value))
    return bool(re.fullmatch(prefix + r"(?:(?:[01]?\d|2[0-3])(?::[0-5]\d|h(?:[0-5]\d)?)|duas(?: horas)? da tarde)", value))


def _asks_available_professionals(body):
    return availability_question(body) and bool(re.search(r"\bquem\b|\b(?:qual|quais)\s+(?:dos\s+)?profissionais?\b", norm(body)))


def _slot_result(value, *, pricing=False):
    if not isinstance(value, dict) or not isinstance(value.get("slots"), list):
        raise HTTPException(503, "Não foi possível validar a consulta de agenda")
    if pricing and (type(value.get("price_cents")) is not int or value["price_cents"] < 0
            or type(value.get("duration_min")) is not int or value["duration_min"] <= 0):
        raise HTTPException(503, "Não foi possível validar os valores da agenda")
    return value


async def _contextual_service_selection(tenant, phone, body, state, business, catalog, professionals, redis, key, preview):
    if (state.get("step") not in {None, "menu", "date"} or state.get("availability_query") or state.get("deferred_plan")
            or state.get("pending_clock") and not (state.get("booking_draft") and "service" in state.get("clarification_pending", []))
            or not (state.get("service_reply_expected") or is_service_request(body))):
        return None
    choice = resolve_service_reply(body, catalog, awaiting_service=True)
    if choice.kind == "ambiguous":
        state.update(step="menu", menu_ids=[str(option.id) for option in choice.options])
        await redis.set(key, json.dumps(state), ex=_remaining_state_ttl(state))
        return {"answer": "Qual destas opções você procura?\n" + " • ".join(presentation.escape_label(row.name) for row in choice.options),
                "step": "menu", "_service_reply_prompt": True}
    if choice.kind != "resolved":
        return None
    if choice.corrected and not (state.get("service_reply_expected") or state.get("booking_draft") and "service" in state.get("clarification_pending", [])):
        return None
    plan = UnderstandingPlan(intents=["booking"], service_ids=list(choice.service_ids), selection_mode="replace")
    plan = _merge_booking_draft(plan, state, catalog, professionals)
    return await _planned_slots(tenant, phone, plan, state, business, catalog, redis, key, preview, "")


async def _professionals_availability(tenant, state, inquiry, business, catalog, pros, redis, key, *, factual="", preview=False):
    """A read-only answer to WHO is free; eligibility alone is never availability."""
    ids, day = inquiry["services"], date.fromisoformat(inquiry["day"])
    available = []
    try:
        async with asyncio.timeout(12), get_session_factory()() as db:
            for person in pros:
                actual = _slot_result(await list_slots(db, tenant, UUID(str(person["id"])), [UUID(value) for value in ids], day), pricing=True)
                if actual["slots"]:
                    available.append({"id": str(person["id"]), "name": person["name"], "slots": actual["slots"][:8],
                        "price_cents": actual["price_cents"], "duration_min": actual["duration_min"]})
    except AVAILABILITY_ERRORS:
        state["availability_query"] = {**inquiry, "waiting_for": "retry"}
        await redis.set(key, json.dumps(state), ex=_remaining_state_ttl(state))
        return _bounded_reply(factual, "Não consegui consultar a agenda agora. Pode me pedir para tentar novamente em instantes?", state.get("step", "menu"))
    shown = available[:4]
    try:
        names = [row["name"] for row in catalog if str(row["id"]) in ids]
        if inquiry.get("information_only") is True:
            answer = "\n\n".join(presentation.render_slots(names, row["slots"], _preferences(business),
                timezone=business["timezone"], day=day, total_cents=row["price_cents"], duration_min=row["duration_min"],
                professional=row["name"], simulation=preview, ask_selection=False) for row in shown)
            if not shown:
                answer = presentation.render_slots(names, [], _preferences(business), timezone=business["timezone"], ask_selection=False)
        else:
            answer = presentation.render_professional_availability(names, shown, _preferences(business),
                timezone=business["timezone"], day=day, simulation=preview)
    except ValueError:
        return _bounded_reply(factual, "Há várias opções nessa consulta. Tem preferência por algum profissional para eu mostrar os horários completos?", state.get("step", "menu"))
    if len(available) > len(shown):
        answer += f"\n_Mostrei {len(shown)} dos {len(available)} profissionais com horários; posso consultar os demais._"
    result = _bounded_reply(factual, answer, state.get("step", "menu"))
    if result["step"] != "choose_priority":
        state.pop("availability_query", None)
        state["last_availability"] = {"services": list(ids), "day": day.isoformat(), "kind": "professionals",
            "professionals": [{"id": row["id"], "name": row["name"]} for row in shown],
            "queried_at": datetime.now().timestamp()}
        await redis.set(key, json.dumps(state), ex=_remaining_state_ttl(state))
    return result


def _professional_indifference(body):
    return bool(re.fullmatch(r"(?:pra mim |para mim )?(?:tanto faz|qualquer um|qualquer profissional|sem preferencia)(?: por favor)?[.!]*", norm(body)))


def _queried_professionals(body, professionals):
    """Cover the complete coordinated subject, never pick one singular name."""
    value = norm(body)
    if re.search(r"\b(?:agend\w*|marc\w*|reserv\w*|confirm\w*|cancel\w*|desmarc\w*|remarc\w*|compr\w*|pagar|"
            r"quero|prefiro|nao|nem|sem|menos|exceto|ou|se|caso|talvez|melhor|troca\w*)\b", value):
        return None
    subject = re.search(r"\bquais?\s+(?:sao\s+)?(?:os\s+)?horarios(?:\s+(?:livres|disponiveis))?"
        r"(?:\s+(?:do|da|dos|das|de))?\s+(.+?)\s+(?:tem|estao|ficam|teriam)\b", value)
    if not subject:
        return None
    names = {}
    for person in professionals:
        full_name = norm(person["name"])
        for alias in [full_name, full_name.split()[0], *person.get("aliases", [])]:
            term = norm(str(alias)).strip()
            if term:
                names.setdefault(term, set()).add(str(person["id"]))
    requested = []
    for part in re.split(r"\s+e\s+|\s*,\s*", subject.group(1)):
        term = re.sub(r"^(?:o|a)\s+", "", part.strip())
        candidates = names.get(term, set())
        if len(candidates) != 1:
            return None
        requested.append(next(iter(candidates)))
    if not 2 <= len(requested) <= 6 or len(set(requested)) != len(requested):
        return None
    # An additional introduced name is another subject, not an ignorable tail.
    if re.search(r"\b(?:com|pelo|pela|profissional|barbeiro|barbeira)\s+", value[subject.end():]):
        return None
    return requested


async def _multiple_professional_availability(tenant, body, plan, state, business, catalog, professionals, redis, key, preview):
    requested = _queried_professionals(body, professionals)
    if (not requested or not availability_question(body) or set(plan.intents) - {"availability", "other"}
            or set(plan.ambiguities) - {"professional"}):
        return None
    local = build_plan(message=body, catalog=catalog, professionals=[],
        reference_date=datetime.now(ZoneInfo(business["timezone"])).date())
    ids = [str(value) for value in local.query_service_ids or local.service_ids]
    if local.ambiguities or not local.requested_date or not ids:
        return None
    if denial := capability_denial(business, "appointments"):
        return denial
    eligible = await professionals_for(tenant, [UUID(value) for value in ids])
    candidates = [person for person in eligible if str(person["id"]) in requested]
    if len(candidates) != len(requested):
        await _review_after_inquiry(state, redis, key)
        return {"answer": "Não consegui validar todos os profissionais citados para esses serviços. Pode conferir os nomes ou pedir ajuda à equipe?", "step": state.get("step", "menu")}
    await _review_after_inquiry(state, redis, key)
    return await _professionals_availability(tenant, state, {"services": ids, "day": local.requested_date, "kind": "professionals", "information_only": True},
        business, catalog, candidates, redis, key, preview=preview)


async def _indifferent_professional_turn(tenant, phone, body, state, business, catalog, redis, key, preview):
    inquiry = state.get("availability_query")
    question = isinstance(inquiry, dict) and inquiry.get("waiting_for") == "professional"
    choice = _professional_indifference(body)
    deferred = state.get("professional_indifference_expires_at")
    continuing = (state.get("step") == "date" and type(deferred) in {int, float}
        and math.isfinite(deferred) and deferred > datetime.now().timestamp() and simple_schedule_reply(body, "date"))
    if not ((choice and (question or state.get("step") == "professional")) or continuing):
        return None
    if inquiry and not question:
        return None
    if denial := capability_denial(business, "appointments"):
        return denial
    if question:
        await _review_after_inquiry(state, redis, key)
    source = inquiry if question else state
    ids = source.get("services", [])
    if not ids or not set(ids) <= {str(row["id"]) for row in catalog}:
        return None
    day = requested_day(body, business["timezone"]) if continuing else None
    try:
        if day is None and source.get("day"):
            day = date.fromisoformat(source["day"])
    except (TypeError, ValueError):
        day = None
    if day is None or day < datetime.now(ZoneInfo(business["timezone"])).date():
        if question:
            state["availability_query"] = {**inquiry, "kind": "professionals", "waiting_for": "date"}
            state["availability_query"].pop("professional", None)
        else:
            state = {name: state[name] for name in ("services", "customer_name", "pending_clock", "pending_relation") if name in state}
            state.update(step="date", professional_indifference_expires_at=datetime.now().timestamp() + 300)
        await redis.set(key, json.dumps(state), ex=_remaining_state_ttl(state))
        return {"answer": "Posso conferir os profissionais disponíveis. Para qual dia você quer?", "step": state.get("step", "menu")}
    pros = await professionals_for(tenant, [UUID(value) for value in ids])
    if question:
        return await _professionals_availability(tenant, state, {**inquiry, "kind": "professionals", "day": day.isoformat()},
            business, catalog, pros, redis, key, preview=preview)
    clock, relation = state.get("pending_clock"), state.get("pending_relation", "exact")
    available = []
    try:
        async with asyncio.timeout(12), get_session_factory()() as db:
            for person in pros:
                actual = _slot_result(await list_slots(db, tenant, UUID(str(person["id"])), [UUID(value) for value in ids], day), pricing=True)
                for value in actual["slots"]:
                    try:
                        when = datetime.fromisoformat(value)
                        if when.tzinfo is None or when.utcoffset() is None or when.astimezone(ZoneInfo(business["timezone"])).date() != day:
                            raise ValueError("Scoped local slot required")
                    except (TypeError, ValueError):
                        raise HTTPException(503, "Não foi possível validar os horários da agenda") from None
                    local_clock = when.astimezone(ZoneInfo(business["timezone"])).strftime("%H:%M")
                    if clock and ((relation == "exact" and local_clock != clock) or
                            (relation == "after" and local_clock <= clock) or (relation == "before" and local_clock >= clock)):
                        continue
                    available.append((when, str(person["id"]), person))
    except AVAILABILITY_ERRORS:
        return {"answer": "Não consegui verificar a disponibilidade de todos os profissionais agora. Nenhum agendamento foi criado ou alterado. Podemos tentar novamente?", "step": state.get("step", "professional")}
    await qr_intake_revision.assert_current()
    if not available:
        return {"answer": "Não encontrei horário livre com os profissionais habilitados para esse pedido" +
            (" no horário solicitado." if clock else " nessa data.") + " Qual outro dia ou horário você prefere?", "step": state.get("step", "professional")}
    # Indifference authorizes choosing an eligible professional, never a
    # booking. Earliest real slot wins; stable ID breaks simultaneous ties.
    chosen = min(available, key=lambda row: (row[0], row[1]))[2]
    plan = UnderstandingPlan(intents=["booking"], service_ids=[UUID(value) for value in ids],
        requested_date=day.isoformat(), professional_id=UUID(str(chosen["id"])), requested_time=clock,
        time_relation=relation if clock else "none")
    return await _planned_slots(tenant, phone, plan, state, business, catalog, redis, key, preview,
        "Encontrei disponibilidade real com " + presentation.escape_label(chosen["name"]) + ".")


async def _continue_recent_availability(tenant, phone, body, state, business, catalog, professionals, redis, key, preview):
    """A whole clock selection can prepare a new summary, never confirm one."""
    previous = _recent_availability(state, catalog)
    if not previous or state.get("availability_query") or state.get("step") not in {None, "menu"}:
        return None
    text_value = norm(body)
    if (catalog_question(body) or availability_question(body) or control_intent(body) is not None or "?" in body
            or re.search(r"\b(?:nao|nem|nunca|se|caso|desde que|talvez|depende|mas|ou)\b", text_value)):
        return None
    context = await _time_context(tenant, body, state, business, catalog, professionals)
    clock = build_plan(message=body, catalog=[], context={**context, "service_ids": [], "step": "time"})
    if not clock.requested_time or clock.time_relation != "exact":
        return None
    ids = previous["services"]
    option = state.get("service_quote_option")
    async def clarify_composition():
        amended = build_plan(message=body, catalog=catalog, professionals=professionals,
            context={**context, "service_ids": [], "step": "time"},
            reference_date=datetime.now(ZoneInfo(business["timezone"])).date())
        draft = UnderstandingPlan(intents=["booking"], requested_date=amended.requested_date or previous["day"],
            requested_time=amended.requested_time or clock.requested_time, time_relation="exact",
            professional_id=amended.professional_id, ambiguities=["service"])
        await _save_booking_draft(draft, {}, business, catalog, professionals, redis, key)
        return {"answer": "Quais serviços você quer para esse horário?", "step": "menu", "_service_reply_prompt": True}
    if clock.ambiguities:
        return await clarify_composition() if isinstance(option, dict) else None
    if isinstance(option, dict):
        fresh = type(option.get("expires_at")) in {int, float} and math.isfinite(option["expires_at"]) and option["expires_at"] > datetime.now().timestamp()
        accepted = bool(re.match(r"^(?:pode ser|quero|prefiro|fechou)(?:\s|[,;])", text_value))
        valid_option = (fresh and option.get("revision") == qr_profile.catalog_revision(business, catalog, professionals)
                and option.get("day") == previous["day"] and isinstance(option.get("services"), list)
                and option["services"] and set(option["services"]) <= {str(row["id"]) for row in catalog})
        if accepted and not valid_option:
            state.pop("service_quote_option", None)
            await redis.set(key, json.dumps(state), ex=_remaining_state_ttl(state))
            return {"answer": "Precisamos conferir novamente os serviços e os valores desse orçamento antes de agendar. Quais serviços você quer incluir?", "step": state.get("step", "menu")}
        if accepted and valid_option:
            ids = option["services"]
    if denial := capability_denial(business, "appointments"):
        return denial
    candidates = await professionals_for(tenant, [UUID(value) for value in ids])
    shown_ids = {str(row["id"]) for row in previous.get("professionals", [])}
    if previous.get("professional"):
        shown_ids.add(previous["professional"])
    candidates = [row for row in candidates if str(row["id"]) in shown_ids]
    choices = []
    unrecognized = False
    try:
        async with asyncio.timeout(12), get_session_factory()() as db:
            for person in candidates:
                actual = _slot_result(await list_slots(db, tenant, UUID(str(person["id"])), [UUID(value) for value in ids], date.fromisoformat(previous["day"])), pricing=True)
                selected = choose_time(body, actual["slots"], timezone=business["timezone"], business_hours=context.get("hours"), numbered_menu=False)
                if selected.kind == "selected":
                    choices.append((person, actual, selected.when))
                elif selected.kind == "uncertain":
                    unrecognized = True
    except AVAILABILITY_ERRORS:
        return {"answer": "Não consegui consultar a agenda agora. Podemos tentar novamente em instantes?", "step": state.get("step", "menu")}
    if unrecognized and not choices:
        if isinstance(option, dict):
            return await clarify_composition()
        return None
    if not choices:
        return {"answer": "Esse horário não está livre para os serviços consultados. Quer que eu confira outras opções?", "step": state.get("step", "menu")}
    proposed = {"step": "professional", "services": list(ids), "day": previous["day"],
        "pending_clock": clock.requested_time, "pending_relation": "exact", "customer_name": state.get("customer_name")}
    if len(choices) > 1:
        proposed["professionals"] = [{"id": str(row[0]["id"]), "name": row[0]["name"]} for row in choices]
        await redis.set(key, json.dumps(proposed), ex=STATE_TTL)
        return {"answer": "Nesse horário, tenho estas opções. Com quem você prefere?\n" + "\n".join(
            "• " + presentation.escape_label(row[0]["name"]) for row in choices), "step": "professional"}
    person, actual, when = choices[0]
    proposed.update(professional=str(person["id"]), professional_name=person["name"],
        price=actual["price_cents"], duration=actual["duration_min"], step="time", slot_menu_numbered=False)
    return await prepare_booking_summary(tenant, phone, proposed, when, business, catalog, redis, key, preview)


async def answer_availability(tenant: UUID, body: str, state: dict, business: dict,
                              catalog: list[dict], redis, key: str):
    """A separate read-only inquiry; booking selections and confirmation stay intact."""
    if denial := capability_denial(business, "appointments"):
        return denial
    if state.get("step") in {"confirm", "reschedule_confirm"}:
        state["confirmation_requires_review"] = True
    inquiry = dict(state.get("availability_query", {}))
    if _asks_available_professionals(body):
        inquiry["kind"] = "professionals"
    if not inquiry:
        previous = state.get("last_availability", {})
        try:
            if 0 <= datetime.now().timestamp() - float(previous.get("queried_at", 0)) <= 900:
                inquiry = dict(previous)
        except (TypeError, ValueError):
            pass
    reference = catalog_question_services(re.split(r"\bcom\s+", body, maxsplit=1, flags=re.IGNORECASE)[0], catalog, strict=False)
    if reference is not None:
        inquiry["services"] = [str(item) for item in reference]
        inquiry.pop("professional", None)
    if inquiry.get("waiting_for") == "service" and body.strip().isdigit():
        index = int(body.strip()) - 1
        if 0 <= index < len(catalog):
            inquiry["services"] = [str(catalog[index]["id"])]
    service_reply = resolve_service_reply(body, catalog, awaiting_service=inquiry.get("waiting_for") == "service")
    if service_reply.kind == "resolved":
        inquiry["services"] = [str(value) for value in service_reply.service_ids]
    elif service_reply.kind == "ambiguous":
        listing = " • ".join(presentation.escape_label(option.name) for option in service_reply.options)
        state["availability_query"] = {**inquiry, "waiting_for": "service"}
        await redis.set(key, json.dumps(state), ex=_remaining_state_ttl(state))
        return {"answer": "Qual destas opções você procura?\n" + listing, "step": state.get("step", "menu")}
    valid = {str(item["id"]) for item in catalog}
    ids = [item for item in inquiry.get("services", state.get("services", [])) if item in valid]
    day = requested_day(body, business["timezone"])
    if day:
        inquiry["day"] = day.isoformat()
        inquiry.pop("search_next", None)
    elif re.search(r"\b(?:que|qual|quais|proximos?)(?: sao)?(?: os?)?\s+dias?\b|\bquando\b|\btem\s+(?:algum\s+)?livre\b", norm(body)):
        inquiry["search_next"] = True
        inquiry.pop("day", None)
    chosen_day = None if inquiry.get("search_next") else inquiry.get("day") or state.get("day")

    async def ask(answer, field):
        inquiry["waiting_for"] = field
        state["availability_query"] = inquiry
        if not state.get("step"):
            state["step"] = "menu"
        await redis.set(key, json.dumps(state), ex=_remaining_state_ttl(state))
        return {"answer": answer, "step": state["step"]}

    if not ids:
        listing = "\n".join(f"{i}. {item['name']}" for i, item in enumerate(catalog[:12], 1))
        return await ask("Posso consultar a agenda real. Qual serviço você quer consultar?\n" + listing, "service")
    inquiry["services"] = ids
    names = " e ".join(item["name"] for item in catalog if str(item["id"]) in ids)
    if not chosen_day and not inquiry.get("search_next"):
        return await ask(f"Para consultar {names}, qual dia deseja? Pode dizer amanhã ou uma data, como 20/11. Nenhuma reserva será criada.", "date")
    if chosen_day:
        inquiry["day"] = chosen_day
    pros = await professionals_for(tenant, [UUID(item) for item in ids])
    if not pros:
        state.pop("availability_query", None)
        await redis.set(key, json.dumps(state), ex=STATE_TTL)
        return {"answer": "Não há profissional habilitado para essa combinação de serviços. Nenhuma reserva foi alterada.", "step": state.get("step", "menu")}
    if inquiry.get("kind") == "professionals" and chosen_day:
        return await _professionals_availability(tenant, state, inquiry, business, catalog, pros, redis, key)
    mentioned = re.split(r"\bcom\s+", body, maxsplit=1, flags=re.IGNORECASE)
    named = re.split(r"\b(?:amanha|hoje|depois|no dia|dia\s+\d|as\s+\d)\b", norm(mentioned[-1]), maxsplit=1)[0].strip(" ,.!?")
    selected = professional_choice(named, pros)
    if selected is None and len(mentioned) == 1:
        previous = inquiry.get("professional") or state.get("professional")
        selected = next((p for p in pros if str(p["id"]) == previous), None)
        selected = selected or (pros[0] if len(pros) == 1 else None)
    if selected is None:
        period = f"em {date.fromisoformat(chosen_day):%d/%m}" if chosen_day else "nos próximos sete dias"
        return await ask(f"Para consultar {names} {period}, qual profissional prefere?\n" + "\n".join(
            f"{i}. {p['name']}" for i, p in enumerate(pros, 1)), "professional")
    inquiry["professional"] = str(selected["id"])
    if inquiry.get("search_next"):
        today = datetime.now(ZoneInfo(business["timezone"])).date()
        found = []
        found_slots = []
        metadata = []
        try:
            async with asyncio.timeout(12), get_session_factory()() as db:
                for offset in range(7):
                    candidate_day = today + timedelta(days=offset)
                    result = await list_slots(db, tenant, UUID(inquiry["professional"]),
                                              [UUID(item) for item in ids], candidate_day)
                    result = _slot_result(result)
                    focused, _ = _focused_query_slots(result["slots"], inquiry, business["timezone"])
                    times = [datetime.fromisoformat(item).strftime("%H:%M") for item in focused[:6]]
                    if times:
                        found.append(f"• {candidate_day:%d/%m}: " + ", ".join(times))
                        found_slots.extend(focused[:6])
                        price, duration = result.get("price_cents"), result.get("duration_min")
                        metadata.append((price, duration) if type(price) is int and price >= 0 and type(duration) is int and duration > 0 else None)
                    if len(found) == 3:
                        break
        except AVAILABILITY_ERRORS:
            return await ask("Não consegui consultar a agenda agora. Podemos tentar novamente em instantes?", "retry")
        state.pop("availability_query", None)
        state["last_availability"] = {"services": list(ids), "professional": str(selected["id"]), "search_next": True,
            "queried_at": datetime.now().timestamp()}
        await redis.set(key, json.dumps(state), ex=_remaining_state_ttl(state))
        shared = metadata[0] if metadata and metadata[0] is not None and all(row == metadata[0] for row in metadata) else None
        answer = "_Consulta dos próximos sete dias._\n\n" + presentation.render_slots([x["name"] for x in catalog if str(x["id"]) in ids], found_slots,
            _preferences(business), timezone=business["timezone"], professional=selected["name"],
            total_cents=shared[0] if shared else None, duration_min=shared[1] if shared else None, ask_selection=False)
        review = "\n" + resume_prompt(state) if state.get("confirmation_requires_review") else ""
        return {"answer": answer + "\n\n_Consulta sem reserva; nenhum agendamento foi criado ou alterado._" + review,
                "step": state.get("step", "menu")}
    try:
        async with get_session_factory()() as db:
            result = await list_slots(db, tenant, UUID(inquiry["professional"]),
                                      [UUID(item) for item in ids], date.fromisoformat(chosen_day))
            result = _slot_result(result)
    except AVAILABILITY_ERRORS:
        return await ask("Não consegui consultar a agenda agora. Podemos tentar novamente em instantes?", "retry")
    state.pop("availability_query", None)
    state["last_availability"] = {"services": list(ids), "professional": str(selected["id"]), "day": chosen_day,
        "queried_at": datetime.now().timestamp()}
    await redis.set(key, json.dumps(state), ex=_remaining_state_ttl(state))
    focused, note = _focused_query_slots(result["slots"], inquiry, business["timezone"])
    price, duration = result.get("price_cents"), result.get("duration_min")
    answer = (note + "\n\n" if note else "") + presentation.render_slots([x["name"] for x in catalog if str(x["id"]) in ids], focused[:12],
        _preferences(business), timezone=business["timezone"], professional=selected["name"], day=chosen_day,
        total_cents=price if type(price) is int and price >= 0 else None,
        duration_min=duration if type(duration) is int and duration > 0 else None, ask_selection=False)
    review = "\n" + resume_prompt(state) if state.get("confirmation_requires_review") else ""
    return {"answer": answer + "\n\n_Consulta sem reserva; nenhum agendamento foi criado ou alterado._" + review,
            "step": state.get("step", "menu")}


def _focused_query_slots(values, inquiry, timezone):
    clock = inquiry.get("pending_clock")
    if not isinstance(clock, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", clock):
        return values, ""
    relation = inquiry.get("pending_relation", "exact")
    local = [(value, datetime.fromisoformat(value).astimezone(ZoneInfo(timezone))) for value in values]
    selected = [value for value, when in local if (when.strftime("%H:%M") == clock if relation == "exact" else
        when.strftime("%H:%M") > clock if relation == "after" else when.strftime("%H:%M") < clock if relation == "before" else False)]
    if selected or relation in {"after", "before"} or not values:
        return selected, ""
    target = int(clock[:2])*60 + int(clock[3:])
    nearest = sorted(local, key=lambda item: (abs(item[1].hour*60+item[1].minute-target), item[1]))[:6]
    return [value for value, _ in nearest], ("O horário pedido não está disponível; estes são horários reais próximos." if relation == "exact" else "Estes são horários reais próximos ao período pedido.")


async def start_booking(tenant: UUID, body: str, decision: Intention, state: dict,
                        business: dict, catalog: list[dict], redis, key: str, preview: bool):
    if denial := capability_denial(business, "appointments"):
        return denial
    chosen = [item for item in catalog if item["id"] in decision.service_ids]
    # Drop stale slots, totals and confirmation tokens whenever services change.
    next_state = {"services": [str(item["id"]) for item in chosen], "step": "date",
                  "customer_name": state.get("customer_name")}
    chosen_day = requested_day(" ".join(filter(None, [body, decision.requested_when])), business["timezone"])
    mentions_weekday = bool(re.search(r"\b(?:segunda|terca|quarta|quinta|sexta|sabado|domingo)(?:[ -]feira)?\b", norm(body)))
    if mentions_weekday:
        # The utterance is authoritative; a classifier cannot override its calendar reference.
        chosen_day = requested_day(body, business["timezone"])
        if chosen_day is None:
            await redis.set(key, json.dumps(next_state), ex=STATE_TTL)
            return {"answer": "Qual dia você prefere? Recebi mais de uma referência de data e preciso confirmar antes de consultar a agenda.", "step": "date"}
    day = chosen_day.isoformat() if chosen_day else state.get("day")
    total = sum(item["price_cents"] for item in chosen)
    duration = sum(item["duration_min"] for item in chosen)
    names = " e ".join(item["name"] for item in chosen)
    summary = _service_quote([row["id"] for row in chosen], catalog, business) if set(next_state["services"]) != set(state.get("services") or []) else ""
    if not day and not state.get("professional") and not re.search(r"\bcom\s+", body, re.IGNORECASE):
        await redis.set(key, json.dumps(next_state), ex=STATE_TTL)
        return {"answer": summary + " Para qual dia gostaria de agendar?", "step": "date"}
    pros = await professionals_for(tenant, [item["id"] for item in chosen])
    if not pros:
        await redis.set(key, json.dumps(next_state), ex=STATE_TTL)
        return {"answer": summary + " Não há profissional habilitado para essa combinação. Pode escolher outro serviço.",
                "step": "date"}
    previous = next((p for p in pros if str(p["id"]) == state.get("professional")), None)
    mention = re.split(r"\bcom\s+", body, maxsplit=1, flags=re.IGNORECASE)
    named = re.split(r"\b(?:amanha|hoje|depois|no dia|dia\s+\d|as\s+\d|pelas\s+\d)\b|(?<!\w)\d{1,4}[-/]\d",
                     norm(mention[-1]), maxsplit=1)[0].strip(" ,.!?")
    explicit = len(mention) == 2
    selected = professional_choice(named, pros)
    if not explicit:
        selected = selected or previous or (pros[0] if len(pros) == 1 else None)
    if selected:
        next_state["professional"] = str(selected["id"])
        next_state["professional_name"] = selected["name"]
    if not day and (selected or not explicit):
        await redis.set(key, json.dumps(next_state), ex=STATE_TTL)
        return {"answer": summary + " Para qual dia gostaria de agendar?", "step": "date"}
    if day:
        next_state["day"] = day
    if selected:
        answer, next_state = await offer_slots(tenant, next_state, preview)
    else:
        next_state.update(step="professional", professionals=[{"id": str(p["id"]), "name": p["name"]} for p in pros])
        answer = ("Não identifiquei esse profissional com segurança. " if explicit else "") + \
            "Qual profissional prefere?\n" + "\n".join(f"{i}. {p['name']}" for i, p in enumerate(pros, 1))
    await redis.set(key, json.dumps(next_state), ex=STATE_TTL)
    return {"answer": summary + "\n" + answer, "step": next_state["step"]}


def _bounded_reply(factual: str, operational: str, step: str) -> dict:
    answer = "\n\n".join(x for x in (factual, operational) if x)
    if not presentation.fits_message(answer):
        return {"answer": "Recebi mais de um assunto e quero preservar todas as condições sem cortar informações. Você prefere consultar as informações ou preparar o agendamento primeiro? Nenhuma alteração foi confirmada.",
                "step": "choose_priority"}
    return {"answer": answer, "step": step, "_literal_information": bool(factual)}


async def _commerce_turn(tenant: UUID, phone: str, body: str, state: dict, business: dict,
                          catalog: list[dict], redis, key: str, preview: bool):
    """Tenant/contact-scoped products, with no partial mixed operation."""
    if not qr_commerce.enabled():
        return None
    message_id = _message_identity.get()
    if not message_id:
        return None  # Only the authenticated intake may provide real identity.
    commerce_key = key + ":commerce"
    try:
        commerce_state = json.loads(await redis.get(commerce_key) or "null")
    except (TypeError, ValueError):
        commerce_state = None
    if not isinstance(commerce_state, dict):
        commerce_state = None
    lower = norm(body)
    booking_subject = bool(catalog_match(body, catalog)) or bool(re.search(
        r"\b(?:agendar|agendamento|agenda|remarcar|reagendar|horarios?|dias? livres|marcar|reservar|cabelo|corte|barba)\b", lower))
    if (qr_commerce.receiving.enabled() and qr_commerce.receiving.receiving_reference(body, commerce_state)
            and not catalog_match(body, catalog)):
        booking_subject = False  # A product's receiving link never creates a new appointment.
    relevant = await qr_commerce.probe(tenant, phone, body, message_id=message_id, state=commerce_state)
    semantic = None
    if (not relevant and commerce_state and commerce_state.get("pending") and not booking_subject
            and control_intent(body) not in {"greeting", "identity", "human", "my_bookings"}):
        action = "cancel" if commerce_state["pending"].get("action") == "cancel" else "order"
        consent = contextual_response(body, awaiting_confirmation=True, confirmation_action=action)
        if consent.reason == "unresolved":
            consent = await interpret_contextual_response(message=body, awaiting_confirmation=True,
                key=os.getenv("GROQ_API_KEY", ""), model=os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"), confirmation_action=action)
            if consent.kind == "uncertain":
                return {"answer": presentation.render_prompt("confirmation_review", _preferences(business)), "step": "commerce_clarify"}
        if consent.kind == "affirm":
            relevant, semantic = True, "affirm"
        elif consent.kind == "decline":
            relevant, body = True, "não"
    if not relevant:
        if commerce_state and commerce_state.get("pending"):
            commerce_state["last_message_id"] = message_id
            await redis.set(commerce_key, json.dumps(commerce_state), ex=STATE_TTL)
        return None
    if state.get("step") in {"confirm", "reschedule_confirm"} and compound_candidate(body):
        scope = qr_commerce.store.CustomerScope(tenant, phone, message_id)
        product_plan = qr_commerce.interpret(body, await qr_commerce.store.active_catalog(scope),
            (commerce_state or {}).get("cart", []), (commerce_state or {}).get("choices", {}))
        if product_plan.intent in {"order", "quote", "adjust", "cancel", "confirm"}:
            await _review_after_inquiry(state, redis, key)
            return {"answer": "Recebi uma confirmação e outro pedido. Qual resumo você quer conferir primeiro? Nenhuma nova operação foi confirmada.", "step": "choose_priority"}
    if booking_subject:
        await _review_after_inquiry(state, redis, key)
        if commerce_state and commerce_state.get("pending"):
            commerce_state["last_message_id"] = message_id
            await redis.set(commerce_key, json.dumps(commerce_state), ex=STATE_TTL)
        return {"answer": "Recebi assuntos de produtos e de agendamento. Qual você prefere resolver primeiro? Vou conferir cada resumo antes de qualquer confirmação.", "step": "choose_priority"}
    if state.get("step") in {"confirm", "reschedule_confirm"} and contextual_response(body, awaiting_confirmation=True, semantic_kind=semantic).kind == "affirm":
        await _review_after_inquiry(state, redis, key)
        return {"answer": "Há um resumo de agendamento e uma conversa de produtos. Qual resumo você quer conferir antes de confirmar?", "step": "choose_priority"}
    await _review_after_inquiry(state, redis, key)
    if (re.search(r"\b(?:vendem|vende|voces tem)\b", lower) and not re.search(
            r"\b(?:comprar|compra|quero|pedido|orcamento|quanto|preco|valor|estoque|quantidade)\b", lower)):
        if denial := capability_denial(business, "catalog_answers"):
            return denial
        factual = await _independent_information(tenant, phone, body, "catalog", business, catalog)
        return {"answer": factual or "Não consegui confirmar esse produto no catálogo atual. A equipe pode conferir essa informação.",
                "step": "commerce_information"}
    if denial := capability_denial(business, "catalog_answers"):
        scope = qr_commerce.store.CustomerScope(tenant, phone, message_id)
        product_plan = qr_commerce.interpret(body, await qr_commerce.store.active_catalog(scope),
            (commerce_state or {}).get("cart", []), (commerce_state or {}).get("choices", {}))
        if (product_plan.intent in {"price", "catalog", "stock", "faq"} or
                (qr_commerce.receiving.enabled() and qr_commerce.receiving.interpret_receiving(body).query)):
            return denial
    result = await qr_commerce.handle(tenant, phone, body, message_id=message_id, state=commerce_state, preview=preview,
        customer_name=state.get("customer_name"), allow_orders="product_orders" in (business.get("profile") or {}).get("capabilities", []),
        preferences=_preferences(business), semantic_kind=semantic)
    if result is None:
        return None
    private_state = result.get("state")
    if isinstance(private_state, dict):
        await redis.set(commerce_key, json.dumps(private_state), ex=STATE_TTL)
    return {"answer": result.get("answer"), "step": result.get("step", "commerce_information"),
            **({"_verified_operation": True} if result.get("_verified_operation") is True else {})}


async def _independent_information(tenant: UUID, phone: str, query: str, kind: str,
                                   business: dict, catalog: list[dict]):
    """Read factual information without retaining a product selection or cart."""
    if catalog_match(query, catalog):
        return None  # It may be a question about the proposed booking's facts.
    if re.search(r"\b(?:comprar|compra|pedido|orcamento|adicion\w*|inclu\w*|retira\w*|troca\w*)\b", norm(query)):
        return None
    if qr_commerce.enabled():
        from app.qr_commerce_information import readonly_information
        scope = qr_commerce.store.CustomerScope(tenant, phone, _message_identity.get())
        factual = await readonly_information(scope, query, kind=kind,
            profile=business.get("profile"), preferences=_preferences(business))
        if factual:
            return factual
        if kind != "faq":
            return None
    plan = build_plan(message=query, catalog=catalog, faqs=faq_catalog(business),
        proposal=UnderstandingPlan(intents=["faq"]))
    reply = compose_information(plan, business, catalog, message=query)
    return reply.answer if reply.answer and not reply.unresolved else None


async def _compound_booking_turn(tenant: UUID, phone: str, body: str, state: dict, business: dict,
                                  catalog: list[dict], redis, key: str, preview: bool):
    """One unchanged-summary consent plus independent read-only facts (section10)."""
    if state.get("step") not in {"confirm", "reschedule_confirm"} or not compound_candidate(body):
        return None
    incoming_id, summary_id = _message_identity.get(), state.get("confirmation_message_id")
    try:
        fresh = math.isfinite(float(state.get("confirmation_expires_at", 0))) and float(state.get("confirmation_expires_at", 0)) > datetime.now().timestamp()
    except (TypeError, ValueError):
        fresh = False
    if (not fresh or state.get("confirmation_requires_review") or state.get("availability_query") or
            not incoming_id or not summary_id or incoming_id == summary_id or not state.get("confirmation_summary")):
        return None
    decision = await interpret_compound_response(message=body, key=os.getenv("GROQ_API_KEY", ""),
        model=os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"))
    if decision.kind == "other":
        return None
    async def clarify(facts=""):
        await _review_after_inquiry(state, redis, key)
        return _bounded_reply(facts, presentation.render_prompt("confirmation_review", _preferences(business)), state["step"])
    if decision.kind != "independent_information":
        return await clarify()
    if denial := capability_denial(business, "catalog_answers"):
        await _review_after_inquiry(state, redis, key)
        return denial
    try:
        private = json.loads(await redis.get(key + ":commerce") or "null")
    except (TypeError, ValueError):
        private = None
    if isinstance(private, dict) and private.get("pending"):
        return await clarify()
    try:
        factual = await _independent_information(tenant, phone, decision.information_clause, decision.information_kind, business, catalog)
    except (HTTPException, GroqServiceError, ValueError):
        return await clarify()
    if not factual:
        return await clarify()
    try:
        if state["step"] == "reschedule_confirm":
            facts = state["reschedule_facts"]
            if not state.get("expected_snapshot"):
                return await clarify(factual)
            summary = presentation.BookingSummary(tuple(facts["services"]), datetime.fromisoformat(state["new_start"]),
                facts["timezone"], facts["total_cents"], professional=facts["professional"], duration_min=facts["duration_min"], protocol="X" * 80)
            maximum = presentation.render_summary(summary, _preferences(business), confirmed=True, operation="reschedule")
        else:
            maximum = presentation.render_summary(_summary(state, state["selected_start"], business, catalog, protocol="X" * 80),
                                                  _preferences(business), confirmed=True)
    except (KeyError, TypeError, ValueError):
        return await clarify(factual)
    if not presentation.fits_message(maximum + "\n\n" + factual):
        return await clarify()
    # No preceding information/review mutation: recurse only through the normal
    # summary/tenant/revision/backend confirmation gates, with commerce skipped.
    result = await _turn(tenant, phone, "SIM", redis, key.removeprefix(f"orion:qr:ai:{tenant}:"), preview, _confirm_scoped=True)
    composed = _bounded_reply(result.get("answer") or "", factual, result["step"])
    if result.get("_verified_operation") is True:
        composed["_verified_operation"] = True
    return composed


async def _review_after_inquiry(state: dict, redis, key: str):
    if state.get("step") in REVIEW_STEPS:
        state["confirmation_requires_review"] = True
        await redis.set(key, json.dumps(state), ex=_remaining_state_ttl(state))


def _draft_remaining(state: dict) -> float:
    value = state.get("clarification_expires_at")
    return max(0, value - datetime.now().timestamp()) if type(value) in {int, float} and math.isfinite(value) else 0


def _price_correction_prefix(body: str, state: dict) -> str | None:
    """A correction before a price question changes only the private request."""
    if state.get("step") not in {"date", "professional", "time", "confirm"} or state.get("appointment"):
        return None
    parts = re.split(r"\b(?:quanto|precos?|valores?|custa\w*)\b", norm(body), maxsplit=1)
    if len(parts) != 2:
        return None
    prefix = parts[0]
    if (not re.search(r"\b(?:melhor|na verdade|quer dizer|em vez|ao inves|deixa|retir\w*|tira\w*|remove\w*)\b", prefix)
            or re.search(r"\b(?:se|caso|desde que|talvez|ou)\b", prefix)):
        return None
    return prefix


def _price_correction_source(state: dict, business: dict, catalog: list[dict], professionals: list[dict]) -> dict:
    """Copy current factual choices, never a hold or authority to confirm it."""
    if state.get("booking_draft"):
        return state
    now = datetime.now().timestamp()
    deadline, fresh = now + 300, True
    if state.get("step") == "confirm":
        value = state.get("confirmation_expires_at")
        fresh = type(value) in {int, float} and math.isfinite(value) and value > now
        if fresh:
            deadline = min(value, deadline)
    source = {"booking_draft": True, "clarification_expires_at": deadline}
    if not fresh:
        return source  # An expired summary contributes no inherited choices.
    if replacement := _replacement_hold(state):
        source["replacement_hold"] = replacement
    valid = {str(row["id"]) for row in catalog}
    ids = state.get("services")
    if isinstance(ids, list) and ids and all(isinstance(value, str) and value in valid for value in ids):
        source["services"] = ids[:]
    person = next((row for row in professionals if str(row["id"]) == state.get("professional")), None)
    if person:
        source.update(professional=str(person["id"]), professional_name=person["name"])
    zone = ZoneInfo(business["timezone"])
    try:
        day = date.fromisoformat(state["day"])
        if day >= datetime.now(zone).date():
            source["day"] = day.isoformat()
    except (KeyError, TypeError, ValueError):
        pass
    clock = state.get("pending_clock")
    if isinstance(clock, str) and re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", clock):
        source.update(pending_clock=clock, pending_relation=state.get("pending_relation", "exact"))
    elif state.get("selected_start") and source.get("day"):
        try:
            selected = datetime.fromisoformat(state["selected_start"])
            if selected.tzinfo is not None and selected.astimezone(zone).date().isoformat() == source["day"]:
                source.update(pending_clock=selected.astimezone(zone).strftime("%H:%M"), pending_relation="exact")
        except (TypeError, ValueError):
            pass
    for field in ("customer_name", "new_customer_name"):
        if state.get(field):
            source[field] = state[field]
    return source


def _merge_booking_draft(plan: UnderstandingPlan, state: dict, catalog: list[dict], professionals: list[dict]) -> UnderstandingPlan:
    """Reuse only unambiguous facts from this short-lived, unconfirmed request."""
    if not state.get("booking_draft") or _draft_remaining(state) <= 0 or "booking" not in plan.intents or set(plan.intents) & {"cancel", "reschedule", "confirm", "human"}:
        return plan
    update = {}
    if (not plan.service_ids and "service" not in plan.ambiguities and not plan.declined_service_ids
            and plan.selection_mode not in {"remove", "simulate"}):
        ids = [UUID(value) for value in state.get("services", [])]
        if ids and set(ids) <= {UUID(str(row["id"])) for row in catalog}:
            update.update(service_ids=ids, selection_mode="replace")
    if not plan.requested_date and "date" not in plan.ambiguities and state.get("day"):
        update["requested_date"] = state["day"]
    if not plan.professional_id and "professional" not in plan.ambiguities and state.get("professional"):
        person = UUID(state["professional"])
        if person in {UUID(str(row["id"])) for row in professionals}:
            update["professional_id"] = person
    if not plan.requested_time and "time" not in plan.ambiguities and state.get("pending_clock"):
        update.update(requested_time=state["pending_clock"], time_relation=state.get("pending_relation", "exact"))
    return plan.model_copy(update=update)


async def _save_booking_draft(plan: UnderstandingPlan, state: dict, business: dict, catalog: list[dict], professionals: list[dict], redis, key: str, *, allow_complete: bool = False):
    """An unclear booking saves facts, never a hold, consent or old summary."""
    ambiguous = set(plan.ambiguities)
    if ("booking" not in plan.intents or (not ambiguous and not allow_complete) or not ambiguous <= {"service", "date", "time", "professional"}
            or set(plan.intents) & {"cancel", "reschedule", "confirm", "human"}):
        return None
    now = datetime.now().timestamp()
    deadline = state["clarification_expires_at"] if state.get("booking_draft") and _draft_remaining(state) > 0 else now+300
    pending = [field for field in ("service", "date", "professional", "time") if field in ambiguous]
    step = {"service": "menu", "date": "date", "professional": "professional", "time": "time"}[pending[0]] if pending else "time"
    draft = {"step": step, "booking_draft": True, "clarification_pending": pending, "clarification_expires_at": deadline}
    if replacement := _replacement_hold(state):
        draft["replacement_hold"] = replacement
    if "service" not in ambiguous and plan.service_ids:
        draft["services"] = [str(value) for value in plan.service_ids]
    if "date" not in ambiguous and plan.requested_date:
        draft["day"] = plan.requested_date
    if "professional" not in ambiguous and plan.professional_id:
        person = next((row for row in professionals if str(row["id"]) == str(plan.professional_id)), None)
        if person:
            draft.update(professional=str(person["id"]), professional_name=person["name"])
    if "time" not in ambiguous and plan.requested_time:
        draft.update(pending_clock=plan.requested_time, pending_relation=plan.time_relation)
    if plan.customer_name or state.get("customer_name"):
        draft["customer_name"] = plan.customer_name or state["customer_name"]
    if plan.customer_name or state.get("new_customer_name"):
        draft["new_customer_name"] = plan.customer_name or state["new_customer_name"]
    if step == "menu":
        draft["menu_ids"] = [str(row["id"]) for row in catalog[:12]]
    if step == "professional":
        draft["professionals"] = [{"id": str(row["id"]), "name": row["name"]} for row in professionals]
    if step == "time":
        draft["slots"] = []
    await redis.set(key, json.dumps(draft), ex=max(1, min(300, int(deadline-now))))
    return draft


def _draft_description(state: dict, catalog: list[dict], *, ask_review: bool = True) -> str:
    names = [row["name"] for row in catalog if str(row["id"]) in state.get("services", [])]
    day = date.fromisoformat(state["day"]).strftime("%d/%m/%Y") if state.get("day") else None
    clock = None
    if state.get("pending_clock"):
        hour, minute = state["pending_clock"].split(":")
        clock = str(int(hour)) + "h" + (minute if minute != "00" else "")
        relation = {"exact": "", "after": "depois de ", "before": "antes de ", "around": "por volta de "}.get(state.get("pending_relation"), "")
        clock = relation + clock
    return presentation.render_draft_request(names, _reply_preferences.get(), desired_day=day,
        desired_time=clock, professional=state.get("professional_name"), ask_review=ask_review)


def _resume_booking_draft(body: str) -> bool:
    text = norm(body).strip(" .!?")
    return _summary_requested(body) or bool(re.fullmatch(
        r"(?:quero|pode|podemos|vamos|gostaria de)\s+(?:seguir|continuar|prosseguir)\s+com\s+(?:esse|este|o)\s+pedido", text))


def _draft_review_accepted(body: str, state: dict, catalog: list[dict], professionals: list[dict]) -> bool:
    incoming, offered = _message_identity.get(), state.get("draft_review_message_id")
    return bool(state.get("booking_draft") and _draft_remaining(state) > 0 and body.strip().lower() in YES
        and incoming and offered and incoming != offered and not state.get("clarification_pending")
        and state.get("services") and set(state["services"]) <= {str(row["id"]) for row in catalog}
        and state.get("day") and state.get("professional") in {str(row["id"]) for row in professionals}
        and state.get("pending_clock") and state.get("pending_relation") == "exact")


def _replacement_hold(state, professional=None):
    value = state.get("replacement_hold")
    if state.get("hold") and state.get("hold_snapshot"):
        value = {"id": state["hold"], "expected_snapshot": state["hold_snapshot"],
            "professional": state.get("professional"), "expires_at": state.get("confirmation_expires_at")}
    try:
        valid = (isinstance(value, dict) and UUID(value["id"]) and UUID(value["professional"])
            and re.fullmatch(r"[a-f0-9]{64}", value["expected_snapshot"])
            and type(value.get("expires_at")) in {int, float} and math.isfinite(value["expires_at"])
            and value["expires_at"] > datetime.now().timestamp())
    except (KeyError, ValueError, TypeError, AttributeError):
        return None
    if not valid or professional is not None and value["professional"] != str(professional):
        return None
    return dict(value)


def _replacement_slot_kwargs(state, professional, phone):
    value = _replacement_hold(state, professional)
    return {"replace_hold_id": UUID(value["id"]), "customer_phone": "+"+phone,
        "expected_snapshot": value["expected_snapshot"]} if value else {}


def _hold_matches_summary(hold, state, when, catalog):
    try:
        requested = datetime.fromisoformat(when)
        items = hold["items"]
        selected = {str(row["id"]): row for row in catalog if str(row["id"]) in state["services"]}
        return (str(hold["professional_id"]) == state["professional"] and hold["start_at"] == requested
            and hold["end_at"] == requested + timedelta(minutes=state["duration"])
            and hold["total_cents"] == state["price"] and len(items) == len(state["services"])
            and [str(row["service_id"]) for row in items] == state["services"]
            and all(row["name_snapshot"] == selected[str(row["service_id"])]["name"]
                and row["price_snapshot_cents"] == selected[str(row["service_id"])]["price_cents"]
                and row["duration_snapshot_min"] == selected[str(row["service_id"])]["duration_min"] for row in items))
    except (KeyError, TypeError, ValueError):
        return False


async def _recover_booking(tenant, phone, state, business, catalog, redis, key, *, reason):
    """Keep verified choices after a failed hold, never its consent or handle."""
    ids = [str(row["id"]) for row in catalog if str(row["id"]) in state.get("services", [])]
    recovered = {"step": "time", "booking_recovery": True, "services": ids}
    for field in ("day", "professional", "professional_name", "customer_name"):
        if state.get(field):
            recovered[field] = state[field]
    await redis.set(key, json.dumps(recovered), ex=STATE_TTL)
    plan = UnderstandingPlan(intents=["booking"])
    return await _planned_slots(tenant, phone, plan, recovered, business, catalog, redis, key, False, reason)


async def prepare_booking_summary(tenant: UUID, phone: str, state: dict, when: str,
                                  business: dict, catalog: list[dict], redis, key: str,
                                  preview: bool, factual: str = ""):
    """Budget and validate the complete reply before the only hold creation path."""
    if denial := capability_denial(business, "appointments"):
        return denial
    names = " e ".join(item["name"] for item in catalog if str(item["id"]) in state["services"])
    professional_name = state.get("professional_name") or next((p["name"] for p in state.get("professionals", [])
        if p["id"] == state.get("professional")), None)
    who = f" com {professional_name}" if professional_name else ""
    composed = presentation.compose_summary(_summary(state, when, business, catalog), _preferences(business), simulation=preview)
    summary = composed.render()
    reply = _bounded_reply(factual, summary, "confirm")
    if reply["step"] == "choose_priority":
        return reply
    if preview:
        state.update(step="confirm", hold="preview")
    else:
        request_fingerprint = booking_fingerprint(UUID(state["professional"]),
            [UUID(x) for x in state["services"]], datetime.fromisoformat(when), "+" + phone)
        identity = _message_identity.get()
        if not identity:
            return _bounded_reply(factual, "Preciso de uma nova mensagem identificada para preparar este resumo. Pode escolher o horário novamente?", "no_summary")
        idem = hashlib.sha256(f"qr:{tenant}:{identity}:{request_fingerprint}".encode()).hexdigest()
        replacement = _replacement_hold(state, state["professional"])
        try:
            if state.get("new_customer_name"):
                await qr_intake_revision.check_before_write()
                await remember_name(tenant, phone, state["new_customer_name"])
            selected_name = state.get("customer_name") or await customer_name(tenant, phone) or "Cliente WhatsApp"
            async with get_session_factory()() as db:
                await qr_intake_revision.check_before_write()
                hold = await book_appointment(db, tenant, UUID(state["professional"]),
                    [UUID(x) for x in state["services"]], "+" + phone, selected_name,
                    datetime.fromisoformat(when), "qr-" + idem, source="whatsapp_qr",
                    **({"replace_hold_id": UUID(replacement["id"]), "expected_snapshot": replacement["expected_snapshot"]} if replacement else {}))
        except HTTPException:
            return await _recover_booking(tenant, phone, state, business, catalog, redis, key,
                reason="O horário não está mais disponível. Nenhuma reserva foi confirmada. Vou conferir alternativas para o mesmo pedido.")
        expires = hold.get("hold_expires_at")
        if (hold.get("status") != "hold" or not isinstance(expires, datetime) or expires.tzinfo is None
                or expires.timestamp() <= datetime.now().timestamp() or not isinstance(hold.get("expected_snapshot"), str)
                or not re.fullmatch(r"[a-f0-9]{64}", hold["expected_snapshot"]) or not _hold_matches_summary(hold, state, when, catalog)):
            return await _recover_booking(tenant, phone, state, business, catalog, redis, key,
                reason="Não consegui validar uma reserva provisória vigente para este resumo. Vou consultar alternativas sem confirmar nada.")
        state.update(step="confirm", hold=str(hold["id"]), hold_snapshot=hold["expected_snapshot"])
        state.pop("replacement_hold", None)
    state.pop("new_customer_name", None)
    for field in ("booking_draft", "clarification_pending", "clarification_expires_at", "draft_review_message_id"):
        state.pop(field, None)
    deadline = datetime.now().timestamp() + 300 if preview else expires.timestamp()
    state.update(confirmation_summary=summary, confirmation_presentation=composed.as_dict(), confirmation_expires_at=deadline,
                 selected_start=when, confirmation_message_id=_message_identity.get())
    await redis.set(key, json.dumps(state), ex=_remaining_state_ttl(state))
    return reply


async def prepare_reschedule_summary(tenant: UUID, phone: str, state: dict, when: str,
                                    business: dict, catalog: list[dict], redis, key: str,
                                    preview: bool, factual: str = ""):
    """A proposal only; committing the change requires a later fresh consent."""
    if preview:
        facts = _summary(state, when, business, catalog)
        expected = None
    else:
        try:
            async with get_session_factory()() as db:
                proposal = await preview_reschedule(db, tenant, UUID(state["appointment"]),
                    datetime.fromisoformat(when), customer_phone="+" + phone)
            facts = presentation.BookingSummary(tuple(proposal["services"]), proposal["start_at"], proposal["timezone"],
                proposal["total_cents"], professional=proposal["professional"], duration_min=proposal["duration_min"])
            expected = proposal["expected_snapshot"]
        except HTTPException:
            return _bounded_reply(factual, "Não consegui validar a remarcação desse horário. O agendamento original permanece. Podemos consultar outra opção.", "reschedule_conflict")
    composed = presentation.compose_summary(facts, _preferences(business), simulation=preview, operation="reschedule")
    summary = composed.render()
    result = _bounded_reply(factual, summary, "reschedule_confirm")
    if result["step"] != "choose_priority":
        state.update(step="reschedule_confirm", new_start=when, confirmation_summary=summary, confirmation_presentation=composed.as_dict(),
                     confirmation_expires_at=datetime.now().timestamp() + 300, expected_snapshot=expected,
                     confirmation_message_id=_message_identity.get(),
                     reschedule_facts={"services": list(facts.services), "professional": facts.professional,
                        "total_cents": facts.total_cents, "duration_min": facts.duration_min, "timezone": facts.timezone})
        await redis.set(key, json.dumps(state), ex=_remaining_state_ttl(state))
    return result


async def _natural_time_turn(tenant: UUID, phone: str, body: str, state: dict, business: dict,
                             catalog: list[dict], redis, key: str, preview: bool):
    """Resolve a simple natural clock/ordinal from a fresh full backend grid."""
    rescheduling = state.get("step") == "reschedule_time"
    if denial := capability_denial(business, "rescheduling" if rescheduling else "appointments"):
        return denial
    valid = {str(x["id"]) for x in catalog}
    try:
        day = date.fromisoformat(state["day"])
        ids = [UUID(x) for x in state["services"]]
        if (not ids or not set(state["services"]) <= valid or
                day < datetime.now(ZoneInfo(business["timezone"])).date()):
            raise ValueError
        pros = await professionals_for(tenant, ids)
        person = next((p for p in pros if str(p["id"]) == state["professional"]), None)
        if person is None:
            raise ValueError
        async with get_session_factory()() as db:
            actual = _slot_result(await list_slots(db, tenant, UUID(state["professional"]), ids, day,
                **(_replacement_slot_kwargs(state, state["professional"], phone) if not rescheduling else {})), pricing=True)
    except AVAILABILITY_ERRORS:
        return {"answer": "Não consegui consultar a agenda agora. Podemos tentar novamente em instantes?", "step": state.get("step", "date")}
    except (KeyError, ValueError):
        return {"answer": "Preciso consultar os dados e a disponibilidade desse pedido novamente. Qual dia você prefere?", "step": state.get("step", "date")}
    time_context = await _time_context(tenant, body, state, business, catalog, pros)
    choice = choose_time(body, actual["slots"], presented_slots=state.get("slots", []),
                         timezone=business["timezone"], business_hours=time_context.get("hours"),
                         numbered_menu=state.get("slot_menu_numbered", True))
    state.update(price=actual["price_cents"], duration=actual["duration_min"], professional_name=person["name"])
    if choice.kind == "selected":
        if rescheduling:
            return await prepare_reschedule_summary(tenant, phone, state, choice.when, business, catalog, redis, key, preview)
        return await prepare_booking_summary(tenant, phone, state, choice.when, business, catalog, redis, key, preview)
    options = list(choice.alternatives) if choice.kind == "alternatives" else actual["slots"]
    if choice.kind == "uncertain":
        return {"answer": "Qual horário você prefere? Preciso distinguir a escolha antes de preparar o resumo.", "step": state["step"]}
    state["slots"] = options[:8]
    state["slot_menu_numbered"] = False
    if not options:
        state["step"] = "reschedule_day" if rescheduling else "date"
    await redis.set(key, json.dumps(state), ex=STATE_TTL)
    return {"answer": ("Esse horário não está disponível. " if choice.kind == "unavailable" else "") +
            presentation.render_slots([x["name"] for x in catalog if str(x["id"]) in state["services"]], options[:8],
                _preferences(business), timezone=business["timezone"], total_cents=state["price"],
                duration_min=state["duration"], simulation=preview),
            "step": state["step"]}


def _exact_legacy_choice(body: str, state: dict, intent: str | None) -> bool:
    clean, step = norm(body).strip(" ?!.,"), state.get("step", "")
    if state.get("booking_draft") and _resume_booking_draft(body):
        return False  # A draft must consult availability before any new summary.
    if step in {"time", "reschedule_time"} and (simple_schedule_reply(body, "time") or
            (clean.isdigit() and 12 <= int(clean) <= 23 and int(clean) > len(state.get("slots", [])))):
        return False  # Resolve an explicit clock against the full current backend result.
    if state.get("pending_clock") and step in {"date", "professional", "reschedule_day"}:
        return False
    if step == "reschedule_pick" and state.get("deferred_reschedule") and clean.isdigit():
        return False
    return (intent in {"greeting", "identity", "my_bookings"} or body.strip().lower() in RESET or
        _summary_requested(body) or
        (not state.get("availability_query") and (
            (step in {"confirm", "reschedule_confirm"} and body.strip().lower() in YES | NO) or
            (step in {"menu", "professional", "time", "reschedule_pick", "reschedule_time"} and clean.isdigit()) or
            (step in {"date", "professional", "time", "confirm", "reschedule_day"} and simple_schedule_reply(body, "date")) or
            (step == "professional" and professional_choice(body, state.get("professionals", [])) is not None) or
            (step in {"time", "reschedule_time"} and simple_schedule_reply(body, "time")))))


async def _planned_slots(tenant: UUID, phone: str, plan: UnderstandingPlan, state: dict,
                         business: dict, catalog: list[dict], redis, key: str, preview: bool,
                         factual: str, *, query: bool = False, rescheduling: bool = False, search_next: bool = False):
    """Read the full real slot result; only an exact requested clock may create a hold."""
    capability = "rescheduling" if rescheduling else "appointments"
    if denial := capability_denial(business, capability):
        return denial
    today = datetime.now(ZoneInfo(business["timezone"])).date()
    inquiry = dict(state.get("availability_query", {})) if query else {}
    recent_inquiry = state.get("last_availability", {}) if query and not inquiry else {}
    try:
        recent = 0 <= datetime.now().timestamp() - float(recent_inquiry.get("queried_at", 0)) <= 900
    except (TypeError, ValueError):
        recent = False
    if recent and not (plan.query_service_ids or plan.service_ids):
        inquiry = recent_inquiry
    search_next = query and not plan.requested_date and (search_next or inquiry.get("search_next", False))
    ids = [str(x) for x in (plan.query_service_ids or plan.service_ids)] if query else [str(x) for x in plan.service_ids]
    if not ids and plan.selection_mode in {"remove", "replace"} and plan.declined_service_ids:
        if query:
            result = _bounded_reply(factual, "Retirei os serviços desta consulta. Qual serviço deseja consultar agora? Seu pedido anterior permanece sem alterações.", state.get("step", "menu"))
            if result["step"] != "choose_priority":
                state["availability_query"] = {"services": [], "waiting_for": "service", "search_next": search_next,
                    **({"day": plan.requested_date or inquiry.get("day")} if plan.requested_date or inquiry.get("day") else {})}
                state.pop("last_availability", None)
                await redis.set(key, json.dumps(state), ex=_remaining_state_ttl(state))
            return result
        if rescheduling:
            return _bounded_reply(factual, "A remarcação preserva os serviços originais. Para trocar esses serviços, é preciso atendimento humano.", state.get("step", "reschedule_day"))
        result = _bounded_reply(factual, "Retirei os serviços dessa solicitação. Qual serviço deseja escolher agora? Nenhum agendamento foi confirmado ou cancelado.", "menu")
        if result["step"] != "choose_priority":
            await redis.set(key, json.dumps({"step": "menu", "services": [],
                "customer_name": plan.customer_name or state.get("customer_name")}), ex=STATE_TTL)
        return result
    ids = ids or (inquiry["services"] if "services" in inquiry else state.get("services", []))
    valid = {str(x["id"]) for x in catalog}
    if not ids or any(x not in valid for x in ids):
        result = _bounded_reply(factual, "Qual serviço deseja consultar? Preciso identificar os serviços antes de verificar a agenda.", state.get("step", "menu"))
        if query and result["step"] != "choose_priority":
            state["availability_query"] = {"services": [], "waiting_for": "service", "search_next": search_next,
                **({"kind": inquiry["kind"]} if inquiry.get("kind") else {}),
                **({"day": plan.requested_date} if plan.requested_date else {})}
            await redis.set(key, json.dumps(state), ex=STATE_TTL)
        return result
    if rescheduling and ids != state.get("services"):
        return _bounded_reply(factual, "A remarcação preserva os serviços originais. Para trocar esses serviços, é preciso atendimento humano.", state.get("step", "reschedule_day"))
    if rescheduling and plan.professional_id and str(plan.professional_id) != state.get("professional"):
        return _bounded_reply(factual, "A remarcação preserva o profissional da reserva original. Para trocar o profissional, é preciso atendimento humano.", state.get("step", "reschedule_day"))
    old_day = inquiry.get("day") or state.get("day")
    try:
        old_day = old_day if old_day and date.fromisoformat(old_day) >= today else None
    except ValueError:
        old_day = None
    # Catalog/model ordering does not change the services already quoted.
    changed = set(ids) != set(state.get("services") or [])
    day = plan.requested_date or old_day
    if day and date.fromisoformat(day) < today:
        return _bounded_reply(factual, "Essa data já passou. Qual data futura você prefere?", state.get("step", "date"))
    proposed = {"services": list(ids), "customer_name": plan.customer_name or state.get("customer_name"), "step": "date"}
    if not query and not rescheduling and (replacement := _replacement_hold(state)):
        proposed["replacement_hold"] = replacement
    if state.get("booking_recovery"):
        proposed["booking_recovery"] = True
    if plan.customer_name or state.get("new_customer_name"):
        proposed["new_customer_name"] = plan.customer_name or state["new_customer_name"]
    if rescheduling:
        proposed.update(appointment=state["appointment"], professional=state["professional"])
    clock = plan.requested_time or inquiry.get("pending_clock") or (state.get("pending_clock") if state.get("step") in {"date", "professional", "reschedule_day"} else None)
    relation = plan.time_relation if plan.requested_time else inquiry.get("pending_relation", state.get("pending_relation", "exact"))
    if clock:
        proposed.update(pending_clock=clock, pending_relation=relation)
    if not day and not search_next and not (plan.professional_id or state.get("professional") or inquiry.get("professional")):
        if changed and not query and not rescheduling and not factual:
            factual = _service_quote(ids, catalog, business)
        result = _bounded_reply(factual, "Para qual dia deseja consultar?", state.get("step", "menu") if query else "date")
        if result["step"] != "choose_priority":
            if query:
                state["availability_query"] = {**inquiry, "services": list(ids), "waiting_for": "date"}
                await redis.set(key, json.dumps(state), ex=_remaining_state_ttl(state))
            else:
                await redis.set(key, json.dumps(proposed), ex=STATE_TTL)
        return result
    pros = await professionals_for(tenant, [UUID(x) for x in ids])
    selected_id = str(plan.professional_id) if plan.professional_id else inquiry.get("professional") or state.get("professional")
    selected = next((p for p in pros if str(p["id"]) == selected_id), None)
    if plan.professional_id and selected is None:
        return _bounded_reply(factual, "Esse profissional não está habilitado para todos os serviços pedidos. Qual profissional prefere?", state.get("step", "professional"))
    if not selected and len(pros) == 1:
        selected = pros[0]
    if not pros:
        return _bounded_reply(factual, "Não há profissional habilitado para essa combinação de serviços.", state.get("step", "date"))
    if query and inquiry.get("kind") == "professionals" and day:
        return await _professionals_availability(tenant, state, {**inquiry, "services": ids, "day": day},
            business, catalog, pros, redis, key, factual=factual, preview=preview)
    if day:
        proposed["day"] = day
    if selected:
        proposed.update(professional=str(selected["id"]), professional_name=selected["name"])
    else:
        proposed.update(step="professional", professionals=[{"id": str(x["id"]), "name": x["name"]} for x in pros])
    if (not day and not search_next) or not selected:
        if changed and not query and not rescheduling and not factual:
            factual = _service_quote(ids, catalog, business)
        question = "Para qual dia deseja consultar?" if not day and not search_next else "Qual profissional prefere?\n" + "\n".join(x["name"] for x in pros)
        result = _bounded_reply(factual, question, state.get("step", "menu") if query else proposed["step"])
        if result["step"] != "choose_priority":
            if query:
                state["availability_query"] = {"services": ids, **({"day": day} if day else {}),
                    **({"kind": inquiry["kind"]} if inquiry.get("kind") else {}),
                    **({"professional": str(selected["id"])} if selected else {}), "waiting_for": "date" if not day and not search_next else "professional",
                    "search_next": search_next, **({"pending_clock": clock, "pending_relation": relation} if clock else {})}
                await redis.set(key, json.dumps(state), ex=_remaining_state_ttl(state))
            else:
                if rescheduling:
                    proposed["step"] = "reschedule_day"
                    result["step"] = "reschedule_day"
                await redis.set(key, json.dumps(proposed), ex=STATE_TTL)
        return result
    if query:
        found = []
        found_slots = []
        metadata = []
        try:
            async with asyncio.timeout(12), get_session_factory()() as db:
                for target in ([today + timedelta(days=x) for x in range(7)] if search_next else [date.fromisoformat(day)]):
                    current = await list_slots(db, tenant, UUID(proposed["professional"]), [UUID(x) for x in ids], target)
                    current = _slot_result(current)
                    actual = current["slots"]
                    if clock and relation == "exact":
                        actual = [x for x in actual if datetime.fromisoformat(x).strftime("%H:%M") == clock]
                    if clock and relation in {"after", "before"}:
                        actual = [x for x in actual if (datetime.fromisoformat(x).strftime("%H:%M") > clock if relation == "after" else datetime.fromisoformat(x).strftime("%H:%M") < clock)]
                    if actual:
                        found.append(f"{target:%d/%m}: " + ", ".join(datetime.fromisoformat(x).strftime("%H:%M") for x in actual[:8]))
                        found_slots.extend(actual[:8])
                        price, duration = current.get("price_cents"), current.get("duration_min")
                        metadata.append((price, duration) if type(price) is int and price >= 0 and type(duration) is int and duration > 0 else None)
                    if search_next and len(found) == 3:
                        break
        except AVAILABILITY_ERRORS:
            return _bounded_reply(factual, "Não consegui verificar a agenda real agora. Nenhuma reserva foi alterada.", state.get("step", "menu"))
        shared = metadata[0] if metadata and metadata[0] is not None and all(row == metadata[0] for row in metadata) else None
        result = _bounded_reply(factual,
            presentation.render_slots([x["name"] for x in catalog if str(x["id"]) in ids], found_slots,
                _preferences(business), timezone=business["timezone"], professional=selected["name"], day=day if not search_next else None,
                total_cents=shared[0] if shared else None, duration_min=shared[1] if shared else None, ask_selection=False) +
            "\n\n_Consulta sem reserva; nenhum agendamento foi criado ou alterado._", state.get("step", "menu"))
        if result["step"] != "choose_priority":
            state.pop("availability_query", None)
            state["last_availability"] = {"services": list(ids), "professional": str(selected["id"]),
                "search_next": bool(search_next), "queried_at": datetime.now().timestamp(),
                **({"day": day} if day else {})}
            await redis.set(key, json.dumps(state), ex=_remaining_state_ttl(state))
        return result
    try:
        async with get_session_factory()() as db:
            slots = await list_slots(db, tenant, UUID(proposed["professional"]), [UUID(x) for x in ids], date.fromisoformat(day),
                **(_replacement_slot_kwargs(proposed, proposed["professional"], phone) if not rescheduling else {}))
            slots = _slot_result(slots, pricing=True)
    except AVAILABILITY_ERRORS:
        return _bounded_reply(factual, "Não consegui consultar a agenda agora. Podemos tentar novamente em instantes?", state.get("step", "date"))
    options = slots["slots"]
    if clock and relation in {"after", "before"}:
        options = [x for x in options if (datetime.fromisoformat(x).strftime("%H:%M") > clock if relation == "after" else datetime.fromisoformat(x).strftime("%H:%M") < clock)]
    proposed.update(slots=options[:8], slot_menu_numbered=False, price=slots["price_cents"], duration=slots["duration_min"], step="reschedule_time" if rescheduling else "time")
    exact = next((x for x in options if clock and relation == "exact" and datetime.fromisoformat(x).strftime("%H:%M") == clock), None)
    if exact:
        proposed.pop("pending_clock", None);proposed.pop("pending_relation", None)
        if rescheduling:
            return await prepare_reschedule_summary(tenant, phone, proposed, exact, business, catalog, redis, key, preview, factual)
        return await prepare_booking_summary(tenant, phone, proposed, exact, business, catalog, redis, key, preview, factual)
    question = ("O horário exato pedido não está disponível. " if clock and relation == "exact" else "") + (
        presentation.render_slots([x["name"] for x in catalog if str(x["id"]) in ids], options[:8], _preferences(business),
            timezone=business["timezone"], professional=proposed.get("professional_name"), total_cents=slots["price_cents"], duration_min=slots["duration_min"], simulation=preview)
        if options else presentation.render_prompt("unavailable", _preferences(business)))
    result = _bounded_reply(factual, question, proposed["step"] if options else "reschedule_day" if rescheduling else "date")
    if result["step"] != "choose_priority":
        proposed["step"] = result["step"]
        await redis.set(key, json.dumps(proposed), ex=STATE_TTL)
    return result


async def _multi_turn(tenant: UUID, phone: str, body: str, state: dict, business: dict,
                      catalog: list[dict], professionals: list[dict], redis, key: str, preview: bool):
    if state.get("step") == "reschedule_pick" and state.get("deferred_reschedule") and body.strip().isdigit():
        return await continue_reschedule(tenant, phone, body, state, business, redis, key, preview)
    if state.get("availability_query") and body.strip().lower() in YES | NO:
        await _review_after_inquiry(state, redis, key)
        if body.strip().lower() in NO:
            state.pop("availability_query", None)
            await redis.set(key, json.dumps(state), ex=_remaining_state_ttl(state))
            return {"answer": "Encerrei somente a consulta de disponibilidade. O pedido anterior não foi confirmado, cancelado ou alterado.", "step": state.get("step", "menu")}
        return {"answer": resume_prompt(state), "step": state.get("step", "menu")}
    draft_review = _draft_review_accepted(body, state, catalog, professionals)
    if state.get("booking_draft") and body.strip().lower() in YES and not draft_review:
        pending = state.get("clarification_pending") or [field for field, value in (
            ("service", state.get("services")), ("date", state.get("day")),
            ("professional", state.get("professional")), ("time", state.get("pending_clock"))) if not value]
        if pending:
            return {"answer": clarification_for(UnderstandingPlan(intents=["booking"], ambiguities=pending)), "step": state["step"]}
        return {"answer": _draft_description(state, catalog), "step": state["step"]}
    context = await _time_context(tenant, body, state, business, catalog, professionals)
    clock_choice = state.get("step") in {"time", "reschedule_time"} and (simple_schedule_reply(body, "time") or
        (body.strip().isdigit() and 12 <= int(body.strip()) <= 23 and int(body.strip()) > len(state.get("slots", []))))
    local_choice = simple_schedule_reply(body, "date") or professional_choice(body, professionals) is not None or clock_choice
    priority = norm(body).strip(" ?!.,")
    deferred = state.get("deferred_plan")
    try:
        if state.get("booking_draft") and (_resume_booking_draft(body) or draft_review):
            plan = UnderstandingPlan(intents=["booking"])
        elif deferred and priority in {"agendamento", "agendamento primeiro", "agenda", "agenda primeiro", "preparar agendamento", "preco", "precos", "valores", "precos primeiro"}:
            plan = UnderstandingPlan.model_validate(deferred)
            if priority in {"preco", "precos", "valores", "precos primeiro"}:
                plan = plan.model_copy(update={"intents": ["price"], "service_ids": [], "faq_ids": []})
            else:
                plan = plan.model_copy(update={"intents": ["booking"], "query_service_ids": [], "faq_ids": []})
        elif local_choice and (state.get("availability_query") or state.get("pending_clock") or clock_choice):
            plan = build_plan(message=body, catalog=catalog, professionals=professionals, context=context,
                              reference_date=datetime.now(ZoneInfo(business["timezone"])).date())
            plan = plan.model_copy(update={"intents": ["availability" if state.get("availability_query") else
                "reschedule" if state.get("step", "").startswith("reschedule_") else "booking"]})
        else:
            plan = await interpret_plan(message=body, catalog=catalog, professionals=professionals,
            faqs=faq_catalog(business), history=state.get("history", []), context=context,
            reference_date=datetime.now(ZoneInfo(business["timezone"])).date(), key=os.getenv("GROQ_API_KEY", ""),
            model=os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"))
        plan = _merge_booking_draft(plan, state, catalog, professionals)
    except (GroqServiceError, ValueError):
        await _review_after_inquiry(state, redis, key)
        return {"answer": "Não consegui interpretar o pedido completo com segurança. Pode esclarecer o serviço, o dia e o horário finais? Nenhuma alteração foi confirmada.",
                "step": state.get("step", "clarify"), "degraded": True}
    if "human" in plan.intents:
        await redis.set(key, json.dumps({"step": "human"}), ex=1800)
        return {"answer": "Vou deixar esta conversa para atendimento humano. Para retomar o bot, envie MENU.", "step": "human"}
    operations = set(plan.intents)
    if "availability" in operations and _asks_available_professionals(body):
        state.setdefault("availability_query", {})["kind"] = "professionals"
    continuing_query = bool(state.get("availability_query")) and not bool(operations & {"price", "faq", "catalog"}) and not re.search(r"\b(?:agendar|marcar|reservar)\b", norm(body))
    if continuing_query:
        operations.discard("booking");operations.discard("confirm");operations.add("availability")
    changes = bool(plan.service_ids or plan.requested_date or plan.requested_time or plan.professional_id)
    if not ("confirm" in operations and not changes):
        await _review_after_inquiry(state, redis, key)
    for selected, capability in ((bool(operations & {"price", "catalog"}), "catalog_answers"),
            (bool(operations & {"booking", "availability"}), "appointments"),
            ("reschedule" in operations, "rescheduling")):
        if selected and (denial := capability_denial(business, capability)):
            return denial
    information = compose_information(plan, business, catalog, message=body)
    factual = information.answer or ""
    if not presentation.fits_message(factual):
        await _review_after_inquiry(state, redis, key)
        if "booking" in operations:
            state["deferred_plan"] = plan.model_dump(mode="json")
            await redis.set(key, json.dumps(state), ex=_remaining_state_ttl(state))
        return _bounded_reply(factual, "", "information")
    if "service" in information.unresolved:
        return _bounded_reply(factual, "", state.get("step", "clarify"))
    if (state.get("availability_query") and plan.requested_time and not (set(plan.ambiguities) - {"professional"})
            and not operations - {"price", "availability", "other"}):
        inquiry = state["availability_query"]
        inquiry.update(pending_clock=plan.requested_time, pending_relation=plan.time_relation)
        if plan.requested_date:
            inquiry["day"] = plan.requested_date
            inquiry.pop("search_next", None)
        if re.match(r"^(?:pera|espera|melhor|na verdade)\b", norm(body)) and (plan.query_service_ids or plan.service_ids):
            inquiry["services"] = [str(value) for value in plan.query_service_ids or plan.service_ids]
        await redis.set(key, json.dumps(state), ex=_remaining_state_ttl(state))
        result = await answer_availability(tenant, "", state, business, catalog, redis, key)
        return _bounded_reply(factual, result["answer"], result["step"])
    if "price" in operations:
        state.pop("service_quote_option", None)
        previous = _recent_availability(state, catalog)
        if plan.selection_mode == "simulate" and plan.query_service_ids and previous:
            state["service_quote_option"] = {"services": [str(value) for value in plan.query_service_ids],
                "day": previous["day"], "revision": qr_profile.catalog_revision(business, catalog, professionals),
                "expires_at": datetime.now().timestamp() + 300}
        if state:
            await redis.set(key, json.dumps(state), ex=_remaining_state_ttl(state))
    if "price" in operations and not operations & {"cancel", "reschedule", "human"}:
        prefix = _price_correction_prefix(body, state)
        if prefix and not (set(plan.ambiguities) - {"time"}):
            if denial := capability_denial(business, "appointments"):
                return denial
            source = _price_correction_source(state, business, catalog, professionals)
            amended = build_plan(message=prefix, catalog=catalog, professionals=professionals, context=context,
                reference_date=datetime.now(ZoneInfo(business["timezone"])).date(), proposal=UnderstandingPlan(intents=["booking"]))
            amended = _merge_booking_draft(amended, source, catalog, professionals)
            draft = await _save_booking_draft(amended, source, business, catalog, professionals, redis, key, allow_complete=True)
            if draft:
                if draft["clarification_pending"]:
                    return _bounded_reply(factual, _draft_description(draft, catalog, ask_review=False) + "\n\n" + _plan_clarification(amended, context, business), draft["step"])
                if (not draft["clarification_pending"] and draft.get("services") and draft.get("day")
                        and draft.get("professional") and draft.get("pending_clock") and draft.get("pending_relation") == "exact"
                        and _message_identity.get()):
                    draft["draft_review_message_id"] = _message_identity.get()
                    await redis.set(key, json.dumps(draft), ex=max(1, min(300, int(_draft_remaining(draft)))))
                return _bounded_reply(factual, _draft_description(draft, catalog), draft["step"])
    multiple_availability = await _multiple_professional_availability(tenant, body, plan, state, business,
        catalog, professionals, redis, key, preview)
    if multiple_availability is not None:
        return multiple_availability
    if plan.requires_clarification:
        await _review_after_inquiry(state, redis, key)
        draft = await _save_booking_draft(plan, state, business, catalog, professionals, redis, key)
        if draft:
            reply = _bounded_reply(factual, _plan_clarification(plan, context, business), draft["step"])
            if draft["step"] == "menu":
                reply["_service_reply_prompt"] = True
            return reply
        return _bounded_reply(factual, _plan_clarification(plan, context, business), state.get("step", "clarify"))
    if "confirm" in operations and not changes:
        if state.get("step") not in {"confirm", "reschedule_confirm"}:
            return _bounded_reply(factual, "Ainda não há um resumo válido para confirmar. Qual pedido deseja preparar?", "no_summary")
        try:
            remaining = float(state.get("confirmation_expires_at", 0)) - datetime.now().timestamp()
        except (TypeError, ValueError):
            remaining = 0
        if not math.isfinite(remaining) or remaining <= 0:
            return _bounded_reply(factual, "O resumo anterior expirou. Precisamos rever a disponibilidade antes de confirmar.", "expired")
        if presentation.message_units(factual) + 350 > 1200:
            return _bounded_reply(factual, "x" * 1201, "confirm")
        result = await _turn(tenant, phone, "SIM", redis, key.removeprefix(f"orion:qr:ai:{tenant}:"), preview)
        composed = _bounded_reply(factual, result["answer"] or "", result["step"])
        if result.get("_verified_operation") is True:
            composed["_verified_operation"] = True
        return composed
    if "cancel" in operations and "reschedule" not in operations:
        active_request = state.get("step") in {"date", "professional", "time", "confirm"} or state.get("step", "").startswith("reschedule_")
        if active_request and not re.search(r"\b(?:reserva|agendamento|confirmad\w*)\b", norm(body)):
            result = _bounded_reply(factual, "Encerrei somente esta solicitação. Reservas já confirmadas permanecem.", "cancelled")
            if result["step"] != "choose_priority":
                await redis.delete(key)
            return result
        cancellation = await qr_cancellation.handle(tenant, phone, body, business=business, state=state,
            redis=redis, key=key, message_id=_message_identity.get(), preview=preview, requested=True)
        result = _bounded_reply(factual, cancellation["answer"] or "", cancellation["step"])
        if result["step"] == "choose_priority":
            await _require_pending_review(redis, key)
        return result
    if "reschedule" in operations:
        if not state.get("step", "").startswith("reschedule_"):
            if presentation.message_units(factual) + 500 > 1200:
                return _bounded_reply(factual, "x" * 1201, "reschedule")
            result = await begin_reschedule(tenant, phone, business, redis, key, preview)
            state = json.loads(await redis.get(key) or "{}")
            if state.get("step") == "reschedule_pick" and (plan.requested_date or plan.requested_time):
                state["deferred_reschedule"] = plan.model_copy(update={"intents": ["reschedule"], "faq_ids": [], "query_service_ids": []}).model_dump(mode="json")
                await redis.set(key, json.dumps(state), ex=STATE_TTL)
            if not state.get("appointment") or not plan.requested_date:
                return _bounded_reply(factual, result["answer"], result["step"])
        return await _planned_slots(tenant, phone, plan, state, business, catalog, redis, key, preview, factual, rescheduling=True)
    if "booking" in operations:
        result = await _planned_slots(tenant, phone, plan, state, business, catalog, redis, key, preview, factual)
        if result["step"] == "choose_priority":
            state["deferred_plan"] = plan.model_dump(mode="json")
            await redis.set(key, json.dumps(state), ex=_remaining_state_ttl(state))
        return result
    if "availability" in operations or continuing_query:
        await _review_after_inquiry(state, redis, key)
        return await _planned_slots(tenant, phone, plan, state, business, catalog, redis, key, preview, factual, query=True,
            search_next=bool(re.search(r"\b(?:que|qual|quais|proximos?)(?: sao)?(?: os?)?\s+dias?\b|\bquando\b|\btem\s+(?:algum\s+)?livre\b", norm(body))))
    await _review_after_inquiry(state, redis, key)
    reply = _bounded_reply(factual, resume_prompt(state) if state.get("confirmation_requires_review") or state.get("availability_query") else "" if factual else
        "Posso ajudar com informações ou preparar um pedido. O que você gostaria de consultar?", state.get("step", "information"))
    if factual and reply["step"] == "choose_priority":
        reply = {"answer": factual, "step": state.get("step", "information")}
    if plan.customer_name and reply["step"] != "choose_priority":
        if not preview:
            await qr_intake_revision.check_before_write()
            await remember_name(tenant, phone, plan.customer_name)
        state["customer_name"] = plan.customer_name
        await redis.set(key, json.dumps(state), ex=_remaining_state_ttl(state))
    return reply


async def turn(tenant: UUID, phone: str, body: str, redis, namespace: str, preview: bool = False):
    """Keep only recent exchanges in this tenant/session; never resurrect completed holds."""
    key = f"orion:qr:ai:{tenant}:{namespace}"
    try:
        before = json.loads(await redis.get(key) or "{}")
    except (TypeError, ValueError):
        before = {}
    history = before.get("history", []) if isinstance(before, dict) else []
    if body.strip().lower() in RESET:
        history = []
    token = _reply_preferences.set(None)
    facts_token = _reply_facts.set(None)
    identity_token = _message_identity.set(_message_identity.get() or ("preview-" + str(uuid4()) if preview else None))
    try:
        result = await _turn(tenant, phone, body, redis, namespace, preview)
        literal = result.pop("_literal_information", False)
        service_question = result.pop("_service_reply_prompt", False) is True
        if result.get("answer"):
            result = {**result, "answer": presentation.render_message(result["answer"], _reply_preferences.get(), kind="message" if literal else result.get("step", "message"))}
            if not presentation.fits_message(result["answer"]):
                result = _bounded_reply(result["answer"], "", result.get("step", "information"))
        if qr_commerce.enabled() and _message_identity.get() and not result.get("step", "").startswith("commerce"):
            commerce_key = key + ":commerce"
            try:
                private = json.loads(await redis.get(commerce_key) or "null")
            except (TypeError, ValueError):
                private = None
            if isinstance(private, dict) and private.get("pending"):
                private["last_message_id"] = _message_identity.get()
                await redis.set(commerce_key, json.dumps(private), ex=STATE_TTL)
    finally:
        _reply_preferences.reset(token)
        _reply_facts.reset(facts_token)
        _message_identity.reset(identity_token)
    if result.get("answer"):
        try:
            current = json.loads(await redis.get(key) or "null")
        except (TypeError, ValueError):
            current = None
        if isinstance(current, dict):
            if current.get("step") == "menu" and service_question:
                current["service_reply_expected"] = True
            else:
                current.pop("service_reply_expected", None)
            history = [item for item in history if isinstance(item, dict) and
                       item.get("role") in {"user", "assistant"} and isinstance(item.get("content"), str)]
            current["history"] = (history + [{"role": "user", "content": body[:1500]},
                {"role": "assistant", "content": result["answer"][:1500]}])[-8:]
            if current.get("booking_draft"):
                ttl = max(1, min(300, int(_draft_remaining(current))))
            elif current.get("step") in REVIEW_STEPS:
                ttl = _remaining_state_ttl(current)
            else:
                ttl = 1800 if current.get("step") == "human" else STATE_TTL
            await redis.set(key, json.dumps(current), ex=ttl)
    return result


async def _turn(tenant: UUID, phone: str, body: str, redis, namespace: str, preview: bool = False, *, _confirm_scoped: bool = False):
    state_key = f"orion:qr:ai:{tenant}:{namespace}"
    text_clean = body.strip().lower()
    if text_clean in RESET:
        await redis.delete(state_key)
        await redis.delete(state_key + ":commerce")
        state = {}
    else:
        try:
            state = json.loads(await redis.get(state_key) or "{}")
        except (TypeError, ValueError):
            state = {}
    if not isinstance(state, dict):
        state = {}
    if state.get("booking_draft") and _draft_remaining(state) <= 0:
        await redis.delete(state_key)
        state = {}
    business, catalog, professionals = await tenant_data(tenant)
    _reply_preferences.set(_preferences(business))
    _reply_facts.set((business, catalog))
    profile = business.get("profile")
    context_key = state_key + ":catalog-context"
    revision = qr_profile.catalog_revision(business, catalog, professionals)
    old_revision = await redis.get(context_key)
    if isinstance(old_revision, bytes):
        old_revision = old_revision.decode()
    active_choice = state.get("step") in {"date", "professional", "time", "confirm", "cancel_pick", "cancel_confirm"} or state.get("step", "").startswith("reschedule_") or bool(state.get("booking_draft"))
    context_changed = ((old_revision is not None and old_revision != revision)
                       or (old_revision is None and active_choice))
    await redis.set(context_key, revision, ex=STATE_TTL)
    if context_changed and state.get("step") != "human":
        service_question = state.get("step") == "menu" and state.get("service_reply_expected") is True
        state = {"customer_name": state.get("customer_name")} if state.get("customer_name") else {}
        if service_question:
            state.update(step="menu", service_reply_expected=True, menu_ids=[str(row["id"]) for row in catalog[:12]])
        await redis.delete(state_key)
        await redis.delete(state_key + ":commerce")
        if text_clean in YES or norm(body) in {"retomar resumo", "ver resumo", "rever resumo"}:
            return {"answer": "As informações do atendimento mudaram. Vou precisar revisar o pedido antes de confirmar. Nenhum agendamento foi confirmado ou alterado.",
                    "step": "context_changed"}
    intent = control_intent(body)
    if state.get("step") == "human":
        if _assistant_resume_requested(body):
            await redis.delete(state_key)
            await redis.delete(state_key + ":commerce")
            return {"answer": "Voltei ao atendimento automático. Como posso ajudar agora?", "step": "menu"}
        return {"answer": None, "step": "human"}
    if intent == "human":
        await redis.set(state_key, json.dumps({"step": "human"}), ex=1800)
        return {"answer": "Vou deixar esta conversa para atendimento humano. Para retomar o bot, envie MENU.", "step": "human"}
    if intent in {"human_declined", "human_clarify"}:
        await _review_after_inquiry(state, redis, state_key)
        return {"answer": ("Tudo bem, continuo por aqui. Como posso ajudar?" if intent == "human_declined" else
            "Você quer continuar comigo ou falar com uma pessoa da equipe?"), "step": state.get("step", "clarify")}
    amended_day = await _day_restriction_turn(tenant, phone, body, state, business, catalog, redis, state_key, preview)
    if amended_day is not None:
        return amended_day
    indifferent = await _indifferent_professional_turn(tenant, phone, body, state, business, catalog, redis, state_key, preview)
    if indifferent is not None:
        return indifferent
    if state.get("booking_recovery") and re.fullmatch(r"(?:qual|quais|tem|ha|mostra|me mostra)(?:\s+os?)?(?:\s+outros?)?\s+horarios?(?:\s+(?:livres?|disponiveis?))?[?!.\s]*", norm(body)):
        return await _recover_booking(tenant, phone, state, business, catalog, redis, state_key,
            reason="Vou consultar novamente as alternativas para o mesmo serviço, profissional e dia.")
    if state.get("service_quote_option") and not catalog_question(body) and re.search(
            r"\b(?:nao|nem|sem|menos|so|apenas|somente|retira\w*|tira\w*|remove\w*|troca\w*|muda\w*|melhor|em vez|ao inves)\b", norm(body)):
        state.pop("service_quote_option", None)
        await redis.set(state_key, json.dumps(state), ex=_remaining_state_ttl(state))
    if state.get("step") in qr_cancellation.STEPS and (availability_question(body) or state.get("availability_query")):
        # A new inquiry is the active task. Retain its factual question and
        # conversational history, never a dormant cancellation authorization.
        state = {field: state[field] for field in ("history", "customer_name", "availability_query") if field in state}
        state["step"] = "menu"
        await redis.set(state_key, json.dumps(state), ex=STATE_TTL)
    declined = contextual_response(body, awaiting_confirmation=True).kind == "decline"
    abandoning = bool(re.fullmatch(r"(?:cancela|cancelar|cancele|desmarca|desmarcar|desisto|desistir|deixa (?:pra|para) la)[.!\s]*", norm(body)))
    if not state.get("availability_query") and state.get("step") in {"date", "professional", "time", "confirm", "reschedule_day", "reschedule_time", "reschedule_confirm"} and (declined or abandoning):
        await redis.delete(state_key)
        return {"answer": "Tudo bem, encerrei esse pedido. Quando precisar de alguma coisa, é só me chamar.", "step": "request_closed"}
    if state.get("step") in {None, "menu"} and not state.get("availability_query") and declined:
        await redis.delete(state_key)
        return {"answer": "Tudo bem! Quando precisar, é só me chamar.", "step": "courtesy"}
    if intent == "greeting" and state.get("step") in qr_cancellation.STEPS:
        # A greeting opens a new conversation, not a standing cancellation.
        state = {field: state[field] for field in ("history", "customer_name") if field in state}
        state.update(step="menu", menu_ids=[str(row["id"]) for row in catalog[:12]])
        await redis.set(state_key, json.dumps(state), ex=STATE_TTL)
        return {"answer": catalog_menu(business, catalog, "Olá! "), "step": "menu", "_service_reply_prompt": True}
    if norm(body).strip(" ?!.,") in {"que dia e hoje", "qual dia e hoje", "qual e a data de hoje", "qual a data de hoje"}:
        await _review_after_inquiry(state, redis, state_key)
        today = datetime.now(ZoneInfo(business["timezone"]))
        weekdays = ("segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo")
        return {"answer": f"Hoje é {weekdays[today.weekday()]}, {today:%d/%m/%Y}.", "step": state.get("step", "information"), "_literal_information": True}
    if intent == "greeting" and (state.get("step") not in {None, "menu"} or state.get("availability_query")):
        display = state.get("customer_name")
        greeting = f"Olá, {presentation.escape_label(display)}!" if display else "Olá!"
        selected = [item["name"] for item in catalog if str(item["id"]) in state.get("services", [])]
        context = " Seu pedido atual é " + " e ".join(presentation.escape_label(name) for name in selected) + "." if selected else ""
        return {"answer": greeting + context + " Posso ajudar com valores, consultar dias livres ou continuar seu pedido.",
                "step": state["step"]}
    if state.get("reschedule_conflict_review"):
        retry_query = await _retry_reschedule_query(tenant, body, state, business, redis, state_key, preview)
        if retry_query is not None:
            return retry_query
    information_request = (catalog_question(body) or availability_question(body) or intent == "identity" or
        bool(profile and qr_profile.factual_answer(profile, body)))
    if state.get("step") in qr_cancellation.STEPS:
        if information_request and not _summary_requested(body):
            await _review_after_inquiry(state, redis, state_key)
        else:
            return await qr_cancellation.handle(tenant, phone, body, business=business, state=state,
                redis=redis, key=state_key, message_id=_message_identity.get(), preview=preview)
    if state.get("availability_query") and not information_request and not _summary_requested(body):
        if text_clean in YES | NO:
            await _review_after_inquiry(state, redis, state_key)
            if text_clean in NO:
                state.pop("availability_query", None)
                await redis.set(state_key, json.dumps(state), ex=_remaining_state_ttl(state))
                return {"answer": "Tudo bem, encerrei essa consulta. Quando precisar, é só me chamar.", "step": state.get("step", "menu")}
            return {"answer": resume_prompt(state), "step": state.get("step", "menu")}
        field = state["availability_query"].get("waiting_for")
        resolved = resolve_service_reply(body, catalog, awaiting_service=field == "service")
        valid_reply = (resolved.kind in {"resolved", "ambiguous"} or
            field == "date" and simple_schedule_reply(body, "date") or
            field == "professional" and professional_choice(body, professionals) is not None)
        if valid_reply:
            return await answer_availability(tenant, body, state, business, catalog, redis, state_key)
    if state.get("step") == "reschedule_pick" and not information_request and intent in {None, "reschedule"}:
        details = state.get("candidate_details", [])
        kind, _ = qr_cancellation.choose_candidate(body, details, business["timezone"])
        if kind != "none" or body.strip().isdigit():
            return await continue_reschedule(tenant, phone, body, state, business, redis, state_key, preview)
    service_selection = await _contextual_service_selection(tenant, phone, body, state, business, catalog, professionals, redis, state_key, preview)
    if service_selection is not None:
        return service_selection
    clock_selection = await _continue_recent_availability(tenant, phone, body, state, business, catalog, professionals, redis, state_key, preview)
    if clock_selection is not None:
        return clock_selection
    if not _confirm_scoped:
        if not _price_correction_prefix(body, state):
            compound = await _compound_booking_turn(tenant, phone, body, state, business, catalog, redis, state_key, preview)
            if compound is not None:
                return compound
        commerce = await _commerce_turn(tenant, phone, body, state, business, catalog, redis, state_key, preview)
        if commerce is not None:
            return commerce
    if state.get("step") in {"confirm", "reschedule_confirm"} and not state.get("availability_query"):
        consent = contextual_response(body, awaiting_confirmation=True)
        another_subject = (_summary_requested(body) or catalog_question(body) or availability_question(body)
            or catalog_match(body, catalog) or is_service_request(body) or intent is not None)
        if consent.reason == "unresolved" and not another_subject and os.getenv("QR_AI_MULTI_INTENT_ENABLED", "0") != "1":
            consent = await interpret_contextual_response(message=body, awaiting_confirmation=True,
                key=os.getenv("GROQ_API_KEY", ""), model=os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"))
            if consent.kind == "uncertain":
                await _review_after_inquiry(state, redis, state_key)
                return {"answer": presentation.render_prompt("interpretation_error", _preferences(business)) +
                    "\n\n" + presentation.render_prompt("confirmation_review", _preferences(business)), "step": state["step"]}
        if consent.kind == "affirm":
            if denial := capability_denial(business, "rescheduling" if state.get("step") == "reschedule_confirm" else "appointments"):
                return denial
            incoming_id, summary_id = _message_identity.get(), state.get("confirmation_message_id")
            try:
                remaining = float(state.get("confirmation_expires_at", 0)) - datetime.now().timestamp()
            except (TypeError, ValueError):
                remaining = 0
            if (not math.isfinite(remaining) or remaining <= 0 or
                    not isinstance(state.get("confirmation_summary"), str) or not state["confirmation_summary"].strip()):
                if state.get("step") == "confirm" and not preview:
                    return await _recover_booking(tenant, phone, state, business, catalog, redis, state_key,
                        reason="O prazo do resumo anterior terminou. Mantive seu pedido e vou conferir horários reais antes de qualquer nova confirmação.")
                return {"answer": "O resumo anterior expirou ou precisa ser apresentado novamente. Vamos rever a disponibilidade antes de confirmar?", "step": "expired"}
            if not incoming_id or not summary_id or incoming_id == summary_id:
                await _review_after_inquiry(state, redis, state_key)
                return {"answer": "Preciso apresentar o resumo vigente antes de aceitar uma nova confirmação. Você quer conferir o resumo?", "step": state["step"]}
            body, text_clean = "SIM", "sim"
    if state.get("step") in {"time", "reschedule_time"} and not state.get("availability_query"):
        time_context = await _time_context(tenant, body, state, business, catalog, professionals)
        reference = choose_time(body, state.get("slots", []), presented_slots=state.get("slots", []), timezone=business["timezone"],
            business_hours=time_context.get("hours"), numbered_menu=state.get("slot_menu_numbered", True))
        legacy_selected = reference.kind == "selected" and (text_clean.isdigit() or simple_schedule_reply(body, "time"))
        if reference.kind != "uncertain" and (not legacy_selected or os.getenv("QR_AI_MULTI_INTENT_ENABLED", "0") == "1"):
            return await _natural_time_turn(tenant, phone, body, state, business, catalog, redis, state_key, preview)
    if os.getenv("QR_AI_MULTI_INTENT_ENABLED", "0") == "1" and not _exact_legacy_choice(body, state, intent):
        return await _multi_turn(tenant, phone, body, state, business, catalog, professionals, redis, state_key, preview)
    if intent == "identity":
        return {"answer": qr_profile.factual_answer(profile or {}, body), "step": state.get("step", "information"), "_literal_information": True}
    if profile:
        known = qr_profile.factual_answer(profile, body)
        if known:
            if state.get("step") in REVIEW_STEPS:
                state["confirmation_requires_review"] = True
                await redis.set(state_key, json.dumps(state), ex=_remaining_state_ttl(state))
            return {"answer": known, "step": state.get("step", "information"), "_literal_information": True}
        if business.get("profile_id"):
            capabilities = set(profile.get("capabilities", []))
            wanted = "rescheduling" if intent == "reschedule" or state.get("step", "").startswith("reschedule_") else "appointments"
            information = catalog_question(body) or availability_question(body)
            if "catalog_answers" not in capabilities and catalog_question(body):
                return {"answer": "Este atendimento não está habilitado para informar valores automaticamente. Posso deixar sua dúvida para atendimento humano.", "step": "capability_unavailable"}
            if (wanted not in capabilities and not information and (is_service_request(body) or text_clean in YES or state.get("step") in {"date", "professional", "time", "confirm"})):
                return {"answer": "Este atendimento não está habilitado para concluir agendamentos. Posso deixar sua solicitação para atendimento humano.", "step": "capability_unavailable"}
    if (state.get("confirmation_requires_review") or _summary_requested(body)) and state.get("step") in {"confirm", "reschedule_confirm"}:
        # Agreement here accepts the offer to REVIEW, never the operation. The
        # actual confirmation still needs a subsequent trusted message ID.
        resume = _summary_requested(body) or text_clean in YES
        if resume:
            try:
                deadline = float(state.get("confirmation_expires_at", 0))
            except (TypeError, ValueError):
                deadline = 0
            remaining = deadline - datetime.now().timestamp()
            summary = state.get("confirmation_summary")
            if not math.isfinite(remaining) or remaining <= 0 or not isinstance(summary, str) or not summary.strip():
                if state.get("step") == "confirm" and not preview:
                    return await _recover_booking(tenant, phone, state, business, catalog, redis, state_key,
                        reason="O prazo do resumo terminou. Mantive seu pedido e vou conferir novas opções reais.")
                await redis.delete(state_key)
                return {"answer": "O prazo do resumo terminou. Vamos conferir o serviço, o dia e a disponibilidade novamente?", "step": "expired"}
            state.pop("availability_query", None)
            state.pop("confirmation_requires_review", None)
            state["confirmation_message_id"] = _message_identity.get()
            await redis.set(state_key, json.dumps(state), ex=_remaining_state_ttl(state))
            return {"answer": presentation.render_summary_review(state.get("confirmation_presentation") or summary, _preferences(business)), "step": state["step"]}
        if text_clean in YES and not state.get("availability_query"):
            return {"answer": resume_prompt(state), "step": state["step"]}
    if state.get("availability_query") and text_clean in NO:
        state.pop("availability_query", None)
        await redis.set(state_key, json.dumps(state), ex=_remaining_state_ttl(state))
        return {"answer": "Encerrei somente a consulta de disponibilidade. O pedido anterior não foi confirmado, cancelado ou alterado.", "step": state.get("step", "menu")}
    cancel = not catalog_question(body) and bool(re.search(
        r"\b(?:cancelar|cancele|desistir|desisto|nao quero mais agendar)\b", norm(body)))
    if cancel:
        active_request = state.get("step") in {"date", "professional", "time", "confirm"} or state.get("step", "").startswith("reschedule_")
        if (active_request or re.search(r"\b(?:solicitacao|pedido|consulta)\b", norm(body))) and not re.search(r"\b(?:reserva|agendamento|confirmad\w*)\b", norm(body)):
            await redis.delete(state_key)
            return {"answer": "Tudo bem, encerrei somente esta solicitação. Reservas já confirmadas permanecem.", "step": "cancelled"}
        return await qr_cancellation.handle(tenant, phone, body, business=business, state=state,
            redis=redis, key=state_key, message_id=_message_identity.get(), preview=preview, requested=True)
    if not catalog:
        return {"answer": "Ainda não há serviços ativos cadastrados neste estabelecimento.", "step": "no_catalog"}
    if re.fullmatch(r"(?:quero|queria|pode ser|vou querer)(?: fazer)?\s+tudo[.!?\s]*", norm(body)):
        return {"answer": "Quando você diz tudo, quais serviços quer incluir? Pode dizer os nomes:\n" + "\n".join(
            f"• {item['name']}" for item in catalog[:12]), "step": state.get("step", "menu")}
    supplied = supplied_name(body)
    if supplied:
        if not preview:
            await qr_intake_revision.check_before_write()
            await remember_name(tenant, phone, supplied)
        state["customer_name"] = supplied
        await redis.set(state_key, json.dumps(state), ex=STATE_TTL)
        if not is_service_request(body):
            return {"answer": f"Prazer, {supplied}! Posso ajudar com serviços ou agendamentos?", "step": "named"}
    if intent == "hostile":
        return {"answer": "Posso ajudar com horários, valores ou um atendente humano. Como prefere continuar?", "step": "courtesy"}
    if intent == "cancel_request":
        return await qr_cancellation.handle(tenant, phone, body, business=business, state=state,
            redis=redis, key=state_key, message_id=_message_identity.get(), preview=preview, requested=True)
    if intent == "my_bookings":
        if preview:
            return {"answer": "O histórico de clientes reais não é exibido na simulação.", "step": "preview_history"}
        records = await customer_appointments(tenant, phone)
        if not records:
            return {"answer": "Não encontrei reservas vinculadas a este número neste estabelecimento.", "step": "no_history"}
        return {"answer": presentation.render_booking_history(records, _preferences(business), timezone=business["timezone"]), "step": "history"}
    if intent == "reschedule":
        if denial := capability_denial(business, "rescheduling"):
            return denial
        return await begin_reschedule(tenant, phone, business, redis, state_key, preview)
    querying = availability_question(body)
    inquiry_reply = state.get("availability_query") and not (
        catalog_question(body) or
        re.search(r"\b(?:agendar|marcar|reservar)\b", norm(body)))
    if querying or inquiry_reply:
        if denial := capability_denial(business, "appointments"):
            return denial
        return await answer_availability(tenant, body, state, business, catalog, redis, state_key)
    step = state.get("step", "")
    if step in {"date", "professional", "time", "confirm"} and state.get("services") and simple_schedule_reply(body, "date"):
        valid = {str(item["id"]) for item in catalog}
        chosen_ids = [UUID(item) for item in state["services"] if item in valid]
        if chosen_ids:
            return await start_booking(tenant, body, Intention(intent="agendar", service_ids=chosen_ids,
                requested_when=body), state, business, catalog, redis, state_key, preview)
    question = catalog_question(body)
    numeric = text_clean.isdigit()
    direct = (
        intent == "greeting" or
        (step in {"menu", "time", "reschedule_pick", "reschedule_time"} and numeric) or
        (step in {"confirm", "reschedule_confirm"} and text_clean in YES | NO) or
        (step in {"date", "reschedule_day"} and simple_schedule_reply(body, "date")) or
        (step == "professional" and professional_choice(body, state.get("professionals", [])) is not None) or
        (step in {"time", "reschedule_time"} and simple_schedule_reply(body, "time"))
    )
    decision = None
    degraded = False
    if question or not direct:
        try:
            decision = await interpret(
                message=body, history=state.get("history", []), catalog=catalog,
                context=conversation_context(state, catalog),
                key=os.getenv("GROQ_API_KEY", ""),
                model=os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"),
            )
        except GroqServiceError as exc:
            import logging
            logging.getLogger("orion.groq").warning("classification_failed status=%s", exc.status)
            degraded = True
            matched = catalog_match(body, catalog)
            if not question and re.search(r"\b(?:nao|nem|sem|menos)\b", norm(body)):
                matched = []  # A negated service is never a safe fallback selection.
            decision = Intention(intent="perguntar" if question else "agendar" if is_service_request(body) else "outro",
                                 service_ids=matched, requested_when=body[:120])
        # Validate again at the state-machine boundary, including alternative classifiers.
        valid_ids = {str(item["id"]) for item in catalog}
        decision = decision.model_copy(update={"service_ids": [item for item in decision.service_ids if str(item) in valid_ids]})
        if decision.intent == "humano":
            await redis.set(state_key, json.dumps({"step": "human"}), ex=1800)
            return {"answer": "Certo! Vou deixar esta conversa para atendimento humano. Para retomar o bot, envie MENU.", "step": "human"}
        if question or decision.intent == "perguntar":
            if denial := capability_denial(business, "catalog_answers"):
                return denial
            if state.get("step") in REVIEW_STEPS:
                state["confirmation_requires_review"] = True
                await redis.set(state_key, json.dumps(state), ex=_remaining_state_ttl(state))
            ids = {str(item) for item in decision.service_ids}
            local_ids = catalog_question_services(body, catalog) if question else None
            if local_ids is not None:
                ids = {str(item) for item in local_ids}
            if degraded and question:
                # Exact-name fallback may match only "Barba" in "cabelo e barba".
                # Show trusted individual prices rather than a misleading partial total.
                ids = {str(item) for item in local_ids} if local_ids else set()
            if local_ids is None and not ids and asks_current_price(body):
                ids = set(state.get("services", []))
            chosen = [item for item in catalog if str(item["id"]) in ids]
            if chosen:
                lines = "\n".join(f"• {item['name']}: {format_money(item['price_cents'])} ({item['duration_min']} min)" for item in chosen)
                total = sum(item["price_cents"] for item in chosen)
                duration = sum(item["duration_min"] for item in chosen)
                answer = f"{lines}\nTotal desses serviços: {format_money(total)}, cerca de {duration} minutos."
            else:
                answer = ("Para calcular o total certo, confirme quais serviços deseja consultar. " if local_ids == [] else "") + \
                    "Estes são os serviços e valores cadastrados:\n" + "\n".join(
                    f"• {item['name']}: {format_money(item['price_cents'])} ({item['duration_min']} min)" for item in catalog[:12])
            if profile and (chosen or local_ids != []):
                answer = qr_profile.quote(chosen or catalog[:12], profile, body)
            if not state:
                state = {"step": "menu", "menu_ids": [str(item["id"]) for item in catalog[:12]]}
                await redis.set(state_key, json.dumps(state), ex=STATE_TTL)
            return {"answer": answer + ("" if profile else "\n\n" + resume_prompt(state)), "step": state.get("step", "catalog"),
                    "_literal_information": True,
                    **({"degraded": True} if degraded else {})}
        if decision.intent == "agendar" and decision.service_ids:
            if step.startswith("reschedule_"):
                return {"answer": "A remarcação preserva os serviços da reserva original. Para mudar esses serviços, peça atendimento humano.\n" + resume_prompt(state), "step": step}
            return await start_booking(tenant, body, decision, state, business, catalog, redis, state_key, preview)
        if step in {"date", "professional", "time", "confirm"} or step.startswith("reschedule_"):
            return {"answer": "Não consegui identificar essa escolha com segurança. Posso informar preços, mudar o serviço ou chamar um atendente.\n" + resume_prompt(state),
                    "step": step, **({"degraded": True} if degraded else {})}
    if state.get("step", "").startswith("reschedule_"):
        return await continue_reschedule(tenant, phone, body, state, business, redis, state_key, preview)
    if state.get("step") == "human":
        return {"answer": None, "step": "human"}
    if state.get("step") == "confirm":
        if text_clean in NO:
            await redis.delete(state_key)
            return {"answer": "Tudo bem. A reserva não foi confirmada. Escreva MENU para começar novamente.", "step": "cancelled"}
        if text_clean not in YES:
            return {"answer": presentation.render_prompt("confirmation", _preferences(business)), "step": "confirm"}
        if preview:
            await redis.delete(state_key)
            return {"answer": "Simulação concluída. Nenhum agendamento real foi criado.", "step": "preview_complete"}
        if not isinstance(state.get("hold_snapshot"), str) or not re.fullmatch(r"[a-f0-9]{64}", state["hold_snapshot"]):
            return await _recover_booking(tenant, phone, state, business, catalog, redis, state_key,
                reason="O resumo anterior precisa ser validado novamente. Mantive seu pedido para consultar outras opções, sem confirmar nada.")
        try:
            async with get_session_factory()() as db:
                await qr_intake_revision.check_before_write()
                reserved = await confirm_hold(db, tenant, UUID(state["hold"]), customer_phone="+"+phone,
                    expected_snapshot=state["hold_snapshot"], source="whatsapp_qr")
        except HTTPException:
            return await _recover_booking(tenant, phone, state, business, catalog, redis, state_key,
                reason="Não foi possível confirmar esse horário. Mantive o serviço, o profissional e o dia para conferir alternativas; nenhuma nova confirmação foi feita.")
        await redis.delete(state_key)
        when = reserved["start_at"].astimezone(ZoneInfo(business["timezone"]))
        return {"answer": presentation.render_summary(_summary(state, when, business, catalog,
                    protocol=reserved["protocol"], total_cents=reserved["total_cents"]), _preferences(business), confirmed=True),
                "step": "confirmed", "protocol": reserved["protocol"], "_verified_operation": True}
    if state.get("step") == "time":
        options = state.get("slots", [])
        when = None
        if text_clean.isdigit() and 1 <= int(text_clean) <= len(options):
            when = options[int(text_clean) - 1]
        else:
            parsed = requested_time(body)
            for candidate in options:
                if parsed and datetime.fromisoformat(candidate).strftime("%H:%M") == parsed:
                    when = candidate
                    break
        if not when:
            return {"answer": presentation.render_prompt("time", _preferences(business)), "step": "time"}
        return await prepare_booking_summary(tenant, phone, state, when, business, catalog, redis, state_key, preview)
    if state.get("step") == "professional":
        candidates = state.get("professionals", [])
        selected = professional_choice(body, candidates)
        if selected is None:
            return {"answer": presentation.render_prompt("professional", _preferences(business)), "step": "professional"}
        state["professional"] = selected["id"]
        state["professional_name"] = selected["name"]
        if not state.get("day"):
            state["step"] = "date"
            await redis.set(state_key, json.dumps(state), ex=STATE_TTL)
            return {"answer": "Para qual dia? Pode escrever amanhã ou uma data (ex.: 20/10).", "step": "date"}
        answer, state = await offer_slots(tenant, state, preview)
        await redis.set(state_key, json.dumps(state), ex=STATE_TTL)
        return {"answer": answer, "step": state["step"]}
    if state.get("step") == "date":
        chosen_day = requested_day(body, business["timezone"])
        if chosen_day is None:
            return {"answer": "Informe o dia, por exemplo: amanhã, 20/10 ou 2026-10-20.", "step": "date"}
        state["day"] = chosen_day.isoformat()
        if not state.get("professional"):
            pros = await professionals_for(tenant, [UUID(x) for x in state["services"]])
            if not pros:
                await redis.delete(state_key)
                return {"answer": "Não há profissional habilitado para essa combinação. Escreva MENU para recomeçar.", "step": "not_available"}
            if len(pros) > 1:
                state["step"] = "professional"
                state["professionals"] = [{"id": str(p["id"]), "name": p["name"]} for p in pros]
                await redis.set(state_key, json.dumps(state), ex=STATE_TTL)
                return {"answer": "Qual profissional prefere?\n" + "\n".join(
                    f"{i}. {p['name']}" for i, p in enumerate(pros, 1)), "step": "professional"}
            state["professional"] = str(pros[0]["id"])
            state["professional_name"] = pros[0]["name"]
        answer, state = await offer_slots(tenant, state, preview)
        await redis.set(state_key, json.dumps(state), ex=STATE_TTL)
        return {"answer": answer, "step": state["step"]}

    if intent == "greeting":
        display = state.get("customer_name") or (None if preview else await customer_name(tenant, phone))
        greet = f"Olá, {presentation.escape_label(display)}! " if display else "Olá! "
        await redis.set(state_key, json.dumps({"step": "menu",
            "menu_ids": [str(item["id"]) for item in catalog[:12]],
            "customer_name": display}), ex=STATE_TTL)
        return {"answer": catalog_menu(business, catalog, greet), "step": "menu", "_service_reply_prompt": True}
    if state.get("step") == "menu" and text_clean.isdigit():
        if denial := capability_denial(business, "appointments"):
            return denial
        index = int(text_clean) - 1
        ids = state.get("menu_ids", [])
        if 0 <= index < len(ids):
            state = {"step": "date", "services": [ids[index]],
                     "customer_name": state.get("customer_name")}
            await redis.set(state_key, json.dumps(state), ex=STATE_TTL)
            return {"answer": "Para qual dia deseja agendar? Ex.: amanhã ou 20/10.", "step": "date"}
        return {"answer": presentation.render_prompt("service", _preferences(business)), "step": "menu"}
    await redis.set(state_key, json.dumps({"step": "menu",
        "menu_ids": [str(item["id"]) for item in catalog[:12]],
        "customer_name": state.get("customer_name")}), ex=STATE_TTL)
    return {"answer": catalog_menu(business, catalog,
            "A IA está temporariamente indisponível; vou usar o menu. " if degraded else ""),
            "step": "menu", "_service_reply_prompt": True, **({"degraded": True} if degraded else {})}


_STATE_SNAPSHOT_SCRIPT = """
local result={}
for _,key in ipairs(KEYS) do
 table.insert(result,redis.call('GET',key) or false)
 table.insert(result,redis.call('PTTL',key))
end
return result
"""
_STATE_RESTORE_SCRIPT = """
for i,key in ipairs(KEYS) do
 local offset=(i-1)*3
 local present=tonumber(ARGV[offset+1])
 local ttl=tonumber(ARGV[offset+3])
 if present==1 and ttl==-1 then redis.call('SET',key,ARGV[offset+2])
 elseif present==1 and ttl>0 then redis.call('SET',key,ARGV[offset+2],'PX',ttl)
 else redis.call('DEL',key) end
end
return 1
"""


def _provisional_keys(key: str):
    return key, key + ":commerce", key + ":catalog-context"


async def _snapshot_provisional_state(redis, key: str):
    started = monotonic()
    rows = await redis.eval(_STATE_SNAPSHOT_SCRIPT, 3, *_provisional_keys(key))
    if not isinstance(rows, (list, tuple)) or len(rows) != 6 or any(type(rows[i]) is not int for i in (1, 3, 5)):
        raise RuntimeError("Unable to snapshot provisional conversation state")
    return started, rows


async def _restore_provisional_state(redis, key: str, snapshot):
    if snapshot is None:
        return
    started, rows = snapshot
    elapsed = max(0, math.ceil((monotonic() - started) * 1000))
    args = []
    for index in (0, 2, 4):
        value, ttl = rows[index:index+2]
        args.extend((int(value is not None), value if value is not None else "", ttl - elapsed if ttl >= 0 else ttl))
    await redis.eval(_STATE_RESTORE_SCRIPT, 3, *_provisional_keys(key), *args)


async def _require_pending_review(redis, key: str):
    """A summary that was not delivered cannot authorize the next acceptance."""
    try:
        state = json.loads(await redis.get(key) or "null")
    except (TypeError, ValueError):
        state = None
    if isinstance(state, dict) and state.get("step") in {"confirm", "reschedule_confirm", "cancel_confirm"}:
        state["confirmation_requires_review"] = True
        state["confirmation_message_id"] = None
        await redis.set(key, json.dumps(state), ex=_remaining_state_ttl(state))
    try:
        commerce = json.loads(await redis.get(key + ":commerce") or "null")
    except (TypeError, ValueError):
        commerce = None
    if isinstance(commerce, dict) and commerce.get("pending"):
        commerce["last_message_id"] = None
        await redis.set(key + ":commerce", json.dumps(commerce), ex=STATE_TTL)


async def run_locked(tenant: UUID, phone: str, body: str, namespace: str,
                     message_id: str | None = None, preview: bool = False,
                     prepare=None, reset: bool = False, message_ids: list[str] | None = None,
                     intake_revision: int | None = None, require_summary_review: bool = False):
    from redis.asyncio import from_url
    from app import qr_operation_receipt
    redis = from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"),
                     socket_connect_timeout=3, socket_timeout=20)
    lock = redis.lock(f"orion:qr:ai:lock:{tenant}:{namespace}", timeout=90, blocking_timeout=4)
    revision_token = None
    provisional_snapshot = None
    state_key = f"orion:qr:ai:{tenant}:{namespace}"
    try:
        async with lock:
            ids = message_ids or ([message_id] if message_id else [])
            keys = [f"orion:qr:ai:done:{tenant}:{namespace}:{item}" for item in ids]
            legacy_keys = [] if preview else [f"orion:qr:ai:done:{tenant}:{item}" for item in ids]
            if any([await redis.exists(key) for key in keys + legacy_keys]):
                return {"answer": None, "step": "duplicate"}
            if not preview and qr_intake_revision.enabled():
                revision_token = qr_intake_revision.bind(tenant, phone, ids, intake_revision, redis)
            try:
                await qr_intake_revision.assert_current()
                if revision_token is not None:
                    provisional_snapshot = await _snapshot_provisional_state(redis, state_key)
                if require_summary_review and not preview:
                    await _require_pending_review(redis, f"orion:qr:ai:{tenant}:{namespace}")
                if reset:
                    await redis.delete(f"orion:qr:ai:{tenant}:{namespace}")
                transcript = await prepare() if prepare else None
                if transcript is not None:
                    body = transcript["text"]
                identity_token = _message_identity.set(message_id)
                recent_token = _recent_operation.set(None)
                try:
                    # A delivered receipt is acknowledged below. Keep its
                    # validated reference only for this explicit date follow-up.
                    if not preview and _weekday_restriction(body) is not None:
                        raw = await redis.get(qr_operation_receipt.key(tenant, phone))
                        _recent_operation.set(qr_operation_receipt._read(raw, tenant, phone) if raw else None)
                    result = await qr_operation_receipt.pending(redis, tenant, phone, require_summary_review, preview=preview)
                    if result is None:
                        result = await turn(tenant, phone, body, redis, namespace, preview=preview)
                    verified_operation = result.pop("_verified_operation", False) is True
                    await qr_operation_receipt.save(redis, tenant, phone, message_id, result,
                        verified_operation=verified_operation, preview=preview)
                    await qr_intake_revision.assert_current()
                finally:
                    _recent_operation.reset(recent_token)
                    _message_identity.reset(identity_token)
                if transcript is not None:
                    result = {**result, "transcript": body, "input_kind": "audio"}
            except qr_intake_revision.Superseded as superseded:
                result = superseded.result()
                if superseded.not_committed:
                    await _restore_provisional_state(redis, state_key, provisional_snapshot)
                    return result
                await _require_pending_review(redis, f"orion:qr:ai:{tenant}:{namespace}")
            if keys:
                async with redis.pipeline(transaction=True) as pipe:
                    for key in keys + legacy_keys:
                        pipe.set(key, "1", ex=604800, nx=True)
                    await pipe.execute()
            return result
    finally:
        if revision_token is not None:
            qr_intake_revision.reset(revision_token)
        await redis.aclose()


@router.post("/internal/qr/ai/reply", include_in_schema=False)
async def qr_reply(payload: Incoming, request: Request):
    configured = os.getenv("QR_BRIDGE_TOKEN", "")
    given = request.headers.get("x-orion-bridge-key", "")
    if len(configured) < 32 or not secrets.compare_digest(configured, given):
        raise HTTPException(401, "Acesso negado")
    if os.getenv("QR_AI_ENABLED", "0") != "1":
        return {"answer": None, "step": "disabled"}
    allowlist = {item.strip() for item in os.getenv("QR_AI_ALLOWED_SENDERS", "").split(",") if item.strip()}
    if not allowlist or payload.phone not in allowlist:
        return {"answer": None, "step": "not_allowlisted"}
    try:
        tenant = UUID(os.environ["QR_AI_TENANT_ID"])
    except (KeyError, ValueError):
        raise HTTPException(503, "Estabelecimento de atendimento não configurado") from None
    # The authenticated bridge supplies a PN-scoped profile. Metadata does not
    # grant permission, change the allowlist, or replace a manually useful name.
    async def remember_profile():
        if payload.profile and useful_name(payload.profile.name):
            # Metadata enrichment is idempotent and independent of a booking or
            # its consent. Do not mark it as an irreversible assistant action.
            await qr_intake_revision.assert_current()
            try:
                async with asyncio.timeout(1.5):
                    await remember_whatsapp_name(tenant, payload.phone, payload.profile.name)
            except (SQLAlchemyError, TimeoutError, ConnectionError, RuntimeError):
                # A missing name must not break the normal assistant response.
                pass

    return await run_locked(tenant, payload.phone, payload.text, payload.phone, payload.message_id,
                            message_ids=payload.message_ids, intake_revision=payload.intake_revision,
                            require_summary_review=payload.require_summary_review,
                            prepare=remember_profile if payload.profile else None)


@router.post("/api/v1/tenants/{tenant_id}/ai/conversation-preview")
async def conversation_preview(tenant_id: UUID, payload: Simulate,
                               actor: Actor = Depends(authenticate)):
    from app.tenant_access import resolve_tenant_access
    await resolve_tenant_access(actor, tenant_id)
    if os.getenv("GROQ_PREVIEW_ENABLED", "0") != "1":
        raise HTTPException(423, "Prévia desativada")
    owner = str(actor.account_id) if actor.account_id else actor.role
    snapshot = await qr_profile.resolve_preview(tenant_id, payload.profile_version, payload.style_rules)
    token = qr_profile.preview_snapshot.set(snapshot)
    try:
        return await run_locked(tenant_id, "5567999999999", payload.text,
                                f"preview:{owner}:{payload.session}",
                                message_id=str(payload.request_id) if payload.request_id else None,
                                preview=True, reset=payload.reset)
    finally:
        qr_profile.preview_snapshot.reset(token)


@router.post("/api/v1/tenants/{tenant_id}/ai/conversation-preview/audio")
async def conversation_preview_audio(
    tenant_id: UUID, request: Request, response: Response,
    session: str = Query(min_length=8, max_length=64, pattern=r"^[a-zA-Z0-9-]+$"),
    request_id: UUID = Query(), reset: bool = False,
    profile_version: UUID | None = None,
    actor: Actor = Depends(authenticate),
):
    from app.tenant_access import resolve_tenant_access
    from app.groq_audio import transcribe_request
    await resolve_tenant_access(actor, tenant_id)
    if os.getenv("GROQ_PREVIEW_ENABLED", "0") != "1" or os.getenv("QR_AI_AUDIO_ENABLED", "0") != "1":
        raise HTTPException(423, "Prévia por áudio desativada")
    response.headers["Cache-Control"] = "no-store"
    owner = str(actor.account_id) if actor.account_id else actor.role

    async def prepare():
        return await transcribe_request(request, preview=True)

    snapshot = await qr_profile.resolve_preview(tenant_id, profile_version)
    token = qr_profile.preview_snapshot.set(snapshot)
    try:
        return await run_locked(tenant_id, "5567999999999", "",
                                f"preview:{owner}:{session}", message_id="audio:" + str(request_id),
                                preview=True, prepare=prepare, reset=reset)
    finally:
        qr_profile.preview_snapshot.reset(token)


@router.post("/api/v1/tenants/{tenant_id}/ai/style-examples")
async def style_examples(tenant_id: UUID, payload: StyleExamples,
                         actor: Actor = Depends(authenticate)):
    from app.tenant_access import resolve_tenant_access
    from app.qr_presentation import STYLE_LABELS
    token, business, catalog, professionals, summary = None, None, [], [], None
    notice = "Não há dados de agenda suficientes para mostrar estes modelos com informações reais."
    chosen = []
    try:
        try:
            async with asyncio.timeout(12):
                await resolve_tenant_access(actor, tenant_id)
                snapshot = await qr_profile.resolve_preview(tenant_id, payload.profile_version, payload.style_rules)
                token = qr_profile.preview_snapshot.set(snapshot)
                business, catalog, professionals = await tenant_data(tenant_id)
                profile = business.get("profile") or {}
                if "appointments" not in profile.get("capabilities", []):
                    notice = "O agendamento não está habilitado neste perfil. Os exemplos de confirmação e cancelamento de agenda não se aplicam."
                elif not catalog or not professionals:
                    notice = "Não há serviços e profissionais ativos suficientes para consultar um exemplo real de agenda."
                else:
                    chosen = catalog[:2]
                    candidates = await professionals_for(tenant_id, [UUID(str(item["id"])) for item in chosen])
                    if not candidates and len(chosen) > 1:
                        chosen = catalog[:1]
                        candidates = await professionals_for(tenant_id, [UUID(str(item["id"])) for item in chosen])
                    valid_pros = {str(person["id"]) for person in professionals}
                    professional = next((person for person in candidates if str(person["id"]) in valid_pros), None)
                    if professional is None:
                        notice = "Não encontrei um profissional habilitado para os serviços consultados. Nenhum horário foi inventado."
                    else:
                        async with get_session_factory()() as db:
                            async with tenant_transaction(db, tenant_id):
                                rows = (await db.execute(text("""
                                    SELECT t.timezone,t.booking_horizon_days,t.min_notice_minutes,h.weekday,h.end_time
                                    FROM tenants t LEFT JOIN working_hours h
                                      ON h.tenant_id=t.id AND h.professional_id=:professional
                                    WHERE t.id=:tenant AND t.status='active' ORDER BY h.weekday
                                """), {"tenant": tenant_id, "professional": UUID(str(professional["id"]))})).mappings().all()
                            target = None
                            if rows and rows[0]["timezone"] == business["timezone"]:
                                horizon, minimum = rows[0]["booking_horizon_days"], rows[0]["min_notice_minutes"]
                                if type(horizon) is int and 1 <= horizon <= 365 and type(minimum) is int and minimum >= 0:
                                    zone = ZoneInfo(business["timezone"])
                                    now = datetime.now(zone)
                                    earliest = now + timedelta(minutes=minimum)
                                    first = max(0, (earliest.date() - now.date()).days)
                                    for offset in range(first, min(horizon, first + 7) + 1):
                                        day = now.date() + timedelta(days=offset)
                                        if any(row["weekday"] == day.weekday() and row["end_time"] is not None and
                                               datetime.combine(day, row["end_time"], zone) > earliest for row in rows):
                                            target = day
                                            break
                            if target is None:
                                notice = "Não identifiquei expediente dentro do horizonte de agendamento para esta consulta. Não há um horário real para ilustrar os modelos."
                            else:
                                # One complete grid, shared by all four visual styles.
                                grid = await list_slots(db, tenant_id, UUID(str(professional["id"])),
                                    [UUID(str(item["id"])) for item in chosen], target)
                                if grid["slots"]:
                                    when = datetime.fromisoformat(grid["slots"][0])
                                    if when.utcoffset() is None or when.astimezone(ZoneInfo(business["timezone"])).date() != target:
                                        raise ValueError("Invalid factual example time")
                                    summary = presentation.BookingSummary(tuple(item["name"] for item in chosen), when,
                                        business["timezone"], grid["price_cents"], professional=professional["name"],
                                        duration_min=grid["duration_min"], protocol="EXEMPLO-VISUAL-SEM-VALIDADE")
                                else:
                                    notice = "Não encontrei horário livre na grade real consultada. Os modelos de agenda não serão preenchidos com datas ou profissionais inventados."
        except TimeoutError:
            if business is None:
                raise HTTPException(503, "Não consegui carregar os dados autorizados no prazo de 12 segundos") from None
            notice = "A consulta de agenda ultrapassou o prazo de 12 segundos. Nenhum exemplo operacional foi preenchido com dados inventados."
        except (ValueError, KeyError, TypeError):
            if business is None:
                raise HTTPException(503, "Não consegui validar os dados autorizados para os exemplos") from None
            notice = "Não consegui validar os dados reais de agenda nesta consulta. Os exemplos operacionais não serão preenchidos por suposição."
        except HTTPException as exc:
            if business is None or exc.status_code in {401, 403}:
                raise
            notice = "Não consegui consultar a agenda real agora. Nenhuma confirmação ou cancelamento foi realizado."
        base = business.get("profile") or {"style_rules": StyleRules().model_dump()}
        examples = []
        marker = "*Exemplo visual — nenhuma operação realizada*\n\n"
        for style, label in STYLE_LABELS.items():
            prefs = {**base["style_rules"], "style": style}
            answer = qr_profile.quote(chosen or catalog[:2], {"style_rules": prefs}, "Quero apenas saber o preço") if catalog else "Ainda não há serviços ativos cadastrados."
            if summary:
                suffix = "\n\n_Protocolo de exemplo, sem validade. Não existe reserva ou cancelamento associado a esta demonstração._"
                confirmation = marker + presentation.render_summary(summary, prefs, confirmed=True) + suffix
                cancellation = marker + presentation.render_summary(summary, prefs, confirmed=True, operation="cancel") + suffix
            else:
                confirmation = cancellation = marker + notice
            examples.append({"style": style, "label": label, "answer": answer,
                             "confirmation_example": confirmation, "cancellation_example": cancellation})
        return {"examples": examples, "catalog_revision": qr_profile.catalog_revision(business, catalog, professionals),
                "profile_version": business.get("profile_id")}
    finally:
        if token is not None:
            qr_profile.preview_snapshot.reset(token)
