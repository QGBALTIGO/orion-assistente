"""Actual adopted ASGI notice/reply/ACK/notice/selection over real PG/Redis.

No provider transport or model is available. Fixture keys/data are synthetic.
The complete assistant turn and all authorization/selection guards stay real.
"""
import hashlib
import json
import os
from pathlib import Path
import time
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
import httpx
import pytest
import pytest_asyncio
from redis.asyncio import from_url
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app import database, automation_runtime, inbox_control as control, inbox_conversations, qr_channels, qr_legacy
from app import qr_ai, qr_assistant_interactions as choices
from tests.integration.test_qr_presentation_ack_postgres import (  # noqa: F401
    inbox_db, automation_db, PHONE,
)

pytestmark = pytest.mark.integration
REAL_RUN_LOCKED = qr_ai.run_locked


async def seed_catalog(c):
    async with c.db.migration.begin() as db:
        for index in range(14):
            await db.execute(text('''INSERT INTO services(id,tenant_id,name,duration_min,price_cents)
                VALUES(:id,:tenant,:name,30,:price)'''),
                {'id':uuid4(),'tenant':c.db.tenant,'name':f'Serviço sintético {index+1:02}','price':3500+index*100})


async def journal_notice(c,message_id,body='[Escolha recebida]',kind='text'):
    response=await c.post('/messages',{'phone':PHONE.removeprefix('+'),'message_id':message_id,'text':body,'kind':kind})
    assert response.status_code==200,response.text
    notice=await c.post('/interaction',{'phone':PHONE.removeprefix('+'),'message_id':message_id})
    assert notice.status_code==200,notice.text
    return notice.json()['revision']


async def issue_catalog(c):
    await seed_catalog(c)
    revision=await journal_notice(c,'synthetic-catalog-input','lista')
    response=await c.post('/ai/reply',{'phone':PHONE.removeprefix('+'),'message_id':'synthetic-catalog-input',
        'text':'lista','intake_revision':revision,'capabilities':{'assistant_interactions':1}})
    assert response.status_code==200,response.text
    result=response.json();assert result['step']=='catalog' and result.get('presentation'),result
    page=result['presentation']
    row=next(row for row in page['action']['sections'][0]['rows'] if row['title']=='Mais serviços')
    return page,row


async def acknowledge_menu(c,page):
    return await c.post('/ai/presentation-ack',{'phone':PHONE.removeprefix('+'),
        'presentation_id':page['id'],'provider_id':'synthetic-catalog-provider'})


async def click_menu(c,row,revision,message_id='synthetic-native-click'):
    return await c.post('/ai/reply',{'phone':PHONE.removeprefix('+'),'message_id':message_id,
        'text':'[Escolha recebida]','intake_revision':revision,'capabilities':{'assistant_interactions':1},
        'selection':{'id':row['id'],'quoted_message_id':'synthetic-catalog-provider'}})


async def assert_no_domain_write(c):
    async with c.db.migration.connect() as db:
        for table in ('appointments','calendar_outbox'):
            assert (await db.execute(text(f'SELECT count(*) FROM {table} WHERE tenant_id=:t'),{'t':c.db.tenant})).scalar_one()==0
    assert not c.db.sent


