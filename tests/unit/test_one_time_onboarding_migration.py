"""Fail-closed controls for the explicitly authorized backup/rehearsal operation."""
from dataclasses import replace
import json
from pathlib import Path
from uuid import uuid4

import pytest
from scripts import one_time_onboarding_migration as op

def configuration(monkeypatch, **changes):
    values = {"ORION_RUN_ONBOARDING_MIGRATION": "1", "ORION_MIGRATION_SWITCH_ROLE": "1",
        "MIGRATION_DATABASE_URL": "postgresql+psycopg://postgres:synthetic-only@postgres.railway.internal/railway",
        "ORION_EXPECTED_DATABASE_HOST": "postgres.railway.internal", "ORION_EXPECTED_DATABASE_NAME": "railway",
        "ORION_ONBOARDING_MIGRATION_RUN_ID": str(uuid4()),
        "QR_BRIDGE_URL": "http://orion-bridge.railway.internal:3000",
        "QR_BRIDGE_TOKEN": "synthetic-backup-only-000000000000000000000000000000"}
    values.update(changes)
    for name, value in values.items():
        if value is None: monkeypatch.delenv(name, raising=False)
        else: monkeypatch.setenv(name, value)
    return values

@pytest.mark.parametrize("name,value,reason", [
    ("ORION_RUN_ONBOARDING_MIGRATION", "0", "explicit_flag_required"),
    ("ORION_RUN_ONBOARDING_MIGRATION", "2", "explicit_flag_required"),
    ("ORION_MIGRATION_SWITCH_ROLE", "0", "role_switch_required"),
    ("MIGRATION_DATABASE_URL", None, "invalid_configuration"),
    ("ORION_ONBOARDING_MIGRATION_RUN_ID", "not-uuid", "invalid_configuration"),
    ("ORION_ONBOARDING_MIGRATION_RUN_ID", "00000000-0000-0000-0000-000000000000", "canonical_run_id_required"),
    ("ORION_EXPECTED_DATABASE_HOST", "different.railway.internal", "database_identity_required"),
    ("ORION_EXPECTED_DATABASE_NAME", "different", "database_identity_required"),
    ("MIGRATION_DATABASE_URL", "sqlite:///test", "postgres_required"),
    ("MIGRATION_DATABASE_URL", "postgresql://postgres@postgres.railway.internal/railway", "owner_url_required"),
    ("MIGRATION_DATABASE_URL", "postgresql://postgres:synthetic@postgres.railway.internal/railway?options=x", "unsupported_database_option"),
    ("QR_BRIDGE_URL", "https://external.example", "private_bridge_required"),
    ("QR_BRIDGE_URL", "http://bridge.railway.internal:3000/path", "private_bridge_required"),
    ("QR_BRIDGE_URL", "http://bridge.railway.internal:3000?key=synthetic", "private_bridge_required"),
    ("QR_BRIDGE_URL", "http://user:password@bridge.railway.internal:3000", "private_bridge_required"),
    ("QR_BRIDGE_URL", "http://bridge.railway.internal:3001", "private_bridge_required"),
    ("QR_BRIDGE_URL", "http://localhost:3000", "private_bridge_required"),
    ("QR_BRIDGE_TOKEN", "short", "backup_key_required"),
])
def test_no_unapproved_identity_or_transport_can_start_the_operation(monkeypatch, name, value, reason):
    configuration(monkeypatch, **{name:value})
    with pytest.raises(op.Stop, match=reason): op.Settings.from_environment()

def test_credential_settings_are_not_exposed_by_repr(monkeypatch):
    values=configuration(monkeypatch)
    settings=op.Settings.from_environment()
    assert "synthetic-only" not in repr(settings) and values["QR_BRIDGE_TOKEN"] not in repr(settings)
    assert settings.url.drivername == "postgresql+psycopg"

def test_disabled_does_no_database_or_backup_io(monkeypatch,capsys):
    monkeypatch.delenv("ORION_RUN_ONBOARDING_MIGRATION",raising=False)
    monkeypatch.setattr(op,"execute",lambda *_:pytest.fail("Disabled operation performed I/O"))
    assert op.main()==0
    assert json.loads(capsys.readouterr().out)=={"status":"disabled"}

def test_failure_log_never_prints_exception_text_or_configuration(monkeypatch,capsys):
    configuration(monkeypatch)
    def fail(_):raise RuntimeError("synthetic-only password /secret/path sensitive-row")
    monkeypatch.setattr(op,"execute",fail)
    assert op.main()==1
    assert json.loads(capsys.readouterr().out)=={"status":"stopped","code":"operation_failed_no_details"}

def test_unknown_stop_reason_is_redacted(monkeypatch,capsys):
    configuration(monkeypatch)
    def fail(_):raise op.Stop("Contains sensitive value")
    monkeypatch.setattr(op,"execute",fail)
    assert op.main()==1 and "sensitive" not in capsys.readouterr().out

