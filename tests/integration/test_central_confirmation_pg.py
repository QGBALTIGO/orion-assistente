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
