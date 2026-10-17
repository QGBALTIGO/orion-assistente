"""Simulador Groq autenticado, somente leitura, separado do WhatsApp real."""
import os
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy import text

from app.catalog_api import Actor, authenticate, authorize, support_only
from app.catalog_schemas import StrictModel
from app.database import get_session_factory, tenant_transaction
from app.groq_ai import MODELS, GroqServiceError, interpret

router = APIRouter(prefix="/api/v1/tenants/{tenant_id}/ai", tags=["IA em simulação"])


class HistoryMessage(StrictModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=1500)


class PreviewRequest(StrictModel):
    message: str = Field(min_length=1, max_length=1500)
    history: list[HistoryMessage] = Field(default_factory=list, max_length=8)


@router.get("/status")
async def status(tenant_id: UUID, actor: Actor = Depends(authenticate)):
    authorize(actor, tenant_id)
    support_only(actor)
    model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    return {
        "provider": "groq",
        "model": model if model in MODELS else None,
        "configured": bool(os.getenv("GROQ_API_KEY")),
        "preview_enabled": os.getenv("GROQ_PREVIEW_ENABLED", "0") == "1",
        "whatsapp_automation_enabled": False,
    }


@router.post("/preview")
async def preview(tenant_id: UUID, payload: PreviewRequest,
                  actor: Actor = Depends(authenticate)):
    authorize(actor, tenant_id, write=True)
    support_only(actor)
    if os.getenv("GROQ_PREVIEW_ENABLED", "0") != "1":
        raise HTTPException(423, "Prévia de IA desativada")
    key = os.getenv("GROQ_API_KEY", "")
    if not key:
        raise HTTPException(503, "Groq ainda não configurada")

    async with get_session_factory()() as session:
        async with tenant_transaction(session, tenant_id):
            business = (await session.execute(text(
                "SELECT name,timezone FROM tenants WHERE id=:tenant AND status='active'"
            ), {"tenant": tenant_id})).mappings().first()
            if business is None:
                raise HTTPException(404, "Estabelecimento indisponível")
            services = (await session.execute(text("""
                SELECT id,name,price_cents,duration_min FROM services
                WHERE tenant_id=:tenant AND active ORDER BY name LIMIT 60
            """), {"tenant": tenant_id})).mappings().all()
    catalog = [dict(item) for item in services]
    try:
        decision = await interpret(
            message=payload.message,
            history=[item.model_dump() for item in payload.history],
            catalog=catalog, key=key,
            model=os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
        )
    except GroqServiceError as exc:
        raise HTTPException(exc.status, exc.message) from None

    chosen_ids = set(decision.service_ids)
    selected = [service for service in catalog if service["id"] in chosen_ids]
    price = sum(item["price_cents"] for item in selected)
    duration = sum(item["duration_min"] for item in selected)
    selected_public = [
        {
            "id": str(item["id"]), "name": item["name"],
            "price_cents": item["price_cents"], "duration_min": item["duration_min"],
        } for item in selected
    ]
    # Dados financeiros e horários nunca vêm do modelo de linguagem.
    if decision.intent == "humano":
        answer = "Entendi que você quer falar com um atendente. Simulação: nenhuma transferência foi aberta."
    elif selected:
        names = " e ".join(item["name"] for item in selected)
        answer = (
            f"Encontrei {names} no catálogo da {business['name']}. "
            f"Total: R$ {price / 100:.2f} e duração dos serviços: {duration} minutos."
        )
        if decision.intent == "agendar":
            answer += " Para agendar, ainda preciso verificar a agenda real e sua confirmação."
    else:
        answer = (
            f"Posso ajudar com os serviços da {business['name']}. "
            "Qual serviço você procura? Não encontrei uma correspondência segura no catálogo."
        )
    return {
        "mode": "simulation",
        "provider": "groq",
        "intent": decision.intent,
        "requested_when_unverified": decision.requested_when,
        "services": selected_public,
        "price_cents": price if selected else None,
        "duration_min": duration if selected else None,
        "answer": answer,
        "booking_created": False,
        "calendar_event_created": False,
        "human_handoff_created": False,
        "whatsapp_message_sent": False,
    }
