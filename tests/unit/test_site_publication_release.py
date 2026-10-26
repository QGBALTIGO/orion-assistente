"""Release authorization scope and verified private backup precede all DDL."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import backup_and_migrate_site_publication as release
from scripts import backup_and_migrate_commissions as common


def configure(monkeypatch, *, current=None, backup_error=False, migration_result=0, target_after=True, contract=True):
    version = [current or release.PREVIOUS]
    events = []
    monkeypatch.setattr(release, "connection_values", lambda: ("synthetic-private-connection", object()))
    monkeypatch.setattr(release, "migrated_version", lambda _raw: version[0])
    monkeypatch.setattr(release, "storage_values", lambda: {"bucket": "synthetic-private"})
    def backup(_url, storage, destination, **kwargs):
        events.append("backup")
        assert storage == {"bucket": "synthetic-private"}
        assert isinstance(destination, Path) and destination.name == "backup.dump"
        assert kwargs == {"backup_context": "site-publication-0036", "schema_revision": release.PREVIOUS}
        if backup_error:
            raise RuntimeError("synthetic-secret-must-not-appear-in-logs")
    def migration(args, **kwargs):
        events.append("migration")
        assert args == [release.sys.executable, "-m", "alembic", "upgrade", release.TARGET]
        assert kwargs["env"]["ORION_MIGRATION_SWITCH_ROLE"] == "1"
        assert kwargs["stdout"] == kwargs["stderr"] == release.subprocess.DEVNULL
        assert kwargs["check"] is False and kwargs["timeout"] == 600
        if target_after:
            version[0] = release.TARGET
        return SimpleNamespace(returncode=migration_result)
    def schema(_raw):
        events.append("contract")
        return contract
    monkeypatch.setattr(release, "perform_backup", backup)
    monkeypatch.setattr(release.subprocess, "run", migration)
    monkeypatch.setattr(release, "schema_contract_ready", schema)
    return version, events


def test_reviewed_scope_and_shared_backup_guards_are_not_reimplemented():
    assert release.PREVIOUS == "0035_public_amenities" and release.TARGET == "0036_site_publication"
    assert release.connection_values is common.connection_values
    assert release.storage_values is common.storage_values
    assert release.perform_backup is common.perform_backup


def test_unreviewed_revision_stops_before_storage_backup_or_migration(monkeypatch):
    _, events = configure(monkeypatch, current="0034_variable_combo_pricing")
    monkeypatch.setattr(release, "storage_values", lambda: events.append("storage"))
    assert release.main() == 2 and events == []


def test_verified_backup_precedes_exact_one_revision_and_target_recheck(monkeypatch):
    _, events = configure(monkeypatch)
    assert release.main() == 0
    assert events == ["backup", "migration", "contract"]
    assert release.main() == 0
    assert events == ["backup", "migration", "contract", "contract"]


def test_backup_failure_never_invokes_migration_and_logs_no_exception(monkeypatch, capsys):
    _, events = configure(monkeypatch, backup_error=True)
    assert release.main() == 3 and events == ["backup"]
    output = capsys.readouterr()
    assert "synthetic-secret" not in output.out + output.err
    assert "predeploy_failed_closed" in output.err


@pytest.mark.parametrize("result,target_after", [(1, True), (0, False)])
def test_migration_failure_or_unconfirmed_revision_cannot_report_success(monkeypatch, result, target_after):
    _, events = configure(monkeypatch, migration_result=result, target_after=target_after)
    assert release.main() == 3
    assert events == ["backup", "migration"]


@pytest.mark.parametrize("current", [release.PREVIOUS, release.TARGET])
def test_missing_schema_guard_rejects_revision_only_success(monkeypatch, current):
    _, events = configure(monkeypatch, current=current, contract=False)
    assert release.main() == 3
    assert events == (["backup", "migration", "contract"] if current == release.PREVIOUS else ["contract"])


def test_missing_reviewed_storage_aborts_before_backup_and_ddl(monkeypatch):
    _, events = configure(monkeypatch)
    def missing(): raise ValueError("synthetic-private-bucket-missing")
    monkeypatch.setattr(release, "storage_values", missing)
    assert release.main() == 3 and events == []
