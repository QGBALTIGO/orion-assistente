"""Retrospective facts cannot start or resume a customer automation."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app import automation_runtime as runtime, plan_permissions


@pytest.mark.asyncio
async def test_direct_queue_refuses_retroactive_facts_before_schema_or_consent_reads(monkeypatch):
    monkeypatch.setenv("MARKETING_EXECUTION_ENABLED", "1")
    settings = {"enabled": True, "owner_active": True, "tenant_status": "active",
                "preferences_approved": True}
    class DB:
        async def execute(self, *args, **kwargs):
            pytest.fail("A historical entry must not enqueue a new interaction")
    assert await runtime.queue_notification(DB(), uuid4(), settings, {}, {},
        "after_service", "historical-visit", {"booking_mode": "retroactive"}, {}) is False


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,expected", [
    ("standard", None), ("exceptional", None), ("retroactive", "retroactive_appointment"),
])
async def test_dispatch_rechecks_persisted_booking_mode_even_for_preexisting_job(monkeypatch, mode, expected):
    now = datetime.now(timezone.utc)
    customer = uuid4()
    async def allowed(*args): return True
    async def contact(*args): return {"marketing_opt_in": True, "updated_at": now}, None
    monkeypatch.setattr(plan_permissions, "has_feature", allowed)
    monkeypatch.setattr(runtime, "contact_facts", contact)
    class DB:
        async def execute(self, query, params):
            sql = str(query)
            if "SELECT version FROM management_preferences" in sql:
                return SimpleNamespace(scalar_one_or_none=lambda: 1)
            if "FROM appointments" in sql:
                row = {"status": "completed", "start_at": now-timedelta(days=20),
                       "updated_at": now, "booking_mode": mode}
            elif "FROM customers" in sql:
                row = {"id": customer, "version": 1}
            else:
                pytest.fail("Unexpected fact lookup: " + sql)
            return SimpleNamespace(mappings=lambda: SimpleNamespace(one_or_none=lambda: row))
    job = {"event": "after_service", "contact_e164": "+5567999999901",
        "appointment_id": uuid4(), "appointment_version": now, "campaign_id": None,
        "customer_id": customer, "customer_version": 1, "settings_version": 1,
        "preferences_version": 1, "consent_version": now, "interaction_revision": 0,
        "due_at": now-timedelta(seconds=1), "expires_at": now+timedelta(hours=1)}
    settings = {"enabled": True, "version": 1, "owner_active": True,
                "tenant_status": "active", "preferences_approved": True}
    assert await runtime.job_reason(DB(), uuid4(), job, settings) == expected
