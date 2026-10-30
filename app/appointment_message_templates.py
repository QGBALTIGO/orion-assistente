"""15 official, verbatim WhatsApp message templates for Orion appointments.

The delimiters (*, _, ~) are plain text for WhatsApp's native formatting.
No HTML/Markdown renderer should transform them before the QR transport.
The only substitutions are trusted facts from the appointment engine.
"""
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from app.public_origins import canonical_public_origin
from app.qr_presentation import fits_message

# These are the approved, literal message bodies, WITHOUT internal instructions.
# Model 12 is a conditional insertion into model 11; 15 is not scheduled.
TEMPLATES = {
    "public_prefill": """*Olá, Orion!* 👋

Acabei de realizar um agendamento pelo site e gostaria de confirmar meu horário. 😊

🧾 Protocolo: *{{protocolo}}*

_Aguardo os detalhes da minha confirmação._""",
    "confirmation": """*Olá, {{primeiro_nome_cliente}}!* 👋

Tudo certo! Seu agendamento foi confirmado com sucesso. Confira os detalhes:

✂️ Serviço: *{{servico}}*

📅 Data: *{{data}}*
🕒 Horário: *{{horario}}*

👤 Profissional: *{{nome_profissional}}*

🔗 *Gerencie seu agendamento:*
{{link_cliente}}

_Precisa cancelar ou remarcar? Utilize o link acima com até 1 hora de antecedência._

{{assinatura}}""",
    "professional_new": """📩 *NOVO AGENDAMENTO RECEBIDO!*

Olá, {{nome_profissional}}! 👋

_Um novo atendimento foi agendado com você._

👤 Cliente: *{{nome_cliente}}*
✂️ Serviço: *{{servico}}*

📅 Data: *{{data}}*
🕒 Horário: *{{horario}}*
⏱️ Duração: *{{duracao_minutos}} minutos*

🔗 *Gerencie o atendimento:*
{{link_profissional}}

_O atendimento já consta na sua agenda._

{{assinatura}}""",
    "professional_cancelled": """❌ *AGENDAMENTO CANCELADO!*

Olá, {{nome_profissional}}!

_O cliente cancelou o atendimento que estava agendado com você._

👤 Cliente: *{{nome_cliente}}*
✂️ Serviço: *{{servico}}*

📅 Data: *{{data}}*
🕒 Horário: *{{horario}}*

🔗 *Consulte o atendimento:*
{{link_profissional}}

_O horário foi liberado na sua agenda e está disponível para novos agendamentos._

{{assinatura}}""",
    "customer_cancelled": """*Olá, {{primeiro_nome_cliente}}!*

❌ Infelizmente, seu agendamento precisou ser *cancelado pela nossa equipe.*

Confira os detalhes do atendimento cancelado:

✂️ Serviço: *{{servico}}*

📅 Data: *{{data}}*
🕒 Horário: *{{horario}}*

👤 Profissional: *{{nome_profissional}}*

Pedimos desculpas pelo inconveniente e esperamos receber você em outra oportunidade. 😊

🔗 *Deseja agendar novamente?*
{{link_novo_agendamento}}

_Escolha uma nova data e horário de sua preferência._

{{assinatura}}""",
    "professional_rescheduled": """🔄 *AGENDAMENTO REMARCADO!*

Olá, {{nome_profissional}}! 👋

_O cliente realizou uma alteração no horário do atendimento._

👤 Cliente: *{{nome_cliente}}*
✂️ Serviço: *{{servico}}*

📅 *Horário anterior:*
~{{data_anterior}} às {{horario_anterior}}~

✅ *Novo horário confirmado:*
📅 Data: *{{nova_data}}*
🕒 Horário: *{{novo_horario}}*

🔗 *Consulte o agendamento atualizado:*
{{link_profissional}}

_Sua agenda já foi atualizada com as novas informações._

{{assinatura}}""",
    "customer_rescheduled": """*Olá, {{primeiro_nome_cliente}}!* 👋

🔄 Seu agendamento foi *remarcado pela nossa equipe.*

Confira abaixo o novo horário do seu atendimento:

✂️ Serviço: *{{servico}}*
👤 Profissional: *{{nome_profissional}}*

📅 *Horário anterior:*
~{{data_anterior}} às {{horario_anterior}}~

✅ *Novo horário confirmado:*
📅 Data: *{{nova_data}}*
🕒 Horário: *{{novo_horario}}*

🔗 *Gerencie seu agendamento:*
{{link_cliente}}

_Por favor, considere a nova data e o novo horário informados acima._

Aguardamos você! 😊

{{assinatura}}""",
    "customer_proposal": """*Olá, {{primeiro_nome_cliente}}!* 👋

🔄 Nossa equipe gostaria de propor um *novo horário* para seu atendimento.

Confira a sugestão:

✂️ Serviço: *{{servico}}*
👤 Profissional: *{{nome_profissional}}*

📅 *Seu horário atual:*
{{data_atual}} às {{horario_atual}}

✨ *Novo horário sugerido:*
📅 Data: *{{nova_data}}*
🕒 Horário: *{{novo_horario}}*

⏳ *Prazo para responder:*
{{data_limite_aceite}} às {{horario_limite_aceite}}

🔗 *Aceite ou recuse a proposta:*
{{link_cliente}}

_Seu horário original continua válido até que você aceite a alteração. O novo horário será verificado no momento da confirmação._

{{assinatura}}""",
    "professional_proposal_declined": """📋 *PROPOSTA DE REMARCAÇÃO RECUSADA!*

Olá, {{nome_profissional}}!

_O cliente optou por não aceitar o novo horário sugerido._

👤 Cliente: *{{nome_cliente}}*
✂️ Serviço: *{{servico}}*

📅 Data atual: *{{data}}*
🕒 Horário atual: *{{horario}}*

🔗 *Consulte o atendimento:*
{{link_profissional}}

_A proposta foi recusada. Consulte a agenda para conferir a situação atual do atendimento._

{{assinatura}}""",
    "reminder": """*Olá, {{primeiro_nome_cliente}}!* 👋

⏰ Passando para lembrar que seu atendimento na *{{empresa}}* será daqui a aproximadamente *1 hora!* 😊

✂️ Serviço: *{{servico}}*

📅 Data: *{{data}}*
🕒 Horário: *{{horario}}*

👤 Profissional: *{{nome_profissional}}*

📍 *Local do atendimento:*
{{endereco_empresa}}

_Programe-se para chegar no horário. Estamos esperando por você!_

Até já! ✨

{{assinatura}}""",
    "review": """*Olá, {{primeiro_nome_cliente}}!* 😊

Foi um prazer receber você! Esperamos que tenha gostado do atendimento com *{{nome_profissional}}*.

⭐ *Que tal avaliar sua experiência?*

Sua opinião é muito importante e nos ajuda a oferecer um atendimento cada vez melhor.

🔗 *Deixe sua avaliação:*
{{link_avaliacao}}

_Leva apenas alguns instantes e faz toda a diferença para nossa equipe._

Muito obrigado pela preferência! 💙

{{assinatura}}""",
    "return_invitation": """✂️ *Que tal garantir seu próximo horário?*

Gostou do atendimento? Já pode escolher o melhor dia e horário para sua próxima visita! 😊

🔗 *Agende novamente:*
{{link_novo_agendamento}}

_Esperamos ver você novamente em breve!_""",
    "invalid_protocol": """*Olá!* 👋

🔐 Não foi possível confirmar o protocolo informado para este número de WhatsApp.

_Por segurança, precisamos validar o contato antes de compartilhar informações de qualquer agendamento._

📱 Tente novamente utilizando o *WhatsApp cadastrado no momento do agendamento* ou solicite ajuda diretamente à empresa.

Agradecemos pela compreensão! 😊

{{assinatura}}""",
    "status": """*Olá, {{primeiro_nome_cliente}}!* 👋

📋 Temos uma atualização sobre seu agendamento.

A situação atual do seu atendimento é:

📌 Status: *{{estado_atendimento}}*

🔗 *Consulte seu agendamento:*
{{link_cliente}}

_Acesse o link acima para conferir as informações atualizadas do atendimento._

{{assinatura}}""",
    "professional_reassigned": """🔄 *PROFISSIONAL ALTERADO!*

Olá, {{nome_profissional}}! 👋

_Um atendimento foi atribuído a você após uma alteração de profissional._

👤 Cliente: *{{nome_cliente}}*
✂️ Serviço: *{{servico}}*

📅 Data: *{{data}}*
🕒 Horário: *{{horario}}*

👤 Profissional atual: *{{nome_profissional}}*

🔗 *Consulte o atendimento:*
{{link_profissional}}

_Confira os detalhes do agendamento atualizado na sua agenda._

{{assinatura}}""",
}
PLACEHOLDER = re.compile(r"\{\{([a-z_]+)\}\}")
STATUS_LABELS = {
    "cancelled": "cancelado",
    "completed": "concluído",
    "no_show": "registrado como ausência",
    "hold": "em reserva temporária",
}


