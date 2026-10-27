"""Real PostgreSQL account lifecycle with encrypted queued mail; no outbound mail."""
import secrets
import pytest
import httpx
from cryptography.fernet import Fernet

from app import account_auth,panel_auth,qr_api,email_transport
from app.main import app


class RedisDouble:
    def __init__(self): self.entries={}
    async def get(self,k): return self.entries.get(k)
    async def set(self,k,v,**_): self.entries[k]=str(v);return True
    async def delete(self,k): self.entries.pop(k,None)
    async def incr(self,k):
        n=int(self.entries.get(k,"0"))+1;self.entries[k]=str(n);return n
    async def expire(self,k,s): return True
    async def aclose(self): pass
    async def eval(self, script, count, *args):
        keys, limits = args[:count], args[count:]
        if any(int(self.entries.get(key, "0")) >= int(limits[2*i]) for i, key in enumerate(keys)):
            return 0
        for key in keys:
            await self.incr(key)
        return 1
    def pipeline(self):
        parent=self
        class Pipe:
            def incr(self,k):self.key=k;return self
            def expire(self,k,s):return self
            async def execute(self):await parent.incr(self.key)
        return Pipe()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_registration_without_mail_login_and_recovery(monkeypatch):
    cache=RedisDouble()
    monkeypatch.setattr(panel_auth,"_redis",lambda:cache)
    monkeypatch.setattr(account_auth,"_redis",lambda:cache)
    monkeypatch.setattr(account_auth,"_password_valid",lambda secret:secret=="legacy-proof")
    monkeypatch.setenv("PANEL_ADMIN_USERNAME","admin")
    monkeypatch.setenv("SUPPORT_API_TOKEN","synthetic-account-lifecycle-support-token-over-thirty-two")
    monkeypatch.setenv("PANEL_ADMIN_PASSWORD_HASH","ci-only-synthetic")
    monkeypatch.setenv("PANEL_REGISTRATION_ENABLED","1")
    monkeypatch.setenv("PANEL_EMAIL_ENABLED","0")
    monkeypatch.setenv("PANEL_EMAIL_LIVE_SEND","1")
    monkeypatch.setenv("PANEL_EMAIL_TRANSPORT","resend_https")
    monkeypatch.setenv("PANEL_EMAIL_FERNET_KEY",Fernet.generate_key().decode())
    monkeypatch.setenv("PANEL_TEST_MODE","0")
    monkeypatch.setenv("PANEL_SMTP_HOST","smtp.example.test")
    monkeypatch.setenv("PANEL_SMTP_USERNAME","synthetic-user")
    monkeypatch.setenv("PANEL_SMTP_PASSWORD","synthetic-password")
    monkeypatch.setenv("PANEL_SMTP_FROM","Orion <no-reply@example.test>")
    monkeypatch.setenv("PANEL_PUBLIC_URL","https://test")
    monkeypatch.setenv("PANEL_OTP_SECRET","otp-tests-only-secure-key-never-in-production-456")
    address="test-"+secrets.token_hex(10)+"@example.test"
    monkeypatch.setenv("PANEL_OWNER_EMAIL",address)
    mail=[]
    original_enqueue=account_auth.email_outbox.enqueue
    async def captured_enqueue(db, **kwargs):
        mail.append((kwargs["recipient"],kwargs["kind"],kwargs.get("code"),"https://test"))
        return await original_enqueue(db, **kwargs)
    monkeypatch.setattr(account_auth.email_outbox,"enqueue",captured_enqueue)
    bridge=[]
    async def fake_bridge(path,method="GET",payload=None,query=None):
        bridge.append(path)
        return {"status":"connected"}
    monkeypatch.setattr(qr_api,"_bridge",fake_bridge)
    h={"Origin":"https://test"}
    payload={"name":"Pessoa Teste","email":address,"password":"Correct#Secret123"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="https://test") as c:
        feature=(await c.get("/auth/features")).json()
        assert feature=={"registration":True,"passwordRecovery":False,"testMode":False}
        created=await c.post("/auth/register",json=payload,headers=h)
        assert created.status_code==201,created.text
        assert not mail
        duplicate=await c.post("/auth/register",json=payload,headers=h)
        assert duplicate.status_code==409
        signed=await c.post("/auth/login",json={
            "username":address,"password":payload["password"],"remember":True},headers=h)
        assert signed.status_code==200,signed.text
        assert signed.json()["role"]=="member"
        assert "Max-Age=2592000" in signed.headers["set-cookie"]
        assert (await c.get("/auth/me")).json()["role"]=="member"
        assert (await c.get("/api/v1/qr/status")).status_code==403
        assert not bridge
        csrf=signed.json()["csrf"]
        # An email matching PANEL_OWNER_EMAIL can never confer admin automatically.
        denied=await c.post("/auth/admin/claim",json={"legacy_password":"legacy-proof"},
            headers={**h,"X-Orion-CSRF":csrf})
        assert denied.status_code==403

        # Recovery is independent and only works after email delivery is configured.
        assert (await c.post("/auth/password/forgot",json={"email":address},headers=h)).status_code==503
        monkeypatch.setenv("PANEL_EMAIL_ENABLED","1")
        assert (await c.get("/auth/features")).json()["passwordRecovery"] is True
        forgot=await c.post("/auth/password/forgot",json={"email":address},headers=h)
        assert forgot.status_code==200,forgot.text
        assert len(mail)==1 and mail[-1][1]=="reset"
        code=mail[-1][2]
        assert len(code)==6 and code.isdigit()
        wrong="000000" if code!="000000" else "000001"
        assert (await c.post("/auth/password/reset",json={
            "email":address,"code":wrong,"password":"New#Pass123456"},headers=h)).status_code==400
        reset=await c.post("/auth/password/reset",json={
            "email":address,"code":code,"password":"New#Pass123456"},headers=h)
        assert reset.status_code==200,reset.text
        assert (await c.post("/auth/password/reset",json={
            "email":address,"code":code,"password":"Another#Pass123456"},headers=h)).status_code==400
        assert (await c.get("/auth/me")).status_code==401
        assert (await c.post("/auth/login",json={
            "username":address,"password":payload["password"]},headers=h)).status_code==401
        new_login=await c.post("/auth/login",json={
            "username":address,"password":"New#Pass123456"},headers=h)
        assert new_login.status_code==200 and new_login.json()["role"]=="member"
        csrf=new_login.json()["csrf"]
        # Recovery code also proves mailbox ownership for the separate owner claim.
        claim=await c.post("/auth/admin/claim",json={"legacy_password":"legacy-proof"},
            headers={**h,"X-Orion-CSRF":csrf})
        assert claim.status_code==200,claim.text
        assert claim.json()["reloginRequired"] is True
        assert (await c.get("/auth/me")).status_code==401
        final=await c.post("/auth/login",json={
            "username":address,"password":"New#Pass123456"},headers=h)
        assert final.status_code==200 and final.json()["role"]=="admin"
        assert (await c.get("/api/v1/qr/status")).json()["status"]=="connected"


