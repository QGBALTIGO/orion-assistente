"""Real PG16 dump/restore/rehearsal of the sealed 0019 -> 0023 operation.

Only UUID-named disposable loopback databases are used. The private HTTP store
is simulated; encrypted envelopes, pg_dump/pg_restore, roles, DDL and RLS are not.
"""
from types import SimpleNamespace
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import event, text
from sqlalchemy.engine import Engine

from scripts import one_time_onboarding_migration as ops
from test_business_migration_ops_postgres import migration_source, install_mock_private_store  # noqa: F401
from test_fulfillment_migration_ops_postgres import fulfillment_source  # noqa: F401
from test_one_time_management_migration import management_source, independently_hash_old_rows  # noqa: F401

pytestmark = pytest.mark.integration


@pytest.fixture
def onboarding_source(management_source):
    previous = management_source
    config = Config(str(ops.ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ops.ROOT / "alembic"))
    command.upgrade(config, ops.SOURCE)
    settings = ops.Settings(previous.settings.url, previous.settings.bridge_url,
                            previous.settings.token, str(uuid4()))
    with previous.engine.begin() as db:
        account = db.exec_driver_sql("SELECT id FROM panel_accounts").scalar_one()
        assert db.execute(text("SELECT role FROM tenant_memberships WHERE tenant_id=:t AND account_id=:a AND active"),
                          {"t": previous.tenant, "a": account}).scalar_one() == "owner"
        db.execute(text("""INSERT INTO management_preferences(tenant_id,preferences) VALUES(:t,
            '{"business":{"trade_name":"Synthetic preserved settings"},"automations":{"vacancy_requested":true}}'::jsonb)"""), {"t": previous.tenant})
        db.execute(text("INSERT INTO contact_consents(tenant_id,contact_e164,marketing_opt_in) VALUES(:t,'+5567999999901',true)"), {"t": previous.tenant})
        assert ops.backup.revision(db) == ops.SOURCE
    return SimpleNamespace(engine=previous.engine, settings=settings, tenant=previous.tenant,
                           customer=previous.customer, account=account)


def evidence(case, *, old_columns=None):
    with case.engine.begin() as db:
        ops.proof.set_timeouts(db)
        columns = old_columns or {row[0]: [c[0] for c in row[3]]
            for row in ops.backup.catalog_structure(db, ops.SOURCE_TABLES)}
        return {"columns": columns, "revision": ops.backup.revision(db),
                "tables": ops.backup.table_names(db), "counts": ops.counts(db),
                "rows": independently_hash_old_rows(db, {name: fields for name, fields in columns.items() if name != "alembic_version"}),
                "catalog": ops.old_catalog(db, projected=old_columns is not None),
                "security": ops.proof.security_structure(db), "programs": ops.proof.programs(db)}


def assert_restore_removed(case):
    with case.engine.begin() as db:
        assert not db.execute(text("SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname=:name)"),
                              {"name": ops.backup.restore_name(case.settings.run_id)}).scalar_one()


def test_sealed_execute_real_backup_restore_and_rehearsal_preserve_0019_rows_and_security(onboarding_source, monkeypatch):
    case = onboarding_source
    before = evidence(case)
    stored, requests = install_mock_private_store(monkeypatch, case.settings)
    result = ops.execute(case.settings)
    assert result["status"] == "complete" and result["revision"] == ops.TARGET
    assert result["restore_verified"] and result["rehearsal_verified"] and result["preserved_counts"] == before["counts"]
    assert requests == ["POST", "GET"] and stored["body"] and not stored["body"].startswith(b"PGDMP")
    after = evidence(case, old_columns=before["columns"])
    assert after["rows"] == before["rows"] and after["counts"] == before["counts"]
    assert after["catalog"] == before["catalog"] and after["tables"] == ops.TARGET_TABLES
    assert ops.proof.projected_security(after["security"], before["security"]) == before["security"]
    old_keys = {(f[0], f[1], f[2]) for f in before["programs"]["functions"]}
    assert [f for f in after["programs"]["functions"] if (f[0], f[1], f[2]) in old_keys] == before["programs"]["functions"]
    assert after["programs"]["triggers"] == before["programs"]["triggers"]
    with case.engine.begin() as db:
        assert not any(ops.counts(db, ops.NEW_TABLES).values())
        assert db.exec_driver_sql("SELECT count(*) FROM contact_consents WHERE operational_opt_in").scalar_one() == 0
        assert db.execute(text("SELECT status,attempts FROM message_outbox WHERE tenant_id=:t"), {"t": case.tenant}).one() == ("pending", 0)
        assert db.execute(text("SELECT status,total_cents FROM appointments WHERE tenant_id=:t"), {"t": case.tenant}).one() == ("confirmed", 3500)
        db.exec_driver_sql("SET LOCAL ROLE app_runtime")
        assert db.exec_driver_sql("SELECT count(*) FROM customers").scalar_one() == 0
        db.execute(text("SELECT set_config('app.tenant_id',:t,true)"), {"t": str(case.tenant)})
        assert db.exec_driver_sql("SELECT count(*) FROM customers").scalar_one() == 1
    assert_restore_removed(case)


