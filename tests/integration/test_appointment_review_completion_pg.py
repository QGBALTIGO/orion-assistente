"""Native reviews after trusted completion, with synthetic transport only."""
from datetime import timedelta
import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text

from app import appointment_access as access, appointment_access_api as api
from app import appointment_notifications as notifications
from app.appointment_review_completion import completion_evidence
from app.database import tenant_transaction
from app.management_models import transition_appointment
from app.main import app
from tests.integration.test_booking import booking_db, phone
from tests.integration.test_appointment_notifications_pg import comm_db, booking, jobs
from tests.integration.test_appointment_trigger_matrix_pg import move_to_past, mark_due, fake_transport

pytestmark=pytest.mark.integration


@pytest_asyncio.fixture
async def review_db(comm_db,monkeypatch):
    d=comm_db
    monkeypatch.setattr(api,'get_session_factory',lambda:d['factory'])
    async def no_rate_limit(*args,**kwargs):pass
    monkeypatch.setattr(api,'public_limit',no_rate_limit)
    async with d['migrator'].begin() as db:
        await db.execute(text('''UPDATE appointment_communication_settings
            SET review=true,review_delay_min=15,return_invitation=true WHERE tenant_id=:t'''),{'t':d['a']})
        await db.execute(text('''INSERT INTO management_site_preferences(tenant_id,public_slug,public_enabled)
            VALUES(:t,:slug,true)'''),{'t':d['a'],'slug':'review-'+uuid4().hex[:20]})
        await db.execute(text('''INSERT INTO appointment_return_intervals(tenant_id,service_id,days)
            VALUES(:t,:service,14)'''),{'t':d['a'],'service':d['cut']})
    try:yield d
    finally:
        async with d['migrator'].begin() as db:
            for table in ('appointment_message_consents','financial_entries','management_requests','management_site_preferences'):
                await db.execute(text('DELETE FROM '+table+' WHERE tenant_id=:t'),{'t':d['a']})


async def complete(d,appointment,*,operator='owner',automated=False):
    payload=SimpleNamespace(expected_status='confirmed',expected_updated_at=None,coupon_id=None,
        final_total_cents=None,idempotency_key='review-completion-'+str(appointment['id']))
    async with d['factory']() as db:
        async with tenant_transaction(db,d['a']):
            return await transition_appointment(db,d['a'],appointment['id'],'completed',payload,operator,
                automated=automated,authorized_professional_id=d['pro'] if operator=='professional' else None)


async def token_for(d,appointment):
    async with d['factory']() as db:
        async with tenant_transaction(db,d['a']):
            return (await access.issue(db,d['a'],appointment['id'])).split('#')[1]


async def scoped_consent(d,appointment,*,contact=None,operational=True,review=True):
    # Explicit synthetic consent; never infers a choice from the booking itself.
    async with d['migrator'].begin() as db:
        await db.execute(text('''INSERT INTO appointment_message_consents
            (tenant_id,appointment_id,customer_id,contact_e164,operational_opt_in,review_opt_in,
             source,actor,purpose_version,evidence_hash)
            VALUES(:t,:id,:customer,:phone,:operational,:review,'panel','{"role":"owner"}',1,:hash)'''),
            {'t':d['a'],'id':appointment['id'],'customer':appointment['customer_id'],
             'phone':contact or phone(920),'operational':operational,'review':review,'hash':'b'*64})


