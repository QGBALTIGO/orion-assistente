"""Backup/CAS and restart contracts for the financial recovery schema release."""
from contextlib import contextmanager
import importlib
import json
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest

from scripts import backup_and_migrate_cakto_recovery as release
from scripts import predeploy_one_click_links as entry

ROOT = Path(__file__).resolve().parents[2]


@contextmanager
def unlocked(_):
    yield


def prepared(monkeypatch, revisions):
    events = []
    versions = iter(revisions)
    monkeypatch.setattr(release, "connection_values", lambda: ("synthetic", object()))
    monkeypatch.setattr(release, "migrated_version", lambda _: next(versions))
    monkeypatch.setattr(release, "release_lock", unlocked)
    monkeypatch.setattr(release, "previous_ready", lambda _: True)
    monkeypatch.setattr(release, "schema_ready", lambda _: True)
    monkeypatch.setattr(release, "storage_values", lambda: {})

    def backup(*_args, **kwargs):
        assert kwargs == {"backup_context": "cakto-recovery-0073",
                          "schema_revision": release.PREVIOUS, "preserve_ownership": True}
        events.append("verified-backup")

    def migrate(command, **kwargs):
        assert events == ["verified-backup"]
        assert command == [release.sys.executable, "-m", "alembic", "upgrade", release.TARGET]
        assert kwargs["env"]["ORION_MIGRATION_SWITCH_ROLE"] == "1"
        assert kwargs["check"] is True and kwargs["timeout"] == 600
        assert kwargs["stdout"] == kwargs["stderr"] == release.subprocess.DEVNULL
        events.append("migration")

    monkeypatch.setattr(release, "perform_backup", backup)
    monkeypatch.setattr(release.subprocess, "run", migrate)
    return events


def test_reviewed_successors_keep_the_additive_chain_after_published_0072():
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("path_separator", "os")
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    graph = ScriptDirectory.from_config(cfg)
    assert graph.get_heads() == ["0077_commercial_demos"]
    assert graph.get_revision("0077_commercial_demos").down_revision == "0076_dental_clinic"
    assert graph.get_revision("0076_dental_clinic").down_revision == "0075_fixed_client_monthly"
    assert graph.get_revision("0075_fixed_client_monthly").down_revision == "0074_fixed_client_series"
    assert graph.get_revision("0074_fixed_client_series").down_revision == release.TARGET
    assert graph.get_revision(release.TARGET).down_revision == release.PREVIOUS
    assert release.PREVIOUS == "0072_subscription_group_add"
    assert release.TARGET == "0073_cakto_recovery"


def test_backup_and_two_source_checks_precede_the_only_ddl(monkeypatch):
    events = prepared(monkeypatch, [release.PREVIOUS] * 3 + [release.TARGET])
    assert release.main() == 0 and events == ["verified-backup", "migration"]


@pytest.mark.parametrize("revision", ["0071_subscription_direct", "0074_unreviewed", "", None])
def test_unknown_source_never_backs_up_or_migrates(monkeypatch, revision):
    events = prepared(monkeypatch, [revision])
    assert release.main() == 3 and events == []


@pytest.mark.parametrize("valid", [False, True])
def test_completed_restart_only_checks_catalog_without_lock_or_backup(monkeypatch, valid):
    events = prepared(monkeypatch, [release.TARGET])
    monkeypatch.setattr(release, "schema_ready", lambda _: valid)
    monkeypatch.setattr(release, "release_lock", lambda _: pytest.fail("No migration lock on completed restart"))
    assert release.main() == (0 if valid else 3) and events == []


@pytest.mark.parametrize("stage", ["source", "post_backup"])
def test_source_contract_loss_stops_release(monkeypatch, stage):
    events = prepared(monkeypatch, [release.PREVIOUS] * 3)
    states = iter([False] if stage == "source" else [True, False])
    monkeypatch.setattr(release, "previous_ready", lambda _: next(states))
    assert release.main() == 3
    assert events == ([] if stage == "source" else ["verified-backup"])


@pytest.mark.parametrize("revision,valid,expected", [
    (release.TARGET, True, 0), (release.TARGET, False, 3), ("0074_unreviewed", True, 3),
])
def test_post_backup_revision_change_never_runs_a_second_ddl(monkeypatch, revision, valid, expected):
    events = prepared(monkeypatch, [release.PREVIOUS, release.PREVIOUS, revision])
    monkeypatch.setattr(release, "schema_ready", lambda _: valid)
    assert release.main() == expected and events == ["verified-backup"]


