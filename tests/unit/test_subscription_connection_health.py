"""Transport readiness survives the API boundary without leaking diagnostics."""
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.subscription_devices import _safe_connection_health, _safe_runtime
from app import subscription_devices as devices


def test_degraded_transport_overrides_old_open_event_without_exposing_identifiers():
    value = _safe_runtime({"status": "connected", "connection_health": {
        "state": "degraded", "last_activity_at": 1793898000000,
        "transport_observed": True, "interrupted_at": 1793898100000,
        "disconnect_code": 408, "reason": "network_timeout", "action": "retry_connection",
        "reconnect_attempts": 2, "owner": "PRIVATE-OWNER", "tenant_id": "PRIVATE-TENANT",
        "instance_id": "PRIVATE-INSTANCE", "error": "PRIVATE-PROVIDER-ERROR",
    }})
    assert value["status"] == "degraded"
    health = value["connection_health"]
    assert health["last_activity_at"] == datetime.fromtimestamp(1793898000, timezone.utc).isoformat()
    assert health["disconnect_code"] == 408
    assert health["reconnect_attempts"] == 2
    assert "PRIVATE" not in json.dumps(value)


@pytest.mark.parametrize("state, expected", [("reconnecting", "reconnecting"),
    ("needs_attention", "needs_attention"), ("stopped", "disconnected"), ("connected", "connected")])
def test_readiness_policy(state, expected):
    assert _safe_runtime({"status": "connected", "connection_health": {"state": state}})["status"] == expected


def test_runtime_remains_compatible_without_new_health_fields():
    assert "connection_health" not in _safe_runtime({"status": "connected"})
    assert _safe_runtime({"status": "connected"})["status"] == "connected"


def test_incoming_backlog_is_visible_without_journal_content_or_a_false_connected_state():
    value = _safe_runtime({"status": "connected", "connection_health": {
        "state": "needs_attention", "action": "review_incoming_messages", "reason": "ingress_review_required",
        "ingress_pending": 2, "ingress_blocked": 1, "ingress_oldest_at": 1793898000000,
        "ingress_storage_unavailable": False, "journal": {"body": "PRIVATE-MESSAGE"},
        "error_code": "PRIVATE-FILESYSTEM-ERROR", "phone": "PRIVATE-PHONE"}})
    assert value["status"] == "needs_attention"
    health = value["connection_health"]
    assert health["ingress_pending"] == 2 and health["ingress_blocked"] == 1
    assert health["action"] == "review_incoming_messages"
    assert health["ingress_oldest_at"] == datetime.fromtimestamp(1793898000, timezone.utc).isoformat()
    assert "PRIVATE" not in json.dumps(value)
    bad = _safe_connection_health({"state": "degraded", "ingress_pending": True,
        "ingress_blocked": 10**100, "ingress_storage_unavailable": "true"})
    assert bad["ingress_pending"] == bad["ingress_blocked"] == 0
    assert bad["ingress_storage_unavailable"] is False


@pytest.mark.parametrize("health", [None, [], "connected", {"state": []}, {"state": "invented"}])
def test_invalid_health_cannot_manufacture_connected_state(health):
    assert _safe_connection_health(health) is None


def test_untrusted_health_fields_are_bounded_and_timestamp_offsets_normalized():
    health = _safe_connection_health({"state": "needs_attention", "action": ["reauthenticate"],
        "reason": {"logged_out": True}, "disconnect_code": True, "reconnect_attempts": 10**100,
        "last_activity_at": True, "connected_at": 10**100, "interrupted_at": "2026-11-05T12:00:00-04:00",
        "recovered_at": "2026-11-05T12:00:00", "reconnect_at": float("inf")})
    assert health["action"] is None and health["reason"] is None
    assert health["disconnect_code"] is None and health["reconnect_attempts"] == 0
    assert health["interrupted_at"] == "2026-11-05T16:00:00+00:00"
    assert all(health[key] is None for key in ("last_activity_at", "connected_at", "recovered_at", "reconnect_at"))


@pytest.mark.asyncio
@pytest.mark.parametrize("age, expected", [(10, "connected"), (100, "unavailable")])
async def test_stored_open_event_expires_when_the_process_stops_reporting(monkeypatch, age, expected):
    class Result:
        def mappings(self): return self
        def one_or_none(self): return None
    class Database:
        async def execute(self, *_args): return Result()
    async def metrics(*_args): return {"available": False}
    monkeypatch.setattr(devices, "_metrics", metrics)
    row = {"id": uuid4(), "tenant_id": uuid4(), "name": "Synthetic device", "revision": 1,
        "status": "connected", "runtime": {"status": "connected", "connection_health": {"state": "connected"}},
        "runtime_at": datetime.now(timezone.utc) - timedelta(seconds=age),
        "settings": {"transcription": {"enabled": False}, "calls": {"reject_enabled": False}}}
    result = await devices.device_dto(Database(), row)
    assert result["status"] == expected
    if expected == "unavailable":
        assert result["connection_health"]["stale"] is True
        assert result["connection_health"]["state"] == "degraded"
