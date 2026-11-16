"""Shared bookings use the real PostgreSQL motor, never production data."""
import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from fastapi import HTTPException
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.booking_service import book_appointment, list_slots
from app.catalog_api import Actor
from app.database import tenant_transaction
from test_appointment_operations_postgres import (
    operations_db, create_manual, create_normal, extras_payload, manual_payload,
)
from test_booking import booking_db, phone, start_at

pytestmark = pytest.mark.integration


async def available(d, service=None):
    async with d['factory']() as db:
        result = await list_slots(db, d['a'], d['pro'], [service or d['beard']], d['day'])
        return {datetime.fromisoformat(value) for value in result['slots']}


async def cancel(d, row):
    response = await d['client'].post(d['base'] + f"/appointments/{row['id']}/cancel", json={
        'expected_updated_at': row['updated_at'], 'idempotency_key': str(uuid4()),
        'requested_by': 'business', 'reason': 'Cancelamento sintético individual'})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
async def test_two_customers_different_services_block_union_and_cancel_independently(operations_db):
    d = operations_db
    first = await create_normal(d)
    second = await create_manual(d, start_at=first['start_at'], allow_overlap=True,
        service_ids=[str(d['beard'])], customer_phone=phone(951), customer_name='Cliente Barba')
    assert first['id'] != second['id'] and first['customer_id'] != second['customer_id']
    assert first['total_cents'] == 3500 and second['total_cents'] == 2000
    assert second['allow_overlap'] is True and first['allow_overlap'] is False
    assert second['items'][0]['name_snapshot'] == 'Barba'
    assert first['items'][0]['name_snapshot'] == 'Corte'
    assert start_at(d['day'], 10) not in await available(d)
    # Cancel the long normal visit: the exception still occupies its own 20 min.
    await cancel(d, first)
    slots = await available(d)
    assert start_at(d['day'], 10) not in slots
    assert start_at(d['day'], 10, 30) in slots
    await cancel(d, second)
    assert start_at(d['day'], 10) in await available(d)


@pytest.mark.asyncio
async def test_public_engine_and_normal_panel_cannot_use_exception_capacity(operations_db):
    d = operations_db
    row = await create_manual(d, start_at=start_at(d['day'], 10).isoformat(), allow_overlap=True)
    normal = manual_payload(d, start_at=row['start_at'])
    for key in ('mode', 'walk_in', 'appointment_messages_opt_in'):
        normal.pop(key)
    response = await d['client'].post(d['base']+'/appointments/hold', json=normal)
    assert response.status_code == 409, response.text
    response = await d['client'].post(d['base']+'/appointments/hold', json={**normal, 'allow_overlap': True})
    assert response.status_code == 422, response.text
    async with d['factory']() as db:
        with pytest.raises(HTTPException) as error:
            await book_appointment(db, d['a'], d['pro'], [d['beard']], phone(952),
                'Visitante do site', start_at(d['day'], 10), str(uuid4()), source='public_site',
                nonblocking_professional_lock=True)
        assert error.value.status_code == 409
    denied = await d['client'].post(d['base']+'/appointments/manual',
        json=manual_payload(d, start_at=row['start_at'], customer_phone=phone(953)))
    assert denied.status_code == 409, denied.text


@pytest.mark.asyncio
async def test_concurrent_exceptions_allow_two_only_and_replay_same_identity(operations_db):
    d = operations_db
    requests = [manual_payload(d, allow_overlap=True, customer_phone=phone(960+i)) for i in range(8)]
    results = await asyncio.gather(*(d['client'].post(d['base']+'/appointments/manual', json=data) for data in requests))
    assert sorted(r.status_code for r in results) == [201,201,409,409,409,409,409,409], [r.text for r in results]
    index = next(i for i,r in enumerate(results) if r.status_code == 201)
    replays = await asyncio.gather(*(d['client'].post(d['base']+'/appointments/manual', json=requests[index]) for _ in range(3)))
    assert all(r.status_code == 201 and r.json()['id'] == results[index].json()['id'] for r in replays)
    changed = await d['client'].post(d['base']+'/appointments/manual', json={**requests[index], 'allow_overlap': False})
    assert changed.status_code == 409


