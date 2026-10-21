"""Comandos de WhatsApp por identidade assinada e papel no tenant.

Recebe SOMENTE tenant/canal/sender já resolvidos por webhook Meta autenticado.
Não usa protocolo como senha; o número remetente deve pertencer à reserva.
"""
import json
import re
from datetime import date, datetime, time, timedelta, timezone
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import text

from app.booking_service import (
    acquire_professional_lock, cancel_appointment, reschedule_appointment, preview_reschedule, list_slots,
)
from app.database import get_session_factory, tenant_transaction
from app.communication_events import summary_was_accepted
from app import qr_presentation as presentation
from app.qr_dialogue import choose_time
from app.qr_understanding import build_plan, normalized
from app.whatsapp_dialogue import (
    checked, consent, summary, owned_booking, owned_future_bookings, booking_reference, booking_listing,
)

UTC = timezone.utc


async def _authorized_staff(tenant: UUID, sender: str):
    async with get_session_factory()() as s:
        async with tenant_transaction(s, tenant):
            return (await s.execute(text("""
                SELECT role, professional_id FROM authorized_staff
                WHERE tenant_id=:tenant AND phone_e164=:sender AND active
            """), {"tenant":tenant, "sender":sender})).mappings().first()


async def _owned_booking(tenant: UUID, sender: str, protocol: str):
    return await owned_booking(tenant, sender, protocol=protocol)


async def _today_staff_agenda(tenant: UUID, role: str, professional: UUID | None, tomorrow: bool):
    async with get_session_factory()() as s:
        async with tenant_transaction(s, tenant):
            tzname=(await s.execute(text("SELECT timezone FROM tenants WHERE id=:tenant"),
                                    {"tenant":tenant})).scalar_one()
            tz=ZoneInfo(tzname)
            day=datetime.now(UTC).astimezone(tz).date()+timedelta(days=int(tomorrow))
            start=datetime.combine(day,time.min,tzinfo=tz).astimezone(UTC)
            stop=datetime.combine(day+timedelta(days=1),time.min,tzinfo=tz).astimezone(UTC)
            rows=(await s.execute(text("""
                SELECT a.start_at, p.name AS professional_name, c.name AS customer_name
                FROM appointments a
                JOIN professionals p ON p.tenant_id=a.tenant_id AND p.id=a.professional_id
                JOIN customers c ON c.tenant_id=a.tenant_id AND c.id=a.customer_id
                WHERE a.tenant_id=:tenant AND a.status='confirmed'
                  AND a.start_at>=:start AND a.start_at<:stop
                  AND (:all_staff OR a.professional_id=:professional)
                ORDER BY a.start_at LIMIT 40
            """), {"tenant":tenant,"start":start,"stop":stop,
                   "all_staff":role in ("owner","assistant"),
                   "professional":professional or UUID(int=0)})).mappings().all()
    if not rows:
        return "Nenhum atendimento confirmado para " + day.strftime("%d/%m") + "."
    lines=[f"Agenda de {day:%d/%m}:"]
    for row in rows:
        local=row["start_at"].astimezone(tz)
        lines.append(f"{local:%H:%M} — {row['professional_name']} — {row['customer_name']}")
    if len(rows)==40:lines.append("Exibindo os 40 primeiros atendimentos.")
    return "\n".join(lines)


