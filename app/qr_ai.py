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
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request, Query, Response
from pydantic import Field, model_validator
from sqlalchemy import text

from app.booking_service import book_appointment, confirm_hold, list_slots, reschedule_appointment, preview_reschedule, fingerprint as booking_fingerprint
from app.catalog_api import Actor, authenticate, authorize, support_only
from app.catalog_schemas import StrictModel
from app.database import get_session_factory, tenant_transaction
from app.groq_ai import GroqServiceError, Intention, interpret
from app.qr_customer import supplied_name, customer_name, remember_name, customer_appointments, appointment_services
from app.qr_behaviors import control_intent, catalog_match, norm, catalog_question_services, availability_question
from app.business_profile_schemas import StyleRules
from app import qr_profile
from app import qr_presentation as presentation
from app import qr_commerce
from app import qr_intake_revision
from app.groq_understanding import interpret_plan
from app.qr_understanding import UnderstandingPlan, build_plan, clarification_for
from app.qr_information import info as compose_information, faq_catalog
from app.qr_dialogue import (contextual_response, interpret_contextual_response, choose_time, choose_professional,
    compound_candidate, interpret_compound_response)

router = APIRouter(tags=["Orion IA conversacional"])
router.include_router(qr_intake_revision.router)
STATE_TTL = 7 * 24 * 3600  # contexto temporário até 7 dias; agendamentos ficam no PostgreSQL
PHONE_RE = re.compile(r"^55[1-9][0-9]{9,10}$")
TIME_RE = re.compile(r"(?<!\d)([01]?\d|2[0-3])(?:[:h]([0-5]\d))?(?!\d)")
YES = {"sim", "s", "confirmo", "pode confirmar", "confirmar", "ok pode confirmar"}
NO = {"não", "nao", "n", "cancelar", "desistir"}
RESET = {"menu", "reiniciar", "começar", "recomecar", "recomeçar"}
_reply_preferences: ContextVar[dict | None] = ContextVar("orion_qr_reply_preferences", default=None)
_reply_facts: ContextVar[tuple | None] = ContextVar("orion_qr_reply_facts", default=None)
_message_identity: ContextVar[str | None] = ContextVar("orion_qr_message_identity", default=None)


def _preferences(business: dict) -> dict | None:
    return (business.get("profile") or {}).get("style_rules")


def _summary_requested(body: str) -> bool:
    text = norm(body).strip(" ?!.,")
    return text in {"retomar resumo", "ver resumo", "rever resumo", "resumo original"} or bool(re.fullmatch(
        r"(?:(?:quero|pode|poderia|vamos|me)\s+)*(?:ver|rever|retomar|mostrar|mostra|conferir)\s+(?:(?:o|esse|meu)\s+)?resumo(?:\s+(?:de novo|novamente|original))?", text))


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


def format_money(cents: int):
    return f"R$ {cents // 100},{cents % 100:02d}"


def capability_denial(business: dict, capability: str) -> dict | None:
    if business.get("profile_id") and capability not in business.get("profile", {}).get("capabilities", []):
        return {"answer": "Este atendimento não está habilitado para essa operação. Posso deixar a conversa para atendimento humano.",
                "step": "capability_unavailable"}
    return None


async def offer_slots(tenant: UUID, state: dict, preview: bool):
    async with get_session_factory()() as db:
        try:
            slots = await list_slots(
                db, tenant, UUID(state["professional"]),
                [UUID(x) for x in state["services"]], date.fromisoformat(state["day"]),
            )
        except HTTPException:
            return "Não há horários disponíveis nessa data. Informe outra data.", {**state, "step": "date"}
    options = slots["slots"][:8]
    if not options:
        return "Não encontrei horários livres nessa data. Informe outra data.", {**state, "step": "date"}
    state.update(step="time", slots=options, price=slots["price_cents"], duration=slots["duration_min"])
    facts = _reply_facts.get()
    if facts:
        business, catalog = facts
        return presentation.render_slots([x["name"] for x in catalog if str(x["id"]) in state["services"]], options,
            _preferences(business), timezone=business["timezone"], day=state["day"], total_cents=state["price"],
            duration_min=state["duration"], simulation=preview, professional=state.get("professional_name")), state
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
        state = {"step": "reschedule_pick", "candidate_ids": [str(x["id"]) for x in future[:8]]}
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


