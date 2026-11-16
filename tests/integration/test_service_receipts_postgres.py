"""Real Postgres: receipt timing, isolation, cash dates and exactly-once effects."""
import asyncio
from datetime import datetime,timedelta,timezone
from uuid import UUID,uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database import tenant_transaction
from app.receipt_worker import confirm_service_receipt,scan_service_receipts
from app.catalog_api import Actor,authenticate
from test_management import management_db,customer,appointment,entry

pytestmark=pytest.mark.integration


async def policy(d,mode='after_24h'):
    response=await d['client'].get(d['base']+'/finance/receipts/settings')
    response=await d['client'].put(d['base']+'/finance/receipts/settings',json={
        'mode':mode,'expected_version':response.json()['version']})
    assert response.status_code==200,response.text
    return response.json()


async def completed(d,*,age=2,automatic=False):
    if automatic:
        await policy(d)
        async with d['migrator'].begin() as db:
            await db.execute(text("UPDATE tenant_receipt_settings SET activated_at=now()-interval '4 days' WHERE tenant_id=:t"),{'t':d['a']})
    c=await customer(d)
    a=await appointment(d,c['id'],start_at=d['now']-timedelta(hours=age))
    response=await d['client'].post(d['base']+f'/appointments/{a}/complete',json={'idempotency_key':str(uuid4())})
    assert response.status_code==200,response.text
    rows=await d['client'].get(d['base']+f'/finance/receipts/appointment/{a}')
    assert rows.status_code==200,rows.text
    assert len(rows.json()['items'])==1,rows.text
    return a,rows.json()['items'][0]


async def action(d,row,name='confirm',**fields):
    return await d['client'].post(d['base']+f"/finance/{row['id']}/receipt",json={
        'action':name,'expected_version':row['version'],'idempotency_key':str(uuid4()),**fields})


async def due_now(d,row):
    async with d['migrator'].begin() as db:
        await db.execute(text("UPDATE financial_entries SET auto_receipt_due_at=clock_timestamp() WHERE tenant_id=:t AND id=:id"),
            {'t':d['a'],'id':UUID(row['id'])})


@pytest.mark.asyncio
async def test_manual_is_default_standard_completion_creates_one_receivable(management_db):
    d=management_db;a,row=await completed(d)
    assert row['status']=='pending' and row['service_receivable']
    assert row['auto_receipt_due_at'] is None and not row['auto_receipt_active']
    assert await confirm_service_receipt({},str(d['a']),row['id'])=='changed'
    summary=(await d['client'].get(d['base']+'/finance/summary',params=d['period'])).json()['summary']
    assert summary['pending_income_cents']==10000 and summary['balance_cents']==0
    assert (await d['client'].get(d['base']+'/finance/receipts/settings')).json()['mode']=='manual'


@pytest.mark.asyncio
async def test_24_hours_deadline_never_runs_early_and_duplicate_workers_settle_once(management_db):
    d=management_db;a,row=await completed(d,automatic=True)
    async with d['migrator'].connect() as db:
        end=(await db.execute(text('SELECT end_at FROM appointments WHERE tenant_id=:t AND id=:a'),
            {'t':d['a'],'a':UUID(a)})).scalar_one()
    assert datetime.fromisoformat(row['auto_receipt_due_at'])==end+timedelta(hours=24)
    assert row['auto_receipt_active']
    assert await confirm_service_receipt({},str(d['a']),row['id'])=='changed'
    await due_now(d,row)
    results=await asyncio.gather(*(confirm_service_receipt({},str(d['a']),row['id']) for _ in range(4)))
    assert 'confirmed' in results
    final=(await d['client'].get(d['base']+'/finance/receipts/'+row['id'])).json()
    assert final['status']=='paid' and final['receipt_source']=='automatic'
    assert final['payment_method']=='other' and final['receipt_actor_id'] is None
    assert len(final['receipt_history'])==1
    async with d['migrator'].connect() as db:
        assert (await db.execute(text("SELECT count(*) FROM financial_entries WHERE tenant_id=:t AND appointment_id=:a"),
            {'t':d['a'],'a':UUID(a)})).scalar_one()==1


@pytest.mark.asyncio
async def test_hold_resume_and_reopen_are_explicit_and_preserve_history(management_db):
    d=management_db;a,row=await completed(d,automatic=True)
    response=await action(d,row,'hold');assert response.status_code==200,response.text
    held=response.json();assert held['keep_pending'] and not held['auto_receipt_active']
    await due_now(d,row)
    assert await confirm_service_receipt({},str(d['a']),row['id'])=='changed'
    resumed=await action(d,held,'resume');assert resumed.status_code==200,resumed.text
    assert resumed.json()['auto_receipt_active']
    paid=await action(d,resumed.json(),payment_method='cash');assert paid.status_code==200,paid.text
    assert paid.json()['payment_method']=='cash' and paid.json()['receipt_source']=='manual'
    reopened=await action(d,paid.json(),'reopen');assert reopened.status_code==200,reopened.text
    result=reopened.json();assert result['status']=='pending' and result['keep_pending']
    assert result['received_at'] is None
    assert [e['action'] for e in result['receipt_history']]==['reopen','confirm','resume','hold']
    assert result['receipt_history'][0]['details']['previous']['status']=='paid'


