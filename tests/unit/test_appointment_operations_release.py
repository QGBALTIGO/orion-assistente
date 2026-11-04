"""Backup ordering, schema compatibility and runtime startup boundaries."""
import importlib

import pytest

from scripts import backup_and_migrate_appointment_operations as release
from scripts import backup_and_migrate_subscription_sent_messages as previous_release
from scripts import wait_for_appointment_operations_schema as barrier
from scripts import wait_for_single_whatsapp_schema as chain


def prepared(monkeypatch, versions):
    events = []
    revisions = iter(versions)
    monkeypatch.setattr(release, "connection_values", lambda: ("synthetic", object()))
    monkeypatch.setattr(release, "migrated_version", lambda _: next(revisions))
    monkeypatch.setattr(release, "storage_values", lambda: {})
    monkeypatch.setattr(previous_release, "schema_ready", lambda _: True)
    monkeypatch.setattr(release, "schema_ready", lambda _: True)

    def backup(*args, **kwargs):
        assert kwargs["schema_revision"] == release.PREVIOUS
        assert kwargs["backup_context"] == "appointment-operations-0066"
        assert kwargs["preserve_ownership"] is True
        events.append("verified-backup")

    def migrate(command, **kwargs):
        assert events == ["verified-backup"]
        assert command[-3:] == ["alembic", "upgrade", release.TARGET]
        assert kwargs["env"]["ORION_MIGRATION_SWITCH_ROLE"] == "1"
        assert kwargs["check"] is True and kwargs["timeout"] == 600
        assert kwargs["stdout"] == release.subprocess.DEVNULL
        assert kwargs["stderr"] == release.subprocess.DEVNULL
        events.append("migration")

    monkeypatch.setattr(release, "perform_backup", backup)
    monkeypatch.setattr(release.subprocess, "run", migrate)
    return events


def test_only_verified_private_backup_allows_reviewed_migration(monkeypatch):
    events = prepared(monkeypatch, [release.PREVIOUS, release.PREVIOUS, release.TARGET])
    assert release.main() == 0
    assert events == ["verified-backup", "migration"]


@pytest.mark.parametrize("revision", ["0064_subscription_templates", "0067_unknown", ""])
def test_unreviewed_source_cannot_run_backup_or_migration(monkeypatch, revision):
    events = prepared(monkeypatch, [revision])
    assert release.main() == 3 and events == []


@pytest.mark.parametrize("valid", [True, False])
@pytest.mark.parametrize("revision", ["0066_appointment_operations", "0067_subscription_diagnostics"])
def test_restart_only_checks_already_published_contract(monkeypatch, valid, revision):
    events = prepared(monkeypatch, [revision])
    monkeypatch.setattr(release, "schema_ready", lambda _: valid)
    assert release.main() == (0 if valid else 3)
    assert events == []


def test_previous_security_contract_must_be_intact(monkeypatch):
    events = prepared(monkeypatch, [release.PREVIOUS])
    monkeypatch.setattr(previous_release, "schema_ready", lambda _: False)
    assert release.main() == 3 and events == []


def test_failed_backup_stops_ddl_and_hides_private_details(monkeypatch, capsys):
    events = prepared(monkeypatch, [release.PREVIOUS])

    def failed(*args, **kwargs):
        raise RuntimeError("synthetic-private-storage-credential")

    monkeypatch.setattr(release, "perform_backup", failed)
    assert release.main() == 3 and events == []
    assert "synthetic-private-storage-credential" not in capsys.readouterr().err


@pytest.mark.parametrize("revision,valid,expected", [
    (release.TARGET, True, 0), (release.TARGET, False, 3), ("unreviewed", True, 3),
    ("0067_subscription_diagnostics", True, 0), ("0067_subscription_diagnostics", False, 3),
])
def test_concurrent_revision_is_verified_or_rejected_without_overwrite(monkeypatch, revision, valid, expected):
    events = prepared(monkeypatch, [release.PREVIOUS, revision])
    monkeypatch.setattr(release, "schema_ready", lambda _: valid)
    assert release.main() == expected and events == ["verified-backup"]


def test_contract_is_revalidated_after_backup_before_ddl(monkeypatch):
    events = prepared(monkeypatch, [release.PREVIOUS, release.PREVIOUS])
    states = iter([True, False])
    monkeypatch.setattr(previous_release, "schema_ready", lambda _: next(states))
    assert release.main() == 3 and events == ["verified-backup"]


