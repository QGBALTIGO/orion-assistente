"""Migration operation: private target, authenticated backup and fail-closed ordering."""
from contextlib import contextmanager
import hashlib
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.engine import make_url

from scripts import one_time_business_migration as ops


def test_pg16_literal_array_cast_canonicalization_preserves_values_and_types():
    source = "CHECK (status = ANY ((ARRAY['hold'::character varying, 'it''s confirmed'::character varying])::text[]))"
    restored = "CHECK (status = ANY (ARRAY[('hold'::character varying)::text, ('it''s confirmed'::character varying)::text]))"
    assert ops.canonical_expression(source) == restored
    assert ops.canonical_expression(restored) == restored
    assert ops.canonical_expression(source.replace("'hold'", "'cancelled'")) != restored
    assert ops.canonical_expression(source.replace("::text[]", "::varchar[]")) != restored
    assert ops.canonical_expression(source.replace("= ANY", "<> ALL")) != restored


@pytest.mark.parametrize("quote", ["'", '"', "$$", "$example$"])
def test_pg16_canonicalization_never_changes_quoted_sql_content(quote):
    fragment = "(ARRAY['hold'::character varying])::text[]"
    quoted = quote + fragment.replace(quote, quote + quote) + quote if quote in {"'", '"'} else quote + fragment + quote
    assert ops.canonical_expression("SELECT " + quoted) == "SELECT " + quoted


def test_pg16_canonicalization_leaves_other_array_expressions_exact():
    for expression in ("(ARRAY[1::integer, 2::integer])::text[]", "(ARRAY[current_user])::text[]",
                       "(ARRAY['a'::varchar])::text[]", "(ARRAY['a'::character varying])::integer[]"):
        assert ops.canonical_expression(expression) == expression


def test_pg16_only_the_exact_existing_buffer_check_can_flatten_and_grouping():
    assert ops.canonical_expression(ops._BUFFER_CHECK_SOURCE) == ops._BUFFER_CHECK_RESTORED
    for changed in (ops._BUFFER_CHECK_SOURCE.replace("240", "241", 1),
                    ops._BUFFER_CHECK_SOURCE.replace("AND", "OR", 1),
                    ops._BUFFER_CHECK_SOURCE.replace("prep_min", "duration_min"),
                    "'" + ops._BUFFER_CHECK_SOURCE + "'"):
        assert ops.canonical_expression(changed) == changed


def test_catalog_aliases_apply_only_to_sql_expression_fields_without_mutating_input():
    expression = ops._BUFFER_CHECK_SOURCE
    original = [[expression, True, True, [[expression, expression, True, expression]],
                 [expression], [expression], [[expression, expression, [expression], expression, expression, expression]]]]
    result = ops.canonical_catalog(original)
    assert result[0][0] == expression
    assert result[0][3][0][:3] == [expression, expression, True]
    assert result[0][3][0][3] == ops._BUFFER_CHECK_RESTORED
    assert result[0][4] == result[0][5] == [ops._BUFFER_CHECK_RESTORED]
    assert result[0][6][0][:4] == [expression, expression, [expression], expression]
    assert result[0][6][0][4:] == [ops._BUFFER_CHECK_RESTORED] * 2
    assert original[0][3][0][3] == expression


@pytest.fixture
def settings():
    return ops.Settings(make_url("postgresql+psycopg://owner:synthetic-only@db.railway.internal:5432/orion_test"),
                        "http://bridge.railway.internal:3000", "synthetic-bridge-token-" + "x" * 32, str(uuid4()))


@pytest.fixture
def configured(monkeypatch, settings):
    for key, value in {
        "ORION_RUN_BUSINESS_MIGRATION": "1", "ORION_MIGRATION_SWITCH_ROLE": "1",
        "MIGRATION_DATABASE_URL": settings.url.render_as_string(hide_password=False),
        "ORION_EXPECTED_DATABASE_HOST": settings.url.host,
        "ORION_EXPECTED_DATABASE_NAME": settings.url.database,
        "ORION_BUSINESS_MIGRATION_RUN_ID": settings.run_id,
        "QR_BRIDGE_URL": settings.bridge_url, "QR_BRIDGE_TOKEN": settings.token,
    }.items():
        monkeypatch.setenv(key, value)
    return settings


def test_explicit_configuration_never_includes_secrets_in_repr(configured):
    result = ops.Settings.from_environment()
    assert result.url == configured.url
    assert configured.token not in repr(result) and "synthetic-only" not in repr(result)