def label(value, limit=160):
    """Untrusted names must not open/close native WhatsApp markup or add URLs."""
    value = " ".join(str(value or "").split())[:limit]
    value = re.sub(r"https?://\S+|www\.\S+", "[link removido]", value, flags=re.I)
    return value.translate(str.maketrans({"*": "", "_": "", "~": "", "\`": "", "<": "", ">": ""}))


def safe_link(value):
    origin = canonical_public_origin()
    if (isinstance(value, str) and not re.search(r"[\s<>]", value)
            and (value.startswith(origin + "/a#")
                 or value.startswith(origin + "/s/")
                 or value.startswith(origin + "/panel#"))):
        return value
    return None


def render_template(event, values, *, include_return=False):
    """Render only the published template. No unresolved fields are deliverable."""
    body = TEMPLATES[event]
    if event == "review" and include_return and values.get("link_novo_agendamento"):
        body = body.replace("{{assinatura}}", TEMPLATES["return_invitation"] + "\n\n{{assinatura}}")
    for name in ("link_cliente", "link_profissional", "link_novo_agendamento", "link_avaliacao"):
        if not values.get(name):
            # Links are optional only when unavailable or unauthorized.
            body = re.sub(r"(?m)^🔗[^\n]+\n\{\{" + name + r"\}\}\n*", "", body)
            # A secondary sentence explicitly referring to a missing link is omitted too.
            if event == "confirmation" and name == "link_cliente":
                body = body.replace("_Precisa cancelar ou remarcar? Utilize o link acima com até 1 hora de antecedência._\n\n", "")
            if event == "status" and name == "link_cliente":
                body = body.replace("_Acesse o link acima para conferir as informações atualizadas do atendimento._\n\n", "")
            if event == "customer_cancelled" and name == "link_novo_agendamento":
                body = body.replace("_Escolha uma nova data e horário de sua preferência._\n\n", "")
            if event == "review" and name == "link_avaliacao":
                body = body.replace("_Leva apenas alguns instantes e faz toda a diferença para nossa equipe._\n\n", "")
    body = re.sub(r"\n{3,}", "\n\n", body)
    def substitute(match):
        key = match.group(1)
        if key not in values or values[key] is None:
            raise ValueError("missing_appointment_message_fact:" + key)
        return str(values[key])
    body = PLACEHOLDER.sub(substitute, body)
    if "{{" in body or "}}" in body or not fits_message(body):
        raise ValueError("appointment_message_invalid_or_too_long")
    return body


