"""Check the real central broker against scoped customer permission and reviews.

Synthetic PostgreSQL only. The sender is a spy, never a WhatsApp transport.
"""
import hashlib
import os

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app import appointment_notifications as notifications
from app.appointment_consent import revoke_scoped_consents
from app.database import tenant_transaction
from tests.integration.test_booking import booking_db, phone
from tests.integration.test_appointment_notifications_pg import comm_db
from tests.integration.test_appointment_scoped_consent_pg import scoped_db, new_booking, due_reminder
from tests.integration.test_appointment_review_completion_pg import move_to_past, complete

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def central_db(scoped_db):
    try:
        yield scoped_db
    finally:
        async with scoped_db['migrator'].begin() as db:
            for table in ('financial_entries','management_requests'):
                await db.execute(text('DELETE FROM '+table+' WHERE tenant_id=:t'),{'t':scoped_db['a']})


def use_central(d, monkeypatch):
    monkeypatch.setenv('ORION_CENTRAL_APPOINTMENT_WHATSAPP_ENABLED','1')
    monkeypatch.setenv('ORION_CENTRAL_NOTIFICATIONS_ENABLED','1')
    monkeypatch.setattr(notifications,'central_channel',lambda:(d['a'],d['channel']))


async def authorize(support, d, operation, body, *, recipient=None, admin=None, digest=None):
    recipient=recipient or phone(920)
    async with support.connect() as db:
        return (await db.execute(text('''SELECT public.orion_authorize_central_appointment_notification(
            :operation,:admin,:channel,:phone,:recipient_digest,:body_digest)'''),
            {'operation':operation,'admin':admin or d['a'],'channel':d['channel'],
             'phone':recipient.lstrip('+'),'recipient_digest':hashlib.sha256(recipient.encode()).hexdigest(),
             'body_digest':digest or hashlib.sha256(body.encode()).hexdigest()})).scalar_one()


@pytest.mark.asyncio
async def test_central_scoped_reminder_checks_scope_and_last_moment_withdrawal(central_db,monkeypatch):
    d=central_db;use_central(d,monkeypatch)
    appointment,_,_=await new_booking(d,opted_in=True,role='owner')
    notice=await due_reminder(d,appointment)
    support=create_async_engine(os.environ['SUPPORT_DATABASE_URL'])
    sent=[]
    async def sender(tenant,channel,number,body,operation):
        assert (tenant,channel,number)==(d['a'],d['channel'],phone(920))
        assert await authorize(support,d,notice['id'],body) is True
        assert await authorize(support,d,notice['id'],body,recipient=phone(921)) is False
        assert await authorize(support,d,notice['id'],body,admin=d['b']) is False
        assert await authorize(support,d,notice['id'],body,digest='0'*64) is False
        # Same transaction boundary as a preference received after preparation.
        async with d['factory']() as db:
            async with tenant_transaction(db,d['a']):
                await revoke_scoped_consents(db,d['a'],phone(920),'all')
        assert await authorize(support,d,notice['id'],body) is False
        sent.append(operation)
        return {'accepted':False,'state':'rejected','operation_id':operation}
    try:
        await notifications.process_notification({'appointment_sender':sender},str(d['a']),str(notice['id']))
        assert len(sent)==1
    finally:
        await support.dispose()


@pytest.mark.asyncio
async def test_central_review_allows_scoped_feedback_but_requires_marketing_for_return(central_db,monkeypatch):
    d=central_db;use_central(d,monkeypatch)
    async with d['migrator'].begin() as db:
        await db.execute(text('UPDATE appointment_communication_settings SET review=true WHERE tenant_id=:t'),{'t':d['a']})
    appointment,_,_=await new_booking(d,opted_in=True,role='owner')
    await move_to_past(d,appointment['id'])
    await complete(d,appointment,operator='system',automated=True)
    async with d['migrator'].begin() as db:
        notice=(await db.execute(text('''UPDATE appointment_notifications
            SET due_at=clock_timestamp()-interval '1 second'
            WHERE tenant_id=:t AND appointment_id=:a AND event='review' RETURNING *'''),
            {'t':d['a'],'a':appointment['id']})).mappings().one()
    support=create_async_engine(os.environ['SUPPORT_DATABASE_URL'])
    checked=[]
    async def sender(_tenant,_channel,_number,body,operation):
        assert 'Deixe sua avaliação' in body and 'Agende novamente' not in body
        assert await authorize(support,d,notice['id'],body) is True
        async with d['migrator'].begin() as db:
            await db.execute(text('UPDATE appointment_notifications SET requires_marketing=true WHERE tenant_id=:t AND id=:id'),
                             {'t':d['a'],'id':notice['id']})
        assert await authorize(support,d,notice['id'],body) is False
        async with d['migrator'].begin() as db:
            await db.execute(text('''INSERT INTO contact_consents(tenant_id,contact_e164,operational_opt_in,marketing_opt_in)
                VALUES(:t,:phone,false,true)'''),{'t':d['a'],'phone':phone(920)})
        assert await authorize(support,d,notice['id'],body) is True
        async with d['migrator'].begin() as db:
            await db.execute(text("""UPDATE appointment_history SET details=jsonb_set(details,'{actor,role}','\"customer\"')
                WHERE tenant_id=:t AND appointment_id=:a AND event='completed'"""),
                {'t':d['a'],'a':appointment['id']})
        assert await authorize(support,d,notice['id'],body) is False
        checked.append(operation)
        return {'accepted':False,'state':'rejected','operation_id':operation}
    try:
        await notifications.process_notification({'appointment_sender':sender},str(d['a']),str(notice['id']))
        assert len(checked)==1
    finally:
        await support.dispose()


@pytest.mark.asyncio
async def test_scoped_permissions_and_schema_barrier_preserve_runtime_tenant_isolation(central_db):
    from scripts.backup_and_migrate_appointment_consent import schema_ready
    from scripts.wait_for_appointment_consent_schema import probe_schema,Settings
    d=central_db
    appointment,_,_=await new_booking(d,opted_in=True,role='owner')
    async with d['factory']() as db:
        async with tenant_transaction(db,d['b']):
            assert not (await db.execute(text('SELECT appointment_id FROM appointment_message_consents'))).all()
        async with tenant_transaction(db,d['a']):
            assert (await db.execute(text('SELECT appointment_id FROM appointment_message_consents'))).scalar_one()==appointment['id']
    assert schema_ready(os.environ['MIGRATION_DATABASE_URL'])
    assert await probe_schema(Settings.from_environment(),10)
