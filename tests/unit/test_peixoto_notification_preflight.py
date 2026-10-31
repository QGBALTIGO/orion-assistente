"""No-PII, read-only checks for a single explicitly authorized tenant."""
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest

from app import peixoto_notification_preflight as preflight


class Rows:
    def __init__(self, records):
        self.records = records

    def mappings(self):
        return self

    def first(self):
        return self.records[0] if self.records else None

    def all(self):
        return self.records


class Db:
    def __init__(self, phone_valid=True):
        self.phone_valid = phone_valid
        self.statements = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass

    async def execute(self, query, params=None):
        sql = str(query)
        self.statements.append(sql)
        if params:
            assert params["tenant"] == preflight.TENANT
        if "FROM professionals" in sql:
            return Rows([{"active": True, "phone_valid": self.phone_valid}])
        if "FROM appointment_communication_settings" in sql:
            return Rows([])
        if "FROM appointment_notifications" in sql:
            return Rows([{"event": "professional_new", "status": "delivered", "total": 1}])
        raise AssertionError("Unexpected SQL or write: " + sql[:80])


@asynccontextmanager
async def tenant_scope(db, tenant):
    assert tenant == preflight.TENANT
    yield db


@pytest.mark.asyncio
async def test_verified_admin_and_professional_emits_only_bools_and_counts(monkeypatch):
    from app import qr_channels
    admin, channel = uuid4(), uuid4()
    db = Db()
    monkeypatch.setattr(preflight, "central_enabled", lambda: True)
    monkeypatch.setattr(preflight, "central_channel", lambda: (admin, channel))
    monkeypatch.setattr(preflight, "get_session_factory", lambda: lambda: db)
    monkeypatch.setattr(preflight, "tenant_transaction", tenant_scope)

    async def active_entitlement(_db, _tenant, feature):
        assert feature == "notifications.basic"
        return True

    monkeypatch.setattr(preflight, "has_feature", active_entitlement)

    class Registry:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

    monkeypatch.setattr(preflight, "support_session_factory", lambda: Registry)
    async def canonical(_db, tenant):
        assert tenant == admin
        return ({"id": channel, "enabled": True}, "ready")
    async def bridge(_row, action, method):
        assert (action, method) == ("status", "GET")
        return {"status": "connected", "phone_verified": True}
    monkeypatch.setattr(qr_channels, "_canonical", canonical)
    monkeypatch.setattr(qr_channels, "_bridge_control", bridge)
    monkeypatch.setattr(qr_channels, "_control_result", lambda row, obj, kind: obj)

    report = await preflight.inspect()
    assert report["central_feature_enabled"] is True
    assert report["active_essential_entitlement"] is True
    assert report["professional_active"] is True
    assert report["professional_phone_valid"] is True
    assert report["notifications_configured"] is True
    assert report["sender_connected_and_verified"] is True
    assert report["last_24h_notifications"] == {"professional_new:delivered": 1}
    import json
    flattened = json.dumps(report)
    assert "phone_e164" not in flattened
    assert "credential" not in flattened
    assert all(sql.lstrip().startswith("SELECT") for sql in db.statements)


@pytest.mark.asyncio
async def test_missing_professional_phone_and_channel_cannot_claim_readiness(monkeypatch):
    db = Db(phone_valid=False)
    monkeypatch.setattr(preflight, "central_enabled", lambda: True)
    monkeypatch.setattr(preflight, "central_channel", lambda: (None, None))
    monkeypatch.setattr(preflight, "get_session_factory", lambda: lambda: db)
    monkeypatch.setattr(preflight, "tenant_transaction", tenant_scope)
    async def no_plan(*_):
        return False
    monkeypatch.setattr(preflight, "has_feature", no_plan)
    result = await preflight.inspect()
    assert result["active_essential_entitlement"] is False
    assert result["professional_phone_valid"] is False
    assert result["sender_channel_ready"] is False
    assert result["sender_connected_and_verified"] is False
