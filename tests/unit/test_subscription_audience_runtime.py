"""A missing preferences store must never admit an automation permissively."""
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app import subscription_audience_store as audience
from app import subscription_chatbot_engine as engine


@pytest.mark.asyncio
async def test_unavailable_contact_preferences_fail_before_creating_run(monkeypatch):
    async def revision(db, tenant):
        return 1
    async def unavailable(db, tenant, phone):
        raise RuntimeError("subscription_audience_contacts unavailable")
    class NoWrites:
        async def execute(self, *args, **kwargs):
            pytest.fail("Preferences unavailable must not create a run or effects")
    monkeypatch.setattr(engine.store, "revision", revision)
    monkeypatch.setattr(audience, "preferences", unavailable)
    with pytest.raises(RuntimeError, match="subscription_audience_contacts unavailable"):
        await engine._input(NoWrites(), {"tenant_id": uuid4()}, uuid4(),
                            {"chatbot_revision": 1, "phone": "5567999999901", "kind": "text"}, 1)


@pytest.mark.asyncio
async def test_test_response_handles_unadmitted_run_with_rollback_error(monkeypatch):
    async def check_device(*args, **kwargs):
        return {"id": uuid4(), "tenant_id": uuid4(), "status": "connected", "generation": uuid4()}, {"fence": 1}
    async def allowed(*args, **kwargs):
        return True
    async def unadmitted(*args, **kwargs):
        return None
    class Event:
        def scalar_one(self):
            return uuid4()
    class Database:
        async def execute(self, *args, **kwargs):
            return Event()
    monkeypatch.setattr(engine.store, "check_device", check_device)
    monkeypatch.setattr(engine, "_contact_allows", allowed)
    monkeypatch.setattr(engine, "_input", unadmitted)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as raised:
        await engine.create_test(Database(), uuid4(), SimpleNamespace(account_id=uuid4()),
                                 {"device_id": None, "keyword": "olá"},
                                 SimpleNamespace(device_id=uuid4(), phone="5567999999901", expected_chatbot_revision=1),
                                 {"id": uuid4()})
    assert raised.value.status_code == 409 and raised.value.detail["code"] == "CHATBOT_CONFLICT"
