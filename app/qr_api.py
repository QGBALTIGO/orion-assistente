"""Proxy de QR Code para um dispositivo de TESTE, autenticado por suporte.

Nenhuma informação de pareamento é publicada sem Bearer operacional válido.
A sessão/credencial do WhatsApp vive exclusivamente no volume do bridge.
"""
import os

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pathlib import Path

from app.catalog_api import Actor, authenticate, support_only

router=APIRouter(tags=["WhatsApp — QR de dispositivo de teste"])


def _config() -> tuple[str,str]:
    url=os.getenv("QR_BRIDGE_URL","").rstrip("/")
    token=os.getenv("QR_BRIDGE_TOKEN","")
    if (not url.startswith("http://") or "@" in url or "?" in url
            or "#" in url or len(token)<32):
        raise HTTPException(503,"Conexão por QR ainda não configurada")
    return url,token


async def _bridge(path:str,method:str="GET") -> dict:
    url,token=_config()
    try:
        async with httpx.AsyncClient(timeout=8.0,trust_env=False) as client:
            response=await client.request(
                method,url+path,headers={"X-Orion-Bridge-Key":token},
            )
        if response.status_code>=400:
            raise HTTPException(502,"Serviço QR indisponível ou recusou operação")
        obj=response.json()
        if not isinstance(obj,dict):
            raise ValueError("invalid bridge payload")
        return obj
    except (httpx.RequestError,ValueError):
        raise HTTPException(503,"Serviço QR temporariamente inacessível") from None


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


@router.get("/conectar-whatsapp",include_in_schema=False)
async def qr_page():
    # Conteúdo estático sem segredos; toda chamada autenticada no backend.
    return FileResponse(
        Path(__file__).parent/"static"/"qr_connect.html",
        headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"},
    )
