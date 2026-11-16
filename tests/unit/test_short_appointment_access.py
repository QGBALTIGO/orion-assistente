"""Security contract for globally short appointment links and QR-sender OTP.

No network, actual WhatsApp send, real phone or production DB is used.
"""
import hashlib
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from app import short_appointment_api as short, appointment_access as access


class FakeCache:
    def __init__(self):
        self.data={}
        self.attempts={}
    async def set(self,key,val,ex=None,nx=False):
        if nx and key in self.data:return False
        self.data[key]=val
        return True
    async def get(self,key):
        return self.data.get(key)
    async def eval(self,source,nkeys,key,*args):
        if "INCR" in source:
            self.attempts[key]=self.attempts.get(key,0)+1
            return self.attempts[key]
        previous=self.data.get(key)
        if previous!=args[0]:return 0
        if len(args)==2:self.data[key]=args[1]
        else:self.data.pop(key,None)
        return 1
    async def aclose(self):pass


class Result:
    def __init__(self,value):self.value=value
    def mappings(self):return self
    def first(self):return self.value
    def scalar_one_or_none(self):return self.value


class Registry:
    def __init__(self,row):self.row=row;self.calls=[]
    async def __aenter__(self):return self
    async def __aexit__(self,*_):pass
    async def execute(self,q,params):
        assert "orion_resolve_short_appointment" in str(q)
        assert params["phone"]=="+5521999999922"
        self.calls.append(params)
        return Result(self.row)


@pytest.fixture
def api(monkeypatch):
    app=FastAPI()
    app.include_router(short.router)
    cache=FakeCache()
    monkeypatch.setenv("INTEGRATION_FERNET_KEY","synthetic-qa-secret-key-"+"x"*80)
    monkeypatch.setattr(short,"active",lambda:True)
    monkeypatch.setattr(short,"_redis",lambda:cache)
    async def no_rate(*_args,**_kwargs):
        pass
    monkeypatch.setattr(short,"public_limit",no_rate)
    from app import appointment_confirmation
    async def central_number():return "5567999999999"
    monkeypatch.setattr(appointment_confirmation,"central_admin_phone",central_number)
    client=TestClient(app,base_url="https://orionagenda.pro")
    return client,cache


def test_public_code_is_not_a_password_and_start_never_resolves_booking(api):
    client,cache=api
    request={"code":"AG-ABCDEF","role":"customer"}
    # The public shell has no appointment facts, even if the code is fake.
    page=client.get("/AG-ABCDEF")
    assert page.status_code==200
    assert "Cliente de Teste" not in page.text
    assert "endereço" not in page.text.lower()
    started=client.post("/api/short-appointment/start",json=request,
                        headers={"Origin":"https://orionagenda.pro"})
    assert started.status_code==200,started.text
    result=started.json()
    assert set(result)=={"status","challenge","whatsapp_url"}
    assert result["status"]=="requested"
    assert result["whatsapp_url"].startswith("https://wa.me/5567999999999?text=")
    assert parse_qs(urlsplit(result["whatsapp_url"]).query)["text"][0].startswith("ORION VERIFICAR C AG-ABCDEF ")
    assert len(cache.data)==1
    assert "__Host-orion_appointment_challenge" in client.cookies
    # No customer phone/booking details are leaked in the API reply.
    assert "tenant_id" not in started.text and "appointment_id" not in started.text
    unknown=client.get("/AG-AAAAAA")
    assert unknown.status_code==200 and unknown.content==page.content
    assert client.get("/p/AG-ABCDEF").status_code==200


def test_protocol_validation_and_cross_site_requests_are_rejected(api):
    client,_=api
    assert client.get("/login").status_code==404
    assert client.get("/AG-ABCDEFGHI").status_code==404
    result=client.post("/api/short-appointment/start",
                       json={"code":"AG-ABCDEF"},
                       headers={"Origin":"https://evil.invalid"})
    assert result.status_code==403
    assert client.post("/api/short-appointment/verify",json={
        "code":"AG-ABCDEF","challenge":"A"*22,"otp":"000000",
        "role":"customer"},headers={"Origin":"https://orionagenda.pro"}).status_code in (400,503)


@pytest.mark.asyncio
async def test_sender_phone_must_match_booking_before_any_otp_is_issued(api,monkeypatch):
    client,cache=api
    started=client.post("/api/short-appointment/start",
        json={"code":"AG-ABCDEF","role":"professional"},
        headers={"Origin":"https://orionagenda.pro"}).json()
    challenge=started["challenge"]
    lookup=Registry(None)
    monkeypatch.setattr(short,"support_session_factory",lambda:lambda:lookup)
    async def eligible(*_):return True
    monkeypatch.setattr(short,"has_feature_for_tenant",eligible)
    answer=await short.inbound_otp_from_verified_phone(
        f"ORION VERIFICAR P AG-ABCDEF {challenge}","5521999999922")
    assert answer["state"]=="unrecognized"
    assert "código de acesso" not in answer["answer"].lower()
    assert json.loads(next(iter(cache.data.values())))["state"]=="waiting"
    assert lookup.calls[0]["role"]=="professional"


