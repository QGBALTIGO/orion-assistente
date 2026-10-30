"""The existing commercial catalogue and Orion's single feature matrix.

An entitlement never switches an installation flag or connects an integration.
"""
CODES = {
    "essencial": {"name": "Orion Essencial", "price_cents": 2990, "max_professionals": 2,
        "db_code": "orion_essencial", "offer_names": ("Orion Essencial",),
        "checkout_url": "https://pay.cakto.com.br/3bdhdkc_1219844"},
    "profissional": {"name": "Orion Profissional", "price_cents": 8990, "max_professionals": 6,
        "db_code": "orion_profissional", "offer_names": ("Orion Profissional", "Orion Pro"),
        "checkout_url": "https://pay.cakto.com.br/3652fm4"},
    "elite": {"name": "Orion Elite", "price_cents": 13990, "max_professionals": 10,
        "db_code": "orion_elite", "offer_names": ("Orion Elite",),
        "checkout_url": "https://pay.cakto.com.br/v4md8n3"},
}

BASE_FEATURES = frozenset({
    "workspace.access", "dashboard.basic", "agenda.manage", "clients.manage",
    "performance.advanced", "services.manage", "professionals.manage", "finance.basic",
    "site.manage", "notifications.basic", "settings.manage", "calendar.manage",
})
PRO_FEATURES = BASE_FEATURES | frozenset({
    "conversations.manual", "goals.manage", "finance.advanced", "commissions.advanced",
    "loyalty.manage", "marketing.ai", "marketing.campaigns", "coupons.manage",
    "consultant.ai", "relationships.manage",
})
ELITE_FEATURES = PRO_FEATURES | frozenset({
    "reception.ai", "whatsapp.ai_booking", "reception.automation",
})
PLAN_FEATURES = {"essencial": BASE_FEATURES, "profissional": PRO_FEATURES, "elite": ELITE_FEATURES}
RECOVERY_FEATURES = frozenset({"plan.manage", "account.manage", "business.setup"})
ALL_FEATURES = ELITE_FEATURES | RECOVERY_FEATURES | {"commerce.admin"}
FEATURE_LABELS = {
    "workspace.access": "Acesso ao painel",
    "dashboard.basic": "Dashboard e indicadores básicos",
    "agenda.manage": "Agenda e agendamentos com vários serviços",
    "clients.manage": "Clientes e histórico de atendimentos",
    "conversations.manual": "WhatsApp e conversas manuais",
    "services.manage": "Serviços, preços e combos",
    "professionals.manage": "Profissionais e disponibilidade",
    "finance.basic": "Financeiro básico",
    "site.manage": "Site público de agendamento",
    "notifications.basic": "Comunicação automática básica",
    "settings.manage": "Configurações da empresa",
    "calendar.manage": "Integração com Google Agenda",
    "performance.advanced": "Desempenho avançado",
    "goals.manage": "Metas da empresa e da equipe",
    "finance.advanced": "Relatórios financeiros avançados",
    "commissions.advanced": "Gestão avançada de comissões",
    "loyalty.manage": "Fidelização e recompensas",
    "marketing.ai": "Marketing IA",
    "marketing.campaigns": "Planejamento de campanhas",
    "coupons.manage": "Cupons",
    "consultant.ai": "Consultor IA de gestão",
    "relationships.manage": "Relacionamento e retenção",
    "reception.ai": "Recepcionista IA no WhatsApp",
    "whatsapp.ai_booking": "Agendamentos por conversa com IA",
    "reception.automation": "Automações da recepção IA",
}


def feature_catalog():
    rows = [{"id": key, "name": label, "implemented": True,
             "mode": "drafts" if key == "marketing.campaigns" else "available",
             "plans": [code for code, features in PLAN_FEATURES.items() if key in features]}
            for key, label in FEATURE_LABELS.items() if key != "workspace.access"]
    rows.append({"id": "support.priority", "name": "Suporte prioritário",
                 "implemented": False, "mode": "unavailable", "plans": ["elite"],
                 "description": "Não há um fluxo de suporte prioritário implementado no painel."})
    return rows