@pytest.mark.asyncio
@pytest.mark.parametrize('operator,automated',[('owner',False),('professional',False),('system',True)])
async def test_trusted_completion_enqueues_once_and_native_link_accepts_one_review(review_db,operator,automated):
    d=review_db;a=await booking(d)
    await move_to_past(d,a['id'])
    assert (await complete(d,a,operator=operator,automated=automated))['status']=='completed'
    # The original operation receipt must not create or cancel a second invite.
    assert (await complete(d,a,operator=operator,automated=automated))['status']=='completed'
    notices=[dict(n) for n in await jobs(d) if n['event']=='review']
    assert len(notices)==1 and notices[0]['status']=='pending'
    notice=notices[0]
    assert timedelta(minutes=14)<notice['due_at']-notice['created_at']<timedelta(minutes=16)
    async with d['factory']() as db:
        async with tenant_transaction(db,d['a']):
            evidence=await completion_evidence(db,d['a'],a['id'])
            assert evidence.can_review
            assert (evidence.staff_completed_at is None)==automated
    token=await token_for(d,a)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='https://orionagenda.pro') as client:
        viewed=await client.post('/api/appointment-access',json={'token':token})
        assert viewed.status_code==200 and viewed.json()['can_review'] is True
        assert bool(viewed.json()['return_suggestions']) is not automated
        await mark_due(d,notice['id'])
        ctx,sent=fake_transport()
        assert await notifications.process_notification(ctx,str(d['a']),str(notice['id']))=='accepted'
        assert await notifications.process_notification(ctx,str(d['a']),str(notice['id']))=='accepted'
        assert len(sent)==1 and 'Deixe sua avaliação' in sent[0][3]
        reviewed=await client.post('/api/appointment-access',json={'token':token,'action':'review','rating':5})
        assert reviewed.status_code==200 and reviewed.json()['review']['rating']==5
        assert reviewed.json()['can_review'] is False
        assert (await client.post('/api/appointment-access',json={'token':token,'action':'review','rating':4})).status_code==409


@pytest.mark.asyncio
@pytest.mark.parametrize('case',['status_only','untrusted_actor','missing_version','premature_automatic','future_staff','no_show','cancelled'])
async def test_untrusted_or_early_completion_neither_schedules_nor_accepts_review(review_db,case):
    d=review_db;a=await booking(d)
    await move_to_past(d,a['id'])
    details={'v':1,'source':'system','actor':{'role':'system'},'requested_by':'system',
             'previous':{'status':'confirmed'}}
    if case=='untrusted_actor':details['actor']['role']='customer'
    if case=='missing_version':details.pop('v')
    if case=='future_staff':details={'v':1,'source':'panel','actor':{'role':'owner'}}
    status=case if case in {'no_show','cancelled'} else 'completed'
    async with d['migrator'].begin() as db:
        await db.execute(text('UPDATE appointments SET status=:status WHERE tenant_id=:t AND id=:id'),
                         {'t':d['a'],'id':a['id'],'status':status})
        if case=='premature_automatic':
            await db.execute(text("""UPDATE appointments SET end_at=clock_timestamp()+interval '20 minutes',
                occupied_until=clock_timestamp()+interval '30 minutes' WHERE tenant_id=:t AND id=:id"""),
                {'t':d['a'],'id':a['id']})
        if case=='future_staff':
            await db.execute(text("""UPDATE appointments SET start_at=clock_timestamp()+interval '1 hour',
                end_at=clock_timestamp()+interval '2 hours',occupied_from=clock_timestamp()+interval '55 minutes',
                occupied_until=clock_timestamp()+interval '2 hours 10 minutes' WHERE tenant_id=:t AND id=:id"""),
                {'t':d['a'],'id':a['id']})
        if case!='status_only':
            await db.execute(text('''INSERT INTO appointment_history(tenant_id,appointment_id,event,operator,details)
                VALUES(:t,:id,'completed','system',CAST(:details AS jsonb))'''),
                {'t':d['a'],'id':a['id'],'details':json.dumps(details)})
    async with d['factory']() as db:
        async with tenant_transaction(db,d['a']):
            assert not (await completion_evidence(db,d['a'],a['id'])).can_review
            await notifications.enqueue_event(db,d['a'],a['id'],'completed',details)
    assert not [n for n in await jobs(d) if n['event']=='review']
    token=await token_for(d,a)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='https://orionagenda.pro') as client:
        viewed=await client.post('/api/appointment-access',json={'token':token})
        assert viewed.status_code==200 and viewed.json()['can_review'] is False
        assert (await client.post('/api/appointment-access',json={'token':token,'action':'review','rating':5})).status_code==409


@pytest.mark.asyncio
@pytest.mark.parametrize('matching_phone',[True,False])
async def test_scoped_review_requires_same_current_customer_phone_and_never_adds_marketing(review_db,matching_phone):
    d=review_db;a=await booking(d)
    await scoped_consent(d,a,contact=phone(920 if matching_phone else 921))
    async with d['migrator'].begin() as db:
        await db.execute(text('UPDATE contact_consents SET operational_opt_in=false,marketing_opt_in=false WHERE tenant_id=:t'),{'t':d['a']})
    await move_to_past(d,a['id']);await complete(d,a)
    notice=next(n for n in await jobs(d) if n['event']=='review')
    await mark_due(d,notice['id'])
    ctx,sent=fake_transport()
    result=await notifications.process_notification(ctx,str(d['a']),str(notice['id']))
    assert result==('accepted' if matching_phone else 'communication_not_authorized')
    assert len(sent)==int(matching_phone)
    if sent:
        assert 'Deixe sua avaliação' in sent[0][3]
        assert 'Agende novamente' not in sent[0][3]
    stored=next(n for n in await jobs(d) if n['id']==notice['id'])
    assert stored['requires_marketing'] is False


