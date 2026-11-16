"""The duration override migration must never run without verified backup."""
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

from scripts import backup_and_migrate_combo_duration as release


def context(monkeypatch, current):
    state = {"version": current, "calls": []}
    monkeypatch.setattr(release, "connection_values", lambda: ("synthetic-url", object()))
    monkeypatch.setattr(release, "release_lock", lambda raw: nullcontext())
    monkeypatch.setattr(release, "migrated_version", lambda raw: state["version"])
    monkeypatch.setattr(release, "short_links_ready", lambda raw: True)
    monkeypatch.setattr(release, "schema_ready", lambda raw: True)
    monkeypatch.setattr(release, "storage_values", lambda: state["calls"].append("storage") or {"bucket":"synthetic"})
    def backup(url, settings, path: Path, **kw):
        assert path.name == "backup.dump"
        assert kw == {"backup_context":"combo-duration-0055",
                      "schema_revision":release.PREVIOUS, "preserve_ownership":True}
        state["calls"].append("backup")
    monkeypatch.setattr(release, "perform_backup", backup)
    def migrate(*args, **kwargs):
        assert args[0][-3:] == ["alembic", "upgrade", release.TARGET]
        assert kwargs["env"]["ORION_MIGRATION_SWITCH_ROLE"] == "1"
        state["calls"].append("migrate")
        state["version"] = release.TARGET
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(release.subprocess, "run", migrate)
    return state


def test_unreviewed_schema_cannot_trigger_migration(monkeypatch):
    state = context(monkeypatch, "0053_central_notifications")
    assert release.main() == 2
    assert state["calls"] == []


def test_backup_failure_aborts_without_schema_changes(monkeypatch):
    state = context(monkeypatch, release.PREVIOUS)
    def failing_backup(*args, **kwargs):
        state["calls"].append("backup")
        raise OSError("synthetic failure, never leak to logs")
    monkeypatch.setattr(release, "perform_backup", failing_backup)
    assert release.main() == 3
    assert state["calls"] == ["storage", "backup"]


def test_backup_verified_before_single_schema_migration(monkeypatch):
    state = context(monkeypatch, release.PREVIOUS)
    assert release.main() == 0
    assert state["calls"] == ["storage","backup","migrate"]
    assert release.main() == 0
    assert state["calls"] == ["storage","backup","migrate"]


def test_invalid_schema_verification_fails_closed(monkeypatch):
    state = context(monkeypatch, release.TARGET)
    monkeypatch.setattr(release, "schema_ready", lambda raw: False)
    assert release.main() == 3
    assert state["calls"] == []
