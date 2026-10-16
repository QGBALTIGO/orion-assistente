"""Asaas sandbox: autenticação por token de webhook e ativação só após reconciliação."""
import json
import os
import secrets
from uuid import UUID

import httpx
from fastapi import APIRouter,Depends,HTTPException,Request
from sqlalchemy import text

from app.catalog_api import Actor,authenticate,support_only,support_session_factory

router=APIRouter(tags=["Assinatura SaaS"])


class AsaasSandboxClient:
    BASE="https://api-sandbox.asaas.com/v3"

    async def get_payment(self,payment_id:str) -> dict:
        key=os.getenv("ASAAS_SANDBOX_API_KEY")
        if not key:
            raise RuntimeError("Asaas sandbox não configurado")
        if not payment_id or "/" in payment_id or len(payment_id)>120:
            raise ValueError("ID de pagamento inválido")
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp=await client.get(self.BASE+"/payments/"+payment_id,
                 headers={"access_token":key,"User-Agent":"OrionBarbearias/0.6"})
            resp.raise_for_status()
            return resp.json()


def verify_webhook_token(provided:str|None) -> bool:
    expected=os.getenv("ASAAS_WEBHOOK_TOKEN","")
    return len(expected)>=32 and provided is not None and secrets.compare_digest(expected,provided)


@router.post("/webhooks/asaas")
async def asaas_webhook(request:Request):
    if not os.getenv("ASAAS_WEBHOOK_TOKEN"):
        raise HTTPException(503,"Webhook Asaas não configurado")
    if not verify_webhook_token(request.headers.get("asaas-access-token")):
        raise HTTPException(401,"Autenticação recusada")
    raw=await request.body()
    if len(raw)>65_536:raise HTTPException(413,"Payload muito grande")
    try:data=json.loads(raw)
    except (json.JSONDecodeError,UnicodeDecodeError):
        raise HTTPException(400,"JSON inválido") from None
    if not isinstance(data,dict):raise HTTPException(400,"Estrutura inválida")
    event_id=data.get("id")
    event=data.get("event")
    payment=data.get("payment",{})
    if not isinstance(payment,dict) or not isinstance(event_id,str) or not isinstance(event,str):
        raise HTTPException(400,"Evento inválido")
    external_subscription_id=payment.get("subscription")
    payment_id=payment.get("id")
    if not event_id or len(event_id)>120 or len(event)>80:
        raise HTTPException(400,"Identificador inválido")
    if not isinstance(external_subscription_id,str) or not isinstance(payment_id,str):
        return {"received":True}
    if len(external_subscription_id)>120 or len(payment_id)>120:
        raise HTTPException(400,"Identificador inválido")
    # Fonte de tenant é a assinatura previamente criada por suporte, NÃO o webhook.
    async with support_session_factory()() as s:
        async with s.begin():
            tenant=(await s.execute(text("""
                SELECT tenant_id FROM subscriptions
                WHERE external_subscription_id=:ext
            """),{"ext":external_subscription_id})).scalar_one_or_none()
            if tenant:
                await s.execute(text("""
                    INSERT INTO billing_events (tenant_id,provider_event_id,external_payment_id,event_type)
                    VALUES (:tenant,:event_id,:payment_id,:event_type)
                    ON CONFLICT (provider_event_id) DO NOTHING
                """),{"tenant":tenant,"event_id":event_id,"payment_id":payment_id,"event_type":event})
    # Não ativar assinatura com base apenas no webhook.
    return {"received":True}


async def reconcile_event(event_id:str,provider=None):
    """Consulta provedor real sandbox; injete Fake para testes sem cobrança."""
    provider=provider or AsaasSandboxClient()
    async with support_session_factory()() as s:
        async with s.begin():
            stored=(await s.execute(text("""
                SELECT id,tenant_id,external_payment_id,status FROM billing_events
                WHERE provider_event_id=:event FOR UPDATE
            """),{"event":event_id})).mappings().first()
            if not stored:return "unknown"
            if stored["status"]=="verified":return "already_verified"
            sub=(await s.execute(text("""
                SELECT id,external_subscription_id FROM subscriptions
                WHERE tenant_id=:tenant FOR UPDATE
            """),{"tenant":stored["tenant_id"]})).mappings().first()
            if not sub:return "missing_subscription"
            data=await provider.get_payment(stored["external_payment_id"])
            if data.get("id")!=stored["external_payment_id"] or data.get("subscription")!=sub["external_subscription_id"]:
                return "mismatch"
            status=data.get("status")
            next_status={"RECEIVED":"active","CONFIRMED":"active","OVERDUE":"past_due"}.get(status)
            if not next_status:
                await s.execute(text("""
                    UPDATE billing_events SET status='ignored',verified_at=now()
                    WHERE id=:id
                """),{"id":stored["id"]})
                return "ignored"
            await s.execute(text("""
                UPDATE subscriptions SET status=:status,updated_at=now(),
                    last_verified_payment_at=CASE WHEN :status='active' THEN now()
                      ELSE last_verified_payment_at END
                WHERE id=:id
            """),{"status":next_status,"id":sub["id"]})
            await s.execute(text("""
                UPDATE billing_events SET status='verified',verified_at=now()
                WHERE id=:id
            """),{"id":stored["id"]})
            return next_status


@router.get("/api/v1/admin/subscriptions")
async def admin_subscriptions(actor:Actor=Depends(authenticate)):
    support_only(actor)
    async with support_session_factory()() as s:
        rows=(await s.execute(text("""
            SELECT s.tenant_id,s.status,s.external_subscription_id,p.code,p.monthly_cents
            FROM subscriptions s JOIN plans p ON p.id=s.plan_id
            ORDER BY s.updated_at DESC LIMIT 200
        """))).mappings().all()
        from fastapi.encoders import jsonable_encoder
        return jsonable_encoder(rows)


@router.get("/api/v1/admin/plans")
async def admin_plans(actor:Actor=Depends(authenticate)):
    support_only(actor)
    async with support_session_factory()() as s:
        rows=(await s.execute(text("""
            SELECT id,code,name,monthly_cents,max_professionals,max_messages_month
            FROM plans WHERE active ORDER BY monthly_cents
        """))).mappings().all()
        from fastapi.encoders import jsonable_encoder
        return jsonable_encoder(rows)