async def _staff_block(tenant: UUID, professional: UUID, daystr: str, left: str, right: str):
    """Bloqueio seguro, revalidação em transação/lock; recusa reservas confirmadas."""
    async with get_session_factory()() as s:
        async with tenant_transaction(s,tenant):
            tzname=(await s.execute(text("SELECT timezone FROM tenants WHERE id=:t"),
                                    {"t":tenant})).scalar_one()
            tz=ZoneInfo(tzname)
            try:
                day=date.fromisoformat(daystr)
                start=datetime.combine(day,time.fromisoformat(left),tzinfo=tz).astimezone(UTC)
                end=datetime.combine(day,time.fromisoformat(right),tzinfo=tz).astimezone(UTC)
            except ValueError:
                return "Data inválida. Use BLOQUEAR AAAA-MM-DD HH:MM-HH:MM."
            if (start<=datetime.now(UTC) or end<=start
                or start.astimezone(tz).date()!=day or end.astimezone(tz).date()!=day):
                return "O intervalo precisa ser futuro, válido e no mesmo dia."
            await acquire_professional_lock(s,tenant,professional)
            conflict=(await s.execute(text("""
                SELECT 1 FROM appointments WHERE tenant_id=:tenant
                AND professional_id=:professional AND occupied_from<:end
                AND occupied_until>:start AND status IN ('confirmed','hold')
                AND (status<>'hold' OR hold_expires_at>now()) LIMIT 1
            """), {"tenant":tenant,"professional":professional,
                   "start":start,"end":end})).first()
            if conflict:
                return "Já existe uma reserva nesse intervalo; o bloqueio não foi criado."
            existing=(await s.execute(text("""
                SELECT 1 FROM schedule_blocks WHERE tenant_id=:tenant
                AND professional_id=:professional AND starts_at<:end
                AND ends_at>:start LIMIT 1
            """), {"tenant":tenant,"professional":professional,
                   "start":start,"end":end})).first()
            if existing:
                return "Este intervalo já está bloqueado."
            bid=uuid4()
            await s.execute(text("""
                INSERT INTO schedule_blocks (id,tenant_id,professional_id,starts_at,ends_at,reason)
                VALUES (:id,:tenant,:professional,:start,:end,'Solicitado por staff autenticado')
            """),{"id":bid,"tenant":tenant,"professional":professional,
                  "start":start,"end":end})
            await s.execute(text("""
                INSERT INTO tenant_audit (tenant_id,action,entity,entity_id)
                VALUES (:tenant,'created','schedule_blocks',:id)
            """),{"tenant":tenant,"id":bid})
            return "Horário bloqueado: "+day.strftime("%d/%m")+" "+left+"–"+right+"."


