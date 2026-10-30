"""Real transactional events, recipient isolation and ambiguous-send recovery."""
import asyncio
import json
from uuid import uuid4

from cryptography.fernet import Fernet
import pytest
import pytest_asyncio
from sqlalchemy import text

from tests.integration.test_booking import booking_db, phone, start_at
from app import appointment_notifications as notifications
from app import appointment_confirmation as confirmation
from app.booking_service import book_appointment, confirm_hold, cancel_appointment, reschedule_appointment
from app.appointment_lifecycle import customer_actor
from app.database import tenant_transaction

pytestmark=pytest.mark.integration
PRO_PHONE='+5567999999922'


@pytest_asyncio.fixture
async def comm_db(booking_db,monkeypatch):
    d=booking_db;account,channel=uuid4(),uuid4();d.update(account=account,channel=channel)
    monkeypatch.setenv('APPOINTMENT_COMMUNICATIONS_ENABLED','1')
    monkeypatch.setenv('APPOINTMENT_COMMUNICATIONS_PILOT_ONLY','0')
    monkeypatch.setenv('INTEGRATION_FERNET_KEY',Fernet.generate_key().decode())
    monkeypatch.setattr(notifications,'get_session_factory',lambda:d['factory'])
    monkeypatch.setattr(confirmation,'get_session_factory',lambda:d['factory'])
    async with d['migrator'].begin() as db:
        await db.execute(text("INSERT INTO panel_accounts(id,name,email,password_hash) VALUES(:id,'Teste',:email,'unused')"),{'id':account,'email':str(account)+'@example.test'})
        await db.execute(text("INSERT INTO tenant_memberships(tenant_id,account_id,role) VALUES(:t,:id,'owner')"),{'t':d['a'],'id':account})
        await db.execute(text("""INSERT INTO qr_channels(id,tenant_id,owner_account_id,provider,label,credential_hash,credential_encrypted,
            allowed_senders,enabled,idempotency_key,request_fingerprint) VALUES(:id,:t,:a,'baileys','Teste',:hash,'unused',CAST(:senders AS jsonb),true,:key,:hash)"""),
            {'id':channel,'t':d['a'],'a':account,'hash':'a'*64,'senders':json.dumps([PRO_PHONE[1:],phone(920)[1:]]),'key':uuid4()})
        await db.execute(text("UPDATE professionals SET phone_e164=:phone WHERE tenant_id=:t AND id=:id"),{'phone':PRO_PHONE,'t':d['a'],'id':d['pro']})
        await db.execute(text("INSERT INTO appointment_communication_settings(tenant_id,enabled,channel_id) VALUES(:t,true,:c)"),{'t':d['a'],'c':channel})
        await db.execute(text("INSERT INTO contact_consents(tenant_id,contact_e164,operational_opt_in,marketing_opt_in) VALUES(:t,:phone,true,true)"),{'t':d['a'],'phone':phone(920)})
    try:yield d
    finally:
        async with d['migrator'].begin() as db:
            await db.execute(text('UPDATE tenants SET whatsapp_channel_id=NULL WHERE id=:t'),{'t':d['a']})
            for table in ('inbox_reads','inbox_provider_receipts','inbox_messages','inbox_control_events','inbox_controls','inbox_channel_links',
                'appointment_return_intervals','appointment_change_proposals','appointment_confirmation_receipts','appointment_notifications','appointment_communication_settings','contact_consents','qr_channels','tenant_memberships'):
                await db.execute(text(f'DELETE FROM {table} WHERE tenant_id=:t'),{'t':d['a']})
            await db.execute(text('DELETE FROM panel_accounts WHERE id=:id'),{'id':account})


async def booking(d):
    async with d['factory']() as db:
        held=await book_appointment(db,d['a'],d['pro'],[d['cut']],phone(920),'Cliente sintético',start_at(d['day'],10),'comm-'+str(uuid4()))
        return await confirm_hold(db,d['a'],held['id'])


async def jobs(d):
    async with d['migrator'].connect() as db:
        return (await db.execute(text('SELECT * FROM appointment_notifications WHERE tenant_id=:t ORDER BY event'),{'t':d['a']})).mappings().all()


