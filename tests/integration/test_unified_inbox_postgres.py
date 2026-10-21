"""All recipients, channels, provider calls and accounts here are synthetic."""
import asyncio
from datetime import datetime,timedelta,timezone
from uuid import uuid4,UUID

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app import inbox_runtime as runtime,inbox_control as control,inbox_conversations as api
from app.database import tenant_transaction
from tests.integration.test_inbox_tools_postgres import inbox_db
from tests.integration.test_automation_postgres import automation_db,PHONE
from tests.integration.test_communication_postgres import isolated_redis

pytestmark=pytest.mark.integration


@pytest_asyncio.fixture
async def central(inbox_db,monkeypatch):
    c=inbox_db
    monkeypatch.setenv('INBOX_SEND_ENABLED','1')
    monkeypatch.setattr(runtime,'get_session_factory',lambda:c.factory)
    monkeypatch.setattr(control,'get_session_factory',lambda:c.factory)
    async with c.factory() as db:
        async with tenant_transaction(db,c.tenant):
            await runtime.record_inbound(db,c.tenant,c.channel,PHONE,'synthetic-inbound','Olá')
    return c


async def assume(c,channel=None):
    return await control.change_control(c.tenant,channel or c.channel,control.Change(contact_e164=PHONE,expected_revision=2,mode='human'),c.actor)


async def test_explicit_ai_resume_invalidates_only_old_proposals_once(central,isolated_redis):
    import json
    from types import SimpleNamespace
    c=central;redis,key=isolated_redis
    await assume(c)
    await control.change_control(c.tenant,c.channel,control.Change(contact_e164=PHONE,expected_revision=3,mode='ai'),c.actor)
    revision=await control.assistant_revision(c.tenant,c.channel,PHONE)
    for suffix in ('',':commerce',':birthday-invite'):
        await redis.set(key+suffix,json.dumps({'step':'confirm','summary':'Old proposal'}),ex=300)
    await redis.set(key+':verified-receipt','committed-domain-receipt',ex=300)
    before=await api.messages(c.tenant,c.channel,PHONE,None,c.actor)
    token=control.bind_assistant(SimpleNamespace(tenant_id=c.tenant,channel_id=c.channel,provider='qr'),PHONE,revision)
    try:
        await control.clear_transient_handoff(redis,key)
        assert not await redis.exists(key,key+':commerce',key+':birthday-invite')
        assert await redis.get(key+':verified-receipt')==b'committed-domain-receipt'
        await redis.set(key,json.dumps({'step':'choose','new':True}),ex=300)
        await control.clear_transient_handoff(redis,key)
        assert json.loads(await redis.get(key))=={'step':'choose','new':True}
    finally:
        control.reset_assistant(token)
        await redis.delete(key,key+':ai-resume-generation',key+':verified-receipt')
    after=await api.messages(c.tenant,c.channel,PHONE,None,c.actor)
    assert after['items']==before['items'] and after['control']['mode']=='ai'


async def enqueue(c,operation=None,channel=None,body='Resposta sintética'):
    operation=operation or uuid4()
    result=await api.enqueue(c.tenant,channel or c.channel,api.Message(contact_e164=PHONE,expected_revision=3,operation_id=operation,text=body),c.actor)
    return operation,result


async def row(c,operation):
    async with c.factory() as db:
        async with tenant_transaction(db,c.tenant):
            return dict((await db.execute(text('SELECT * FROM inbox_messages WHERE tenant_id=:t AND id=:id'),{'t':c.tenant,'id':operation})).mappings().one())


async def test_history_exists_with_assistant_off_and_is_tenant_scoped(central):
    c=central
    data=await api.messages(c.tenant,c.channel,PHONE,None,c.actor)
    assert data['items'][0]['body']=='Olá' and data['control']['mode']=='ai'
    assert (await api.conversations(c.tenant,c.channel,'','',0,c.actor))['items'][0]['customer_id']==str(c.customer)
    async with c.factory() as db:
        assert not (await db.execute(text('SELECT id FROM inbox_messages'))).all()
    async with c.factory() as db:
        async with tenant_transaction(db,c.other):
            assert not (await db.execute(text('SELECT id FROM inbox_messages'))).all()
            with pytest.raises(DBAPIError):
                await db.execute(text("INSERT INTO inbox_channel_links(tenant_id,channel_id,provider,qr_channel_id) VALUES(:t,:c,'qr',:c)"),{'t':c.other,'c':c.channel})


