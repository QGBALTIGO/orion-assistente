"""Painel seguro: sessão HttpOnly, limite de login, CSRF e conexão QR protegida."""
import base64
import hashlib
import os

import httpx
import pytest

from app import panel_auth, qr_api
from app.main import app


class FakePipe:
    def __init__(self,db):self.db=db;self.key=None
    def incr(self,key):self.key=key;return self
    def expire(self,key,seconds):return self
    async def execute(self):
        self.db.store[self.key]=str(int(self.db.store.get(self.key,"0"))+1)

class MemoryRedis:
    def __init__(self):self.store={}
    async def get(self,key):return self.store.get(key)
    async def set(self,key,value,**options):
        self.store[key]=value
        return True
    async def delete(self,key):self.store.pop(key,None)
    def pipeline(self):return FakePipe(self)
    async def aclose(self):pass


def demo_hash(password):
    salt=b"synthetic-testing-salt"
    iterations=220000
    digest=hashlib.pbkdf2_hmac("sha256",password.encode(),salt,iterations)
    return ("pbkdf2_sha256$"+str(iterations)+"$"+
            base64.urlsafe_b64encode(salt).decode()+"$"+
            base64.urlsafe_b64encode(digest).decode())


@pytest.mark.asyncio
async def test_panel_login_cookie_csrf_and_bridge(monkeypatch):
    redis=MemoryRedis()
    monkeypatch.setattr(panel_auth,"_redis",lambda:redis)
    monkeypatch.setenv("PANEL_ADMIN_USERNAME","admin")
    monkeypatch.setenv("PANEL_ADMIN_PASSWORD_HASH",demo_hash("test-12345678"))
    monkeypatch.setenv("SUPPORT_API_TOKEN","synthetic-support-token-12345678901234567890")
    async def fake_bridge(path,method="GET",payload=None,query=None):
        return {"path":path,"method":method,"payload":payload,"query":query}
    monkeypatch.setattr(qr_api,"_bridge",fake_bridge)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="https://test") as c:
        assert (await c.get("/api/v1/qr/status")).status_code==401
        wrong=await c.post("/auth/login",json={"username":"admin","password":"wrong"},
                           headers={"Origin":"https://test"})
        assert wrong.status_code==401
        okay=await c.post("/auth/login",json={"username":"admin","password":"test-12345678"},
                          headers={"Origin":"https://test"})
        assert okay.status_code==200,okay.text
        assert "__Host-orion_session" in c.cookies
        assert "HttpOnly" in okay.headers["set-cookie"]
        assert "Secure" in okay.headers["set-cookie"]
        csrf=okay.json()["csrf"]
        assert (await c.get("/auth/me")).json()["authenticated"]
        assert (await c.get("/api/v1/qr/status")).json()["path"]=="/internal/status"
        no_csrf=await c.post("/api/v1/qr/reconnect")
        assert no_csrf.status_code==403
        with_csrf=await c.post("/api/v1/qr/reconnect",headers={"X-Orion-CSRF":csrf,
                                        "Origin":"https://test"})
        assert with_csrf.status_code==200
        bad_origin=await c.post("/api/v1/qr/pair",json={"phone":"5567999999999"},
            headers={"X-Orion-CSRF":csrf,"Origin":"https://other.test"})
        assert bad_origin.status_code==403
        sent=await c.post("/api/v1/qr/send",json={"phone":"5567999999999","text":"Olá teste"},
            headers={"X-Orion-CSRF":csrf,"Origin":"https://test"})
        assert sent.status_code==200
        assert sent.json()["payload"]["text"]=="Olá teste"
        assert (await c.post("/auth/logout",headers={"X-Orion-CSRF":csrf,
                 "Origin":"https://test"})).status_code==200
        assert (await c.get("/auth/me")).status_code==401


@pytest.mark.asyncio
async def test_panel_ui_includes_qr_pairing_chats_without_manual_support_token():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="https://test") as c:
        r=await c.get("/painel")
        assert r.status_code==200
        assert "Conectar dispositivo" in r.text
        assert "Código de emparelhamento" in r.text or "Gerar código de emparelhamento" in r.text
        assert "Enviar mensagem" in r.text
        assert "SUPPORT_API_TOKEN" not in r.text
        d=await c.get("/conectar-whatsapp",follow_redirects=False)
        assert d.status_code==303
        assert d.headers["location"]=="/painel?aba=dispositivo"
        old=await c.get("/panel?aba=agenda",follow_redirects=False)
        assert old.status_code==308
        assert old.headers["location"]=="/painel?aba=agenda"
        assert old.headers["cache-control"]=="no-store"


@pytest.mark.asyncio
async def test_short_links_do_not_capture_fixed_routes(monkeypatch):
    monkeypatch.setenv("APPOINTMENT_COMMUNICATIONS_ENABLED", "1")
    monkeypatch.setenv("ORION_SHORT_APPOINTMENT_LINKS_ENABLED", "1")
    monkeypatch.setenv("ORION_CENTRAL_CONFIRMATION_REPLY_ENABLED", "1")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="https://test") as c:
        for path in ("/", "/painel", "/convite", "/health", "/manifest.webmanifest"):
            response=await c.get(path)
            assert response.status_code==200, (path,response.text[:200])
        assert 'href="/painel"' in (await c.get("/")).text
        assert 'href="/panel"' not in (await c.get("/")).text
        manifest=(await c.get("/manifest.webmanifest")).json()
        assert manifest["start_url"]=="/painel"
        assert manifest["scope"]=="/painel"
        # A valid short alias continues to serve the verification shell.
        assert (await c.get("/AG-4NNSY2")).status_code==200
        # Arbitrary one-segment paths must not disclose or resolve a booking.
        assert (await c.get("/not-a-protocol")).status_code==404
