"""Complete, bounded audio transcription for subscription WhatsApp devices.

The caller authenticates the device before passing the request, then owns the
durable quota/attempt and lease/generation/revision checks through ``authorize``.
This module never accepts a provider URL, performs a send, or invokes the legacy
assistant. It decodes local media before reserving quota or contacting Groq.

Provider contract: https://console.groq.com/docs/speech-to-text (2026-11-03).
"""
from __future__ import annotations

import asyncio
import json
import math
import os
from pathlib import Path
import shutil
import tempfile
from typing import Awaitable, Callable

from fastapi import HTTPException
import httpx
from app.qr_audio_quality import reliable_transcript


MAX_AUDIO_BYTES = 25_000_000
MAX_TRANSCRIPT_CHARS = 120_000
MAX_PROVIDER_BYTES = 1_000_000
MAX_MINUTES = 30
PROVIDER_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
MODEL = "whisper-large-v3-turbo"
FORMATS = {
    "audio/ogg": ("ogg", "ogg"),
    "audio/opus": ("ogg", "ogg"),
    "audio/webm": ("webm", "matroska"),
    "audio/wav": ("wav", "wav"),
    "audio/x-wav": ("wav", "wav"),
    "audio/mpeg": ("mp3", "mp3"),
    "audio/mp4": ("m4a", "mov"),
}
# One decoder per API process. The caller also reserves durable, tenant-scoped
# ASR concurrency; this semaphore bounds local CPU/memory before that phase.
_decoder_slot = asyncio.Semaphore(1)


def _error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status, {"code": code, "message": message})


def audio_available() -> bool:
    """Read-only readiness; no provider probe, media decode or quota charge."""
    return bool(os.getenv("GROQ_API_KEY", "").strip()
                and shutil.which("ffmpeg") and shutil.which("ffprobe"))


def _check_container(body: bytearray, media_type: str) -> None:
    if not body:
        raise _error(422, "DEVICE_AUDIO_EMPTY", "O áudio está vazio. Envie outro áudio.")
    prefix = body[:12]
    valid = {
        "audio/ogg": prefix[:4] == b"OggS",
        "audio/opus": prefix[:4] == b"OggS",
        "audio/webm": prefix[:4] == b"\x1a\x45\xdf\xa3",
        "audio/wav": prefix[:4] == b"RIFF" and prefix[8:12] == b"WAVE",
        "audio/x-wav": prefix[:4] == b"RIFF" and prefix[8:12] == b"WAVE",
        "audio/mpeg": prefix[:3] == b"ID3" or (
            len(prefix) >= 2 and prefix[0] == 255 and prefix[1] & 224 == 224),
        "audio/mp4": len(prefix) >= 12 and prefix[4:8] == b"ftyp",
    }.get(media_type, False)
    if not valid:
        raise _error(415, "DEVICE_AUDIO_INVALID", "O arquivo não contém um áudio compatível.")


async def _read_body(request) -> tuple[bytearray, str]:
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type not in FORMATS:
        raise _error(415, "DEVICE_AUDIO_FORMAT", "Formato de áudio não suportado.")
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            size = int(declared)
        except (ValueError, TypeError):
            raise _error(400, "DEVICE_AUDIO_SIZE", "Tamanho de áudio inválido.") from None
        if size < 0:
            raise _error(400, "DEVICE_AUDIO_SIZE", "Tamanho de áudio inválido.")
        if size > MAX_AUDIO_BYTES:
            raise _error(413, "DEVICE_AUDIO_TOO_LARGE", "O áudio deve ter até 25 MB.")
    body = bytearray()
    try:
        async for chunk in request.stream():
            if len(body) + len(chunk) > MAX_AUDIO_BYTES:
                raise _error(413, "DEVICE_AUDIO_TOO_LARGE", "O áudio deve ter até 25 MB.")
            body.extend(chunk)
        _check_container(body, media_type)
        return body, media_type
    except BaseException:
        body[:] = b"\x00" * len(body)
        raise