@pytest.mark.asyncio
async def test_confirmed_booking_notifies_only_assigned_professional_without_customer_message(comm_db):
    d=comm_db;await booking(d);items=await jobs(d)
    assert {r['event'] for r in items}=={'professional_new','reminder'}
    staff=next(r for r in items if r['event']=='professional_new')
    assert staff['recipient_id']==d['pro'] and staff['recipient_id']!=d['pro2']
    sent=[]
    async def send(t,c,number,body,operation):
        sent.append((number,operation));assert t==d['a'] and c==d['channel']
        return {'accepted':True,'state':'accepted','operation_id':operation}
    assert await notifications.process_notification({'appointment_sender':send},str(d['a']),str(staff['id']))=='accepted'
    assert await notifications.process_notification({'appointment_sender':send},str(d['a']),str(staff['id']))=='accepted'
    assert sent==[(PRO_PHONE,str(staff['id']))]


@pytest.mark.asyncio
async def test_cancellation_is_committed_and_old_reminders_invalidated_when_transport_fails(comm_db):
    d=comm_db;a=await booking(d)
    async with d['factory']() as db:
        await cancel_appointment(db,d['a'],a['id'],context=customer_actor(a['customer_id'],'public_site'))
    items=await jobs(d)
    assert all(r['status']=='cancelled' for r in items if r['event']!='professional_cancelled')
    notice=next(r for r in items if r['event']=='professional_cancelled');count=[]
    async def timeout(*args):count.append(1);raise TimeoutError('simulated secret must not persist')
    assert await notifications.process_notification({'appointment_sender':timeout},str(d['a']),str(notice['id']))=='unknown'
    assert await notifications.process_notification({'appointment_sender':timeout},str(d['a']),str(notice['id']))=='unknown'
    assert len(count)==1
    async with d['migrator'].connect() as db:
        assert (await db.execute(text('SELECT status FROM appointments WHERE id=:id'),{'id':a['id']})).scalar_one()=='cancelled'
    assert 'simulated secret' not in json.dumps([dict(x) for x in await jobs(d)],default=str)


@pytest.mark.asyncio
async def test_confirmation_requires_matching_phone_and_does_not_return_foreign_details(comm_db):
    d=comm_db;a=await booking(d)
    body='Olá, Orion! Protocolo: '+a['protocol']
    denied=await confirmation.confirmation_reply(d['a'],phone(921),body,'qr-test','message1')
    foreign=await confirmation.confirmation_reply(d['b'],phone(920),body,'qr-test','message1')
    assert denied==foreign==confirmation.GENERIC
    answer=await confirmation.confirmation_reply(d['a'],phone(920),body,'qr-test','message2')
    assert 'confirmado' in answer and '/a#' in answer
    assert answer==await confirmation.confirmation_reply(d['a'],phone(920),body,'qr-test','message2')
    async with d['factory']() as db:
        await cancel_appointment(db,d['a'],a['id'],context=customer_actor(a['customer_id'],'public_site'))
    assert await confirmation.confirmation_reply(d['a'],phone(920),body,'qr-test','message2')==''


