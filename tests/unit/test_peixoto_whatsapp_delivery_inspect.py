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
