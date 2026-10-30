"""Synthetic, no-send qualification for professional transactional WhatsApp alerts.

These tests exercise real appointment events, outbox tables, tenant isolation,
the exact message body and the worker's send-once contract on PostgreSQL.
No QR socket or real WhatsApp number is contacted.
"""
import json
from uuid import uuid4

import pytest
from sqlalchemy import text

from app import appointment_notifications as notices
from app.appointment_lifecycle import LifecycleActor,customer_actor
from app.booking_service import cancel_appointment,reschedule_appointment
from tests.integration.test_booking import booking_db,phone,start_at
from tests.integration.test_appointment_notifications_pg import (
    comm_db,booking,jobs,PRO_PHONE
)

pytestmark=pytest.mark.integration


async def capture_professional(d,notice):
    sent=[]
    async def synthetic_sender(tenant,channel,recipient,body,operation):
        sent.append({'tenant':tenant,'channel':channel,'recipient':recipient,
                     'body':body,'operation':operation})
        return {'accepted':True,'state':'accepted','provider_id':'synthetic-receipt-id'}
    ctx={'appointment_sender':synthetic_sender}
    result=await notices.process_notification(ctx,str(d['a']),str(notice['id']))
    assert result=='accepted'
    assert await notices.process_notification(ctx,str(d['a']),str(notice['id']))=='accepted'
    assert len(sent)==1,'A duplicate job must not repeat the WhatsApp message'
    assert (sent[0]['tenant'],sent[0]['channel'],sent[0]['operation'])==(
        d['a'],d['channel'],str(notice['id']))
    return sent[0]


@pytest.mark.asyncio
async def test_new_booking_notifies_only_selected_professional_with_factual_message(comm_db):
    d=comm_db;appointment=await booking(d)
    existing=await jobs(d)
    matching=[x for x in existing if x['event']=='professional_new']
    assert len(matching)==1
    notice=matching[0]
    assert notice['recipient_id']==d['pro']
    assert notice['recipient_id']!=d['pro2']
    assert notice['recipient_kind']=='professional'
    assert len([x for x in existing if x['event']=='reminder'])==1
    sent=await capture_professional(d,notice)
    assert sent['recipient']==PRO_PHONE
    body=sent['body']
    assert 'NOVO AGENDAMENTO RECEBIDO!' in body
    assert 'Cliente sintético' in body
    assert '*Corte*' in body
    assert 'Barbeiro sintético' in body
    assert str(appointment['id']) in body
    assert '/panel?aba=agenda&agendamento=' in body
    assert '40 minutos' in body
    assert '*' in body and '_' in body
    assert 'http://localhost' not in body


@pytest.mark.asyncio
async def test_customer_cancellation_informs_professional_once_and_revokes_pending_reminder(comm_db):
    d=comm_db;appointment=await booking(d)
    async with d['factory']() as db:
        await cancel_appointment(db,d['a'],appointment['id'],
                                 context=customer_actor(appointment['customer_id'],'public_site'))
    events=await jobs(d)
    assert not [n for n in events if n['event']=='customer_cancelled']
    old=next(n for n in events if n['event']=='professional_new')
    reminder=next(n for n in events if n['event']=='reminder')
    assert old['status']==reminder['status']=='cancelled'
    cancelled=[n for n in events if n['event']=='professional_cancelled']
    assert len(cancelled)==1
    assert cancelled[0]['recipient_id']==d['pro']
    sent=await capture_professional(d,cancelled[0])
    assert sent['recipient']==PRO_PHONE
    assert 'AGENDAMENTO CANCELADO!' in sent['body']
    assert 'Cliente sintético' in sent['body']
    assert 'liberado na sua agenda' in sent['body']
    assert str(appointment['id']) in sent['body']


@pytest.mark.asyncio
async def test_customer_reschedule_notifies_assigned_professional_with_old_and_new_time(comm_db):
    d=comm_db;appointment=await booking(d)
    async with d['factory']() as db:
        await reschedule_appointment(db,d['a'],appointment['id'],start_at(d['day'],14),
                                     context=customer_actor(appointment['customer_id'],'public_site'))
    events=await jobs(d)
    moved=[n for n in events if n['event']=='professional_rescheduled']
    assert len(moved)==1 and moved[0]['recipient_id']==d['pro']
    assert not [n for n in events if n['event']=='customer_rescheduled']
    assert next(n for n in events if n['event']=='professional_new')['status']=='cancelled'
    reminders=[n for n in events if n['event']=='reminder']
    assert len(reminders)==2 and sum(x['status']=='pending' for x in reminders)==1
    sent=await capture_professional(d,moved[0])
    assert sent['recipient']==PRO_PHONE
    assert 'AGENDAMENTO REMARCADO!' in sent['body']
    assert '10:00' in sent['body'] and '14:00' in sent['body']
    assert '~' in sent['body']
    assert str(appointment['id']) in sent['body']