@pytest_asyncio.fixture
async def roundtrip_case(inbox_db,monkeypatch):
    c=inbox_db
    # The shared domain fixture deliberately uses NullPool. A full steady-state
    # ASGI conversation must use the bounded production pool behavior, while
    # retaining the same app_runtime role, RLS and transaction guards.
    assert make_url(os.environ['APP_DATABASE_URL']).username=='app_runtime'
    pooled_engine=create_async_engine(os.environ['APP_DATABASE_URL'],pool_pre_ping=True,pool_size=5,max_overflow=0)
    pooled_factory=async_sessionmaker(pooled_engine,expire_on_commit=False)
    channel,owner=qr_legacy.channel_id(c.tenant),uuid4()
    secret='synthetic-native-roundtrip-'+str(uuid4())
    for key,value in {'INBOX_CONTROL_ENABLED':'0','QR_LEGACY_CENTRAL_ENABLED':'1',
                      'QR_AI_ENABLED':'1','QR_AI_TENANT_ID':str(c.tenant)}.items():monkeypatch.setenv(key,value)
    monkeypatch.setattr(database,'get_engine',lambda:pooled_engine)
    for module in (database,control,qr_ai,automation_runtime):monkeypatch.setattr(module,'get_session_factory',lambda:pooled_factory)
    async with c.migration.begin() as db:
        await db.execute(text("UPDATE tenants SET status='active',bot_enabled=true WHERE id=:t"),{'t':c.tenant})
        await db.execute(text("UPDATE tenant_plan_controls SET manual_plan_code='elite' WHERE tenant_id=:t"),{'t':c.tenant})
        await db.execute(text('''INSERT INTO qr_channels(id,tenant_id,owner_account_id,provider,label,
            credential_hash,credential_encrypted,allowed_senders,enabled,assistant_enabled,
            lease_owner,lease_expires_at,fence,idempotency_key,request_fingerprint)
            VALUES(:id,:t,:a,'baileys','Synthetic roundtrip',:key,'unused-synthetic',CAST(:senders AS jsonb),
            true,true,:owner,clock_timestamp()+interval '10 minutes',4,:registration,:fingerprint)'''),
            {'id':channel,'t':c.tenant,'a':c.account,'key':qr_channels.credential_hash(secret),
             'senders':json.dumps([PHONE.removeprefix('+')]),'owner':owner,'registration':uuid4(),'fingerprint':'b'*64})
        await db.execute(text("INSERT INTO inbox_channel_links(tenant_id,channel_id,provider,qr_channel_id) VALUES(:t,:c,'qr',:c)"),{'t':c.tenant,'c':channel})
    redis=from_url(os.environ['REDIS_URL'],socket_connect_timeout=2,socket_timeout=3)
    await redis.ping()
    key=f'orion:qr:ai:{c.tenant}:{PHONE.removeprefix("+")}'
    api=FastAPI();api.include_router(qr_ai.router);api.include_router(control.router);api.include_router(inbox_conversations.router)
    headers={'X-Orion-Channel-Key':secret,'X-Orion-Channel-Owner':str(owner),'X-Orion-Channel-Fence':'4'}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api),base_url='https://synthetic.test') as client:
        async def post(path,payload):return await client.post(f'/internal/qr/channels/{channel}'+path,json=payload,headers=headers)
        try:yield SimpleNamespace(db=c,channel=channel,owner=owner,redis=redis,key=key,post=post)
        finally:
            try:
                patterns=[f'orion:qr:ai:{c.tenant}:*',f'orion:qr:intake:{c.tenant}:*',
                          f'orion:qr:interaction:{c.tenant}:*',f'orion:qr:ai:done:{c.tenant}:*']
                for pattern in patterns:
                    keys=[item async for item in redis.scan_iter(match=pattern)]
                    if keys:await redis.delete(*keys)
                async with c.migration.begin() as db:await db.execute(text('DELETE FROM services WHERE tenant_id=:t'),{'t':c.tenant})
            finally:
                await redis.aclose()
                await pooled_engine.dispose()


