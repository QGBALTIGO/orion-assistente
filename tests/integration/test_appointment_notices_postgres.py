"""Real PostgreSQL transactions/RLS with an explicit transport double, no messages sent."""
import asyncio
from datetime import datetime, time, timezone
from uuid import UUID
import pytest
import pytest_asyncio
from sqlalchemy import text
from app import appointment_notice_worker as worker
from app.booking_service import cancel_appointment
from app.catalog_api import Actor, authenticate
from app.database import tenant_transaction
from test_management import management_db, customer, appointment  # noqa: F401
from test_appointment_lifecycle import agenda_db, hold, confirm, detail, operation  # noqa: F401

pytestmark = pytest.mark.integration
ACCOUNT = '5511999998888'

@pytest_asyncio.fixture
async def notice_db(agenda_db, monkeypatch):
    d = agenda_db
    monkeypatch.setenv('ORION_APPOINTMENT_NOTICES_ENABLED','1')
    monkeypatch.setenv('QR_AI_TENANT_ID',str(d['a']))
    monkeypatch.setattr(worker,'get_session_factory',lambda:d['factory'])
    packets = []
    async def transport(path,method='GET',payload=None):
        packets.append((path,payload))
        if path=='readiness':
            return {'ready':True,'tenant_id':str(d['a']),'account_phone':ACCOUNT}
        return {'id':payload['id'],'tenant_id':payload['tenant_id'],'account_phone':ACCOUNT,
                'status':'sent' if path=='send' else 'delivered','message_id':'SYNTHETIC_RECEIPT_001'}
    monkeypatch.setattr(worker,'bridge',transport)
    yield dict(d,packets=packets,transport=transport)

async def rows(d,identity):
    async with d['factory']() as db:
        async with tenant_transaction(db,d['a']):
            return [dict(r) for r in (await db.execute(text('''SELECT * FROM appointment_notices
                WHERE tenant_id=:tenant AND appointment_id=:id ORDER BY created_at,id'''),
                {'tenant':d['a'],'id':UUID(str(identity))})).mappings().all()]

async def due(d,notice):
    async with d['migrator'].begin() as db:
        await db.execute(text("UPDATE appointment_notices SET next_attempt_at=now()-interval '1 second',updated_at=now()-interval '1 minute' WHERE id=:id"),{'id':notice['id']})

async def process(d,notice):
    return await worker.process_appointment_notice({},str(d['a']),str(notice['id']))

@pytest.mark.asyncio
async def test_panel_confirmation_move_cancel_commit_notices_and_invalidate_old_times(notice_db):
    d=notice_db
    temporary=await hold(d)
    assert await rows(d,temporary['id'])==[]
    booking=await confirm(d,temporary)
    assert [r['event'] for r in await rows(d,booking['id'])]==['confirmed']
    move=await d['client'].post(d['agenda']+f"/appointments/{booking['id']}/reschedule",
        json=operation(booking,start_at=datetime.combine(d['day'],time(11),d['zone']).isoformat()))
    assert move.status_code==200,move.text
    moved=move.json()
    pending=await rows(d,booking['id'])
    assert [r['status'] for r in pending]==['superseded','pending']
    assert pending[-1]['snapshot']['previous_start_at']
    cancelled=await d['client'].post(d['agenda']+f"/appointments/{booking['id']}/cancel",
        json=operation(moved,requested_by='professional',reason='INTERNAL_NOT_FOR_CUSTOMER'))
    assert cancelled.status_code==200,cancelled.text
    pending=await rows(d,booking['id'])
    assert [r['status'] for r in pending]==['superseded','superseded','pending']
    assert pending[-1]['event']=='cancelled'
    assert 'INTERNAL_NOT_FOR_CUSTOMER' not in str(pending[-1]['snapshot'])
    assert d['packets']==[] # Saving is purely transactional, not a network operation.
    displayed=(await detail(d,booking['id']))['whatsapp_notifications']
    assert displayed['enabled'] and displayed['items'][0]['status_label']=='Aguardando envio'
    assert all('snapshot' not in row and 'message_id' not in row for row in displayed['items'])