async def try_command(
    tenant: UUID, channel: UUID, sender: str, user_text: str,
    state: dict, redis, state_key: str, *, message_id: str | None = None, preferences=None,
) -> tuple[bool, str | None]:
    """Retorna (tratado, texto). Chamado antes dos menus normais."""
    cmd=user_text.strip().lower()
    if cmd in ("sair","parar mensagens","cancelar publicidade"):
        async with get_session_factory()() as s:
            async with tenant_transaction(s,tenant):
                await s.execute(text("""
                    INSERT INTO contact_consents
                    (tenant_id,contact_e164,marketing_opt_in,source)
                    VALUES (:t,:phone,false,'whatsapp')
                    ON CONFLICT (tenant_id,contact_e164) DO UPDATE
                    SET marketing_opt_in=false,source='whatsapp',updated_at=now()
                """),{"t":tenant,"phone":sender})
        await redis.delete(state_key)
        return True,"Seu contato foi retirado das comunicações promocionais."

    async def save(value):
        value["last_message_id"] = message_id
        await redis.set(state_key,json.dumps(value,default=str),ex=300)

    async def show_bookings(records, action=None):
        if records is None:
            return True,"Há muitas reservas vinculadas a este contato. A equipe pode ajudar a localizar a reserva que você quer revisar."
        if not records:
            return True,"Não há reservas futuras confirmadas para este número."
        try:
            answer, shown = booking_listing(records,action=action)
        except HTTPException:
            return True,"Os dados das reservas precisam ser conferidos pela equipe antes de preparar uma alteração."
        if message_id:
            await save({"step":"customer_booking_pick","reference_ids":shown,"booking_action":action})
        return True,answer

    if cmd in {"meus horários","meus horarios","minhas reservas","meus agendamentos"}:
        return await show_bookings(await owned_future_bookings(tenant,sender))

    staff_cmds={"agenda hoje","agenda amanhã","agenda amanha",
                "pausar bot","retomar bot","confirmar pausa","confirmar retomada"}
    block=re.fullmatch(r"bloquear (\d{4}-\d{2}-\d{2}) (\d{2}:\d{2})-(\d{2}:\d{2})",cmd)
    staff_request=cmd in staff_cmds or block or cmd=="confirmar bloqueio"
    request=re.search(r"\b(cancelar|cancela|cancele|cancelamento|desmarcar|desmarca|desmarque|remarcar|remarca|remarque|remarcacao|reagendar|reagenda|reagende)\b",normalized(cmd))
    # A new target starts another proposal, never authorizes the previous one.
    target_request=bool(request and normalized(cmd)[request.end():].strip(" .!?"))

    async def cancellation_proposal(appt):
        answer=checked(presentation.render_summary(summary(appt),preferences,operation="cancel"))
        checked(presentation.render_summary(summary(appt),preferences,operation="cancel",confirmed=True))
        await save({"step":"customer_cancel","protocol":appt["protocol"],
                    "expected_snapshot":appt["expected_snapshot"],"summary_message_id":message_id})
        return True,answer

    async def rescheduling_proposal(appt,start):
        async with get_session_factory()() as s:
            proposal=await preview_reschedule(s,tenant,appt["id"],start,customer_phone=sender)
        answer=checked(presentation.render_summary(summary(proposal),preferences,operation="reschedule"))
        checked(presentation.render_summary(summary(proposal),preferences,operation="reschedule",confirmed=True))
        await save({"step":"customer_reschedule_confirm","protocol":appt["protocol"],
            "new_start":start.isoformat(),"expected_snapshot":proposal["expected_snapshot"],
            "summary_message_id":message_id})
        return True,answer

    pending=state.get("step") in {"customer_cancel","customer_reschedule_confirm"}
    if pending and not target_request and not staff_request:
        action="cancel" if state["step"]=="customer_cancel" else "booking"
        decision=await consent(user_text,pending=True,action=action,semantic_allowed=bool(message_id and state.get("summary_message_id")
            and state.get("last_message_id")==state["summary_message_id"] and message_id!=state["summary_message_id"]))
        appt=await _owned_booking(tenant,sender,state["protocol"])
        if not appt or appt["start_at"]<=datetime.now(UTC):
            return True,"Essa reserva não está disponível para alteração neste atendimento."
        if decision.kind=="decline":
            state.pop("summary_message_id",None)
            state.pop("expected_snapshot",None)
            state["step"]="customer_reschedule" if action!="cancel" else "customer_adjustment"
            await save(state)
            return True,"Tudo bem, a reserva original permanece. Qual detalhe você gostaria de ajustar?"
        if decision.kind=="affirm":
            if not message_id or message_id==state.get("summary_message_id"):
                return True,"Preciso de uma nova resposta depois do resumo antes de fazer essa alteração."
            try:
                if (not state.get('summary_message_id') or state.get("last_message_id")!=state["summary_message_id"] or not state.get("expected_snapshot")
                        or state.get('confirmation_requires_review')
                        or not await summary_was_accepted(tenant,channel,sender,state)):
                    return await cancellation_proposal(appt) if action=="cancel" else await rescheduling_proposal(appt,datetime.fromisoformat(state["new_start"]))
                if action=="cancel":
                    checked(presentation.render_summary(summary(appt),preferences,operation="cancel",confirmed=True))
                    async with get_session_factory()() as s:
                        updated=await cancel_appointment(s,tenant,appt["id"],customer_phone=sender,
                            expected_snapshot=state["expected_snapshot"],future_only=True,source="whatsapp_meta")
                    updated.update(timezone=appt["timezone"],professional=appt["professional"])
                else:
                    start=datetime.fromisoformat(state["new_start"])
                    async with get_session_factory()() as s:
                        proposal=await preview_reschedule(s,tenant,appt["id"],start,customer_phone=sender)
                    checked(presentation.render_summary(summary(proposal),preferences,operation="reschedule",confirmed=True))
                    async with get_session_factory()() as s:
                        updated=await reschedule_appointment(s,tenant,appt["id"],start,
                            expected_snapshot=state["expected_snapshot"],customer_phone=sender,source="whatsapp_meta")
                    updated.update(timezone=proposal["timezone"],professional=proposal["professional"])
                await redis.delete(state_key)
                return True,presentation.render_summary(summary(updated),preferences,
                    operation="cancel" if action=="cancel" else "reschedule",confirmed=True)
            except HTTPException:
                state.pop("summary_message_id",None)
                await save(state)
                return True,"A reserva ou o catálogo mudou. A alteração não foi confirmada; vamos conferir um novo resumo."
        if decision.kind=="amend" and action!="cancel":
            state["step"]="customer_reschedule"
            state["new_day"]=datetime.fromisoformat(state["new_start"]).date().isoformat()
            state.pop("summary_message_id",None)
        else:
            await save(state)
            return True,"A reserva original permanece. Você quer ajustar algum detalhe ou revisar o resumo antes de continuar?"

    if state.get("step")=="customer_reschedule":
        appt=await _owned_booking(tenant,sender,state["protocol"])
        if not appt:
            return True,"Reserva indisponível para remarcação."
        plan=build_plan(message=user_text,catalog=[],reference_date=datetime.now(ZoneInfo(appt["timezone"])).date())
        day=plan.requested_date or state.get("new_day")
        if any(value != "time" for value in plan.ambiguities) or not day:
            await save(state)
            return True,"Qual nova data você prefere? Pode dizer o dia e o horário desejado."
        state["new_day"]=day
        try:
            async with get_session_factory()() as s:
                available=await list_slots(s,tenant,appt["professional_id"],
                    [item["service_id"] for item in appt["items"]],date.fromisoformat(day))
            clock_text="às "+plan.requested_time if plan.requested_time and plan.time_relation=="exact" else user_text
            choice=choose_time(clock_text,available["slots"],presented_slots=state.get("slots"),timezone=appt["timezone"])
            if choice.kind=="selected":
                if not message_id:
                    return True,"Preciso receber sua escolha em uma nova mensagem para preparar a alteração."
                return await rescheduling_proposal(appt,datetime.fromisoformat(choice.when))
            state["slots"]=list(choice.alternatives or available["slots"])[:8]
            await save(state)
            return True,checked(presentation.render_slots([x["name_snapshot"] for x in appt["items"]],state["slots"],preferences,
                timezone=appt["timezone"],day=day,total_cents=available["price_cents"],duration_min=available["duration_min"],professional=appt["professional"]))
        except HTTPException:
            await save(state)
            return True,presentation.render_prompt("unavailable",preferences)

    protocol_reference=re.search(r"\bag-[a-z0-9-]+\b",cmd)
    natural_reference=(not protocol_reference or (state.get("step")=="customer_booking_pick" and not request)
        or (request and request.group() in {"cancelamento","remarcacao"}))
    if not staff_request and natural_reference and (request or state.get("step")=="customer_booking_pick"):
        if not message_id:
            return True,"Preciso receber essa solicitação em uma nova mensagem antes de preparar uma alteração."
        records=await owned_future_bookings(tenant,sender)
        if not records:
            return await show_bookings(records)
        actions=set("cancel" if match.group().startswith(("cancel","desmar")) else "reschedule"
            for match in re.finditer(r"\b(?:cancel\w*|desmar\w*|remarc\w*|reagend\w*)\b",normalized(cmd)))
        action=next(iter(actions)) if len(actions)==1 else state.get("booking_action") if not actions else None
        shown=state.get("reference_ids",[]) if state.get("step")=="customer_booking_pick" else []
        kind,selected=booking_reference(user_text,records,presented_ids=shown)
        if kind!="selected" or len(actions)>1:
            return await show_bookings(records,action)
        record=next(row for row in records if str(row["id"])==selected)
        if not action:
            answer,_=booking_listing([record])
            await save({"step":"customer_booking_pick","reference_ids":[selected],"booking_action":None})
            return True,answer
        appt=await _owned_booking(tenant,sender,record["protocol"])
        if not appt or appt.get("status")!="confirmed" or appt["start_at"]<=datetime.now(UTC):
            return True,"Essa reserva não está mais disponível para alteração neste atendimento. Nenhuma reserva foi alterada."
        if action=="cancel":
            return await cancellation_proposal(appt)
        await save({"step":"customer_reschedule","protocol":appt["protocol"]})
        return True,"Qual nova data e horário você prefere? A reserva original permanece até você conferir e confirmar a remarcação."

    m=re.search(r"\b(cancelar|cancela|cancele|desmarcar|remarcar|reagendar)\b.*\b(ag-[a-z2-9]{6})\b",cmd)
    if m:
        protocol=m.group(2).upper()
        appt=await _owned_booking(tenant,sender,protocol)
        if not appt or appt["start_at"]<=datetime.now(UTC):
            return True,"Reserva não encontrada para este número ou indisponível."
        if re.search(r"\b(?:como|politica|nao|não)\b",cmd):
            return True,"A reserva permanece. Para solicitar uma alteração, diga qual detalhe deseja mudar."
        if not message_id:
            return True,"Preciso receber essa solicitação em uma nova mensagem antes de preparar uma alteração."
        if m.group(1) in {"cancelar","cancela","cancele","desmarcar"}:
            try:
                return await cancellation_proposal(appt)
            except HTTPException:
                return True,"O resumo completo precisa de revisão da equipe antes de cancelar."
        await save({"step":"customer_reschedule","protocol":protocol})
        return True,"Qual nova data e horário você prefere? A reserva original permanece até você conferir e confirmar a remarcação."
    if protocol_reference and request:
        return True,"Reserva não encontrada para este número ou indisponível."

    if staff_request:
        staff=await _authorized_staff(tenant,sender)
        if not staff:
            return True,"Comando restrito à equipe autorizada."
        role,professional=staff["role"],staff["professional_id"]
        if cmd in ("agenda hoje","agenda amanhã","agenda amanha"):
            if role=="barber" and not professional:
                return True,"Profissional ainda não vinculado ao número autorizado."
            return True,await _today_staff_agenda(tenant,role,professional,cmd!="agenda hoje")
        if cmd in ("pausar bot","retomar bot"):
            if role!="owner":
                return True,"Comando reservado ao proprietário autorizado."
            action="pause" if cmd=="pausar bot" else "resume"
            await redis.set(state_key,json.dumps({"step":"staff_bot","action":action}),ex=120)
            return True,"Responda "+("CONFIRMAR PAUSA" if action=="pause" else "CONFIRMAR RETOMADA")+" em dois minutos."
        if cmd in ("confirmar pausa","confirmar retomada"):
            action="pause" if cmd=="confirmar pausa" else "resume"
            if role!="owner" or state.get("step")!="staff_bot" or state.get("action")!=action:
                return True,"Solicitação expirada ou não autorizada."
            async with get_session_factory()() as s:
                async with tenant_transaction(s,tenant):
                    await s.execute(text("""
                        UPDATE whatsapp_channels SET bot_enabled=:enabled
                        WHERE tenant_id=:t AND id=:channel
                    """),{"t":tenant,"channel":channel,"enabled":action=="resume"})
                    await s.execute(text("""
                        INSERT INTO tenant_audit (tenant_id,action,entity,entity_id)
                        VALUES (:t,:act,'whatsapp_channels',:channel)
                    """),{"t":tenant,"act":"updated","channel":channel})
            await redis.delete(state_key)
            return True,("Bot retomado." if action=="resume" else "Bot pausado neste número.")
        if block:
            if not professional or role not in ("barber","owner"):
                return True,"Número sem profissional vinculado para bloqueio."
            await redis.set(state_key,json.dumps({
                "step":"staff_block","professional":str(professional),
                "day":block.group(1),"start":block.group(2),"end":block.group(3)
            }),ex=120)
            return True,"Confirme o bloqueio respondendo CONFIRMAR BLOQUEIO."
        if cmd=="confirmar bloqueio":
            if (state.get("step")!="staff_block" or not professional
                or str(professional)!=state.get("professional")):
                return True,"Bloqueio expirado ou não autorizado."
            result=await _staff_block(
                tenant,professional,state["day"],state["start"],state["end"],
            )
            await redis.delete(state_key)
            return True,result
    return False,None
