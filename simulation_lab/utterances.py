"""Original Portuguese synthetic conversation templates; no external LLM calls."""
from __future__ import annotations

OPERATION = {
    "agenda": "agenda.validate_and_write",
    "professionals_services_commissions": "catalog_and_commission.update",
    "whatsapp_ai_notifications": "conversation_intent_and_outbox",
    "finance_performance": "finance.reconcile",
    "crm_history": "crm.scoped_identity",
    "recurrence": "recurrence.validate_series",
    "public_ux": "browser.render_and_interact",
    "onboarding_plans": "onboarding_or_billing.validate",
    "external_integrations": "integration.outbox_reconcile",
    "tenant_security": "authorization.deny_or_scope",
    "failures_performance": "retry_or_recovery",
}

PROMPTS = {
    "consultar_horarios": "Tem algum horario {quando} as {hora} para {servicos} com {pro}?",
    "reservar": "Pode agendar {servicos} {quando} as {hora} com {pro}?",
    "encaixar": "Hoje esta cheio, consegue um encaixe para {servicos} as {hora}?",
    "alterar_profissional": "Queria trocar meu horario para outro {pro} {quando}.",
    "reservar_combo": "Quanto fica o combo de {servicos}, e cabe {quando} as {hora}?",
    "agendar_familiar": "Quero marcar {servicos} para meu filho {quando} as {hora}.",
    "bloquear": "Preciso bloquear {quando} as {hora} na agenda do {pro}.",
    "remarcar": "Nao vou conseguir chegar; remarca {servicos} para {quando} as {hora}?",
    "cancelar": "Pode cancelar so meu agendamento de {servicos}?",
    "pedir_antes_da_abertura": "Tem como me atender antes de abrir, as {hora}?",
    "pedir_apos_fechar": "Preciso agendar depois do expediente, as {hora}, da?",
    "cancelar_botao": "Cliquei no botao Cancelar, mas quero cancelar somente esse horario.",
    "confirmar_botao": "Apertei Confirmar no agendamento de {quando} as {hora}.",
    "audio": "[transcricao de audio] Preciso de {servicos} {quando}.",
    "mensagem_citada": "Respondendo aquela mensagem antiga: esse horario ainda existe?",
    "criar_serie": "Quero {servicos} toda sexta as {hora} por {ocorrencias} semanas.",
    "cancelar_uma": "Nao posso comparecer numa das semanas; cancela so a proxima?",
    "editar_todas": "Altera todas as {ocorrencias} visitas fixas para {hora}.",
    "editar_uma": "Muda somente o proximo atendimento da serie, os outros ficam.",
    "unificar_whatsapp_site": "Agendei pelo site e agora escrevo pelo WhatsApp; sao o mesmo cadastro?",
    "mesmo_nome": "Duas clientes tem o mesmo nome; nao misture o historico.",
    "whatsapp_indisponivel": "O WhatsApp caiu depois da confirmacao, ainda esta marcado?",
    "redis_offline": "O sistema ficou lento e minha mensagem foi enviada duas vezes.",
    "rls": "Sou da empresa A; consigo abrir a agenda da empresa B com um ID?",
    "ver_financeiro_alheio": "Posso ver a receita de outro barbeiro de outra empresa?",
    "produto_extra": "Acrescente um produto e confira a comissao do atendimento.",
    "servico_extra": "Inclua barba no corte que ja esta em andamento.",
    "desconto": "De um desconto sem recalcular a comissao de forma incorreta.",
    "pagamento_parcial": "O cliente pagou uma parte, quanto falta receber?",
    "token_expired": "Meu token expirou durante a confirmacao, o que acontece?",
    "calendar_token_expirado": "A agenda do Google desconectou, mas a reserva continua?",
    "calendar_remarcacao": "Remarquei o corte, atualize apenas o evento certo.",
    "webhook_duplicado": "Recebemos o mesmo webhook do plano duas vezes.",
    "criar_empresa": "Estou cadastrando a empresa e quero continuar depois.",
    "validar_cep": "Meu CEP deu erro, consigo corrigir o endereco sem perder tudo?",
    "nome_longo": "O nome do profissional nao cabe no cartao do celular.",
    "conexao_lenta": "A pagina nao carregou no 4G e perdi os dados do formulario.",
    "troca_tema": "Mudei para tema escuro no meio da reserva.",
    "concorrencia": "Duas pessoas escolheram o ultimo horario ao mesmo tempo.",
    "falha_apos_commit": "A confirmacao falhou, mas o atendimento ja foi salvo.",
}

GENERAL = {
    "professionals_services_commissions": "Ajuste {intent} para {servicos} do {pro}, sem perder o historico.",
    "whatsapp_ai_notifications": "Cliente pergunta sobre {servicos} {quando} as {hora}; acao {intent}.",
    "finance_performance": "Na hora de {intent} para {servicos}, calcule o valor e a comissao corretamente.",
    "crm_history": "No cadastro de cliente, preciso de {intent} e conferir seu historico.",
    "recurrence": "Preciso de {intent} para {servicos} com {pro} por {ocorrencias} ocorrencias.",
    "public_ux": "No meu celular ocorreu {intent} ao selecionar {servicos}.",
    "onboarding_plans": "Durante o cadastro da empresa preciso de {intent} sem perder o progresso.",
    "external_integrations": "A integracao apresentou {intent}; preserve a reserva de {servicos}.",
    "tenant_security": "Tentei {intent}; o acesso precisa respeitar a empresa e o papel.",
    "failures_performance": "Ocorreu {intent} durante a reserva de {servicos} as {hora}.",
    "agenda": "Quero {intent} de {servicos} {quando} as {hora}.",
}


def render_message(area, state):
    start = state["start_min"]
    values = {
        "intent": state["intent"].replace("_", " "),
        "quando": ("hoje" if state["lead_days"] == 0 else
                   "amanha" if state["lead_days"] == 1 else
                   "daqui a %d dias" % state["lead_days"]),
        "hora": "%dh%02d" % divmod(start, 60),
        "servicos": " e ".join(x.replace("_", " ") for x in state["service_names"]),
        "pro": state["professional_role"],
        "ocorrencias": state["series_occurrences"],
    }
    base = PROMPTS.get(state["intent"], GENERAL[area]).format(**values)
    variant = state["language"]
    if variant == "abreviado":
        for old, new in (
            ("Quero", "qro"), ("quero", "qro"), ("voce", "vc"),
            ("para", "p"), ("amanha", "amnh"), ("horario", "hr"),
            ("consegue", "csg"), ("com", "c/"), ("quanto", "qnt"),
        ):
            base = base.replace(old, new)
    elif variant == "coloquial":
        base = "Opa, tudo bem? " + base[0].lower() + base[1:]
    elif variant == "erro_digitacao":
        base = base.replace("agendar", "agnda").replace("servico", "servso").replace(
            "horario", "orario").replace("confirmacao", "confirmaçao")
    elif variant == "audio_transcrito":
        base = "[audio transcrito] " + base
    elif variant == "mensagem_curta":
        base = "%s: %s as %s?" % (values["intent"], values["servicos"], values["hora"])
    elif variant == "mensagem_longa":
        base = ("Boa tarde! Estou tentando resolver isso hoje com a equipe e "
                "preciso que confirmem a disponibilidade real antes de agir. " + base)
    return base