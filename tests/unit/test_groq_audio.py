"""Teste de voz API: sem credencial real nem rede externa."""
import httpx
import pytest

from app.main import app
from app import groq_audio

KEY = "synthetic-bridge-key-1234567891234567891234567"
PHONE = "5567999999999"
URL = "/internal/qr/ai/transcribe"


@pytest.mark.asyncio
async def test_audio_is_disabled_by_default(monkeypatch):
    monkeypatch.setenv("QR_BRIDGE_TOKEN", KEY)
    monkeypatch.delenv("QR_AI_AUDIO_ENABLED", raising=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        r = await client.post(URL, headers={
            "X-Orion-Bridge-Key": KEY, "X-Orion-Phone": PHONE,
            "Content-Type": "audio/ogg"
        }, content=b"fake")
    assert r.status_code == 423


@pytest.mark.asyncio
async def test_audio_auth_and_allowlist_are_mandatory(monkeypatch):
    monkeypatch.setenv("QR_BRIDGE_TOKEN", KEY)
    monkeypatch.setenv("QR_AI_ENABLED", "1")
    monkeypatch.setenv("QR_AI_AUDIO_ENABLED", "1")
    monkeypatch.delenv("QR_AI_ALLOWED_SENDERS", raising=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        unauthorized = await client.post(URL, headers={"Content-Type": "audio/ogg"},
                                         content=b"fake")
        unlisted = await client.post(URL, headers={
            "X-Orion-Bridge-Key": KEY, "X-Orion-Phone": PHONE,
            "Content-Type": "audio/ogg"}, content=b"fake")
    assert unauthorized.status_code == 401
    assert unlisted.status_code == 403


@pytest.mark.asyncio
async def test_audio_groq_mock_produces_text_without_real_request(monkeypatch):
    monkeypatch.setenv("QR_BRIDGE_TOKEN", KEY)
    monkeypatch.setenv("QR_AI_ENABLED", "1")
    monkeypatch.setenv("QR_AI_AUDIO_ENABLED", "1")
    monkeypatch.setenv("QR_AI_ALLOWED_SENDERS", PHONE)
    monkeypatch.setenv("GROQ_API_KEY", "test-only-key")
    original = httpx.AsyncClient
    calls = []

    def handler(req):
        calls.append(req)
        assert req.url.path == "/openai/v1/audio/transcriptions"
        assert req.headers["authorization"] == "Bearer test-only-key"
        assert b"whisper-large-v3-turbo" in req.content
        assert b"OggS-audio-synthetic" in req.content
        assert b"verbose_json" in req.content
        return httpx.Response(200, json={"text": " Quero corte e barba ", "duration": 4.5})

    monkeypatch.setattr(groq_audio.httpx, "AsyncClient",
                        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    async with original(transport=httpx.ASGITransport(app=app),
                        base_url="http://test") as client:
        r = await client.post(URL, headers={
            "X-Orion-Bridge-Key": KEY, "X-Orion-Phone": PHONE,
            "Content-Type": "audio/ogg"}, content=b"OggS-audio-synthetic")
    assert r.status_code == 200, r.text
    assert r.json() == {"text": "Quero corte e barba"}
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_audio_rejects_malformed_media(monkeypatch):
    monkeypatch.setenv("QR_BRIDGE_TOKEN", KEY)
    monkeypatch.setenv("QR_AI_ENABLED", "1")
    monkeypatch.setenv("QR_AI_AUDIO_ENABLED", "1")
    monkeypatch.setenv("QR_AI_ALLOWED_SENDERS", PHONE)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        invalid = await client.post(URL, headers={
            "X-Orion-Bridge-Key": KEY, "X-Orion-Phone": PHONE,
            "Content-Type": "text/plain"}, content=b"abc")
        empty = await client.post(URL, headers={
            "X-Orion-Bridge-Key": KEY, "X-Orion-Phone": PHONE,
            "Content-Type": "audio/ogg"}, content=b"")
    assert invalid.status_code == 415
    assert empty.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("payload,status", [
    ({"text": "sim", "duration": 121}, 413),
    ({"text": "sim", "duration": True}, 502),
    ({"text": "sim", "duration": None}, 502),
    ({"text": "sim"}, 502),
    ({"text": "sim", "duration": 0}, 502),
    ({"text": "sim, mas não confirme. " * 100, "duration": 60}, 422),
    ({"text": "", "duration": 3}, 502),
])
async def test_no_partial_transcription_or_unverified_duration(monkeypatch, payload, status):
    monkeypatch.setenv("GROQ_API_KEY", "synthetic")
    original = httpx.AsyncClient
    monkeypatch.setattr(groq_audio.httpx, "AsyncClient", lambda **kwargs: original(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload)), **kwargs))
    class Request:
        headers = {"content-type": "audio/ogg"}
        async def stream(self):
            yield b"OggS-synthetic-no-real-audio"
    with pytest.raises(Exception) as error:
        await groq_audio.transcribe_request(Request())
    assert error.value.status_code == status


@pytest.mark.asyncio
async def test_audio_buffer_is_wiped_even_if_key_missing(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    captured = []
    original = groq_audio.validate_audio
    def validation(body, kind):
        captured.append(body)
        original(body, kind)
    monkeypatch.setattr(groq_audio, "validate_audio", validation)
    class Request:
        headers = {"content-type": "audio/ogg"}
        async def stream(self):
            yield b"OggS-synthetic"
    with pytest.raises(Exception) as error:
        await groq_audio.transcribe_request(Request())
    assert error.value.status_code == 503 and captured
    assert set(captured[0]) == {0}


@pytest.mark.asyncio
async def test_mime_mismatch_is_not_sent_to_provider(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "synthetic")
    def forbid(**_):
        raise AssertionError("Invalid audio cannot leave the server")
    monkeypatch.setattr(groq_audio.httpx, "AsyncClient", forbid)
    class Request:
        headers = {"content-type": "audio/ogg"}
        async def stream(self):
            yield b"<html>not audio</html>"
    with pytest.raises(Exception) as error:
        await groq_audio.transcribe_request(Request())
    assert error.value.status_code == 415


def test_synthetic_wav_fixtures_are_valid_and_truncation_is_rejected():
    from pathlib import Path
    files = list((Path(__file__).parents[1] / "fixtures" / "audio").glob("*.wav"))
    assert {path.name for path in files} == {"synthetic-price-pt.wav", "synthetic-correction-pt.wav"}
    for path in files:
        body = bytearray(path.read_bytes())
        groq_audio.validate_audio(body, "audio/wav")
        with pytest.raises(Exception) as error:
            groq_audio.validate_audio(body[:100], "audio/wav")
        assert error.value.status_code == 415
