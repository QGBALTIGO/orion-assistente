"""Release guards for the private subscription templates extension."""
import pytest

from scripts import backup_and_migrate_subscription_templates as release
from scripts import backup_and_migrate_subscription_chatbot as consent
from scripts import wait_for_subscription_templates_schema as barrier
from scripts import wait_for_single_whatsapp_schema as chain


def prepared(monkeypatch, versions):
    events = []
    revisions = iter(versions)
    monkeypatch.setattr(release, "connection_values", lambda: ("synthetic", object()))
    monkeypatch.setattr(release, "migrated_version", lambda _: next(revisions))
    monkeypatch.setattr(release, "storage_values", lambda: {})
    monkeypatch.setattr(consent, "schema_ready", lambda _: True)
    monkeypatch.setattr(release, "schema_ready", lambda _: True)

    def backup(*args, **kwargs):
        assert kwargs["schema_revision"] == release.PREVIOUS
        assert kwargs["backup_context"] == "subscription-templates-0064"
        assert kwargs["preserve_ownership"] is True
        events.append("verified-backup")

    def migrate(command, **kwargs):
        assert events == ["verified-backup"]
        assert command[-3:] == ["alembic", "upgrade", release.TARGET]
        assert kwargs["env"]["ORION_MIGRATION_SWITCH_ROLE"] == "1"
        assert kwargs["check"] is True and kwargs["timeout"] == 600
        events.append("migration")

    monkeypatch.setattr(release, "perform_backup", backup)
    monkeypatch.setattr(release.subprocess, "run", migrate)
    return events


def test_only_verified_backup_allows_reviewed_revision(monkeypatch):
    events = prepared(monkeypatch, [release.PREVIOUS, release.PREVIOUS, release.TARGET])
    assert release.main() == 0
    assert events == ["verified-backup", "migration"]


@pytest.mark.parametrize("revision", ["0061_appointment_consent", "0065_unknown", ""])
def test_unknown_source_never_changes_database(monkeypatch, revision):
    events = prepared(monkeypatch, [revision])
    assert release.main() == 3 and events == []


@pytest.mark.parametrize("valid", [True, False])
def test_published_revision_only_checks_contract(monkeypatch, valid):
    events = prepared(monkeypatch, [release.TARGET])
    monkeypatch.setattr(release, "schema_ready", lambda _: valid)
    assert release.main() == (0 if valid else 3)
    assert events == []


def test_old_permission_contract_must_remain_intact(monkeypatch):
    events = prepared(monkeypatch, [release.PREVIOUS])
    monkeypatch.setattr(consent, "schema_ready", lambda _: False)
    assert release.main() == 3 and events == []


def test_failed_backup_hides_internal_details_and_prevents_ddl(monkeypatch, capsys):
    events = prepared(monkeypatch, [release.PREVIOUS])

    def failed(*args, **kwargs):
        raise RuntimeError("synthetic-private-storage-credential")

    monkeypatch.setattr(release, "perform_backup", failed)
    assert release.main() == 3 and events == []
    assert "synthetic-private-storage-credential" not in capsys.readouterr().err


@pytest.mark.parametrize("revision,expected", [(release.TARGET, 0), ("unreviewed", 3)])
def test_concurrent_revision_change_is_never_overwritten(monkeypatch, revision, expected):
    events = prepared(monkeypatch, [release.PREVIOUS, revision])
    assert release.main() == expected and events == ["verified-backup"]


def test_prior_release_gates_recognize_new_additive_revision_without_replaying(monkeypatch):
    from scripts import backup_and_migrate_cakto, backup_and_migrate_manual_start_times
    from scripts import backup_and_migrate_prepaid_pix, backup_and_migrate_registration
    from scripts import backup_and_migrate_short_links, backup_and_migrate_combo_duration

    def forbidden(*args, **kwargs):
        pytest.fail("Published migrations cannot be replayed")

    for module in (consent, backup_and_migrate_cakto, backup_and_migrate_manual_start_times,
                   backup_and_migrate_prepaid_pix):
        monkeypatch.setattr(module, "connection_values", lambda: ("synthetic", object()))
        monkeypatch.setattr(module, "migrated_version", lambda _: release.TARGET)
        monkeypatch.setattr(module, "schema_ready", lambda _: True)
        monkeypatch.setattr(module, "perform_backup", forbidden)
        assert module.main() == 0
    registration = backup_and_migrate_registration
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
@pytest.mark.parametrize("ready", [True, False])
async def test_probe_uses_only_read_only_catalogs_under_runtime_role(monkeypatch, ready):
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
        assert kwargs["server_settings"]["default_transaction_read_only"] == "on"
        assert kwargs["server_settings"]["search_path"] == "pg_catalog"
        return Connection()

    monkeypatch.setattr(barrier.asyncpg, "connect", connect)
    assert await barrier.probe_schema(barrier.Settings("synthetic"), 3) is ready
    assert closed == [True]


@pytest.mark.asyncio
@pytest.mark.parametrize("role,restricted", [("app_migrator", True), ("app_support", True), ("app_runtime", False)])
async def test_probe_rejects_privileged_or_wrong_database_role(monkeypatch, role, restricted):
    class Connection:
        async def fetchrow(self, query):
            return {"runtime_role": role, "authenticated_role": role,
                    "restricted_role": restricted, "schema_ready": True}

        def terminate(self):
            pass

    async def connect(*args, **kwargs):
        return Connection()

    monkeypatch.setattr(barrier.asyncpg, "connect", connect)
    with pytest.raises(barrier.Stop, match="runtime_role_required"):
        await barrier.probe_schema(barrier.Settings("synthetic"), 3)


@pytest.mark.parametrize("blocked", [0, 1, 2, 3, 4, None])
def test_worker_waits_for_all_five_contracts_and_stops_at_first_failure(monkeypatch, blocked):
    from scripts import wait_for_manual_start_times_schema as hours
    from scripts import wait_for_appointment_consent_schema as notifications
    from scripts import wait_for_subscription_devices_schema as devices
    from scripts import wait_for_subscription_chatbot_schema as chatbot
    from scripts import wait_for_subscription_sent_messages_schema as sent_messages_wait
    from scripts import wait_for_appointment_operations_schema as operations_wait
    monkeypatch.setattr(operations_wait, "main", lambda: 0)
    monkeypatch.setattr(sent_messages_wait, "main", lambda: 0)
    events = []

    async def ready(_):
        return {"ok": True}

    monkeypatch.setattr(chain, "wait_for_schema", ready)
    monkeypatch.setattr(chain.Settings, "from_environment", lambda: barrier.Settings("synthetic"))
    for index, module in enumerate((hours, notifications, devices, chatbot, barrier)):
        monkeypatch.setattr(module, "main", lambda i=index: events.append(i) or int(i == blocked))
    assert chain.main() == int(blocked is not None)
    assert events == list(range(5 if blocked is None else blocked + 1))
