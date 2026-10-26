"""Tenant-bound published facts and explicit isolated preview overrides."""
from contextvars import ContextVar
import hashlib
import json
import os
from uuid import UUID

from fastapi import HTTPException

from app.business_profile_schemas import BusinessProfile, StyleRules
from app.business_profile_service import profile_version, published_profile_snapshot
from app.database import get_session_factory
from app.qr_presentation import InteractionContext, QuoteItem, render_quote

preview_snapshot: ContextVar[dict | None] = ContextVar("orion_preview_profile", default=None)


def enabled() -> bool:
    return os.getenv("BUSINESS_PROFILES_ENABLED", "0") == "1"


async def resolve_preview(tenant: UUID, version: UUID | None = None,
                          style_rules: StyleRules | None = None) -> dict | None:
    if not enabled():
        if version or style_rules:
            raise HTTPException(423, "Os perfis de atendimento ainda não estão habilitados")
        return None
    async with get_session_factory()() as db:
        if version:
            row = await profile_version(db, tenant, version)
            snapshot = {"id": row["id"], "revision": row["revision"],
                        "profile": BusinessProfile.model_validate(row["profile"])}
        else:
            snapshot = await published_profile_snapshot(db, tenant)
    if style_rules:
        snapshot = snapshot or {"id": None, "revision": 0, "profile": BusinessProfile()}
        snapshot = {**snapshot, "profile": snapshot["profile"].model_copy(update={"style_rules": style_rules})}
    return {**snapshot, "tenant": tenant} if snapshot else None


async def runtime_profile(tenant: UUID) -> dict | None:
    override = preview_snapshot.get()
    if override:
        if override["tenant"] != tenant:
            raise HTTPException(403, "Perfil fora do estabelecimento autorizado")
        return override
    if not enabled():
        return None
    async with get_session_factory()() as db:
        snapshot = await published_profile_snapshot(db, tenant)
    return {**snapshot, "tenant": tenant} if snapshot else None


def catalog_revision(business: dict, catalog: list[dict], professionals: list[dict]) -> str:
    facts = {"name": business["name"], "timezone": business["timezone"],
             "services": catalog, "professionals": professionals,
             "profile_id": business.get("profile_id"), "profile": business.get("profile")}
    return hashlib.sha256(json.dumps(facts, sort_keys=True, default=str).encode()).hexdigest()[:24]


def quote(items: list[dict], profile: dict, body: str) -> str:
    verified = [QuoteItem(x["name"], x["price_cents"], duration_min=x["duration_min"],
                          price_kind=x.get("price_mode","fixed")) for x in items]
    return render_quote(verified, profile["style_rules"],
                        context=InteractionContext.from_message(body, information_only=True))


def factual_answer(profile: dict, body: str) -> str | None:
    """Only retrieve owner-published facts; no model-supplied policies or opening hours."""
    from app.qr_behaviors import norm
    message = norm(body)
    if message.strip(" ?!.,") in {"qual o seu nome", "qual seu nome", "como voce se chama", "quem e voce"}:
        name = profile.get("assistant_name") or "Orion"
        business = profile.get("business_name")
        return f"Meu nome é {name}. Sou o assistente virtual" + (f" da {business}." if business else " deste atendimento.")
    if any(x in message for x in ("endereco", "onde fica", "localizacao")):
        return profile.get("address") or "O endereço ainda não foi cadastrado. Posso deixar essa dúvida para atendimento humano."
    if any(x in message for x in ("telefone de contato", "numero de contato", "como entro em contato")):
        return profile.get("contact_phone") or "O telefone de contato ainda não foi cadastrado."
    # An exact FAQ match is safe without inventing a semantic match from one token.
    matches = [x["answer"] for x in profile.get("faqs", []) if norm(x["question"]).strip(" ?!.") == message.strip(" ?!.")]
    if len(set(matches)) == 1:
        return matches[0]
    policies = [x["description"] for x in profile.get("policies", [])
                if norm(x["topic"]) and norm(x["topic"]) in message]
    if len(policies) == 1 and ("?" in body or any(x in message for x in ("qual", "como", "politica", "pode"))):
        return policies[0]
    return None
