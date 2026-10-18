"""No waitlist or customer consent is invented by an automation draft."""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

from fastapi import HTTPException
import pytest

from app import automation_api as api, automation_policy as policy, automation_runtime as runtime
from app.catalog_api import Actor


def test_vacancy_feature_is_explicitly_unavailable_and_not_shared_mutable_state():
    features = policy.feature_status()
    assert features["vacancy"] == {"available": False, "enabled": False, "mode": "draft", "reason": "waitlist_contract_missing"}
    features["vacancy"]["enabled"] = True
    assert not policy.feature_status()["vacancy"]["enabled"]


@pytest.mark.parametrize("requested", [False, True])
def test_draft_preference_is_not_execution_consent(requested):
    preferences = {"automations": {"vacancy_requested": requested}}
    assert policy.unavailable_requested(preferences) is requested
    settings = {"enabled": True, "owner_active": True, "tenant_status": "active", "preferences_approved": True,
                "vacancy_requested": requested}
    assert runtime.execution_authorized(settings) is not requested


@pytest.mark.asyncio
@pytest.mark.parametrize("event", ["vacancy", "waitlist", "unknown"])
async def test_unimplemented_event_never_reads_clients_or_creates_job(event, monkeypatch):
    monkeypatch.setenv("MARKETING_EXECUTION_ENABLED", "1")
    settings = {"enabled": True, "owner_active": True, "tenant_status": "active", "preferences_approved": True}
    assert await runtime.queue_notification(None, uuid4(), settings, {}, {}, event, "synthetic", {}, {}) is False


def test_consumer_rejects_vacancy_without_expecting_fabricated_facts():
    from datetime import datetime, timezone
    assert policy.delivery_reason({"event": "vacancy"}, None, None, None,
        now=datetime.now(timezone.utc), preferences_version=None) == "waitlist_contract_missing"


@pytest.fixture
def settings_api(monkeypatch):
    monkeypatch.setenv("MARKETING_EXECUTION_ENABLED", "1")
    state = SimpleNamespace(tenant=uuid4(), actor=Actor("member", account_id=uuid4()), writes=[],
        current=None, replay=False, requested=True)
    class DB:
        info = {"automation_owner_auth_version": 1}
        async def execute(self, query, params=None):
            sql = str(query)
            if sql.startswith("SELECT version FROM management_preferences"):
                return SimpleNamespace(scalar_one_or_none=lambda: 1)
            if sql.startswith("SELECT preferences FROM management_preferences"):
                return SimpleNamespace(scalar_one_or_none=lambda: {"automations": {"vacancy_requested": state.requested}})
            if sql.startswith(("INSERT", "UPDATE")): state.writes.append(sql)
            return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: []))
    db = DB()
    @asynccontextmanager
    async def owner_session(*args, **kwargs): yield db
    @asynccontextmanager
    async def read_session(*args, **kwargs): yield db
    async def owner(*args): return 1
    async def settings(*args, **kwargs): return state.current
    async def request(*args, **kwargs): return None, state.replay
    async def channel(*args): return {}
    monkeypatch.setattr(api, "owner", owner)
    monkeypatch.setattr(api, "owner_write_session", owner_session)
    monkeypatch.setattr(api, "management_session", read_session)
    monkeypatch.setattr(api, "settings_for", settings)
    monkeypatch.setattr(api, "request_identity", request)
    monkeypatch.setattr(api, "checked_channel", channel)
    return state


@pytest.mark.asyncio
async def test_owner_enable_cannot_treat_vacancy_draft_as_reviewed_function(settings_api):
    state = settings_api
    payload = api.ExecutionSave(expected_version=0, expected_preferences_version=1, idempotency_key="synthetic-vacancy",
                                enabled=True, channel_id=uuid4(), acknowledged=True)
    with pytest.raises(HTTPException) as rejected:
        await api.save_execution(state.tenant, payload, state.actor)
    assert rejected.value.status_code == 409 and "rascunho" in rejected.value.detail
    assert not state.writes


@pytest.mark.asyncio
async def test_replayed_enable_does_not_report_unavailable_feature_as_enabled(settings_api):
    state = settings_api
    state.current = {"version": 2, "enabled": True, "owner_active": True, "tenant_status": "active",
                     "preferences_approved": True, "vacancy_requested": True}
    state.replay = True
    payload = api.ExecutionSave(expected_version=0, expected_preferences_version=1, idempotency_key="synthetic-replay",
                                enabled=True, channel_id=uuid4(), acknowledged=True)
    result = await api.save_execution(state.tenant, payload, state.actor)
    assert result == {"recorded": True, "version": 2, "enabled": False} and not state.writes


@pytest.mark.asyncio
async def test_execution_get_includes_truthful_draft_when_feature_flag_is_off(settings_api, monkeypatch):
    monkeypatch.setenv("MARKETING_EXECUTION_ENABLED", "0")
    monkeypatch.setenv("MARKETING_RECONCILIATION_ENABLED", "0")
    result = await api.get_execution(settings_api.tenant, settings_api.actor)
    assert not result["enabled"] and not result["available"]
    assert result["features"]["vacancy"]["mode"] == "draft" and not settings_api.writes
