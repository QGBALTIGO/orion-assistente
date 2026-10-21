"""Communication effects against local PG/Redis, with zero real transports."""
import asyncio
from datetime import datetime,timedelta,timezone
import json
import os
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException
from redis.asyncio import from_url
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app import contact_preferences as birthday,communication_api as api,automation_runtime as runtime,automation_api,worker
from app.communication_events import bind_meta
from app.database import tenant_transaction
from tests.integration.test_automation_postgres import automation_db,queue,PHONE
from tests.redis_test_scope import communication_redis_url

pytestmark=pytest.mark.integration


@pytest_asyncio.fixture
async def isolated_redis():
    url=os.getenv('ORION_INTAKE_TEST_REDIS_URL')
    if not url:
        if os.getenv('ORION_REQUIRE_INTAKE_REDIS_TEST')=='1' or os.getenv('REQUIRE_REDIS_TESTS')=='1':
            pytest.fail('Mandatory disposable communication Redis not configured')
        pytest.skip('Explicit disposable Redis required')
    try:url=communication_redis_url(url)
    except ValueError:pytest.fail('Communication tests require the designated isolated Redis database15')
    redis=from_url(url,socket_connect_timeout=2,socket_timeout=3)
    await redis.ping()
    key='orion:communication:test:'+uuid4().hex
    try:yield redis,key
    finally:
        await redis.delete(key,key+':birthday-invite')
        await redis.aclose()


async def booking(c):
    identity,pro=uuid4(),uuid4();start=datetime.now(timezone.utc)+timedelta(days=2)
    async with c.migration.begin() as db:
        await db.execute(text("INSERT INTO professionals(id,tenant_id,name) VALUES(:id,:t,'Synthetic professional')"),{'id':pro,'t':c.tenant})
        await db.execute(text('''INSERT INTO appointments(id,tenant_id,customer_id,professional_id,protocol,idempotency_key,request_fingerprint,
          start_at,end_at,occupied_from,occupied_until,status,total_cents)
          VALUES(:id,:t,:c,:p,'SYNTCOMM1',:key,:fp,:start,:end,:start,:end,'confirmed',3500)'''),
          {'id':identity,'t':c.tenant,'c':c.customer,'p':pro,'key':str(uuid4()),'fp':uuid4().hex*2,'start':start,'end':start+timedelta(hours=1)})
    return identity


@pytest.mark.parametrize('month,day',[(None,15),(2,None),(2,31),(13,1),(1,0)])
async def test_database_rejects_incomplete_or_invalid_birthday_pair(automation_db,month,day):
    c=automation_db
    with pytest.raises(DBAPIError):
        async with c.migration.begin() as db:
            await db.execute(text('UPDATE customers SET birth_month=:m,birth_day=:d WHERE id=:id'),{'m':month,'d':day,'id':c.customer})


async def test_optional_birthday_has_purpose_and_preserves_full_legacy_date_and_booking(automation_db,isolated_redis,monkeypatch):
    c=automation_db;redis,key=isolated_redis
    monkeypatch.setenv('BIRTHDAY_COLLECTION_ENABLED','1');monkeypatch.setattr(birthday,'get_session_factory',lambda:c.factory)
    identity=await booking(c)
    async with c.migration.begin() as db:
        await db.execute(text("UPDATE customers SET birth_date='1992-01-10' WHERE id=:id"),{'id':c.customer})
        await db.execute(text('UPDATE contact_consents SET marketing_opt_in=false,operational_opt_in=false WHERE tenant_id=:t'),{'t':c.tenant})
    assert 'opcional' in await birthday.offer_birthday(c.tenant,PHONE,redis,key,'SYNTCOMM1')
    result=await birthday.birthday_turn(c.tenant,PHONE,'29/02 e qual o endereço?',redis,key)
    assert result['remainder']=='qual o endereço?'
    async with c.migration.begin() as db:
        row=(await db.execute(text('SELECT birth_month,birth_day,birth_date FROM customers WHERE id=:id'),{'id':c.customer})).one()
        assert tuple(row[:2])==(2,29) and row.birth_date.isoformat()=='1992-01-10'
        consent=(await db.execute(text('SELECT * FROM contact_consents WHERE tenant_id=:t'),{'t':c.tenant})).mappings().one()
        assert consent['birthday_opt_in'] and consent['birthday_purpose_version']==1
        assert not consent['marketing_opt_in'] and not consent['operational_opt_in']
        assert (await db.execute(text('SELECT status FROM appointments WHERE id=:id'),{'id':identity})).scalar_one()=='confirmed'
    assert not await redis.exists(key+':birthday-invite') and not c.sent