@pytest.mark.parametrize('require_review', [False, True])
@pytest.mark.parametrize('envelope_kind', ['none', 'interactive'])
async def test_adopted_native_catalog_click_survives_second_incoming_notice(
        roundtrip_case, monkeypatch, require_review, envelope_kind):
    c=roundtrip_case
    monkeypatch.setattr(qr_ai, 'run_locked', REAL_RUN_LOCKED)
    async def no_model(*_args, **_kwargs):
        raise AssertionError('Catalog navigation must not ask an external model')
    monkeypatch.setattr(qr_ai, 'interpret', no_model)
    await seed_catalog(c)
    trace=[]
    async def snapshot(phase):
        state=json.loads(await c.redis.get(c.key) or '{}')
        presentation=json.loads(await c.redis.get(c.key+':presentation') or 'null')
        async with c.db.migration.connect() as db:
            control=(await db.execute(text('''SELECT mode,revision,pending_operation IS NOT NULL AS pending
                FROM inbox_controls WHERE tenant_id=:tenant AND channel_id=:channel AND contact_e164=:phone'''),
                {'tenant':c.db.tenant,'channel':c.channel,'phone':PHONE})).mappings().one_or_none()
        item={'phase':phase,'state':state,'state_digest':await choices.state_digest(c.redis,c.key),
              'presentation':presentation,'control':dict(control) if control else None}
        trace.append(item)
        return item

    original_selected=choices.selected_action
    original_revision=choices.control_revision
    observed_revisions=[]
    async def revision_probe(*args,**kwargs):
        result=await original_revision(*args,**kwargs)
        observed_revisions.append(result)
        return result
    monkeypatch.setattr(choices,'control_revision',revision_probe)
    async def selected_probe(redis,tenant,phone,namespace,selection,**kwargs):
        record=json.loads(await redis.get(c.key+':presentation') or 'null')
        digest=await choices.state_digest(redis,c.key)
        revision_start=len(observed_revisions)
        guard={'phase':'selected_action_guard','record_exists':record is not None,
               'authority_matches':bool(record and record.get('authority')==choices.authority(tenant,phone,namespace)),
               'control_stored':record.get('control_revision') if record else None,
               'deadline_remaining':record.get('expires_at',0)-time.time() if record else None,
               'quote_matches':bool(record and record.get('provider_id')==selection.quoted_message_id),
               'state_matches':bool(record and record.get('state')==digest),
               'token_exists':bool(record and hashlib.sha256(selection.id.encode()).hexdigest() in record.get('choices',{}))}
        trace.append(guard)
        result=await original_selected(redis,tenant,phone,namespace,selection,**kwargs)
        guard['control_current']=observed_revisions[-1] if len(observed_revisions)>revision_start else None
        guard['selected']=result is not None
        return result
    monkeypatch.setattr(choices,'selected_action',selected_probe)
    try:
        await snapshot('before_notice1')
        first_journal=await c.post('/messages',{'phone':PHONE.removeprefix('+'),'message_id':'synthetic-catalog-input','text':'lista','kind':'text'})
        assert first_journal.status_code==200,first_journal.text
        await snapshot('after_journal1')
        first_notice=await c.post('/interaction',{'phone':PHONE.removeprefix('+'),'message_id':'synthetic-catalog-input'})
        assert first_notice.status_code==200,first_notice.text
        await snapshot('after_notice1')
        response=await c.post('/ai/reply',{'phone':PHONE.removeprefix('+'),'message_id':'synthetic-catalog-input',
            'text':'lista','intake_revision':first_notice.json()['revision'],
            'capabilities':{'assistant_interactions':1}})
        assert response.status_code==200,response.text
        reply=response.json()
        assert reply['step']=='catalog' and reply.get('presentation'),reply
        presentation=reply['presentation']
        rows=presentation['action']['sections'][0]['rows']
        next_row=next(row for row in rows if row['title']=='Mais serviços')
        await snapshot('after_reply1')
        ack=await c.post('/ai/presentation-ack',{'phone':PHONE.removeprefix('+'),
            'presentation_id':presentation['id'],'provider_id':'synthetic-catalog-provider'})
        assert ack.status_code==200,ack.text
        before=await snapshot('after_ack')
        click_journal=await c.post('/messages',{'phone':PHONE.removeprefix('+'),'message_id':'synthetic-native-click',
            'text':'[Escolha recebida]','kind':'text',
            **({'envelope':{'version':1,'kind':'interactive','reply_to':'synthetic-catalog-provider'}} if envelope_kind=='interactive' else {})})
        assert click_journal.status_code==200,click_journal.text
        await snapshot('after_journal2')
        notice=await c.post('/interaction',{'phone':PHONE.removeprefix('+'),'message_id':'synthetic-native-click'})
        assert notice.status_code==200,notice.text
        after=await snapshot('after_notice2')
        assert before['state_digest']==after['state_digest'],'A normal incoming notice must not rewrite the catalog state'
        selected=await c.post('/ai/reply',{'phone':PHONE.removeprefix('+'),'message_id':'synthetic-native-click',
            'text':'[Escolha recebida]','intake_revision':notice.json()['revision'],
            'capabilities':{'assistant_interactions':1},'require_summary_review':require_review,
            **({'envelope':{'version':1,'kind':'interactive','reply_to':'synthetic-catalog-provider'}} if envelope_kind=='interactive' else {}),
            'selection':{'id':next_row['id'],'quoted_message_id':'synthetic-catalog-provider'}})
        await snapshot('after_selection_reply')
        assert selected.status_code==200,selected.text
        result=selected.json()
        if envelope_kind=='interactive':
            # This counterfactual metadata is deliberately NOT emitted by the
            # primary typed-click adapter. Preserve the conservative repair.
            assert result['step']=='event_repair' and 'presentation' not in result,result
            assert (await snapshot('unsafe_envelope_repair'))['state']['confirmation_requires_review'] is True
        else:
            assert result['step']=='catalog',result
            assert result['presentation']['action']['description'].rsplit('\n\n',1)[-1]=='14 serviços · Página 2/2',result
            assert result['presentation']['action']['footer']=='',result
            assert all(guard['selected'] for guard in trace if guard['phase']=='selected_action_guard')
            assert result['presentation']['id']!=presentation['id']
            after_page=await snapshot('page2_verified')
            assert len(after_page['state']['catalog_menu_ids'])==6
            assert not set(before['state']['catalog_menu_ids']) & set(after_page['state']['catalog_menu_ids'])
        async with c.db.migration.connect() as db:
            assert (await db.execute(text('SELECT count(*) FROM appointments WHERE tenant_id=:t'),{'t':c.db.tenant})).scalar_one()==0
            assert (await db.execute(text('SELECT count(*) FROM calendar_outbox WHERE tenant_id=:t'),{'t':c.db.tenant})).scalar_one()==0
        assert not c.db.sent
    finally:
        directory=os.getenv('ORION_NATIVE_SELECTION_EVIDENCE')
        if directory:
            folder=Path(directory);folder.mkdir(parents=True,exist_ok=True)
            (folder/f'catalog-{envelope_kind}-review-{require_review}.json').write_text(json.dumps(trace,ensure_ascii=False,indent=2,default=str),encoding='utf8')
        async with c.db.migration.begin() as db:
            await db.execute(text('DELETE FROM services WHERE tenant_id=:tenant'),{'tenant':c.db.tenant})
        await c.redis.delete(c.key,c.key+':presentation',c.key+':catalog-context')
        patterns=[f'orion:qr:intake:{c.db.tenant}:*',f'orion:qr:interaction:{c.db.tenant}:*',f'orion:qr:ai:done:{c.db.tenant}:*']
        for pattern in patterns:
            keys=[key async for key in c.redis.scan_iter(match=pattern)]
            if keys:await c.redis.delete(*keys)


