"""0029 adoption rehearsal on disposable PG16/loopback Redis, fake bridge only.

The operator never upgrades schema or touches session files. Every migration,
row and Redis key below belongs to this fixture's random synthetic database.
"""
from dataclasses import replace
import json
from uuid import uuid4
from urllib.parse import urlsplit

from alembic import command
from alembic.config import Config
import pytest
import redis
from sqlalchemy import text

from scripts import one_time_legacy_adoption as ops
from test_legacy_adoption_postgres import source,run,PHONE  # noqa: F401
from test_business_migration_ops_postgres import migration_source,install_mock_private_store  # noqa: F401
from test_fulfillment_migration_ops_postgres import fulfillment_source  # noqa: F401
from test_one_time_management_migration import management_source  # noqa: F401
from test_one_time_onboarding_migration_postgres import onboarding_source  # noqa: F401
from test_one_time_inbox_migration_postgres import inbox_source  # noqa: F401

pytestmark=pytest.mark.integration


@pytest.fixture
def current_source(source):
    case,settings=source
    config=Config(str(ops.inbox.ROOT/'alembic.ini'))
    config.set_main_option('script_location',str(ops.inbox.ROOT/'alembic'))
    command.upgrade(config,ops.communication.TARGET)
    case.pending,case.event,case.preserved_qr=uuid4(),uuid4(),uuid4()
    case.legacy_outbox=uuid4()
    with case.engine.begin() as db:
        customer=db.execute(text('SELECT id FROM customers WHERE tenant_id=:t'),{'t':case.tenant}).scalar_one()
        account=db.execute(text("SELECT account_id FROM tenant_memberships WHERE tenant_id=:t AND role='owner' AND active"),{'t':case.tenant}).scalar_one()
        db.execute(text("UPDATE customers SET birth_date='1992-02-29',birth_month=2,birth_day=29 WHERE id=:c"),{'c':customer})
        db.execute(text("""UPDATE contact_consents SET marketing_opt_in=false,operational_opt_in=false,
            birthday_opt_in=false,birthday_declined_at=now(),birthday_purpose_version=1,source='whatsapp'
            WHERE tenant_id=:t AND contact_e164=:p"""),{'t':case.tenant,'p':'+'+PHONE})
        db.execute(text("""INSERT INTO automation_contact_state(tenant_id,contact_e164,human_paused)
            VALUES(:t,:p,true)"""),{'t':case.tenant,'p':'+'+PHONE})
        db.execute(text("""INSERT INTO inbound_events(id,tenant_id,channel_id,external_id,sender_e164,message_text,envelope)
            VALUES(:id,:t,:c,'synthetic-adoption-event',:p,'nao quero mensagens de aniversario',
                CAST(:envelope AS jsonb))"""),
            {'id':case.event,'t':case.tenant,'c':case.channel,'p':'+'+PHONE,
             'envelope':json.dumps({'kind':'text','control':'birthday','provider_at':1792512000})})
        db.execute(text("""INSERT INTO message_outbox(id,tenant_id,channel_id,event_id,to_e164,body,status,last_error)
            VALUES(:id,:t,:c,:e,:p,'Synthetic definite no-send','dead','channel_not_authorized')"""),
            {'id':case.legacy_outbox,'t':case.tenant,'c':case.channel,'e':case.event,'p':'+'+PHONE})
        control=db.execute(text('SELECT id FROM inbox_controls WHERE tenant_id=:t'),{'t':case.tenant}).scalar_one()
        db.execute(text("""INSERT INTO inbox_messages(tenant_id,control_id,direction,source,external_id,body,
            control_revision,status,envelope) VALUES(:t,:c,'in','customer','synthetic-adoption-journal',
            'Mensagem preservada',1,'received','{"kind":"edited","target":"synthetic-old"}'::jsonb)"""),
            {'t':case.tenant,'c':control})
        db.execute(text("""INSERT INTO communication_pending(id,tenant_id,contact_e164,event_key,event,reason)
            VALUES(:id,:t,:p,'synthetic-adoption-pending','booking_confirmed','human_paused')"""),
            {'id':case.pending,'t':case.tenant,'p':'+'+PHONE})
        db.execute(text("""INSERT INTO qr_channels(id,tenant_id,owner_account_id,provider,label,credential_hash,
            credential_encrypted,allowed_senders,enabled,assistant_enabled,idempotency_key,request_fingerprint)
            VALUES(:id,:t,:a,'baileys','Synthetic existing secondary',:hash,:encrypted,CAST(:senders AS jsonb),
                false,false,:key,repeat('b',64))"""),
            {'id':case.preserved_qr,'t':case.tenant,'a':account,'hash':ops.credential_hash('synthetic-secondary-key-'+('x'*32)),
             'encrypted':ops._cipher().encrypt(('synthetic-secondary-key-'+('x'*32)).encode()).decode(),'senders':json.dumps([PHONE]),'key':uuid4()})
        db.execute(text("""INSERT INTO automation_jobs(tenant_id,contact_e164,event,event_key,customer_id,customer_version,
            channel_id,settings_version,preferences_version,consent_version,body,due_at,expires_at,status,
            claim_token,attempted_at,presentation)
            SELECT tenant_id,contact_e164,'booking_confirmed','synthetic-preserved-uncertain',:customer,1,:channel,1,1,
            operational_updated_at,'Synthetic accepted result unknown',now(),now()+interval '1 day','uncertain',
            :claim,now(),CAST(:presentation AS jsonb)
            FROM contact_consents WHERE tenant_id=:t AND contact_e164=:p"""),
            {'t':case.tenant,'p':'+'+PHONE,'customer':customer,'channel':case.preserved_qr,'claim':uuid4(),
             'presentation':json.dumps({'schema_version':1,'source':'custom','template_revision':1,'variant':'custom','body_sha256':'synthetic'})})
    import os
    address=os.environ['REDIS_URL']
    endpoint=urlsplit(address)
    assert endpoint.scheme=='redis' and endpoint.hostname in {'127.0.0.1','localhost','::1'}
    assert endpoint.path=='/15'  # Same isolated test DB in local and Linux CI.
    client=redis.Redis.from_url(address,socket_connect_timeout=3,socket_timeout=3)
    keys={f'orion:qr:ai:{case.tenant}:{PHONE}':b'{"step":"human","hold":"synthetic-preserved"}',
        f'orion:qr:ai:done:{case.tenant}:synthetic-old-id':b'1',
        f'orion:qr:operation-receipt:{case.tenant}:{PHONE}':b'{"synthetic_receipt":"preserved"}'}
    try:
        for key,value in keys.items():client.set(key,value,ex=3600)
        case.redis=client;case.redis_keys=keys
        yield case,settings
    finally:
        client.delete(*keys);client.close()


