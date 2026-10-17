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
        assert b"audio-synthetic" in req.content
        return httpx.Response(200, json={"text": " Quero corte e barba "})

    monkeypatch.setattr(groq_audio.httpx, "AsyncClient",
                        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    async with original(transport=httpx.ASGITransport(app=app),
                        base_url="http://test") as client:
        r = await client.post(URL, headers={
            "X-Orion-Bridge-Key": KEY, "X-Orion-Phone": PHONE,
            "Content-Type": "audio/ogg"}, content=b"audio-synthetic")
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
