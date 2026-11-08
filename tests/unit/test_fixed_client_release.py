"""Safety gates for a real 0073 -> 0074 fixed-client release.

No production DB or backup is used: credentials and archive are fakes.
"""
from contextlib import contextmanager

import pytest

from scripts import backup_and_migrate_fixed_client_series as release
from scripts import wait_for_fixed_client_schema as wait


@contextmanager
def unlocked(_):
    yield


def prepared(monkeypatch, revisions, *, previous=True, target=True):
    events = []
    versions = iter(revisions)
    monkeypatch.setattr(release, "connection_values", lambda: ("synthetic-dsn", object()))
    monkeypatch.setattr(release, "migrated_version", lambda _: next(versions))
    monkeypatch.setattr(release, "release_lock", unlocked)
    monkeypatch.setattr(release, "storage_values", lambda: {"dummy": True})
    monkeypatch.setattr(release.predecessor, "schema_ready", lambda _: previous)
    monkeypatch.setattr(release, "schema_ready", lambda _: target)

    def backup(_url, _settings, _file, **args):
        assert args == {"backup_context": "fixed-client-0074",
                        "schema_revision": release.PREVIOUS, "preserve_ownership": True}
        events.append("verified-private-backup")

    def migrate(command, **args):
        assert command == [release.sys.executable, "-m", "alembic", "upgrade", release.TARGET]
        assert args["check"] is True and args["timeout"] == 600
        assert args["stdout"] == args["stderr"] == release.subprocess.DEVNULL
        assert args["env"]["ORION_MIGRATION_SWITCH_ROLE"] == "1"
        events.append("alembic-upgrade")

    monkeypatch.setattr(release, "perform_backup", backup)
    monkeypatch.setattr(release.subprocess, "run", migrate)
    return events


def test_release_extends_published_head_only_with_verified_backup(monkeypatch):
    events = prepared(monkeypatch, [release.PREVIOUS, release.PREVIOUS,
                                  release.PREVIOUS, release.TARGET])
    assert release.main() == 0
    assert events == ["verified-private-backup", "alembic-upgrade"]


def test_repeat_predeploy_does_not_backup_or_migrate_again(monkeypatch):
    events = prepared(monkeypatch, [release.TARGET])
    assert release.main() == 0
    assert events == []


@pytest.mark.parametrize("revision", ["0072_subscription_group_add", "0074_unknown", "0052_paraguay_phone"])
def test_unreviewed_head_fails_before_private_archive(monkeypatch, revision):
    events = prepared(monkeypatch, [revision])
    assert release.main() == 3 and not events


def test_backup_failure_blocks_ddl_and_redacts_storage_error(monkeypatch, capsys):
    events = prepared(monkeypatch, [release.PREVIOUS, release.PREVIOUS])

    def fail(*_args, **_kwargs):
        raise RuntimeError("private backup credentials and contents")

    monkeypatch.setattr(release, "perform_backup", fail)
    assert release.main() == 3 and events == []
    output = capsys.readouterr()
    assert "private backup credentials" not in output.err + output.out


def test_predecessor_contract_blocks_mutation(monkeypatch):
    events = prepared(monkeypatch, [release.PREVIOUS, release.PREVIOUS], previous=False)
    assert release.main() == 3 and events == []


def test_ddl_does_not_override_failed_postcondition(monkeypatch):
    events = prepared(monkeypatch, [release.PREVIOUS, release.PREVIOUS,
                                  release.PREVIOUS, release.TARGET], target=False)
    assert release.main() == 3
    assert events == ["verified-private-backup", "alembic-upgrade"]


def test_runtime_schema_barrier_is_read_only_and_includes_previous_contract():
    assert wait.SCHEMA_MARKER == release.TARGET
    assert "predecessor.CONTRACT_QUERY" in wait.CONTRACT_QUERY or wait.predecessor.CONTRACT_QUERY in wait.CONTRACT_QUERY
    assert "relforcerowsecurity" in wait.ROLE_CONTRACT
    assert "app_runtime" in wait.ROLE_CONTRACT
    assert "app_support" in wait.ROLE_CONTRACT
    assert "appointment_series" in wait.CONSTRAINT_CONTRACT
    assert "alembic_version" in wait.REVISION_CONTRACT
    assert "default_transaction_read_only" in wait.probe_schema.__code__.co_consts.__str__()
