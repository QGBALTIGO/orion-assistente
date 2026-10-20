from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app import automation_runtime as runtime


class Result:
    def __init__(self, row=None, scalar=None): self.row, self.scalar = row, scalar
    def mappings(self): return self
    def one_or_none(self): return self.row
    def scalar_one(self): return self.scalar


@pytest.fixture
def flow(monkeypatch):
    monkeypatch.setenv("MARKETING_EXECUTION_ENABLED", "1")
    now = datetime.now(timezone.utc).replace(hour=14, minute=0, second=0, microsecond=0)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None): return now if tz else now.replace(tzinfo=None)
    monkeypatch.setattr(runtime, "datetime", Clock)
    state = SimpleNamespace(tenant=uuid4(), job={"id": uuid4(), "channel_id": uuid4(), "contact_e164": "+5567999999901",
        "body": "Mensagem sintética", "status": "pending", "due_at": now-timedelta(minutes=1), "expires_at": now+timedelta(days=2)},
        settings={"timezone": "UTC", "daily_limit": 20}, reason=None, second_reason=None, checks=0, count=0, queries=[], sends=[])
    class DB:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def execute(self, query, params=None):
            sql = str(query); values = params or {}; state.queries.append((sql, values))
            if sql.startswith("SELECT * FROM automation_jobs"):
                return Result(dict(state.job) if state.job["status"] == "pending" else None)
            if sql.startswith("SELECT channel_id,status FROM automation_jobs"):
                return Result(dict(state.job) if state.job["status"] in {"sent", "uncertain"} else None)
            if "SELECT count(*)" in sql: return Result(scalar=state.count)
            if "SET status='sending'" in sql:
                state.job.update(status="sending", claim_token=values["token"], attempted_at=now)
            if "SET status=:status" in sql:
                state.job.update(status=values["status"], last_error=values["error"], external_delivery_id=values["external"])
            if "SET status='cancelled'" in sql:
                state.job.update(status="cancelled", last_error=values.get("reason", "daily_limit_expired"))
            if "SET due_at=:due" in sql: state.job["due_at"] = values["due"]
            if "SET last_reconciled_at" in sql:
                if values["accepted"]: state.job["status"] = "sent"
                previous = state.job.get("delivery_status", "unknown")
                state.job["delivery_status"] = max((previous, values["delivery"]), key=["unknown", "delivered", "read"].index)
            return Result()
    @asynccontextmanager
    async def tx(db, tenant):
        assert tenant == state.tenant
        yield db
    async def settings(*args, **kwargs): return state.settings
    async def reason(*args):
        state.checks += 1
        return state.second_reason if state.checks > 1 else state.reason
    async def sender(tenant, channel, phone, body, operation):
        state.sends.append((tenant, channel, phone, body, operation))
        return operation
    monkeypatch.setattr(runtime, "get_session_factory", lambda: DB)
    monkeypatch.setattr(runtime, "tenant_transaction", tx)
    monkeypatch.setattr(runtime, "settings_for", settings)
    monkeypatch.setattr(runtime, "job_reason", reason)
    state.sender = sender
    state.process = lambda sender=sender: runtime.process_automation_job({"automation_sender": sender}, str(state.tenant), str(state.job["id"]))
    return state


@pytest.mark.asyncio
async def test_attempt_is_committed_before_send_and_repeated_worker_never_resends(flow):
    async def sender(*args):
        assert flow.job["status"] == "sending" and flow.job["claim_token"]
        return await flow.sender(*args)
    assert await flow.process(sender) == "sent"
    assert await flow.process(sender) == "already_claimed"
    assert len(flow.sends) == 1 and flow.job.get("delivery_status", "unknown") == "unknown"


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["timeout", "wrong_id", "reset"])
async def test_uncertain_or_reset_delivery_is_never_retried(flow, outcome):
    async def sender(*args):
        flow.sends.append(args)
        if outcome in {"timeout", "reset"}: raise TimeoutError("synthetic-private-provider-error")
        return str(uuid4())
    assert await flow.process(sender) == "uncertain"
    assert await flow.process(sender) == "already_claimed"
    assert len(flow.sends) == 1 and flow.job["external_delivery_id"] is None
    assert "synthetic-private" not in str(flow.job)


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["consent_changed", "conversation_changed", "approval_revoked", "campaign_paused", "coupon_changed"])
async def test_revalidation_after_claim_stops_external_action(flow, reason):
    flow.second_reason = reason
    assert await flow.process() == "cancelled"
    assert not flow.sends and flow.job["last_error"] == reason


@pytest.mark.asyncio
async def test_not_due_never_claims_or_consumes_daily_limit(flow):
    flow.reason = "not_due"
    assert await flow.process() == "not_due"
    assert flow.job["status"] == "pending" and "attempted_at" not in flow.job


