"""Motor conversacional ligado ao WhatsApp QR, autenticado e isolado por tenant.

O LLM apenas classifica; agenda, preço, profissional e confirmação vêm do Orion.
O preview usa estado isolado e nunca grava reserva nem envia mensagens externas.
"""
import hashlib
import json
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
from app.groq_ai import GroqServiceError, interpret
from app.qr_customer import supplied_name, customer_name, remember_name, customer_appointments, appointment_services
from app.qr_behaviors import control_intent, catalog_match

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
        state.update(step="reschedule_confirm", new_start=chosen)
        await redis.set(key, json.dumps(state), ex=300)
        return {"answer": (f"Remarcar para {datetime.fromisoformat(chosen):%d/%m às %H:%M}? "
                           "Responda SIM ou NÃO."), "step": "reschedule_confirm"}
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


async def turn(tenant: UUID, phone: str, body: str, redis, namespace: str, preview: bool = False):
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
    business, catalog, _ = await tenant_data(tenant)
    if not catalog:
        return {"answer": "Ainda não há serviços ativos cadastrados neste estabelecimento.", "step": "no_catalog"}
    intent = control_intent(body)
    supplied = supplied_name(body)
    if supplied:
        if not preview:
            await remember_name(tenant, phone, supplied)
        state["customer_name"] = supplied
        await redis.set(state_key, json.dumps(state), ex=STATE_TTL)
        if not is_service_request(body):
            return {"answer": f"Prazer, {supplied}! Posso ajudar com serviços ou agendamentos?", "step": "named"}
    if intent == "human":
        await redis.set(state_key, json.dumps({"step": "human"}), ex=1800)
        return {"answer": "Vou deixar esta conversa para atendimento humano. Para retomar o bot, envie MENU.", "step": "human"}
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
        summary = (f"Resumo: {datetime.fromisoformat(when):%d/%m às %H:%M}, "
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
        await redis.set(state_key, json.dumps(state), ex=300)
        return {"answer": summary + "\nPosso confirmar? Responda SIM ou NÃO.", "step": "confirm"}
    if state.get("step") == "professional":
        candidates = state.get("professionals", [])
        index = int(text_clean) - 1 if text_clean.isdigit() else -1
        if not (0 <= index < len(candidates)):
            index = next((i for i, p in enumerate(candidates) if p["name"].lower() == text_clean), -1)
        if index == -1:
            return {"answer": "Qual profissional prefere? Responda com o número indicado.", "step": "professional"}
        state["professional"] = candidates[index]["id"]
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
    decision = None
    try:
        decision = await interpret(
            message=body, history=state.get("history", []),
            catalog=catalog, key=os.getenv("GROQ_API_KEY", ""),
            model=os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"),
        )
    except GroqServiceError as exc:
        import logging
        logging.getLogger("orion.groq").warning("classification_failed status=%s", exc.status)
        matched = catalog_match(body, catalog)
        if matched:
            from app.groq_ai import Intention
            decision = Intention(intent="agendar", service_ids=matched, requested_when=body[:120])
        else:
            await redis.set(state_key, json.dumps({"step": "menu",
                "menu_ids": [str(item["id"]) for item in catalog[:12]]}), ex=STATE_TTL)
            return {"answer": catalog_menu(business, catalog,
                    "A IA está temporariamente indisponível; vou usar o menu. "),
                    "step": "menu", "degraded": True}
    if decision.intent == "humano":
        await redis.set(state_key, json.dumps({"step": "human"}), ex=STATE_TTL)
        return {"answer": "Certo! Vou deixar esta conversa para atendimento humano. Nossa equipe poderá responder por aqui.", "step": "human"}
    if decision.service_ids:
        state = {"services": [str(x) for x in decision.service_ids], "step": "date",
                 "customer_name": state.get("customer_name")}
        total = sum(item["price_cents"] for item in catalog if item["id"] in decision.service_ids)
        duration = sum(item["duration_min"] for item in catalog if item["id"] in decision.service_ids)
        names = " e ".join(item["name"] for item in catalog if item["id"] in decision.service_ids)
        state["history"] = [{"role": "user", "content": body}]
        chosen_day = requested_day(
            " ".join(filter(None, [body, decision.requested_when or ""])), business["timezone"]
        )
        if decision.intent == "agendar":
            if chosen_day:
                state["day"] = chosen_day.isoformat()
                pros = await professionals_for(tenant, decision.service_ids)
                if not pros:
                    return {"answer": "Não há profissional habilitado para esses serviços.", "step": "not_available"}
                if len(pros) == 1:
                    state["professional"] = str(pros[0]["id"])
                    answer, state = await offer_slots(tenant, state, preview)
                    await redis.set(state_key, json.dumps(state), ex=STATE_TTL)
                    return {"answer": (f"{names}: {format_money(total)} e {duration} minutos.\n" + answer),
                            "step": state["step"]}
                state["professionals"] = [{"id": str(p["id"]), "name": p["name"]} for p in pros]
                state["step"] = "professional"
                await redis.set(state_key, json.dumps(state), ex=STATE_TTL)
                return {"answer": f"{names}: {format_money(total)} e {duration} minutos.\nQual profissional prefere?\n" +
                        "\n".join(f"{i}. {p['name']}" for i, p in enumerate(pros, 1)), "step": "professional"}
            await redis.set(state_key, json.dumps(state), ex=STATE_TTL)
            return {"answer": f"{names}: {format_money(total)}, {duration} minutos. Para qual dia gostaria de agendar?", "step": "date"}
        await redis.set(state_key, json.dumps(state), ex=STATE_TTL)
        return {"answer": f"{names}: {format_money(total)}, duração aproximada {duration} minutos. Para agendar, informe o dia desejado.", "step": "date"}
    await redis.set(state_key, json.dumps({"step": "menu",
        "menu_ids": [str(item["id"]) for item in catalog[:12]]}), ex=STATE_TTL)
    return {"answer": catalog_menu(business, catalog), "step": "menu"}


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