def test_reviewed_files_form_exact_unbranched_chain():
    modules=op.reviewed_migrations()
    assert [m.revision for m in modules]==[r for r,_,_ in op.MIGRATIONS]
    assert modules[0].down_revision==op.SOURCE and modules[-1].revision==op.TARGET

@pytest.mark.parametrize("changed", range(4))
def test_every_changed_migration_byte_is_refused_before_database_io(tmp_path, monkeypatch, changed):
    directory=tmp_path/"alembic"/"versions";directory.mkdir(parents=True)
    for index,(revision,_,_) in enumerate(op.MIGRATIONS):
        content=(op.ROOT/"alembic"/"versions"/(revision+".py")).read_bytes().replace(b"\r\n",b"\n")
        if index==changed: content+=b"\n# unreviewed change\n"
        (directory/(revision+".py")).write_bytes(content)
    monkeypatch.setattr(op,"ROOT",tmp_path)
    monkeypatch.setattr(op.backup,"engine_for",lambda *_:pytest.fail("I/O before all reviewed bytes validated"))
    with pytest.raises(op.Stop,match="unreviewed_onboarding_migration"):
        op.execute(object())

def test_missing_migration_is_refused_before_database_io(tmp_path,monkeypatch):
    monkeypatch.setattr(op,"ROOT",tmp_path)
    monkeypatch.setattr(op.backup,"engine_for",lambda *_:pytest.fail("Unexpected database I/O"))
    with pytest.raises(op.Stop,match="reviewed_migration_missing"):op.execute(object())

def test_source_application_requires_exact_rehearsal_proof(monkeypatch):
    monkeypatch.setattr(op,"reviewed_migrations",lambda:[])
    settings=type("Settings",(),{"url":type("URL",(),{"database":"production"})(),"run_id":str(uuid4())})()
    with pytest.raises(op.Stop,match="rehearsal_proof_required"):
        op.apply_exact_migrations(None,settings,{})

def test_sql_identifiers_cannot_come_from_database_or_user_input():
    assert op.SOURCE_TABLES==frozenset(op.agenda.TABLES)
    assert op.TARGET_TABLES==op.SOURCE_TABLES|op.NEW_TABLES
    assert not op.SOURCE_TABLES.intersection(op.NEW_TABLES)
    assert "alembic_version" in op.SOURCE_TABLES

def test_only_reviewed_associative_financial_check_alias_is_normalized():
    nested = "CHECK ((((amount_cents >= 1) AND (amount_cents <= 1000000000)) AND ((currency)::text = 'BRL'::text)))"
    flat = "CHECK (((amount_cents >= 1) AND (amount_cents <= 1000000000) AND ((currency)::text = 'BRL'::text)))"
    def catalog(expression, name="financial_entries"):
        return [[name, True, True, [], [expression], [], []]]
    assert op.canonical_catalog(catalog(nested)) == op.canonical_catalog(catalog(flat))
    for altered in (nested.replace("1000000000", "999999999"), nested.replace(" AND ", " OR ", 1),
                    nested.replace("'BRL'", "'USD'"), nested.replace(">= 1", "> 1")):
        assert op.canonical_catalog(catalog(altered)) != op.canonical_catalog(catalog(flat))
    assert op.canonical_catalog(catalog(nested, "unrelated")) != op.canonical_catalog(catalog(flat, "unrelated"))


@pytest.mark.parametrize("table,source,restored", op.RESTORE_CHECK_ALIASES)
def test_every_observed_restore_alias_preserves_bounds_boolean_operators_and_scope(table, source, restored):
    def catalog(name, expression): return [[name, True, True, [], [expression], [], []]]
    expected = op.canonical_catalog(catalog(table, restored))
    assert op.canonical_catalog(catalog(table, source)) == expected
    for changed in (source.replace(" AND ", " OR ", 1), source.replace(">=", ">", 1), source + " "):
        assert changed != source
        assert op.canonical_catalog(catalog(table, changed)) != expected
    assert op.canonical_catalog(catalog("unrelated", source)) != op.canonical_catalog(catalog("unrelated", restored))


@pytest.mark.parametrize("change", ["run", "database", "snapshot"])
def test_restored_proof_cannot_authorize_another_database_run_or_snapshot(monkeypatch, change):
    monkeypatch.setattr(op, "reviewed_migrations", lambda: [])
    run_id = str(uuid4()); database = op.backup.restore_name(run_id); expected = {"synthetic": "sealed-source"}
    receipt = op._RestoredSourceProof(run_id, database, expected)
    settings = type("Settings", (), {"run_id": run_id, "url": type("URL", (), {"database": database})()})()
    if change == "run": receipt = replace(receipt, run_id=str(uuid4()))
    if change == "database": receipt = replace(receipt, database="production")
    if change == "snapshot": receipt = replace(receipt, snapshot={"synthetic": "changed"})
    with pytest.raises(op.Stop, match="restored_source_proof_mismatch"):
        op.apply_exact_migrations(None, settings, expected, restored_proof=receipt)