async def test_refusal_is_persistent_and_does_not_interrupt_booking_or_other_consent(automation_db,isolated_redis,monkeypatch):
    c=automation_db;redis,key=isolated_redis
    monkeypatch.setenv('BIRTHDAY_COLLECTION_ENABLED','1');monkeypatch.setattr(birthday,'get_session_factory',lambda:c.factory)
    identity=await booking(c)
    assert await birthday.offer_birthday(c.tenant,PHONE,redis,key,'SYNTCOMM1')
    assert 'não vou insistir' in (await birthday.birthday_turn(c.tenant,PHONE,'prefiro não informar',redis,key))['answer']
    assert await birthday.offer_birthday(c.tenant,PHONE,redis,key,'SYNTCOMM1')==''
    async with c.migration.begin() as db:
        row=(await db.execute(text('SELECT * FROM contact_consents WHERE tenant_id=:t'),{'t':c.tenant})).mappings().one()
        assert row['birthday_declined_at'] and row['marketing_opt_in'] and row['operational_opt_in'] and not row['birthday_opt_in']
        assert (await db.execute(text('SELECT status FROM appointments WHERE id=:id'),{'id':identity})).scalar_one()=='confirmed'
    assert not c.sent


async def test_old_birthday_invitation_cannot_regrant_after_a_separate_revocation(automation_db,isolated_redis,monkeypatch):
    c=automation_db;redis,key=isolated_redis
    monkeypatch.setenv('BIRTHDAY_COLLECTION_ENABLED','1');monkeypatch.setattr(birthday,'get_session_factory',lambda:c.factory)
    await booking(c);assert await birthday.offer_birthday(c.tenant,PHONE,redis,key,'SYNTCOMM1')
    async with c.factory() as db:
        async with tenant_transaction(db,c.tenant):await birthday.record_optout(db,c.tenant,PHONE,'birthday')
    result=await birthday.birthday_turn(c.tenant,PHONE,'15/08',redis,key)
    assert 'Não alterei sua data nem reativei' in result['answer']
    async with c.migration.begin() as db:
        assert (await db.execute(text('SELECT birth_day FROM customers WHERE id=:id'),{'id':c.customer})).scalar_one() is None
        assert not (await db.execute(text('SELECT birthday_opt_in FROM contact_consents WHERE tenant_id=:t'),{'t':c.tenant})).scalar_one()
    assert not c.sent


async def test_birthday_only_revocation_preserves_already_reviewed_marketing_job(automation_db):
    c=automation_db;created,job=await queue(c);assert created
    async with c.factory() as db:
        async with tenant_transaction(db,c.tenant):
            await birthday.record_optout(db,c.tenant,PHONE,'birthday')
            assert await runtime.job_reason(db,c.tenant,job,await runtime.settings_for(db,c.tenant)) is None
            assert (await db.execute(text('SELECT status FROM automation_jobs WHERE id=:id'),{'id':job['id']})).scalar_one()=='pending'
    assert not c.sent


async def important(c,key='synthetic-confirmation'):
    identity=await booking(c)
    async with c.migration.begin() as db:
        await db.execute(text('''UPDATE management_preferences SET preferences=jsonb_set(preferences,'{automations,rules}',
          CAST(:rules AS jsonb)) WHERE tenant_id=:t'''),{'t':c.tenant,'rules':json.dumps([{'event':'booking_confirmed','requested_enabled':True,'template':'Seu protocolo: {protocolo}'}])})
        row=(await db.execute(text('SELECT * FROM appointments WHERE id=:id'),{'id':identity})).mappings().one()
    return identity,await queue(c,key,event='booking_confirmed',facts={**dict(row),'appointment_id':identity,'occurred_at':datetime.now(timezone.utc)},labels={'protocolo':'SYNTCOMM1'})


async def test_marketing_revocation_preserves_operational_job_and_its_consent_version(automation_db):
    c=automation_db;identity,(created,job)=await important(c);assert created
    async with c.factory() as db:
        async with tenant_transaction(db,c.tenant):
            await birthday.record_optout(db,c.tenant,PHONE,'marketing')
            assert await runtime.job_reason(db,c.tenant,job,await runtime.settings_for(db,c.tenant)) is None
    assert not c.sent


