"""Synthetic audio only: real local decode, mocked provider, no WhatsApp sends."""
import asyncio
import io
import json
from pathlib import Path
import shutil
import subprocess
import wave

from fastapi import HTTPException
import httpx
import pytest

from app import subscription_device_audio as audio


class Request:
    def __init__(self, body=b"OggSsynthetic", media_type="audio/ogg", headers=None):
        self.body = body
        self.headers = {"content-type": media_type, **(headers or {})}
        self.read = False

    async def stream(self):
        self.read = True
        midpoint = len(self.body) // 2
        yield self.body[:midpoint]
        yield self.body[midpoint:]


@pytest.fixture
def provider(monkeypatch):
    observed = []
    response = {"text": "Texto completo da mensagem.", "duration": 65.0}
    status = [200]
    monkeypatch.setenv("GROQ_API_KEY", "synthetic-device-audio-test-key")
    monkeypatch.setattr(audio, "audio_available", lambda: True)

    async def normalize(body, media_type, max_seconds):
        observed.append(("decode", max_seconds))
        return bytearray(b"OggSnormalized-synthetic"), 65.0

    def send(request):
        observed.append("provider")
        assert request.method == "POST"
        assert str(request.url) == audio.PROVIDER_URL
        assert request.headers["authorization"] == "Bearer synthetic-device-audio-test-key"
        assert b"whisper-large-v3-turbo" in request.content
        assert b"message.ogg" in request.content
        assert b"OggSnormalized-synthetic" in request.content
        return httpx.Response(status[0], content=json.dumps(response).encode(),
                              headers={"content-type": "application/json"})

    original = httpx.AsyncClient

    def client(**kwargs):
        assert kwargs["follow_redirects"] is False and kwargs["trust_env"] is False
        return original(transport=httpx.MockTransport(send), **kwargs)

    async def authorize(duration, *, phase):
        observed.append((phase, duration))

    monkeypatch.setattr(audio, "_normalize_audio", normalize)
    monkeypatch.setattr(audio.httpx, "AsyncClient", client)
    return observed, response, status, authorize


@pytest.mark.asyncio
async def test_provider_is_called_once_after_actual_duration_reservation_and_rechecked(provider):
    observed, _, _, authorize = provider
    result = await audio.transcribe_audio(Request(), max_minutes=30, authorize=authorize)
    assert result == {"text": "Texto completo da mensagem.", "duration_seconds": 65.0}
    assert observed == [("decode", 1800), ("before_provider", 65.0), "provider", ("after_provider", 65.0)]


@pytest.mark.asyncio
async def test_full_transcript_above_legacy_and_32k_limits_is_preserved(provider):
    _, response, _, authorize = provider
    response["text"] = "Mensagem completa. " * 2500
    result = await audio.transcribe_audio(Request(), max_minutes=30, authorize=authorize)
    assert len(result["text"]) > 32_000
    assert result["text"] == response["text"].strip()


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["before_provider", "after_provider"])
async def test_revoked_revision_lease_or_quota_never_releases_a_transcript(provider, phase):
    observed, _, _, _ = provider

    async def denied(duration, *, phase: str):
        observed.append(phase)
        if phase == denied.phase:
            raise HTTPException(409, "synthetic_authority_changed")

    denied.phase = phase
    with pytest.raises(HTTPException) as error:
        await audio.transcribe_audio(Request(), max_minutes=30, authorize=denied)
    assert error.value.status_code == 409
    assert observed.count("provider") == (0 if phase == "before_provider" else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [302, 400, 401, 429, 500, 503])
async def test_failed_or_redirected_provider_is_never_retried(provider, status):
    observed, _, response_status, authorize = provider
    response_status[0] = status
    with pytest.raises(HTTPException) as error:
        await audio.transcribe_audio(Request(), max_minutes=30, authorize=authorize)
    assert error.value.status_code == 503
    assert observed.count("provider") == 1
    assert not any(isinstance(x, tuple) and x[0] == "after_provider" for x in observed)


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [None, True, -1, 0, float("nan"), float("inf"), 1801, 10])
async def test_malformed_or_mismatched_provider_duration_is_not_returned(provider, value):
    observed, response, _, authorize = provider
    response["duration"] = value
    with pytest.raises(HTTPException) as error:
        await audio.transcribe_audio(Request(), max_minutes=30, authorize=authorize)
    assert error.value.status_code == 502
    assert observed.count("provider") == 1


@pytest.mark.asyncio
async def test_transcript_is_never_silently_truncated(provider):
    _, response, _, authorize = provider
    response["text"] = "a" * (audio.MAX_TRANSCRIPT_CHARS + 1)
    with pytest.raises(HTTPException) as error:
        await audio.transcribe_audio(Request(), max_minutes=30, authorize=authorize)
    assert error.value.detail["code"] == "DEVICE_AUDIO_TRANSCRIPT_TOO_LONG"


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [None, True, False, 0, 31, 1.5, "30"])
async def test_invalid_config_never_reads_or_charges_audio(provider, value):
    observed, _, _, authorize = provider
    request = Request()
    with pytest.raises(HTTPException) as error:
        await audio.transcribe_audio(request, max_minutes=value, authorize=authorize)
    assert error.value.status_code == 422
    assert observed == [] and request.read is False


