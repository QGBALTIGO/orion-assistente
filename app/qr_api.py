"""API protegida para QR/código e caixa de conversas do dispositivo vinculado."""
import os
from pathlib import Path
from typing import Literal
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.catalog_api import Actor, authenticate, support_only

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
    return await _bridge("/internal/chats")


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
