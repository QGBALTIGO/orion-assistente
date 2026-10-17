from copy import deepcopy
import hashlib
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from scripts import one_time_business_migration as backup
from scripts import one_time_fulfillment_migration as ops


def test_full_source_contract_is_exact_and_original_defaults_are_unchanged():
    assert len(backup.LEGACY_TABLES) == 33 and len(ops.TABLES) == 46
    backup.approved_source_contract(backup.SOURCE, backup.LEGACY_TABLES)
    backup.approved_source_contract(ops.SOURCE, ops.TABLES)
    assert backup.SOURCE == "0009_email_otp" and backup.TARGET == ops.SOURCE
    import inspect
    for function in (backup.owner_preflight, backup.capture_dump, backup.verify_restore):
        signature = inspect.signature(function)
        assert signature.parameters["source"].default == backup.SOURCE
        assert signature.parameters["tables"].default == backup.LEGACY_TABLES


@pytest.mark.parametrize("source,tables", [
    (ops.SOURCE, backup.LEGACY_TABLES), (backup.SOURCE, ops.TABLES),
    (ops.TARGET, ops.TABLES), (ops.SOURCE, ops.TABLES - {"commercial_orders"}),
    (ops.SOURCE, ops.TABLES | {"unreviewed_table"}),
])
def test_partial_or_unknown_backup_contract_is_rejected(source, tables):
    with pytest.raises(backup.Stop, match="unapproved_source_contract"):
        backup.approved_source_contract(source, tables)


def test_only_the_byte_pinned_single_upgrade_is_loaded():
    migration = ops.reviewed_migration()
    assert migration.revision == ops.TARGET and migration.down_revision == ops.SOURCE
    assert migration.branch_labels is migration.depends_on is None


def test_modified_migration_is_refused_before_module_execution(monkeypatch, tmp_path):
    directory = tmp_path / "alembic" / "versions"
    directory.mkdir(parents=True)
    (directory / (ops.TARGET + ".py")).write_text("raise AssertionError('must never execute')", encoding="utf-8")
    monkeypatch.setattr(backup, "ROOT", tmp_path)
    with pytest.raises(backup.Stop, match="unreviewed_fulfillment_migration"):
        ops.reviewed_migration()


def test_migration_hash_allows_only_line_ending_equivalence(monkeypatch, tmp_path):
    original = (backup.ROOT / "alembic" / "versions" / (ops.TARGET + ".py")).read_bytes().replace(b"\r\n", b"\n")
    directory = tmp_path / "alembic" / "versions"
    directory.mkdir(parents=True)
    (directory / (ops.TARGET + ".py")).write_bytes(original.replace(b"\n", b"\r\n"))
    monkeypatch.setattr(backup, "ROOT", tmp_path)
    assert ops.reviewed_migration().revision == ops.TARGET


def install_execute_phases(monkeypatch, *, failing_phase=None):
    phases = []
    class Context:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
    class Engine:
        def begin(self):
            return Context()
        def dispose(self):
            phases.append("dispose")
    def phase(name):
        phases.append(name)
        if failing_phase == name:
            raise backup.Stop("synthetic_" + name + "_failed")
    def contract(options):
        assert options == {"source": ops.SOURCE, "tables": ops.TABLES}
    def preflight(*args, **options):
        contract(options); phase("preflight"); return {"major": 16}
    def capture(engine, settings, path, **options):
        contract(options); phase("capture"); path.write_bytes(b"PGDMPsynthetic")
        return {name: 1 for name in ops.TABLES}, "snapshot_fingerprint"
    def durable(settings, envelope, client):
        phase("readback")
        return backup.decrypt_dump(envelope, settings.token, settings.run_id), hashlib.sha256(envelope).hexdigest()
    def restore(*args, **options):
        contract(options); phase("restore")
        assert Path(args[3]).read_bytes() == b"PGDMPsynthetic"
    def apply(engine, settings, **options):
        phase("apply")
        assert options == {"expected_catalog": "snapshot_fingerprint", "expected_security": {"synthetic": True}}
        return {name: 1 for name in ops.TABLES}
    monkeypatch.setattr(ops, "reviewed_migration", lambda: phase("reviewed"))
    monkeypatch.setattr(backup, "engine_for", lambda _: Engine())
    monkeypatch.setattr(backup, "owner_preflight", preflight)
    monkeypatch.setattr(ops, "source_only", lambda _: phase("source"))
    monkeypatch.setattr(ops, "security_structure", lambda _: {"synthetic": True})
    monkeypatch.setattr(backup, "client_major", lambda _: 16)
    monkeypatch.setattr(backup, "capture_dump", capture)
    monkeypatch.setattr(backup, "durable_backup", durable)
    monkeypatch.setattr(backup, "verify_restore", restore)
    monkeypatch.setattr(ops, "apply_exact_migration", apply)
    monkeypatch.setattr(ops.httpx, "Client", lambda **kwargs: Context())
    settings = SimpleNamespace(url=None, token="only-ci-synthetic-" + "x" * 40, run_id=str(uuid4()))
    return phases, settings


