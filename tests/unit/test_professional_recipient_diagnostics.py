"""No-PII recipient check and owner-only diagnostics for staff WhatsApp."""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException, Response
from starlette.requests import Request

from app import appointment_communications_api as routes
from app import appointment_notifications
from app import professional_recipient_diagnostics as diagnostic
from app import public_site_api

PHONE = "+5511999990000"
KEY = "synthetic-private-bridge-token-longer-than-32-characters"


@pytest.mark.asyncio
@pytest.mark.parametrize("flag,payload,expected", [
    (200, {"registered": True, "identity": "pn_confirmed"}, "registered"),
    (200, {"registered": True, "identity": "phone_normalized"}, "registered"),
    (200, {"registered": True, "identity": "different_identifier"}, "unverified"),
    (200, {"registered": False, "identity": "unverified"}, "not_recognized"),
    (200, {"registered": None, "identity": "lookup_failed"}, "unverified"),
    (502, {"error": "private"}, "unavailable"),
])
async def test_provider_check_is_private_get_and_returns_only_safe_status(monkeypatch,flag,payload,expected):
    monkeypatch.setenv("QR_BRIDGE_TOKEN",KEY)
    monkeypatch.setattr(diagnostic,"bridge_origin",
                        lambda:"http://orion-qr-bridge.railway.internal:3000")
    calls=[]
    class Client:
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def get(self,url,**kwargs):
            calls.append((url,kwargs))
            return SimpleNamespace(status_code=flag,json=lambda:payload)
    monkeypatch.setattr(diagnostic.httpx,"AsyncClient",lambda **kwargs:Client())
    outcome=await diagnostic.check_professional_recipient(PHONE)
    assert outcome==expected
    assert len(calls)==1
    assert calls[0][0].endswith("/internal/recipient")
    assert calls[0][1]["params"] == {"phone":PHONE[1:]}
    assert calls[0][1]["headers"] == {"X-Orion-Bridge-Key":KEY}
    assert PHONE not in outcome and KEY not in outcome


@pytest.mark.asyncio
async def test_missing_invalid_or_unconfigured_number_never_contacts_provider(monkeypatch):
    monkeypatch.delenv("QR_BRIDGE_TOKEN",raising=False)
    assert await diagnostic.check_professional_recipient(None)=="missing_phone"
    assert await diagnostic.check_professional_recipient("12345")=="invalid_phone"
    assert await diagnostic.check_professional_recipient(PHONE)=="unavailable"


def request():
    return Request({"type":"http","method":"GET","path":"/",
                    "headers":[],"client":("127.0.0.1",12000)})


@pytest.mark.asyncio
async def test_other_merchant_or_readonly_staff_cannot_probe_professional(monkeypatch):
    tenant,professional=uuid4(),uuid4()
    calls=[]
    async def viewer(*args):
        calls.append("access")
        return SimpleNamespace(role="viewer",account_id=uuid4())
    monkeypatch.setattr(routes,"resolve_tenant_access",viewer)
    with pytest.raises(HTTPException) as blocked:
        await routes.professional_recipient_readiness(
            tenant,professional,request(),Response(),SimpleNamespace(role="member"))
    assert blocked.value.status_code==403 and calls==["access"]


@pytest.mark.asyncio
async def test_owner_can_check_only_the_database_professional_without_sending(monkeypatch):
    tenant,professional,owner=uuid4(),uuid4(),uuid4()
    async def access(*args):
        return SimpleNamespace(role="owner",account_id=owner)
    monkeypatch.setattr(routes,"resolve_tenant_access",access)
    monkeypatch.setattr(appointment_notifications,"central_enabled",lambda:True)
    async def limit(*args,**kwargs):
        assert kwargs["subject"]==str(owner)
    monkeypatch.setattr(public_site_api,"public_limit",limit)
    class Rows:
        def mappings(self):return self
        def first(self):return {"active":True,"phone_e164":PHONE}
    class Database:
        async def execute(self,stmt,params):
            assert str(stmt).lstrip().startswith("SELECT")
            assert params=={"tenant":tenant,"professional":professional}
            return Rows()
    @asynccontextmanager
    async def management(t,actor):
        assert t==tenant
        yield Database()
    monkeypatch.setattr(routes,"management_session",management)
    attempted=[]
    async def look(phone):
        attempted.append(phone)
        return "not_recognized"
    monkeypatch.setattr(diagnostic,"check_professional_recipient",look)
    response=Response()
    answer=await routes.professional_recipient_readiness(
        tenant,professional,request(),response,SimpleNamespace(role="member"))
    assert answer=={"status":"not_recognized"}
    assert attempted==[PHONE]
    assert response.headers["cache-control"]=="no-store"
    assert PHONE not in str(answer)
