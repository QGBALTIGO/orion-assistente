"""Release invariants: verified backup, one additive revision and worker wait."""
import pytest

from scripts import backup_and_migrate_appointment_consent as release
from scripts import backup_and_migrate_manual_start_times as prepaid
from scripts import backup_and_migrate_cakto as cakto
from scripts import backup_and_migrate_registration as registration
from scripts import wait_for_appointment_consent_schema as barrier
from scripts import wait_for_single_whatsapp_schema as worker_barrier


def setup_release(monkeypatch, versions):
    observed = []
    revisions = iter(versions)
    monkeypatch.setattr(release, "connection_values", lambda: ("synthetic", object()))
    monkeypatch.setattr(release, "migrated_version", lambda _: next(revisions))
    monkeypatch.setattr(release, "storage_values", lambda: {"synthetic": True})
    monkeypatch.setattr(prepaid, "schema_ready", lambda _: True)
    monkeypatch.setattr(release, "schema_ready", lambda _: True)

    def backup(*args, **kwargs):
        assert kwargs["schema_revision"] == release.PREVIOUS
        assert kwargs["preserve_ownership"] is True
        observed.append("verified-backup")

    def migrate(command, **kwargs):
        assert observed == ["verified-backup"]
        assert command[-3:] == ["alembic", "upgrade", release.TARGET]
        assert kwargs["env"]["ORION_MIGRATION_SWITCH_ROLE"] == "1"
        assert kwargs["check"] is True
        observed.append("migration")

    monkeypatch.setattr(release, "perform_backup", backup)
    monkeypatch.setattr(release.subprocess, "run", migrate)
    return observed


def test_backup_precedes_only_reviewed_migration_and_final_contract(monkeypatch):
    observed = setup_release(monkeypatch, [release.PREVIOUS, release.PREVIOUS, release.TARGET])
    assert release.main() == 0
    assert observed == ["verified-backup", "migration"]


@pytest.mark.parametrize("revision", ["0058_professional_scope", "future_schema", ""])
def test_unreviewed_source_revision_never_backs_up_or_migrates(monkeypatch, revision):
    observed = setup_release(monkeypatch, [revision])
    assert release.main() == 3
    assert observed == []


@pytest.mark.parametrize("valid", [True, False])
def test_already_published_revision_only_verifies_contract(monkeypatch, valid):
    observed = setup_release(monkeypatch, [release.TARGET])
    monkeypatch.setattr(release, "schema_ready", lambda _: valid)
    assert release.main() == (0 if valid else 3)
    assert observed == []


def test_failed_backup_prevents_migration_and_hides_exception_details(monkeypatch, capsys):
    observed = setup_release(monkeypatch, [release.PREVIOUS])

    def failed_backup(*args, **kwargs):
        raise RuntimeError("private-connection-or-storage-details")

    monkeypatch.setattr(release, "perform_backup", failed_backup)
    assert release.main() == 3
    assert observed == []
    assert "private-connection-or-storage-details" not in capsys.readouterr().err


def test_changed_revision_after_backup_prevents_wrong_migration(monkeypatch):
    observed = setup_release(monkeypatch, [release.PREVIOUS, "unreviewed_revision"])
    assert release.main() == 3
    assert observed == ["verified-backup"]


def test_concurrent_completed_migration_only_checks_current_contract(monkeypatch):
    observed = setup_release(monkeypatch, [release.PREVIOUS, release.TARGET])
    assert release.main() == 0
    assert observed == ["verified-backup"]


def test_existing_release_verifiers_accept_0061_without_replaying_ddl(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("An already applied migration must not run again")

    for module in (prepaid, cakto):
        monkeypatch.setattr(module, "connection_values", lambda: ("synthetic", object()))
        monkeypatch.setattr(module, "migrated_version", lambda _: release.TARGET)
        monkeypatch.setattr(module, "schema_ready", lambda _: True)
        monkeypatch.setattr(module, "perform_backup", forbidden)
        assert module.main() == 0
    from scripts import backup_and_migrate_short_links, backup_and_migrate_combo_duration
    monkeypatch.setattr(registration, "connection_values", lambda: ("synthetic", object()))
    monkeypatch.setattr(registration, "migrated_version", lambda _: release.TARGET)
    for module, name in ((backup_and_migrate_short_links, "schema_ready"),
                         (backup_and_migrate_combo_duration, "schema_ready"),
                         (registration, "one_click_schema_ready"),
                         (registration, "portal_schema_ready"),
                         (registration, "professional_hardening_schema_ready")):
        monkeypatch.setattr(module, name, lambda _: True)
    monkeypatch.setattr(registration, "main", forbidden)
    assert registration.predeploy_main() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("ready", [False, True])
async def test_worker_probe_uses_read_only_runtime_and_waits_for_schema(monkeypatch, ready):
    closed = []

    class Connection:
        async def fetchrow(self, _):
            return {"runtime_role": "app_runtime", "authenticated_role": "app_runtime",
                    "restricted_role": True, "schema_ready": ready}

        def terminate(self):
            closed.append(True)

    async def connect(dsn, **kwargs):
        assert dsn == "synthetic"
        assert kwargs["server_settings"]["default_transaction_read_only"] == "on"
        assert kwargs["server_settings"]["search_path"] == "pg_catalog"
        return Connection()

    monkeypatch.setattr(barrier.asyncpg, "connect", connect)
    assert await barrier.probe_schema(barrier.Settings("synthetic"), 3) is ready
    assert closed == [True]


@pytest.mark.asyncio
async def test_worker_probe_rejects_privileged_database_identity(monkeypatch):
    closed = []

    class Connection:
        async def fetchrow(self, _):
            return {"runtime_role": "app_migrator", "authenticated_role": "app_migrator",
                    "restricted_role": True, "schema_ready": True}

        def terminate(self):
            closed.append(True)

    async def connect(*args, **kwargs):
        return Connection()

    monkeypatch.setattr(barrier.asyncpg, "connect", connect)
    with pytest.raises(barrier.Stop, match="runtime_role_required"):
        await barrier.probe_schema(barrier.Settings("synthetic"), 3)
    assert closed == [True]


@pytest.mark.parametrize("manual_result,consent_result,expected", [(1,0,1),(0,1,1),(0,0,0)])
def test_worker_requires_both_schema_barriers(monkeypatch, manual_result, consent_result, expected):
    from scripts import wait_for_manual_start_times_schema as manual
    calls=[]
    async def ready(_):
        return {"ok": True}
    monkeypatch.setattr(worker_barrier, "wait_for_schema", ready)
    monkeypatch.setattr(worker_barrier.Settings, "from_environment", lambda: barrier.Settings("synthetic"))
    monkeypatch.setattr(manual, "main", lambda: calls.append("manual") or manual_result)
    monkeypatch.setattr(barrier, "main", lambda: calls.append("consent") or consent_result)
    assert worker_barrier.main()==expected
    assert calls==(["manual"] if manual_result else ["manual","consent"])
