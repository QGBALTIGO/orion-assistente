"""Autorização e bloqueio padrão do simulador (sem banco e sem rede)."""
import httpx
import pytest

from app.main import app


@pytest.mark.asyncio
async def test_preview_is_disabled_even_with_valid_support(monkeypatch):
    monkeypatch.setenv("SUPPORT_API_TOKEN", "test-operator-token-long-enough-00000000")
    monkeypatch.delenv("GROQ_PREVIEW_ENABLED", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "fake-test-key")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        res = await client.post(
            "/api/v1/tenants/00000000-0000-4000-8000-000000000001/ai/preview",
            headers={"Authorization": "Bearer test-operator-token-long-enough-00000000"},
            json={"message": "Oi"},
        )
    assert res.status_code == 423


@pytest.mark.asyncio
async def test_ai_status_never_returns_key(monkeypatch):
    monkeypatch.setenv("SUPPORT_API_TOKEN", "test-operator-token-long-enough-00000000")
    monkeypatch.setenv("GROQ_API_KEY", "fake-secret-that-cannot-appear")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        res = await client.get(
            "/api/v1/tenants/00000000-0000-4000-8000-000000000001/ai/status",
            headers={"Authorization": "Bearer test-operator-token-long-enough-00000000"},
        )
    assert res.status_code == 200
    assert res.json()["configured"] is True
    assert "fake-secret" not in res.text


@pytest.mark.asyncio
async def test_ai_preview_needs_auth(monkeypatch):
    monkeypatch.setenv("SUPPORT_API_TOKEN", "test-operator-token-long-enough-00000000")
    monkeypatch.setenv("GROQ_PREVIEW_ENABLED", "1")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        res = await client.post(
            "/api/v1/tenants/00000000-0000-4000-8000-000000000001/ai/preview",
            json={"message": "oi"},
        )
    assert res.status_code == 401
