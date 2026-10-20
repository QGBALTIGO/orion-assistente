"""Real PG16 encrypted backup/restore/rehearsal on disposable synthetic databases."""
from dataclasses import replace
from uuid import uuid4
from contextlib import nullcontext
import pytest
from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet
from sqlalchemy import text
from scripts import one_time_legacy_adoption as ops
from test_business_migration_ops_postgres import migration_source, install_mock_private_store  # noqa: F401
from test_fulfillment_migration_ops_postgres import fulfillment_source  # noqa: F401
from test_one_time_management_migration import management_source  # noqa: F401
from test_one_time_onboarding_migration_postgres import onboarding_source  # noqa: F401
from test_one_time_inbox_migration_postgres import inbox_source  # noqa: F401

pytestmark=pytest.mark.integration
PHONE='5567999999901'


@pytest.fixture
def source(inbox_source,monkeypatch):
    case=inbox_source
    config=Config(str(ops.inbox.ROOT/'alembic.ini'))
    config.set_main_option('script_location',str(ops.inbox.ROOT/'alembic'))
    command.upgrade(config,ops.inbox.TARGET)
    monkeypatch.setenv('INTEGRATION_FERNET_KEY',Fernet.generate_key().decode())
    monkeypatch.setattr(ops,'session_proof',lambda *_:'a'*64)
    settings=ops.Settings(case.settings.url,case.settings.bridge_url,case.settings.token,str(uuid4()),case.tenant,(PHONE,), 'register', 'a'*64)
    with case.engine.begin() as db:
        # The shared migration fixture deliberately starts with the bot off.
        # Model an already active pilot only in this disposable database.
        db.execute(text("UPDATE tenants SET bot_enabled=true WHERE id=:t"),{'t':case.tenant})
        assert db.execute(text("SELECT bot_enabled FROM tenants WHERE id=:t"),{'t':case.tenant}).scalar_one()
    return case,settings


def run(settings,monkeypatch):
    with monkeypatch.context() as mp:
        stored,requests=install_mock_private_store(mp,settings)
        result=ops.execute(settings)
        assert requests==['POST','GET'] and stored['body']
        assert result['restore_verified'] and result['rehearsal_verified'] and result['session_preserved']
        return result


def test_both_phases_preserve_existing_records_and_pauses_without_copying_sessions(source,monkeypatch):
    case,settings=source
    with case.engine.begin() as db: before=ops.snapshot(db)
    result=run(settings,monkeypatch)
    assert result['registered'] and not result['authority_adopted']
    with case.engine.begin() as db:
        plan=ops.plan_for(db,settings)
        assert ops.snapshot(db,exclude=plan)==before
        assert db.execute(text('SELECT count(*) FROM inbox_channel_links WHERE channel_id=:c'),{'c':plan['id']}).scalar_one()==0
    assert ops.execute(settings)['status']=='already_register'
    activated=replace(settings,run_id=str(uuid4()),phase='activate')
    monkeypatch.setattr(ops,'paused_contacts',lambda *_:(PHONE,))
    monkeypatch.setattr(ops,'conversation_locks',lambda *_:nullcontext())
    with case.engine.begin() as db: before_activation=ops.snapshot(db)
    assert run(activated,monkeypatch)['authority_adopted']
    with case.engine.begin() as db:
        controls=db.execute(text("SELECT contact_e164,mode,assigned_account_id,pending_operation FROM inbox_controls WHERE channel_id=:c"),{'c':plan['id']}).all()
        assert [tuple(r) for r in controls]==[('+'+PHONE,'human',None,None)]
        assert ops.snapshot(db)['rows']['qr_channels']==before_activation['rows']['qr_channels']
        assert ops.snapshot(db)['catalog']==before_activation['catalog']
        # An explicit later operator resume must survive another deployment.
        db.execute(text("UPDATE inbox_controls SET mode='ai',revision=revision+1 WHERE channel_id=:c"),{'c':plan['id']})
    assert ops.execute(activated)['status']=='already_activate'
    with case.engine.begin() as db:
        assert db.execute(text('SELECT mode FROM inbox_controls WHERE channel_id=:c'),{'c':plan['id']}).scalar_one()=='ai'