async def test_unread_cursor_is_personal_monotonic_and_does_not_send_provider_receipts(central):
    c=central
    listed=await api.conversations(c.tenant,c.channel,'','Olá',0,c.actor)
    assert listed['items'][0]['unread_count']==1 and listed['items'][0]['message_count']==1
    data=await api.messages(c.tenant,c.channel,PHONE,None,c.actor)
    cursor=UUID(data['items'][-1]['id'])
    for _ in range(2):
        result=await api.mark_read(c.tenant,c.channel,api.ReadThrough(contact_e164=PHONE,message_id=cursor),c.actor)
        assert result=={'read':True,'provider_read_receipt':False}
    assert not (await api.conversations(c.tenant,c.channel,'','',0,c.actor,True))['items']
    with pytest.raises(HTTPException):
        await api.mark_read(c.tenant,c.channel,api.ReadThrough(contact_e164=PHONE,message_id=uuid4()),c.actor)
    async with c.factory() as db:
        async with tenant_transaction(db,c.tenant):
            await runtime.record_inbound(db,c.tenant,c.channel,PHONE,'new-message','Nova mensagem')
    assert (await api.conversations(c.tenant,c.channel,'','',0,c.actor,True))['items'][0]['unread_count']==1


@pytest.mark.parametrize('mode',['waiting','closed'])
async def test_customer_reopens_waiting_or_closed_conversation_without_resuming_ai(central,mode):
    c=central;await assume(c)
    await control.change_control(c.tenant,c.channel,control.Change(contact_e164=PHONE,expected_revision=3,mode=mode),c.actor)
    async with c.factory() as db:
        async with tenant_transaction(db,c.tenant):
            await runtime.record_inbound(db,c.tenant,c.channel,PHONE,'reopen-'+mode,'Retornei')
    resumed=await control.get_control(c.tenant,c.channel,PHONE,c.actor)
    assert resumed['mode']=='human' and await control.blocked(c.tenant,c.channel,PHONE)
    assert resumed['assigned_account_id']==(str(c.account) if mode=='waiting' else None)


async def test_manual_requires_claim_then_idempotent_request_and_single_worker(central):
    c=central
    with pytest.raises(HTTPException):
        await enqueue(c)
    await assume(c)
    operation,result=await enqueue(c)
    assert result['status']=='queued' and result['sent'] is False
    assert (await enqueue(c,operation))[1]['status']=='queued'
    with pytest.raises(HTTPException):
        await enqueue(c,operation,body='Conteúdo diferente')
    entered,release=asyncio.Event(),asyncio.Event()
    calls=[]
    async def sender(*args,**kwargs):
        calls.append(kwargs['message']);entered.set();await release.wait()
        return {'state':'accepted','accepted':True}
    task=asyncio.create_task(runtime.process_inbox_message({'inbox_qr_sender':sender},str(c.tenant),str(operation)))
    await asyncio.wait_for(entered.wait(),5)
    try:
        assert await runtime.process_inbox_message({'inbox_qr_sender':sender},str(c.tenant),str(operation))=='already_claimed'
        with pytest.raises(HTTPException):
            await control.change_control(c.tenant,c.channel,control.Change(contact_e164=PHONE,expected_revision=3,mode='ai'),c.actor)
    finally:
        release.set()
    assert await task=='accepted'
    assert len(calls)==1 and calls[0]['source']=='manual'
    assert (await row(c,operation))['status']=='accepted'
    assert (await enqueue(c,operation))[1]['sent'] is True