@pytest.mark.asyncio
async def test_long_exception_can_cross_adjacent_visits_without_three_at_once(operations_db):
    d = operations_db
    # Barba has no prep/cleanup: 10:00-10:20 and 10:30-10:50 are disjoint.
    await create_normal(d, services=[d['beard']])
    await create_normal(d, hour=10, minute=30, services=[d['beard']])
    row = await create_manual(d, start_at=start_at(d['day'], 10).isoformat(),
        allow_overlap=True, service_ids=[str(d['botox'])], customer_phone=phone(970))
    assert row['allow_overlap'] is True
    denied = await d['client'].post(d['base']+'/appointments/manual', json=manual_payload(d,
        start_at=start_at(d['day'], 10, 10).isoformat(), allow_overlap=True, customer_phone=phone(971)))
    assert denied.status_code == 409 and 'dois clientes' in denied.text


@pytest.mark.asyncio
async def test_shared_exception_never_intercepts_live_public_hold(operations_db):
    d = operations_db
    async with d['factory']() as db:
        hold = await book_appointment(db, d['a'], d['pro'], [d['beard']], phone(980),
            'Reserva do site', start_at(d['day'], 10), str(uuid4()), source='public_site')
    response = await d['client'].post(d['base']+'/appointments/manual', json=manual_payload(d,
        start_at=hold['start_at'], allow_overlap=True))
    assert response.status_code == 409 and 'confirmação' in response.text


@pytest.mark.asyncio
async def test_same_customer_is_not_silently_duplicated(operations_db):
    d = operations_db
    row = await create_manual(d)
    response = await d['client'].post(d['base']+'/appointments/manual', json=manual_payload(d, allow_overlap=True))
    assert response.status_code == 409 and 'cliente já tem' in response.text
    response = await d['client'].post(d['base']+'/appointments/manual', json=manual_payload(d,
        allow_overlap=True, customer_phone=phone(981), professional_id=str(d['pro2'])))
    assert response.status_code == 201, response.text


@pytest.mark.asyncio
async def test_extras_extend_shared_visit_until_next_capacity_limit(operations_db):
    d = operations_db
    first = await create_normal(d, services=[d['beard']])
    shared = await create_manual(d, service_ids=[str(d['beard'])], allow_overlap=True,
        start_at=first['start_at'], customer_phone=phone(982))
    response = await d['client'].post(d['base']+f"/appointments/{shared['id']}/extras", json=extras_payload(d, shared,
        service_ids=[str(d['cut'])]))
    assert response.status_code == 200, response.text
    changed = response.json()
    assert datetime.fromisoformat(changed['end_at']) > datetime.fromisoformat(shared['end_at'])
    assert start_at(d['day'], 10, 45) not in await available(d)


@pytest.mark.asyncio
@pytest.mark.parametrize('table', ['schedule_blocks','time_off'])
async def test_explicit_sharing_still_respects_blocks_and_time_off(operations_db, table):
    d = operations_db
    async with d['migrator'].begin() as db:
        await db.execute(text(f'INSERT INTO {table} (tenant_id,professional_id,starts_at,ends_at) VALUES(:t,:p,:a,:b)'),
            {'t':d['a'],'p':d['pro'],'a':start_at(d['day'],19),'b':start_at(d['day'],21)})
    response = await d['client'].post(d['base']+'/appointments/manual', json=manual_payload(d, allow_overlap=True))
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_professional_scope_viewer_and_other_tenant_are_preserved(operations_db):
    d = operations_db
    d['selected']['actor'] = Actor('member', tenant_id=d['a'], account_id=d['member'], membership_role='professional')
    body = manual_payload(d, allow_overlap=True)
    body.pop('professional_id')
    response = await d['client'].post('/api/v1/professional/appointments/manual',json=body)
    assert response.status_code == 201 and response.json()['allow_overlap'] is True, response.text
    response = await d['client'].post('/api/v1/professional/appointments/manual',json={**body,'professional_id':str(d['pro2'])})
    assert response.status_code == 422
    d['selected']['actor'] = Actor('member', tenant_id=d['a'], account_id=d['owner'], membership_role='viewer')
    denied = await d['client'].post(d['base']+'/appointments/manual',json=manual_payload(d,allow_overlap=True,customer_phone=phone(983)))
    assert denied.status_code == 403
    d['selected']['actor'] = Actor('member', tenant_id=d['b'], account_id=d['owner'], membership_role='owner')
    denied = await d['client'].post(d['base']+'/appointments/manual',json=manual_payload(d,allow_overlap=True,customer_phone=phone(984)))
    assert denied.status_code == 403