@pytest.mark.parametrize("key,value,code", [
    ("ORION_RUN_BUSINESS_MIGRATION", "0", "explicit_flag_required"),
    ("ORION_MIGRATION_SWITCH_ROLE", "0", "role_switch_required"),
    ("ORION_EXPECTED_DATABASE_NAME", "another", "database_identity_required"),
    ("ORION_EXPECTED_DATABASE_HOST", "another", "database_identity_required"),
    ("ORION_BUSINESS_MIGRATION_RUN_ID", "not-a-uuid", "invalid_configuration"),
    ("ORION_BUSINESS_MIGRATION_RUN_ID", "11111111-1111-1111-8111-111111111111", "canonical_run_id_required"),
    ("QR_BRIDGE_TOKEN", "short", "backup_key_required"),
    ("QR_BRIDGE_URL", "https://bridge.example.com", "private_bridge_required"),
    ("QR_BRIDGE_URL", "http://bridge.railway.internal:3000/forward", "private_bridge_required"),
    ("QR_BRIDGE_URL", "http://user:password@bridge.railway.internal:3000", "private_bridge_required"),
    ("MIGRATION_DATABASE_URL", "postgresql://owner:synthetic-only@db.railway.internal:5432/orion_test?options=-crole%3Dapp_runtime", "unsupported_database_option"),
])
def test_configuration_refuses_unsafe_or_implicit_targets(configured, monkeypatch, key, value, code):
    monkeypatch.setenv(key, value)
    with pytest.raises(ops.Stop, match=code):
        ops.Settings.from_environment()


def test_encryption_is_authenticated_bound_to_uuid_and_hides_dump(settings):
    dump = b"PGDMP synthetic private records and credentials"
    envelope = ops.encrypt_dump(dump, settings.token, settings.run_id)
    assert envelope.startswith(b"ORIONDB1") and dump not in envelope
    assert ops.decrypt_dump(envelope, settings.token, settings.run_id) == dump
    for token, run_id, changed in (
        (settings.token + "wrong", settings.run_id, envelope),
        (settings.token, str(uuid4()), envelope),
        (settings.token, settings.run_id, envelope[:-1] + bytes([envelope[-1] ^ 1])),
    ):
        with pytest.raises(ops.Stop, match="backup_authentication_failed"):
            ops.decrypt_dump(changed, token, run_id)


def test_shared_python_node_envelope_vector():
    # Independently verified with Node crypto.hkdfSync + createDecipheriv.
    value = bytes.fromhex("4f52494f4e444231000102030405060708090a0b48167279b9efb476f3e15248eca2c710a1acd3b3a8d2d3c0ebb32f9ada4f5000535d3579cc875edaec291e367b96")
    assert ops.decrypt_dump(value, "synthetic-only-database-backup-secret-0123456789012345",
        "11111111-1111-4111-8111-111111111111") == b"PGDMP\x01synthetic database dump\n"


def test_plaintext_fake_archive_and_oversized_backup_rejected(settings, monkeypatch):
    with pytest.raises(ops.Stop, match="invalid_envelope"):
        ops.decrypt_dump(b"PGDMP" + b"a" * 70, settings.token, settings.run_id)
    with pytest.raises(ops.Stop, match="invalid_custom_dump"):
        ops.encrypt_dump(b"not-a-custom-archive", settings.token, settings.run_id)
    monkeypatch.setattr(ops, "MAX_BYTES", 50)
    with pytest.raises(ops.Stop, match="backup_too_large"):
        ops.encrypt_dump(b"PGDMP" + b"x" * 20, settings.token, settings.run_id)


@pytest.mark.parametrize("uncertain", [False, True])
def test_post_once_and_exact_readback_prove_durable_encrypted_copy(settings, uncertain):
    dump = b"PGDMP synthetic archive"
    envelope = ops.encrypt_dump(dump, settings.token, settings.run_id)
    calls = []
    def handler(request):
        calls.append(request.method)
        assert request.headers["X-Orion-Bridge-Key"] == settings.token
        assert request.headers["X-Orion-SHA256"] == hashlib.sha256(envelope).hexdigest()
        if request.method == "POST":
            assert request.content == envelope
            if uncertain:
                raise httpx.ReadTimeout("synthetic uncertain reply")
            return httpx.Response(201, json={"id": settings.run_id, "sha256": hashlib.sha256(envelope).hexdigest(), "size": len(envelope)})
        return httpx.Response(200, content=envelope, headers={"content-type": "application/octet-stream"})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        restored, digest = ops.durable_backup(settings, envelope, client)
    assert restored == dump and digest == hashlib.sha256(envelope).hexdigest()
    assert calls == ["POST", "GET"]


