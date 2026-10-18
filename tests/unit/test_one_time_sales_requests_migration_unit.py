"""Configuration, reviewed bytes and output boundaries of the additive runner."""
import hashlib
import json

import pytest
from sqlalchemy.engine import make_url

from scripts import one_time_management_migration as previous
from scripts import one_time_sales_requests_migration as ops


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("ORION_RUN_SALES_REQUESTS_MIGRATION", "1")
    monkeypatch.setenv("ORION_MIGRATION_SWITCH_ROLE", "1")
    monkeypatch.setenv("MIGRATION_DATABASE_URL", "postgresql://postgres:synthetic-secret@127.0.0.1:55432/synthetic")
    monkeypatch.setenv("ORION_EXPECTED_DATABASE_HOST", "127.0.0.1")
    monkeypatch.setenv("ORION_EXPECTED_DATABASE_NAME", "synthetic")


def test_reviewed_upgrade_and_exact_source_contract():
    module = ops.reviewed_migration()
    assert module.revision == "0018_sales_requests"
    assert module.down_revision == "0017_financial_settlements"
    assert ops.SOURCE_TABLES == previous.TARGET_TABLES and len(ops.SOURCE_TABLES) == 66
    assert ops.TARGET_TABLES - ops.SOURCE_TABLES == {"sales_requests"}
    assert len(ops.TARGET_SCHEMA_SHA256) == 64


def test_configuration_never_exposes_credential_in_repr(configured):
    settings = ops.Settings.from_environment()
    assert settings.expected_database == "synthetic" and settings.switch_role is True
    assert "synthetic-secret" not in repr(settings)
    assert "postgresql" not in repr(settings)


@pytest.mark.parametrize("key,value,code", [
    ("ORION_RUN_SALES_REQUESTS_MIGRATION", "0", "explicit_flag_required"),
    ("ORION_RUN_SALES_REQUESTS_MIGRATION", "yes", "explicit_flag_required"),
    ("ORION_EXPECTED_DATABASE_HOST", "another-host.invalid", "database_identity_required"),
    ("ORION_EXPECTED_DATABASE_HOST", "", "database_identity_required"),
    ("ORION_EXPECTED_DATABASE_NAME", "another_database", "database_identity_required"),
    ("ORION_EXPECTED_DATABASE_NAME", "", "database_identity_required"),
    ("ORION_MIGRATION_SWITCH_ROLE", "yes", "invalid_role_switch"),
    ("MIGRATION_DATABASE_URL", "sqlite:///synthetic", "postgres_required"),
    ("MIGRATION_DATABASE_URL", "postgresql://postgres@/synthetic", "database_url_required"),
    ("MIGRATION_DATABASE_URL", "not a URL containing a secret", "invalid_configuration"),
    ("MIGRATION_DATABASE_URL", "postgresql://postgres@127.0.0.1/synthetic?options=-c+role%3Dpostgres", "unsupported_database_option"),
    ("MIGRATION_DATABASE_URL", "postgresql://postgres@127.0.0.1/synthetic?host=another-host.invalid", "unsupported_database_option"),
])
def test_unsafe_configuration_stops_before_engine(configured, monkeypatch, key, value, code):
    monkeypatch.setenv(key, value)
    monkeypatch.setattr(ops, "engine_for", lambda *_: pytest.fail("No connection before valid configuration"))
    with pytest.raises(ops.Stop, match="^" + code + "$"):
        ops.Settings.from_environment()


def test_known_transport_options_preserved_and_role_switch_can_be_disabled(configured, monkeypatch):
    monkeypatch.setenv("MIGRATION_DATABASE_URL", "postgresql://app_migrator@127.0.0.1:55432/synthetic?sslmode=verify-full&sslrootcert=%2Ftmp%2Fsynthetic.pem")
    monkeypatch.setenv("ORION_MIGRATION_SWITCH_ROLE", "0")
    settings = ops.Settings.from_environment()
    assert settings.switch_role is False
    assert settings.url.query == {"sslmode": "verify-full", "sslrootcert": "/tmp/synthetic.pem"}


def test_direct_settings_identity_is_also_checked_before_connection(monkeypatch):
    settings = ops.Settings(make_url("postgresql://postgres@127.0.0.1/source"), "127.0.0.1", "other", True)
    monkeypatch.setattr(ops, "engine_for", lambda *_: pytest.fail("No connection for mismatched settings"))
    with pytest.raises(ops.Stop, match="database_identity_required"):
        ops.execute(settings)


