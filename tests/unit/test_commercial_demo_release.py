"""A demo rollout may migrate only after a verified backup and known schema."""
from contextlib import contextmanager

import pytest
from scripts import backup_and_migrate_commercial_demos as release
from scripts import predeploy_one_click_links as entry


@contextmanager
def unlocked(_):
    yield


def prepare(monkeypatch, versions, *, predecessor=True, postcondition=True, backup_ok=True):
    events = []
    revisions = iter(versions)
    monkeypatch.setattr(release, "connection_values", lambda: ("synthetic", object()))
    monkeypatch.setattr(release, "migrated_version", lambda _: next(revisions))
    monkeypatch.setattr(release, "release_lock", unlocked)
    monkeypatch.setattr(release, "storage_values", lambda: {})
    monkeypatch.setattr(release.previous, "schema_ready", lambda _: predecessor)
    monkeypatch.setattr(release, "schema_ready", lambda _: postcondition)

    def backup(*args, **kwargs):
        assert kwargs == {"backup_context": "commercial-demos-0077",
                          "schema_revision": "0076_dental_clinic", "preserve_ownership": True}
        if not backup_ok:
            raise RuntimeError("synthetic sensitive provider details")
        events.append("backup_verified")

    def migrate(command, **kwargs):
        assert events == ["backup_verified"]
        assert command[-3:] == ["alembic", "upgrade", "0077_commercial_demos"]
        assert kwargs["check"] and kwargs["timeout"] == 600
        assert kwargs["env"]["ORION_MIGRATION_SWITCH_ROLE"] == "1"
        events.append("migration")

    monkeypatch.setattr(release, "perform_backup", backup)
    monkeypatch.setattr(release.subprocess, "run", migrate)
    return events


def test_backup_precedes_the_additive_migration(monkeypatch):
    events = prepare(monkeypatch, [release.PREVIOUS, release.PREVIOUS])
    assert release.main() == 0
    assert events == ["backup_verified", "migration"]


def test_published_revision_is_only_verified(monkeypatch):
    events = prepare(monkeypatch, [release.TARGET])
    assert release.main() == 0 and events == []


@pytest.mark.parametrize("head", ["0075_fixed_client_monthly", "0078_unknown", ""])
def test_unreviewed_head_never_reaches_backup_or_ddl(monkeypatch, head):
    events = prepare(monkeypatch, [head])
    assert release.main() == 3 and events == []


def test_broken_predecessor_prevents_backup(monkeypatch):
    events = prepare(monkeypatch, [release.PREVIOUS], predecessor=False)
    assert release.main() == 3 and events == []


def test_failed_backup_blocks_migration_and_redacts_error(monkeypatch, capsys):
    events = prepare(monkeypatch, [release.PREVIOUS], backup_ok=False)
    assert release.main() == 3 and events == []
    captured = capsys.readouterr()
    assert "sensitive" not in captured.out + captured.err


def test_concurrent_revision_change_prevents_ddl(monkeypatch):
    events = prepare(monkeypatch, [release.PREVIOUS, "0078_unknown"])
    assert release.main() == 3 and events == ["backup_verified"]


def test_incorrect_result_blocks_the_rollout(monkeypatch):
    events = prepare(monkeypatch, [release.PREVIOUS, release.PREVIOUS], postcondition=False)
    assert release.main() == 3 and events == ["backup_verified", "migration"]


def test_legacy_release_cannot_succeed_without_dental_predecessor(monkeypatch):
    from scripts import apply_variable_combo_migration as revisions
    monkeypatch.setattr(revisions, "migrated_version", lambda _: "0075_fixed_client_monthly")
    monkeypatch.setattr(release, "main", lambda: pytest.fail("Cannot migrate an older schema"))
    with pytest.raises(RuntimeError, match="predecessor_not_published"):
        entry.commercial_demo_release("synthetic")


def test_demo_runtime_barrier_retains_all_clinical_and_monthly_checks():
    from scripts import wait_for_commercial_demos_schema as probe
    from scripts import wait_for_dental_schema as previous
    assert previous.CONTRACT_QUERY in probe.CONTRACT_QUERY
    assert "relforcerowsecurity" in probe.DEMO_CONTRACT
    assert "read_commercial_demo(text)" in probe.DEMO_CONTRACT