@pytest.mark.asyncio
async def test_policy_is_prospective_and_disabling_reenabling_does_not_release_old_jobs(management_db):
    d=management_db;a,old=await completed(d)
    first=await policy(d)
    assert first['version']==1
    again=await policy(d);assert again['version']==1
    retained=(await d['client'].get(d['base']+'/finance/receipts/'+old['id'])).json()
    assert retained['auto_receipt_due_at'] is None
    a,row=await completed(d,automatic=True);await due_now(d,row)
    await policy(d,'manual')
    assert await confirm_service_receipt({},str(d['a']),row['id'])=='changed'
    await policy(d)
    assert await confirm_service_receipt({},str(d['a']),row['id'])=='changed'
    response=await d['client'].put(d['base']+'/finance/receipts/settings',json={'mode':'manual','expected_version':first['version']})
    assert response.status_code==409


@pytest.mark.asyncio
async def test_late_worker_recovers_prospective_booking_without_another_24_hour_wait(management_db):
    d=management_db;a,row=await completed(d,age=26,automatic=True)
    assert datetime.fromisoformat(row['auto_receipt_due_at'])<datetime.now(timezone.utc)
    assert await confirm_service_receipt({},str(d['a']),row['id'])=='confirmed'


@pytest.mark.asyncio
async def test_cash_receipt_date_is_separate_from_service_and_month_filters(management_db):
    d=management_db;a,row=await completed(d,age=24*40)
    before=(await d['client'].get(d['base']+'/performance',params=d['period'])).json()
    result=await action(d,row,payment_method='pix',received_at=d['now'].isoformat())
    assert result.status_code==200,result.text
    paid=result.json();assert paid['occurred_at']==row['occurred_at']
    assert datetime.fromisoformat(paid['received_at'])==d['now']
    day=d['now'].date().isoformat()
    cash=(await d['client'].get(d['base']+'/finance/summary',params={'start_date':day,'end_date':day})).json()['summary']
    assert cash['income_cents']==10000 and cash['balance_cents']==10000
    after=(await d['client'].get(d['base']+'/performance',params=d['period'])).json()
    assert before==after


@pytest.mark.asyncio
async def test_all_month_receivables_and_summary_find_old_pending_services(management_db):
    d=management_db;a,row=await completed(d,age=24*50)
    current=(await d['client'].get(d['base']+'/finance')).json()
    assert row['id'] not in {e['id'] for e in current['items']}
    all_months=(await d['client'].get(d['base']+'/finance',params={'receivables':'true'})).json()
    assert row['id'] in {e['id'] for e in all_months['items']}
    summary=(await d['client'].get(d['base']+'/finance/receipts/summary')).json()
    assert summary=={'count':1,'amount_cents':10000}


@pytest.mark.asyncio
async def test_manual_retry_and_concurrent_confirmation_have_one_effect(management_db):
    d=management_db;a,row=await completed(d)
    payload={'action':'confirm','expected_version':row['version'],'payment_method':'cash','idempotency_key':str(uuid4())}
    url=d['base']+f"/finance/{row['id']}/receipt"
    first=await d['client'].post(url,json=payload);assert first.status_code==200,first.text
    replay=await d['client'].post(url,json=payload);assert replay.status_code==200,replay.text
    assert len(replay.json()['receipt_history'])==1
    stale=await action(d,row);assert stale.status_code==409
    different=await d['client'].post(url,json={**payload,'payment_method':'pix'});assert different.status_code==409


@pytest.mark.asyncio
async def test_legacy_editor_uses_same_history_and_does_not_rewrite_service_time(management_db):
    d=management_db;a,row=await completed(d)
    result=await d['client'].patch(d['base']+'/finance/'+row['id'],json={'status':'paid','expected_version':row['version']})
    assert result.status_code==200,result.text
    final=(await d['client'].get(d['base']+'/finance/receipts/'+row['id'])).json()
    assert final['occurred_at']==row['occurred_at'] and final['received_at']
    assert final['receipt_source']=='manual' and len(final['receipt_history'])==1
    changed=await d['client'].patch(d['base']+'/finance/'+row['id'],json={'amount_cents':9000,'expected_version':final['version']})
    assert changed.status_code==422


