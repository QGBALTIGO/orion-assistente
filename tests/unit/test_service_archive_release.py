"""A service archive must be backed up and must retain its predecessor contract."""
from contextlib import contextmanager

from scripts import backup_and_migrate_service_archive as release


@contextmanager
def unlocked(_):
    yield


def test_backup_precedes_service_archive_schema(monkeypatch):
    events = []
    versions = iter([release.PREVIOUS, release.PREVIOUS, release.TARGET])
    monkeypatch.setattr(release, "connection_values", lambda: ("synthetic", object()))
    monkeypatch.setattr(release, "migrated_version", lambda _: next(versions))
    monkeypatch.setattr(release, "release_lock", unlocked)
    monkeypatch.setattr(release, "storage_values", lambda: {})
    monkeypatch.setattr(release.previous, "schema_ready", lambda _: True)
    monkeypatch.setattr(release, "contract_ready", lambda _: True)

    def backup(*args, **kwargs):
        assert kwargs == {"backup_context": "service-archive-0079",
            "schema_revision": release.PREVIOUS, "preserve_ownership": True}
        events.append("backup")

    def migrate(command, **kwargs):
        assert events == ["backup"]
        assert command[-3:] == ["alembic", "upgrade", release.TARGET]
        assert kwargs["check"] and kwargs["env"]["ORION_MIGRATION_SWITCH_ROLE"] == "1"
        events.append("migrate")

    monkeypatch.setattr(release, "perform_backup", backup)
    monkeypatch.setattr(release.subprocess, "run", migrate)
    assert release.main() == 0
    assert events == ["backup", "migrate"]


def test_known_target_only_checks(monkeypatch):
    monkeypatch.setattr(release, "connection_values", lambda: ("synthetic", object()))
    monkeypatch.setattr(release, "migrated_version", lambda _: release.TARGET)
    monkeypatch.setattr(release, "release_lock", unlocked)
    monkeypatch.setattr(release, "contract_ready", lambda _: True)
    monkeypatch.setattr(release, "perform_backup", lambda *a, **k: (_ for _ in ()).throw(AssertionError()))
    assert release.main() == 0


def test_unknown_revision_fails_closed(monkeypatch):
    monkeypatch.setattr(release, "connection_values", lambda: ("synthetic", object()))
    monkeypatch.setattr(release, "migrated_version", lambda _: "0080_unknown")
    monkeypatch.setattr(release, "release_lock", unlocked)
    monkeypatch.setattr(release, "perform_backup", lambda *a, **k: (_ for _ in ()).throw(AssertionError()))
    assert release.main() == 3


def test_schema_contract_keeps_history_barriers():
    from scripts.wait_for_service_archive_schema import CONTRACT_QUERY
    from scripts.wait_for_customer_legacy_history_schema import CONTRACT_QUERY as previous

    assert previous in CONTRACT_QUERY
    assert "ck_service_archive_inactive" in CONTRACT_QUERY