async def continue_reschedule(tenant: UUID, phone: str, body: str, state: dict,
                              business: dict, redis, key: str, preview: bool):
    if denial := capability_denial(business, "rescheduling"):
        return denial
    step = state.get("step")
    text_clean = body.strip().lower()
    if step == "reschedule_pick":
        ids = state.get("candidate_ids", [])
        if not text_clean.isdigit() or not 1 <= int(text_clean) <= len(ids):
            return {"answer": "Responda com o número do agendamento que deseja remarcar.", "step": step}
        return await selected_reschedule(tenant, phone, UUID(ids[int(text_clean)-1]), redis, key)
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
            return {"answer": "Escolha um horário listado, pelo número ou pelo horário (ex.: 14:00).", "step": step}
        _, catalog, _ = await tenant_data(tenant)
        return await prepare_reschedule_summary(tenant, phone, state, chosen, business, catalog, redis, key, preview)
    if step == "reschedule_confirm":
        if text_clean in NO:
            await redis.delete(key)
            return {"answer": "Remarcação cancelada. O horário original permanece.", "step": "reschedule_cancelled"}
        if text_clean not in YES:
            return {"answer": "Responda SIM ou NÃO.", "step": step}
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
                    datetime.fromisoformat(state["new_start"]), expected_snapshot=state["expected_snapshot"], customer_phone="+" + phone)
        except HTTPException:
            await redis.delete(key)
            return {"answer": "O novo horário ficou indisponível. O original permanece. Escreva REMARCAR para tentar novamente.", "step": "reschedule_conflict"}
        await redis.delete(key)
        when = updated["start_at"].astimezone(ZoneInfo(business["timezone"]))
        facts = state["reschedule_facts"]
        summary = presentation.BookingSummary(tuple(facts["services"]), when, facts["timezone"], facts["total_cents"],
            professional=facts["professional"], duration_min=facts["duration_min"], protocol=updated["protocol"])
        return {"answer": presentation.render_summary(summary, _preferences(business), confirmed=True, operation="reschedule"), "step": "rescheduled"}
    return {"answer": "Escreva REMARCAR para escolher um novo horário.", "step": "unknown"}


