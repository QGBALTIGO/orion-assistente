"""Real PostgreSQL account lifecycle with synthetic SMTP and Redis; no outbound mail."""
import secrets
import pytest
import httpx

from app import account_auth,panel_auth,qr_api
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
    def pipeline(self):
        parent=self
        class Pipe:
            def incr(self,k):self.key=k;return self
            def expire(self,k,s):return self
            async def execute(self):await parent.incr(self.key)
        return Pipe()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_verified_account_separation_recovery_owner_claim(monkeypatch):
    cache=RedisDouble()
    monkeypatch.setattr(panel_auth,"_redis",lambda:cache)
    monkeypatch.setattr(account_auth,"_redis",lambda:cache)
    monkeypatch.setattr(account_auth,"_password_valid",lambda secret:secret=="legacy-proof")
    monkeypatch.setenv("PANEL_ADMIN_USERNAME","admin")
    monkeypatch.setenv("PANEL_ADMIN_PASSWORD_HASH","synthetic-tests-only")
    monkeypatch.setenv("PANEL_EMAIL_ENABLED","1")
    monkeypatch.setenv("PANEL_SMTP_HOST","smtp.example.test")
    monkeypatch.setenv("PANEL_SMTP_USERNAME","synthetic-user")
    monkeypatch.setenv("PANEL_SMTP_PASSWORD","synthetic-password")
    monkeypatch.setenv("PANEL_SMTP_FROM","Orion <no-reply@example.test>")
    monkeypatch.setenv("PANEL_PUBLIC_URL","https://test")
    monkeypatch.setenv("PANEL_REGISTRATION_ENABLED","1")
    monkeypatch.setenv("PANEL_OTP_SECRET","otp-tests-only-secure-key-never-in-production-456")
    address="test-"+secrets.token_hex(10)+"@example.test"
    monkeypatch.setenv("PANEL_OWNER_EMAIL",address)
    mail=[]
    monkeypatch.setattr(account_auth,"_send_email",
        lambda recipient,purpose,secret,url:mail.append((recipient,purpose,secret,url)))
    bridge=[]
    async def fake_bridge(path,method="GET",payload=None,query=None):
        bridge.append(path)
        return {"status":"connected"}
    monkeypatch.setattr(qr_api,"_bridge",fake_bridge)
    h={"Origin":"https://test"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="https://test") as c:
        feature=await c.get("/auth/features")
        assert feature.json()=={"registration":True,"passwordRecovery":True,"testMode":False}
        registration={"name":"Pessoa Teste","email":address,"password":"Correct#Secret123"}
        created=await c.post("/auth/register",json=registration,headers=h)
        assert created.status_code==202,created.text
        assert len(mail)==1 and mail[-1][1]=="verify"
        assert mail[-1][2].isdigit() and len(mail[-1][2])==6
        assert (await c.post("/auth/login",json={
            "username":address,"password":registration["password"]},headers=h)).status_code==401
        wrong="000000" if mail[-1][2]!="000000" else "000001"
        first_code=mail[-1][2]
        for _ in range(5):
            bad_verify=await c.post("/auth/verify",json={
                "email":address,"code":wrong},headers=h)
            assert bad_verify.status_code==400
        assert (await c.post("/auth/verify",json={
            "email":address,"code":first_code},headers=h)).status_code==400
        resent=await c.post("/auth/verify/resend",json={"email":address},headers=h)
        assert resent.status_code==200
        assert mail[-1][2]!=first_code
        verified=await c.post("/auth/verify",json={"email":address,"code":mail[-1][2]},headers=h)
        assert verified.status_code==200,verified.text
        assert (await c.post("/auth/verify/resend",json={"email":address},headers=h)).status_code==200
        assert (await c.post("/auth/verify",json={"email":address,"code":mail[-1][2]},headers=h)).status_code==400
        signin=await c.post("/auth/login",json={
            "username":address,"password":registration["password"],"remember":True},headers=h)
        assert signin.status_code==200,signin.text
        assert signin.json()["role"]=="member"
        assert "Max-Age=2592000" in signin.headers["set-cookie"]
        assert (await c.get("/auth/me")).json()["role"]=="member"
        assert (await c.get("/api/v1/qr/status")).status_code==403
        assert not bridge
        csrf=signin.json()["csrf"]
        assert (await c.post("/auth/admin/claim",json={
            "legacy_password":"incorrect"},headers={**h,"X-Orion-CSRF":csrf})).status_code==403
        claim=await c.post("/auth/admin/claim",json={
            "legacy_password":"legacy-proof"},headers={**h,"X-Orion-CSRF":csrf})
        assert claim.status_code==200,claim.text
        assert claim.json()["reloginRequired"] is True
        assert (await c.get("/auth/me")).status_code==401
        signin=await c.post("/auth/login",json={
            "username":address,"password":registration["password"]},headers=h)
        assert signin.json()["role"]=="admin"
        assert (await c.get("/api/v1/qr/status")).json()["status"]=="connected"
        forgot=await c.post("/auth/password/forgot",json={"email":address},headers=h)
        assert forgot.status_code==200,forgot.text
        assert mail[-1][1]=="reset"
        assert len(mail[-1][2])==6 and mail[-1][2].isdigit()
        assert (await c.post("/auth/password/reset",json={
            "email":address,"code":"123456","password":"New#Pass123456"},headers=h)).status_code==400
        reset=await c.post("/auth/password/reset",json={
            "email":address,"code":mail[-1][2],"password":"New#Pass123456"},headers=h)
        assert reset.status_code==200,reset.text
        assert (await c.post("/auth/password/reset",json={
            "email":address,"code":mail[-1][2],"password":"Another#Pass123"},headers=h)).status_code==400
        assert (await c.get("/auth/me")).status_code==401
        assert (await c.post("/auth/login",json={
            "username":address,"password":registration["password"]},headers=h)).status_code==401
        new_login=await c.post("/auth/login",json={
            "username":address,"password":"New#Pass123456"},headers=h)
        assert new_login.status_code==200 and new_login.json()["role"]=="admin"