def assert_original_scope(case,before,plan):
    with case.engine.begin() as db:
        actual=ops.snapshot(db,exclude=plan)
        assert actual==before
    assert {key:case.redis.get(key) for key in case.redis_keys}==case.redis_keys


def test_0029_register_activate_backup_restore_preserves_every_new_fact_and_session(current_source,monkeypatch):
    case,settings=current_source
    with case.engine.begin() as db:
        before=ops.snapshot(db)
        assert before['revision']==ops.communication.TARGET
        assert set(before['tables'])==ops.communication.TARGET_TABLES
        assert before['counts']['communication_pending']==1 and before['identity_sequences'][0]['state'][1]
    receipt=run(settings,monkeypatch)
    assert receipt['revision']==ops.communication.TARGET and receipt['preserved_counts']==before['counts']
    with case.engine.begin() as db:plan=ops.plan_for(db,settings)
    assert_original_scope(case,before,plan)
    assert ops.execute(settings)=={'status':'already_register','revision':ops.communication.TARGET}
    with monkeypatch.context() as guarded:
        guarded.setattr(ops,'capture_dump',lambda *_:pytest.fail('Wrong alias credential reached backup'))
        with pytest.raises(ops.Stop,match='existing_alias_mismatch'):
            ops.execute(replace(settings,token='synthetic-wrong-alias-key-'+('x'*40)))
    activated=replace(settings,run_id=str(uuid4()),phase='activate')
    with case.engine.begin() as db:before_activation=ops.snapshot(db)
    receipt=run(activated,monkeypatch)
    assert receipt['revision']==ops.communication.TARGET and receipt['authority_adopted']
    with case.engine.begin() as db:
        after=ops.snapshot(db)
        assert after['identity_sequences']==before['identity_sequences']
        assert after['programs']==before['programs'] and after['security']==before['security']
        assert after['rows']['communication_pending']==before['rows']['communication_pending']
        assert after['rows']['contact_consents']==before['rows']['contact_consents']
        assert after['rows']['automation_jobs']==before['rows']['automation_jobs']
        assert after['rows']['inbound_events']==before['rows']['inbound_events']
        assert after['rows']['inbox_messages']==before['rows']['inbox_messages']
        assert after['rows']['qr_channels']==before_activation['rows']['qr_channels']
        assert db.execute(text('SELECT mode FROM inbox_controls WHERE channel_id=:id'),{'id':plan['id']}).scalar_one()=='human'
        assert db.execute(text('SELECT status FROM communication_pending WHERE id=:id'),{'id':case.pending}).scalar_one()=='pending'
        assert not db.execute(text('SELECT birthday_opt_in OR operational_opt_in OR marketing_opt_in FROM contact_consents WHERE tenant_id=:t'),{'t':case.tenant}).scalar_one()
    assert {key:case.redis.get(key) for key in case.redis_keys}==case.redis_keys
    assert ops.execute(activated)=={'status':'already_activate','revision':ops.communication.TARGET}