def catalog_menu(business: dict, catalog: list[dict], greeting: str = "") -> str:
    lines = "\n".join(
        f"{n}. {presentation.escape_label(item['name'])} — {format_money(item['price_cents'])} ({item['duration_min']} min)"
        for n, item in enumerate(catalog[:12], 1))
    return f"{greeting}Bem-vindo à {presentation.escape_label(business['name'])}!\n{lines}\nQual serviço deseja?"


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
        return "O pedido anterior continua sem confirmação. Para rever aquele pedido, envie RETOMAR RESUMO."
    step = state.get("step", "")
    if step == "professional":
        listing = "\n".join(f"{i}. {p['name']}" for i, p in enumerate(state.get("professionals", []), 1))
        return "Quando quiser continuar, escolha o profissional pelo nome ou número:\n" + listing
    return {
        "date": "Quando quiser continuar, informe o dia desejado.",
        "time": "Quando quiser continuar, escolha um dos horários que listei.",
        "confirm": "O pedido ainda não foi confirmado. Para concluir, responda SIM; para desistir, NÃO.",
        "reschedule_day": "A reserva original permanece. Para continuar a remarcação, informe a nova data.",
        "reschedule_time": "A reserva original permanece. Para continuar, escolha um horário listado.",
        "reschedule_confirm": "A reserva original permanece. Confirme a remarcação com SIM ou desista com NÃO.",
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


async def answer_availability(tenant: UUID, body: str, state: dict, business: dict,
                              catalog: list[dict], redis, key: str):
    """A separate read-only inquiry; booking selections and confirmation stay intact."""
    if denial := capability_denial(business, "appointments"):
        return denial
    if state.get("step") in {"confirm", "reschedule_confirm"}:
        state["confirmation_requires_review"] = True
    inquiry = dict(state.get("availability_query", {}))
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
        await redis.set(key, json.dumps(state), ex=300 if state["step"] in {"confirm", "reschedule_confirm"} else STATE_TTL)
        return {"answer": answer, "step": state["step"]}

    if not ids:
        listing = "\n".join(f"{i}. {item['name']}" for i, item in enumerate(catalog[:12], 1))
        return await ask("Posso consultar a agenda real. Para qual serviço? Pode dizer o nome ou número:\n" + listing, "service")
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
        try:
            async with asyncio.timeout(12), get_session_factory()() as db:
                for offset in range(7):
                    candidate_day = today + timedelta(days=offset)
                    result = await list_slots(db, tenant, UUID(inquiry["professional"]),
                                              [UUID(item) for item in ids], candidate_day)
                    times = [datetime.fromisoformat(item).strftime("%H:%M") for item in result["slots"][:6]]
                    if times:
                        found.append(f"• {candidate_day:%d/%m}: " + ", ".join(times))
                        found_slots.extend(result["slots"][:6])
                    if len(found) == 3:
                        break
        except (HTTPException, TimeoutError):
            return await ask("Não consegui verificar a agenda completa agora. Qual data deseja consultar?", "date")
        state.pop("availability_query", None)
        state["last_availability"] = {"services": list(ids), "professional": str(selected["id"]), "search_next": True,
            "queried_at": datetime.now().timestamp()}
        await redis.set(key, json.dumps(state), ex=300 if state.get("step") in {"confirm", "reschedule_confirm"} else STATE_TTL)
        heading = f"Consultei {names} com {selected['name']} nos próximos sete dias."
        answer = heading + "\n\n" + presentation.render_slots([x["name"] for x in catalog if str(x["id"]) in ids], found_slots,
            _preferences(business), timezone=business["timezone"], ask_selection=False)
        review = "\n" + resume_prompt(state) if state.get("confirmation_requires_review") else ""
        return {"answer": answer + "\nConsulta sem reserva; nenhum agendamento foi criado ou alterado." + review,
                "step": state.get("step", "menu")}
    try:
        async with get_session_factory()() as db:
            result = await list_slots(db, tenant, UUID(inquiry["professional"]),
                                      [UUID(item) for item in ids], date.fromisoformat(chosen_day))
    except HTTPException:
        return await ask("Não consegui confirmar horários disponíveis nessa data. Qual outra data deseja consultar?", "date")
    state.pop("availability_query", None)
    state["last_availability"] = {"services": list(ids), "professional": str(selected["id"]), "day": chosen_day,
        "queried_at": datetime.now().timestamp()}
    await redis.set(key, json.dumps(state), ex=300 if state.get("step") in {"confirm", "reschedule_confirm"} else STATE_TTL)
    times = [datetime.fromisoformat(item).strftime("%H:%M") for item in result["slots"][:12]]
    heading = f"Disponibilidade para {names} com {selected['name']} em {date.fromisoformat(chosen_day):%d/%m}: "
    answer = heading + "\n\n" + presentation.render_slots([x["name"] for x in catalog if str(x["id"]) in ids], result["slots"][:12],
        _preferences(business), timezone=business["timezone"], ask_selection=False)
    review = "\n" + resume_prompt(state) if state.get("confirmation_requires_review") else ""
    return {"answer": answer + " Consulta sem reserva; nenhum agendamento foi criado ou alterado." + review,
            "step": state.get("step", "menu")}


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
    summary = f"{names}: {format_money(total)}, {duration} minutos."
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
        if product_plan.intent in {"price", "catalog", "stock", "faq"}:
            return denial
    result = await qr_commerce.handle(tenant, phone, body, message_id=message_id, state=commerce_state, preview=preview,
        customer_name=state.get("customer_name"), allow_orders="product_orders" in (business.get("profile") or {}).get("capabilities", []),
        preferences=_preferences(business), semantic_kind=semantic)
    if result is None:
        return None
    private_state = result.get("state")
    if isinstance(private_state, dict):
        await redis.set(commerce_key, json.dumps(private_state), ex=STATE_TTL)
    return {"answer": result.get("answer"), "step": result.get("step", "commerce_information")}


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
    return _bounded_reply(result.get("answer") or "", factual, result["step"])


async def _review_after_inquiry(state: dict, redis, key: str):
    if state.get("step") in {"confirm", "reschedule_confirm"}:
        state["confirmation_requires_review"] = True
        await redis.set(key, json.dumps(state), ex=300)


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
    summary = presentation.render_summary(_summary(state, when, business, catalog), _preferences(business), simulation=preview)
    reply = _bounded_reply(factual, summary, "confirm")
    if reply["step"] == "choose_priority":
        return reply
    if preview:
        state.update(step="confirm", hold="preview")
    else:
        request_fingerprint = booking_fingerprint(UUID(state["professional"]),
            [UUID(x) for x in state["services"]], datetime.fromisoformat(when), "+" + phone)
        idem = hashlib.sha256(f"qr:{tenant}:{request_fingerprint}".encode()).hexdigest()
        try:
            if state.get("new_customer_name"):
                await qr_intake_revision.check_before_write()
                await remember_name(tenant, phone, state["new_customer_name"])
            selected_name = state.get("customer_name") or await customer_name(tenant, phone) or "Cliente WhatsApp"
            async with get_session_factory()() as db:
                await qr_intake_revision.check_before_write()
                hold = await book_appointment(db, tenant, UUID(state["professional"]),
                    [UUID(x) for x in state["services"]], "+" + phone, selected_name,
                    datetime.fromisoformat(when), "qr-" + idem)
        except HTTPException:
            await redis.delete(key)
            return _bounded_reply(factual, "O horário não está mais disponível. Nenhuma reserva foi confirmada. Podemos consultar outro horário.", "conflict")
        state.update(step="confirm", hold=str(hold["id"]))
    state.pop("new_customer_name", None)
    state.update(confirmation_summary=summary, confirmation_expires_at=datetime.now().timestamp() + 300,
                 selected_start=when, confirmation_message_id=_message_identity.get())
    await redis.set(key, json.dumps(state), ex=300)
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
    summary = presentation.render_summary(facts, _preferences(business), simulation=preview, operation="reschedule")
    result = _bounded_reply(factual, summary, "reschedule_confirm")
    if result["step"] != "choose_priority":
        state.update(step="reschedule_confirm", new_start=when, confirmation_summary=summary,
                     confirmation_expires_at=datetime.now().timestamp() + 300, expected_snapshot=expected,
                     confirmation_message_id=_message_identity.get(),
                     reschedule_facts={"services": list(facts.services), "professional": facts.professional,
                        "total_cents": facts.total_cents, "duration_min": facts.duration_min, "timezone": facts.timezone})
        await redis.set(key, json.dumps(state), ex=300)
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
            actual = await list_slots(db, tenant, UUID(state["professional"]), ids, day)
    except (HTTPException, KeyError, ValueError):
        return {"answer": "Preciso consultar os dados e a disponibilidade desse pedido novamente. Qual dia você prefere?", "step": state.get("step", "date")}
    choice = choose_time(body, actual["slots"], presented_slots=state.get("slots", []),
                         timezone=business["timezone"])
    state.update(price=actual["price_cents"], duration=actual["duration_min"], professional_name=person["name"])
    if choice.kind == "selected":
        if rescheduling:
            return await prepare_reschedule_summary(tenant, phone, state, choice.when, business, catalog, redis, key, preview)
        return await prepare_booking_summary(tenant, phone, state, choice.when, business, catalog, redis, key, preview)
    options = list(choice.alternatives) if choice.kind == "alternatives" else actual["slots"]
    if choice.kind == "uncertain":
        return {"answer": "Qual horário você prefere? Preciso distinguir a escolha antes de preparar o resumo.", "step": state["step"]}
    state["slots"] = options[:8]
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
    inquiry = state.get("availability_query", {}) if query else {}
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
                await redis.set(key, json.dumps(state), ex=300 if state.get("step") in {"confirm", "reschedule_confirm"} else STATE_TTL)
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
    changed = ids != state.get("services", [])
    day = plan.requested_date or old_day
    if day and date.fromisoformat(day) < today:
        return _bounded_reply(factual, "Essa data já passou. Qual data futura você prefere?", state.get("step", "date"))
    if changed and plan.selection_mode == "replace" and not plan.declined_service_ids and not plan.requested_date:
        day = None
    proposed = {"services": list(ids), "customer_name": plan.customer_name or state.get("customer_name"), "step": "date"}
    if plan.customer_name or state.get("new_customer_name"):
        proposed["new_customer_name"] = plan.customer_name or state["new_customer_name"]
    if rescheduling:
        proposed.update(appointment=state["appointment"], professional=state["professional"])
    clock = plan.requested_time or inquiry.get("pending_clock") or (state.get("pending_clock") if state.get("step") in {"date", "professional", "reschedule_day"} else None)
    relation = plan.time_relation if plan.requested_time else inquiry.get("pending_relation", state.get("pending_relation", "exact"))
    if clock:
        proposed.update(pending_clock=clock, pending_relation=relation)
    pros = await professionals_for(tenant, [UUID(x) for x in ids])
    selected_id = str(plan.professional_id) if plan.professional_id else inquiry.get("professional") or state.get("professional")
    selected = next((p for p in pros if str(p["id"]) == selected_id), None)
    if plan.professional_id and selected is None:
        return _bounded_reply(factual, "Esse profissional não está habilitado para todos os serviços pedidos. Qual profissional prefere?", state.get("step", "professional"))
    if not selected and len(pros) == 1:
        selected = pros[0]
    if not pros:
        return _bounded_reply(factual, "Não há profissional habilitado para essa combinação de serviços.", state.get("step", "date"))
    if day:
        proposed["day"] = day
    if selected:
        proposed.update(professional=str(selected["id"]), professional_name=selected["name"])
    else:
        proposed.update(step="professional", professionals=[{"id": str(x["id"]), "name": x["name"]} for x in pros])
    if (not day and not search_next) or not selected:
        question = "Para qual dia deseja consultar?" if not day and not search_next else "Qual profissional prefere?\n" + "\n".join(x["name"] for x in pros)
        result = _bounded_reply(factual, question, state.get("step", "menu") if query else proposed["step"])
        if result["step"] != "choose_priority":
            if query:
                state["availability_query"] = {"services": ids, **({"day": day} if day else {}),
                    **({"professional": str(selected["id"])} if selected else {}), "waiting_for": "date" if not day and not search_next else "professional",
                    "search_next": search_next, **({"pending_clock": clock, "pending_relation": relation} if clock else {})}
                await redis.set(key, json.dumps(state), ex=300 if state.get("step") in {"confirm", "reschedule_confirm"} else STATE_TTL)
            else:
                if rescheduling:
                    proposed["step"] = "reschedule_day"
                    result["step"] = "reschedule_day"
                await redis.set(key, json.dumps(proposed), ex=STATE_TTL)
        return result
    if query:
        found = []
        found_slots = []
        try:
            async with asyncio.timeout(12), get_session_factory()() as db:
                for target in ([today + timedelta(days=x) for x in range(7)] if search_next else [date.fromisoformat(day)]):
                    current = await list_slots(db, tenant, UUID(proposed["professional"]), [UUID(x) for x in ids], target)
                    actual = current["slots"]
                    if clock and relation == "exact":
                        actual = [x for x in actual if datetime.fromisoformat(x).strftime("%H:%M") == clock]
                    if clock and relation in {"after", "before"}:
                        actual = [x for x in actual if (datetime.fromisoformat(x).strftime("%H:%M") > clock if relation == "after" else datetime.fromisoformat(x).strftime("%H:%M") < clock)]
                    if actual:
                        found.append(f"{target:%d/%m}: " + ", ".join(datetime.fromisoformat(x).strftime("%H:%M") for x in actual[:8]))
                        found_slots.extend(actual[:8])
                    if search_next and len(found) == 3:
                        break
        except (HTTPException, TimeoutError):
            return _bounded_reply(factual, "Não consegui verificar a agenda real agora. Nenhuma reserva foi alterada.", state.get("step", "menu"))
        result = _bounded_reply(factual, f"Disponibilidade real com {selected['name']}:\n\n" +
            presentation.render_slots([x["name"] for x in catalog if str(x["id"]) in ids], found_slots,
                _preferences(business), timezone=business["timezone"], ask_selection=False) +
            "\nConsulta sem reserva; nenhum agendamento foi criado ou alterado.", state.get("step", "menu"))
        if result["step"] != "choose_priority":
            state.pop("availability_query", None)
            state["last_availability"] = {"services": list(ids), "professional": str(selected["id"]),
                "search_next": bool(search_next), "queried_at": datetime.now().timestamp(),
                **({"day": day} if day else {})}
            await redis.set(key, json.dumps(state), ex=300 if state.get("step") in {"confirm", "reschedule_confirm"} else STATE_TTL)
        return result
    try:
        async with get_session_factory()() as db:
            slots = await list_slots(db, tenant, UUID(proposed["professional"]), [UUID(x) for x in ids], date.fromisoformat(day))
    except HTTPException:
        return _bounded_reply(factual, "Não consegui verificar horários para essa combinação e data. Qual outra data deseja consultar?", state.get("step", "date"))
    options = slots["slots"]
    if clock and relation in {"after", "before"}:
        options = [x for x in options if (datetime.fromisoformat(x).strftime("%H:%M") > clock if relation == "after" else datetime.fromisoformat(x).strftime("%H:%M") < clock)]
    proposed.update(slots=options[:8], price=slots["price_cents"], duration=slots["duration_min"], step="reschedule_time" if rescheduling else "time")
    exact = next((x for x in options if clock and relation == "exact" and datetime.fromisoformat(x).strftime("%H:%M") == clock), None)
    if exact:
        proposed.pop("pending_clock", None);proposed.pop("pending_relation", None)
        if rescheduling:
            return await prepare_reschedule_summary(tenant, phone, proposed, exact, business, catalog, redis, key, preview, factual)
        return await prepare_booking_summary(tenant, phone, proposed, exact, business, catalog, redis, key, preview, factual)
    question = ("O horário exato pedido não está disponível. " if clock and relation == "exact" else "") + (
        presentation.render_slots([x["name"] for x in catalog if str(x["id"]) in ids], options[:8], _preferences(business),
            timezone=business["timezone"], total_cents=slots["price_cents"], duration_min=slots["duration_min"], simulation=preview)
        if options else presentation.render_prompt("unavailable", _preferences(business)))
    result = _bounded_reply(factual, question, proposed["step"] if options else "reschedule_day" if rescheduling else "date")
    if result["step"] != "choose_priority":
        proposed["step"] = result["step"]
        await redis.set(key, json.dumps(proposed), ex=STATE_TTL)
    return result


async def _multi_turn(tenant: UUID, phone: str, body: str, state: dict, business: dict,
                      catalog: list[dict], professionals: list[dict], redis, key: str, preview: bool):
    if state.get("step") == "reschedule_pick" and state.get("deferred_reschedule") and body.strip().isdigit():
        ids = state.get("candidate_ids", [])
        index = int(body.strip()) - 1
        if not 0 <= index < len(ids):
            return {"answer": "Qual dos agendamentos listados você quer remarcar?", "step": "reschedule_pick"}
        try:
            plan = UnderstandingPlan.model_validate(state["deferred_reschedule"])
        except ValueError:
            return {"answer": "Preciso rever os dados da remarcação antes de continuar. Qual data prefere?", "step": "reschedule_pick"}
        result = await selected_reschedule(tenant, phone, UUID(ids[index]), redis, key)
        current = json.loads(await redis.get(key) or "{}")
        if current.get("appointment"):
            return await _planned_slots(tenant, phone, plan, current, business, catalog, redis, key, preview, "", rescheduling=True)
        return result
    if state.get("availability_query") and body.strip().lower() in YES | NO:
        await _review_after_inquiry(state, redis, key)
        if body.strip().lower() in NO:
            state.pop("availability_query", None)
            await redis.set(key, json.dumps(state), ex=300 if state.get("step") in {"confirm", "reschedule_confirm"} else STATE_TTL)
            return {"answer": "Encerrei somente a consulta de disponibilidade. O pedido anterior não foi confirmado, cancelado ou alterado.", "step": state.get("step", "menu")}
        return {"answer": resume_prompt(state), "step": state.get("step", "menu")}
    context = {"service_ids": state.get("services", []), "step": state.get("step", "menu")}
    profile = business.get("profile") or {}
    hours = [{"opens": row["opens"][:5], "closes": row["closes"][:5]} for row in profile.get("hours", [])
             if profile.get("timezone") in {None, business["timezone"]}]
    if hours:
        context["hours"] = hours
    clock_choice = state.get("step") in {"time", "reschedule_time"} and (simple_schedule_reply(body, "time") or
        (body.strip().isdigit() and 12 <= int(body.strip()) <= 23 and int(body.strip()) > len(state.get("slots", []))))
    local_choice = simple_schedule_reply(body, "date") or professional_choice(body, professionals) is not None or clock_choice
    priority = norm(body).strip(" ?!.,")
    deferred = state.get("deferred_plan")
    try:
        if deferred and priority in {"agendamento", "agendamento primeiro", "agenda", "agenda primeiro", "preparar agendamento", "preco", "precos", "valores", "precos primeiro"}:
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
    except (GroqServiceError, ValueError):
        await _review_after_inquiry(state, redis, key)
        return {"answer": "Não consegui interpretar o pedido completo com segurança. Pode esclarecer o serviço, o dia e o horário finais? Nenhuma alteração foi confirmada.",
                "step": state.get("step", "clarify"), "degraded": True}
    if "human" in plan.intents:
        await redis.set(key, json.dumps({"step": "human"}), ex=1800)
        return {"answer": "Vou deixar esta conversa para atendimento humano. Para retomar o bot, envie MENU.", "step": "human"}
    operations = set(plan.intents)
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
            await redis.set(key, json.dumps(state), ex=300 if state.get("step") in {"confirm", "reschedule_confirm"} else STATE_TTL)
        return _bounded_reply(factual, "", "information")
    if "service" in information.unresolved:
        return _bounded_reply(factual, "", state.get("step", "clarify"))
    if plan.requires_clarification:
        await _review_after_inquiry(state, redis, key)
        return _bounded_reply(factual, clarification_for(plan), state.get("step", "clarify"))
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
        return _bounded_reply(factual, result["answer"] or "", result["step"])
    if "cancel" in operations and "reschedule" not in operations:
        answer = "Encerrei somente esta solicitação. Reservas já confirmadas permanecem; para cancelar uma delas, é preciso atendimento humano."
        result = _bounded_reply(factual, answer, "cancelled")
        if result["step"] != "choose_priority":
            await redis.delete(key)
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
            await redis.set(key, json.dumps(state), ex=300 if state.get("step") in {"confirm", "reschedule_confirm"} else STATE_TTL)
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
        await redis.set(key, json.dumps(state), ex=300 if state.get("step") in {"confirm", "reschedule_confirm"} else STATE_TTL)
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
            history = [item for item in history if isinstance(item, dict) and
                       item.get("role") in {"user", "assistant"} and isinstance(item.get("content"), str)]
            current["history"] = (history + [{"role": "user", "content": body[:1500]},
                {"role": "assistant", "content": result["answer"][:1500]}])[-8:]
            if current.get("step") in {"confirm", "reschedule_confirm"}:
                now = datetime.now().timestamp()
                deadline = current.setdefault("confirmation_expires_at", now + 300)
                try:
                    remaining = float(deadline) - now
                except (TypeError, ValueError):
                    remaining = 0
                ttl = max(1, min(300, int(remaining))) if math.isfinite(remaining) else 1
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
    business, catalog, professionals = await tenant_data(tenant)
    _reply_preferences.set(_preferences(business))
    _reply_facts.set((business, catalog))
    profile = business.get("profile")
    context_key = state_key + ":catalog-context"
    revision = qr_profile.catalog_revision(business, catalog, professionals)
    old_revision = await redis.get(context_key)
    if isinstance(old_revision, bytes):
        old_revision = old_revision.decode()
    active_choice = state.get("step") in {"date", "professional", "time", "confirm"} or state.get("step", "").startswith("reschedule_")
    context_changed = ((old_revision is not None and old_revision != revision)
                       or (old_revision is None and active_choice))
    await redis.set(context_key, revision, ex=STATE_TTL)
    if context_changed and state.get("step") != "human":
        state = {"customer_name": state.get("customer_name")} if state.get("customer_name") else {}
        await redis.delete(state_key)
        await redis.delete(state_key + ":commerce")
        if text_clean in YES or norm(body) in {"retomar resumo", "ver resumo", "rever resumo"}:
            return {"answer": "As informações do atendimento mudaram. Vou precisar revisar o pedido antes de confirmar. Nenhum agendamento foi confirmado ou alterado.",
                    "step": "context_changed"}
    intent = control_intent(body)
    if state.get("step") == "human":
        return {"answer": None, "step": "human"}
    if intent == "human":
        await redis.set(state_key, json.dumps({"step": "human"}), ex=1800)
        return {"answer": "Vou deixar esta conversa para atendimento humano. Para retomar o bot, envie MENU.", "step": "human"}
    if intent in {"human_declined", "human_clarify"}:
        await _review_after_inquiry(state, redis, state_key)
        return {"answer": ("Tudo bem, continuo por aqui. Como posso ajudar?" if intent == "human_declined" else
            "Você quer continuar comigo ou falar com uma pessoa da equipe?"), "step": state.get("step", "clarify")}
    if intent == "greeting" and (state.get("step") not in {None, "menu"} or state.get("availability_query")):
        display = state.get("customer_name")
        greeting = f"Olá, {presentation.escape_label(display)}!" if display else "Olá!"
        selected = [item["name"] for item in catalog if str(item["id"]) in state.get("services", [])]
        context = " Seu pedido atual é " + " e ".join(presentation.escape_label(name) for name in selected) + "." if selected else ""
        return {"answer": greeting + context + " Posso ajudar com valores, consultar dias livres ou continuar seu pedido.",
                "step": state["step"]}
    if not _confirm_scoped:
        compound = await _compound_booking_turn(tenant, phone, body, state, business, catalog, redis, state_key, preview)
        if compound is not None:
            return compound
        commerce = await _commerce_turn(tenant, phone, body, state, business, catalog, redis, state_key, preview)
        if commerce is not None:
            return commerce
    if state.get("step") in {"confirm", "reschedule_confirm"} and not state.get("availability_query"):
        consent = contextual_response(body, awaiting_confirmation=True)
        if consent.kind == "decline":
            await _review_after_inquiry(state, redis, state_key)
            retained = " O horário original permanece." if state["step"] == "reschedule_confirm" else ""
            return {"answer": "Tudo bem, mantive os dados do seu pedido sem confirmar." + retained + "\n\n" + presentation.render_prompt("adjustment", _preferences(business)), "step": state["step"]}
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
            incoming_id, summary_id = _message_identity.get(), state.get("confirmation_message_id")
            if incoming_id and (not summary_id or incoming_id == summary_id):
                await _review_after_inquiry(state, redis, state_key)
                return {"answer": "Preciso apresentar o resumo vigente antes de aceitar uma nova confirmação. Você quer conferir o resumo?", "step": state["step"]}
            try:
                remaining = float(state.get("confirmation_expires_at", 0)) - datetime.now().timestamp()
            except (TypeError, ValueError):
                remaining = 0
            if (not math.isfinite(remaining) or remaining <= 0 or
                    not isinstance(state.get("confirmation_summary"), str) or not state["confirmation_summary"].strip()):
                return {"answer": "O resumo anterior expirou ou precisa ser apresentado novamente. Vamos rever a disponibilidade antes de confirmar?", "step": "expired"}
            body, text_clean = "SIM", "sim"
    if state.get("step") in {"time", "reschedule_time"} and not state.get("availability_query"):
        reference = choose_time(body, state.get("slots", []), presented_slots=state.get("slots", []), timezone=business["timezone"])
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
            if state.get("step") in {"confirm", "reschedule_confirm"}:
                state["confirmation_requires_review"] = True
                await redis.set(state_key, json.dumps(state), ex=300)
            return {"answer": known, "step": state.get("step", "information"), "_literal_information": True}
        if business.get("profile_id"):
            capabilities = set(profile.get("capabilities", []))
            wanted = "rescheduling" if intent == "reschedule" or state.get("step", "").startswith("reschedule_") else "appointments"
            information = catalog_question(body) or availability_question(body)
            if "catalog_answers" not in capabilities and catalog_question(body):
                return {"answer": "Este atendimento não está habilitado para informar valores automaticamente. Posso deixar sua dúvida para atendimento humano.", "step": "capability_unavailable"}
            if (wanted not in capabilities and not information and (is_service_request(body) or text_clean in YES or state.get("step") in {"date", "professional", "time", "confirm"})):
                return {"answer": "Este atendimento não está habilitado para concluir agendamentos. Posso deixar sua solicitação para atendimento humano.", "step": "capability_unavailable"}
    if state.get("confirmation_requires_review") and state.get("step") in {"confirm", "reschedule_confirm"}:
        resume = _summary_requested(body)
        if resume:
            try:
                deadline = float(state.get("confirmation_expires_at", 0))
            except (TypeError, ValueError):
                deadline = 0
            remaining = deadline - datetime.now().timestamp()
            summary = state.get("confirmation_summary")
            if not math.isfinite(remaining) or remaining <= 0 or not isinstance(summary, str) or not summary.strip():
                await redis.delete(state_key)
                return {"answer": "A confirmação anterior expirou ou o resumo original não está disponível. Nenhuma reserva foi confirmada ou alterada. Envie MENU para preparar um novo pedido.", "step": "expired"}
            state.pop("availability_query", None)
            state.pop("confirmation_requires_review", None)
            state["confirmation_message_id"] = _message_identity.get()
            await redis.set(state_key, json.dumps(state), ex=max(1, min(300, int(remaining))))
            return {"answer": "Este é o pedido anterior, sem incluir os serviços apenas consultados:\n" + summary +
                    "\nPara confirmar este resumo, responda SIM. Para desistir, NÃO.", "step": state["step"]}
        if text_clean in YES and not state.get("availability_query"):
            return {"answer": resume_prompt(state), "step": state["step"]}
    if state.get("availability_query") and text_clean in NO:
        state.pop("availability_query", None)
        await redis.set(state_key, json.dumps(state), ex=300 if state.get("step") in {"confirm", "reschedule_confirm"} else STATE_TTL)
        return {"answer": "Encerrei somente a consulta de disponibilidade. O pedido anterior não foi confirmado, cancelado ou alterado.", "step": state.get("step", "menu")}
    cancel = not catalog_question(body) and bool(re.search(
        r"\b(?:cancelar|cancele|desistir|desisto|nao quero mais agendar)\b", norm(body)))
    if cancel:
        active = state.get("step") in {"date", "professional", "time", "confirm"} or state.get("step", "").startswith("reschedule_")
        if active:
            await redis.delete(state_key)
            return {"answer": "Tudo bem, encerrei esta solicitação sem confirmar alterações. Reservas já confirmadas permanecem; para cancelar uma delas, peça atendimento humano.", "step": "cancelled"}
        return {"answer": "Para cancelar uma reserva já confirmada, posso encaminhar ao atendimento humano para conferir os dados. Nenhuma reserva foi alterada.", "step": "cancel_request"}
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
        return {"answer": "Para cancelar sua reserva, posso encaminhar ao atendimento humano para confirmar os dados.", "step": "cancel_request"}
    if intent == "my_bookings":
        if preview:
            return {"answer": "O histórico de clientes reais não é exibido na simulação.", "step": "preview_history"}
        records = await customer_appointments(tenant, phone)
        if not records:
            return {"answer": "Não encontrei reservas vinculadas a este número neste estabelecimento.", "step": "no_history"}
        lines = "\n".join(
            f"• {row['start_at'].astimezone(ZoneInfo(business['timezone'])):%d/%m/%Y às %H:%M}"
            f" · {row['status']} · protocolo {row['protocol']}"
            for row in records)
        return {"answer": "Agendamentos deste número:\n" + lines, "step": "history"}
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
            if state.get("step") in {"confirm", "reschedule_confirm"}:
                state["confirmation_requires_review"] = True
                await redis.set(state_key, json.dumps(state), ex=300)
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
            return {"answer": "Para concluir, responda SIM ou NÃO.", "step": "confirm"}
        if preview:
            await redis.delete(state_key)
            return {"answer": "Simulação concluída. Nenhum agendamento real foi criado.", "step": "preview_complete"}
        try:
            async with get_session_factory()() as db:
                await qr_intake_revision.check_before_write()
                reserved = await confirm_hold(db, tenant, UUID(state["hold"]))
        except HTTPException:
            await redis.delete(state_key)
            return {"answer": "Este horário expirou ou ficou indisponível. Escreva MENU para tentar novamente.", "step": "expired"}
        await redis.delete(state_key)
        when = reserved["start_at"].astimezone(ZoneInfo(business["timezone"]))
        return {"answer": presentation.render_summary(_summary(state, when, business, catalog,
                    protocol=reserved["protocol"], total_cents=reserved["total_cents"]), _preferences(business), confirmed=True),
                "step": "confirmed", "protocol": reserved["protocol"]}
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
            return {"answer": "Escolha um dos horários disponíveis pelo número ou horário (ex.: 14:00).", "step": "time"}
        return await prepare_booking_summary(tenant, phone, state, when, business, catalog, redis, state_key, preview)
    if state.get("step") == "professional":
        candidates = state.get("professionals", [])
        selected = professional_choice(body, candidates)
        if selected is None:
            return {"answer": "Qual profissional prefere? Pode responder pelo nome ou número indicado.", "step": "professional"}
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
        return {"answer": catalog_menu(business, catalog, greet), "step": "menu"}
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
        return {"answer": "Escolha o número de um serviço listado.", "step": "menu"}
    await redis.set(state_key, json.dumps({"step": "menu",
        "menu_ids": [str(item["id"]) for item in catalog[:12]],
        "customer_name": state.get("customer_name")}), ex=STATE_TTL)
    return {"answer": catalog_menu(business, catalog,
            "A IA está temporariamente indisponível; vou usar o menu. " if degraded else ""),
            "step": "menu", **({"degraded": True} if degraded else {})}


async def _require_pending_review(redis, key: str):
    """A summary that was not delivered cannot authorize the next acceptance."""
    try:
        state = json.loads(await redis.get(key) or "null")
    except (TypeError, ValueError):
        state = None
    if isinstance(state, dict) and state.get("step") in {"confirm", "reschedule_confirm", "cancel_confirm"}:
        state["confirmation_requires_review"] = True
        state["confirmation_message_id"] = None
        try:
            remaining = float(state.get("confirmation_expires_at", 0)) - datetime.now().timestamp()
        except (TypeError, ValueError):
            remaining = 0
        ttl = max(1, min(300, int(remaining))) if math.isfinite(remaining) else 1
        await redis.set(key, json.dumps(state), ex=ttl)
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
    redis = from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"),
                     socket_connect_timeout=3, socket_timeout=20)
    lock = redis.lock(f"orion:qr:ai:lock:{tenant}:{namespace}", timeout=90, blocking_timeout=4)
    revision_token = None
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
                if require_summary_review and not preview:
                    await _require_pending_review(redis, f"orion:qr:ai:{tenant}:{namespace}")
                if reset:
                    await redis.delete(f"orion:qr:ai:{tenant}:{namespace}")
                transcript = await prepare() if prepare else None
                if transcript is not None:
                    body = transcript["text"]
                identity_token = _message_identity.set(message_id)
                try:
                    result = await turn(tenant, phone, body, redis, namespace, preview=preview)
                    await qr_intake_revision.assert_current()
                finally:
                    _message_identity.reset(identity_token)
                if transcript is not None:
                    result = {**result, "transcript": body, "input_kind": "audio"}
            except qr_intake_revision.Superseded as superseded:
                result = superseded.result()
                if superseded.not_committed:
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
    return await run_locked(tenant, payload.phone, payload.text, payload.phone, payload.message_id,
                            message_ids=payload.message_ids, intake_revision=payload.intake_revision,
                            require_summary_review=payload.require_summary_review)


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
    await resolve_tenant_access(actor, tenant_id)
    snapshot = await qr_profile.resolve_preview(tenant_id, payload.profile_version, payload.style_rules)
    token = qr_profile.preview_snapshot.set(snapshot)
    try:
        business, catalog, professionals = await tenant_data(tenant_id)
        base = business.get("profile") or {"style_rules": StyleRules().model_dump()}
        examples = []
        for style, label in STYLE_LABELS.items():
            prefs = {**base["style_rules"], "style": style}
            answer = qr_profile.quote(catalog[:2], {"style_rules": prefs}, "Quero apenas saber o preço") if catalog else "Ainda não há serviços ativos cadastrados."
            examples.append({"style": style, "label": label, "answer": answer})
        return {"examples": examples, "catalog_revision": qr_profile.catalog_revision(business, catalog, professionals),
                "profile_version": business.get("profile_id")}
    finally:
        qr_profile.preview_snapshot.reset(token)