@pytest.mark.parametrize("revision,valid", [(release.PREVIOUS, True), (release.TARGET, False)])
def test_successful_command_cannot_hide_unpublished_or_invalid_schema(monkeypatch, revision, valid):
    events = prepared(monkeypatch, [release.PREVIOUS, release.PREVIOUS, revision])
    monkeypatch.setattr(release, "schema_ready", lambda _: valid)
    assert release.main() == 3 and events == ["verified-backup", "migration"]


PRIOR_RELEASES = (
    "backup_and_migrate_cakto", "backup_and_migrate_prepaid_pix",
    "backup_and_migrate_manual_start_times", "backup_and_migrate_appointment_consent",
    "backup_and_migrate_subscription_devices", "backup_and_migrate_subscription_chatbot",
    "backup_and_migrate_subscription_templates", "backup_and_migrate_subscription_sent_messages",
)


@pytest.mark.parametrize("name", PRIOR_RELEASES)
@pytest.mark.parametrize("valid", [True, False])
@pytest.mark.parametrize("revision", ["0066_appointment_operations", "0067_subscription_diagnostics"])
def test_prior_releases_verify_own_contract_without_replaying_ddl(monkeypatch, name, valid, revision):
    module = importlib.import_module("scripts." + name)
    monkeypatch.setattr(module, "connection_values", lambda: ("synthetic", object()))
    monkeypatch.setattr(module, "migrated_version", lambda _: revision)
    monkeypatch.setattr(module, "schema_ready", lambda _: valid)

    def forbidden(*args, **kwargs):
        pytest.fail("Published migrations cannot be replayed")

    monkeypatch.setattr(module, "perform_backup", forbidden)
    monkeypatch.setattr(module.subprocess, "run", forbidden)
    assert module.main() == (0 if valid else 3)


@pytest.mark.parametrize("revision", ["0066_appointment_operations", "0067_subscription_diagnostics"])
def test_registration_gate_recognizes_additive_revision_and_keeps_older_contracts(monkeypatch, revision):
    from scripts import backup_and_migrate_registration as registration
    from scripts import backup_and_migrate_short_links as short_links
    from scripts import backup_and_migrate_combo_duration as combos
    monkeypatch.setattr(registration, "connection_values", lambda: ("synthetic", object()))
    monkeypatch.setattr(registration, "migrated_version", lambda _: revision)
    checks = ((short_links, "schema_ready"), (combos, "schema_ready"),
              (registration, "one_click_schema_ready"), (registration, "portal_schema_ready"),
              (registration, "professional_hardening_schema_ready"))
    for module, name in checks:
        monkeypatch.setattr(module, name, lambda _: True)
    monkeypatch.setattr(registration, "main", lambda: pytest.fail("Earlier migration replay"))
    assert registration.predeploy_main() == 0
    for module, name in checks:
        monkeypatch.setattr(module, name, lambda _: False)
        assert registration.predeploy_main() == 3
        monkeypatch.setattr(module, name, lambda _: True)


@pytest.mark.parametrize("blocked", [None, 7, 8])
@pytest.mark.parametrize("revision", ["0066_appointment_operations", "0067_subscription_diagnostics"])
def test_api_predeploy_preserves_agenda_before_diagnostics_and_normal_startup(monkeypatch, blocked, revision):
    from scripts import predeploy_one_click_links as entry
    from scripts import backup_and_migrate_commissions as backup
    from scripts import apply_variable_combo_migration as version
    from scripts import backup_and_migrate_registration as registration
    events = []
    monkeypatch.delenv("ORION_PIX_PROVISION_ONCE", raising=False)
    monkeypatch.setattr(entry.subprocess, "run", lambda *a, **k: events.append("legacy-checks"))
    monkeypatch.setattr(backup, "connection_values", lambda: ("synthetic", object()))
    monkeypatch.setattr(version, "migrated_version", lambda _: revision)
    monkeypatch.setattr(registration, "portal_schema_ready", lambda _: True)
    monkeypatch.setattr(registration, "professional_hardening_schema_ready", lambda _: True)
    modules = (*PRIOR_RELEASES[1:], "backup_and_migrate_appointment_operations", "backup_and_migrate_subscription_diagnostics")
    for index, name in enumerate(modules):
        module = importlib.import_module("scripts." + name)
        monkeypatch.setattr(module, "main", lambda i=index: events.append(i) or int(i == blocked))
    if blocked is None:
        entry.main()
    else:
        error = "appointment_operations_schema_not_ready" if blocked == 7 else "subscription_diagnostics_schema_not_ready"
        with pytest.raises(RuntimeError, match=error):
            entry.main()
    assert events == ["legacy-checks", *range(len(modules) if blocked is None else blocked + 1)]