@pytest.mark.asyncio
async def test_admin_recovers_only_verified_recent_inbound_once_with_real_native_owner(comm_db,monkeypatch):
    import base64,hashlib,os
    from app import appointment_communications_api as api,inbox_runtime,inbox_control,management_api,qr_legacy,legacy_admin_identity
    from app.catalog_api import Actor
    from fastapi import HTTPException
    d=comm_db;a=await booking(d)
    for flag in ('INBOX_CONTROL_ENABLED','INBOX_SEND_ENABLED','QR_CHANNELS_ENABLED','QR_LEGACY_CENTRAL_ENABLED','PANEL_LEGACY_ADMIN_IDENTITY_ENABLED'):
        monkeypatch.setenv(flag,'1')
    monkeypatch.setenv('QR_AI_TENANT_ID',str(d['a']))
    monkeypatch.setenv('APPOINTMENT_COMMUNICATIONS_PILOT_ONLY','1')
    monkeypatch.setenv('APPOINTMENT_COMMUNICATIONS_PILOT_TENANT_ID',str(d['a']))
    monkeypatch.setenv('APPOINTMENT_COMMUNICATIONS_PILOT_PHONE','556781234567')
    monkeypatch.delenv('APPOINTMENT_COMMUNICATIONS_PILOT_CUSTOMER_PHONES',raising=False)
    monkeypatch.setenv('PANEL_ADMIN_USERNAME','synthetic-protocol-admin')
    encoded='pbkdf2_sha256$200000$'+base64.b64encode(b'synthetic-salt').decode()+'$'+base64.b64encode(b'x'*32).decode()
    monkeypatch.setenv('PANEL_ADMIN_PASSWORD_HASH',encoded)
    username,digest=legacy_admin_identity.configuration_binding()
    bridge_key='synthetic-recovery-bridge-key-'+'x'*40
    monkeypatch.setenv('QR_BRIDGE_TOKEN',bridge_key)
    credential=Fernet(os.environ['INTEGRATION_FERNET_KEY'].encode()).encrypt(bridge_key.encode()).decode()
    async with d['migrator'].begin() as db:
        await db.execute(text("""UPDATE panel_accounts SET role='admin',auth_provider='legacy_admin',email=NULL,password_hash=NULL,
            legacy_username=:username,legacy_credential_digest=:digest WHERE id=:a"""),{'a':d['account'],'username':username,'digest':digest})
        await db.execute(text('UPDATE qr_channels SET credential_hash=:hash,credential_encrypted=:encrypted WHERE id=:c'),
            {'hash':hashlib.sha256(bridge_key.encode()).hexdigest(),'encrypted':credential,'c':d['channel']})
        await db.execute(text('UPDATE tenants SET whatsapp_channel_id=:c WHERE id=:t'),{'t':d['a'],'c':d['channel']})
        # Manual inbox recovery is a Profissional/Elite feature after the
        # Essencial tier loses conversations.manual. This test exercises the
        # authorized Professional path; other tests retain Essencial basic alerts.
        await db.execute(text("UPDATE tenant_plan_controls SET manual_plan_code='profissional' WHERE tenant_id=:t"),
            {'t':d['a']})
    monkeypatch.setattr(qr_legacy,'channel_id',lambda t:d['channel'] if t==d['a'] else uuid4())
    monkeypatch.setattr(management_api,'get_session_factory',lambda:d['factory'])
    monkeypatch.setattr(inbox_runtime,'get_session_factory',lambda:d['factory'])
    async with d['factory']() as db:
        async with tenant_transaction(db,d['a']):await inbox_control.register_channel(db,d['a'],d['channel'])
    actor=Actor('admin',account_id=d['account']);payload=api.RecoverConfirmation(protocol=a['protocol'])
    with pytest.raises(HTTPException) as missing:await api.recover_confirmation(d['a'],payload,actor)
    assert missing.value.status_code==404
    body='Olá, Orion! Protocolo: '+a['protocol']
    async with d['factory']() as db:
        async with tenant_transaction(db,d['a']):
            await inbox_runtime.record_inbound(db,d['a'],d['channel'],phone(921),'other-contact',body)
    with pytest.raises(HTTPException) as wrong:await api.recover_confirmation(d['a'],payload,actor)
    assert wrong.value.status_code==404
    async with d['factory']() as db:
        async with tenant_transaction(db,d['a']):
            await inbox_runtime.record_inbound(db,d['a'],d['channel'],phone(920),'verified-contact',body)
    with pytest.raises(HTTPException) as unapproved:await api.recover_confirmation(d['a'],payload,actor)
    assert unapproved.value.status_code==403
    monkeypatch.setenv('APPOINTMENT_COMMUNICATIONS_PILOT_CUSTOMER_PHONES',phone(920).lstrip('+'))
    first=await api.recover_confirmation(d['a'],payload,actor)
    assert first['status']=='queued' and not first['accepted']
    assert await api.recover_confirmation(d['a'],payload,actor)==first
    sent=[]
    async def sender(t,c,op,*,message):
        assert (t,c,message['to'])==(d['a'],d['channel'],phone(920).lstrip('+'))
        # Model 02 deliberately omits the protocol: validate its approved native markup and scoped link.
        assert '✂️ Serviço: *' in message['text'] and '/a#' in message['text']
        assert '🔗 *Gerencie seu agendamento:*' in message['text']
        sent.append(op);return {'accepted':True,'state':'accepted','provider_id':'synthetic-provider-id'}
    context={'inbox_qr_sender':sender}
    assert await inbox_runtime.process_inbox_message(context,str(d['a']),str(first['operation_id']))=='accepted'
    assert await inbox_runtime.process_inbox_message(context,str(d['a']),str(first['operation_id']))=='already_claimed'
    assert len(sent)==1
    async with d['factory']() as db:
        async with tenant_transaction(db,d['a']):
            assert (await db.execute(text("SELECT count(*) FROM inbox_messages WHERE tenant_id=:t AND direction='out'"),{'t':d['a']})).scalar_one()==1
            assert (await db.execute(text('SELECT count(*) FROM appointment_access WHERE tenant_id=:t'),{'t':d['a']})).scalar_one()==1


