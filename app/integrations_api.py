"""OAuth Google Calendar e CSV Excel protegidos por RBAC/RLS."""
import os
import secrets
from datetime import datetime,timezone
from urllib.parse import urlencode
from uuid import UUID

import httpx
from cryptography.fernet import Fernet
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.catalog_api import Actor, authenticate, authorize, support_only
from app.csv_export import csv_document
from app.database import get_session_factory, tenant_transaction

router=APIRouter(tags=["Integrações opcionais"])

def oauth_config():
    cid=os.getenv("GOOGLE_CLIENT_ID")
    secret=os.getenv("GOOGLE_CLIENT_SECRET")
    redirect=os.getenv("GOOGLE_REDIRECT_URI")
    fernet=os.getenv("INTEGRATION_FERNET_KEY")
    if not all((cid,secret,redirect,fernet)) or not redirect.startswith("https://"):
        raise HTTPException(503,"OAuth Google ainda não foi configurado")
    try:Fernet(fernet.encode())
    except Exception:raise HTTPException(503,"Criptografia Google não configurada") from None
    return cid,secret,redirect,fernet


@router.get("/api/v1/tenants/{tenant_id}/google/connect")
async def connect_google(tenant_id:UUID,actor:Actor=Depends(authenticate)):
    authorize(actor,tenant_id,write=True)
    support_only(actor)
    cid,_,redirect,_=oauth_config()
    async with get_session_factory()() as s:
        async with tenant_transaction(s,tenant_id):
            if not (await s.execute(text("SELECT id FROM tenants WHERE id=:id"),
                                    {"id":tenant_id})).first():
                raise HTTPException(404,"Barbearia não encontrada")
    state=secrets.token_urlsafe(32)
    from redis.asyncio import from_url
    redis=from_url(os.getenv("REDIS_URL","redis://localhost:6379/0"))
    try:
        await redis.set("google:oauth:"+state,str(tenant_id),ex=600,nx=True)
    finally:await redis.aclose()
    url="https://accounts.google.com/o/oauth2/v2/auth?"+urlencode({
        "client_id":cid,"redirect_uri":redirect,"response_type":"code",
        "scope":"https://www.googleapis.com/auth/calendar.events",
        "state":state,"access_type":"offline","prompt":"consent",
    })
    return {"authorization_url":url}


@router.get("/integrations/google/callback",response_class=HTMLResponse)
async def callback_google(code:str,state:str):
    if len(state)>150 or len(code)>2048:
        raise HTTPException(400,"Resposta OAuth inválida")
    cid,client_secret,redirect,fernet=oauth_config()
    from redis.asyncio import from_url
    redis=from_url(os.getenv("REDIS_URL","redis://localhost:6379/0"))
    try:tenant_raw=await redis.getdel("google:oauth:"+state)
    finally:await redis.aclose()
    if not tenant_raw:raise HTTPException(403,"Estado OAuth inválido ou expirado")
    tenant_id=UUID(tenant_raw.decode())
    async with httpx.AsyncClient(timeout=15) as client:
        r=await client.post("https://oauth2.googleapis.com/token",data={
            "client_id":cid,"client_secret":client_secret,"code":code,
            "grant_type":"authorization_code","redirect_uri":redirect
        })
        if r.status_code!=200:
            raise HTTPException(502,"Google recusou autorização")
        result=r.json()
    refresh=result.get("refresh_token","")
    scope=result.get("scope","")
    if not refresh or "https://www.googleapis.com/auth/calendar.events" not in scope.split():
        raise HTTPException(409,"Autorize acesso a eventos do calendário novamente")
    encrypted=Fernet(fernet.encode()).encrypt(refresh.encode())
    async with get_session_factory()() as s:
        async with tenant_transaction(s,tenant_id):
            await s.execute(text("""
                INSERT INTO google_connections (tenant_id,calendar_id,refresh_token_enc,status)
                VALUES (:t,'primary',:token,'active')
                ON CONFLICT (tenant_id) DO UPDATE
                SET refresh_token_enc=EXCLUDED.refresh_token_enc,status='active',
                    updated_at=now(),last_error=NULL
            """),{"t":tenant_id,"token":encrypted})
    return "<html lang='pt-BR'><body><h1>Google Calendar autorizado</h1><p>Você pode fechar esta página.</p></body></html>"


@router.get("/api/v1/tenants/{tenant_id}/google/status")
async def google_status(tenant_id:UUID,actor:Actor=Depends(authenticate)):
    authorize(actor,tenant_id)
    async with get_session_factory()() as s:
        async with tenant_transaction(s,tenant_id):
            row=(await s.execute(text("""
                SELECT calendar_id,status,last_error FROM google_connections WHERE tenant_id=:tenant
            """),{"tenant":tenant_id})).mappings().first()
            return dict(row) if row else {"status":"not_connected"}


@router.post("/api/v1/tenants/{tenant_id}/google/revoke")
async def revoke_google(tenant_id:UUID,actor:Actor=Depends(authenticate)):
    authorize(actor,tenant_id,write=True)
    async with get_session_factory()() as s:
        async with tenant_transaction(s,tenant_id):
            await s.execute(text("""
                UPDATE google_connections SET refresh_token_enc=NULL,status='revoked',
                    updated_at=now() WHERE tenant_id=:tenant
            """),{"tenant":tenant_id})
    return {"status":"revoked"}


@router.get("/api/v1/tenants/{tenant_id}/export/appointments.csv")
async def export_appointments(tenant_id:UUID,actor:Actor=Depends(authenticate)):
    authorize(actor,tenant_id)
    async with get_session_factory()() as s:
        async with tenant_transaction(s,tenant_id):
            rows=(await s.execute(text("""
                SELECT a.protocol,a.start_at,a.end_at,a.status,p.name,c.name,c.whatsapp_e164,
                       a.total_cents
                FROM appointments a
                JOIN professionals p ON p.tenant_id=a.tenant_id AND p.id=a.professional_id
                JOIN customers c ON c.tenant_id=a.tenant_id AND c.id=a.customer_id
                WHERE a.tenant_id=:tenant ORDER BY a.start_at LIMIT 10000
            """),{"tenant":tenant_id})).all()
    safe_rows=[(protocol,start.isoformat(),end.isoformat(),status,pro,customer,phone,
                f"{cents/100:.2f}".replace(".",","))
               for protocol,start,end,status,pro,customer,phone,cents in rows]
    payload=csv_document(["Protocolo","Início UTC","Fim UTC","Situação",
                          "Profissional","Cliente","Telefone","Total (R$)"],safe_rows)
    return Response(payload,media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition":'attachment; filename="agendamentos.csv"',
                             "Cache-Control":"no-store"})
