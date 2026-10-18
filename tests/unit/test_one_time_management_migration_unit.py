"""The management executor must keep its own sealed, byte-reviewed contract."""
import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.engine import make_url

from scripts import one_time_business_migration as backup
from scripts import one_time_fulfillment_migration as fulfillment
from scripts import one_time_management_migration as ops


@pytest.fixture
def settings():
    return ops.Settings(
        make_url("postgresql+psycopg://owner:synthetic-management-password@db.railway.internal:5432/synthetic_management"),
        "http://bridge.railway.internal:3000", "synthetic-management-token-" + "x" * 40,
        str(uuid4()),
    )


@pytest.fixture
def configured(monkeypatch, settings):
    values = {
        "ORION_RUN_MANAGEMENT_MIGRATION": "1",
        "ORION_MANAGEMENT_MIGRATION_RUN_ID": settings.run_id,
        "ORION_MIGRATION_SWITCH_ROLE": "1",
        "MIGRATION_DATABASE_URL": settings.url.render_as_string(hide_password=False),
        "ORION_EXPECTED_DATABASE_HOST": settings.url.host,
        "ORION_EXPECTED_DATABASE_NAME": settings.url.database,
        "QR_BRIDGE_URL": settings.bridge_url,
        "QR_BRIDGE_TOKEN": settings.token,
        # The earlier executor's flag must neither enable nor be required by this one.
        "ORION_RUN_BUSINESS_MIGRATION": "0",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    return settings


def test_management_contract_does_not_broaden_legacy_backup_allowlists():
    assert ops.SOURCE == "0012_commercial_fulfillment"
    assert ops.TARGET == "0017_financial_settlements"
    assert ops.SOURCE_TABLES == fulfillment.TABLES == backup.LEGACY_TABLES | backup.NEW_TABLES
    assert len(ops.SOURCE_TABLES) == 46 and len(ops.NEW_TABLES) == 20
    assert ops.TARGET_TABLES == ops.SOURCE_TABLES | ops.NEW_TABLES
    assert not ops.SOURCE_TABLES.intersection(ops.NEW_TABLES)
    backup.approved_source_contract(backup.SOURCE, backup.LEGACY_TABLES)
    backup.approved_source_contract(fulfillment.SOURCE, fulfillment.TABLES)
    for revision, tables in ((ops.SOURCE, ops.SOURCE_TABLES), (ops.TARGET, ops.TARGET_TABLES)):
        with pytest.raises(backup.Stop, match="unapproved_source_contract"):
            backup.approved_source_contract(revision, tables)


def test_five_reviewed_migrations_form_only_the_0012_to_0017_chain():
    modules = ops.reviewed_migrations()
    assert [module.revision for module in modules] == [
        "0013_management", "0014_public_catalog", "0015_loyalty_rewards",
        "0016_management_extras", "0017_financial_settlements",
    ]
    previous = ops.SOURCE
    for module in modules:
        assert module.down_revision == previous
        assert module.branch_labels is module.depends_on is None
        previous = module.revision
    assert previous == ops.TARGET


def test_new_explicit_settings_are_sealed_and_their_repr_omits_credentials(configured):
    actual = ops.Settings.from_environment()
    assert actual.url == configured.url and actual.run_id == configured.run_id
    assert actual.bridge_url == configured.bridge_url and actual.token == configured.token
    assert configured.token not in repr(actual)
    assert configured.url.password not in repr(actual)


@pytest.mark.parametrize("key,value", [
    ("ORION_RUN_MANAGEMENT_MIGRATION", "0"),
    ("ORION_MIGRATION_SWITCH_ROLE", "0"),
    ("ORION_EXPECTED_DATABASE_HOST", "another.railway.internal"),
    ("ORION_EXPECTED_DATABASE_NAME", "another_database"),
    ("ORION_MANAGEMENT_MIGRATION_RUN_ID", "invalid-uuid"),
    ("ORION_MANAGEMENT_MIGRATION_RUN_ID", "11111111-1111-1111-8111-111111111111"),
    ("QR_BRIDGE_TOKEN", "short"),
    ("QR_BRIDGE_URL", "https://external.example.test"),
    ("QR_BRIDGE_URL", "http://bridge.railway.internal:3000/path"),
    ("QR_BRIDGE_URL", "http://user:synthetic-password@bridge.railway.internal:3000"),
    ("MIGRATION_DATABASE_URL", "postgresql://owner@db.railway.internal:5432/synthetic_management"),
    ("MIGRATION_DATABASE_URL", "postgresql://owner:synthetic-only@db.railway.internal:5432/synthetic_management?options=-crole%3Dapp_runtime"),
])
def test_configuration_rejects_unsealed_target_or_transport(configured, monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    with pytest.raises(backup.Stop):
        ops.Settings.from_environment()


def test_legacy_enable_flag_cannot_start_management(configured, monkeypatch):
    monkeypatch.setenv("ORION_RUN_BUSINESS_MIGRATION", "1")
    monkeypatch.delenv("ORION_RUN_MANAGEMENT_MIGRATION")
    with pytest.raises(backup.Stop):
        ops.Settings.from_environment()


def copy_reviewed_modules(tmp_path):
    target = tmp_path / "alembic" / "versions"
    target.mkdir(parents=True)
    for module in ops.reviewed_migrations():
        original = Path(module.__file__)
        (target / original.name).write_bytes(original.read_bytes())
    return target


@pytest.mark.parametrize("revision", [
    "0013_management", "0014_public_catalog", "0015_loyalty_rewards",
    "0016_management_extras", "0017_financial_settlements",
])
def test_any_modified_module_stops_before_module_side_effects_or_backup(
    monkeypatch, tmp_path, settings, revision,
):
    target = copy_reviewed_modules(tmp_path)
    marker = tmp_path / "unreviewed-code-executed"
    path = target / (revision + ".py")
    path.write_text(path.read_text() + "\nfrom pathlib import Path\nPath(" + repr(str(marker)) + ").write_text('unexpected')\n")
    # Both are temporary test bindings; the earlier runners and their allowlists stay intact.
    monkeypatch.setattr(ops, "ROOT", tmp_path)
    def forbidden(*args, **kwargs):
        pytest.fail("Unreviewed Python must be rejected before database or backup I/O")
    monkeypatch.setattr(backup, "engine_for", forbidden)
    with pytest.raises(backup.Stop):
        ops.execute(settings)
    assert not marker.exists()


def test_reviewed_hashes_allow_only_line_ending_equivalence(monkeypatch, tmp_path):
    target = copy_reviewed_modules(tmp_path)
    for path in target.glob("*.py"):
        path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    monkeypatch.setattr(ops, "ROOT", tmp_path)
    assert len(ops.reviewed_migrations()) == 5


@pytest.mark.parametrize("failure", ["expected_stop", "unexpected_exception"])
def test_cli_never_prints_configuration_credentials_or_external_error_text(
    monkeypatch, capsys, settings, failure,
):
    monkeypatch.setattr(ops.Settings, "from_environment", lambda: settings)
    def execute(value):
        assert value is settings
        if failure == "expected_stop":
            raise backup.Stop("backup_readback_mismatch")
        raise RuntimeError("synthetic transport failure " + settings.token + " " +
                           settings.url.render_as_string(hide_password=False))
    monkeypatch.setattr(ops, "execute", execute)
    assert ops.main() == 1
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result == {"status": "stopped", "code": (
        "backup_readback_mismatch" if failure == "expected_stop" else "operation_failed_no_details"
    )}
    for private in (settings.token, settings.url.password, settings.url.host,
                    settings.url.database, "synthetic transport failure"):
        assert private not in captured.out + captured.err


def test_cli_success_report_has_no_settings_object(monkeypatch, capsys, settings):
    monkeypatch.setattr(ops.Settings, "from_environment", lambda: settings)
    report = {"status": "complete", "revision": ops.TARGET, "restore_verified": True,
              "backup_sha256": hashlib.sha256(b"synthetic-encrypted-backup").hexdigest()}
    monkeypatch.setattr(ops, "execute", lambda value: report)
    assert ops.main() == 0
    output = capsys.readouterr()
    assert json.loads(output.out) == report and not output.err