async def test_panel_birthday_revocation_preserves_existing_operational_permission_and_job(automation_db):
    c=automation_db;_,(created,job)=await important(c);assert created
    async with c.migration.begin() as db:
        await db.execute(text('UPDATE contact_consents SET birthday_opt_in=true,birthday_purpose_version=1 WHERE tenant_id=:t'),{'t':c.tenant})
        current=(await db.execute(text('SELECT updated_at FROM contact_consents WHERE tenant_id=:t'),{'t':c.tenant})).scalar_one()
    await automation_api.consent(c.tenant,automation_api.ContactConsent(contact_e164=PHONE,marketing_opt_in=True,
        operational_opt_in=True,birthday_opt_in=False,expected_updated_at=current,evidence='Synthetic explicit birthday revocation'),c.actor)
    async with c.factory() as db:
        async with tenant_transaction(db,c.tenant):
            assert await runtime.job_reason(db,c.tenant,job,await runtime.settings_for(db,c.tenant)) is None
            record=(await db.execute(text('SELECT * FROM contact_consents WHERE tenant_id=:t'),{'t':c.tenant})).mappings().one()
            assert record['birthday_declined_at'] and record['operational_opt_in'] and not record['birthday_opt_in']
    assert not c.sent


async def test_important_human_notice_is_team_only_scoped_and_ack_does_not_resume_or_send(automation_db):
    c=automation_db
    async with c.migration.begin() as db:
        await db.execute(text('INSERT INTO automation_contact_state(tenant_id,contact_e164,human_paused) VALUES(:t,:phone,true)'),{'t':c.tenant,'phone':PHONE})
    _,(created,job)=await important(c);assert not created and job is None
    data=await api.pending(c.tenant,None,c.actor);assert len(data['items'])==1 and data['audience']=='team'
    item=data['items'][0];assert item['reason']=='human_paused'
    async with c.factory() as db:
        async with tenant_transaction(db,c.other):assert not (await db.execute(text('SELECT id FROM communication_pending'))).all()
    result=await api.acknowledge(c.tenant,item['id'],api.Acknowledge(expected_version=1),c.actor)
    assert not result['sent']
    with pytest.raises(HTTPException):await api.acknowledge(c.tenant,item['id'],api.Acknowledge(expected_version=1),c.actor)
    async with c.migration.begin() as db:assert (await db.execute(text('SELECT human_paused FROM automation_contact_state WHERE tenant_id=:t'),{'t':c.tenant})).scalar_one()
    assert not c.sent


async def test_new_meta_receipt_fences_old_write_and_delete_never_undoes_committed_booking(automation_db,isolated_redis,monkeypatch):
    c=automation_db;redis,key=isolated_redis;identity=await booking(c);channel,old,new=uuid4(),uuid4(),uuid4()
    monkeypatch.setattr(worker,'get_session_factory',lambda:c.factory)
    async with c.migration.begin() as db:
        await db.execute(text("INSERT INTO whatsapp_channels(id,tenant_id,phone_number_id,business_number,status) VALUES(:id,:t,:number,'+5567999999900','testing')"),{'id':channel,'t':c.tenant,'number':uuid4().hex})
    try:
        async def receipt(event,external,envelope):
            async with c.migration.begin() as db:
                await db.execute(text("INSERT INTO inbound_events(id,tenant_id,channel_id,external_id,sender_e164,message_text,envelope) VALUES(:id,:t,:c,:external,:phone,'sim',CAST(:envelope AS jsonb))"),{'id':event,'t':c.tenant,'c':channel,'external':external,'phone':PHONE,'envelope':json.dumps(envelope)})
        await receipt(old,'old',{'kind':'text'})
        ready,release=asyncio.Event(),asyncio.Event()
        async def obsolete_write():
            with bind_meta(c.tenant,channel,PHONE,old):
                ready.set();await release.wait()
                async with c.factory() as db:
                    async with tenant_transaction(db,c.tenant):await db.execute(text("UPDATE customers SET name='MUST NOT COMMIT' WHERE id=:id"),{'id':c.customer})
        task=asyncio.create_task(obsolete_write());await ready.wait()
        await receipt(new,'new',{'kind':'deleted','target':'old'})
        release.set()
        with pytest.raises(HTTPException) as error:await task
        assert error.value.status_code==409
        result=await worker._event_answer(c.tenant,new,{'channel_id':channel,'sender_e164':PHONE,'message_text':'','envelope':{'version':1,'kind':'deleted','target':'old'}},redis)
        assert 'não cancela' in result
        async with c.migration.begin() as db:
            assert (await db.execute(text('SELECT name FROM customers WHERE id=:id'),{'id':c.customer})).scalar_one()=='Cliente sintético'
            assert (await db.execute(text('SELECT status FROM appointments WHERE id=:id'),{'id':identity})).scalar_one()=='confirmed'
    finally:
        async with c.migration.begin() as db:
            await db.execute(text('DELETE FROM inbound_events WHERE channel_id=:c'),{'c':channel})
            await db.execute(text('DELETE FROM whatsapp_channels WHERE id=:c'),{'c':channel})
        from app.whatsapp_bot import state_key
        await redis.delete(state_key(str(c.tenant),str(channel),PHONE))


