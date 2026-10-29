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
            for table in ('appointment_change_proposals','appointment_confirmation_receipts','appointment_notifications','appointment_communication_settings','contact_consents','qr_channels','tenant_memberships'):
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