@pytest.mark.parametrize('tamper',['restore','source','owner'])
def test_failure_never_changes_the_original_alias_or_existing_data(source,monkeypatch,tamper):
    case,settings=source
    with case.engine.begin() as db: before=ops.snapshot(db)
    original=ops.verify_restore
    def verify(*args):
        if tamper=='restore': raise ops.Stop('restored_snapshot_mismatch')
        receipt=original(*args)
        with case.engine.begin() as db:
            if tamper=='source':
                db.execute(text("UPDATE workflow_definitions SET name='Synthetic concurrent edit' WHERE tenant_id=:t"),{'t':case.tenant})
            else:
                db.execute(text("UPDATE tenant_memberships SET active=false WHERE tenant_id=:t"),{'t':case.tenant})
        return receipt
    monkeypatch.setattr(ops,'verify_restore',verify)
    with monkeypatch.context() as mp:
        install_mock_private_store(mp,settings)
        with pytest.raises(ops.Stop): ops.execute(settings)
    with case.engine.begin() as db:
        assert ops.snapshot(db)['catalog']==before['catalog']
        assert not db.execute(text('SELECT EXISTS(SELECT 1 FROM qr_channels WHERE id=:id)'),{'id':ops.qr_legacy.channel_id(case.tenant)}).scalar_one()


def test_ambiguous_owner_stops_before_backup_or_registration(source,monkeypatch):
    case,settings=source
    with case.engine.begin() as db:
        account=uuid4()
        db.execute(text("INSERT INTO panel_accounts(id,name,email,password_hash,role) VALUES(:a,'Synthetic second owner',:email,'synthetic','member')"),{'a':account,'email':account.hex+'@example.test'})
        db.execute(text("INSERT INTO tenant_memberships(tenant_id,account_id,role) VALUES(:t,:a,'owner')"),{'t':case.tenant,'a':account})
    monkeypatch.setattr(ops,'capture_dump',lambda *_:pytest.fail('backup should not start'))
    with pytest.raises(ops.Stop,match='unique_existing_owner_required'):ops.execute(settings)


def test_inactive_tenant_stops_without_enabling_it(source,monkeypatch):
    case,settings=source
    with case.engine.begin() as db:
        db.execute(text('UPDATE tenants SET bot_enabled=false WHERE id=:t'),{'t':case.tenant})
    monkeypatch.setattr(ops,'capture_dump',lambda *_:pytest.fail('backup should not start'))
    with pytest.raises(ops.Stop,match='existing_tenant_not_active'):ops.execute(settings)
    with case.engine.begin() as db:
        assert not db.execute(text('SELECT bot_enabled FROM tenants WHERE id=:t'),{'t':case.tenant}).scalar_one()


def test_primary_account_change_stops_before_backup(source,monkeypatch):
    _,settings=source
    monkeypatch.setattr(ops,'session_proof',lambda *_:'b'*64)
    monkeypatch.setattr(ops,'capture_dump',lambda *_:pytest.fail('backup should not start'))
    with pytest.raises(ops.Stop,match='primary_account_changed_preserved'):ops.execute(settings)


@pytest.mark.parametrize('status',['queued','sending','uncertain'])
def test_activation_preserves_and_refuses_any_pending_outbound(source,monkeypatch,status):
    case,settings=source
    run(settings,monkeypatch)
    message=uuid4()
    with case.engine.begin() as db:
        control=db.execute(text('SELECT id FROM inbox_controls WHERE tenant_id=:t'),{'t':case.tenant}).scalar_one()
        db.execute(text("""INSERT INTO inbox_messages(id,tenant_id,control_id,direction,source,body,
            fingerprint,control_revision,status) VALUES(:m,:t,:c,'out','assistant','Synthetic pending',repeat('a',64),1,:s)"""),
            {'m':message,'t':case.tenant,'c':control,'s':status})
        before=ops.snapshot(db)
    monkeypatch.setattr(ops,'paused_contacts',lambda *_:())
    monkeypatch.setattr(ops,'capture_dump',lambda *_:pytest.fail('backup should not start'))
    with pytest.raises(ops.Stop,match='pending_outbound_preserved'):
        ops.execute(replace(settings,phase='activate',run_id=str(uuid4())))
    with case.engine.begin() as db:
        assert ops.snapshot(db)==before
