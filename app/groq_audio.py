"""Voice-message transcription via Groq: private bridge only, opt-in and allowlisted."""
import logging
import os
import re
import secrets

import httpx
from fastapi import APIRouter, HTTPException, Request

router = APIRouter(tags=["Áudio privado do Orion"])
logger = logging.getLogger("orion.voice")
VOICE_PHONE = re.compile(r"^55[1-9][0-9](?:9[0-9]{8}|[2-5][0-9]{7})$")
MAX_AUDIO = 2_000_000


@router.post("/internal/qr/ai/transcribe", include_in_schema=False)
async def transcribe_voice(request: Request):
    key = os.getenv("QR_BRIDGE_TOKEN", "")
    received = request.headers.get("x-orion-bridge-key", "")
    if len(key) < 32 or not secrets.compare_digest(key, received):
        raise HTTPException(401, "Acesso negado")
    if os.getenv("QR_AI_ENABLED") != "1" or os.getenv("QR_AI_AUDIO_ENABLED") != "1":
        raise HTTPException(423, "Transcrição não habilitada")
    phone = request.headers.get("x-orion-phone", "")
    allowed = {x.strip() for x in os.getenv("QR_AI_ALLOWED_SENDERS", "").split(",") if x.strip()}
    if not VOICE_PHONE.fullmatch(phone) or not allowed or phone not in allowed:
        raise HTTPException(403, "Contato não autorizado")
    media_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    extensions = {"audio/ogg": "ogg", "audio/webm": "webm"}
    if media_type not in extensions:
        raise HTTPException(415, "Formato de áudio não suportado")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_AUDIO:
            raise HTTPException(413, "Áudio excede limite de 2 MB")
    if not body:
        raise HTTPException(422, "Áudio vazio")
    api_key = os.getenv("GROQ_API_KEY", "")
    if not api_key:
        raise HTTPException(503, "Transcrição indisponível")
    try:
        async with httpx.AsyncClient(timeout=25) as client:
            res = await client.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                headers={"Authorization": "Bearer " + api_key},
                files={"file": ("message." + extensions[media_type],
                                bytes(body), media_type)},
                data={"model": "whisper-large-v3-turbo", "language": "pt",
                      "response_format": "json"},
            )
        if res.status_code != 200:
            logger.warning("groq_audio_http=%s", res.status_code)
            raise HTTPException(502, "Falha temporária na transcrição")
        try:
            raw = res.json().get("text", "")
            if not isinstance(raw, str) or not raw.strip():
                raise ValueError()
            return {"text": raw.strip()[:1500]}
        except (ValueError, AttributeError, TypeError):
            raise HTTPException(502, "Transcrição inválida") from None
    except httpx.RequestError:
        raise HTTPException(503, "Provedor de áudio indisponível") from None
    finally:
        body[:] = b"\x00" * len(body)