async def test_meta_requires_a_persisted_accepted_summary_for_the_same_recipient(automation_db):
    from app.communication_events import summary_was_accepted
    c=automation_db;channel,event=uuid4(),uuid4();body='Confira o agendamento de teste.'
    async with c.migration.begin() as db:
        await db.execute(text("INSERT INTO whatsapp_channels(id,tenant_id,phone_number_id,business_number,status) VALUES(:id,:t,:number,'+5567999999900','testing')"),{'id':channel,'t':c.tenant,'number':uuid4().hex})
        await db.execute(text("INSERT INTO inbound_events(id,tenant_id,channel_id,external_id,sender_e164,message_text) VALUES(:id,:t,:c,'synthetic-summary',:phone,'quero conferir')"),{'id':event,'t':c.tenant,'c':channel,'phone':PHONE})
        await db.execute(text('INSERT INTO message_outbox(tenant_id,channel_id,event_id,to_e164,body) VALUES(:t,:c,:e,:p,:body)'),{'t':c.tenant,'c':channel,'e':event,'p':PHONE,'body':body})
    state={'summary_message_id':str(event),'last_answer':body}
    try:
        assert not await summary_was_accepted(c.tenant,channel,PHONE,state)
        async with c.migration.begin() as db:
            # Trusted synthetic transport fixture; no provider is invoked.
            await db.execute(text("UPDATE message_outbox SET status='sent',external_delivery_id='synthetic-receipt' WHERE tenant_id=:t AND event_id=:e"),{'t':c.tenant,'e':event})
        assert await summary_was_accepted(c.tenant,channel,PHONE,state)
        assert not await summary_was_accepted(c.other,channel,PHONE,state)
        assert not await summary_was_accepted(c.tenant,channel,'+5567999999902',state)
        assert not await summary_was_accepted(c.tenant,channel,PHONE,{**state,'last_answer':'Outro resumo'})
    finally:
        async with c.migration.begin() as db:
            for table in ('message_outbox','inbound_events','whatsapp_channels'):
                await db.execute(text('DELETE FROM '+table+' WHERE tenant_id=:t AND '+('id=:c' if table=='whatsapp_channels' else 'channel_id=:c')),{'t':c.tenant,'c':channel})


async def test_timezone_change_invalidates_a_prepared_operational_message(automation_db):
    c=automation_db;_,(created,job)=await important(c);assert created
    async with c.migration.begin() as db:await db.execute(text("UPDATE tenants SET timezone='Pacific/Auckland' WHERE id=:t"),{'t':c.tenant})
    async with c.factory() as db:
        async with tenant_transaction(db,c.tenant):assert await runtime.job_reason(db,c.tenant,job,await runtime.settings_for(db,c.tenant))=='configuration_changed'
    assert not c.sent


async def test_real_preview_and_queued_payload_have_identical_body_and_presentation(automation_db):
    c=automation_db;identity,(created,job)=await important(c);assert created
    result=await api.preview(c.tenant,api.Preview(event='booking_confirmed',customer_id=c.customer,
        appointment_id=identity,expected_preferences_version=1),c.actor)
    assert result['body']==job['body'] and result['presentation']==job['presentation']
    assert result['appointment']['id']==str(identity) and result['timezone']==job['presentation']['source_timezone']
    assert result['sent'] is False and result['execution_authorized'] is False
    assert not c.sent


async def test_every_queue_caller_captures_style_revision_and_revalidates_it(automation_db,monkeypatch):
    from app import communication_style
    revision={'id':'synthetic-published-profile-one'}
    async def style(*_):return {'emoji':'light','_published_profile_id':revision['id']}
    monkeypatch.setattr(communication_style,'style_for',style)
    c=automation_db;created,job=await queue(c);assert created
    assert job['presentation']['profile_revision']==revision['id']
    revision['id']='synthetic-published-profile-two'
    async with c.factory() as db:
        async with tenant_transaction(db,c.tenant):assert await runtime.job_reason(db,c.tenant,job,await runtime.settings_for(db,c.tenant))=='configuration_changed'
    assert not c.sent