@pytest.mark.parametrize("failure", ["backup", "migration"])
def test_backup_or_ddl_failure_is_closed_and_does_not_print_private_exception(monkeypatch, capsys, failure):
    events = prepared(monkeypatch, [release.PREVIOUS] * 3)

    def fail(*_a, **_kw):
        raise RuntimeError("private-storage-or-database-detail")

    if failure == "backup":
        monkeypatch.setattr(release, "perform_backup", fail)
    else:
        monkeypatch.setattr(release.subprocess, "run", fail)
    assert release.main() == 3
    assert events == ([] if failure == "backup" else ["verified-backup"])
    output = capsys.readouterr()
    assert "private-storage-or-database-detail" not in output.out + output.err


@pytest.mark.parametrize("revision,contract", [(release.PREVIOUS, True), (release.TARGET, False)])
def test_successful_alembic_is_not_enough_without_exact_head_and_contract(monkeypatch, revision, contract):
    events = prepared(monkeypatch, [release.PREVIOUS] * 3 + [revision])
    monkeypatch.setattr(release, "schema_ready", lambda _: contract)
    assert release.main() == 3 and events == ["verified-backup", "migration"]


def prepare_entry(monkeypatch, revision):
    from scripts import backup_and_migrate_commissions as connections
    from scripts import apply_variable_combo_migration as versions
    events = []
    monkeypatch.setattr(connections, "connection_values", lambda: ("synthetic", object()))
    monkeypatch.setattr(versions, "migrated_version", lambda _: revision)
    monkeypatch.setattr(entry, "predecessors_ready", lambda _: events.append("readonly-contracts") or True)
    monkeypatch.setattr(release, "main", lambda: events.append("release73") or 0)
    monkeypatch.setattr(release, "schema_ready", lambda _: True)
    from scripts import backup_and_migrate_fixed_client_series as fixed
    monkeypatch.setattr(fixed, "main", lambda: events.append("release74") or 0)
    monkeypatch.setattr(fixed, "schema_ready", lambda _: True)
    from scripts import backup_and_migrate_fixed_client_monthly as monthly
    monkeypatch.setattr(monthly, "main", lambda: events.append("release75") or 0)
    monkeypatch.setattr(monthly, "schema_ready", lambda _: True)
    monkeypatch.setattr(entry, "_legacy_main", lambda: pytest.fail("No historical migration on 72/73"))
    # This fixture isolates the historical 0072-0075 chain. The newer demo
    # backup gate and missing-predecessor failure have their own release tests.
    monkeypatch.setattr(entry, "commercial_demo_release", lambda _: None)

    def run(command, **kwargs):
        assert kwargs == {"check": True}
        if command == [entry.sys.executable, "-c", entry.RUNTIME_PREDEPLOY_CHECK]:
            events.append("runtime-admin")
        else:
            assert command == [entry.sys.executable, "-m", "scripts.probe_cakto_read_only"]
            events.append("provider-get-probe")

    monkeypatch.setattr(entry.subprocess, "run", run)
    return events


@pytest.mark.parametrize("revision", [release.PREVIOUS, release.TARGET])
def test_current_predeploy_preserves_readonly_guards_without_provisioning_even_when_old_flag_is_set(monkeypatch, revision):
    events = prepare_entry(monkeypatch, revision)
    monkeypatch.setenv("ORION_PIX_PROVISION_ONCE", "1")
    entry.main()
    assert events == ["runtime-admin", "readonly-contracts", "release73",
                      "release74", "release75", "readonly-contracts", "provider-get-probe"]
    assert "wait_for_admin_identity_schema" in entry.RUNTIME_PREDEPLOY_CHECK
    assert "tested_runtime_versions_match" in entry.RUNTIME_PREDEPLOY_CHECK
    assert "backup_and_migrate" not in entry.RUNTIME_PREDEPLOY_CHECK


def test_predeploy_after_0074_never_repeats_0073_backup(monkeypatch):
    from scripts import backup_and_migrate_fixed_client_series as fixed
    events = prepare_entry(monkeypatch, fixed.TARGET)
    entry.main()
    assert events == ["runtime-admin", "readonly-contracts", "release74",
                      "release75", "readonly-contracts", "provider-get-probe"]


@pytest.mark.parametrize("stage", ["predecessor", "release", "postcondition", "probe"])
def test_current_predeploy_propagates_each_failed_guard(monkeypatch, stage):
    events = prepare_entry(monkeypatch, release.PREVIOUS)
    if stage == "predecessor":
        monkeypatch.setattr(entry, "predecessors_ready", lambda _: False)
    elif stage == "release":
        monkeypatch.setattr(release, "main", lambda: 3)
    elif stage == "postcondition":
        monkeypatch.setattr(release, "schema_ready", lambda _: False)
    else:
        def fail_probe(command, **kwargs):
            if "scripts.probe_cakto_read_only" in command:
                raise entry.subprocess.CalledProcessError(1, command)
        monkeypatch.setattr(entry.subprocess, "run", fail_probe)
    with pytest.raises((RuntimeError, entry.subprocess.CalledProcessError)):
        entry.main()
    assert "provider-get-probe" not in events
    if stage == "predecessor":
        assert "release73" not in events