@pytest.mark.parametrize("status", [302, 409, 423, 500])
def test_explicit_failed_backup_ack_never_retries_or_reads(settings, status):
    envelope = ops.encrypt_dump(b"PGDMP archive", settings.token, settings.run_id)
    calls = []
    def handler(request):
        calls.append(request.method)
        return httpx.Response(status)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ops.Stop, match="backup_write_not_acknowledged"):
            ops.durable_backup(settings, envelope, client)
    assert calls == ["POST"]


def test_readback_mismatch_prevents_restore(settings):
    envelope = ops.encrypt_dump(b"PGDMP archive", settings.token, settings.run_id)
    def handler(request):
        if request.method == "POST":
            return httpx.Response(201, json={"id": settings.run_id, "sha256": hashlib.sha256(envelope).hexdigest(), "size": len(envelope)})
        return httpx.Response(200, content=envelope + b"changed", headers={"content-type": "application/octet-stream"})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ops.Stop, match="backup_readback_mismatch"):
            ops.durable_backup(settings, envelope, client)


def test_pg_clients_receive_exact_target_without_inherited_options(settings, monkeypatch):
    monkeypatch.setenv("PGOPTIONS", "-c role=wrong")
    monkeypatch.setenv("PGSERVICE", "other-database")
    monkeypatch.setenv("QR_BRIDGE_TOKEN", settings.token)
    monkeypatch.setenv("MIGRATION_DATABASE_URL", "private-url")
    env = ops.pg_environment(settings.url.set(database="exact_test_database"))
    assert env["PGDATABASE"] == "exact_test_database" and env["PGUSER"] == "owner"
    assert env["PGPASSWORD"] == "synthetic-only"
    assert "PGSERVICE" not in env and "role=" not in env["PGOPTIONS"]
    assert "QR_BRIDGE_TOKEN" not in env and "MIGRATION_DATABASE_URL" not in env


@pytest.mark.parametrize("name,created_oid,actual_oid,owner", [
    ("production", 2, 2, 1), ("orion_restore_" + "a" * 32, 1, 1, 1),
    ("orion_restore_" + "a" * 32, 2, 3, 1), ("orion_restore_" + "a" * 32, 2, 2, 7),
])
def test_cleanup_cannot_drop_source_replacement_or_foreign_database(name, created_oid, actual_oid, owner):
    class Connection:
        def execute(self, *_):
            return SimpleNamespace(first=lambda: SimpleNamespace(oid=actual_oid, datdba=owner))
        def exec_driver_sql(self, _):
            pytest.fail("Unsafe DROP attempted")
    with pytest.raises(ops.Stop, match="restore_cleanup_identity_failed"):
        ops.safe_drop_created(Connection(), name, created_oid, 1, 1)


@pytest.mark.parametrize("failing_phase", ["backup", "restore", "none"])
def test_migration_runs_only_after_verified_restore(settings, monkeypatch, failing_phase):
    events = []
    class Engine:
        @contextmanager
        def begin(self):
            yield None
        def dispose(self):
            pass
    monkeypatch.setattr(ops, "engine_for", lambda _: Engine())
    monkeypatch.setattr(ops, "owner_preflight", lambda *_: {"major": 16})
    monkeypatch.setattr(ops, "client_major", lambda _: 16)
    def capture(_, __, path):
        ops.private_write(path, b"PGDMP test")
        events.append("snapshot")
        return {"tenants": 1}, "fingerprint"
    monkeypatch.setattr(ops, "capture_dump", capture)
    def backup(*_):
        events.append("backup")
        if failing_phase == "backup":
            raise ops.Stop("backup_readback_mismatch")
        return b"PGDMP test", "hash"
    monkeypatch.setattr(ops, "durable_backup", backup)
    def restore(*_):
        events.append("restore")
        if failing_phase == "restore":
            raise ops.Stop("restored_count_mismatch")
    monkeypatch.setattr(ops, "verify_restore", restore)
    def migrate(_):
        events.append("migrate")
        return {"tenants": 1}
    monkeypatch.setattr(ops, "apply_exact_migrations", migrate)
    if failing_phase == "none":
        assert ops.execute(settings)["revision"] == ops.TARGET
        assert events == ["snapshot", "backup", "restore", "migrate"]
    else:
        with pytest.raises(ops.Stop):
            ops.execute(settings)
        assert "migrate" not in events


def test_cli_never_prints_exception_data(configured, monkeypatch, capsys):
    def fail(_):
        raise RuntimeError("postgresql://secret-owner-password private customer record")
    monkeypatch.setattr(ops, "execute", fail)
    assert ops.main() == 1
    printed = capsys.readouterr().out
    assert "operation_failed_no_details" in printed
    assert "password" not in printed and "customer" not in printed and "postgresql" not in printed