async def test_lost_ack_after_current_click_records_receipt_without_rebasing_or_renewing(roundtrip_case):
    c=roundtrip_case
    page,row=await issue_catalog(c)
    before=json.loads(await c.redis.get(c.key+':presentation'))
    ttl=await c.redis.pttl(c.key+':presentation')
    assert before['provider_id'] is None
    revision=await journal_notice(c,'synthetic-native-click')
    ack=await acknowledge_menu(c,page)
    assert ack.status_code==200,ack.text
    after=json.loads(await c.redis.get(c.key+':presentation'))
    assert after['provider_id']=='synthetic-catalog-provider'
    assert after['control_revision']==before['control_revision']
    # Redis's existing cjson ACK encoding rounds epoch floats to 14 digits.
    # Account for at most 100 microseconds of representation, never a new TTL.
    assert abs(after['expires_at']-before['expires_at'])<0.0001
    assert 0<await c.redis.pttl(c.key+':presentation')<=ttl
    response=await click_menu(c,row,revision)
    assert response.status_code==200,response.text
    assert response.json()['step']=='catalog' and response.json()['presentation']['action']['description'].rsplit('\n\n',1)[-1]=='14 serviços · Página 2/2'
    assert response.json()['presentation']['action']['footer']==''
    replay=await click_menu(c,row,revision)
    assert replay.status_code==200 and replay.json()['step']=='duplicate',replay.text
    await assert_no_domain_write(c)


@pytest.mark.parametrize('change', ['delta_two','operator','previous_human','wrong_body','wrong_kind','wrong_id',
                                  'foreign_phone_control','foreign_tenant_control','expired','state','fence'])