def test_unknown_predeploy_head_never_starts_any_historical_or_provider_process(monkeypatch):
    events = prepare_entry(monkeypatch, "0074_unknown")
    with pytest.raises(RuntimeError, match="source_revision_not_approved"):
        entry.main()
    assert events == []


def test_reviewed_legacy_path_must_finish_before_the_new_backup_gate(monkeypatch):
    events = prepare_entry(monkeypatch, "0071_subscription_direct")
    monkeypatch.setattr(entry, "_legacy_main", lambda: events.append("legacy-chain"))
    monkeypatch.setattr(entry, "current_release", lambda raw: events.append("new-release"))
    entry.main()
    assert events == ["legacy-chain", "new-release"]


@pytest.mark.parametrize("flag", ["0", "1", "", "true"])
def test_prepaid_get_probe_runs_only_when_pix_is_enabled_and_must_succeed(monkeypatch, flag):
    events = prepare_entry(monkeypatch, release.TARGET)
    monkeypatch.setenv("ORION_PIX_PREPAID_ENABLED", flag)

    def run(command, **kwargs):
        assert kwargs == {"check": True}
        events.append(command[-1])
        if command[-1] == "scripts.probe_prepaid_pix_read_only":
            raise entry.subprocess.CalledProcessError(3, command)

    monkeypatch.setattr(entry.subprocess, "run", run)
    if flag == "1":
        with pytest.raises(entry.subprocess.CalledProcessError):
            entry.main()
    else:
        entry.main()
    assert ("scripts.probe_prepaid_pix_read_only" in events) is (flag == "1")


@pytest.mark.parametrize("value", [True, False, None, {"ready": True}, "true"])
def test_prepaid_probe_exit_requires_exact_true_and_never_prints_provider_details(monkeypatch, capsys, value):
    from scripts import probe_prepaid_pix_read_only as probe

    async def check():
        return value

    monkeypatch.setattr(probe, "check", check)
    assert probe.main() == (0 if value is True else 3)
    output = capsys.readouterr()
    assert output.err == ""
    assert output.out.strip() in {"orion_prepaid_pix_read_only_ready", "orion_prepaid_pix_read_only_not_ready"}


def test_prepaid_probe_exception_is_redacted_and_not_success(monkeypatch, capsys):
    from scripts import probe_prepaid_pix_read_only as probe

    async def check():
        raise RuntimeError("private-provider-body-or-credential")

    monkeypatch.setattr(probe, "check", check)
    assert probe.main() == 3
    assert capsys.readouterr().out.strip() == "orion_prepaid_pix_read_only_probe_failed_closed"


@pytest.mark.asyncio
@pytest.mark.parametrize("original", [None, "0", "1"])
async def test_prepaid_probe_invalid_cipher_fails_before_provider_get_and_restores_flags(monkeypatch, original):
    from scripts import probe_prepaid_pix_read_only as probe
    if original is None:
        monkeypatch.delenv("ORION_PIX_PREPAID_ENABLED", raising=False)
    else:
        monkeypatch.setenv("ORION_PIX_PREPAID_ENABLED", original)
    monkeypatch.setattr(probe, "pix_cipher", lambda: (_ for _ in ()).throw(RuntimeError("invalid-key-private")))
    monkeypatch.setattr(probe, "CaktoClient", lambda: pytest.fail("No provider client before cipher validation"))
    with pytest.raises(RuntimeError, match="invalid-key-private"):
        await probe.check()
    assert probe.os.environ.get("ORION_PIX_PREPAID_ENABLED") == original


@pytest.mark.parametrize("missing", [None, *range(len(entry.READONLY_PREDECESSORS))])
def test_each_original_0072_predecessor_contract_is_still_enforced(monkeypatch, missing):
    events = []
    for index, (name, method) in enumerate(entry.READONLY_PREDECESSORS):
        module = importlib.import_module("scripts.backup_and_migrate_" + name)
        monkeypatch.setattr(module, method, lambda _, i=index: events.append(i) or i != missing)
        monkeypatch.setattr(module, "main", lambda: pytest.fail("Read-only verification must not call migration entry"))
    assert entry.predecessors_ready("synthetic") is (missing is None)
    assert events == list(range(len(entry.READONLY_PREDECESSORS) if missing is None else missing + 1))


def test_railway_predeploy_and_runtime_image_include_the_reviewed_release():
    railway = json.loads((ROOT / "railway.json").read_text(encoding="utf-8"))
    assert railway["deploy"]["preDeployCommand"] == "python -m scripts.predeploy_one_click_links"
    docker = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY scripts/ ./scripts/" in docker and "COPY alembic/ ./alembic/" in docker
