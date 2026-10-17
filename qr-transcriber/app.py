"""Private CPU-only Whisper transcription endpoint for the Orion QR bridge.

No public domain, no WhatsApp self-messages, no media persistence.
Audio is bounded by size and duration and is transcribed only on opt-in.
"""
import asyncio
from contextlib import asynccontextmanager
from io import BytesIO
import hmac
import os

import av
from fastapi import FastAPI, HTTPException, Request
from faster_whisper import WhisperModel

MAX_AUDIO_BYTES = 2_000_000
MAX_AUDIO_SECONDS = 120
MAX_TEXT_CHARS = 2_000
VALID_LANGUAGES = frozenset(("auto", "pt", "en", "es"))
MODEL_NAME = "base"  # CPU-friendly; Portuguese supported, higher accuracy than tiny.
_model = None
_transcribe_lock = asyncio.Lock()


def _create_model():
    # Image bakes the model files under /models; never fetch during a request.
    return WhisperModel(
        os.getenv("QR_ASR_MODEL", MODEL_NAME),
        download_root=os.getenv("QR_ASR_MODEL_DIR", "/models"),
        device="cpu",
        compute_type="int8",
        cpu_threads=max(1, min(4, int(os.getenv("QR_ASR_CPU_THREADS", "2")))),
        num_workers=1,
        local_files_only=True,
    )


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _model
    if len(os.getenv("QR_ASR_TOKEN", "")) < 32:
        raise RuntimeError("QR_ASR_TOKEN must have at least 32 characters")
    _model = await asyncio.to_thread(_create_model)
    try:
        yield
    finally:
        _model = None


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)


@app.get("/health")
async def health():
    if _model is None:
        raise HTTPException(503, "model not ready")
    return {"status": "ok", "model_loaded": True}


def _audio_duration(data: bytes):
    try:
        with av.open(BytesIO(data), mode="r") as container:
            streams = [stream for stream in container.streams if stream.type == "audio"]
            if not streams:
                raise ValueError("audio stream missing")
            stream = streams[0]
            # Some OGG voice notes lack metadata duration; decoded samples
            # provide a reliable bound before model inference.
            seconds = 0.0
            for frame in container.decode(stream):
                if frame.sample_rate <= 0:
                    raise ValueError("invalid audio sample rate")
                seconds += frame.samples / frame.sample_rate
                if seconds > MAX_AUDIO_SECONDS:
                    raise ValueError("audio exceeds duration limit")
            if seconds <= 0:
                raise ValueError("empty audio stream")
            return seconds
    except (av.FFmpegError, EOFError, OSError) as err:
        raise ValueError("cannot decode audio") from err


def _recognize(data: bytes, language: str):
    if _model is None:
        raise RuntimeError("model not ready")
    _audio_duration(data)
    segments, _info = _model.transcribe(
        BytesIO(data),
        language=None if language == "auto" else language,
        vad_filter=True,
        beam_size=1,
        condition_on_previous_text=False,
    )
    parts, used = [], 0
    for segment in segments:
        if segment.end > MAX_AUDIO_SECONDS:
            break
        snippet = segment.text.strip()
        if not snippet:
            continue
        remaining = MAX_TEXT_CHARS - used
        if remaining <= 0:
            break
        clipped = snippet[:remaining]
        parts.append(clipped)
        used += len(clipped) + (1 if used else 0)
    return " ".join(parts)[:MAX_TEXT_CHARS]


@app.post("/transcribe")
async def transcribe(request: Request):
    key = os.getenv("QR_ASR_TOKEN", "")
    supplied = request.headers.get("x-orion-asr-key", "")
    if len(key) < 32 or not hmac.compare_digest(key, supplied):
        raise HTTPException(401, "unauthorized")
    if request.headers.get("content-type", "").split(";")[0].strip() != "application/octet-stream":
        raise HTTPException(415, "unsupported media type")
    length = request.headers.get("content-length")
    if length and (not length.isdigit() or int(length) > MAX_AUDIO_BYTES):
        raise HTTPException(413, "audio exceeds size limit")
    language = request.headers.get("x-orion-audio-language", "auto")
    if language not in VALID_LANGUAGES:
        raise HTTPException(422, "unsupported language")
    if _model is None:
        raise HTTPException(503, "model not ready")

    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > MAX_AUDIO_BYTES:
            raise HTTPException(413, "audio exceeds size limit")
        data.extend(chunk)
    if not data:
        raise HTTPException(422, "empty audio")

    try:
        # One inference per replica. Avoid unbounded model contention and keep
        # /health responsive by executing CPU work in a separate thread.
        try:
            await asyncio.wait_for(_transcribe_lock.acquire(), timeout=1)
        except asyncio.TimeoutError:
            raise HTTPException(503, "transcription is busy") from None
        try:
            try:
                result = await asyncio.to_thread(_recognize, bytes(data), language)
            except (ValueError, av.FFmpegError, EOFError):
                raise HTTPException(422, "audio could not be processed") from None
            except Exception:
                raise HTTPException(503, "transcription unavailable") from None
            return {"text": result}
        finally:
            _transcribe_lock.release()
    finally:
        data[:] = b"\x00" * len(data)