@pytest.mark.asyncio
async def test_registration_flag_does_not_depend_on_smtp(monkeypatch):
    monkeypatch.setenv("PANEL_EMAIL_ENABLED","0")
    monkeypatch.setenv("PANEL_REGISTRATION_ENABLED","1")
    monkeypatch.setenv("PANEL_TEST_MODE","0")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="https://test") as c:
        feature=(await c.get("/auth/features")).json()
        assert feature=={"registration":True,"passwordRecovery":False,"testMode":False}
    monkeypatch.setenv("PANEL_REGISTRATION_ENABLED","0")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="https://test") as c:
        feature=(await c.get("/auth/features")).json()
        assert feature["registration"] is False
        denied=await c.post("/auth/register",json={
            "name":"Outro Nome","email":"blocked@example.test",
            "password":"Correct#Secret123"},headers={"Origin":"https://test"})
        assert denied.status_code==503


@pytest.mark.integration
@pytest.mark.asyncio
async def test_resend_test_mode_only_restricts_delivery_not_signups(monkeypatch):
    cache=RedisDouble()
    monkeypatch.setattr(panel_auth,"_redis",lambda:cache)
    monkeypatch.setattr(account_auth,"_redis",lambda:cache)
    monkeypatch.setenv("PANEL_EMAIL_TRANSPORT","resend_https")
    monkeypatch.setenv("PANEL_EMAIL_ENABLED","1")
    monkeypatch.setenv("PANEL_EMAIL_LIVE_SEND","1")
    monkeypatch.setenv("PANEL_EMAIL_FERNET_KEY",Fernet.generate_key().decode())
    monkeypatch.setenv("PANEL_TEST_MODE","1")
    monkeypatch.setenv("PANEL_TEST_EMAIL","owner-mailbox@example.test")
    monkeypatch.setenv("PANEL_REGISTRATION_ENABLED","1")
    monkeypatch.setenv("PANEL_SMTP_FROM","Orion <onboarding@resend.dev>")
    monkeypatch.setenv("PANEL_SMTP_PASSWORD","test-resend-key-not-real")
    monkeypatch.setenv("PANEL_PUBLIC_URL","https://test")
    monkeypatch.setenv("PANEL_OTP_SECRET","otp-tests-only-secure-key-never-in-production-456")
    email="other-"+secrets.token_hex(10)+"@example.test"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="https://test") as c:
        feature=(await c.get("/auth/features")).json()
        assert feature=={"registration":True,"passwordRecovery":True,"testMode":True}
        created=await c.post("/auth/register",json={
            "name":"Outro Nome","email":email,
            "password":"Correct#Secret123"},headers={"Origin":"https://test"})
        assert created.status_code==201,created.text
        assert (await c.post("/auth/verify/resend",json={
            "email":email},headers={"Origin":"https://test"})).status_code==403
        forgot=await c.post("/auth/password/forgot",json={
            "email":email},headers={"Origin":"https://test"})
        assert forgot.status_code==200  # no email to an unapproved recipient
        assert (await c.post("/auth/password/reset",json={
            "email":email,"code":"123456","password":"Correct#Secret123"},
            headers={"Origin":"https://test"})).status_code==403