@pytest.mark.asyncio
async def test_database_guard_also_rejects_bypassed_capacity_and_normal_booking(operations_db):
    d = operations_db
    row = await create_manual(d, allow_overlap=True)
    await create_manual(d, allow_overlap=True, customer_phone=phone(985))
    for shared in (True, False):
        async with d['factory']() as db:
            with pytest.raises(IntegrityError) as error:
                async with tenant_transaction(db,d['a']):
                    customer = (await db.execute(text("INSERT INTO customers (tenant_id,name) VALUES(:t,'Terceiro sintético') RETURNING id"), {'t':d['a']})).scalar_one()
                    await db.execute(text('''INSERT INTO appointments
                      (tenant_id,professional_id,customer_id,protocol,idempotency_key,request_fingerprint,start_at,end_at,
                       occupied_from,occupied_until,status,total_cents,booking_mode,created_by_account_id,allow_overlap)
                      SELECT tenant_id,professional_id,:customer,:protocol,:key,request_fingerprint,start_at,end_at,
                        occupied_from,occupied_until,'confirmed',total_cents,'exceptional',created_by_account_id,:shared
                      FROM appointments WHERE tenant_id=:t AND id=:id'''),
                      {'t':d['a'],'id':UUID(row['id']),'customer':customer,'protocol':str(uuid4())[:9],'key':str(uuid4()),'shared':shared})
            assert error.value.orig.sqlstate == '23P01'


@pytest.mark.asyncio
async def test_shared_customers_have_independent_receipts_and_commissions(operations_db):
    d=operations_db
    first=await create_manual(d)
    second=await create_manual(d,allow_overlap=True,customer_phone=phone(986),service_ids=[str(d['beard'])])
    # Move synthetic visits into the past; the production clock is untouched.
    past=datetime.now(timezone.utc)-timedelta(hours=2)
    async with d['migrator'].begin() as db:
        for row,minutes in ((first,40),(second,20)):
            await db.execute(text('''UPDATE appointments SET start_at=:lo,end_at=:hi,
                occupied_from=:lo,occupied_until=:hi WHERE tenant_id=:t AND id=:id'''),
                {'t':d['a'],'id':UUID(row['id']),'lo':past,'hi':past+timedelta(minutes=minutes)})
    ledger=[]
    for row in (first,second):
        payload={'expected_updated_at':row['updated_at'],'idempotency_key':str(uuid4())}
        for _ in range(2):
            done=await d['client'].post(d['base']+f"/management/appointments/{row['id']}/complete",json=payload)
            assert done.status_code==200,done.text
        response=await d['client'].get(d['base']+f"/management/finance/receipts/appointment/{row['id']}")
        assert response.status_code==200,response.text
        entries=response.json()['items']
        assert len(entries)==1 and entries[0]['status']=='pending'
        ledger.append(entries[0])
    assert ledger[0]['id']!=ledger[1]['id']
    response=await d['client'].post(d['base']+f"/management/finance/{ledger[0]['id']}/receipt",json={
        'action':'confirm','expected_version':ledger[0]['version'],'idempotency_key':str(uuid4())})
    assert response.status_code==200,response.text
    async with d['migrator'].connect() as db:
        entries=(await db.execute(text('''SELECT appointment_id,amount_cents,status FROM financial_entries
            WHERE tenant_id=:t AND service_receivable ORDER BY amount_cents DESC'''),{'t':d['a']})).mappings().all()
        assert [(r['amount_cents'],r['status']) for r in entries]==[(3500,'confirmed'),(2000,'pending')]
        commissions=(await db.execute(text('SELECT sum(commission_cents) FROM appointments WHERE tenant_id=:t'),
            {'t':d['a']})).scalar_one()
        assert commissions==1375
