"""0076 clinical release gate: backup -> reviewed migration -> verified RLS.

This suite uses only fakes; no production database or patient information.
"""
from contextlib import contextmanager
import subprocess

import pytest
from scripts import backup_and_migrate_dental_clinic as release


@contextmanager
def unlocked(_):
    yield


def prepare(monkeypatch, versions, *, predecessor=True, postcondition=True):
    steps=[]
    values=iter(versions)
    monkeypatch.setattr(release, "connection_values", lambda: ("synthetic-conn", object()))
    monkeypatch.setattr(release, "migrated_version", lambda raw: next(values))
    monkeypatch.setattr(release, "release_lock", unlocked)
    monkeypatch.setattr(release, "storage_values", lambda: {"synthetic":True})
    monkeypatch.setattr(release.previous, "schema_ready", lambda _: predecessor)
    monkeypatch.setattr(release, "schema_ready", lambda _: postcondition)

    def verify_backup(url, settings, filename, **kwargs):
        assert kwargs=={"backup_context":"dental-clinic-0076",
                         "schema_revision":release.PREVIOUS,
                         "preserve_ownership":True}
        assert steps==[]
        steps.append("verified_private_backup")

    def migrate(command, **kwargs):
        assert steps==["verified_private_backup"]
        assert command[-3:]==["alembic", "upgrade", release.TARGET]
        assert kwargs["check"] is True and kwargs["timeout"]==600
        assert kwargs["env"]["ORION_MIGRATION_SWITCH_ROLE"]=="1"
        assert kwargs["stdout"]==subprocess.DEVNULL
        assert kwargs["stderr"]==subprocess.DEVNULL
        steps.append("reviewed_migration")

    monkeypatch.setattr(release,"perform_backup",verify_backup)
    monkeypatch.setattr(release.subprocess,"run",migrate)
    return steps


def test_only_private_verified_backup_can_authorize_0076_migration(monkeypatch):
    steps=prepare(monkeypatch,[release.PREVIOUS]*3)
    assert release.main()==0
    assert steps==["verified_private_backup","reviewed_migration"]


def test_published_0076_only_checks_read_only_contract(monkeypatch):
    steps=prepare(monkeypatch,[release.TARGET])
    assert release.main()==0
    assert steps==[]


@pytest.mark.parametrize("head",["0072_subscription_group_add","0076_unknown",""])
def test_unknown_or_old_schema_head_never_backup_or_mutates(monkeypatch,head):
    steps=prepare(monkeypatch,[head])
    assert release.main()==3 and steps==[]


def test_predecessor_must_have_passed_before_backup(monkeypatch):
    steps=prepare(monkeypatch,[release.PREVIOUS]*2,predecessor=False)
    assert release.main()==3
    assert steps==[]


def test_failed_backup_prevents_migration_and_redacts_provider_details(monkeypatch,capsys):
    steps=prepare(monkeypatch,[release.PREVIOUS]*2)
    def fail(*args,**kwargs):
        raise RuntimeError("private credentials should never appear in terminal")
    monkeypatch.setattr(release,"perform_backup",fail)
    assert release.main()==3
    assert steps==[]
    captured=capsys.readouterr()
    assert "private credentials" not in captured.out + captured.err


def test_changed_revision_after_backup_fails_closed(monkeypatch):
    steps=prepare(monkeypatch,[release.PREVIOUS,release.PREVIOUS,"0077_other"])
    assert release.main()==3
    assert steps==["verified_private_backup"]


def test_migration_without_verified_postcondition_fails(monkeypatch):
    steps=prepare(monkeypatch,[release.PREVIOUS]*3,postcondition=False)
    assert release.main()==3
    assert steps==["verified_private_backup","reviewed_migration"]


def test_0076_has_one_parent_matching_existing_monthly_migration():
    from pathlib import Path
    source=(Path(__file__).parents[2] / "alembic/versions/0076_dental_clinic.py").read_text()
    assert 'revision = "0076_dental_clinic"' in source
    assert 'down_revision = "0075_fixed_client_monthly"' in source
    assert 'EXCLUDE USING gist' in source
    assert 'FORCE ROW LEVEL SECURITY' in source
