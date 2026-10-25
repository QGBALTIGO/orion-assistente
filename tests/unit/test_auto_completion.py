"""Auto-completion is opt-in and cannot scan when disabled."""
import pytest

from app.auto_completion_worker import enabled, scan_due_auto_completions, complete_due_appointment


@pytest.mark.asyncio
async def test_feature_disabled_never_accesses_database_or_queue(monkeypatch):
    monkeypatch.delenv("ORION_AUTO_COMPLETE_APPOINTMENTS", raising=False)
    assert not enabled()
    assert await scan_due_auto_completions({"redis": object()}) == 0
    assert await complete_due_appointment({"redis": object()}, "unused", "unused") == "disabled"


def test_feature_enabled_requires_exact_flag(monkeypatch):
    for value in ("", "true", "yes", "0"):
        monkeypatch.setenv("ORION_AUTO_COMPLETE_APPOINTMENTS", value)
        assert not enabled()
    monkeypatch.setenv("ORION_AUTO_COMPLETE_APPOINTMENTS", "1")
    assert enabled()
