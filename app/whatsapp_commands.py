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
    acquire_professional_lock, cancel_appointment, reschedule_appointment,
)
from app.database import get_session_factory, tenant_transaction

UTC = timezone.utc


async def _authorized_staff(tenant: UUID, sender: str):
    async with get_session_factory()() as s:
        async with tenant_transaction(s, tenant):
            return (await s.execute(text("""
                SELECT role, professional_id FROM authorized_staff
                WHERE tenant_id=:tenant AND phone_e164=:sender AND active
            """), {"tenant":tenant, "sender":sender})).mappings().first()


async def _owned_booking(tenant: UUID, sender: str, protocol: str):
    async with get_session_factory()() as s:
        async with tenant_transaction(s, tenant):
            return (await s.execute(text("""
                SELECT a.id, a.protocol, a.status FROM appointments a
                JOIN customers c ON c.id=a.customer_id AND c.tenant_id=a.tenant_id
                WHERE a.tenant_id=:tenant AND c.whatsapp_e164=:sender
                  AND a.protocol=:protocol AND a.status='confirmed'
                  AND a.end_at>now()
            """), {"tenant":tenant,"sender":sender,"protocol":protocol})).mappings().first()


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
    state: dict, redis, state_key: str,
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

    if cmd=="meus horários" or cmd=="meus horarios":
        async with get_session_factory()() as s:
            async with tenant_transaction(s,tenant):
                tzname=(await s.execute(text("SELECT timezone FROM tenants WHERE id=:t"),
                                        {"t":tenant})).scalar_one()
                bookings=(await s.execute(text("""
                    SELECT a.protocol,a.start_at FROM appointments a
                    JOIN customers c ON a.customer_id=c.id AND a.tenant_id=c.tenant_id
                    WHERE a.tenant_id=:t AND c.whatsapp_e164=:sender
                      AND a.status='confirmed' AND a.start_at>=now()
                    ORDER BY a.start_at LIMIT 10
                """),{"t":tenant,"sender":sender})).mappings().all()
        if not bookings:
            return True,"Não há reservas futuras confirmadas para este número."
        tz=ZoneInfo(tzname)
        lines=[f"{b['protocol']} — {b['start_at'].astimezone(tz):%d/%m às %H:%M}"
               for b in bookings]
        return True,("Suas reservas:\n"+"\n".join(lines)+
                     "\nPara cancelar: CANCELAR AG-XXXXXX.")

    if state.get("step")=="customer_cancel" and cmd=="sim cancelar":
        appt=await _owned_booking(tenant,sender,state["protocol"])
        await redis.delete(state_key)
        if not appt:
            return True,"Reserva indisponível ou já cancelada."
        async with get_session_factory()() as s:
            await cancel_appointment(s,tenant,appt["id"])
        return True,"Agendamento cancelado. Protocolo "+appt["protocol"]+"."

    if state.get("step")=="customer_reschedule" and re.fullmatch(
        r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}",cmd,
    ):
        appt=await _owned_booking(tenant,sender,state["protocol"])
        if not appt:
            await redis.delete(state_key)
            return True,"Reserva indisponível para remarcação."
        async with get_session_factory()() as s:
            async with tenant_transaction(s,tenant):
                tzname=(await s.execute(text("SELECT timezone FROM tenants WHERE id=:t"),
                                        {"t":tenant})).scalar_one()
        try:
            naive=datetime.strptime(cmd,"%Y-%m-%d %H:%M")
            start=naive.replace(tzinfo=ZoneInfo(tzname))
        except ValueError:
            return True,"Data inválida. Use AAAA-MM-DD HH:MM."
        try:
            async with get_session_factory()() as s:
                updated=await reschedule_appointment(s,tenant,appt["id"],start)
        except HTTPException:
            return True,"Novo horário indisponível. Envie outra data e hora."
        await redis.delete(state_key)
        return True,(f"Remarcado! {updated['protocol']} — "
                     f"{updated['start_at'].astimezone(ZoneInfo(tzname)):%d/%m às %H:%M}.")

    m=re.fullmatch(r"(cancelar|remarcar)\s+(ag-[a-z2-9]{6})",cmd)
    if m:
        protocol=m.group(2).upper()
        appt=await _owned_booking(tenant,sender,protocol)
        if not appt:
            return True,"Reserva não encontrada para este número ou indisponível."
        if m.group(1)=="cancelar":
            await redis.set(state_key,json.dumps({"step":"customer_cancel","protocol":protocol}),ex=120)
            return True,"Para confirmar o cancelamento, responda SIM CANCELAR."
        await redis.set(state_key,json.dumps({"step":"customer_reschedule","protocol":protocol}),ex=300)
        return True,"Informe a nova data e hora: AAAA-MM-DD HH:MM."

    staff_cmds={"agenda hoje","agenda amanhã","agenda amanha",
                "pausar bot","retomar bot","confirmar pausa","confirmar retomada"}
    block=re.fullmatch(r"bloquear (\d{4}-\d{2}-\d{2}) (\d{2}:\d{2})-(\d{2}:\d{2})",cmd)
    if cmd in staff_cmds or block or cmd=="confirmar bloqueio":
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
