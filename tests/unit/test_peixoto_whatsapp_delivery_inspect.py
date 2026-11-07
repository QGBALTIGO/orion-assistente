"""One-contact WhatsApp lookup is read-only and redacts identifiers."""
from contextlib import asynccontextmanager
from types import SimpleNamespace
import json

import pytest
from app import peixoto_whatsapp_delivery_inspect as qa


def test_provider_payload_redacts_phone_jid_and_secret():
    payload = {
        "registered": True, "identity": "pn_confirmed",
        "phone": "+5567999999922",
        "jid": "private-secret@s.whatsapp.net",
        "credential": "secret",
        "hasLid": False,
        "delivery": {"count": 2, "pending": 0, "serverAccepted": 1,
                     "delivered": 1, "read": 0,
                     "lastReceipt": "delivered", "phone": "+5567999999922",
                     "provider_id": "never-log"},
    }
    output = qa.safe_report(payload)
    dump = json.dumps(output)
    assert output["registered"] is True
    assert output["identity"] == "pn_confirmed"
    assert output["delivery"]["delivered"] == 1
    for forbidden in ("+5567999999922", "private-secret", "credential", "provider_id", "never-log"):
        assert forbidden not in dump


def test_malformed_payload_is_denied_without_exception():
    assert qa.safe_report(None) == {"registered": None, "identity": "unknown", "delivery": {}}
    assert qa.safe_report({"registered": "true", "identity": "user@example.com",
                           "delivery": {"count": -1, "read": "5",
                                        "lastReceipt": "random-secret"}}) == {
        "registered": None, "identity": "unknown",
        "has_lid": None, "delivery": {"last_receipt": "unknown"}
    }


class Result:
    def scalar_one_or_none(self):
        return "+5567999999922"


class DB:
    async def __aenter__(self):
        return self
    async def __aexit__(self, *_):
        pass
    async def execute(self, stmt, params):
        assert "FROM professionals" in str(stmt)
        assert params["tenant"] == qa.TENANT
        assert params["professional"] == qa.PROFESSIONAL
        return Result()


@asynccontextmanager
async def scope(db, tenant):
    assert tenant == qa.TENANT
    yield db


@pytest.mark.asyncio
async def test_authorized_private_lookup_requests_only_fixed_recipient(monkeypatch):
    monkeypatch.setenv("QR_BRIDGE_TOKEN", "synthetic-secret-" + "x" * 64)
    monkeypatch.delenv("ORION_PEIXOTO_CONTROL_PHONE", raising=False)
    monkeypatch.setattr(qa, "get_session_factory", lambda: lambda: DB())
    monkeypatch.setattr(qa, "tenant_transaction", scope)
    monkeypatch.setattr(qa, "bridge_origin", lambda: "http://orion-qr-bridge.railway.internal:3000")
    calls = []
    class Response:
        status_code = 200
        def json(self):
            return {"registered": True, "identity": "pn_confirmed",
                    "delivery": {"count": 1, "delivered": 0,
                                 "lastReceipt": "server_accepted"}}
    class Client:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_):
            pass
        async def get(self, url, params, headers):
            calls.append((url, params, headers))
            return Response()
    monkeypatch.setattr(qa.httpx, "AsyncClient", lambda **kwargs: Client())
    result = await qa.inspect()
    assert result == {"state": "inspected", "registered": True,
                      "identity": "pn_confirmed", "has_lid": None,
                      "delivery": {"count": 1, "delivered": 0,
                                   "last_receipt": "server_accepted"}}
    assert len(calls) == 1
    assert calls[0][0] == "http://orion-qr-bridge.railway.internal:3000/internal/recipient"
    assert calls[0][1] == {"phone": "5567999999922"}


@pytest.mark.asyncio
async def test_missing_bridge_key_fails_without_db_or_network(monkeypatch):
    monkeypatch.delenv("QR_BRIDGE_TOKEN", raising=False)
    monkeypatch.setattr(qa, "get_session_factory",
                        lambda: (_ for _ in ()).throw(AssertionError("not called")))
    assert await qa.inspect() == {"state": "bridge_credential_missing"}