async def _command(arguments: list[str], *, timeout: float, capture: bool = False) -> bytes:
    """Execute fixed decoder arguments; never expose decoder stderr or input."""
    process = await asyncio.create_subprocess_exec(
        *arguments, stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE if capture else asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        async with asyncio.timeout(timeout):
            output, _ = await process.communicate()
    except BaseException:
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise
    if process.returncode != 0:
        raise _error(415, "DEVICE_AUDIO_INVALID", "Não foi possível ler o áudio completo.")
    if capture and len(output or b"") > 65_536:
        raise _error(415, "DEVICE_AUDIO_INVALID", "Não foi possível verificar o áudio.")
    return output or b""


async def _normalize_audio(body: bytearray, media_type: str, max_seconds: int) -> tuple[bytearray, float]:
    """Decode samples into a small, metadata-free voice container before ASR.

    Decoding stops at max+1 seconds, enough to reject overlong media without
    processing an attacker-controlled duration. The forced input demuxer and
    local-file-only protocol prevent playlists/network destinations. A complete
    valid input within the limit is never clipped. Temporary files are removed
    on success, failure, cancellation and timeout.
    """
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise _error(503, "DEVICE_AUDIO_UNAVAILABLE", "A transcrição está temporariamente indisponível.")
    extension, demuxer = FORMATS[media_type]
    async with _decoder_slot:
        with tempfile.TemporaryDirectory(prefix="orion-device-audio-") as folder:
            source, target = Path(folder) / ("source." + extension), Path(folder) / "normalized.ogg"
            for path, contents in ((source, body), (target, b"")):
                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(contents)
            await _command([
                ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-xerror", "-y",
                "-max_alloc", "33554432", "-protocol_whitelist", "file",
                "-err_detect", "explode", "-f", demuxer, "-i", str(source), "-map", "0:a:0",
                "-map_metadata", "-1", "-map_chapters", "-1", "-vn", "-sn", "-dn",
                "-threads", "1", "-ac", "1", "-ar", "16000",
                "-t", str(max_seconds + 1), "-c:a", "libopus", "-b:a", "24k",
                "-vbr", "off", "-application", "voip", "-f", "ogg", str(target),
            ], timeout=50)
            measured = await _command([
                ffprobe, "-v", "error", "-max_alloc", "33554432",
                "-protocol_whitelist", "file", "-f", "ogg",
                "-show_entries", "format=duration:stream=codec_type",
                "-of", "json", str(target),
            ], timeout=5, capture=True)
            try:
                metadata = json.loads(measured)
                duration = float(metadata["format"]["duration"])
                streams = metadata["streams"]
                if (not math.isfinite(duration) or duration <= 0
                        or len(streams) != 1 or streams[0]["codec_type"] != "audio"):
                    raise ValueError()
            except (ValueError, TypeError, KeyError, IndexError):
                raise _error(415, "DEVICE_AUDIO_INVALID", "Não foi possível verificar a duração do áudio.") from None
            # Opus carries encoder padding (typically 6.5ms); it is not extra
            # speech. The additional second decoded above still catches media
            # beyond the chosen duration, without trusting a declared header.
            if duration > max_seconds + 0.05:
                raise _error(413, "DEVICE_AUDIO_TOO_LONG", "O áudio ultrapassa a duração configurada.")
            if target.stat().st_size > MAX_AUDIO_BYTES:
                raise _error(413, "DEVICE_AUDIO_TOO_LARGE", "O áudio deve ter até 25 MB.")
            normalized = bytearray(target.read_bytes())
            if not normalized:
                raise _error(415, "DEVICE_AUDIO_INVALID", "O áudio não contém som compatível.")
            return normalized, min(duration, float(max_seconds))


async def _provider_transcribe(audio: bytearray, duration: float, max_seconds: int) -> str:
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key:
        raise _error(503, "DEVICE_AUDIO_UNAVAILABLE", "A transcrição está temporariamente indisponível.")
    response_body = bytearray()
    try:
        # No redirect, inherited proxy, retry or user-selected endpoint. The
        # callback has already reserved exactly one durable provider attempt.
        async with httpx.AsyncClient(timeout=httpx.Timeout(55, connect=10),
                                     follow_redirects=False, trust_env=False) as client:
            async with client.stream(
                "POST", PROVIDER_URL,
                headers={"Authorization": "Bearer " + api_key},
                files={"file": ("message.ogg", bytes(audio), "audio/ogg")},
                data={"model": MODEL, "language": "pt", "response_format": "verbose_json", "temperature": "0", "timestamp_granularities[]": "segment"},
            ) as response:
                if response.status_code != 200:
                    raise _error(503, "DEVICE_AUDIO_PROVIDER_FAILED", "Não foi possível transcrever o áudio agora.")
                async for chunk in response.aiter_bytes():
                    if len(response_body) + len(chunk) > MAX_PROVIDER_BYTES:
                        raise _error(502, "DEVICE_AUDIO_PROVIDER_INVALID", "O serviço retornou uma transcrição inválida.")
                    response_body.extend(chunk)
        try:
            result = json.loads(response_body)
            text, provider_duration = result["text"], result["duration"]
            if (not isinstance(text, str) or not text.strip()
                    or isinstance(provider_duration, bool)
                    or not isinstance(provider_duration, (int, float))
                    or not math.isfinite(provider_duration) or provider_duration <= 0):
                raise ValueError()
            if (provider_duration > max_seconds + 0.05
                    or abs(provider_duration - duration) > max(2, duration * 0.01)):
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            raise _error(502, "DEVICE_AUDIO_PROVIDER_INVALID", "Não foi possível confirmar a transcrição completa.") from None
        if len(text.strip()) > MAX_TRANSCRIPT_CHARS:
            raise _error(422, "DEVICE_AUDIO_TRANSCRIPT_TOO_LONG", "A transcrição excede o limite de texto. Divida o áudio em partes menores.")
        if not reliable_transcript(result, max_seconds=max_seconds):
            raise _error(422, "DEVICE_AUDIO_UNCLEAR", "Não consegui entender esse áudio com segurança. Grave novamente, mais perto do microfone, ou escreva sua mensagem.")
        return text.strip()
    except httpx.RequestError:
        raise _error(503, "DEVICE_AUDIO_PROVIDER_FAILED", "O serviço de transcrição está temporariamente indisponível.") from None
    finally:
        response_body[:] = b"\x00" * len(response_body)


async def transcribe_audio(request, *, max_minutes: int,
                           authorize: Callable[..., Awaitable[None]]) -> dict:
    """One complete ASR attempt; callback guards both sides of the provider.

    ``authorize(duration, phase='before_provider')`` reserves quota and checks
    current ownership, settings and fencing. ``after_provider`` rechecks those
    permissions without charging again. The authenticated endpoint must reject
    a replay before calling this helper and persist uncertain provider outcomes
    without automatically repeating a potentially billed request.
    """
    if isinstance(max_minutes, bool) or not isinstance(max_minutes, int) or not 1 <= max_minutes <= MAX_MINUTES:
        raise _error(422, "DEVICE_AUDIO_LIMIT", "Escolha uma duração de 1 a 30 minutos.")
    if not callable(authorize):
        raise TypeError("device_audio_authorizer_required")
    if not audio_available():
        raise _error(503, "DEVICE_AUDIO_UNAVAILABLE", "A transcrição está temporariamente indisponível.")
    body, normalized = bytearray(), bytearray()
    try:
        async with asyncio.timeout(120):
            body, media_type = await _read_body(request)
            normalized, duration = await _normalize_audio(body, media_type, max_minutes * 60)
            await authorize(duration, phase="before_provider")
            text = await _provider_transcribe(normalized, duration, max_minutes * 60)
            await authorize(duration, phase="after_provider")
            return {"text": text, "duration_seconds": duration}
    except (TimeoutError, OSError):
        raise _error(503, "DEVICE_AUDIO_TIMEOUT", "A transcrição não pôde ser concluída agora.") from None
    finally:
        body[:] = b"\x00" * len(body)
        normalized[:] = b"\x00" * len(normalized)
