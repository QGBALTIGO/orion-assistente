"""API protegida para QR/código e caixa de conversas do dispositivo vinculado."""
import asyncio
import os
import secrets
import unicodedata
from pathlib import Path
from typing import Literal
from urllib.parse import urlencode
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.catalog_api import Actor, authenticate, support_only
from app.catalog_schemas import StrictModel
from app.customer_names import WhatsAppProfile, contact_phone, display_customer, merge_whatsapp_name, useful_name
from app.database import get_session_factory, tenant_transaction

router=APIRouter(tags=["WhatsApp: dispositivo vinculado"])
BACKEND_ERRORS={
    "already_connected":"Dispositivo já conectado.",
    "O WhatsApp não está conectado":"Conecte o dispositivo primeiro.",
    "Aguarde a disponibilidade do QR e tente novamente":"O código ainda está sendo preparado. Aguarde alguns segundos.",
    "Geração em andamento":"Já existe um pedido de código em andamento.",
    "Código anterior expirou. Reinicie a conexão para gerar outro.":"O código expirou. Clique em Reiniciar conexão e tente novamente.",
    "pairing_in_progress":"Aguarde a confirmação do código antes de tentar outra conexão.",
    "not_logged_out":"A sessão não foi revogada. Tente atualizar a conexão.",
    "Sessão revogada: reinicie o vínculo pelo WhatsApp":"Sessão anterior recusada. Clique em Refazer vínculo.",
    "Aguarde um minuto antes de enviar mais mensagens":"Limite temporário de envios. Aguarde um minuto.",
    "Número inválido. Informe DDI + DDD + telefone":"Número inválido, informe DDI e DDD.",
    "Mensagem deve ter entre 1 e 1200 caracteres":"A mensagem deve conter entre 1 e 1200 caracteres.",
    "not_connected":"Conecte o WhatsApp antes de alterar as configurações.",
    "restart_not_allowed":"A reinicialização não está disponível agora ou está em intervalo de segurança.",
    "disconnect_not_allowed":"Desconexão não autorizada ou dispositivo indisponível.",
    "disconnect_failed":"Não foi possível confirmar a desconexão. Verifique o dispositivo.",
    "Chatbot não integrado ao canal vinculado":"O chatbot ainda não está integrado neste canal.",
    "Serviço de transcrição não configurado":"Configure primeiro o serviço privado de transcrição.",
    "Dispositivo ainda indisponível":"O dispositivo ainda não está pronto.",
}
CONTACT_NAME_TIMEOUT = 1.5
CONTACT_NAME_SOURCES = frozenset({"contact_name", "verified_name", "push_name"})


def qr_contact_tenant() -> UUID | None:
    """The existing server-side QR binding is never chosen by a panel request."""
    try:
        return UUID(os.environ["QR_AI_TENANT_ID"])
    except (KeyError, ValueError):
        return None


async def whatsapp_name_matches(tenant_id: UUID, query: str) -> tuple[list[str], str | None]:
    """Reverse lookup is limited to the existing registry; SQL still owns scope."""
    if (tenant_id != qr_contact_tenant() or not query or
            not any(char.isalpha() or unicodedata.category(char) in {"So", "Sk"} for char in query)):
        return [], None
    try:
        async with asyncio.timeout(CONTACT_NAME_TIMEOUT):
            response = await _bridge("/internal/contact-name-matches", "POST", {"q": query})
    except (HTTPException, TimeoutError, httpx.RequestError):
        return [], None
    account = contact_phone(response.get("account_phone"))
    phones = response.get("phones")
    if account is None or not isinstance(phones, list) or len(phones) > 2000:
        return [], None
    return sorted({"+" + phone for value in phones if (phone := contact_phone(value)) and phone != account}), account


