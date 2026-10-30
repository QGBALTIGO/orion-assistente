"""Phone verification, per-tenant customer sessions and protected customer portal.

All tests use synthetic phones, fake Redis and a local HTTPX transport.
Twilio/SMS is never contacted.
"""
import base64
from datetime import date
from uuid import uuid4

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse

from app import public_customer_auth as auth
from app import public_customer_account_api as account


def configure(monkeypatch):
    monkeypatch.setenv("ORION_CUSTOMER_PORTAL_ENABLED", "1")
    monkeypatch.setenv("ORION_CUSTOMER_VERIFY_ACCOUNT_SID", "AC" + "a" * 32)
    monkeypatch.setenv("ORION_CUSTOMER_VERIFY_SERVICE_SID", "VA" + "b" * 32)
    monkeypatch.setenv("ORION_CUSTOMER_VERIFY_AUTH_TOKEN", "test-only-key-of-at-least-20-chars")
    monkeypatch.setenv("INTEGRATION_FERNET_KEY", Fernet.generate_key().decode())


class MemoryRedis:
    def __init__(self):
        self.data={}
    async def set(self,key,value,ex=None):
        assert ex==auth.SESSION_SECONDS
        self.data[key]=value
    async def get(self,key):
        return self.data.get(key)
    async def delete(self,key):
        self.data.pop(key,None)
    async def aclose(self):
        return None


def request(cookie=None):
    headers=[]
    if cookie:
        headers.append((b"cookie",(auth.COOKIE+"="+cookie).encode()))
    return Request({"type":"http","method":"GET","headers":headers,
                    "client":("127.0.0.1",12345),"path":"/s/empresa-sintetica"})


def test_disabled_without_sms_credentials_and_supported_phone_formats(monkeypatch):
    monkeypatch.delenv("ORION_CUSTOMER_PORTAL_ENABLED",raising=False)
    assert not auth.enabled()
    with pytest.raises(HTTPException) as error:
        auth.require_enabled()
    assert error.value.status_code==503
    assert account.PhoneInput(phone="(67) 99999-1111").phone=="+5567999991111"
    for phone in ("abc", "1234", "+12025550100", "+55999999999999", "not-a-phone"):
        with pytest.raises(ValidationError):
            account.PhoneInput(phone=phone)


def test_profile_contract_disallows_internal_fields_and_phone_replacement():
    sample={"expected_version":3,"name":"Cliente Exemplo","instagram":"@cliente.teste",
            "email":"CLIENTE@EXAMPLE.COM","birth_date":"1995-10-03"}
    model=account.ProfilePatch.model_validate(sample)
    assert model.email=="cliente@example.com"
    assert model.instagram=="@cliente.teste"
    assert model.birth_date==date(1995,10,3)
    assert model.expected_version==3
    for update in ({**sample,"notes":"private"}, {**sample,"customer_id":str(uuid4())},
                   {**sample,"whatsapp_e164":"+5567999991111"},
                   {**sample,"active":False},
                   {"expected_version":0,"name":"A"},
                   {"expected_version":3,"instagram":"bad/name"},
                   {"expected_version":3,"birth_date":"2099-01-01"},
                   {"expected_version":3}):
        with pytest.raises(ValidationError):
            account.ProfilePatch.model_validate(update)


@pytest.mark.asyncio
async def test_sessions_are_opaque_httponly_and_tenant_isolated(monkeypatch):
    configure(monkeypatch)
    db=MemoryRedis()
    monkeypatch.setattr(auth,"_cache",lambda:db)
    first,other,customer=uuid4(),uuid4(),uuid4()
    result=JSONResponse({"ok":True})
    claims=await auth.issue_session(first,customer,3,"+5567999991111",result)
    cookie=result.headers["set-cookie"]
    assert auth.COOKIE+"=" in cookie
    assert "httponly" in cookie.lower() and "secure" in cookie.lower()
    assert "samesite=lax" in cookie.lower()
    raw=cookie.split(";",1)[0].split("=",1)[1]
    assert re_fullmatch_token(raw)
    assert "+5567999991111" not in str(db.data)
    assert (await auth.session_claims(raw,first))["customer"]==str(customer)
    assert await auth.session_claims(raw,other) is None
    response=JSONResponse({"ok":True})
    changed=await auth.issue_session(first,customer,4,"+5567999991111",
                                    response,previous=raw,verified_at=claims["verified_at"])
    assert changed["verified_at"]==claims["verified_at"]
    assert await auth.session_claims(raw,first) is None
    raw_new=response.headers["set-cookie"].split(";",1)[0].split("=",1)[1]
    assert (await auth.session_claims(raw_new,first))["version"]==4
    await auth.revoke_session(raw_new)
    assert await auth.session_claims(raw_new,first) is None


