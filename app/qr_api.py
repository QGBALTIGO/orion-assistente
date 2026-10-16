"""API protegida para QR/código e caixa de conversas do dispositivo vinculado."""
import os
from pathlib import Path
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException
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


@router.post("/api/v1/qr/send")
async def qr_send(data:SendPayload,actor:Actor=Depends(authenticate)):
    support_only(actor)
    return await _bridge("/internal/send","POST",data.model_dump())


@router.get("/conectar-whatsapp",include_in_schema=False)
async def qr_page():
    # Mesmo painel/login em cookie; tela na aba "Conectar dispositivo".
    from fastapi.responses import RedirectResponse
    return RedirectResponse("/panel?aba=dispositivo",status_code=303)