async def whatsapp_names(tenant_id: UUID, phones, *, max_phones=250, expected_account=None) -> dict[str, str]:
    if tenant_id != qr_contact_tenant():
        return {}
    requested = sorted({pn for value in phones if (pn := contact_phone(value))})
    if not requested:
        return {}
    # A page is at most 250 customers; exports have a separate 20k result limit.
    # Only those result phones are requested, with three in-flight calls and a
    # single deadline for all chunks. No address-book enumeration is performed.
    if len(requested) > min(max_phones, 20_000):
        return {}
    semaphore = asyncio.Semaphore(3)
    failed = False

    async def read_chunk(chunk):
        nonlocal failed
        async with semaphore:
            if failed:
                return None
            try:
                return await _bridge("/internal/contact-names", "POST", {"phones": chunk})
            except (HTTPException, httpx.RequestError):
                failed = True
                raise

    try:
        async with asyncio.timeout(CONTACT_NAME_TIMEOUT):
            responses = await asyncio.gather(*(
                read_chunk(requested[index:index + 100])
                for index in range(0, len(requested), 100)
            ), return_exceptions=True)
    except (HTTPException, TimeoutError, httpx.RequestError):
        # Optional metadata cannot make the customer directory unavailable,
        # including during a rollout while an older bridge returns 404.
        return {}
    if any(not isinstance(response, dict) for response in responses):
        return {}
    accounts = {contact_phone(response.get("account_phone")) for response in responses}
    if len(accounts) != 1 or None in accounts:
        return {}
    account_phone = accounts.pop()
    if expected_account is not None and account_phone != expected_account:
        return {}
    names = {}
    asked = set(requested)
    for response in responses:
        items = response.get("items")
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            pn, name = contact_phone(item.get("phone")), useful_name(item.get("display_name"))
            if pn in asked and pn != account_phone and name and item.get("name_source") in CONTACT_NAME_SOURCES:
                names[pn] = name
    return names


async def customer_display_names(tenant_id: UUID, rows: list[dict], *, max_phones=250, expected_account=None) -> list[dict]:
    names = await whatsapp_names(tenant_id, (row.get("phone") for row in rows),
                                 max_phones=max_phones, expected_account=expected_account)
    return [display_customer(row, names.get(contact_phone(row.get("phone")))) for row in rows]


async def saved_chat_names(tenant_id: UUID | None, phones) -> dict[str, str]:
    if tenant_id is None:
        return {}
    wanted = sorted({"+" + pn for value in phones if (pn := contact_phone(value))})
    if not wanted or len(wanted) > 100:
        return {}
    try:
        async with asyncio.timeout(CONTACT_NAME_TIMEOUT):
            async with get_session_factory()() as db:
                async with tenant_transaction(db, tenant_id):
                    rows = (await db.execute(text("""
                        SELECT whatsapp_e164,name FROM customers
                        WHERE tenant_id=:tenant AND deleted_at IS NULL
                          AND whatsapp_e164=ANY(CAST(:phones AS text[]))
                    """), {"tenant": tenant_id, "phones": wanted})).mappings().all()
        return {contact_phone(row["whatsapp_e164"]): name for row in rows
                if (name := useful_name(row["name"]))}
    except (SQLAlchemyError, TimeoutError, ConnectionError, RuntimeError):
        return {}


def _config()->tuple[str,str]:
    url=os.getenv("QR_BRIDGE_URL","").rstrip("/")
    token=os.getenv("QR_BRIDGE_TOKEN","")
    if (not url.startswith("http://") or not url.endswith(".railway.internal:3000")
            or "@" in url or "?" in url or "#" in url or len(token)<32):
        raise HTTPException(503,"Integração WhatsApp ainda não configurada")
    return url,token