@pytest.mark.asyncio
async def test_marketing_revocation_after_body_preparation_prevents_sending_return_cta(review_db,monkeypatch):
    d=review_db;a=await booking(d)
    await scoped_consent(d,a)
    await move_to_past(d,a['id']);await complete(d,a)
    notice=next(n for n in await jobs(d) if n['event']=='review')
    await mark_due(d,notice['id'])
    prepare=notifications.prepare_body
    async def revoke_after_preparation(db,tenant,job,row,settings):
        body,requires_marketing=await prepare(db,tenant,job,row,settings)
        assert requires_marketing and 'Agende novamente' in body
        await db.execute(text('UPDATE contact_consents SET marketing_opt_in=false WHERE tenant_id=:t'),{'t':tenant})
        return body,requires_marketing
    monkeypatch.setattr(notifications,'prepare_body',revoke_after_preparation)
    ctx,sent=fake_transport()
    assert await notifications.process_notification(ctx,str(d['a']),str(notice['id']))=='cancelled'
    assert not sent
    stored=next(n for n in await jobs(d) if n['id']==notice['id'])
    assert stored['requires_marketing'] is True
    assert stored['last_error']=='marketing_not_authorized'


@pytest.mark.asyncio
@pytest.mark.parametrize('central',[True,False])
async def test_reminder_offline_preflight_retries_in_fifteen_seconds_with_original_expiry(review_db,monkeypatch,central):
    from app import qr_channels
    d=review_db
    if central:
        monkeypatch.setenv('ORION_CENTRAL_APPOINTMENT_WHATSAPP_ENABLED','1')
        monkeypatch.setenv('ORION_CENTRAL_NOTIFICATIONS_ENABLED','1')
        monkeypatch.setattr(notifications,'central_channel',lambda:(d['a'],d['channel']))
    else:
        monkeypatch.setenv('ORION_CENTRAL_NOTIFICATIONS_ENABLED','0')
    async def disconnected(*args):return None,'offline'
    monkeypatch.setattr(qr_channels,'_canonical',disconnected)
    await booking(d)
    notice=next(n for n in await jobs(d) if n['event']=='reminder')
    async with d['migrator'].begin() as db:
        expiry=(await db.execute(text("""UPDATE appointment_notifications
            SET due_at=clock_timestamp()-interval '1 second',expires_at=clock_timestamp()+interval '119 seconds'
            WHERE tenant_id=:t AND id=:id RETURNING expires_at"""),{'t':d['a'],'id':notice['id']})).scalar_one()
    assert await notifications.process_notification({},str(d['a']),str(notice['id']))=='channel_unavailable'
    async with d['migrator'].connect() as db:
        stored=(await db.execute(text('''SELECT *,clock_timestamp() AS checked_at FROM appointment_notifications
            WHERE tenant_id=:t AND id=:id'''),{'t':d['a'],'id':notice['id']})).mappings().one()
    assert stored['status']=='pending' and stored['expires_at']==expiry
    assert timedelta(seconds=10)<stored['due_at']-stored['checked_at']<=timedelta(seconds=15)
    assert stored['body_encrypted'] is None and stored['attempted_at'] is None
    async with d['migrator'].begin() as db:
        await db.execute(text("""UPDATE appointment_notifications SET due_at=clock_timestamp()-interval '1 second',
            expires_at=clock_timestamp()+interval '10 seconds' WHERE tenant_id=:t AND id=:id"""),
            {'t':d['a'],'id':notice['id']})
    assert await notifications.process_notification({},str(d['a']),str(notice['id']))=='channel_unavailable'
    failed=next(n for n in await jobs(d) if n['id']==notice['id'])
    assert failed['status']=='failed' and failed['body_encrypted'] is None