@pytest.mark.asyncio
async def test_business_cancel_notifies_customer_not_professional(comm_db):
    d=comm_db;appointment=await booking(d)
    async with d['factory']() as db:
        await cancel_appointment(db,d['a'],appointment['id'],
          context=LifecycleActor(role='owner',source='panel',account_id=d['account']))
    events=await jobs(d)
    assert not [n for n in events if n['event']=='professional_cancelled']
    alert=next(n for n in events if n['event']=='customer_cancelled')
    assert alert['recipient_kind']=='customer'
    sent=await capture_professional(d,alert)
    assert sent['recipient']==phone(920)
    assert 'cancelado pela nossa equipe' in sent['body']
    assert 'Profissional' in sent['body']


@pytest.mark.asyncio
async def test_declined_schedule_proposal_informs_assigned_professional(comm_db):
    d=comm_db;appointment=await booking(d)
    async with d['factory']() as db:
        from app.database import tenant_transaction
        async with tenant_transaction(db,d['a']):
            await notices.enqueue_event(db,d['a'],appointment['id'],'reschedule_declined',
               {'requested_by':'customer','previous':{'proposal_id':str(uuid4())}})
    events=await jobs(d)
    declined=[n for n in events if n['event']=='professional_proposal_declined']
    assert len(declined)==1 and declined[0]['recipient_id']==d['pro']
    sent=await capture_professional(d,declined[0])
    assert sent['recipient']==PRO_PHONE
    assert 'PROPOSTA DE REMARCAÇÃO RECUSADA!' in sent['body']
    assert 'Cliente sintético' in sent['body']


@pytest.mark.asyncio
async def test_professional_not_in_qr_allowlist_is_never_sent(comm_db):
    d=comm_db;await booking(d)
    notice=next(n for n in await jobs(d) if n['event']=='professional_new')
    async with d['migrator'].begin() as db:
        await db.execute(text('''UPDATE qr_channels
             SET allowed_senders=CAST(:phones AS jsonb)
             WHERE tenant_id=:t AND id=:channel'''),
          {'t':d['a'],'channel':d['channel'],
           'phones':json.dumps([phone(920).lstrip('+')])})
    async def forbidden(*args):
        pytest.fail('No WhatsApp send to a professional outside allowlist')
    reason=await notices.process_notification({'appointment_sender':forbidden},
                                               str(d['a']),str(notice['id']))
    assert reason=='transport_recipient_not_authorized'
    assert next(n for n in await jobs(d) if n['id']==notice['id'])['status']=='failed'


@pytest.mark.asyncio
async def test_wrong_company_cannot_read_or_send_other_professional_notification(comm_db):
    d=comm_db;await booking(d)
    notice=next(n for n in await jobs(d) if n['event']=='professional_new')
    async def forbidden(*args):pytest.fail('Never send across tenant without scoped authority')
    outcome=await notices.process_notification({'appointment_sender':forbidden},
                             str(d['b']),str(notice['id']))
    assert outcome=='already_claimed'
    assert next(n for n in await jobs(d) if n['id']==notice['id'])['status']=='pending'


@pytest.mark.asyncio
async def test_pilot_scope_blocks_new_other_companies_and_unlisted_staff(comm_db,monkeypatch):
    from app import appointment_access as access
    d=comm_db
    monkeypatch.setenv('APPOINTMENT_COMMUNICATIONS_PILOT_ONLY','1')
    monkeypatch.setenv('APPOINTMENT_COMMUNICATIONS_PILOT_TENANT_ID',str(d['a']))
    monkeypatch.setenv('APPOINTMENT_COMMUNICATIONS_PILOT_PHONE',phone(920).lstrip('+'))
    monkeypatch.delenv('APPOINTMENT_COMMUNICATIONS_PILOT_CUSTOMER_PHONES',raising=False)
    assert access.tenant_enabled(d['a'])
    assert not access.tenant_enabled(d['b'])
    assert not access.recipient_allowed(PRO_PHONE)
    # The confirmed booking engine works independently of these pilot gates.
    # Its outbox for another company cannot be activated just by a site link.


@pytest.mark.asyncio
async def test_whatsapp_receipt_progresses_accepted_to_delivered_to_read_without_resend(comm_db):
    d=comm_db;await booking(d)
    notice=next(n for n in await jobs(d) if n['event']=='professional_new')
    await capture_professional(d,notice)
    assert next(n for n in await jobs(d) if n['id']==notice['id'])['status']=='accepted'
    seen=[]
    async def inspect(tenant,channel,operation):
        seen.append((tenant,channel,operation))
        return {'operation_id':operation,'accepted':True,
                'delivery':'delivered' if len(seen)==1 else 'read'}
    ctx={'appointment_inspector':inspect}
    assert await notices.reconcile_notification(ctx,str(d['a']),str(notice['id']))=='delivered'
    assert next(n for n in await jobs(d) if n['id']==notice['id'])['status']=='delivered'
    assert await notices.reconcile_notification(ctx,str(d['a']),str(notice['id']))=='read'
    assert next(n for n in await jobs(d) if n['id']==notice['id'])['status']=='read'
    assert seen==[(d['a'],d['channel'],str(notice['id']))]*2
    async def no_resend(*args):
        pytest.fail('A receipt cannot trigger a second WhatsApp send')
    assert await notices.process_notification({'appointment_sender':no_resend},
         str(d['a']),str(notice['id']))=='read'
