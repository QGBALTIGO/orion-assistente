"""Não expor QR nem telefone vinculado sem autenticação de suporte."""
import httpx
import pytest

from app.main import app


@pytest.mark.asyncio
async def test_qr_requires_support_and_never_exposes_api_key(monkeypatch):
    monkeypatch.setenv("SUPPORT_API_TOKEN","support-token-for-test-1234567890123456")
    monkeypatch.setenv("QR_BRIDGE_URL","http://qr-test.railway.internal:3000")
    monkeypatch.setenv("QR_BRIDGE_TOKEN","bridge-token-for-test-1234567890123456")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        for path in ("/api/v1/qr/status","/api/v1/qr/image"):
            response=await client.get(path)
            assert response.status_code==401
        html=await client.get("/conectar-whatsapp")
        assert html.status_code==200
        assert "bridge-token-for-test" not in html.text
        assert "QR Code" in html.text
        assert "<script" in html.text
        assert "Authorization" in html.text


@pytest.mark.asyncio
async def test_qr_backend_fails_closed_without_configuration(monkeypatch):
    monkeypatch.setenv("SUPPORT_API_TOKEN","support-token-for-test-1234567890123456")
    monkeypatch.delenv("QR_BRIDGE_URL",raising=False)
    monkeypatch.delenv("QR_BRIDGE_TOKEN",raising=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        response=await client.get("/api/v1/qr/status",headers={
            "Authorization":"Bearer support-token-for-test-1234567890123456"
        })
        assert response.status_code==503