@pytest.mark.asyncio
async def test_control_phone_can_calibrate_unverified_pro_without_logging_identifiers(monkeypatch):
    monkeypatch.setenv("QR_BRIDGE_TOKEN", "synthetic-secret-" + "x" * 64)
    control = "+5521999999922"
    monkeypatch.setenv("ORION_PEIXOTO_CONTROL_PHONE", control)
    monkeypatch.setattr(qa, "get_session_factory", lambda: lambda: DB())
    monkeypatch.setattr(qa, "tenant_transaction", scope)
    monkeypatch.setattr(qa, "bridge_origin", lambda: "http://orion-qr-bridge.railway.internal:3000")
    called = []
    class Response:
        status_code = 200
        def __init__(self, phone):
            self.phone = phone
        def json(self):
            return {
                "registered": self.phone == control.lstrip("+"),
                "identity": "pn_confirmed" if self.phone == control.lstrip("+") else "unverified",
                "jid": self.phone + "@s.whatsapp.net",
                "delivery": {"count": 1, "delivered": int(self.phone == control.lstrip("+")),
                             "lastReceipt": "delivered" if self.phone == control.lstrip("+") else "pending"}
            }
    class Client:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_):
            pass
        async def get(self, url, params, headers):
            called.append(params["phone"])
            return Response(params["phone"])
    monkeypatch.setattr(qa.httpx, "AsyncClient", lambda **kwargs: Client())
    result = await qa.inspect()
    assert called == ["5567999999922", control.lstrip("+")]
    assert result["registered"] is False
    assert result["control_lookup"]["state"] == "inspected"
    assert result["control_lookup"]["registered"] is True
    assert result["control_lookup"]["identity"] == "pn_confirmed"
    safe = json.dumps(result)
    assert control not in safe
    assert "5567999999922" not in safe
    assert "@s.whatsapp.net" not in safe


@pytest.mark.asyncio
async def test_bad_control_phone_prevents_queries(monkeypatch):
    monkeypatch.setenv("QR_BRIDGE_TOKEN", "synthetic-secret-" + "x" * 64)
    monkeypatch.setenv("ORION_PEIXOTO_CONTROL_PHONE", "https://example.com")
    monkeypatch.setattr(qa, "get_session_factory", lambda: lambda: DB())
    monkeypatch.setattr(qa, "tenant_transaction", scope)
    monkeypatch.setattr(qa.httpx, "AsyncClient",
                        lambda **kwargs: (_ for _ in ()).throw(AssertionError("No network allowed")))
    assert await qa.inspect() == {"state": "invalid_control_scope"}


def test_legacy_phone_comparison_is_read_only_and_never_assumed_to_be_same_person():
    assert qa.legacy_brazil_candidate("+5567999999922") == "+556799999922"
    assert qa.legacy_brazil_candidate("+595981234567") is None
    assert qa.legacy_brazil_candidate("+556788888888") is None
    assert qa.legacy_brazil_candidate("5567999999922") is None


@pytest.mark.asyncio
async def test_legacy_comparison_requires_explicit_operator_flag_and_redacts_contact(monkeypatch):
    monkeypatch.setenv("QR_BRIDGE_TOKEN", "synthetic-secret-" + "x" * 64)
    monkeypatch.setenv("ORION_PEIXOTO_COMPARE_LEGACY_PN", "1")
    monkeypatch.delenv("ORION_PEIXOTO_CONTROL_PHONE", raising=False)
    monkeypatch.setattr(qa, "get_session_factory", lambda: lambda: DB())
    monkeypatch.setattr(qa, "tenant_transaction", scope)
    monkeypatch.setattr(qa, "bridge_origin", lambda: "http://orion-qr-bridge.railway.internal:3000")
    calls=[]
    class Response:
        status_code=200
        def __init__(self, phone): self.phone=phone
        def json(self): return {"registered":self.phone=="556799999922",
                "identity":"pn_confirmed" if self.phone.endswith("9922") else "unverified",
                "secret":"MUST_NEVER_LEAK"}
    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *_): pass
        async def get(self, url, params, headers):
            assert url=="http://orion-qr-bridge.railway.internal:3000/internal/recipient"
            calls.append(params["phone"])
            return Response(params["phone"])
    monkeypatch.setattr(qa.httpx,"AsyncClient",lambda **kw:Client())
    result=await qa.inspect()
    assert calls==["5567999999922", "556799999922"]
    assert result["legacy_pn_comparison"]=={
        "state":"inspected","registered":True,"identity":"pn_confirmed"}
    dump=json.dumps(result)
    assert "5567999999922" not in dump and "556799999922" not in dump
    assert "MUST_NEVER_LEAK" not in dump


@pytest.mark.asyncio
async def test_bridge_http_state_is_redacted_but_distinguishes_connection_issue(monkeypatch):
    monkeypatch.setenv("QR_BRIDGE_TOKEN", "synthetic-secret-"+"x"*64)
    monkeypatch.delenv("ORION_PEIXOTO_COMPARE_LEGACY_PN", raising=False)
    monkeypatch.delenv("ORION_PEIXOTO_CONTROL_PHONE", raising=False)
    monkeypatch.setattr(qa, "get_session_factory", lambda:lambda:DB())
    monkeypatch.setattr(qa, "tenant_transaction", scope)
    monkeypatch.setattr(qa, "bridge_origin", lambda:"http://orion-qr-bridge.railway.internal:3000")
    class Client:
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def get(self, url, **kwargs):
            assert url.endswith("/internal/recipient")
            return SimpleNamespace(status_code=409)
    monkeypatch.setattr(qa.httpx,"AsyncClient",lambda **kwargs:Client())
    assert await qa.inspect()=={"state":"private_lookup_unavailable","http_status":409}
