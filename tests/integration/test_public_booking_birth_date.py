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
          SELECT c.id,c.birth_date,c.birth_month,c.birth_day,c.version FROM customers c
          JOIN appointments a ON a.tenant_id=c.tenant_id AND a.customer_id=c.id
          WHERE a.tenant_id=:tenant AND a.protocol=:protocol
        """),{'tenant':d['a'],'protocol':created.json()['protocol']})).mappings().one()
        assert row['birth_date'].isoformat()=='1994-08-27'
        assert (row['birth_month'],row['birth_day'])==(8,27)
        assert row['version']==1
    month=await d['client'].get(d['base']+'/customers',params={'birthday_month':8})
    assert month.status_code==200,month.text
    assert {item['id'] for item in month.json()['items']}=={str(row['id'])}
    replay=await d['client'].post(d['public']+'/appointments',
        json={**payload,'customer_birth_date':'1996-09-12'})
    assert replay.status_code==201,replay.text
    assert replay.json()['protocol']==created.json()['protocol']
    async with d['migrator'].connect() as db:
        saved=(await db.execute(text("""
          SELECT birth_date,version FROM customers WHERE tenant_id=:tenant AND id=:id
        """),{'tenant':d['a'],'id':row['id']})).one()
        assert saved.birth_date.isoformat()=='1994-08-27'
        assert saved.version==1


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


@pytest.mark.asyncio
@pytest.mark.parametrize('month,day',[(8,27),(4,12),(None,None)])
async def test_public_booking_cannot_fill_existing_partial_or_missing_birthdate(public_db,month,day):
    d=public_db
    await publish(d)
    phone='+5511987654321'
    async with d['migrator'].begin() as db:
        await db.execute(text("""
          INSERT INTO customers(tenant_id,whatsapp_e164,name,birth_month,birth_day)
          VALUES (:tenant,:phone,'Cliente com aniversário parcial',:month,:day),
                 (:other,:phone,'Cliente de outra empresa',8,27)
        """),{'tenant':d['a'],'other':d['b'],'phone':phone,'month':month,'day':day})
    slot=await availability(d)
    created=await d['client'].post(d['public']+'/appointments',
        json={**book_body(slot,phone),'customer_birth_date':'1994-08-27'})
    assert created.status_code==201,created.text
    async with d['migrator'].connect() as db:
        rows=(await db.execute(text("""
          SELECT tenant_id,birth_date,birth_month,birth_day,version FROM customers
          WHERE tenant_id IN (:tenant,:other) AND whatsapp_e164=:phone
        """),{'tenant':d['a'],'other':d['b'],'phone':phone})).mappings().all()
    current=next(row for row in rows if row['tenant_id']==d['a'])
    other=next(row for row in rows if row['tenant_id']==d['b'])
    assert current['birth_date'] is None, 'Anonymous bookings must not modify existing PII'
    assert (current['birth_month'],current['birth_day'])==(month,day)
    assert current['version']==1
    assert other['birth_date'] is None and (other['birth_month'],other['birth_day'])==(8,27) and other['version']==1