async def _bridge(path:str,method:str="GET",payload:dict|None=None,query:dict|None=None)->dict:
    url,token=_config()
    try:
        async with httpx.AsyncClient(timeout=18,trust_env=False) as client:
            r=await client.request(method,url+path,
                    headers={"X-Orion-Bridge-Key":token},json=payload,params=query)
        if r.status_code>=400:
            try:
                error=r.json().get("error","")
            except (ValueError,AttributeError):
                error=""
            raise HTTPException(409 if r.status_code==409 else 422,
                                BACKEND_ERRORS.get(error,"WhatsApp indisponível, tente novamente."))
        obj=r.json()
        if not isinstance(obj,dict):raise ValueError("Formato inválido")
        return obj
    except (httpx.RequestError,ValueError):
        raise HTTPException(503,"Serviço WhatsApp temporariamente inacessível") from None


class PairPayload(BaseModel):
    phone:str=Field(pattern=r"^[1-9]\d{7,14}$")


class CallWindow(BaseModel):
    start: str=Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    end: str=Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    weekdays: list[int]=Field(max_length=7)


class ConnectedSettings(BaseModel):
    model_config={"extra":"forbid"}
    chatbot: bool=False
    transcription: bool=False
    language: Literal["auto","pt","en","es"]="auto"
    callBlocking: bool=False
    callReply: bool=False
    callReplyText: str=Field(default="Não podemos atender ligações agora. Por favor, envie uma mensagem.",max_length=240)
    allowedCallers: list[str]=Field(default_factory=list,max_length=30)
    callSchedule: bool=False
    callHours: CallWindow=Field(default_factory=lambda:CallWindow(
        start="09:00",end="18:00",weekdays=[1,2,3,4,5]
    ))


class ConfirmRestart(BaseModel):
    confirm: Literal["REINICIAR"]


class ConfirmDisconnect(BaseModel):
    confirm: Literal["DESCONECTAR"]


class SendPayload(BaseModel):
    phone:str=Field(pattern=r"^[1-9]\d{7,14}$")
    text:str=Field(min_length=1,max_length=1200)


class ContactName(WhatsAppProfile):
    phone: str = Field(pattern=r"^[1-9][0-9]{7,14}$")


class ContactNames(StrictModel):
    account_phone: str = Field(pattern=r"^[1-9][0-9]{7,14}$")
    items: list[ContactName] = Field(min_length=1, max_length=100)


@router.post("/internal/qr/contact-names", include_in_schema=False)
async def receive_contact_names(payload: ContactNames, request: Request):
    configured = os.getenv("QR_BRIDGE_TOKEN", "")
    given = request.headers.get("x-orion-bridge-key", "")
    if len(configured) < 32 or not secrets.compare_digest(configured, given):
        raise HTTPException(401, "Acesso negado")
    tenant = qr_contact_tenant()
    if tenant is None:
        raise HTTPException(503, "Estabelecimento de atendimento não configurado")
    # This private ingress is independent of chatbot permissions: it never
    # sends, books, creates customers from an address book, or activates anyone.
    # The bridge binds and cancels queued metadata with its current account.
    names = {}
    priority = {"push_name": 0, "verified_name": 1, "contact_name": 2}
    for item in payload.items:
        if item.phone == payload.account_phone or not useful_name(item.name):
            continue
        previous = names.get(item.phone)
        if previous is None or priority[item.source] >= priority[previous.source]:
            names[item.phone] = item
    changed = 0
    async with get_session_factory()() as db:
        async with tenant_transaction(db, tenant):
            active = (await db.execute(text("SELECT id FROM tenants WHERE id=:tenant AND status='active'"),
                                       {"tenant": tenant})).scalar_one_or_none()
            if active is None:
                raise HTTPException(503, "Estabelecimento de atendimento indisponível")
            for phone, item in sorted(names.items()):
                changed += await merge_whatsapp_name(db, tenant, phone, item.name, create=False)
    return {"ok": True, "updated": changed}


@router.get("/health/whatsapp",include_in_schema=False)
async def whatsapp_bridge_health():
    """Liveness de rede API→bridge; nunca revela sessão, QR ou telefone."""
    try:
        await _bridge("/internal/status")
    except HTTPException:
        raise HTTPException(503,"Conector privado indisponível") from None
    return {"status":"ok","bridge":"reachable"}


