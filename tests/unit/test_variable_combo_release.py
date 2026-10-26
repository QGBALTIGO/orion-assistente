"""Guarded 0034 combo price-mode release only migrates after verified backup."""
from pathlib import Path

from scripts import backup_and_migrate_combo_pricing as release


def test_correct_boundary_and_no_writes_when_origin_unexpected(monkeypatch):
    assert release.PREVIOUS == "0033_professional_commissions"
    assert release.TARGET == "0034_variable_combo_pricing"
    monkeypatch.setattr(release, "connection_values", lambda: ("unused", object()))
    monkeypatch.setattr(release, "migrated_version", lambda _: "0032_variable_service_prices")
    attempts = []
    monkeypatch.setattr(release, "storage_values", lambda: attempts.append("storage"))
    monkeypatch.setattr(release, "migrate", lambda: attempts.append("migrate"))
    assert release.main() == 2
    assert attempts == []


def test_backup_required_before_alembic_migration(monkeypatch):
    monkeypatch.setattr(release, "connection_values", lambda: ("unused", object()))
    monkeypatch.setattr(release, "migrated_version", lambda _: release.PREVIOUS)
    monkeypatch.setattr(release, "storage_values", lambda: {"bucket": "test-only"})
    calls = []
    def fail_backup(*args, **kwargs):
        calls.append("backup")
        raise RuntimeError("synthetic backup failed")
    monkeypatch.setattr(release, "perform_backup", fail_backup)
    monkeypatch.setattr(release, "migrate", lambda: calls.append("migrate"))
    assert release.main() == 3
    assert calls == ["backup"]


def test_verified_backup_receives_scope_and_runs_migration_once(monkeypatch):
    version = [release.PREVIOUS]
    monkeypatch.setattr(release, "connection_values", lambda: ("unused", object()))
    monkeypatch.setattr(release, "migrated_version", lambda _: version[0])
    monkeypatch.setattr(release, "storage_values", lambda: {"bucket": "test-only"})
    calls = []
    def backup(_url, _settings, destination: Path, **kwargs):
        assert destination.name == "backup.dump"
        assert kwargs == {
            "backup_context": "variable-combo-0034",
            "schema_revision": release.PREVIOUS,
        }
        calls.append("backup")
    def migrate():
        calls.append("migrate")
        version[0] = release.TARGET
        return 0
    monkeypatch.setattr(release, "perform_backup", backup)
    monkeypatch.setattr(release, "migrate", migrate)
    assert release.main() == 0
    assert calls == ["backup", "migrate"]
    assert release.main() == 0
    assert calls == ["backup", "migrate"]


