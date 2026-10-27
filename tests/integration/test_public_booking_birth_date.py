"""Real PostgreSQL test for public booking birth date persistence.

DOB belongs to the tenant's existing customer profile and is never returned to a
public visitor or used to override a previously captured birthday.
"""
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
from sqlalchemy import text

from test_public_catalog import public_db, publish, availability, book_body

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_public_booking_saves_birth_date_and_replay_does_not_change_it(public_db):
    d=public_db
    await publish(d)
    slot=await availability(d)
    payload={**book_body(slot), 'customer_birth_date':'1994-08-27'}

    invalid = await d['client'].post(d['public']+'/appointments',
        json={**payload,'customer_birth_date':'1880-01-01'})
    assert invalid.status_code==422
    future=(datetime.now(timezone.utc).date()+timedelta(days=2)).isoformat()
    invalid=await d['client'].post(d['public']+'/appointments',
        json={**payload,'customer_birth_date':future})
    assert invalid.status_code==422
    assert (await d['client'].post(d['public']+'/appointments',
            json={**payload,'confirmed':False})).status_code==422

    created=await d['client'].post(d['public']+'/appointments',json=payload)
    assert created.status_code==201,created.text
    assert 'customer_birth_date' not in created.json(), 'DOB is never public response data'
    async with d['migrator'].connect() as db:
        row=(await db.execute(text("""
          SELECT c.id,c.birth_date,c.version FROM customers c
          JOIN appointments a ON a.tenant_id=c.tenant_id AND a.customer_id=c.id
          WHERE a.tenant_id=:tenant AND a.protocol=:protocol
        """),{'tenant':d['a'],'protocol':created.json()['protocol']})).mappings().one()
        assert row['birth_date'].isoformat()=='1994-08-27'
        assert row['version']==2
    replay=await d['client'].post(d['public']+'/appointments',
        json={**payload,'customer_birth_date':'1996-09-12'})
    assert replay.status_code==201,replay.text
    assert replay.json()['protocol']==created.json()['protocol']
    async with d['migrator'].connect() as db:
        saved=(await db.execute(text("""
          SELECT birth_date,version FROM customers WHERE tenant_id=:tenant AND id=:id
        """),{'tenant':d['a'],'id':row['id']})).one()
        assert saved.birth_date.isoformat()=='1994-08-27'
        assert saved.version==2


@pytest.mark.asyncio
async def test_public_booking_does_not_override_existing_customers_birth_date(public_db):
    d=public_db
    await publish(d)
    phone='+5511987654321'
    async with d['migrator'].begin() as db:
        await db.execute(text("""
          INSERT INTO customers(tenant_id,whatsapp_e164,name,birth_date)
          VALUES (:tenant,:phone,'Cliente cadastrado','1988-06-25')
        """),{'tenant':d['a'],'phone':phone})
    slot=await availability(d)
    payload={**book_body(slot,phone),'customer_birth_date':'1993-04-12'}
    created=await d['client'].post(d['public']+'/appointments',json=payload)
    assert created.status_code==201,created.text
    async with d['migrator'].connect() as db:
        row=(await db.execute(text("""
          SELECT c.birth_date,c.name,c.version FROM customers c
          WHERE c.tenant_id=:tenant AND c.whatsapp_e164=:phone
        """),{'tenant':d['a'],'phone':phone})).one()
        assert row.birth_date.isoformat()=='1988-06-25'
        assert row.name=='Cliente cadastrado'
        assert row.version==1
