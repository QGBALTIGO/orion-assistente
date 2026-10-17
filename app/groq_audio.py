"""Voice-message transcription via Groq: private bridge only, opt-in and allowlisted."""
import logging
import io
import math
import os
import re
import secrets
import wave

import httpx
from fastapi import APIRouter, HTTPException, Request

router = APIRouter(tags=["Áudio privado do Orion"])
logger = logging.getLogger("orion.voice")
VOICE_PHONE = re.compile(r"^55[1-9][0-9](?:9[0-9]{8}|[2-5][0-9]{7})$")
MAX_AUDIO = 2_000_000
MAX_DURATION = 120
MEDIA_EXTENSIONS = {"audio/ogg": "ogg", "audio/webm": "webm", "audio/wav": "wav",
                    "audio/x-wav": "wav", "audio/mpeg": "mp3", "audio/mp4": "m4a"}


def validate_audio(body: bytearray, media_type: str) -> None:
    """Reject mismatched containers before transmitting; the decoder verifies duration too."""
    if not body:
        raise HTTPException(422, "O áudio está vazio. Envie outro áudio ou escreva sua mensagem")
    valid = {
        "audio/ogg": body[:4] == b"OggS",
        "audio/webm": body[:4] == b"\x1a\x45\xdf\xa3",
        "audio/wav": body[:4] == b"RIFF" and body[8:12] == b"WAVE",
        "audio/x-wav": body[:4] == b"RIFF" and body[8:12] == b"WAVE",
        "audio/mpeg": body[:3] == b"ID3" or (len(body) >= 2 and body[0] == 255 and body[1] & 224 == 224),
        "audio/mp4": len(body) >= 12 and body[4:8] == b"ftyp",
    }.get(media_type, False)
    if not valid:
        raise HTTPException(415, "Não consegui reconhecer esse áudio. Envie um áudio válido ou escreva a mensagem")
    if media_type in {"audio/wav", "audio/x-wav"}:
        try:
            with wave.open(io.BytesIO(body), "rb") as audio:
                duration = audio.getnframes() / audio.getframerate()
                if not 0 < duration <= MAX_DURATION:
                    raise HTTPException(413, "Envie um áudio de até dois minutos")
                if audio.getnchannels() not in (1, 2) or not 8000 <= audio.getframerate() <= 192000:
                    raise ValueError()
                # A valid header cannot disguise a truncated PCM payload.
                expected = audio.getnframes() * audio.getnchannels() * audio.getsampwidth()
                if len(audio.readframes(audio.getnframes())) != expected:
                    raise ValueError()
        except (wave.Error, EOFError, ValueError, ZeroDivisionError):
            raise HTTPException(415, "O arquivo de áudio está incompleto ou não é compatível") from None


async def transcribe_request(request: Request, *, preview: bool = False) -> dict:
    """One bounded provider request. Never save media, log it, or execute partial text."""
    media_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    supported = MEDIA_EXTENSIONS if preview else {k: MEDIA_EXTENSIONS[k] for k in ("audio/ogg", "audio/webm")}
    if media_type not in supported:
        raise HTTPException(415, "Formato de áudio não suportado")
    try:
        declared = int(request.headers.get("content-length", "0"))
    except ValueError:
        raise HTTPException(400, "Tamanho de áudio inválido") from None
    if declared < 0 or declared > MAX_AUDIO:
        raise HTTPException(413, "Áudio excede limite de 2 MB")
    body = bytearray()
    try:
        async for chunk in request.stream():
            if len(body) + len(chunk) > MAX_AUDIO:
                raise HTTPException(413, "Áudio excede limite de 2 MB")
            body.extend(chunk)
        validate_audio(body, media_type)
        api_key = os.getenv("GROQ_API_KEY", "")
        if not api_key:
            raise HTTPException(503, "Transcrição indisponível")
        async with httpx.AsyncClient(timeout=25) as client:
            res = await client.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                headers={"Authorization": "Bearer " + api_key},
                files={"file": ("message." + supported[media_type], bytes(body), media_type)},
                data={"model": "whisper-large-v3-turbo", "language": "pt",
                      "response_format": "verbose_json", "temperature": "0"},
            )
        if res.status_code != 200:
            logger.warning("groq_audio_http=%s", res.status_code)
            raise HTTPException(502, "Não consegui transcrever agora. Tente novamente mais tarde ou escreva a mensagem")
        try:
            data = res.json()
            raw, duration = data["text"], data["duration"]
            if (not isinstance(raw, str) or not raw.strip() or isinstance(duration, bool)
                    or not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration <= 0):
                raise ValueError()
        except (ValueError, KeyError, AttributeError, TypeError):
            raise HTTPException(502, "Não consegui entender esse áudio com segurança. Pode escrever ou gravar novamente?") from None
        if duration > MAX_DURATION:
            raise HTTPException(413, "Envie um áudio de até dois minutos")
        if len(raw.strip()) > 1500:
            raise HTTPException(422, "Esse áudio tem muita informação. Divida em áudios menores para eu considerar tudo")
        return {"text": raw.strip()}
    except httpx.RequestError:
        raise HTTPException(503, "O serviço de áudio está indisponível. Pode escrever a mensagem?") from None
    finally:
        body[:] = b"\x00" * len(body)


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
    return await transcribe_request(request)