@pytest.mark.asyncio
@pytest.mark.parametrize('invalid', ['expense','cancelled','no_show','amount_changed'])
async def test_worker_skips_ineligible_entries(management_db,invalid):
    d=management_db;a,row=await completed(d,automatic=True);await due_now(d,row)
    async with d['migrator'].begin() as db:
        if invalid in ('cancelled','no_show'):
            await db.execute(text('UPDATE appointments SET status=:s WHERE tenant_id=:t AND id=:a'),{'s':invalid,'t':d['a'],'a':UUID(a)})
        else:
            sql="kind='expense'" if invalid=='expense' else 'amount_cents=9000'
            await db.execute(text('UPDATE financial_entries SET '+sql+' WHERE tenant_id=:t AND id=:id'),{'t':d['a'],'id':UUID(row['id'])})
    assert await confirm_service_receipt({},str(d['a']),row['id'])=='changed'


@pytest.mark.asyncio
async def test_no_future_confirmation_date_and_no_cross_tenant_receipts(management_db):
    d=management_db;a,row=await completed(d)
    result=await action(d,row,received_at=(d['now']+timedelta(days=1)).isoformat())
    assert result.status_code==422
    foreign=await d['client'].get(d['other_base']+'/finance/receipts/'+row['id']);assert foreign.status_code==404
    async with async_sessionmaker(d['runtime'])() as db:
        async with tenant_transaction(db,d['b']):
            assert (await db.execute(text('SELECT count(*) FROM financial_entries WHERE id=:id'),{'id':UUID(row['id'])})).scalar_one()==0


@pytest.mark.asyncio
async def test_role_permissions_owner_settings_editor_receipt_viewer_read_only(management_db):
    d=management_db;a,row=await completed(d)
    async def use_role(role):
        async with d['migrator'].begin() as db:
            await db.execute(text('UPDATE tenant_memberships SET role=:r WHERE tenant_id=:t AND account_id=:a'),
                {'r':role,'t':d['a'],'a':d['account']})
        async def auth():return Actor('member',tenant_id=d['a'],account_id=d['account'],membership_role=role)
        d['app'].dependency_overrides[authenticate]=auth
    await use_role('editor')
    config=await d['client'].put(d['base']+'/finance/receipts/settings',json={'mode':'after_24h','expected_version':0})
    assert config.status_code==403
    result=await action(d,row);assert result.status_code==200,result.text
    await use_role('viewer')
    assert (await d['client'].get(d['base']+'/finance/receipts/'+row['id'])).status_code==200
    assert (await action(d,result.json(),'reopen')).status_code==403


@pytest.mark.asyncio
async def test_private_scanner_and_append_only_history_contract(management_db):
    d=management_db;a,row=await completed(d,automatic=True);await due_now(d,row)
    async with d['support'].connect() as db:
        candidates=(await db.execute(text('SELECT * FROM orion_due_service_receipts(100)'))).mappings().all()
        assert any(str(r['entry_id'])==row['id'] for r in candidates)
        assert set(candidates[0])=={'tenant_id','entry_id'}
    async with d['migrator'].connect() as db:
        permissions=(await db.execute(text("""SELECT
          has_table_privilege('app_runtime','service_receipt_history','INSERT'),
          has_table_privilege('app_runtime','service_receipt_history','UPDATE'),
          has_table_privilege('app_runtime','service_receipt_history','DELETE'),
          has_table_privilege('app_support','financial_entries','SELECT')"""))).one()
        assert permissions==(True,False,False,False)


@pytest.mark.asyncio
async def test_explicit_resume_after_reopening_can_run_a_new_automatic_confirmation(management_db):
    d=management_db;a,row=await completed(d,age=26,automatic=True)
    assert await confirm_service_receipt({},str(d['a']),row['id'])=='confirmed'
    paid=(await d['client'].get(d['base']+'/finance/receipts/'+row['id'])).json()
    reopened=await action(d,paid,'reopen');assert reopened.status_code==200,reopened.text
    resumed=await action(d,reopened.json(),'resume');assert resumed.status_code==200,resumed.text
    assert await confirm_service_receipt({},str(d['a']),row['id'])=='confirmed'
    final=(await d['client'].get(d['base']+'/finance/receipts/'+row['id'])).json()
    assert [e['action'] for e in final['receipt_history']]==['confirm','resume','reopen','confirm']


@pytest.mark.asyncio
async def test_another_linked_payment_blocks_automatic_double_receipt(management_db):
    d=management_db;a,row=await completed(d,automatic=True);await due_now(d,row)
    await entry(d,appointment_id=a,professional_id=str(d['pro']),amount_cents=10000,status='paid')
    assert await confirm_service_receipt({},str(d['a']),row['id'])=='changed'
    async with d['support'].connect() as db:
        candidates=(await db.execute(text('SELECT * FROM orion_due_service_receipts(100)'))).mappings().all()
        assert not any(str(r['entry_id'])==row['id'] for r in candidates)
