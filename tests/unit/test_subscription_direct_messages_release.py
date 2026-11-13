"""The direct-message schema cannot publish or start without reviewed guards."""
import importlib
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest

from scripts import backup_and_migrate_subscription_direct as release
from scripts import backup_and_migrate_subscription_bulk as previous_release
from scripts import wait_for_subscription_direct_schema as barrier


def prepared(monkeypatch, revisions):
    events = []
    versions = iter(revisions)
    monkeypatch.setattr(release, "connection_values", lambda: ("synthetic", object()))
    monkeypatch.setattr(release, "migrated_version", lambda _: next(versions))
    monkeypatch.setattr(release, "storage_values", lambda: {})
    monkeypatch.setattr(previous_release, "schema_ready", lambda _: True)
    monkeypatch.setattr(release, "schema_ready", lambda _: True)

    def backup(*args, **kwargs):
        assert kwargs == {"schema_revision": release.PREVIOUS,
                          "backup_context": "subscription-direct-0071",
                          "preserve_ownership": True}
        events.append("verified-backup")

    def migrate(command, **kwargs):
        assert events == ["verified-backup"]
        assert command == [release.sys.executable, "-m", "alembic", "upgrade", release.TARGET]
        assert kwargs["env"]["ORION_MIGRATION_SWITCH_ROLE"] == "1"
        assert kwargs["stdout"] == kwargs["stderr"] == release.subprocess.DEVNULL
        assert kwargs["check"] is True and kwargs["timeout"] == 600
        events.append("migration")

    monkeypatch.setattr(release, "perform_backup", backup)
    monkeypatch.setattr(release.subprocess, "run", migrate)
    return events


def test_direct_remains_an_ancestor_and_preserves_the_published_bulk_predecessor():
    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("path_separator", "os")
    config.set_main_option("script_location", str(root / "alembic"))
    migrations = ScriptDirectory.from_config(config)
    assert migrations.get_heads() == ["0079_service_archive"]
    assert migrations.get_revision("0077_commercial_demos").down_revision == "0076_dental_clinic"
    assert migrations.get_revision("0076_dental_clinic").down_revision == "0075_fixed_client_monthly"
    assert migrations.get_revision("0075_fixed_client_monthly").down_revision == "0074_fixed_client_series"
    assert migrations.get_revision("0074_fixed_client_series").down_revision == "0073_cakto_recovery"
    assert migrations.get_revision("0072_subscription_group_add").down_revision == release.TARGET
    assert release.TARGET == barrier.SCHEMA_MARKER == "0071_subscription_direct"
    assert release.PREVIOUS == previous_release.TARGET == "0070_subscription_bulk"
    assert migrations.get_revision(release.TARGET).down_revision == release.PREVIOUS
    assert len(release.TARGET) <= 32


def test_verified_private_backup_precedes_the_direct_origin_ddl(monkeypatch):
    events = prepared(monkeypatch, [release.PREVIOUS, release.PREVIOUS, release.TARGET])
    assert release.main() == 0
    assert events == ["verified-backup", "migration"]


@pytest.mark.parametrize("revision", ["0069_subscription_schedules", "0072_unreviewed", ""])
def test_unknown_source_never_runs_backup_or_ddl(monkeypatch, revision):
    events = prepared(monkeypatch, [revision])
    assert release.main() == 3 and events == []


@pytest.mark.parametrize("valid", [True, False])
def test_restart_only_checks_the_published_catalog(monkeypatch, valid):
    events = prepared(monkeypatch, [release.TARGET])
    monkeypatch.setattr(release, "schema_ready", lambda _: valid)
    assert release.main() == (0 if valid else 3)
    assert events == []


@pytest.mark.parametrize("after_backup", [False, True])
def test_predecessor_must_remain_valid_until_ddl(monkeypatch, after_backup):
    events = prepared(monkeypatch, [release.PREVIOUS, release.PREVIOUS])
    results = iter([True, False] if after_backup else [False])
    monkeypatch.setattr(previous_release, "schema_ready", lambda _: next(results))
    assert release.main() == 3
    assert events == (["verified-backup"] if after_backup else [])