def test_target_replay_is_readonly_and_does_not_repeat_backup(onboarding_source, monkeypatch):
    case = onboarding_source
    install_mock_private_store(monkeypatch, case.settings)
    ops.execute(case.settings)
    with case.engine.begin() as db:
        db.execute(text("""INSERT INTO business_onboarding(tenant_id,is_new,configuration)
            VALUES(:t,false,'{"activity":"services"}'::jsonb)"""), {"t": case.tenant})
        columns = {row[0]: [c[0] for c in row[3] if c[0] != "operational_opt_in"]
                   for row in ops.backup.catalog_structure(db, ops.SOURCE_TABLES)}
    before = evidence(case, old_columns=columns)
    def forbidden(*args, **kwargs): pytest.fail("Target replay attempted backup/migration I/O")
    monkeypatch.setattr(ops, "capture_dump", forbidden)
    writes = []
    def watch(conn, cursor, statement, parameters, context, many):
        if statement.lstrip().upper().startswith(("INSERT ", "UPDATE ", "DELETE ", "ALTER ", "CREATE ", "DROP ")):
            writes.append(statement)
    event.listen(Engine, "before_cursor_execute", watch)
    try:
        assert ops.execute(case.settings) == {"status": "already_applied", "revision": ops.TARGET}
    finally:
        event.remove(Engine, "before_cursor_execute", watch)
    after = evidence(case, old_columns=before["columns"])
    assert after == before and writes == []
    with case.engine.begin() as db:
        assert db.execute(text("SELECT configuration->>'activity' FROM business_onboarding WHERE tenant_id=:t"), {"t": case.tenant}).scalar_one() == "services"
    assert_restore_removed(case)


def test_source_changed_after_durable_backup_aborts_without_source_ddl(onboarding_source, monkeypatch):
    case = onboarding_source
    install_mock_private_store(monkeypatch, case.settings)
    expected = {}
    original = ops.verify_restore
    def change_after_rehearsal(*args, **kwargs):
        result = original(*args, **kwargs)
        with case.engine.begin() as db:
            db.execute(text("UPDATE customers SET name='Synthetic legitimate concurrent edit' WHERE id=:id"), {"id": case.customer})
        expected["source"] = evidence(case)
        return result
    monkeypatch.setattr(ops, "verify_restore", change_after_rehearsal)
    with pytest.raises(ops.Stop, match="source_changed_after_backup"):
        ops.execute(case.settings)
    assert evidence(case) == expected["source"] and expected["source"]["revision"] == ops.SOURCE
    assert_restore_removed(case)


def test_failure_in_real_rehearsal_rolls_back_and_preserves_populated_source(onboarding_source, monkeypatch):
    case = onboarding_source
    before = evidence(case)
    install_mock_private_store(monkeypatch, case.settings)
    original = ops.reviewed_migrations
    def failing_migration():
        modules = original()
        upgrade = modules[2].upgrade
        def fail():
            upgrade()
            raise RuntimeError("synthetic rehearsal failure")
        modules[2].upgrade = fail
        return modules
    monkeypatch.setattr(ops, "reviewed_migrations", failing_migration)
    with pytest.raises(RuntimeError, match="synthetic rehearsal failure"):
        ops.execute(case.settings)
    assert evidence(case) == before
    assert_restore_removed(case)
