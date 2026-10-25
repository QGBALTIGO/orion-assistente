"""Safety regression tests for activation, opt-in and immutable identity."""
from datetime import datetime, timezone

import pytest

from app.auto_completion import activation_time, auto_completion_enabled, process_due_appointment, scan_due_appointments


class FakeRedis:
    def __init__(self):
        self.values = {}

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def get(self, key):
        return self.values.get(key)


@pytest.mark.asyncio
async def test_activation_is_utc_and_not_reset_on_repeat():
    redis = FakeRedis()
    first = await activation_time(redis)
    second = await activation_time(redis)
    assert first == second
    assert first.tzinfo == timezone.utc
    assert first <= datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_feature_disabled_never_accesses_database_or_queue(monkeypatch):
    monkeypatch.delenv("ORION_AUTO_COMPLETE_APPOINTMENTS", raising=False)
    assert not auto_completion_enabled()
    assert await scan_due_appointments({"redis": object()}) == 0
    assert await process_due_appointment({"redis": object()}, "unused", "unused") == "disabled"