@pytest.mark.asyncio
@pytest.mark.parametrize("ready", [True, False])
async def test_probe_reads_only_catalogs_with_bounded_runtime_connection(monkeypatch, ready):
    closed = []

    class Connection:
        async def fetchrow(self, query):
            assert query == barrier.SCHEMA_QUERY
            return {"runtime_role": "app_runtime", "authenticated_role": "app_runtime",
                    "restricted_role": True, "schema_ready": ready}

        def terminate(self):
            closed.append(True)

    async def connect(dsn, **kwargs):
        assert dsn == "synthetic"
        assert kwargs["timeout"] == 3 and kwargs["command_timeout"] == 3
        assert kwargs["server_settings"]["default_transaction_read_only"] == "on"
        assert kwargs["server_settings"]["search_path"] == "pg_catalog"
        assert kwargs["server_settings"]["statement_timeout"] == "3000"
        return Connection()

    monkeypatch.setattr(barrier.asyncpg, "connect", connect)
    assert await barrier.probe_schema(barrier.Settings("synthetic"), 3) is ready
    assert closed == [True]


@pytest.mark.asyncio
@pytest.mark.parametrize("role,authenticated,restricted", [
    ("app_migrator", "app_migrator", True), ("app_support", "app_support", True),
    ("app_runtime", "postgres", True), ("app_runtime", "app_runtime", False),
])
async def test_probe_rejects_owner_assumption_and_unrestricted_roles(monkeypatch, role, authenticated, restricted):
    closed = []

    class Connection:
        async def fetchrow(self, query):
            return {"runtime_role": role, "authenticated_role": authenticated,
                    "restricted_role": restricted, "schema_ready": True}

        def terminate(self):
            closed.append(True)

    async def connect(*args, **kwargs):
        return Connection()

    monkeypatch.setattr(barrier.asyncpg, "connect", connect)
    with pytest.raises(barrier.Stop, match="runtime_role_required"):
        await barrier.probe_schema(barrier.Settings("synthetic"), 3)
    assert closed == [True]


@pytest.mark.parametrize("blocked", [*range(8), None])
def test_worker_predeploy_stops_at_first_unready_contract(monkeypatch, blocked):
    names = ("manual_start_times", "appointment_consent", "subscription_devices",
             "subscription_chatbot", "subscription_templates", "subscription_sent_messages",
             "appointment_operations", "subscription_diagnostics")
    events = []

    async def ready(_):
        return {"ok": True}

    monkeypatch.setattr(chain, "wait_for_schema", ready)
    monkeypatch.setattr(chain.Settings, "from_environment", lambda: barrier.Settings("synthetic"))
    for index, name in enumerate(names):
        module = importlib.import_module("scripts.wait_for_" + name + "_schema")
        monkeypatch.setattr(module, "main", lambda i=index: events.append(i) or int(i == blocked))
    assert chain.main() == int(blocked is not None)
    assert events == list(range(len(names) if blocked is None else blocked + 1))


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked", [*range(8), None])
async def test_arq_cannot_consume_jobs_before_all_readonly_contracts(monkeypatch, blocked):
    from app.worker import WorkerSettings
    names = ("plan_permissions", "appointment_consent", "subscription_devices",
             "subscription_chatbot", "subscription_templates", "subscription_sent_messages",
             "appointment_operations", "subscription_diagnostics")
    events = []
    context = {}
    for flag in ("ORION_REVIEW_QA_VERIFY_7364", "ORION_REVIEW_QA_SEND_7364"):
        monkeypatch.delenv(flag, raising=False)
    monkeypatch.setattr(barrier.Settings, "from_environment", lambda: barrier.Settings("synthetic"))
    for index, name in enumerate(names):
        module = importlib.import_module("scripts.wait_for_" + name + "_schema")

        async def wait(settings, i=index):
            assert settings.dsn == "synthetic"
            events.append(i)
            if i == blocked:
                raise barrier.Stop("agenda_schema_timeout")

        monkeypatch.setattr(module, "wait_for_schema", wait)
    if blocked is None:
        await WorkerSettings.on_startup(context)
        assert context["plan_permissions_ready"] is True
    else:
        with pytest.raises(barrier.Stop, match="agenda_schema_timeout"):
            await WorkerSettings.on_startup(context)
        assert "plan_permissions_ready" not in context
    assert events == list(range(len(names) if blocked is None else blocked + 1))
