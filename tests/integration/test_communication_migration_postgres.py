"""Backup, restore and additive upgrade on synthetic disposable PG16 only."""
from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import text
from scripts import one_time_communication_migration as ops
from test_one_time_inbox_migration_postgres import inbox_source  # noqa: F401
from test_one_time_onboarding_migration_postgres import onboarding_source  # noqa: F401
from test_one_time_management_migration import management_source  # noqa: F401
from test_fulfillment_migration_ops_postgres import fulfillment_source  # noqa: F401
from test_business_migration_ops_postgres import migration_source,install_mock_private_store  # noqa: F401

pytestmark=pytest.mark.integration


@pytest.fixture
def communication_source(inbox_source):
    case=inbox_source
    config=Config(str(ops.ROOT/'alembic.ini'))
    config.set_main_option('script_location',str(ops.ROOT/'alembic'))
    command.upgrade(config,ops.SOURCE)
    case.settings=ops.Settings(case.settings.url,case.settings.bridge_url,case.settings.token,case.settings.run_id)
    with case.engine.begin() as db:
        db.execute(text("UPDATE customers SET birth_date='1992-02-29' WHERE tenant_id=:t"),{'t':case.tenant})
        db.execute(text("UPDATE contact_consents SET marketing_opt_in=true,operational_opt_in=true WHERE tenant_id=:t AND contact_e164='+5567999999901'"),{'t':case.tenant})
        db.execute(text("INSERT INTO inbound_events(tenant_id,channel_id,external_id,sender_e164,message_text) VALUES(:t,:c,'synthetic-preserved-event','+5567999999901','Texto original preservado')"),{'t':case.tenant,'c':case.channel})
    return case


def test_restore_rehearsal_preserves_legacy_dates_events_sessions_and_pauses(communication_source,monkeypatch):
    case=communication_source
    with case.engine.begin() as db:before=ops.snapshot(db)
    original=ops.old_catalog
    def compare_catalog(*args,**kwargs):
        value=original(*args,**kwargs)
        if kwargs.get('projected') and value!=before['catalog']:
            expected={row[0]:row for row in before['catalog']}
            differences=[(row[0],index,expected[row[0]][index],part) for row in value
                for index,part in enumerate(row) if part!=expected[row[0]][index]]
            print('Synthetic schema differences:',differences)
        return value
    monkeypatch.setattr(ops,'old_catalog',compare_catalog)
    stored,requests=install_mock_private_store(monkeypatch,case.settings)
    result=ops.execute(case.settings)
    assert result['restore_verified'] and result['rehearsal_verified'] and result['preserved_counts']==before['counts']
    assert requests==['POST','GET'] and stored['body']
    with case.engine.begin() as db:
        assert ops.rows(db,projected=True)==before['rows']
        assert ops.counts(db)==before['counts']
        assert db.exec_driver_sql('SELECT mode FROM inbox_controls').scalar_one()=='human'
        assert not db.exec_driver_sql('SELECT EXISTS(SELECT 1 FROM customers WHERE birth_day IS NOT NULL OR birth_month IS NOT NULL)').scalar_one()
        assert not db.exec_driver_sql('SELECT EXISTS(SELECT 1 FROM contact_consents WHERE birthday_opt_in)').scalar_one()
    assert ops.execute(case.settings)=={'status':'already_applied','revision':ops.TARGET}


@pytest.mark.parametrize('tamper',['restore','concurrent_write','target_manifest'])
def test_bad_proof_keeps_source_unchanged(communication_source,monkeypatch,tamper):
    case=communication_source
    install_mock_private_store(monkeypatch,case.settings)
    original=ops.verify_restore
    def changed(*args):
        if tamper=='restore':raise ops.Stop('restored_snapshot_mismatch')
        target=original(*args)
        if tamper=='target_manifest':target['programs']={}
        else:
            with case.engine.begin() as db:db.execute(text("UPDATE customers SET name='Synthetic concurrent change' WHERE tenant_id=:t"),{'t':case.tenant})
        return target
    monkeypatch.setattr(ops,'verify_restore',changed)
    with pytest.raises(ops.Stop,match={'restore':'restored_snapshot_mismatch','concurrent_write':'source_changed_after_backup','target_manifest':'rehearsed_target_mismatch'}[tamper]):ops.execute(case.settings)
    with case.engine.begin() as db:assert ops.backup.revision(db)==ops.SOURCE


async def test_runtime_gate_uses_only_catalog():
    from scripts import wait_for_communication_schema as gate
    assert await gate.probe_schema(gate.Settings.from_environment(),3)
