"""PostgreSQL proof: global protocol lookup returns only unique matching phone.

This verifies app_support cannot read appointment tables directly and does not
receive any customer information from the restricted security-definer function.
"""
import os
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from sqlalchemy.exc import DBAPIError

from tests.integration.test_booking import booking_db,phone,start_at,async_url
from app.booking_service import book_appointment,confirm_hold
from app.database import tenant_transaction

pytestmark=pytest.mark.integration


@pytest.mark.asyncio
async def test_global_confirm_resolver_enforces_phone_uniqueness_and_role(booking_db):
    db=booking_db
    customer_phone=phone(920)
    async with db['factory']() as s:
        held=await book_appointment(s,db['a'],db['pro'],[db['cut']],customer_phone,
             'Cliente sintético',start_at(db['day'],10),'central-'+str(uuid4()))
        item=await confirm_hold(s,db['a'],held['id'])
    support=create_async_engine(async_url(os.environ['SUPPORT_DATABASE_URL']))
    try:
        async with support.connect() as s:
            async def lookup(protocol,contact):
                return (await s.execute(text(
                    'SELECT tenant_id,appointment_id FROM public.orion_resolve_central_appointment(:p,:c)'
                ),{'p':protocol,'c':contact})).mappings().all()
            assert [dict(x) for x in await lookup(item['protocol'],customer_phone)]==[
                {'tenant_id':db['a'],'appointment_id':item['id']}]
            assert not await lookup(item['protocol'],phone(921))
            assert not await lookup('AG-ZZZZZZ',customer_phone)
            assert not await lookup('wrong',customer_phone)
        with pytest.raises(DBAPIError):
            async with db['factory']() as s:
                async with tenant_transaction(s,db['a']):
                    await s.execute(text(
                        'SELECT * FROM public.orion_resolve_central_appointment(:p,:c)'
                    ),{'p':item['protocol'],'c':customer_phone})
        # Deliberately create the same protocol for the same phone in another
        # tenant. The resolver must return no row instead of picking a company.
        other_customer,other_staff,other_appointment=uuid4(),uuid4(),uuid4()
        async with db['migrator'].begin() as s:
            await s.execute(text("""
              INSERT INTO professionals(id,tenant_id,name)
              VALUES (:id,:t,'Profissional teste')
            """),{'id':other_staff,'t':db['b']})
            await s.execute(text("""
              INSERT INTO customers(id,tenant_id,whatsapp_e164,name)
              VALUES (:id,:t,:p,'Cliente teste')
            """),{'id':other_customer,'t':db['b'],'p':customer_phone})
            await s.execute(text("""
              INSERT INTO appointments(id,tenant_id,customer_id,professional_id,
                   protocol,idempotency_key,request_fingerprint,start_at,end_at,
                   occupied_from,occupied_until,status,total_cents)
              VALUES(:id,:t,:customer,:professional,:protocol,:key,:fingerprint,
                   :starts,:ends,:starts,:ends,'confirmed',4000)
            """),{'id':other_appointment,'t':db['b'],'customer':other_customer,
                   'professional':other_staff,'protocol':item['protocol'],
                   'key':'synthetic-collision-'+str(uuid4()),'fingerprint':'a'*64,
                   'starts':start_at(db['day'],13),'ends':start_at(db['day'],14)})
        try:
            async with support.connect() as s:
                rows=(await s.execute(text("""
                    SELECT * FROM public.orion_resolve_central_appointment(:p,:c)
                """),{'p':item['protocol'],'c':customer_phone})).all()
                assert rows==[]
        finally:
            async with db['migrator'].begin() as s:
                await s.execute(text('DELETE FROM appointments WHERE tenant_id=:t AND id=:id'),
                                {'t':db['b'],'id':other_appointment})
                await s.execute(text('DELETE FROM customers WHERE tenant_id=:t AND id=:id'),
                                {'t':db['b'],'id':other_customer})
                await s.execute(text('DELETE FROM professionals WHERE tenant_id=:t AND id=:id'),
                                {'t':db['b'],'id':other_staff})
    finally:
        await support.dispose()


@pytest.mark.asyncio
async def test_public_customer_gets_real_appointment_confirmation_without_ai_pilot(monkeypatch,booking_db):
    from cryptography.fernet import Fernet
    from app import appointment_confirmation as confirmation,appointment_access as access
    d=booking_db
    contact=phone(920)
    async with d['factory']() as s:
        held=await book_appointment(s,d['a'],d['pro'],[d['cut']],contact,
            'Cliente real de teste',start_at(d['day'],10),'independent-'+str(uuid4()))
        appointment=await confirm_hold(s,d['a'],held['id'])
    monkeypatch.setenv('APPOINTMENT_COMMUNICATIONS_ENABLED','1')
    monkeypatch.setenv('APPOINTMENT_COMMUNICATIONS_PILOT_ONLY','1')
    monkeypatch.setenv('APPOINTMENT_COMMUNICATIONS_PILOT_TENANT_ID',str(d['b']))
    monkeypatch.setenv('APPOINTMENT_COMMUNICATIONS_PILOT_PHONE','556781234567')
    monkeypatch.setenv('ORION_CENTRAL_APPOINTMENT_WHATSAPP_ENABLED','1')
    monkeypatch.setenv('ORION_CENTRAL_CONFIRMATION_REPLY_ENABLED','1')
    monkeypatch.setenv('INTEGRATION_FERNET_KEY',Fernet.generate_key().decode())
    monkeypatch.setattr(confirmation,'get_session_factory',lambda:d['factory'])
    body='*Olá, Orion!* 👋\\n\\nProtocolo: *'+appointment['protocol']+'*'
    assert not access.tenant_enabled(d['a'])
    assert not access.recipient_allowed(contact)
    assert await confirmation.confirmation_reply(d['a'],contact,body,'qr-legacy','pilot') is None
    answer=await confirmation.confirmation_reply(d['a'],contact,body,
                                                   'central-admin-qr','message-123',central=True)
    assert 'confirmado' in answer and 'Cliente' in answer
    assert 'Corte' in answer and '/a#' in answer
    # Duplicate callbacks keep the same capability rather than issuing another
    # link and querying another tenant.
    assert await confirmation.confirmation_reply(d['a'],contact,body,
                          'central-admin-qr','message-123',central=True)==answer
    async with d['factory']() as s:
        async with tenant_transaction(s,d['a']):
            count=(await s.execute(text('SELECT count(*) FROM appointment_access WHERE tenant_id=:t'),
                    {'t':d['a']})).scalar_one()
            assert count==1
