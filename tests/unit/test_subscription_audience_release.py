"""Publication policy: no migration without a matching DB and verified backup."""
from contextlib import contextmanager
import subprocess

import pytest
from sqlalchemy.engine import make_url

from scripts import backup_and_migrate_subscription_audience as release


@contextmanager
def locked(_):
    yield


def prepare(monkeypatch, *, version=None, after_backup=None, scope=True,
            predecessor=True, postcondition=True, backup_fails=False):
    raw = "postgresql+psycopg://fixture:synthetic@127.0.0.1:5542/synthetic"
    runtime = "postgresql+asyncpg://runtime@127.0.0.1:5542/" + ("synthetic" if scope else "other")
    monkeypatch.setenv("APP_DATABASE_URL", runtime)
    monkeypatch.setattr(release, "connection_values", lambda: (raw, make_url(raw)))
    monkeypatch.setattr(release, "release_lock", locked)
    monkeypatch.setattr(release, "verify_key_provisioning", lambda _: None)
    monkeypatch.setattr(release, "preserved_contract", lambda raw: predecessor)
    monkeypatch.setattr(release, "schema_ready", lambda _: postcondition)
    monkeypatch.setattr(release, "storage_values", lambda: {})
    values = iter([version or release.PREVIOUS, after_backup or release.PREVIOUS])
    monkeypatch.setattr(release, "migrated_version", lambda _: next(values))
    steps = []

    def backup(url, storage, path, **kwargs):
        assert kwargs == {"backup_context": "subscription-audience-0092",
                          "schema_revision": release.PREVIOUS, "preserve_ownership": True}
        assert steps == []
        steps.append("private_backup")
        if backup_fails:
            raise RuntimeError("synthetic secret that must not be printed")

    def migrate(command, **kwargs):
        assert steps == ["private_backup"]
        assert command[-3:] == ["alembic", "upgrade", release.TARGET]
        assert kwargs["env"]["ORION_MIGRATION_SWITCH_ROLE"] == "1"
        assert kwargs["timeout"] == 600 and kwargs["check"] is True
        assert kwargs["stdout"] == kwargs["stderr"] == subprocess.DEVNULL
        steps.append("migration")

    monkeypatch.setattr(release, "perform_backup", backup)
    monkeypatch.setattr(release.subprocess, "run", migrate)
    return steps


def test_verified_backup_precedes_additive_migration(monkeypatch):
    steps = prepare(monkeypatch)
    assert release.main() == 0
    assert steps == ["private_backup", "migration"]


def test_current_schema_only_validates_without_backup_or_ddl(monkeypatch):
    steps = prepare(monkeypatch, version=release.TARGET)
    assert release.main() == 0 and steps == []


@pytest.mark.parametrize("options", [
    {"scope": False}, {"version": "0089_unknown"}, {"version": "0086_dental_laboratories"},
    {"predecessor": False},
])
def test_unreviewed_context_does_not_attempt_backup_or_ddl(monkeypatch, options):
    steps = prepare(monkeypatch, **options)
    assert release.main() == 3 and steps == []


@pytest.mark.parametrize("options", [
    {"backup_fails": True}, {"after_backup": "0089_changed"},
])
def test_backup_failure_or_changed_source_never_migrates(monkeypatch, options, capsys):
    steps = prepare(monkeypatch, **options)
    assert release.main() == 3 and steps == ["private_backup"]
    captured = capsys.readouterr()
    assert "synthetic secret" not in captured.out + captured.err


def test_postcondition_failure_rejects_release(monkeypatch):
    steps = prepare(monkeypatch, postcondition=False)
    assert release.main() == 3 and steps == ["private_backup", "migration"]


def test_current_revision_still_requires_a_valid_contract(monkeypatch):
    steps = prepare(monkeypatch, version=release.TARGET, postcondition=False)
    assert release.main() == 3 and steps == []


@pytest.mark.parametrize("key_valid", [True, False])
def test_preexisting_encryption_key_check_is_preserved(monkeypatch, key_valid):
    steps = prepare(monkeypatch, version=release.TARGET)
    def check(_):
        if not key_valid:
            raise RuntimeError("dental_original_key_required")
    monkeypatch.setattr(release, "verify_key_provisioning", check)
    assert release.main() == (0 if key_valid else 3) and steps == []
