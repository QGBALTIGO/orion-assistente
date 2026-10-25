"""Safe appointment end-time automation rules: pure policy and ID contracts."""
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest

from app.appointment_auto_completion import (
    auto_completion_key, policy_enabled, receivable_id,
)
from app.management_preferences import AutoCompletionPolicy, AutoCompletionSave


TENANT = UUID("10000000-0000-4000-8000-000000000001")
APPOINTMENT = UUID("20000000-0000-4000-8000-000000000002")
NOW = datetime(2030, 4, 12, 14, 40, tzinfo=timezone.utc)
END = NOW


@pytest.mark.parametrize("config", [
    None,
    {},
    {"enabled": False, "enabled_at": (NOW-timedelta(hours=1)).isoformat()},
    {"enabled": True},
    {"enabled": True, "enabled_at": "invalid"},
    {"enabled": True, "enabled_at": NOW.replace(tzinfo=None).isoformat()},
    {"enabled": "true", "enabled_at": (NOW-timedelta(hours=1)).isoformat()},
])
def test_disabled_or_invalid_policy_cannot_auto_complete(config):
    assert not policy_enabled(config, END, NOW)


def test_only_services_ending_after_explicit_activation_are_eligible():
    activated = NOW - timedelta(hours=2)
    policy = {"enabled": True, "enabled_at": activated.isoformat()}
    assert policy_enabled(policy, END, NOW)
    assert not policy_enabled(policy, activated-timedelta(minutes=1), NOW)
    assert not policy_enabled(policy, NOW+timedelta(seconds=1), NOW)
    assert policy_enabled(policy, NOW, NOW)


def test_deterministic_scoped_receivable_and_transition_identifiers():
    same = auto_completion_key(TENANT, APPOINTMENT)
    assert len(same) == 36
    assert auto_completion_key(TENANT, APPOINTMENT) == same
    assert auto_completion_key(UUID(int=9), APPOINTMENT) != same
    assert receivable_id(TENANT, APPOINTMENT) == receivable_id(TENANT, APPOINTMENT)
    assert receivable_id(TENANT, APPOINTMENT) != receivable_id(UUID(int=9), APPOINTMENT)


def test_pydantic_cannot_enable_without_aware_activation_or_string_toggle():
    with pytest.raises(ValueError):
        AutoCompletionPolicy(enabled=True)
    with pytest.raises(ValueError):
        AutoCompletionPolicy(enabled=False, enabled_at=NOW)
    with pytest.raises(ValueError):
        AutoCompletionSave(expected_version=0, enabled="true")
    assert AutoCompletionPolicy(enabled=True, enabled_at=NOW).enabled
    assert not AutoCompletionPolicy().enabled


@pytest.mark.asyncio
async def test_paused_worker_broadcasts_safe_google_heartbeat_without_processing(monkeypatch):
    from app.calendar_worker import scan_calendar_outbox

    class Redis:
        def __init__(self):
            self.writes = []
        async def set(self, *args, **kwargs):
            self.writes.append((args, kwargs))

    redis = Redis()
    monkeypatch.setenv("GOOGLE_LIVE_SYNC", "0")
    assert await scan_calendar_outbox({"redis": redis}) == 0
    assert redis.writes == [(("orion:google:worker-health", "paused"), {"ex": 780})]
