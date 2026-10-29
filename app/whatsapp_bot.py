"""Factual Meta conversation scoped by the signed tenant/channel/customer.

The shared planner/formatter never authorizes a write. A hold is confirmed only
after a complete fresh summary and a different authenticated inbound message.
"""
import hashlib
import json
import re
from datetime import date, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import text

from app.booking_service import book_appointment, confirm_hold, list_slots
from app.database import get_session_factory, tenant_transaction
from app import qr_presentation as presentation
from app.qr_dialogue import choose_professional, choose_time
from app.qr_understanding import build_plan
from app.whatsapp_commands import try_command
from app.whatsapp_dialogue import checked, consent, owned_booking, preferences, summary
from app.communication_events import summary_was_accepted

TTL = 1800


def state_key(tenant: str, channel: str, sender: str) -> str:
    return f"wa:state:{tenant}:{channel}:{sender}"


def number_choice(value: str, upper: int) -> int | None:
    if value.isdigit() and 1 <= int(value) <= upper:
        return int(value)-1
    return None


async def reply(tenant: UUID, channel: UUID, sender: str, body: str, redis, *, message_id: str | None = None) -> str | None:
    from app.inbox_control import assistant_turn
    async with assistant_turn(tenant, channel, sender, redis, state_key(str(tenant), str(channel), sender)) as authority:
        if authority is False:
            return None
        return await _scoped_reply(tenant, channel, sender, body, redis, message_id=message_id)


async def _scoped_reply(tenant, channel, sender, body, redis, *, message_id=None):
    key = state_key(str(tenant), str(channel), sender)
    state = json.loads(await redis.get(key) or "{}")
    if message_id and state.get("last_message_id") == message_id and "last_answer" in state:
        return state["last_answer"]
    prefs = await preferences(tenant)
    try:
        answer = await _reply(tenant, channel, sender, body, redis, key, state, prefs, message_id)
    except (ValueError, HTTPException):
        answer = "Não consegui preparar um resumo completo e seguro. Pode indicar quais itens você quer revisar primeiro?"
    if answer is not None:
        # Preserve the exact result on worker retry. An intervening answer also
        # invalidates freshness of consent to an earlier summary.
        current = json.loads(await redis.get(key) or '{"step":"done"}')
        current.update(last_message_id=message_id, last_answer=answer)
        await redis.set(key, json.dumps(current), ex=TTL)
    return answer


