"""Bounded managed-channel transcription, without assistant/domain authority."""
import asyncio
import os
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request

from app import groq_audio
from app.qr_channels import ChannelIdentity, authenticated_channel, normalize_sender

router = APIRouter(tags=["Áudio privado de canais gerenciados"])


def _enabled():
    if os.getenv("QR_CHANNELS_ENABLED", "0") != "1" or os.getenv("QR_AI_AUDIO_ENABLED", "0") != "1":
        raise HTTPException(423, "Transcrição do canal não habilitada")


async def _assistant_ready(identity):
    from app.qr_ai import _managed_assistant_ready
    return await _managed_assistant_ready(identity)


async def _authorize(request, channel_id, phone, previous=None):
    _enabled()
    identity = await authenticated_channel(request, phone=phone)
    if (not isinstance(identity, ChannelIdentity) or identity.channel_id != channel_id
            or identity.provider != "baileys" or identity.allowed_sender is not True):
        raise HTTPException(403, "Contato não autorizado neste canal")
    if previous is not None and identity != previous:
        raise HTTPException(409, "Autorização do canal mudou")
    if not identity.assistant_enabled or not await _assistant_ready(identity):
        raise HTTPException(423, "Assistente do canal desativado")
    return identity


@router.post("/internal/qr/channels/{channel_id}/audio", include_in_schema=False)
async def transcribe_channel_audio(channel_id: UUID, request: Request):
    # No legacy global tenant, credential or allowlist can authorize this route.
    _enabled()
    try:
        phone = normalize_sender(request.headers.get("x-orion-phone", ""))
    except ValueError:
        raise HTTPException(403, "Contato não autorizado neste canal") from None
    try:
        async with asyncio.timeout(30):
            identity = await _authorize(request, channel_id, phone)
            # Shared decoder/ASR enforces media container, 2MB, <=120s, complete
            # text <=1500 and wiping in finally. Exactly one provider attempt.
            result = await groq_audio.transcribe_request(request)
            await _authorize(request, channel_id, phone, previous=identity)
            return {"text": result["text"], "truncated": False}
    except TimeoutError:
        raise HTTPException(503, "Não consegui transcrever agora. Pode escrever a mensagem?") from None