@router.get("/api/v1/qr/status")
async def qr_status(actor:Actor=Depends(authenticate)):
    support_only(actor)
    return await _bridge("/internal/status")


@router.get("/api/v1/qr/image")
async def qr_image(actor:Actor=Depends(authenticate)):
    support_only(actor)
    return await _bridge("/internal/qr")


@router.post("/api/v1/qr/reconnect")
async def qr_reconnect(actor:Actor=Depends(authenticate)):
    support_only(actor)
    return await _bridge("/internal/reconnect","POST")


@router.post("/api/v1/qr/recover")
async def qr_recover(actor:Actor=Depends(authenticate)):
    """Nova sessão apenas quando credencial anterior foi revogada."""
    support_only(actor)
    return await _bridge("/internal/recover","POST")


@router.post("/api/v1/qr/pair")
async def qr_pair(data:PairPayload,actor:Actor=Depends(authenticate)):
    support_only(actor)
    return await _bridge("/internal/pair","POST",data.model_dump())


@router.get("/api/v1/qr/chats")
async def qr_chats(actor:Actor=Depends(authenticate)):
    support_only(actor)
    result = await _bridge("/internal/chats")
    rows = result.get("items")
    if not isinstance(rows, list):
        return result
    names = await saved_chat_names(qr_contact_tenant(), (row.get("phone") for row in rows if isinstance(row, dict)))
    items = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        metadata = useful_name(row.get("display_name")) or useful_name(row.get("whatsapp_name"))
        saved = names.get(contact_phone(row.get("phone")))
        item = display_customer({**row, "name": saved}, metadata)
        if saved:
            item["name_source"] = "customer"
        items.append(item)
    return {**result, "items": items}


@router.get("/api/v1/qr/messages")
async def qr_messages(phone:str,actor:Actor=Depends(authenticate)):
    support_only(actor)
    phone=PairPayload(phone=phone).phone
    return await _bridge("/internal/messages",query={"phone":phone})


@router.get("/api/v1/qr/recipient")
async def qr_recipient(phone:str=Query(pattern=r"^[1-9]\d{7,14}$"),
                       actor:Actor=Depends(authenticate)):
    """Consulta sob demanda, sem enviar mensagens nem alterar sessão Signal."""
    support_only(actor)
    return await _bridge("/internal/recipient",query={"phone":phone})


@router.post("/api/v1/qr/settings")
async def qr_update_settings(data:ConnectedSettings,actor:Actor=Depends(authenticate)):
    support_only(actor)
    return await _bridge("/internal/settings","POST",data.model_dump())


@router.post("/api/v1/qr/profile/refresh")
async def qr_refresh_profile(actor:Actor=Depends(authenticate)):
    support_only(actor)
    return await _bridge("/internal/profile/refresh","POST",{})


@router.post("/api/v1/qr/restart")
async def qr_restart(data:ConfirmRestart,actor:Actor=Depends(authenticate)):
    support_only(actor)
    return await _bridge("/internal/restart","POST",data.model_dump())


@router.post("/api/v1/qr/disconnect")
async def qr_disconnect(data:ConfirmDisconnect,actor:Actor=Depends(authenticate)):
    support_only(actor)
    return await _bridge("/internal/disconnect","POST",data.model_dump())


@router.post("/api/v1/qr/send")
async def qr_send(data:SendPayload,actor:Actor=Depends(authenticate)):
    support_only(actor)
    return await _bridge("/internal/send","POST",data.model_dump())


@router.get("/conectar-whatsapp",include_in_schema=False)
async def qr_page():
    # Mesmo painel/login em cookie; tela na aba "Conectar dispositivo".
    from fastapi.responses import RedirectResponse
    return RedirectResponse("/panel?aba=dispositivo",status_code=303)
