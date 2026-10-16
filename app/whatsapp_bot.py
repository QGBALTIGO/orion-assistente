"""Menus determinísticos. Estados efêmeros por tenant+canal+cliente no Redis.

Protocolo NÃO é token de autenticação; Meta autentica o remetente via assinatura.
"""
import json
import hashlib
from datetime import datetime, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import text

from app.booking_service import book_appointment, confirm_hold, list_slots
from app.database import get_session_factory, tenant_transaction

TTL = 1800


def state_key(tenant: str, channel: str, sender: str) -> str:
    return f"wa:state:{tenant}:{channel}:{sender}"


def number_choice(value: str, upper: int) -> int | None:
    if value.isdigit() and 1 <= int(value) <= upper:
        return int(value)-1
    return None


async def reply(tenant: UUID, channel: UUID, sender: str, body: str, redis) -> str | None:
    key = state_key(str(tenant), str(channel), sender)
    # Suspensão comercial nunca remove histórico nem libera acesso de suporte.
    async with get_session_factory()() as check_session:
        async with tenant_transaction(check_session, tenant):
            inactive = (await check_session.execute(text("""
                SELECT 1 FROM subscriptions WHERE tenant_id=:t
                  AND status IN ('suspended','canceled') LIMIT 1
            """), {"t":tenant})).first()
    if inactive:
        return None
    state = json.loads(await redis.get(key) or "{}")
    user_text = body.strip().lower()
    factory = get_session_factory()
    if user_text in {"falar com humano", "falar com barbeiro", "atendente"}:
        await redis.set(key, json.dumps({"step": "human"}), ex=TTL)
        return "Entendi. Vou encaminhar seu pedido de atendimento humano."
    if state.get("step") == "human":
        return None
    if user_text == "ajuda":
        return "Escreva MENU para começar ou ATENDENTE para falar com nossa equipe."
    if user_text in {"menu", "oi", "olá", "ola", "inicio", "início"} or not state:
        async with factory() as s:
            async with tenant_transaction(s, tenant):
                enabled = (await s.execute(text("""
                    SELECT c.bot_enabled AND t.bot_enabled
                    FROM whatsapp_channels c JOIN tenants t ON t.id=c.tenant_id
                    WHERE c.tenant_id=:tenant AND c.id=:channel
                """), {"tenant": tenant, "channel": channel})).scalar_one_or_none()
                services = (await s.execute(text("""
                    SELECT id, name, duration_min, price_cents
                    FROM services WHERE tenant_id=:tenant AND active ORDER BY name, id LIMIT 12
                """), {"tenant": tenant})).mappings().all()
        if not enabled:
            return None
        if not services:
            return "Ainda estamos configurando os serviços desta barbearia."
        await redis.set(key, json.dumps({"step": "services", "services": [str(x["id"]) for x in services]}), ex=TTL)
        options = "\n".join(f"{n}) {s['name']} — R$ {s['price_cents']/100:.2f} ({s['duration_min']} min)"
                            for n, s in enumerate(services, 1))
        return "Bem-vindo! Escolha um ou mais serviços (ex.: 1,2):\n" + options
    if state["step"] == "services":
        try:
            selected = [number_choice(x.strip(), len(state["services"])) for x in user_text.split(",")]
        except ValueError:
            selected = []
        if not selected or None in selected or len(selected) != len(set(selected)):
            return "Não entendi. Envie os números dos serviços, por exemplo: 1,2."
        ids = [state["services"][i] for i in selected]
        async with factory() as s:
            async with tenant_transaction(s, tenant):
                pros = (await s.execute(text("""
                    SELECT p.id,p.name FROM professionals p
                    WHERE p.tenant_id=:tenant AND p.active
                    AND NOT EXISTS(
                        SELECT 1 FROM unnest(CAST(:ids AS uuid[])) AS required(service_id)
                        WHERE NOT EXISTS(
                            SELECT 1 FROM professional_services x
                            WHERE x.tenant_id=p.tenant_id AND x.professional_id=p.id
                            AND x.service_id=required.service_id
                        )
                    )
                    ORDER BY p.name LIMIT 20
                """), {"tenant": tenant, "ids": ids})).mappings().all()
        if not pros:
            return "Não encontramos um profissional para essa combinação. Envie MENU para tentar outra."
        await redis.set(key, json.dumps({"step": "professional", "services": ids,
                                         "professionals": [str(p["id"]) for p in pros]}), ex=TTL)
        return "Quem prefere?\n" + "\n".join(f"{i}) {p['name']}" for i,p in enumerate(pros,1))
    if state["step"] == "professional":
        choice = number_choice(user_text, len(state["professionals"]))
        if choice is None:
            return "Envie o número do profissional."
        state["professional"] = state["professionals"][choice]
        state["step"] = "date"
        await redis.set(key, json.dumps(state), ex=TTL)
        return "Qual dia? Envie a data no formato AAAA-MM-DD (ex.: 2026-12-10)."
    if state["step"] == "date":
        try:
            day = datetime.strptime(user_text, "%Y-%m-%d").date()
        except ValueError:
            return "Data inválida. Use AAAA-MM-DD."
        async with factory() as s:
            try:
                slots = await list_slots(s, tenant, UUID(state["professional"]),
                                         [UUID(x) for x in state["services"]], day)
            except Exception as exc:
                from fastapi import HTTPException
                if isinstance(exc, HTTPException):
                    return "Data indisponível. Envie outra data ou MENU."
                raise
        if not slots["slots"]:
            return "Sem horários para esta data. Escolha outro dia."
        state.update({"step": "time", "slots": slots["slots"][:30],
                      "duration": slots["duration_min"], "price": slots["price_cents"]})
        await redis.set(key, json.dumps(state), ex=TTL)
        return "Horários livres:\n" + "\n".join(
            f"{i}) {datetime.fromisoformat(slot).strftime('%H:%M')}"
            for i,slot in enumerate(state["slots"],1)
        ) + "\nResponda com o número do horário."
    if state["step"] == "time":
        choice = number_choice(user_text, len(state["slots"]))
        if choice is None:
            return "Escolha um dos números de horários apresentados."
        start = datetime.fromisoformat(state["slots"][choice])
        async with factory() as s:
            try:
                hold = await book_appointment(
                    s, tenant, UUID(state["professional"]), [UUID(x) for x in state["services"]],
                    sender, "Cliente WhatsApp", start,
                    "meta-" + hashlib.sha256((str(channel) + sender + state["slots"][choice] +
                    "-".join(state["services"])).encode()).hexdigest(),
                )
            except Exception as exc:
                from fastapi import HTTPException
                if isinstance(exc, HTTPException):
                    return "O horário acabou de ficar indisponível. Escreva MENU para escolher outro."
                raise
        state.update({"step": "confirm", "hold": str(hold["id"])})
        await redis.set(key, json.dumps(state), ex=TTL)
        return (f"Resumo: {start.strftime('%d/%m às %H:%M')}, "
                f"{state['duration']} min, R$ {state['price']/100:.2f}. "
                "Confirma? Responda SIM ou NÃO em até 5 minutos.")
    if state["step"] == "confirm":
        if user_text in {"não", "nao", "n"}:
            await redis.delete(key)
            return "Tudo bem. Envie MENU para começar novamente."
        if user_text not in {"sim", "s"}:
            return "Por favor, responda SIM ou NÃO."
        from fastapi import HTTPException
        async with factory() as s:
            try:
                confirmed = await confirm_hold(s, tenant, UUID(state["hold"]))
            except HTTPException:
                await redis.delete(key)
                return "O horário expirou ou ficou indisponível. Envie MENU para recomeçar."
        await redis.delete(key)
        async with factory() as s:
            async with tenant_transaction(s, tenant):
                timezone_name = (await s.execute(text(
                    "SELECT timezone FROM tenants WHERE id=:id"
                ), {"id":tenant})).scalar_one()
        dt = confirmed["start_at"].astimezone(ZoneInfo(timezone_name))
        return (f"Agendamento confirmado! {dt:%d/%m às %H:%M}, "
                f"R$ {confirmed['total_cents']/100:.2f}, protocolo {confirmed['protocol']}. "
                "Para cancelar ou remarcar, fale com a barbearia.")
    await redis.delete(key)
    return "Envie MENU para iniciar."