async def test_timeout_or_restart_never_retries_or_releases_uncertain_slot(central):
    c=central;await assume(c);operation,_=await enqueue(c);calls=[]
    async def lost(*args,**kwargs):
        calls.append(1);raise TimeoutError('synthetic unknown provider outcome')
    assert await runtime.process_inbox_message({'inbox_qr_sender':lost},str(c.tenant),str(operation))=='uncertain'
    assert await runtime.process_inbox_message({'inbox_qr_sender':lost},str(c.tenant),str(operation))=='already_claimed'
    assert calls==[1] and (await row(c,operation))['status']=='uncertain'
    assert (await control.get_control(c.tenant,c.channel,PHONE,c.actor))['pending_operation']==str(operation)
    with pytest.raises(HTTPException):
        await control.change_control(c.tenant,c.channel,control.Change(contact_e164=PHONE,expected_revision=3,mode='ai'),c.actor)


@pytest.mark.parametrize('ingress',[True,False])
async def test_customer_request_during_pending_send_pauses_ai_without_releasing_slot(central,ingress):
    c=central;operation=uuid4()
    async with c.factory() as db:
        async with tenant_transaction(db,c.tenant):
            await db.execute(text("""UPDATE inbox_controls SET pending_operation=:op,pending_fingerprint=:fp,pending_since=now()
              WHERE tenant_id=:t AND channel_id=:c AND contact_e164=:phone"""),{'op':operation,'fp':'b'*64,'t':c.tenant,'c':c.channel,'phone':PHONE})
            if ingress:
                await runtime.record_inbound(db,c.tenant,c.channel,PHONE,'human-while-busy','Preciso falar com um atendente')
    if not ingress:await control.customer_handoff(c.tenant,c.channel,PHONE)
    state=await control.get_control(c.tenant,c.channel,PHONE,c.actor)
    assert state['mode']=='human' and state['assigned_account_id'] is None
    assert state['pending_operation']==str(operation) and await control.blocked(c.tenant,c.channel,PHONE)
    with pytest.raises(HTTPException):
        await control.change_control(c.tenant,c.channel,control.Change(contact_e164=PHONE,expected_revision=state['revision'],mode='ai'),c.actor)


@pytest.mark.parametrize('revoke',['membership','password','control'])
async def test_revalidation_before_transport_cancels_revoked_or_obsolete_manual_work(central,revoke):
    c=central;await assume(c);operation,_=await enqueue(c)
    if revoke=='control':
        await control.change_control(c.tenant,c.channel,control.Change(contact_e164=PHONE,expected_revision=3,mode='ai'),c.actor)
    else:
        sql='UPDATE tenant_memberships SET active=false WHERE tenant_id=:t' if revoke=='membership' else 'UPDATE panel_accounts SET auth_version=auth_version+1 WHERE id=:a'
        async with c.migration.begin() as db:
            await db.execute(text(sql),{'t':c.tenant,'a':c.account})
    async def forbidden(*a,**k):
        pytest.fail('revoked work reached transport')
    assert await runtime.process_inbox_message({'inbox_qr_sender':forbidden},str(c.tenant),str(operation))=='cancelled_before_send'
    assert (await row(c,operation))['status']=='failed'


