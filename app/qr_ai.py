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
from datetime import date, datetime, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field
from sqlalchemy import text

from app.booking_service import book_appointment, confirm_hold, list_slots, reschedule_appointment
from app.catalog_api import Actor, authenticate, authorize, support_only
from app.catalog_schemas import StrictModel
from app.database import get_session_factory, tenant_transaction
from app.groq_ai import GroqServiceError, Intention, interpret
from app.qr_customer import supplied_name, customer_name, remember_name, customer_appointments, appointment_services
from app.qr_behaviors import control_intent, catalog_match, norm, catalog_question_services, availability_question

router = APIRouter(tags=["Orion IA conversacional"])
STATE_TTL = 7 * 24 * 3600  # contexto temporário até 7 dias; agendamentos ficam no PostgreSQL
PHONE_RE = re.compile(r"^55[1-9][0-9]{9,10}$")
TIME_RE = re.compile(r"(?<!\d)([01]?\d|2[0-3])(?:[:h]([0-5]\d))?(?!\d)")
YES = {"sim", "s", "confirmo", "pode confirmar", "confirmar", "ok pode confirmar"}
NO = {"não", "nao", "n", "cancelar", "desistir"}
RESET = {"menu", "reiniciar", "começar", "recomecar", "recomeçar"}


class Incoming(StrictModel):
    phone: str = Field(pattern=r"^55[1-9][0-9](?:9[0-9]{8}|[2-5][0-9]{7})$")
    message_id: str = Field(min_length=1, max_length=160, pattern=r"^[a-zA-Z0-9._:-]+$")
    text: str = Field(min_length=1, max_length=1500)


class Simulate(StrictModel):
    session: str = Field(min_length=8, max_length=64, pattern=r"^[a-zA-Z0-9-]+$")
    text: str = Field(min_length=1, max_length=1500)
    reset: bool = False


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
    return dict(business), [dict(row) for row in services], [dict(row) for row in pros]


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
    listing = "\n".join(f"{i}. {datetime.fromisoformat(when):%H:%M}" for i, when in enumerate(options, 1))
    return (f"Encontrei estes horários livres para {date.fromisoformat(state['day']):%d/%m}:\n"
            f"{listing}\nQual horário prefere?"), state


async def begin_reschedule(tenant: UUID, phone: str, business: dict, redis, key: str, preview: bool):
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
        summary = f"Remarcar para {datetime.fromisoformat(chosen):%d/%m às %H:%M}?"
        state.update(step="reschedule_confirm", new_start=chosen, confirmation_summary=summary,
                     confirmation_expires_at=datetime.now().timestamp() + 300)
        await redis.set(key, json.dumps(state), ex=300)
        return {"answer": summary + " Responda SIM ou NÃO.", "step": "reschedule_confirm"}
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
        try:
            async with get_session_factory()() as s:
                updated = await reschedule_appointment(
                    s, tenant, UUID(state["appointment"]),
                    datetime.fromisoformat(state["new_start"]))
        except HTTPException:
            await redis.delete(key)
            return {"answer": "O novo horário ficou indisponível. O original permanece. Escreva REMARCAR para tentar novamente.", "step": "reschedule_conflict"}
        await redis.delete(key)
        when = updated["start_at"].astimezone(ZoneInfo(business["timezone"]))
        return {"answer": f"Remarcação confirmada! {when:%d/%m às %H:%M}. Protocolo {updated['protocol']}.", "step": "rescheduled"}
    return {"answer": "Escreva REMARCAR para escolher um novo horário.", "step": "unknown"}


def catalog_menu(business: dict, catalog: list[dict], greeting: str = "") -> str:
    lines = "\n".join(
        f"{n}. {item['name']} — {format_money(item['price_cents'])} ({item['duration_min']} min)"
        for n, item in enumerate(catalog[:12], 1))
    return f"{greeting}Bem-vindo à {business['name']}!\n{lines}\nQual serviço deseja?"


def is_service_request(value: str) -> bool:
    return any(word in value.lower() for word in ("quero", "agendar", "preciso", "gostaria", "reservar"))


def catalog_question(value: str) -> bool:
    return bool(re.search(
        r"\b(?:quanto\s+(?:que\s+)?(?:custa|custam|fica|ficam|e|sai|tempo)|"
        r"(?:custa|custam|fica|ficam|sai)\s+quanto|precos?|valores?|"
        r"duracao|demora|quais\s+(?:os\s+)?servicos|quais\s+servicos\s+tem)\b", norm(value)))