async def _reply(tenant, channel, sender, body, redis, key, state, prefs, message_id):
    user_text = body.strip().lower()
    from app.contact_preferences import handle_optout,birthday_turn,offer_birthday
    optout=await handle_optout(tenant,sender,body)
    if optout is not None:
        await redis.delete(key+':birthday-invite')
        return optout
    from app.appointment_confirmation import confirmation_reply
    confirmation=await confirmation_reply(tenant,sender,body,str(channel),message_id)
    if confirmation is not None:return confirmation or None
    birthday=await birthday_turn(tenant,sender,body,redis,key)
    if birthday:
        if birthday.get('remainder'):
            following=await _reply(tenant,channel,sender,birthday['remainder'],redis,key,state,prefs,message_id)
            combined=birthday['answer']+('\n\n'+following if following else '')
            return checked(combined)
        return birthday['answer']
    from app.inbox_control import enabled,customer_handoff
    from app.qr_behaviors import control_intent
    intent=control_intent(body)
    if enabled() and intent in {'human','human_clarify'}:
        # A transfer request wins over booking/cancellation commands in the
        # same message. Reuse QR's negation/ambiguity-aware interpretation.
        if intent=='human':
            await customer_handoff(tenant,channel,sender)
            return None
        return 'Você quer falar com uma pessoa da equipe ou continuar com a assistente?'
    handled, answer = await try_command(tenant, channel, sender, body, state, redis, key,
        message_id=message_id, preferences=prefs)
    if handled:
        return answer
    async with get_session_factory()() as db:
        async with tenant_transaction(db, tenant):
            inactive = (await db.execute(text("SELECT 1 FROM subscriptions WHERE tenant_id=:t AND status IN ('suspended','canceled') LIMIT 1"), {"t": tenant})).first()
            settings = (await db.execute(text("""SELECT c.bot_enabled AND t.bot_enabled AS enabled,t.timezone
                FROM whatsapp_channels c JOIN tenants t ON t.id=c.tenant_id
                WHERE c.tenant_id=:t AND c.id=:c"""), {"t": tenant, "c": channel})).mappings().first()
            catalog = [dict(x) for x in (await db.execute(text("""SELECT id,name,duration_min,price_cents,price_mode
                FROM services WHERE tenant_id=:t AND active
                  AND catalog_kind IN ('base','combo') ORDER BY name,id LIMIT 60"""), {"t": tenant})).mappings()]
    if inactive or not settings or not settings["enabled"]:
        return None
    if user_text in {"falar com humano", "falar com barbeiro", "atendente", "quero uma pessoa", "falar com a equipe"}:
        from app.inbox_control import enabled, customer_handoff
        if enabled():
            await customer_handoff(tenant, channel, sender)
            return None
        state.update(step="human")
        state.pop("summary_message_id", None)
        await redis.set(key, json.dumps(state), ex=TTL)
        async with get_session_factory()() as db:
            async with tenant_transaction(db, tenant):
                await db.execute(text("""INSERT INTO human_handoffs
                    (tenant_id,channel_id,contact_e164,status,expires_at)
                    VALUES (:t,:c,:phone,'active',now()+interval '30 minutes')
                    ON CONFLICT (tenant_id,channel_id,contact_e164) DO UPDATE
                    SET status='active',expires_at=now()+interval '30 minutes'"""), {"t": tenant,"c": channel,"phone": sender})
        return presentation.render_handoff(prefs)
    if state.get("step") == "human":
        return None
    if not catalog:
        return "Ainda não há serviços ativos cadastrados para este estabelecimento."
    zone = settings["timezone"]
    plan = build_plan(message=body, catalog=catalog, context={**state,"service_ids":state.get("services",[])},
        reference_date=datetime.now(ZoneInfo(zone)).date())

    async def save():
        await redis.set(key, json.dumps(state), ex=TTL)

    def labels():
        return [item["name"] for item in catalog if str(item["id"]) in state.get("services", [])]

    def price_kind(ids):
        chosen={str(x) for x in ids}
        return "from" if any(row.get("price_mode")=="from" for row in catalog
                             if str(row["id"]) in chosen) else "fixed"

    def menu():
        displayed = catalog[:8]
        quote = presentation.render_quote([presentation.QuoteItem(item["name"], item["price_cents"], duration_min=item["duration_min"],price_kind=item.get("price_mode","fixed")) for item in displayed], prefs)
        return checked(quote + "\n\n" + presentation.render_prompt("service", prefs))

    async def professionals(ids):
        async with get_session_factory()() as db:
            async with tenant_transaction(db,tenant):
                return [dict(x) for x in (await db.execute(text("""SELECT p.id,p.name FROM professionals p
                    WHERE p.tenant_id=:t AND p.active AND NOT EXISTS(
                    SELECT 1 FROM unnest(CAST(:ids AS uuid[])) AS required(service_id) WHERE NOT EXISTS(
                    SELECT 1 FROM professional_services x WHERE x.tenant_id=p.tenant_id AND x.professional_id=p.id AND x.service_id=required.service_id))
                    ORDER BY p.name LIMIT 30"""),{"t":tenant,"ids":ids})).mappings()]

    if "price" in plan.intents:
        subjects = set(plan.query_service_ids)
        selected = [item for item in catalog if item["id"] in subjects] if subjects else [item for item in catalog if str(item["id"]) in state.get("services", [])]
        if not selected:
            return presentation.render_prompt("service", prefs)
        return checked(presentation.render_quote([presentation.QuoteItem(item["name"],item["price_cents"],duration_min=item["duration_min"],price_kind=item.get("price_mode","fixed")) for item in selected],prefs,
            context=presentation.InteractionContext.from_message(body,information_only=True)))

    explicit_query = "?" in body or bool(re.search(r"\b(?:tem|horários?|horarios?|dispon\w*|consult\w*|livres?)\b",user_text))
    if "availability" in plan.intents and "booking" not in plan.intents and (
            explicit_query or state.get("step") not in {"date","time","adjustment","confirm"}):
        ids=[str(item) for item in (plan.query_service_ids or plan.service_ids)] or state.get("services",[])
        if not ids:
            return presentation.render_prompt("availability_service",prefs)
        pros=await professionals(ids)
        chosen=choose_professional(body,pros) or next((x for x in pros if str(x["id"])==state.get("professional")),None)
        if chosen is None and len(pros)==1:
            chosen=pros[0]
        if chosen is None:
            return checked(presentation.render_prompt("availability_professional",prefs,details=[x["name"] for x in pros]))
        day=plan.requested_date or state.get("day")
        if not day:
            return presentation.render_prompt("availability_day",prefs)
        async with get_session_factory()() as db:
            available=await list_slots(db,tenant,chosen["id"],[UUID(x) for x in ids],date.fromisoformat(day))
        return checked(presentation.render_slots([x["name"] for x in catalog if str(x["id"]) in ids],available["slots"][:8],prefs,
            timezone=zone,day=day,total_cents=available["price_cents"],duration_min=available["duration_min"],professional=chosen["name"],ask_selection=False,
            price_kind=price_kind(ids)))

    if state.get("step") == "confirm":
        known=await owned_booking(tenant,sender,item_id=state.get('hold'),statuses=('hold','confirmed')) if state.get('hold') else None
        if known and known.get('status')=='confirmed':
            await redis.delete(key)
            return checked(presentation.render_summary(summary(known),prefs,confirmed=True))
        decision = await consent(body, pending=True, semantic_allowed=bool(message_id and state.get("summary_message_id")
            and state.get("last_message_id")==state["summary_message_id"] and message_id!=state["summary_message_id"]))
        if decision.kind == "decline":
            state.update(step="adjustment")
            state.pop("summary_message_id", None)
            await save()
            return "Tudo bem, ainda não confirmei esse agendamento. " + presentation.render_prompt("adjustment", prefs)
        if decision.kind == "affirm":
            if not message_id or message_id == state.get("summary_message_id"):
                return "Preciso de uma nova resposta depois do resumo para confirmar."
            hold = known
            if not hold:
                state.update(step="date")
                state.pop("summary_message_id",None)
                await save()
                return presentation.render_prompt("expired",prefs)
            if (not state.get('summary_message_id') or state.get("last_message_id") != state["summary_message_id"] or not state.get("expected_snapshot")
                    or state.get('confirmation_requires_review')
                    or not await summary_was_accepted(tenant,channel,sender,state)):
                answer=checked(presentation.render_summary(summary(hold),prefs))
                state.update(summary_message_id=message_id,expected_snapshot=hold["expected_snapshot"])
                state.pop('confirmation_requires_review',None)
                await save()
                return answer
            try:
                checked(presentation.render_summary(summary(hold),prefs,confirmed=True))
                async with get_session_factory()() as db:
                    confirmed=await confirm_hold(db,tenant,hold["id"],customer_phone=sender,
                        expected_snapshot=state["expected_snapshot"],source="whatsapp_meta")
            except HTTPException:
                state.update(step="date")
                state.pop("summary_message_id",None)
                await save()
                return presentation.render_prompt("context_changed",prefs)
            confirmed.update(timezone=hold["timezone"],professional=hold["professional"])
            await redis.delete(key)
            answer=presentation.render_summary(summary(confirmed),prefs,confirmed=True)
            from app.contact_preferences import BIRTHDAY_INVITATION
            if presentation.fits_message(answer+'\n\n'+BIRTHDAY_INVITATION):
                try:invitation=await offer_birthday(tenant,sender,redis,key,confirmed['protocol'])
                except Exception:invitation=''
                if invitation:answer+='\n\n'+invitation
            return answer
        if decision.kind != "amend":
            return presentation.render_prompt("confirmation_review",prefs)
        state.pop("summary_message_id",None)

    selected = [str(item) for item in plan.service_ids]
    if selected and not plan.requires_clarification:
        state.update(services=selected,step="professional")
        for field in ("hold","summary_message_id","expected_snapshot","slots","professional"):
            state.pop(field,None)
    elif state.get("step") == "services":
        # Legacy numbers remain accepted for old conversations, never required.
        choices=[number_choice(x.strip(),len(state.get("offered_services",[]))) for x in user_text.split(",")]
        if choices and None not in choices and len(set(choices))==len(choices):
            state.update(services=[state["offered_services"][i] for i in choices],step="professional")
        else:
            return presentation.render_prompt("service",prefs)
    if not state.get("services") or user_text in {"menu","início","inicio"} or state.get("step") == "done":
        state.update(step="services",offered_services=[str(x["id"]) for x in catalog[:8]])
        await save()
        return menu()
    if any(value != "time" for value in plan.ambiguities):
        await save()
        return presentation.render_prompt("interpretation_error",prefs)

    if state.get("step") == "professional":
        pros=await professionals(state["services"])
        if not pros:
            state.update(step="services")
            await save()
            return "Não há profissional disponível para essa combinação. " + presentation.render_prompt("service",prefs)
        chosen=choose_professional(body,pros)
        if chosen is None and len(pros)==1:
            chosen=pros[0]
        if chosen is None:
            await save()
            return checked(presentation.render_prompt("professional",prefs,services=labels(),details=[x["name"] for x in pros]))
        state.update(professional=str(chosen["id"]),professional_name=chosen["name"],step="date")
    if plan.requested_date:
        state.update(day=plan.requested_date,step="time")
    if state.get("step") in {"date","adjustment","confirm"}:
        if state.get("day") and plan.requested_time:
            state.update(step="time")
        else:
            await save()
            return presentation.render_prompt("date",prefs,services=labels(),professional=state.get("professional_name"))
    if state.get("step") == "time":
        try:
            async with get_session_factory()() as db:
                available=await list_slots(db,tenant,UUID(state["professional"]),[UUID(x) for x in state["services"]],date.fromisoformat(state["day"]))
            clock_text="às "+plan.requested_time if plan.requested_time and plan.time_relation=="exact" else body
            choice=choose_time(clock_text,available["slots"],presented_slots=state.get("slots"),timezone=zone)
            if choice.kind != "selected":
                state["slots"]=list(choice.alternatives or available["slots"])[:8]
                await save()
                return checked(presentation.render_slots(labels(),state["slots"],prefs,timezone=zone,day=state["day"],
                    total_cents=available["price_cents"],duration_min=available["duration_min"],professional=state.get("professional_name"),
                    price_kind=price_kind(state["services"])))
            if not message_id:
                return "Preciso receber sua escolha em uma nova mensagem antes de preparar a reserva."
            candidate={"services":labels(),"start_at":choice.when,"timezone":zone,"total_cents":available["price_cents"],
                       "price_mode":price_kind(state["services"]),
                       "duration_min":available["duration_min"],"professional":state.get("professional_name")}
            checked(presentation.render_summary(summary(candidate),prefs))
            # Reserve room for the real protocol before creating a hold.
            checked(presentation.render_summary(summary({**candidate,"protocol":"AG-XXXXXX"}),prefs,confirmed=True))
            async with get_session_factory()() as db:
                hold=await book_appointment(db,tenant,UUID(state["professional"]),[UUID(x) for x in state["services"]],sender,
                    "Cliente WhatsApp",datetime.fromisoformat(choice.when),"meta-"+hashlib.sha256((str(channel)+sender+message_id).encode()).hexdigest(),
                    source="whatsapp_meta")
            current=await owned_booking(tenant,sender,item_id=hold["id"],statuses=("hold","confirmed"))
            if not current:
                raise HTTPException(409,"Hold unavailable")
            answer=checked(presentation.render_summary(summary(current),prefs))
            state.update(step="confirm",hold=str(hold["id"]),summary_message_id=message_id,expected_snapshot=current["expected_snapshot"])
            await save()
            return answer
        except HTTPException:
            state.update(step="date")
            state.pop("summary_message_id",None)
            await save()
            return presentation.render_prompt("unavailable",prefs)
    return presentation.render_prompt("adjustment",prefs)