@pytest.mark.asyncio
async def test_reschedule_creates_new_version_and_cancels_old_reminder(comm_db):
    d=comm_db;a=await booking(d)
    async with d['factory']() as db:
        await reschedule_appointment(db,d['a'],a['id'],start_at(d['day'],14),context=customer_actor(a['customer_id'],'public_site'))
    items=await jobs(d)
    reminders=[r for r in items if r['event']=='reminder']
    assert len(reminders)==2 and sum(r['status']=='pending' for r in reminders)==1
    assert any(r['event']=='professional_rescheduled' and r['recipient_id']==d['pro'] for r in items)


@pytest.mark.asyncio
async def test_staff_proposal_retains_original_until_verified_customer_accepts(comm_db):
    from app.appointment_changes import propose_if_required
    from app.appointment_lifecycle import LifecycleActor
    from app.booking_api import RescheduleRequest
    d=comm_db;a=await booking(d)
    async with d['migrator'].begin() as db:
        await db.execute(text('UPDATE appointment_communication_settings SET reschedule_acceptance=true WHERE tenant_id=:t'),{'t':d['a']})
    payload=RescheduleRequest(start_at=start_at(d['day'],14),expected_updated_at=a['updated_at'],idempotency_key='proposal-test-one')
    actor=LifecycleActor(role='owner',source='panel',account_id=d['account'])
    async with d['factory']() as db:proposal=await propose_if_required(db,d['a'],a['id'],payload,actor)
    assert proposal['start_at']==a['start_at'] and proposal['change_status']=='pending_customer_acceptance'
    async with d['factory']() as db:replay=await propose_if_required(db,d['a'],a['id'],payload,actor)
    assert replay['pending_change']['id']==proposal['pending_change']['id']
    items=await jobs(d)
    assert sum(r['event']=='customer_proposal' for r in items)==1
    assert any(r['event']=='reminder' and r['status']=='pending' for r in items)
    async with d['factory']() as db:
        changed=await reschedule_appointment(db,d['a'],a['id'],start_at(d['day'],12),
            context=customer_actor(a['customer_id'],'public_site'),proposal_id=proposal['pending_change']['id'])
    assert changed['start_at']==payload.start_at
    assert any(r['event']=='professional_rescheduled' for r in await jobs(d))


@pytest.mark.asyncio
async def test_owner_diagnostics_show_current_recipient_without_tokens_and_disable_pending_jobs(comm_db):
    from app.appointment_communications_api import configuration,save_configuration,Settings
    from app.catalog_api import Actor
    d=comm_db;await booking(d)
    data=await configuration(d['a'],Actor('support'))
    assert data['timezone']=='America/Campo_Grande'
    assert any((n['recipient_label'] or '').casefold()=='cliente sintético' for n in data['notifications'] if n['recipient_kind']=='customer')
    assert 'body_encrypted' not in json.dumps(data) and 'token_hash' not in json.dumps(data)
    saved=await save_configuration(d['a'],Settings(expected_version=1,enabled=False,channel_id=d['channel']),Actor('support'))
    assert saved['version']==2
    assert all(item['status']=='cancelled' for item in await jobs(d))