def test_altered_module_is_rejected_without_executing_its_python(configured, monkeypatch, tmp_path):
    directory = tmp_path / "alembic" / "versions"
    directory.mkdir(parents=True)
    marker = tmp_path / "must-not-exist"
    body = f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n"
    (directory / (ops.TARGET + ".py")).write_text(body)
    monkeypatch.setattr(ops, "ROOT", tmp_path)
    monkeypatch.setattr(ops, "engine_for", lambda *_: pytest.fail("Hash verification must precede DB access"))
    with pytest.raises(ops.Stop, match="unreviewed_sales_migration"):
        ops.execute(ops.Settings.from_environment())
    assert not marker.exists()


def test_missing_migration_is_safe(monkeypatch, tmp_path):
    monkeypatch.setattr(ops, "ROOT", tmp_path)
    with pytest.raises(ops.Stop, match="reviewed_migration_missing"):
        ops.reviewed_migration()


def test_crlf_checkout_executes_the_same_reviewed_bytes(monkeypatch, tmp_path):
    body = (ops.ROOT / "alembic" / "versions" / (ops.TARGET + ".py")).read_bytes()
    directory = tmp_path / "alembic" / "versions"
    directory.mkdir(parents=True)
    (directory / (ops.TARGET + ".py")).write_bytes(body.replace(b"\n", b"\r\n"))
    monkeypatch.setattr(ops, "ROOT", tmp_path)
    assert ops.reviewed_migration().revision == ops.TARGET


def test_chain_is_checked_even_after_reviewed_bytes_match(monkeypatch, tmp_path):
    body = b"revision='0018_sales_requests'\ndown_revision='unreviewed'\nbranch_labels=None\ndepends_on=None\n"
    directory = tmp_path / "alembic" / "versions"
    directory.mkdir(parents=True)
    (directory / (ops.TARGET + ".py")).write_bytes(body)
    monkeypatch.setattr(ops, "ROOT", tmp_path)
    monkeypatch.setattr(ops, "MIGRATION_SHA256", hashlib.sha256(body).hexdigest())
    with pytest.raises(ops.Stop, match="unreviewed_migration_chain"):
        ops.reviewed_migration()


@pytest.mark.parametrize("flag", [None, "0"])
def test_disabled_launcher_does_not_parse_credentials_or_connect(monkeypatch, capsys, flag):
    if flag is None:
        monkeypatch.delenv("ORION_RUN_SALES_REQUESTS_MIGRATION", raising=False)
    else:
        monkeypatch.setenv("ORION_RUN_SALES_REQUESTS_MIGRATION", flag)
    monkeypatch.setenv("MIGRATION_DATABASE_URL", "invalid URL with synthetic-secret")
    monkeypatch.setattr(ops, "execute", lambda *_: pytest.fail("Disabled operation must not connect"))
    assert ops.main() == 0
    assert json.loads(capsys.readouterr().out) == {"ok": True, "status": "disabled"}


@pytest.mark.parametrize("error", [
    RuntimeError("postgresql://user:synthetic-secret@host/db customer@example.test"),
    ops.Stop("postgresql://user:synthetic-secret@host/db customer@example.test"),
])
def test_unexpected_failures_never_print_database_exception_or_contact(configured, monkeypatch, capsys, error):
    def fail(_):
        raise error
    monkeypatch.setattr(ops, "execute", fail)
    assert ops.main() == 1
    output = capsys.readouterr()
    assert json.loads(output.out) == {"ok": False, "error": "operation_failed"}
    assert not output.err
    assert "synthetic-secret" not in output.out and "customer@example.test" not in output.out


def test_fixed_failure_phase_is_printed_without_traceback(configured, monkeypatch, capsys):
    def fail(_):
        raise ops.Stop("unexpected_sales_schema")
    monkeypatch.setattr(ops, "execute", fail)
    assert ops.main() == 1
    assert json.loads(capsys.readouterr().out) == {"ok": False, "error": "unexpected_sales_schema"}


def test_success_is_machine_readable(configured, monkeypatch, capsys):
    result = {"ok": True, "status": "applied", "source": ops.SOURCE, "revision": ops.TARGET}
    monkeypatch.setattr(ops, "execute", lambda _: result)
    assert ops.main() == 0
    assert json.loads(capsys.readouterr().out) == result