def compose_prefill(protocol):
    return render_template("public_prefill", {"protocolo": label(protocol, 30)})


def generic_response(signature="Orion Agenda"):
    return render_template("invalid_protocol", {"assinatura": label(signature, 80) or "Orion Agenda"})


def is_generic_response(value):
    return isinstance(value, str) and value.startswith("*Olá!* 👋\n\n🔐 Não foi possível confirmar o protocolo")


def compose(event, row, *, company, signature="Orion Agenda", customer_link=None,
            booking_link=None, previous=None, include_return=False, address=None, state=None):
    previous = previous or {}
    zone = ZoneInfo(row["timezone"])
    start = row["start_at"].astimezone(zone)
    first = label(row.get("customer_name")).split(" ")[0] or "Cliente"
    professional = label(row.get("professional_name")) or "Profissional não informado"
    service = " + ".join(label(item["name_snapshot"], 100) for item in row["items"])
    if len(service) > 320:
        service = service[:317] + "…"
    before = previous.get("start_at")
    if before is not None:
        before = datetime.fromisoformat(before) if isinstance(before, str) else before
        before = before.astimezone(zone)
    proposed = previous.get("new_start_at")
    if proposed is not None:
        proposed = datetime.fromisoformat(proposed) if isinstance(proposed, str) else proposed
        proposed = proposed.astimezone(zone)
    expires = previous.get("expires_at")
    if expires is not None:
        expires = datetime.fromisoformat(expires) if isinstance(expires, str) else expires
        expires = expires.astimezone(zone)
    if event in {"professional_rescheduled", "customer_rescheduled"} and before is None:
        raise ValueError("missing_previous_appointment_time")
    if event == "customer_proposal" and (proposed is None or expires is None):
        raise ValueError("missing_appointment_proposal_facts")
    professional_link = (canonical_public_origin() + "/panel?aba=agenda&agendamento=" + str(row["id"])
                         if event.startswith("professional_") else None)
    customer_link = safe_link(customer_link)
    booking_link = safe_link(booking_link)
    values = {
        "primeiro_nome_cliente": first,
        "nome_cliente": label(row.get("customer_name")) or "Cliente não informado",
        "nome_profissional": professional,
        "servico": service,
        "data": start.strftime("%d/%m/%Y"),
        "horario": start.strftime("%H:%M"),
        "data_atual": start.strftime("%d/%m/%Y"),
        "horario_atual": start.strftime("%H:%M"),
        "duracao_minutos": sum(item["duration_snapshot_min"] for item in row["items"]),
        "nova_data": start.strftime("%d/%m/%Y") if event != "customer_proposal" else proposed.strftime("%d/%m/%Y"),
        "novo_horario": start.strftime("%H:%M") if event != "customer_proposal" else proposed.strftime("%H:%M"),
        "data_anterior": before.strftime("%d/%m/%Y") if before else None,
        "horario_anterior": before.strftime("%H:%M") if before else None,
        "data_limite_aceite": expires.strftime("%d/%m/%Y") if expires else None,
        "horario_limite_aceite": expires.strftime("%H:%M") if expires else None,
        "empresa": label(company),
        "endereco_empresa": label(address, 200) or "Endereço não informado",
        "assinatura": label(signature, 80) or "Orion Agenda",
        "link_cliente": customer_link,
        "link_profissional": professional_link,
        "link_novo_agendamento": booking_link,
        "link_avaliacao": customer_link,  # scoped appointment page includes its review form
        "estado_atendimento": label(state or STATUS_LABELS.get(row.get("status"), "em atualização")),
    }
    return render_template(event, values, include_return=include_return and bool(booking_link))