async def test_native_click_never_borrows_an_unrelated_generation_or_stale_authority(roundtrip_case,change):
    c=roundtrip_case
    page,row=await issue_catalog(c)
    ack=await acknowledge_menu(c,page);assert ack.status_code==200,ack.text
    if change=='delta_two':await journal_notice(c,'synthetic-other-input','Quero consultar outro assunto')
    body='Texto comum' if change=='wrong_body' else '[Escolha recebida]'
    revision=await journal_notice(c,'synthetic-native-click',body,'audio' if change=='wrong_kind' else 'text')
    message_id='synthetic-native-click'
    if change=='wrong_id':
        message_id='synthetic-different-input'
        notice=await c.post('/interaction',{'phone':PHONE.removeprefix('+'),'message_id':message_id})
        assert notice.status_code==200;revision=notice.json()['revision']
    if change in {'operator','previous_human','foreign_phone_control','foreign_tenant_control','fence'}:
        async with c.db.migration.begin() as db:
            values={'t':c.db.tenant,'channel':c.channel,'phone':PHONE,'account':c.db.account}
            if change=='operator':
                await db.execute(text('''UPDATE inbox_control_events SET source='operator',actor_account_id=:account
                    WHERE tenant_id=:t AND control_id=(SELECT id FROM inbox_controls WHERE tenant_id=:t
                    AND channel_id=:channel AND contact_e164=:phone) AND revision=3'''),values)
            elif change=='previous_human':
                await db.execute(text('''UPDATE inbox_control_events SET previous_mode='human'
                    WHERE tenant_id=:t AND control_id=(SELECT id FROM inbox_controls WHERE tenant_id=:t
                    AND channel_id=:channel AND contact_e164=:phone) AND revision=3'''),values)
            elif change=='foreign_phone_control':
                other=uuid4()
                await db.execute(text("INSERT INTO inbox_controls(id,tenant_id,channel_id,contact_e164) VALUES(:id,:t,:channel,'+5567999999902')"),{**values,'id':other})
                await db.execute(text("UPDATE inbox_messages SET control_id=:other WHERE tenant_id=:t AND external_id='synthetic-native-click'"),{**values,'other':other})
            elif change=='foreign_tenant_control':
                other_control,other_channel=uuid4(),uuid4()
                await db.execute(text("INSERT INTO tenant_memberships(tenant_id,account_id,role) VALUES(:t,:a,'owner')"),
                    {'t':c.db.other,'a':c.db.account})
                await db.execute(text('''INSERT INTO qr_channels(id,tenant_id,owner_account_id,provider,label,
                    credential_hash,credential_encrypted,allowed_senders,enabled,idempotency_key,request_fingerprint)
                    VALUES(:id,:t,:a,'baileys','Foreign synthetic','cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc',
                    'unused-synthetic',CAST(:senders AS jsonb),true,:key,'cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc')'''),
                    {'id':other_channel,'t':c.db.other,'a':c.db.account,'senders':json.dumps([PHONE.removeprefix('+')]),'key':uuid4()})
                await db.execute(text("INSERT INTO inbox_channel_links(tenant_id,channel_id,provider,qr_channel_id) VALUES(:t,:channel,'qr',:channel)"),
                    {'t':c.db.other,'channel':other_channel})
                await db.execute(text('INSERT INTO inbox_controls(id,tenant_id,channel_id,contact_e164) VALUES(:id,:t,:channel,:phone)'),
                    {'id':other_control,'t':c.db.other,'channel':other_channel,'phone':PHONE})
                await db.execute(text("UPDATE inbox_messages SET tenant_id=:other_tenant,control_id=:other WHERE tenant_id=:t AND external_id='synthetic-native-click'"),
                    {**values,'other_tenant':c.db.other,'other':other_control})
            else:await db.execute(text('UPDATE qr_channels SET fence=fence+1 WHERE tenant_id=:t AND id=:channel'),values)
    elif change=='expired':
        record=json.loads(await c.redis.get(c.key+':presentation'));record['expires_at']=time.time()-1
        await c.redis.set(c.key+':presentation',json.dumps(record),ex=300)
    elif change=='state':
        state=json.loads(await c.redis.get(c.key));state['confirmation_requires_review']=True
        await c.redis.set(c.key,json.dumps(state),ex=300)
    response=await click_menu(c,row,revision,message_id)
    if change=='fence':assert response.status_code==409,response.text
    else:assert response.status_code==200 and response.json()['step']=='interaction_expired',response.text
    await assert_no_domain_write(c)


@pytest.mark.parametrize('change',['wrong_body','wrong_kind','operator','delta_two','expired'])
async def test_ack_recovery_is_only_a_current_marker_receipt(roundtrip_case,change):
    c=roundtrip_case
    page,_row=await issue_catalog(c)
    original=json.loads(await c.redis.get(c.key+':presentation'))
    if change=='delta_two':await journal_notice(c,'synthetic-extra-input','Outro assunto')
    await journal_notice(c,'synthetic-native-click','Texto comum' if change=='wrong_body' else '[Escolha recebida]',
                         'audio' if change=='wrong_kind' else 'text')
    if change=='operator':
        async with c.db.migration.begin() as db:
            await db.execute(text('''UPDATE inbox_control_events SET source='operator',actor_account_id=:a
                WHERE tenant_id=:t AND revision=3'''),{'t':c.db.tenant,'a':c.db.account})
    elif change=='expired':
        original['expires_at']=time.time()-1;await c.redis.set(c.key+':presentation',json.dumps(original),ex=300)
    response=await acknowledge_menu(c,page)
    assert response.status_code==409,response.text
    after=json.loads(await c.redis.get(c.key+':presentation'))
    assert after['provider_id'] is None and after['control_revision']==original['control_revision']
    assert after['expires_at']==original['expires_at']
    await assert_no_domain_write(c)