@pytest_asyncio.fixture
async def meta(central,monkeypatch):
    from cryptography.fernet import Fernet
    c=central;c.meta=uuid4();key=Fernet.generate_key()
    monkeypatch.setenv('INTEGRATION_FERNET_KEY',key.decode());monkeypatch.setenv('META_LIVE_SEND','1')
    async with c.migration.begin() as db:
        await db.execute(text("""INSERT INTO whatsapp_channels(id,tenant_id,phone_number_id,business_number,status,token_enc,bot_enabled)
          VALUES(:id,:t,:number,'+5567999999902','connected',:token,true)"""),{'id':c.meta,'t':c.tenant,'number':'synthetic-'+c.meta.hex,'token':Fernet(key).encrypt(b'synthetic-token')})
        await db.execute(text('UPDATE tenants SET bot_enabled=true WHERE id=:t'),{'t':c.tenant})
    async with c.factory() as db:
        async with tenant_transaction(db,c.tenant):
            await runtime.record_inbound(db,c.tenant,c.meta,PHONE,'synthetic-meta-in','Olá Meta',provider_at=datetime.now(timezone.utc))
    try:
        yield c
    finally:
        async with c.migration.begin() as db:
            await db.execute(text('DELETE FROM inbox_reads WHERE tenant_id=:t AND control_id IN(SELECT id FROM inbox_controls WHERE channel_id=:c)'),{'t':c.tenant,'c':c.meta})
            await db.execute(text('DELETE FROM inbox_provider_receipts WHERE tenant_id=:t'),{'t':c.tenant})
            await db.execute(text('DELETE FROM inbox_messages WHERE tenant_id=:t AND control_id IN(SELECT id FROM inbox_controls WHERE channel_id=:c)'),{'t':c.tenant,'c':c.meta})
            await db.execute(text('DELETE FROM inbox_control_events WHERE tenant_id=:t AND control_id IN(SELECT id FROM inbox_controls WHERE channel_id=:c)'),{'t':c.tenant,'c':c.meta})
            await db.execute(text('DELETE FROM inbox_controls WHERE tenant_id=:t AND channel_id=:c'),{'t':c.tenant,'c':c.meta})
            await db.execute(text('DELETE FROM inbox_channel_links WHERE tenant_id=:t AND channel_id=:c'),{'t':c.tenant,'c':c.meta})
            await db.execute(text('DELETE FROM message_outbox WHERE tenant_id=:t'),{'t':c.tenant})
            await db.execute(text('DELETE FROM inbound_events WHERE tenant_id=:t'),{'t':c.tenant})
            await db.execute(text('DELETE FROM usage_counters WHERE tenant_id=:t'),{'t':c.tenant})
            await db.execute(text('DELETE FROM whatsapp_channels WHERE id=:c'),{'c':c.meta})


async def test_meta_human_send_and_early_out_of_order_receipts(meta):
    c=meta;await assume(c,c.meta);operation,_=await enqueue(c,channel=c.meta);external='synthetic-wamid-'+str(operation)
    async def sender(channel,phone,body):
        assert channel['access_token']=='synthetic-token' and phone==PHONE and body=='Resposta sintética'
        async with c.factory() as db:
            async with tenant_transaction(db,c.tenant):
                for status in ['read','accepted','delivered']:
                    await runtime.provider_receipt(db,c.tenant,c.meta,PHONE,external,status)
        return external
    assert await runtime.process_inbox_message({'inbox_meta_sender':sender},str(c.tenant),str(operation))=='accepted'
    assert (await row(c,operation))['status']=='read'
    assert not (await control.get_control(c.tenant,c.meta,PHONE,c.actor))['pending_operation']


async def test_meta_window_closed_never_calls_provider(meta):
    c=meta;await assume(c,c.meta);operation,_=await enqueue(c,channel=c.meta)
    async with c.migration.begin() as db:
        await db.execute(text("UPDATE inbox_messages SET provider_at=now()-interval '25 hours' WHERE tenant_id=:t AND direction='in'"),{'t':c.tenant})
    async def forbidden(*a):pytest.fail('closed service window reached Meta')
    assert await runtime.process_inbox_message({'inbox_meta_sender':forbidden},str(c.tenant),str(operation))=='cancelled_before_send'


async def test_disabled_meta_does_not_starve_qr_or_replay_a_backlog(meta,monkeypatch):
    c=meta;await assume(c,c.meta);operation,_=await enqueue(c,channel=c.meta)
    monkeypatch.setenv('META_LIVE_SEND','0')
    async def forbidden(*a):pytest.fail('disabled Meta reached provider')
    assert await runtime.process_inbox_message({'inbox_meta_sender':forbidden},str(c.tenant),str(operation))=='meta_not_enabled'
    assert (await row(c,operation))['status']=='failed'
    monkeypatch.setenv('META_LIVE_SEND','1')
    assert await runtime.process_inbox_message({'inbox_meta_sender':forbidden},str(c.tenant),str(operation))=='already_claimed'


async def test_meta_verified_operation_receipt_recovers_timeout_without_resending(meta):
    c=meta;await assume(c,c.meta);operation,_=await enqueue(c,channel=c.meta);calls=[]
    async def lost(*args):calls.append(1);raise TimeoutError('synthetic timeout after acceptance')
    assert await runtime.process_inbox_message({'inbox_meta_sender':lost},str(c.tenant),str(operation))=='uncertain'
    async with c.factory() as db:
        async with tenant_transaction(db,c.tenant):
            await runtime.provider_receipt(db,c.tenant,c.meta,PHONE,'verified-wamid','read',str(operation))
    assert (await row(c,operation))['status']=='read'
    assert not (await control.get_control(c.tenant,c.meta,PHONE,c.actor))['pending_operation']
    assert await runtime.process_inbox_message({'inbox_meta_sender':lost},str(c.tenant),str(operation))=='already_claimed'
    assert calls==[1]


