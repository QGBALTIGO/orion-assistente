"""Scheduled-message isolation and its safe projection through existing shared modules."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.plan_permissions import derive_plan
from app.plan_catalog import PLAN_FEATURES, RECOVERY_FEATURES
from app.plan_routing import feature_for_path, subscription_workspace_route
from app import subscription_sent_messages as history
from app.subscription_devices import bulk_effect_descriptor

NOW = datetime(2026, 11, 5, 11, tzinfo=timezone.utc)


@pytest.mark.parametrize("status,expected", [("active", True), ("paused", False), ("suspended", False), ("inactive", False)])
def test_bulk_capability_is_removed_with_subscription_tenant_access(status, expected):
    state = derive_plan({"tenant_id": uuid4(), "niche": "servico_assinaturas", "tenant_status": status}, NOW)
    assert state["permissions"]["bulk_messages.manage"] is expected
    assert state["permissions"]["marketing.campaigns"] is False
    assert state["permissions"]["conversations.manual"] is False


@pytest.mark.parametrize("niche", [None, "barbearia", "clinica", "odontologia", "outro"])
@pytest.mark.parametrize("plan", ["essencial", "profissional", "elite"])
def test_bulk_grant_does_not_expand_other_niches(niche, plan):
    state = derive_plan({"tenant_id": uuid4(), "niche": niche, "tenant_status": "active",
        "manual": {"plan_code": plan, "started_at": NOW-timedelta(days=1), "expires_at": None}}, NOW)
    assert state["permissions"]["bulk_messages.manage"] is False
    assert set(state["features"]) == PLAN_FEATURES[plan] | RECOVERY_FEATURES


@pytest.mark.parametrize("suffix", ["", "/options", "/contacts", "/audience", "/review", "/confirm", "/requests/{key}", "/{batch_id}",
    "/{batch_id}/actions", "/{batch_id}/recipients"])
def test_bulk_read_and_write_routes_have_their_own_feature(suffix):
    route = "/subscription-whatsapp/bulk-messages"+suffix
    for method in ("GET", "POST", "PATCH", "DELETE"):
        assert feature_for_path(route, method) == "bulk_messages.manage"
        assert subscription_workspace_route(route, method, {})
    assert feature_for_path(route+"-similar") == "bulk_messages.manage" if suffix else feature_for_path(route+"-similar") is None


def bulk_effect(status="not_sent"):
    return {"id": uuid4(), "tenant_id": uuid4(), "device_id": uuid4(), "generation": uuid4(),
        "settings_revision": 1, "event_id": uuid4(), "type": "bulk_action", "purpose": "bulk_action",
        "payload": {"phone": "5567999999901", "action": {"type": "text", "text": "Mensagem revisada do lote"}},
        "status": status, "error_code": "TRANSPORT_UNAVAILABLE_BEFORE_SEND", "provider_id": None,
        "attempts": 1, "authorized_at": NOW-timedelta(seconds=2), "expires_at": NOW+timedelta(minutes=2),
        "created_at": NOW-timedelta(seconds=3), "finished_at": NOW, "chatbot_revision": None, "result": {},
        "device_name": "WhatsApp sintético", "device_removed_at": None, "device_runtime": {},
        "incoming_payload": {"origin": "bulk", "batch_id": str(uuid4())}, "incoming_kind": "bulk",
        "run_origin": None, "message_type": "text", "resend_block": None, "due_at": NOW}


@pytest.mark.parametrize("state", ["pending", "running", "accepted", "not_sent", "failed", "uncertain"])
def test_bulk_attempts_never_gain_a_generic_history_resend_action(state):
    row = bulk_effect(state)
    item = history.item_dto(row, NOW, detail=True)
    assert item["origin"] == {"code": "bulk", "label": "Mensagem em massa", "is_resend": False}
    assert item["actions"] == [] and item["resend"]["eligible"] is False
    assert "Mensagem em Massa" in item["resend"]["blocked_reason"]
    assert item["received"]["text"] is None
    assert item["sent"]["text"] == "Mensagem revisada do lote"
    if state == "accepted":
        assert item["outcome"] == "accepted" and item["status"] == "sent"
    elif state == "uncertain":
        assert item["outcome"] == "uncertain" and item["status"] == "uncertain"


def test_pending_bulk_descriptor_contains_no_recipient_content_or_media_url():
    row = bulk_effect()
    value = bulk_effect_descriptor(row)
    assert value == {"operation_id": row["id"], "type": "bulk_action", "action_type": "text",
        "device_id": row["device_id"], "generation": row["generation"], "settings_revision": 1, "due_at": NOW}
    assert "phone" not in value and "payload" not in value and "chatbot_revision" not in value


@pytest.mark.asyncio
async def test_authenticated_poll_routes_interrupted_bulks_and_serial_admission(monkeypatch):
    from contextlib import asynccontextmanager
    from app import subscription_devices as devices, subscription_campaigns as campaigns
    from app import subscription_chatbot_engine as chatbot, subscription_bulk_runtime as bulk
    calls = []
    row = {"id": uuid4(), "tenant_id": uuid4(), "generation": uuid4(), "revision": 1}
    pending = bulk_effect("pending")
    interrupted = bulk_effect("uncertain")
    class Result:
        def __init__(self, rows): self.rows = rows
        def mappings(self): return self
        def all(self): return self.rows
    class DB:
        def __init__(self): self.read = 0
        async def execute(self, query, values):
            self.read += 1
            return Result([interrupted] if self.read == 1 else [pending] if self.read == 2 else [])
    @asynccontextmanager
    async def transaction(_):
        yield DB(), row, {}, uuid4(), 1
    async def interrupted_done(db, device, effect, status):
        assert device is row and effect is interrupted and status == "uncertain"
        calls.append("bulk-interrupted")
    async def chatbot_reap(db, device): calls.append("chatbot-reap")
    async def outbound_admit(db, device): calls.append("outbound-admit")
    async def wrong_handler(*_): pytest.fail("Scheduled work reached the chatbot handler")
    monkeypatch.setattr(devices, "_private_transaction", transaction)
    monkeypatch.setattr(bulk, "after_bulk_effect_finish", interrupted_done)
    monkeypatch.setattr(devices, "admit_outbound_work", outbound_admit)
    monkeypatch.setattr(chatbot, "reap_unreachable", chatbot_reap)
    monkeypatch.setattr(chatbot, "after_effect_finish", wrong_handler)
    value = await devices.pending_effects(uuid4(), object())
    assert calls == ["bulk-interrupted", "chatbot-reap", "outbound-admit"]
    assert value["effects"][0]["type"] == "bulk_action" and value["reconcile"] == []
    assert "payload" not in value["effects"][0] and "phone" not in value["effects"][0]


@pytest.mark.asyncio
async def test_bulk_authorization_always_uses_its_frozen_audience_authority(monkeypatch):
    from contextlib import asynccontextmanager
    from app import subscription_devices as devices, subscription_bulk_runtime as bulk
    from app.subscription_device_schemas import EffectRequest
    effect = bulk_effect("pending")
    row, owner, fence, db = {"id": effect["device_id"]}, uuid4(), 7, object()
    @asynccontextmanager
    async def transaction(_): yield db, row, {}, owner, fence
    async def load(*_): return effect
    async def authorize(database, device, candidate, received_owner, received_fence, payload):
        assert database is db and device is row and candidate is effect
        assert received_owner == owner and received_fence == fence and payload.operation_id == effect["id"]
        return {"authorized": False, "error_code": "BULK_OPTED_OUT"}
    async def generic(*_): pytest.fail("Generic device policy cannot authorize a frozen bulk")
    monkeypatch.setattr(devices, "_private_transaction", transaction)
    monkeypatch.setattr(devices, "_effect", load)
    monkeypatch.setattr(devices, "_policy_still_allows", generic)
    monkeypatch.setattr(bulk, "authorize_bulk_effect", authorize)
    result = await devices.authorize_effect(uuid4(), EffectRequest(operation_id=effect["id"]), object())
    assert result == {"authorized": False, "error_code": "BULK_OPTED_OUT"}


@pytest.mark.asyncio
@pytest.mark.parametrize("turns", [
    ["bulk", "campaign", "scheduled"],
    ["scheduled", "bulk", "campaign"],
    ["campaign", "scheduled", "bulk"],
])
async def test_poll_reconciles_all_modules_before_honoring_database_fair_turns(monkeypatch, turns):
    from app import subscription_devices as devices, subscription_campaigns as campaigns
    from app import subscription_schedule_runtime as scheduled, subscription_bulk_runtime as bulk
    events = []
    row = {"tenant_id": uuid4(), "id": uuid4()}

    class Result:
        def scalars(self): return self
        def all(self): return turns

    class DB:
        async def execute(self, query, values):
            assert events == ["reaped"]
            assert values == {"t": row["tenant_id"]}
            events.append("turns")
            return Result()

    db = DB()

    async def reap(database, tenant):
        assert database is db and tenant == row["tenant_id"]
        events.append("reaped")

    def admit(name):
        async def run(database, candidate):
            assert database is db and candidate is row
            events.append(name)
        return run

    monkeypatch.setattr(bulk, "reap_outbound_work", reap)
    monkeypatch.setattr(bulk, "admit_bulk_messages", admit("bulk"))
    monkeypatch.setattr(campaigns, "admit_due_campaigns", admit("campaign"))
    monkeypatch.setattr(scheduled, "admit_due_schedules", admit("scheduled"))
    await devices.admit_outbound_work(db, row)
    assert events == ["reaped", "turns", *turns]


@pytest.mark.asyncio
async def test_failed_reconciliation_cannot_start_a_different_outbound_module(monkeypatch):
    from app import subscription_devices as devices, subscription_bulk_runtime as bulk

    async def failed(*_):
        raise RuntimeError("synthetic reconciliation unavailable")

    class DB:
        async def execute(self, *_):
            pytest.fail("No admission query may precede complete reconciliation")

    monkeypatch.setattr(bulk, "reap_outbound_work", failed)
    with pytest.raises(RuntimeError, match="synthetic reconciliation unavailable"):
        await devices.admit_outbound_work(DB(), {"tenant_id": uuid4()})
