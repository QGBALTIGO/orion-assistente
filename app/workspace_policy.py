"""Persisted business niche selects its available workspace, not a billing grant."""
from fastapi import HTTPException


SUBSCRIPTION_NICHE = "servico_assinaturas"
STANDARD_WORKSPACE = "standard"
SUBSCRIPTION_WORKSPACE = "subscription_services"
SUBSCRIPTION_WORKSPACE_FEATURES = frozenset({"account.manage", "business.setup", "devices.manage", "chatbot.manage", "templates.manage", "sent_messages.manage", "diagnostics.manage", "campaigns.manage", "scheduled_messages.manage", "bulk_messages.manage"})


def workspace_profile(niche, *, platform=False):
    return (SUBSCRIPTION_WORKSPACE if niche == SUBSCRIPTION_NICHE and not platform
            else STANDARD_WORKSPACE)


def workspace_unavailable(feature=None):
    detail = {"code": "WORKSPACE_FEATURE_UNAVAILABLE",
              "message": "Este recurso ainda não está disponível para Serviço de assinaturas."}
    if feature is not None:
        detail["feature"] = feature
    return HTTPException(403, detail)


def require_standard_workspace(state, feature=None):
    if state and state.get("workspace_profile") == SUBSCRIPTION_WORKSPACE:
        raise workspace_unavailable(feature)