async def test_old_queued_text_is_cancelled_without_resuming_or_sending(central):
    c=central;await assume(c);operation,_=await enqueue(c)
    async with c.migration.begin() as db:
        await db.execute(text("UPDATE inbox_messages SET created_at=now()-interval '11 minutes' WHERE id=:id"),{'id':operation})
    async def forbidden(*a,**k):pytest.fail('expired draft reached transport')
    assert await runtime.process_inbox_message({'inbox_qr_sender':forbidden},str(c.tenant),str(operation))=='cancelled_before_send'
    assert (await control.get_control(c.tenant,c.channel,PHONE,c.actor))['mode']=='human'


async def test_meta_domain_transaction_rejects_stale_generation_after_operator_takeover(meta):
    from types import SimpleNamespace
    c=meta;revision=await control.assistant_revision(c.tenant,c.meta,PHONE)
    await assume(c,c.meta)
    token=control.bind_assistant(SimpleNamespace(tenant_id=c.tenant,channel_id=c.meta),PHONE,revision)
    try:
        with pytest.raises(HTTPException):
            async with c.factory() as db:
                async with tenant_transaction(db,c.tenant):
                    pytest.fail('stale Meta generation entered domain transaction')
    finally:control.reset_assistant(token)


@pytest.mark.parametrize('handoff',[False,True])
@pytest.mark.parametrize('legacy_pending',[False,True])
async def test_meta_worker_prepares_under_authority_and_never_dispatches_legacy_outbox(meta,isolated_redis,monkeypatch,handoff,legacy_pending):
    from app import worker
    c=meta;event=uuid4()
    monkeypatch.setattr(worker,'get_session_factory',lambda:c.factory)
    async with c.migration.begin() as db:
        if legacy_pending:
            await db.execute(text("DELETE FROM inbox_messages WHERE tenant_id=:t AND external_id='synthetic-meta-in'"),{'t':c.tenant})
        await db.execute(text("""INSERT INTO inbound_events(id,tenant_id,channel_id,external_id,sender_e164,message_text)
          VALUES(:id,:t,:c,'synthetic-meta-in',:phone,'Olá Meta')"""),{'id':event,'t':c.tenant,'c':c.meta,'phone':PHONE})
    async def reply(tenant,channel,phone,*args,**kwargs):
        assert control._assistant_scope.get()[:3]==(c.tenant,c.meta,PHONE)
        if handoff:
            await control.customer_handoff(tenant,channel,phone)
            return None
        return 'Resposta Meta sintética'
    monkeypatch.setattr(worker,'reply',reply)
    ctx={'redis':isolated_redis[0]}
    assert await worker.process_inbound_event(ctx,str(c.tenant),str(event))=='processed'
    assert await worker.process_inbound_event(ctx,str(c.tenant),str(event))=='already_processed_or_processing'
    assert await worker.process_message_outbox(ctx,str(c.tenant),str(uuid4()))=='central_outbox_required'
    async with c.factory() as db:
        async with tenant_transaction(db,c.tenant):
            outgoing=(await db.execute(text("SELECT status,source,body FROM inbox_messages WHERE tenant_id=:t AND direction='out'"),{'t':c.tenant})).mappings().all()
            assert len(outgoing)==(0 if handoff else 1)
            if outgoing:assert outgoing[0]=={'status':'queued','source':'assistant','body':'Resposta Meta sintética'}
            assert (await db.execute(text('SELECT message_text FROM inbound_events WHERE id=:id'),{'id':event})).scalar_one()==''
            original=(await db.execute(text("SELECT body,provider_at FROM inbox_messages WHERE tenant_id=:t AND external_id='synthetic-meta-in'"),{'t':c.tenant})).mappings().one()
            assert original['body']=='Olá Meta'
            if legacy_pending:assert original['provider_at'] is None