@pytest.mark.parametrize('state,error',[('pending',None),('dead','delivery_unverified'),('dead','prior_attempt_unverified')])
def test_activation_refuses_legacy_pending_or_ambiguous_attempt_without_backup(current_source,monkeypatch,state,error):
    case,settings=current_source
    with case.engine.begin() as db:
        db.execute(text('UPDATE message_outbox SET status=:s,last_error=:error WHERE id=:id'),{'s':state,'error':error,'id':case.legacy_outbox})
        before=ops.snapshot(db)
    monkeypatch.setattr(ops,'paused_contacts',lambda *_:())
    monkeypatch.setattr(ops,'capture_dump',lambda *_:pytest.fail('Unsafe adoption reached backup'))
    with pytest.raises(ops.Stop,match='pending_outbound_preserved'):
        ops.execute(replace(settings,phase='activate'))
    with case.engine.begin() as db:assert ops.snapshot(db)==before


@pytest.mark.parametrize('failure',['wrong_owner','schema_revision_mismatch'])
def test_owner_and_exact_revision_are_fail_closed_before_backup(current_source,monkeypatch,failure):
    case,settings=current_source
    if failure=='schema_revision_mismatch':
        with case.engine.begin() as db:db.execute(text('UPDATE alembic_version SET version_num=:r'),{'r':ops.inbox.TARGET})
        code='unexpected_source_schema'
    else:
        settings=replace(settings,url=settings.url.set(username='app_migrator'))
        code='database_owner_required'
    monkeypatch.setattr(ops,'capture_dump',lambda *_:pytest.fail('Invalid owner/schema reached backup'))
    with pytest.raises(ops.Stop,match=code):ops.execute(settings)
    with case.engine.begin() as db:
        assert not db.execute(text('SELECT EXISTS(SELECT 1 FROM qr_channels WHERE id=:id)'),{'id':ops.qr_legacy.channel_id(case.tenant)}).scalar_one()
        assert db.execute(text('SELECT status FROM communication_pending WHERE id=:id'),{'id':case.pending}).scalar_one()=='pending'


def test_new_table_change_after_rehearsal_is_detected_and_no_alias_is_inserted(current_source,monkeypatch):
    case,settings=current_source
    install_mock_private_store(monkeypatch,settings)
    original=ops.verify_restore
    def changed(*args):
        proof=original(*args)
        with case.engine.begin() as db:db.execute(text('UPDATE communication_pending SET version=version+1 WHERE id=:id'),{'id':case.pending})
        return proof
    monkeypatch.setattr(ops,'verify_restore',changed)
    with pytest.raises(ops.Stop,match='source_changed_after_backup'):ops.execute(settings)
    with case.engine.begin() as db:
        assert db.execute(text('SELECT version FROM communication_pending WHERE id=:id'),{'id':case.pending}).scalar_one()==2
        assert not db.execute(text('SELECT EXISTS(SELECT 1 FROM qr_channels WHERE id=:id)'),{'id':ops.qr_legacy.channel_id(case.tenant)}).scalar_one()