def re_fullmatch_token(value):
    import re
    return re.fullmatch(r"[A-Za-z0-9_-]{43}", value) is not None


@pytest.mark.asyncio
async def test_twilio_verify_http_is_fixed_origin_and_never_returns_code(monkeypatch):
    configure(monkeypatch)
    sent=[]
    def receiver(request):
        sent.append(request)
        assert request.url.host=="verify.twilio.com"
        assert request.method=="POST"
        assert request.url.path.startswith("/v2/Services/VA")
        assert request.headers["authorization"].startswith("Basic ")
        content=request.content.decode()
        assert "To=%2B5567999991111" in content
        return httpx.Response(201,json={"status":"approved" if "VerificationCheck" in request.url.path else "pending"})
    original=httpx.AsyncClient
    def client(*args,**kwargs):
        return original(transport=httpx.MockTransport(receiver),**kwargs)
    monkeypatch.setattr(auth.httpx,"AsyncClient",client)
    assert await auth.provider_request("+5567999991111")
    assert await auth.provider_request("+5567999991111","123456")
    assert len(sent)==2
    assert "Code=123456" not in sent[0].content.decode()
    assert "Code=123456" in sent[1].content.decode()


@pytest.mark.asyncio
async def test_anonymous_request_is_uniform_and_not_a_customer_lookup(monkeypatch):
    configure(monkeypatch)
    tenant=uuid4()
    queries=[]
    monkeypatch.setattr(account,"public_limit",lambda *args,**kwargs: async_none())
    monkeypatch.setattr(account,"resolve_public",lambda slug: result_value(tenant))
    async def queued(slug,phone):
        queries.append((slug,phone))
    monkeypatch.setattr(account,"enqueue_code",queued)
    response=await account.request_code("empresa-sintetica",account.PhoneInput(phone="+5567999991111"),request())
    assert response.status_code==202 and "Se este número" in response.body.decode()
    assert queries==[("empresa-sintetica","+5567999991111")]


async def result_value(value):
    return value


async def async_none():
    return None


@pytest.mark.asyncio
async def test_portal_fails_closed_without_cookie_before_database_read(monkeypatch):
    configure(monkeypatch)
    monkeypatch.setattr(account,"resolve_public",lambda slug:result_value(uuid4()))
    async def no_claims(*args):
        return None
    monkeypatch.setattr(account,"session_claims",no_claims)
    monkeypatch.setattr(account,"get_session_factory",lambda:pytest.fail("No database before OTP"))
    with pytest.raises(HTTPException) as error:
        await account.identity("empresa-sintetica",request())
    assert error.value.status_code==401


def test_summary_omits_private_customer_and_staff_data():
    now=date(2026,10,30)
    from datetime import datetime, timezone
    item={"id":uuid4(),"protocol":"AG-ABCDEF","status":"completed",
          "start_at":datetime(2026,10,2,10,tzinfo=timezone.utc),
          "end_at":datetime(2026,10,2,10,40,tzinfo=timezone.utc),
          "updated_at":datetime(2026,10,2,10,tzinfo=timezone.utc),
          "professional_name":"Profissional X",
          "items":[{"name_snapshot":"Corte","duration_snapshot_min":40}],
          "total_cents":4000,"price_mode":"fixed","customer_phone":"+5567999991111",
          "customer_id":uuid4(),"commission_cents":1000,"notes":"Private"}
    summary=account.summarized(item,datetime.now(timezone.utc))
    assert summary["status"]=="completed"
    assert summary["services"]==["Corte"]
    assert not summary["can_change"]
    assert not set(summary).intersection({"customer_phone","customer_id","commission_cents","notes"})