@pytest.mark.asyncio
@pytest.mark.parametrize("headers,status", [
    ({"content-length": "25000001"}, 413),
    ({"content-length": "-1"}, 400),
    ({"content-length": "bad"}, 400),
    ({"content-type": "text/plain"}, 415),
])
async def test_request_limits_apply_before_read_decode_or_charge(provider, headers, status):
    observed, _, _, authorize = provider
    request = Request(headers=headers)
    with pytest.raises(HTTPException) as error:
        await audio.transcribe_audio(request, max_minutes=30, authorize=authorize)
    assert error.value.status_code == status
    assert observed == [] and request.read is False


@pytest.mark.asyncio
async def test_actual_stream_limit_ignores_false_content_length(provider, monkeypatch):
    observed, _, _, authorize = provider
    monkeypatch.setattr(audio, "MAX_AUDIO_BYTES", 10)
    with pytest.raises(HTTPException) as error:
        await audio.transcribe_audio(Request(headers={"content-length": "1"}), max_minutes=30, authorize=authorize)
    assert error.value.status_code == 413 and observed == []


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [b"", b"https://internal.example/secret", b"#EXTM3U\nfile:///etc/passwd"])
async def test_empty_and_mismatched_containers_never_decode_or_charge(provider, body):
    observed, _, _, authorize = provider
    with pytest.raises(HTTPException):
        await audio.transcribe_audio(Request(body), max_minutes=30, authorize=authorize)
    assert observed == []


def test_readiness_requires_provider_and_both_real_decoders(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "synthetic-key")
    monkeypatch.setattr(audio.shutil, "which", lambda name: "/usr/bin/" + name)
    assert audio.audio_available()
    monkeypatch.setattr(audio.shutil, "which", lambda name: None if name == "ffprobe" else "/usr/bin/ffmpeg")
    assert not audio.audio_available()
    monkeypatch.setattr(audio.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.delenv("GROQ_API_KEY")
    assert not audio.audio_available()


def wav(seconds):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(8000)
        stream.writeframes(b"\x00\x00" * int(seconds * 8000))
    return bytearray(buffer.getvalue())


@pytest.mark.asyncio
async def test_real_decoder_measures_samples_and_removes_tempfiles(monkeypatch, tmp_path):
    assert shutil.which("ffmpeg") and shutil.which("ffprobe"), "CI must install the same decoders as the runtime"
    original = audio.tempfile.TemporaryDirectory
    monkeypatch.setattr(audio.tempfile, "TemporaryDirectory", lambda **kwargs: original(dir=tmp_path, **kwargs))
    data, duration = await audio._normalize_audio(wav(2), "audio/wav", 2)
    assert data[:4] == b"OggS" and 1.99 <= duration <= 2
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_real_decoder_rejects_overlong_or_truncated_audio_before_provider(tmp_path):
    with pytest.raises(HTTPException) as error:
        await audio._normalize_audio(wav(3), "audio/wav", 2)
    assert error.value.detail["code"] == "DEVICE_AUDIO_TOO_LONG"
    with pytest.raises(HTTPException) as error:
        await audio._normalize_audio(wav(2)[:-1000], "audio/wav", 2)
    assert error.value.detail["code"] == "DEVICE_AUDIO_INVALID"


@pytest.mark.asyncio
async def test_real_30_minute_audio_is_supported_without_paid_provider(tmp_path):
    """Regression against the former 2MB/120s path using actual Opus samples."""
    output = tmp_path / "synthetic-30-minutes.ogg"
    subprocess.run([
        shutil.which("ffmpeg"), "-nostdin", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono", "-t", "1800",
        "-threads", "1", "-c:a", "libopus", "-b:a", "8k", "-vbr", "off", str(output),
    ], check=True, timeout=40, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    data, duration = await audio._normalize_audio(bytearray(output.read_bytes()), "audio/ogg", 1800)
    assert data[:4] == b"OggS" and len(data) < audio.MAX_AUDIO_BYTES
    assert 1799.9 <= duration <= 1800


@pytest.mark.asyncio
async def test_decoder_timeout_kills_child_and_never_leaves_it_running(monkeypatch):
    events = []

    class Process:
        returncode = None

        async def communicate(self):
            raise TimeoutError()

        def kill(self):
            events.append("kill")
            self.returncode = -9

        async def wait(self):
            events.append("reap")

    async def create(*args, **kwargs):
        assert kwargs["stdin"] == asyncio.subprocess.DEVNULL
        assert kwargs["stderr"] == asyncio.subprocess.DEVNULL
        return Process()

    monkeypatch.setattr(audio.asyncio, "create_subprocess_exec", create)
    with pytest.raises(TimeoutError):
        await audio._command(["synthetic-decoder"], timeout=1)
    assert events == ["kill", "reap"]