@pytest.mark.parametrize("revision,valid,result", [
    (release.TARGET, True, 0), (release.TARGET, False, 3), ("0072_unreviewed", True, 3),
])
def test_concurrent_publication_is_verified_without_replaying_ddl(monkeypatch, revision, valid, result):
    events = prepared(monkeypatch, [release.PREVIOUS, revision])
    monkeypatch.setattr(release, "schema_ready", lambda _: valid)
    assert release.main() == result and events == ["verified-backup"]


@pytest.mark.parametrize("revision,valid", [(release.PREVIOUS, True), (release.TARGET, False)])
def test_alembic_success_cannot_hide_an_invalid_revision_or_catalog(monkeypatch, revision, valid):
    events = prepared(monkeypatch, [release.PREVIOUS, release.PREVIOUS, revision])
    monkeypatch.setattr(release, "schema_ready", lambda _: valid)
    assert release.main() == 3 and events == ["verified-backup", "migration"]


@pytest.mark.parametrize("failure", ["backup", "migration"])
def test_failed_backup_or_migration_blocks_ready_and_redacts_details(monkeypatch, capsys, failure):
    events = prepared(monkeypatch, [release.PREVIOUS, release.PREVIOUS])

    def failed(*args, **kwargs):
        raise RuntimeError("synthetic-private-release-detail")

    if failure == "backup":
        monkeypatch.setattr(release, "perform_backup", failed)
    else:
        monkeypatch.setattr(release.subprocess, "run", failed)
    assert release.main() == 3
    assert events == ([] if failure == "backup" else ["verified-backup"])
    output = capsys.readouterr()
    assert "synthetic-private" not in output.out + output.err
    assert "schema_release_failed_closed" in output.err
    assert "orion_subscription_direct_schema_ready" not in output.out


@pytest.mark.parametrize("module_name", ["subscription_diagnostics", "subscription_campaigns",
                                        "subscription_scheduled_messages", "subscription_bulk"])
@pytest.mark.parametrize("valid", [True, False])
def test_recent_predecessors_accept_0071_only_with_their_own_contract(monkeypatch, module_name, valid):
    module = importlib.import_module("scripts.backup_and_migrate_" + module_name)
    monkeypatch.setattr(module, "connection_values", lambda: ("synthetic", object()))
    monkeypatch.setattr(module, "migrated_version", lambda _: release.TARGET)
    monkeypatch.setattr(module, "schema_ready", lambda _: valid)

    def forbidden(*args, **kwargs):
        pytest.fail("Published predecessor must not replay backup or DDL")

    monkeypatch.setattr(module, "perform_backup", forbidden)
    monkeypatch.setattr(module.subprocess, "run", forbidden)
    assert module.main() == (0 if valid else 3)


@pytest.mark.asyncio
@pytest.mark.parametrize("role,authenticated,restricted,ready", [
    ("app_runtime", "app_runtime", True, True),
    ("app_runtime", "app_runtime", True, False),
    ("app_support", "app_support", True, True),
    ("app_migrator", "app_migrator", True, True),
    ("app_runtime", "postgres", True, True),
    ("app_runtime", "app_runtime", False, True),
])
async def test_probe_requires_bounded_readonly_runtime_catalog_access(monkeypatch, role, authenticated, restricted, ready):
    closed = []

    class Connection:
        async def fetchrow(self, query):
            assert query == barrier.SCHEMA_QUERY
            return {"runtime_role": role, "authenticated_role": authenticated,
                    "restricted_role": restricted, "schema_ready": ready}

        def terminate(self):
            closed.append(True)

    async def connect(dsn, **kwargs):
        assert dsn == "synthetic"
        assert kwargs["timeout"] == kwargs["command_timeout"] == 3
        assert kwargs["server_settings"] == {
            "default_transaction_read_only": "on", "search_path": "pg_catalog",
            "statement_timeout": "3000", "lock_timeout": "3000",
            "application_name": "orion-subscription-direct-schema-wait",
        }
        return Connection()

    monkeypatch.setattr(barrier.asyncpg, "connect", connect)
    if (role, authenticated, restricted) == ("app_runtime", "app_runtime", True):
        assert await barrier.probe_schema(barrier.Settings("synthetic"), 3) is ready
    else:
        with pytest.raises(barrier.Stop, match="runtime_role_required"):
            await barrier.probe_schema(barrier.Settings("synthetic"), 3)
    assert closed == [True]
