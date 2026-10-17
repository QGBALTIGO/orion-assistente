"""Complete operation against a new, disposable PG16 database with synthetic rows.

This never downgrades or migrates the CI application's existing database. The
owner URL is accepted only on loopback, and every disposable DB has a random name.
"""
import hashlib
import json
import os
import re
from uuid import uuid4

from alembic import command
from alembic.config import Config
import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url

from scripts import one_time_business_migration as ops

pytestmark = pytest.mark.integration


@pytest.fixture
def migration_source(monkeypatch):
    value = os.getenv("ORION_MIGRATION_TEST_OWNER_URL")
    if not value:
        if os.getenv("ORION_REQUIRE_MIGRATION_OPS_TEST") == "1":
            pytest.fail("Mandatory migration operation test needs its synthetic owner URL")
        pytest.skip("Dedicated migration operation owner URL not configured")
    url = make_url(value)
    assert url.host in {"localhost", "127.0.0.1", "::1"}
    assert url.username == "postgres" and url.database == "postgres"
    assert ops.client_major("pg_dump") == ops.client_major("pg_restore") == 16
    name = "orion_ops_fixture_" + uuid4().hex
    assert re.fullmatch(r"orion_ops_fixture_[0-9a-f]{32}", name)
    admin_engine = ops.engine_for(url)
    source_engine = None
    created_oid = None
    with admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as admin:
        try:
            assert admin.exec_driver_sql("SHOW server_version_num").scalar_one().startswith("16")
            admin.exec_driver_sql('CREATE DATABASE "' + name + '" TEMPLATE template0')
            created_oid = admin.execute(text("SELECT oid FROM pg_database WHERE datname=:name"), {"name": name}).scalar_one()
            admin.exec_driver_sql('REVOKE CONNECT ON DATABASE "' + name + '" FROM PUBLIC')
            admin.exec_driver_sql('GRANT CONNECT,CREATE ON DATABASE "' + name + '" TO app_migrator')
            admin.exec_driver_sql('GRANT CONNECT ON DATABASE "' + name + '" TO app_runtime,app_support')
            source_url = url.set(database=name)
            source_engine = ops.engine_for(source_url)
            with source_engine.begin() as conn:
                conn.exec_driver_sql("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
                conn.exec_driver_sql("GRANT USAGE ON SCHEMA public TO app_runtime,app_support,app_migrator")
                conn.exec_driver_sql("GRANT CREATE ON SCHEMA public TO app_migrator")
            monkeypatch.setenv("MIGRATION_DATABASE_URL", source_url.render_as_string(hide_password=False))
            monkeypatch.setenv("ORION_MIGRATION_SWITCH_ROLE", "1")
            config = Config(str(ops.ROOT / "alembic.ini"))
            config.set_main_option("script_location", str(ops.ROOT / "alembic"))
            command.upgrade(config, ops.SOURCE)
            tenant, account = uuid4(), uuid4()
            with source_engine.begin() as conn:
                conn.execute(text("INSERT INTO tenants(id,name,slug) VALUES (:id,'Synthetic migration business',:slug)"), {"id": tenant, "slug": "ops-" + tenant.hex})
                conn.execute(text("INSERT INTO customers(tenant_id,whatsapp_e164,name) VALUES (:id,'+5567999999901','Synthetic customer')"), {"id": tenant})
                conn.execute(text("INSERT INTO panel_accounts(id,name,email,password_hash) VALUES (:id,'Synthetic account','synthetic@example.test','not-a-real-hash')"), {"id": account})
                # Prove that ownership plus FORCE RLS alone cannot count rows.
                # The operation's preservation check must execute as the owner.
                conn.exec_driver_sql("DROP POLICY customers_migrator ON customers")
                conn.exec_driver_sql("SET LOCAL ROLE app_migrator")
                assert conn.exec_driver_sql("SELECT count(*) FROM customers").scalar_one() == 0
            settings = ops.Settings(source_url, "http://bridge.railway.internal:3000", "only-ci-synthetic-" + "x" * 40, str(uuid4()))
            yield source_engine, settings
        finally:
            if source_engine:
                source_engine.dispose()
            if created_oid is not None:
                # This fixture's own database only; never DROP FORCE or terminate.
                row = admin.execute(text("SELECT oid,pg_get_userbyid(datdba) AS owner FROM pg_database WHERE datname=:name"), {"name": name}).one()
                assert row.oid == created_oid and row.owner == "postgres"
                admin.exec_driver_sql('DROP DATABASE "' + name + '"')
    admin_engine.dispose()


def install_mock_private_store(monkeypatch, settings):
    stored = {}
    requests = []
    def handler(request):
        requests.append(request.method)
        assert request.url.path.endswith(settings.run_id)
        assert request.headers["X-Orion-Bridge-Key"] == settings.token
        if request.method == "POST":
            assert not stored
            envelope = request.content
            assert ops.decrypt_dump(envelope, settings.token, settings.run_id).startswith(b"PGDMP")
            stored["body"] = envelope
            digest = hashlib.sha256(envelope).hexdigest()
            assert request.headers["X-Orion-SHA256"] == digest
            return httpx.Response(201, json={"id": settings.run_id, "sha256": digest, "size": len(envelope)})
        return httpx.Response(200, content=stored["body"], headers={"content-type": "application/octet-stream"})
    original_client = httpx.Client
    monkeypatch.setattr(ops.httpx, "Client", lambda **kwargs: original_client(**kwargs, transport=httpx.MockTransport(handler)))
    return stored, requests


def test_full_backup_restore_and_restricted_migration_preserves_real_rows(migration_source, monkeypatch):
    engine, settings = migration_source
    stored, requests = install_mock_private_store(monkeypatch, settings)
    with engine.begin() as conn:
        assert ops.revision(conn) == ops.SOURCE
        original_rows = conn.exec_driver_sql("SELECT row_to_json(c)::text FROM customers c").scalars().all()
    structures = []
    original_fingerprint = ops.catalog_fingerprint
    def record_fingerprint(conn, tables=ops.LEGACY_TABLES):
        structures.append(ops.catalog_structure(conn, tables))
        return original_fingerprint(conn, tables)
    monkeypatch.setattr(ops, "catalog_fingerprint", record_fingerprint)
    try:
        result = ops.execute(settings)
    except ops.Stop as error:
        if str(error) == "restored_catalog_mismatch" and len(structures) >= 2:
            # These are schema definitions from this synthetic database only.
            # No customer values or production credentials enter the assertion.
            before = {row[0]: row[1:] for row in structures[-2]}
            after = {row[0]: row[1:] for row in structures[-1]}
            differences = {name: {"before": before.get(name), "restored": after.get(name)}
                           for name in before.keys() | after.keys() if before.get(name) != after.get(name)}
            if differences:
                fields = ("rls", "force_rls", "columns", "constraints", "indexes", "policies")
                detail = {name: {field: {"before": left, "restored": right}
                                  for field, left, right in zip(fields, value["before"], value["restored"])
                                  if left != right}
                          for name, value in sorted(differences.items())[:2]}
                print("SYNTHETIC_SCHEMA_DIFFERENCE=" + json.dumps(detail, sort_keys=True, default=str))
            assert not differences, "Restored schema differs; see synthetic schema diagnostics"
        raise
    assert requests == ["POST", "GET"] and stored["body"].startswith(b"ORIONDB1")
    assert result["restore_verified"] is True and result["revision"] == ops.TARGET
    assert result["backup_counts"]["customers"] == result["preserved_counts"]["customers"] == 1
    assert result["preserved_counts"]["panel_accounts"] == 1
    with engine.begin() as conn:
        assert ops.revision(conn) == ops.TARGET
        assert conn.exec_driver_sql("SELECT row_to_json(c)::text FROM customers c").scalars().all() == original_rows
        ops.verify_new_schema(conn)
        assert not conn.execute(text("SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname=:name)"), {"name": ops.restore_name(settings.run_id)}).scalar_one()
        # Roles remain unable to bypass FORCE RLS after the operation.
        assert conn.exec_driver_sql("SELECT bool_and(NOT rolsuper AND NOT rolbypassrls) FROM pg_roles WHERE rolname IN ('app_runtime','app_support','app_migrator')").scalar_one()
        conn.exec_driver_sql("SET LOCAL ROLE app_runtime")
        assert conn.exec_driver_sql("SELECT count(*) FROM customers").scalar_one() == 0


def test_failed_target_validation_rolls_back_both_migrations(migration_source, monkeypatch):
    engine, _ = migration_source
    def stop(_):
        raise ops.Stop("synthetic_target_validation_failure")
    monkeypatch.setattr(ops, "verify_new_schema", stop)
    with pytest.raises(ops.Stop, match="synthetic_target_validation_failure"):
        ops.apply_exact_migrations(engine)
    with engine.begin() as conn:
        assert ops.revision(conn) == ops.SOURCE
        assert ops.table_names(conn) == ops.LEGACY_TABLES
        assert conn.exec_driver_sql("SELECT count(*) FROM customers").scalar_one() == 1


def test_restore_count_mismatch_drops_only_created_test_db_without_migrating(migration_source, tmp_path):
    engine, settings = migration_source
    with engine.begin() as conn:
        identity = ops.owner_preflight(conn, settings)
    path = tmp_path / "synthetic.dump"
    expected, fingerprint = ops.capture_dump(engine, settings, path)
    expected["customers"] += 1
    with pytest.raises(ops.Stop, match="restored_count_mismatch"):
        ops.verify_restore(engine, settings, identity, path, expected, fingerprint)
    with engine.begin() as conn:
        assert ops.revision(conn) == ops.SOURCE and ops.table_names(conn) == ops.LEGACY_TABLES
        assert conn.exec_driver_sql("SELECT count(*) FROM customers").scalar_one() == 1
        assert not conn.execute(text("SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname=:name)"), {"name": ops.restore_name(settings.run_id)}).scalar_one()