@pytest.mark.asyncio
async def test_resend_https_api_sends_without_smtp(monkeypatch):
    monkeypatch.setenv("PANEL_EMAIL_TRANSPORT","resend_https")
    monkeypatch.setenv("PANEL_SMTP_FROM","Orion <onboarding@resend.dev>")
    monkeypatch.setenv("PANEL_SMTP_PASSWORD","ci-only-resend-secret")
    monkeypatch.setenv("PANEL_SMTP_PORT","465")
    monkeypatch.setenv("PANEL_EMAIL_ENABLED","1")
    monkeypatch.setenv("PANEL_EMAIL_LIVE_SEND","1")
    monkeypatch.setenv("PANEL_EMAIL_FERNET_KEY",Fernet.generate_key().decode())
    monkeypatch.setenv("PANEL_OTP_SECRET","synthetic-only-stable-otp-secret-thirty-two-characters")
    monkeypatch.setenv("PANEL_TEST_MODE","0")
    monkeypatch.setenv("PANEL_PUBLIC_URL","https://test")
    responses=[]
    class Response:
        status_code=200
        def json(self):return {"id":"synthetic-provider-uuid"}
    class Client:
        def __init__(self,timeout,**kwargs):self.timeout=timeout
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def post(self,url,headers,json):
            responses.append((url,headers,json,self.timeout))
            return Response()
    monkeypatch.setattr(email_transport.httpx,"AsyncClient",Client)
    payload=email_transport.render_message("owner-mailbox@example.test","reset",code="123456")
    await email_transport.send_message(payload,"synthetic-outbox-uuid")
    assert len(responses)==1
    url,headers,payload,timeout=responses[0]
    assert url=="https://api.resend.com/emails"
    assert headers["Authorization"]=="Bearer ci-only-resend-secret"
    assert payload["to"]==["owner-mailbox@example.test"]
    assert payload["from"]=="Orion <onboarding@resend.dev>"
    assert "123456" in payload["html"] and "123456" in payload["text"]
    assert timeout<=15