@pytest.mark.asyncio
async def test_offline_preflight_retries_only_definite_no_send_with_limit(comm_db,monkeypatch):
    d=comm_db;await booking(d)
    monkeypatch.delenv('QR_BRIDGE_URL',raising=False)
    staff=next(r for r in await jobs(d) if r['event']=='professional_new')
    assert await notifications.process_notification({},str(d['a']),str(staff['id']))=='channel_unavailable'
    retry=next(r for r in await jobs(d) if r['id']==staff['id'])
    assert retry['status']=='pending' and retry['attempts']==1
    async with d['migrator'].begin() as db:
        await db.execute(text("UPDATE appointment_notifications SET attempts=4,due_at=now()-interval '1 second' WHERE id=:id"),{'id':staff['id']})
    assert await notifications.process_notification({},str(d['a']),str(staff['id']))=='channel_unavailable'
    failed=next(r for r in await jobs(d) if r['id']==staff['id'])
    assert failed['status']=='failed' and failed['attempts']==5


@pytest.mark.asyncio
async def test_missing_professional_phone_never_falls_back_to_owner(comm_db):
    d=comm_db;await booking(d)
    async with d['migrator'].begin() as db:
        await db.execute(text('UPDATE professionals SET phone_e164=NULL WHERE tenant_id=:t AND id=:id'),{'t':d['a'],'id':d['pro']})
    staff=next(r for r in await jobs(d) if r['event']=='professional_new')
    async def forbidden(*args):pytest.fail('No fallback recipient may be contacted')
    assert await notifications.process_notification({'appointment_sender':forbidden},str(d['a']),str(staff['id']))=='professional_contact_unavailable'


@pytest.mark.asyncio
async def test_unknown_receipt_inspection_never_claims_delivery_and_does_not_starve_others(comm_db):
    d=comm_db;await booking(d)
    notice=next(r for r in await jobs(d) if r['event']=='professional_new')
    async with d['migrator'].begin() as db:
        await db.execute(text("UPDATE appointment_notifications SET status='unknown',attempted_at=now()-interval '10 minutes',updated_at=now()-interval '10 minutes' WHERE id=:id"),{'id':notice['id']})
    before=next(r for r in await jobs(d) if r['id']==notice['id'])['updated_at']
    async def inspect(*args):return {'operation_id':str(notice['id']),'accepted':False,'delivery':'delivered'}
    assert await notifications.reconcile_notification({'appointment_inspector':inspect},str(d['a']),str(notice['id']))=='unknown'
    after=next(r for r in await jobs(d) if r['id']==notice['id'])
    assert after['status']=='unknown' and after['updated_at']>before


@pytest.mark.asyncio
async def test_anonymous_booking_response_cannot_issue_customer_capability(comm_db,monkeypatch):
    from app import public_site_api
    d=comm_db;a=await booking(d)
    async def resolve(_slug):return d['a']
    monkeypatch.setattr(public_site_api,'resolve_public',resolve)
    async def denied(*args):pytest.fail('Anonymous caller must not get an existing customer capability')
    monkeypatch.setattr(confirmation.access,'issue',denied)
    followup=await confirmation.public_followup('synthetic',a,phone(920))
    assert 'management_url' not in followup


@pytest.mark.asyncio
async def test_return_interval_must_belong_to_the_authorized_company(comm_db):
    from app.appointment_communications_api import configuration,save_configuration,Settings
    from app.catalog_api import Actor
    from fastapi import HTTPException
    d=comm_db
    with pytest.raises(HTTPException) as error:
        await save_configuration(d['a'],Settings(expected_version=1,enabled=False,channel_id=d['channel'],return_intervals={uuid4():21}),Actor('support'))
    assert error.value.status_code==422
    await save_configuration(d['a'],Settings(expected_version=1,enabled=False,channel_id=d['channel'],return_intervals={d['cut']:21}),Actor('support'))
    data=await configuration(d['a'],Actor('support'))
    rule=next(item for item in data['return_services'] if item['id']==str(d['cut']))
    assert rule['days']==21