def test_complete_readback_decrypt_real_restore_gate_precedes_source_ddl(monkeypatch):
    phases, settings = install_execute_phases(monkeypatch)
    result = ops.execute(settings)
    assert phases == ["reviewed", "preflight", "source", "capture", "readback", "restore", "apply", "dispose"]
    assert result["restore_verified"] is True and result["revision"] == ops.TARGET
    assert len(result["backup_counts"]) == len(result["preserved_counts"]) == 46
    assert result["backup_id"] == settings.run_id
    assert "token" not in result and "url" not in result


@pytest.mark.parametrize("failure", ["preflight", "source", "capture", "readback", "restore"])
def test_every_pre_ddl_gate_fails_closed_without_retry(monkeypatch, failure):
    phases, settings = install_execute_phases(monkeypatch, failing_phase=failure)
    with pytest.raises(backup.Stop, match="synthetic_" + failure + "_failed"):
        ops.execute(settings)
    assert "apply" not in phases and phases.count(failure) == 1 and phases[-1] == "dispose"


def test_pg16_is_required_even_if_client_and_server_share_another_major(monkeypatch):
    phases, settings = install_execute_phases(monkeypatch)
    monkeypatch.setattr(backup, "owner_preflight", lambda *a, **k: {"major": 17})
    monkeypatch.setattr(backup, "client_major", lambda _: 17)
    with pytest.raises(backup.Stop, match="postgres_major_mismatch"):
        ops.execute(settings)
    assert "capture" not in phases and "apply" not in phases


def test_cli_uses_same_sealed_settings_and_masks_unexpected_errors(monkeypatch, capsys):
    sentinel = object()
    monkeypatch.setattr(backup.Settings, "from_environment", lambda: sentinel)
    def execute(settings):
        assert settings is sentinel
        raise RuntimeError("synthetic secret and database URL must remain private")
    monkeypatch.setattr(ops, "execute", execute)
    assert ops.main() == 1
    assert capsys.readouterr().out.strip() == '{"status": "stopped", "code": "operation_failed_no_details"}'


def projection_fixture(monkeypatch):
    old_constraint = ["commercial_quotes", "ck_quote_amount", "c", True, False, False, False, "CHECK ((total_cents >= 0))"]
    constraints = [old_constraint,
        ["commercial_quotes", "ck_quote_fulfillment", "c", True, False, False, False, "CHECK (synthetic_reviewed_predicate)"],
        ["commercial_quotes", "fk_quote_pickup_appointment", "f", True, False, False, False,
         "FOREIGN KEY (tenant_id, pickup_appointment_id) REFERENCES appointments(tenant_id, id) ON DELETE RESTRICT"]]
    rows = [[name, True, True, [["id", "uuid", True, None]], [], [], []] for name in sorted(ops.TABLES)]
    quote = next(row for row in rows if row[0] == "commercial_quotes")
    quote[3].extend([["fulfillment_mode", "character varying(16)", False, None],
                    ["pickup_appointment_id", "uuid", False, None], ["fulfillment_snapshot", "jsonb", False, None]])
    quote[4] = [row[7] for row in constraints]
    quote[5] = ["CREATE INDEX existing_index ON public.commercial_quotes USING btree (id)",
                "CREATE INDEX ix_quote_pickup_appointment ON public.commercial_quotes USING btree (tenant_id, pickup_appointment_id) WHERE (pickup_appointment_id IS NOT NULL)"]
    class Result:
        def one(self):
            return (True, True, "app_migrator", None, None)
        def scalar_one(self):
            return 0
    conn = SimpleNamespace(execute=lambda *a: Result(), exec_driver_sql=lambda *a: Result())
    monkeypatch.setattr(backup, "catalog_structure", lambda *a: rows)
    monkeypatch.setattr(ops, "named_constraints", lambda *a: constraints)
    return conn, rows, quote, constraints


def test_projection_preserves_all_old_fields_and_constraint_names_without_mutating_input(monkeypatch):
    conn, rows, quote, constraints = projection_fixture(monkeypatch)
    original = deepcopy(rows)
    projected, old_constraints = ops.projected_catalog(conn)
    assert rows == original
    result = next(row for row in projected if row[0] == "commercial_quotes")
    assert result[3] == [["id", "uuid", True, None]]
    assert result[4] == [constraints[0][7]] and result[5] == [quote[5][0]]
    assert old_constraints == [constraints[0]]
    assert [row for row in projected if row[0] != "commercial_quotes"] == [row for row in original if row[0] != "commercial_quotes"]


@pytest.mark.parametrize("tamper", ["column_type", "column_default", "column_not_null", "constraint_alias", "foreign_target", "index_predicate", "index_duplicate"])
def test_projection_never_hides_unverified_additions(monkeypatch, tamper):
    conn, rows, quote, constraints = projection_fixture(monkeypatch)
    if tamper == "column_type":
        quote[3][1][1] = "character varying(32)"
    elif tamper == "column_default":
        quote[3][1][3] = "'pickup'::character varying"
    elif tamper == "column_not_null":
        quote[3][1][2] = True
    elif tamper == "constraint_alias":
        constraints[1][1] += "_unreviewed"
    elif tamper == "foreign_target":
        constraints[2][7] = constraints[2][7].replace("appointments(", "other_appointments(")
    elif tamper == "index_predicate":
        quote[5][1] = quote[5][1].replace("IS NOT NULL", "IS NULL")
    else:
        quote[5].append(quote[5][1])
    with pytest.raises(backup.Stop):
        ops.projected_catalog(conn)