@pytest.mark.asyncio
async def test_verified_phone_generates_one_otp_and_never_reissues_on_qr_retry(api,monkeypatch):
    client,cache=api
    challenge=client.post("/api/short-appointment/start",
        json={"code":"AG-ABCDEF","role":"customer"},
        headers={"Origin":"https://orionagenda.pro"}).json()["challenge"]
    t,a,c,p=uuid4(),uuid4(),uuid4(),uuid4()
    lookup=Registry({"tenant_id":t,"appointment_id":a,
                     "customer_id":c,"professional_id":p})
    monkeypatch.setattr(short,"support_session_factory",lambda:lambda:lookup)
    async def eligible(_tenant):return True
    monkeypatch.setattr(short,"has_feature_for_tenant",eligible)
    msg=f"ORION VERIFICAR C AG-ABCDEF {challenge}"
    first=await short.inbound_otp_from_verified_phone(msg,"5521999999922")
    second=await short.inbound_otp_from_verified_phone(msg,"5521999999922")
    assert first["state"]=="confirmed"
    assert first["answer"].count("Código de acesso") == 1
    assert second["state"]=="unrecognized"
    state=json.loads(next(iter(cache.data.values())))
    assert state["state"]=="verified_whatsapp"
    assert state["role"]=="customer" and state["tenant"]==str(t)
    assert "5521999999922" not in json.dumps(state)
    assert "otp_hash" in state and len(state["otp_hash"])==64
    import re
    pin=re.search(r"\*([0-9]{6})\*", first["answer"]).group(1)
    assert len(pin)==6 and pin.isdecimal()
    assert short.digest_code(challenge,pin)==state["otp_hash"]


def test_legacy_bearer_links_stay_accepted_and_short_links_are_feature_gated(monkeypatch):
    monkeypatch.setenv("APPOINTMENT_COMMUNICATIONS_ENABLED","1")
    monkeypatch.delenv("ORION_SHORT_APPOINTMENT_LINKS_ENABLED",raising=False)
    assert access.short_links_enabled() is False
    raw=str(uuid4())+"."+"a"*43
    tenant,h=access.parse_token(raw)
    assert tenant and len(h)==64


@pytest.mark.parametrize("code",["AG-ABCDEF","AG-ABCDEF-08A31DB2"])
def test_global_short_alias_format(code):
    assert short.normalized(code)==code


@pytest.mark.parametrize("code",["", "AG-000000","AG-IABCDE","../panel","AG-ABCD","AG-ABCDEF-XYZ"])
def test_invalid_short_alias_rejected(code):
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        short.normalized(code)



def test_otp_attempts_are_per_challenge_even_when_ip_changing(api):
    client,cache=api
    result=client.post("/api/short-appointment/start",
        json={"code":"AG-ABCDEF","role":"customer"},
        headers={"Origin":"https://orionagenda.pro"}).json()
    for number in range(1,8):
        response=client.post("/api/short-appointment/verify",json={
            "code":"AG-ABCDEF","role":"customer",
            "challenge":result["challenge"],"otp":"123456"},
            headers={"Origin":"https://orionagenda.pro","X-Forwarded-For":f"198.51.100.{number}"})
        assert response.status_code==(400 if number<=6 else 429)
    assert len(cache.attempts)==1 and list(cache.attempts.values())==[7]


def test_pin_is_one_use_and_cookie_contains_no_protocol_secret(api,monkeypatch):
    client,cache=api
    start=client.post("/api/short-appointment/start",
        json={"code":"AG-ABCDEF","role":"customer"},
        headers={"Origin":"https://orionagenda.pro"}).json()
    challenge=start["challenge"]
    browser=client.cookies[short.CHALLENGE_COOKIE]
    key=short.scoped_redis_key("challenge",challenge)
    data=json.loads(cache.data[key])
    pin="302051"
    data.update(state="verified_whatsapp",
                otp_hash=short.digest_code(challenge,pin),
                tenant=str(uuid4()),appointment=str(uuid4()),
                customer=str(uuid4()),professional=str(uuid4()),
                phone_hash=hashlib.sha256(b"+5567999999922").hexdigest())
    cache.data[key]=json.dumps(data,separators=(",",":"))
    async def issue(_state,_role):
        return {"authorized":True,"token":str(uuid4())+"."+"a"*43}
    monkeypatch.setattr(short,"session_response",issue)
    req={"code":"AG-ABCDEF","role":"customer","challenge":challenge,"otp":pin}
    response=client.post("/api/short-appointment/verify",
        json=req,headers={"Origin":"https://orionagenda.pro"})
    assert response.status_code==200,response.text
    assert response.json()["authorized"] is True
    assert short.SESSION_COOKIE in client.cookies
    assert short.CHALLENGE_COOKIE not in client.cookies
    cookie_value=response.headers["set-cookie"]
    assert "httponly" in cookie_value.lower()
    assert "secure" in cookie_value.lower()
    assert "samesite=lax" in cookie_value.lower()
    assert pin not in cookie_value
    assert key not in cache.data
    duplicate=client.post("/api/short-appointment/verify",
        json=req,headers={"Origin":"https://orionagenda.pro"})
    assert duplicate.status_code==400