@pytest.mark.asyncio
async def test_duplicate_confirmation_is_one_outbox_record_and_only_one_send(notice_db):
    d=notice_db
    temporary=await hold(d);payload=operation(temporary)
    responses=await asyncio.gather(*(d['client'].post(d['agenda']+f"/appointments/{temporary['id']}/confirm",json=payload) for _ in range(5)))
    assert all(r.status_code==200 for r in responses)
    pending=await rows(d,temporary['id']);assert len(pending)==1
    results=await asyncio.gather(*(process(d,pending[0]) for _ in range(6)))
    assert results.count('sent')==1
    assert len([p for p in d['packets'] if p[0]=='send'])==1
    latest=(await rows(d,temporary['id']))[0]
    assert latest['status']=='sent' # HTTP acceptance is not device delivery.
    await due(d,latest)
    assert await process(d,latest)=='delivered'
    assert (await rows(d,temporary['id']))[0]['status']=='delivered'

@pytest.mark.asyncio
async def test_unknown_timeout_is_receipt_only_never_automatic_replay(notice_db,monkeypatch):
    d=notice_db;booking=await confirm(d,await hold(d));notice=(await rows(d,booking['id']))[0]
    async def transport(path,method='GET',payload=None):
        if path=='send':
            d['packets'].append((path,payload));raise TimeoutError()
        if path=='receipt':
            d['packets'].append((path,payload));return {'id':payload['id'],'status':'unknown'}
        return await d['transport'](path,method,payload)
    monkeypatch.setattr(worker,'bridge',transport)
    assert await process(d,notice)=='uncertain'
    for _ in range(3):
        await due(d,notice);assert await process(d,notice)=='uncertain'
    assert len([p for p in d['packets'] if p[0]=='send'])==1
    # An explicit retry also cannot create a second copy of an uncertain send.
    result=await d['client'].post(d['agenda']+f"/appointments/{booking['id']}/notifications/retry")
    assert result.status_code==200 and result.json()['items'][0]['can_retry'] is False
    assert (await rows(d,booking['id']))[0]['status']=='uncertain'
    monkeypatch.setattr(worker,'bridge',d['transport']);await due(d,notice)
    assert await process(d,notice)=='delivered'

@pytest.mark.asyncio
async def test_disconnected_notice_survives_and_resumes_without_duplicate(notice_db,monkeypatch):
    d=notice_db;booking=await confirm(d,await hold(d));notice=(await rows(d,booking['id']))[0]
    async def offline(*args,**kwargs):return {'ready':False,'tenant_id':str(d['a']),'account_phone':ACCOUNT,'error':'not_connected'}
    monkeypatch.setattr(worker,'bridge',offline)
    for _ in range(9):
        await due(d,notice);assert await process(d,notice)=='blocked'
    assert (await rows(d,booking['id']))[0]['status']=='blocked' and d['packets']==[]
    await due(d,notice)
    retry=await d['client'].post(d['agenda']+f"/appointments/{booking['id']}/notifications/retry")
    assert retry.status_code==200 and retry.json()['items'][0]['status']=='pending'
    monkeypatch.setattr(worker,'bridge',d['transport'])
    assert await process(d,notice)=='sent'

@pytest.mark.asyncio
async def test_unsent_wa_customer_change_supersedes_pending_without_extra_copy(notice_db):
    d=notice_db;booking=await confirm(d,await hold(d))
    async with d['factory']() as db:
        await cancel_appointment(db,d['a'],UUID(booking['id']),customer_phone=booking['customer_phone'],future_only=True,source='whatsapp_qr')
    pending=await rows(d,booking['id'])
    assert len(pending)==1 and pending[0]['status']=='superseded'
    assert await process(d,pending[0])=='not_pending' and d['packets']==[]

