"""Fail-closed configuration and immutable-source checks for the agenda deploy."""
import hashlib
import json
from pathlib import Path

import pytest

from scripts import one_time_agenda_migration as ops


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("ORION_RUN_AGENDA_MIGRATION", "1")
    monkeypatch.setenv("ORION_MIGRATION_SWITCH_ROLE", "1")
    monkeypatch.setenv("MIGRATION_DATABASE_URL", "postgresql://postgres:synthetic-secret@127.0.0.1:55432/synthetic_agenda")
    monkeypatch.setenv("ORION_EXPECTED_DATABASE_HOST", "127.0.0.1")
    monkeypatch.setenv("ORION_EXPECTED_DATABASE_NAME", "synthetic_agenda")


def test_exact_reviewed_additive_chain():
    module = ops.reviewed_migration()
    assert module.revision == "0019_appointment_lifecycle"
    assert module.down_revision == "0018_sales_requests"
    assert ops.TABLES == ops.previous.TARGET_TABLES and len(ops.TABLES) == 67
    for value in (ops.MIGRATION_SHA256, ops.SOURCE_HISTORY_SHA256, ops.TARGET_HISTORY_SHA256):
        assert len(value) == 64 and all(ch in "0123456789abcdef" for ch in value)


@pytest.mark.parametrize("key,value,code", [
    ("ORION_RUN_AGENDA_MIGRATION", "0", "explicit_flag_required"),
    ("ORION_RUN_AGENDA_MIGRATION", "yes", "explicit_flag_required"),
    ("ORION_MIGRATION_SWITCH_ROLE", "0", "role_switch_required"),
    ("ORION_MIGRATION_SWITCH_ROLE", "", "role_switch_required"),
    ("ORION_EXPECTED_DATABASE_HOST", "another.invalid", "database_identity_required"),
    ("ORION_EXPECTED_DATABASE_NAME", "other", "database_identity_required"),
    ("MIGRATION_DATABASE_URL", "sqlite:///synthetic", "postgres_required"),
    ("MIGRATION_DATABASE_URL", "postgresql://postgres@127.0.0.1/synthetic_agenda?options=-c+role%3Dpostgres", "unsupported_database_option"),
])
def test_wrong_configuration_is_rejected_before_connect(configured, monkeypatch, key, value, code):
    monkeypatch.setenv(key, value)
    monkeypatch.setattr(ops, "engine_for", lambda *_: pytest.fail("Must not connect with rejected configuration"))
    with pytest.raises(ops.Stop, match="^" + code + "$"):
        ops.Settings.from_environment()


def test_settings_do_not_expose_credentials(configured):
    settings = ops.Settings.from_environment()
    assert settings.switch_role is True
    assert "synthetic-secret" not in repr(settings)
    assert "postgresql" not in repr(settings)


@pytest.mark.parametrize("flag", [None, "0"])
def test_inactive_flag_does_not_read_configuration_or_connect(monkeypatch, capsys, flag):
    if flag is None:
        monkeypatch.delenv("ORION_RUN_AGENDA_MIGRATION", raising=False)
    else:
        monkeypatch.setenv("ORION_RUN_AGENDA_MIGRATION", flag)
    monkeypatch.setattr(ops.Settings, "from_environment", lambda: pytest.fail("Disabled operation must not inspect configuration"))
    assert ops.main() == 0
    assert json.loads(capsys.readouterr().out) == {"ok": True, "status": "disabled"}


def test_modified_migration_is_rejected_before_execution_or_connection(configured, monkeypatch, tmp_path):
    original = ops.ROOT / "alembic" / "versions" / (ops.TARGET + ".py")
    target = tmp_path / "alembic" / "versions" / original.name
    target.parent.mkdir(parents=True)
    marker = tmp_path / "must-not-exist"
    target.write_bytes(original.read_bytes() + ("\nfrom pathlib import Path\nPath(" + repr(str(marker)) + ").write_text('executed')\n").encode())
    monkeypatch.setattr(ops, "ROOT", tmp_path)
    monkeypatch.setattr(ops, "engine_for", lambda *_: pytest.fail("Tampered Python must not connect"))
    with pytest.raises(ops.Stop, match="unreviewed_agenda_migration"):
        ops.execute(ops.Settings.from_environment())
    assert not marker.exists()


def test_crlf_transport_only_keeps_reviewed_bytes(monkeypatch, tmp_path):
    source = ops.ROOT / "alembic" / "versions" / (ops.TARGET + ".py")
    body = source.read_bytes().replace(b"\r\n", b"\n")
    target = tmp_path / "alembic" / "versions" / source.name
    target.parent.mkdir(parents=True)
    target.write_bytes(body.replace(b"\n", b"\r\n"))
    monkeypatch.setattr(ops, "ROOT", tmp_path)
    assert hashlib.sha256(body).hexdigest() == ops.MIGRATION_SHA256
    assert ops.reviewed_migration().revision == ops.TARGET


@pytest.mark.parametrize("error", [RuntimeError("synthetic-secret@private-host"), ops.Stop("secret is not a safe code")])
def test_operational_failure_is_sanitized(configured, monkeypatch, capsys, error):
    def fail(*_):
        raise error
    monkeypatch.setattr(ops, "execute", fail)
    assert ops.main() == 1
    assert json.loads(capsys.readouterr().out) == {"ok": False, "error": "operation_failed"}