def professional_choice(value: str, candidates: list[dict]) -> dict | None:
    clean = norm(value)
    if clean.isdigit():
        index = int(clean) - 1
        return candidates[index] if 0 <= index < len(candidates) else None
    words = set(re.findall(r"\w+", clean)) - {
        "o", "a", "os", "as", "com", "quero", "prefiro", "pode", "ser", "por", "favor", "profissional"}
    matches = [p for p in candidates if words and words <= set(re.findall(r"\w+", norm(p["name"])))]
    return matches[0] if len(matches) == 1 else None


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
    if state.get("step") in {"confirm", "reschedule_confirm"}:
        state["confirmation_requires_review"] = True
    inquiry = dict(state.get("availability_query", {}))
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
    elif re.search(r"\b(?:que|qual|quais|proximos?)(?: sao)?(?: os?)?\s+dias?\b", norm(body)):
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
        try:
            async with asyncio.timeout(12), get_session_factory()() as db:
                for offset in range(7):
                    candidate_day = today + timedelta(days=offset)
                    result = await list_slots(db, tenant, UUID(inquiry["professional"]),
                                              [UUID(item) for item in ids], candidate_day)
                    times = [datetime.fromisoformat(item).strftime("%H:%M") for item in result["slots"][:6]]
                    if times:
                        found.append(f"• {candidate_day:%d/%m}: " + ", ".join(times))
                    if len(found) == 3:
                        break
        except (HTTPException, TimeoutError):
            return await ask("Não consegui verificar a agenda completa agora. Qual data deseja consultar?", "date")
        state.pop("availability_query", None)
        await redis.set(key, json.dumps(state), ex=300 if state.get("step") in {"confirm", "reschedule_confirm"} else STATE_TTL)
        heading = f"Consultei {names} com {selected['name']} nos próximos sete dias."
        answer = heading + ("\n" + "\n".join(found) if found else " Não encontrei horários livres nesse período.")
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
    await redis.set(key, json.dumps(state), ex=300 if state.get("step") in {"confirm", "reschedule_confirm"} else STATE_TTL)
    times = [datetime.fromisoformat(item).strftime("%H:%M") for item in result["slots"][:12]]
    heading = f"Disponibilidade para {names} com {selected['name']} em {date.fromisoformat(chosen_day):%d/%m}: "
    answer = heading + (", ".join(times) + "." if times else "não encontrei horários livres.")
    review = "\n" + resume_prompt(state) if state.get("confirmation_requires_review") else ""
    return {"answer": answer + " Consulta sem reserva; nenhum agendamento foi criado ou alterado." + review,
            "step": state.get("step", "menu")}


async def start_booking(tenant: UUID, body: str, decision: Intention, state: dict,
                        business: dict, catalog: list[dict], redis, key: str, preview: bool):
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
    result = await _turn(tenant, phone, body, redis, namespace, preview)
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
                ttl = max(1, min(300, int(deadline - now)))
            else:
                ttl = 1800 if current.get("step") == "human" else STATE_TTL
            await redis.set(key, json.dumps(current), ex=ttl)
    return result