@pytest.mark.asyncio
async def test_pilot_sender_blocks_other_recipient_even_in_same_company(comm_db,monkeypatch):
    d=comm_db
    monkeypatch.setenv('APPOINTMENT_COMMUNICATIONS_PILOT_ONLY','1')
    monkeypatch.setenv('APPOINTMENT_COMMUNICATIONS_PILOT_TENANT_ID',str(d['a']))
    monkeypatch.setenv('APPOINTMENT_COMMUNICATIONS_PILOT_PHONE',PRO_PHONE.lstrip('+'))
    await booking(d)
    reminder=next(r for r in await jobs(d) if r['event']=='reminder')
    async with d['migrator'].begin() as db:
        await db.execute(text("UPDATE appointment_notifications SET due_at=now()-interval '1 second',expires_at=now()+interval '1 minute' WHERE id=:id"),{'id':reminder['id']})
    async def denied(*a,**k):pytest.fail('A different contact must not receive pilot messages')
    assert await notifications.process_notification({'appointment_sender':denied},str(d['a']),str(reminder['id']))=='pilot_recipient_not_authorized'
    assert not notifications.access.tenant_enabled(d['b'])


@pytest.mark.asyncio
async def test_retention_removes_old_payloads_without_erasing_booking_history(comm_db):
    from app import appointment_access as access
    d=comm_db;a=await booking(d)
    notice=next(r for r in await jobs(d) if r['event']=='professional_new')
    async with d['factory']() as db:
        async with tenant_transaction(db,d['a']):
            await access.issue(db,d['a'],a['id'])
    async with d['migrator'].begin() as db:
        await db.execute(text("UPDATE appointment_notifications SET status='unknown',due_at=now()-interval '92 days',expires_at=now()-interval '91 days',created_at=now()-interval '92 days',body_encrypted='synthetic',recipient_hash=:hash,previous=CAST(:previous AS jsonb) WHERE id=:id"),{'id':notice['id'],'hash':'a'*64,'previous':'{"test":"old"}'})
        await db.execute(text("UPDATE appointment_access SET expires_at=now()-interval '91 days' WHERE tenant_id=:t"),{'t':d['a']})
        await db.execute(text("INSERT INTO appointment_confirmation_receipts(tenant_id,channel_key,message_id,appointment_id,appointment_version,body_encrypted,created_at) VALUES(:t,'test','old',:id,:v,'synthetic',now()-interval '91 days')"),{'t':d['a'],'id':a['id'],'v':a['updated_at']})
    assert await notifications.cleanup_notification_payloads({})>=3
    old=next(r for r in await jobs(d) if r['id']==notice['id'])
    assert old['status']=='unknown' and old['body_encrypted'] is None and old['recipient_hash'] is None and old['previous']=={}
    async with d['migrator'].connect() as db:
        assert (await db.execute(text('SELECT status FROM appointments WHERE id=:id'),{'id':a['id']})).scalar_one()=='confirmed'
        assert (await db.execute(text('SELECT count(*) FROM appointment_history WHERE appointment_id=:id'),{'id':a['id']})).scalar_one()>0


@pytest.mark.asyncio
async def test_real_arq_dispatches_persistent_notifications_once(comm_db):
    import os
    from arq.connections import create_pool,RedisSettings
    from arq.worker import Worker
    from app.worker import WorkerSettings
    d=comm_db;await booking(d)
    notice=next(r for r in await jobs(d) if r['event']=='professional_new')
    queue='arq:appointment-test:'+str(uuid4())
    pool=await create_pool(RedisSettings.from_dsn(os.environ['REDIS_URL']))
    sent=[]
    async def sender(*args):sent.append(args);return {'state':'accepted','accepted':True}
    try:
        for _ in range(2):
            await pool.enqueue_job('process_appointment_notification',str(d['a']),str(notice['id']),_queue_name=queue,_job_id='appointment-test-'+str(uuid4()))
        worker=Worker(WorkerSettings.functions,redis_pool=pool,queue_name=queue,burst=True,handle_signals=False,
            max_jobs=1,poll_delay=.01,keep_result=0,ctx={'appointment_sender':sender})
        try:
            await asyncio.wait_for(worker.async_run(),15)
            assert worker.jobs_complete==2 and worker.jobs_failed==0
            assert len(sent)==1
            assert next(r for r in await jobs(d) if r['id']==notice['id'])['status']=='accepted'
        finally:await worker.close()
    finally:
        # Only this test's randomly named queue is eligible for removal.
        cleanup=await create_pool(RedisSettings.from_dsn(os.environ['REDIS_URL']))
        await cleanup.delete(queue);await cleanup.aclose()
