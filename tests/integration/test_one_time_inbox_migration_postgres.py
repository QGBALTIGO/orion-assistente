"""Full PG16 backup/restore/rehearsal; synthetic disposable databases only."""
from types import SimpleNamespace
from uuid import uuid4
from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import text
from scripts import one_time_inbox_migration as ops
from test_business_migration_ops_postgres import migration_source, install_mock_private_store  # noqa: F401
from test_fulfillment_migration_ops_postgres import fulfillment_source  # noqa: F401
from test_one_time_management_migration import management_source  # noqa: F401
from test_one_time_onboarding_migration_postgres import onboarding_source  # noqa: F401

pytestmark = pytest.mark.integration


@pytest.fixture
def inbox_source(onboarding_source):
    case = onboarding_source
    config = Config(str(ops.ROOT / 'alembic.ini'))
    config.set_main_option('script_location', str(ops.ROOT / 'alembic'))
    command.upgrade(config, ops.SOURCE)
    settings = ops.Settings(case.settings.url, case.settings.bridge_url, case.settings.token, str(uuid4()))
    channel = uuid4()
    with case.engine.begin() as db:
        db.execute(text("""INSERT INTO whatsapp_channels(id,tenant_id,phone_number_id,business_number,status)
            VALUES(:c,:t,:n,'+5567999999900','testing')"""), {'c': channel, 't': case.tenant, 'n': uuid4().hex})
        db.execute(text("""INSERT INTO human_handoffs(tenant_id,channel_id,contact_e164,status,expires_at)
            VALUES(:t,:c,'+5567999999901','active',now()-interval '1 day'),
            (:t,:c,'+5567999999902','resolved',now()-interval '1 day')"""), {'t': case.tenant, 'c': channel})
        db.execute(text("""INSERT INTO workflow_definitions(tenant_id,name,steps)
            VALUES(:t,'Synthetic preserved draft','[]')"""), {'t': case.tenant})
    return SimpleNamespace(engine=case.engine, settings=settings, tenant=case.tenant, channel=channel)


def test_complete_operation_preserves_all_old_rows_and_imports_only_active_pauses(inbox_source, monkeypatch):
    case = inbox_source
    with case.engine.begin() as db:
        before = ops.snapshot(db)
    stored, requests = install_mock_private_store(monkeypatch, case.settings)
    receipt = ops.execute(case.settings)
    assert receipt['restore_verified'] and receipt['rehearsal_verified']
    assert receipt['revision'] == ops.TARGET and receipt['preserved_counts'] == before['counts']
    assert requests == ['POST', 'GET'] and stored['body']
    with case.engine.begin() as db:
        assert ops.rows(db, projected=True) == before['rows']
        assert ops.counts(db) == before['counts']
        assert ops.old_catalog(db, projected=True) == before['catalog']
        assert ops.preserved_constraints(db, projected=True) == before['constraints']
        assert ops.proof.projected_security(ops.proof.security_structure(db), before['security']) == before['security']
        assert db.exec_driver_sql('SELECT mode FROM inbox_controls').scalar_one() == 'human'
        assert not db.execute(text('SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname=:n)'),
            {'n': ops.backup.restore_name(case.settings.run_id)}).scalar_one()
    assert ops.execute(case.settings) == {'status': 'already_applied', 'revision': ops.TARGET}
    assert requests == ['POST', 'GET']


@pytest.mark.parametrize('tamper', ['restore', 'concurrent_write', 'target_manifest'])
def test_bad_restore_or_changed_source_or_rehearsal_never_applies_original_schema(inbox_source, monkeypatch, tamper):
    case = inbox_source
    install_mock_private_store(monkeypatch, case.settings)
    with case.engine.begin() as db:
        before = ops.snapshot(db)
    original = ops.verify_restore

    def changed(*args):
        if tamper == 'restore':
            raise ops.Stop('restored_snapshot_mismatch')
        result = original(*args)
        if tamper == 'target_manifest': result['programs'] = {}
        else:
            with case.engine.begin() as db:
                db.execute(text("UPDATE workflow_definitions SET name='Synthetic concurrent edit' WHERE tenant_id=:t"), {'t': case.tenant})
        return result

    monkeypatch.setattr(ops, 'verify_restore', changed)
    with pytest.raises(ops.Stop, match={'restore': 'restored_snapshot_mismatch',
        'concurrent_write': 'source_changed_after_backup', 'target_manifest': 'rehearsed_target_mismatch'}[tamper]):
        ops.execute(case.settings)
    with case.engine.begin() as db:
        assert ops.backup.revision(db) == ops.SOURCE and ops.backup.table_names(db) == ops.SOURCE_TABLES
        assert ops.counts(db) == before['counts'] and ops.old_catalog(db) == before['catalog']
        assert ops.proof.security_structure(db) == before['security']


async def test_runtime_gate_uses_catalog_without_revision_or_credentials():
    from scripts import wait_for_inbox_schema as gate
    import os
    if not os.getenv('APP_DATABASE_URL'):
        if os.getenv('REQUIRE_POSTGRES_TESTS') == '1': pytest.fail('Mandatory PostgreSQL runtime missing')
        pytest.skip('Isolated PostgreSQL runtime required')
    settings = gate.Settings.from_environment()
    assert await gate.probe_schema(settings, 3)
    import asyncpg
    db = await asyncpg.connect(settings.dsn, server_settings={'default_transaction_read_only': 'on'})
    try:
        for index in range(len(gate.FUNCTIONS)):
            required = list(gate.FUNCTIONS); required[index] = 'public.synthetic_missing_function(uuid)'
            row = await db.fetchrow(gate.SCHEMA_QUERY, list(gate.TABLES), required)
            assert row['functions_ready'] is False and row['tables_ready'] is True
    finally:
        db.terminate()