async def _turn(tenant: UUID, phone: str, body: str, redis, namespace: str, preview: bool = False):
    state_key = f"orion:qr:ai:{tenant}:{namespace}"
    text_clean = body.strip().lower()
    if text_clean in RESET:
        await redis.delete(state_key)
        state = {}
    else:
        try:
            state = json.loads(await redis.get(state_key) or "{}")
        except (TypeError, ValueError):
            state = {}
    if not isinstance(state, dict):
        state = {}
    business, catalog, _ = await tenant_data(tenant)
    intent = control_intent(body)
    if state.get("step") == "human":
        return {"answer": None, "step": "human"}
    if intent == "human":
        await redis.set(state_key, json.dumps({"step": "human"}), ex=1800)
        return {"answer": "Vou deixar esta conversa para atendimento humano. Para retomar o bot, envie MENU.", "step": "human"}
    if intent == "identity":
        return {"answer": "Sou o Orion, assistente virtual deste atendimento.", "step": state.get("step", "information")}
    if state.get("confirmation_requires_review") and state.get("step") in {"confirm", "reschedule_confirm"}:
        resume = norm(body) in {"retomar resumo", "ver resumo", "rever resumo", "resumo original"}
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
        return await begin_reschedule(tenant, phone, business, redis, state_key, preview)
    querying = availability_question(body)
    inquiry_reply = state.get("availability_query") and not (
        catalog_question(body) or
        re.search(r"\b(?:agendar|marcar|reservar)\b", norm(body)))
    if querying or inquiry_reply:
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
            if not state:
                state = {"step": "menu", "menu_ids": [str(item["id"]) for item in catalog[:12]]}
                await redis.set(state_key, json.dumps(state), ex=STATE_TTL)
            return {"answer": answer + "\n\n" + resume_prompt(state), "step": state.get("step", "catalog"),
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
                reserved = await confirm_hold(db, tenant, UUID(state["hold"]))
        except HTTPException:
            await redis.delete(state_key)
            return {"answer": "Este horário expirou ou ficou indisponível. Escreva MENU para tentar novamente.", "step": "expired"}
        await redis.delete(state_key)
        when = reserved["start_at"].astimezone(ZoneInfo(business["timezone"]))
        return {"answer": (f"Agendamento confirmado para {when:%d/%m às %H:%M}! "
                f"Total: {format_money(reserved['total_cents'])}. Protocolo: {reserved['protocol']}."),
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
        chosen_names = " e ".join(item["name"] for item in catalog if str(item["id"]) in state["services"])
        professional_name = state.get("professional_name") or next((p["name"] for p in state.get("professionals", [])
            if p["id"] == state.get("professional")), None)
        who = f" com {professional_name}" if professional_name else ""
        summary = (f"Resumo: {chosen_names}{who}, {datetime.fromisoformat(when):%d/%m às %H:%M}, "
                   f"{state['duration']} minutos, {format_money(state['price'])}.")
        if preview:
            state.update(step="confirm", hold="preview")
        else:
            idem = hashlib.sha256(
                f"qr:{tenant}:{phone}:{when}:{','.join(state['services'])}".encode()
            ).hexdigest()
            try:
                selected_name = state.get("customer_name") or (None if preview else await customer_name(tenant, phone)) or "Cliente WhatsApp"
                async with get_session_factory()() as db:
                    hold = await book_appointment(
                        db, tenant, UUID(state["professional"]),
                        [UUID(x) for x in state["services"]],
                        "+" + phone, selected_name, datetime.fromisoformat(when), "qr-" + idem
                    )
            except HTTPException:
                await redis.delete(state_key)
                return {"answer": "O horário não está mais disponível. Escreva MENU para tentar novamente.", "step": "conflict"}
            state.update(step="confirm", hold=str(hold["id"]))
        state["confirmation_summary"] = summary
        state["confirmation_expires_at"] = datetime.now().timestamp() + 300
        await redis.set(state_key, json.dumps(state), ex=300)
        return {"answer": summary + "\nPosso confirmar? Responda SIM ou NÃO.", "step": "confirm"}
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
        greet = f"Olá, {display}! " if display else "Olá! "
        await redis.set(state_key, json.dumps({"step": "menu",
            "menu_ids": [str(item["id"]) for item in catalog[:12]],
            "customer_name": display}), ex=STATE_TTL)
        return {"answer": catalog_menu(business, catalog, greet), "step": "menu"}
    if state.get("step") == "menu" and text_clean.isdigit():
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


async def run_locked(tenant: UUID, phone: str, body: str, namespace: str,
                     message_id: str | None = None, preview: bool = False):
    from redis.asyncio import from_url
    redis = from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"),
                     socket_connect_timeout=3, socket_timeout=20)
    lock = redis.lock(f"orion:qr:ai:lock:{tenant}:{namespace}", timeout=45, blocking_timeout=4)
    try:
        async with lock:
            completed_key = f"orion:qr:ai:done:{tenant}:{message_id}" if message_id else None
            if completed_key and await redis.exists(completed_key):
                return {"answer": None, "step": "duplicate"}
            result = await turn(tenant, phone, body, redis, namespace, preview=preview)
            if completed_key:
                await redis.set(completed_key, "1", ex=604800, nx=True)
            return result
    finally:
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
    return await run_locked(tenant, payload.phone, payload.text, payload.phone, payload.message_id)


@router.post("/api/v1/tenants/{tenant_id}/ai/conversation-preview")
async def conversation_preview(tenant_id: UUID, payload: Simulate,
                               actor: Actor = Depends(authenticate)):
    authorize(actor, tenant_id, write=True)
    support_only(actor)
    if os.getenv("GROQ_PREVIEW_ENABLED", "0") != "1":
        raise HTTPException(423, "Prévia desativada")
    from redis.asyncio import from_url
    key = f"orion:qr:ai:{tenant_id}:preview:{payload.session}"
    if payload.reset:
        redis = from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))
        try:
            await redis.delete(key)
        finally:
            await redis.aclose()
    return await run_locked(tenant_id, "5567999999999", payload.text,
                            "preview:" + payload.session, preview=True)