@pytest.mark.asyncio
@pytest.mark.parametrize('change',['phone','expired','version','binding'])
async def test_changed_scope_contact_or_facts_cannot_send_old_notice(notice_db,monkeypatch,change):
    d=notice_db;booking=await confirm(d,await hold(d));notice=(await rows(d,booking['id']))[0]
    async with d['migrator'].begin() as db:
        if change=='phone':await db.execute(text("UPDATE customers SET whatsapp_e164='+5511988887777' WHERE id=:id"),{'id':UUID(booking['customer_id'])})
        if change=='version':await db.execute(text("UPDATE appointments SET updated_at=updated_at+interval '1 second' WHERE id=:id"),{'id':UUID(booking['id'])})
        if change=='expired':await db.execute(text("UPDATE appointment_notices SET expires_at=now()-interval '1 second' WHERE id=:id"),{'id':notice['id']})
    if change=='binding':monkeypatch.setenv('QR_AI_TENANT_ID',str(d['b']))
    result=await process(d,notice)
    assert result=={'phone':'failed','expired':'expired','version':'superseded','binding':'blocked'}[change]
    assert not [p for p in d['packets'] if p[0]=='send']

@pytest.mark.asyncio
@pytest.mark.parametrize('event,path',[('completed','complete'),('no_show','no-show')])
async def test_final_states_emit_notices_without_inventing_payment(notice_db,event,path):
    d=notice_db;person=await customer(d,phone='11912345678');identity=await appointment(d,person['id'])
    response=await d['client'].post(d['base']+f'/appointments/{identity}/{path}',json=operation(await detail(d,identity)))
    assert response.status_code==200,response.text
    pending=await rows(d,identity)
    assert len(pending)==1 and pending[0]['event']==event and pending[0]['status']=='pending'
    assert await process(d,pending[0])=='sent'
    sent=[p[1]['text'] for p in d['packets'] if p[0]=='send'][0]
    assert 'pagamento' not in sent.lower() and 'R$' not in sent

@pytest.mark.asyncio
async def test_rollback_removes_notice_with_business_mutation_and_rls_hides_other_tenant(notice_db):
    d=notice_db;booking=await confirm(d,await hold(d))
    from app.appointment_notices import enqueue_notice
    with pytest.raises(RuntimeError,match='synthetic_rollback'):
        async with d['factory']() as db:
            async with tenant_transaction(db,d['a']):
                await enqueue_notice(db,d['a'],UUID(booking['id']),'completed',{'source':'panel'})
                raise RuntimeError('synthetic_rollback')
    assert len(await rows(d,booking['id']))==1
    async with d['factory']() as db:
        async with tenant_transaction(db,d['b']):
            assert (await db.execute(text('SELECT id FROM appointment_notices'))).all()==[]
    from sqlalchemy.exc import DBAPIError
    with pytest.raises(DBAPIError):
        async with d['support'].connect() as db:
            await db.execute(text('SELECT snapshot FROM appointment_notices'))
    other=await d['client'].post(d['other_agenda']+f"/appointments/{booking['id']}/notifications/retry")
    assert other.status_code==404
    d['app'].dependency_overrides[authenticate]=lambda:Actor('member',d['a'],d['account'],'viewer')
    denied=await d['client'].post(d['agenda']+f"/appointments/{booking['id']}/notifications/retry")
    assert denied.status_code==403

@pytest.mark.asyncio
async def test_professional_lock_blocks_outdated_dispatch_while_appointment_changes(notice_db):
    d=notice_db;booking=await confirm(d,await hold(d));notice=(await rows(d,booking['id']))[0]
    async with d['factory']() as db:
        async with tenant_transaction(db,d['a']):
            await db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:scope,0))'),{'scope':str(d['a'])+':'+str(d['pro'])})
            assert await process(d,notice)=='blocked'
    assert not [p for p in d['packets'] if p[0]=='send']
    await due(d,notice);assert await process(d,notice)=='sent'