@pytest.mark.asyncio
async def test_daily_limit_defers_to_next_local_opening_without_attempt(flow):
    flow.count = 20
    assert await flow.process() == "daily_limit"
    assert flow.job["due_at"].hour == 9 and "attempted_at" not in flow.job and not flow.sends


@pytest.mark.asyncio
@pytest.mark.parametrize("record,expected", [("unknown", "uncertain"), ("wrong_id", "uncertain"), ("accepted", "unknown"), ("delivered", "delivered"), ("read", "read")])
async def test_reconciliation_is_read_only_even_with_execution_paused(flow, record, expected, monkeypatch):
    flow.job["status"] = "uncertain"
    monkeypatch.setenv("MARKETING_EXECUTION_ENABLED", "0")
    monkeypatch.setenv("MARKETING_RECONCILIATION_ENABLED", "1")
    inspected = []
    async def reader(t, c, operation):
        inspected.append(operation)
        return {"operation_id": str(uuid4()) if record == "wrong_id" else operation,
                "accepted": record != "unknown", "state": "accepted", "delivery": record if record in {"delivered", "read"} else "unknown"}
    assert await runtime.reconcile_automation_job({"automation_inspector": reader}, str(flow.tenant), str(flow.job["id"])) == expected
    assert inspected == [str(flow.job["id"])] and not flow.sends
    assert await flow.process() == "disabled"


@pytest.mark.asyncio
async def test_delivery_observations_are_monotonic(flow):
    flow.job.update(status="sent", delivery_status="read")
    async def reader(t, c, operation): return {"operation_id": operation, "accepted": True, "state": "accepted", "delivery": "unknown"}
    await runtime.reconcile_automation_job({"automation_inspector": reader}, str(flow.tenant), str(flow.job["id"]))
    assert flow.job["delivery_status"] == "read" and not flow.sends


@pytest.mark.asyncio
async def test_disabled_entrypoints_touch_no_schema_or_provider(monkeypatch):
    monkeypatch.delenv("MARKETING_EXECUTION_ENABLED", raising=False)
    monkeypatch.delenv("MARKETING_RECONCILIATION_ENABLED", raising=False)
    monkeypatch.setattr(runtime, "get_session_factory", lambda: pytest.fail("disabled schema access"))
    monkeypatch.setattr(runtime, "support_session_factory", lambda: pytest.fail("disabled tenant scan"))
    assert await runtime.plan_tenant(uuid4()) == 0
    assert await runtime.scan_automations({}) == "disabled"
    assert await runtime.process_automation_job({}, str(uuid4()), str(uuid4())) == "disabled"
    assert await runtime.reconcile_automation_job({}, str(uuid4()), str(uuid4())) == "disabled"
    assert not await runtime.queue_notification(None, uuid4(), {}, {}, {}, "campaign", "key", {}, {})
    await runtime.interaction(uuid4(), "+5567999999901")


@pytest.mark.parametrize("field,value", [("enabled", False), ("owner_active", False),
    ("tenant_status", "paused"), ("preferences_approved", False)])
def test_reviewed_execution_requires_current_owner_and_exact_preferences(field, value):
    settings = {"enabled": True, "owner_active": True, "tenant_status": "active", "preferences_approved": True}
    assert runtime.execution_authorized(settings)
    assert runtime.execution_approved is runtime.execution_authorized
    settings[field] = value
    assert not runtime.execution_authorized(settings)


@pytest.mark.asyncio
async def test_inactive_campaign_revalidates_new_booking_in_actual_job_reason(monkeypatch):
    now = datetime.now(timezone.utc)
    customer = uuid4()
    class DB:
        async def execute(self, query, params):
            sql = str(query)
            if "SELECT version FROM management_preferences" in sql:
                return SimpleNamespace(scalar_one_or_none=lambda: 1)
            if "FROM campaigns" in sql:
                row = {"status": "approved", "version": 2, "audience": "inactive_customers"}
            elif "AS eligible" in sql:
                row = {"eligible": False, "has_future": True}
            else:
                row = {"id": customer, "version": 1}
            return SimpleNamespace(mappings=lambda: SimpleNamespace(one_or_none=lambda: row, one=lambda: row))
    async def contact(*args): return {"marketing_opt_in": True, "updated_at": now}, None
    monkeypatch.setattr(runtime, "contact_facts", contact)
    job = {"event": "campaign", "contact_e164": "+5567999999901", "appointment_id": None,
        "campaign_id": uuid4(), "campaign_version": 2, "customer_id": customer, "customer_version": 1,
        "settings_version": 1, "preferences_version": 1, "consent_version": now, "interaction_revision": 0,
        "due_at": now-timedelta(seconds=1), "expires_at": now+timedelta(hours=1)}
    settings = {"enabled": True, "version": 1, "owner_active": True, "tenant_status": "active", "preferences_approved": True}
    assert await runtime.job_reason(DB(), uuid4(), job, settings) == "new_booking"
