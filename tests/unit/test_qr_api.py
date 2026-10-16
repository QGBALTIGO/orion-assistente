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
        redirect=await client.get("/conectar-whatsapp",follow_redirects=False)
        assert redirect.status_code==303
        assert redirect.headers["location"]=="/panel?aba=dispositivo"
        html=await client.get("/panel")
        assert html.status_code==200
        assert "bridge-token-for-test" not in html.text
        assert "QR Code" in html.text
        assert "<script" in html.text
        assert "SUPPORT_API_TOKEN" not in html.text


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



@pytest.mark.asyncio
async def test_private_bridge_health_requires_real_reachability(monkeypatch):
    from app import qr_api
    async def reachable(path,method="GET",payload=None,query=None):
        assert path=="/internal/status"
        return {"status":"qr_ready","hasQr":True}
    monkeypatch.setattr(qr_api,"_bridge",reachable)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="https://test") as client:
        response=await client.get("/health/whatsapp")
        assert response.status_code==200
        assert response.json()=={"status":"ok","bridge":"reachable"}
        assert "hasQr" not in response.text


@pytest.mark.asyncio
async def test_private_bridge_health_fails_closed_when_network_unreachable(monkeypatch):
    from app import qr_api
    from fastapi import HTTPException
    async def unavailable(path,method="GET",payload=None,query=None):
        raise HTTPException(503,"socket connection refused")
    monkeypatch.setattr(qr_api,"_bridge",unavailable)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="https://test") as client:
        response=await client.get("/health/whatsapp")
        assert response.status_code==503
        assert "socket" not in response.text
        assert "telefone" not in response.text



@pytest.mark.asyncio
async def test_recover_requires_admin_and_only_forwards_to_private_bridge(monkeypatch):
    from app import qr_api
    monkeypatch.setenv("SUPPORT_API_TOKEN","support-token-for-test-1234567890123456")
    calls=[]
    async def fake_bridge(path,method="GET",payload=None,query=None):
        calls.append((path,method))
        return {"status":"connecting"}
    monkeypatch.setattr(qr_api,"_bridge",fake_bridge)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="https://test") as client:
        forbidden=await client.post("/api/v1/qr/recover")
        assert forbidden.status_code==401
        okay=await client.post("/api/v1/qr/recover",headers={
            "Authorization":"Bearer support-token-for-test-1234567890123456"
        })
        assert okay.status_code==200
        assert okay.json()=={"status":"connecting"}
        assert calls==[("/internal/recover","POST")]



@pytest.mark.asyncio
async def test_recipient_inspection_requires_auth_and_does_not_send(monkeypatch):
    from app import qr_api
    monkeypatch.setenv("SUPPORT_API_TOKEN","support-token-for-test-1234567890123456")
    calls=[]
    async def fake_bridge(path,method="GET",payload=None,query=None):
        calls.append((path,method,query))
        return {"registered": True, "identity": "pn_confirmed",
                "hasLid": True, "self": False,
                "delivery": {"count": 1, "pending": 1}}
    monkeypatch.setattr(qr_api,"_bridge",fake_bridge)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="https://test") as client:
        denied=await client.get("/api/v1/qr/recipient?phone=5521997557364")
        assert denied.status_code==401
        invalid=await client.get("/api/v1/qr/recipient?phone=bad",headers={
            "Authorization":"Bearer support-token-for-test-1234567890123456"
        })
        assert invalid.status_code==422
        okay=await client.get("/api/v1/qr/recipient?phone=5521997557364",headers={
            "Authorization":"Bearer support-token-for-test-1234567890123456"
        })
        assert okay.status_code==200
        assert okay.json()["hasLid"]
        assert calls==[("/internal/recipient","GET",{"phone":"5521997557364"})]



@pytest.mark.asyncio
async def test_manual_fresh_device_mode_is_strict_and_one_message_only(monkeypatch):
    from app import qr_api
    monkeypatch.setenv("SUPPORT_API_TOKEN","support-token-for-test-1234567890123456")
    calls=[]
    async def fake_bridge(path,method="GET",payload=None,query=None):
        calls.append((path,method,payload))
        return {"accepted":True,"delivery":"pending",
                "freshDeviceLookupRequested":payload.get("freshDevices",False)}
    monkeypatch.setattr(qr_api,"_bridge",fake_bridge)
    headers={"Authorization":"Bearer support-token-for-test-1234567890123456"}
    endpoint="/api/v1/qr/send"
    base={"phone":"5521997557364","text":"Mensagem sintética de teste"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="https://test") as client:
        assert (await client.post(endpoint,json={**base,"freshDevices":True})).status_code==401
        assert (await client.post(endpoint,json={**base,"freshDevices":"true"},
                                  headers=headers)).status_code==422
        requested=await client.post(endpoint,json={**base,"freshDevices":True},
                                    headers=headers)
        assert requested.status_code==200
        assert requested.json()["freshDeviceLookupRequested"] is True
        ordinary=await client.post(endpoint,json=base,headers=headers)
        assert ordinary.status_code==200
        assert ordinary.json()["freshDeviceLookupRequested"] is False
    assert calls==[
        ("/internal/send","POST",{**base,"freshDevices":True}),
        ("/internal/send","POST",{**base,"freshDevices":False})
    ]