@pytest.mark.asyncio
async def test_signup_is_fail_closed_without_smtp_and_no_auto_admin(monkeypatch):
    monkeypatch.delenv("PANEL_EMAIL_ENABLED",raising=False)
    monkeypatch.setenv("PANEL_REGISTRATION_ENABLED","1")
    monkeypatch.setenv("PANEL_OWNER_EMAIL","test-admin@example.test")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="https://test") as c:
        features=(await c.get("/auth/features")).json()
        assert not features["registration"] and not features["passwordRecovery"]
        response=await c.post("/auth/register",json={
            "name":"Outro Nome","email":"test-admin@example.test",
            "password":"Correct#Secret123"},headers={"Origin":"https://test"})
        assert response.status_code==503


@pytest.mark.asyncio
async def test_resend_testing_mode_never_sends_to_unapproved_recipient(monkeypatch):
    cache=RedisDouble()
    monkeypatch.setattr(panel_auth,"_redis",lambda:cache)
    monkeypatch.setattr(account_auth,"_redis",lambda:cache)
    monkeypatch.setenv("PANEL_EMAIL_TRANSPORT","resend_https")
    monkeypatch.setenv("PANEL_EMAIL_ENABLED","1")
    monkeypatch.setenv("PANEL_TEST_MODE","1")
    monkeypatch.setenv("PANEL_TEST_EMAIL","owner-mailbox@example.test")
    monkeypatch.setenv("PANEL_REGISTRATION_ENABLED","0")
    monkeypatch.setenv("PANEL_SMTP_FROM","Orion <onboarding@resend.dev>")
    monkeypatch.setenv("PANEL_SMTP_PASSWORD","test-resend-key-not-real")
    monkeypatch.setenv("PANEL_PUBLIC_URL","https://test")
    monkeypatch.setenv("PANEL_OTP_SECRET","otp-tests-only-secure-key-never-in-production-456")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="https://test") as c:
        feature=(await c.get("/auth/features")).json()
        assert feature=={"registration":True,"passwordRecovery":True,"testMode":True}
        response=await c.post("/auth/register",json={
            "name":"Outro Nome","email":"another@example.test",
            "password":"Correct#Secret123"},headers={"Origin":"https://test"})
        assert response.status_code==403
        response=await c.post("/auth/verify/resend",json={
            "email":"another@example.test"},headers={"Origin":"https://test"})
        assert response.status_code==403
        response=await c.post("/auth/password/forgot",json={
            "email":"another@example.test"},headers={"Origin":"https://test"})
        assert response.status_code==200
        response=await c.post("/auth/password/reset",json={
            "email":"another@example.test","code":"123456",
            "password":"Correct#Secret123"},headers={"Origin":"https://test"})
        assert response.status_code==403


def test_resend_https_api_sends_without_smtp(monkeypatch):
    monkeypatch.setenv("PANEL_EMAIL_TRANSPORT","resend_https")
    monkeypatch.setenv("PANEL_SMTP_FROM","Orion <onboarding@resend.dev>")
    monkeypatch.setenv("PANEL_SMTP_PASSWORD","ci-only-resend-secret")
    monkeypatch.setenv("PANEL_SMTP_PORT","465")
    responses=[]
    class Response:
        status_code=200
        def json(self):return {"id":"synthetic-provider-uuid"}
    def fake_post(url,headers,json,timeout):
        responses.append((url,headers,json,timeout))
        return Response()
    monkeypatch.setattr(account_auth.httpx,"post",fake_post)
    monkeypatch.setattr(account_auth.smtplib,"SMTP_SSL",
        lambda *a,**kw: (_ for _ in ()).throw(AssertionError("No SMTP requests")))
    account_auth._send_email("owner-mailbox@example.test","verify","123456","https://test")
    assert len(responses)==1
    url,headers,payload,timeout=responses[0]
    assert url=="https://api.resend.com/emails"
    assert headers["Authorization"]=="Bearer ci-only-resend-secret"
    assert payload["to"]==["owner-mailbox@example.test"]
    assert payload["from"]=="Orion <onboarding@resend.dev>"
    assert "123456" in payload["html"] and "123456" in payload["text"]
    assert timeout<=15
