"""Real loopback PostgreSQL, fake Meta only; no real destination or token."""
import asyncio
from datetime import datetime,timedelta,timezone
import json
from uuid import uuid4
import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from sqlalchemy import text
from app import legacy_meta_outbox as outbox,management_preferences
from app.database import tenant_transaction
from tests.integration.test_automation_postgres import automation_db,PHONE

pytestmark=pytest.mark.integration


@pytest_asyncio.fixture
async def legacy(automation_db,monkeypatch):
    c=automation_db;c.meta=uuid4();c.event=uuid4();c.outbox=uuid4()
    cipher=Fernet.generate_key();monkeypatch.setenv('INTEGRATION_FERNET_KEY',cipher.decode())
    monkeypatch.setenv('INBOX_CONTROL_ENABLED','0');monkeypatch.setenv('META_LIVE_SEND','1')
    monkeypatch.setattr(outbox,'get_session_factory',lambda:c.factory)
    monkeypatch.setattr(management_preferences,'get_session_factory',lambda:c.factory,raising=False)
    async with c.migration.begin() as db:
        await db.execute(text("UPDATE tenants SET status='active',bot_enabled=true WHERE id=:t"),{'t':c.tenant})
        await db.execute(text("INSERT INTO whatsapp_channels(id,tenant_id,phone_number_id,business_number,status,bot_enabled,token_enc) VALUES(:id,:t,:number,'+5567999999900','connected',true,:token)"),
            {'id':c.meta,'t':c.tenant,'number':uuid4().hex,'token':Fernet(cipher).encrypt(b'synthetic-token-only')})
        await db.execute(text("INSERT INTO inbound_events(id,tenant_id,channel_id,external_id,sender_e164,message_text,envelope) VALUES(:id,:t,:c,'synthetic-reply',:phone,'Qual o preco?',CAST(:e AS jsonb))"),
            {'id':c.event,'t':c.tenant,'c':c.meta,'phone':PHONE,'e':json.dumps({'kind':'text','provider_at':int(datetime.now(timezone.utc).timestamp())-5})})
        await db.execute(text("INSERT INTO message_outbox(id,tenant_id,channel_id,event_id,to_e164,body) VALUES(:id,:t,:c,:e,:phone,'Resposta sintetica')"),
            {'id':c.outbox,'t':c.tenant,'c':c.meta,'e':c.event,'phone':PHONE})
    try:yield c
    finally:
        async with c.migration.begin() as db:
            for table in ('message_outbox','inbound_events','human_handoffs','inbox_control_events','inbox_controls','inbox_channel_links'):
                await db.execute(text(f'DELETE FROM {table} WHERE tenant_id=:t'+(' AND channel_id=:c' if table not in {'inbox_control_events'} else '')),{'t':c.tenant,'c':c.meta})
            await db.execute(text('DELETE FROM whatsapp_channels WHERE id=:id'),{'id':c.meta})


async def read(c):
    async with c.migration.begin() as db:return dict((await db.execute(text('SELECT * FROM message_outbox WHERE id=:id'),{'id':c.outbox})).mappings().one())


async def test_attempt_is_committed_before_io_and_second_worker_cannot_send(legacy):
    c=legacy;entered,release=asyncio.Event(),asyncio.Event();calls=[]
    async def send(channel,phone,body):
        calls.append((channel['operation_id'],phone,body));entered.set();await release.wait();return 'synthetic-accepted'
    task=asyncio.create_task(outbox.process({'legacy_meta_sender':send},str(c.tenant),str(c.outbox)))
    await asyncio.wait_for(entered.wait(),5)
    try:
        row=await read(c)
        assert row['status']=='dead' and row['last_error']=='delivery_unverified' and row['attempts']==1
        assert await outbox.process({'legacy_meta_sender':send},str(c.tenant),str(c.outbox))=='unavailable'
    finally:release.set()
    assert await task=='sent' and len(calls)==1 and (await read(c))['status']=='sent'


async def test_timeout_never_retries_and_authenticated_receipt_reconciles(legacy):
    c=legacy;calls=[]
    async def send(*args):calls.append(1);raise TimeoutError('synthetic ambiguous acceptance')
    ctx={'legacy_meta_sender':send}
    assert await outbox.process(ctx,str(c.tenant),str(c.outbox))=='uncertain'
    assert await outbox.process(ctx,str(c.tenant),str(c.outbox))=='unavailable' and calls==[1]
    async with c.factory() as db:
        async with tenant_transaction(db,c.tenant):
            await outbox.receipt(db,c.tenant,c.meta,'+5567999999902','provider-id','delivered',c.outbox)
            await outbox.receipt(db,c.tenant,uuid4(),PHONE,'provider-id','delivered',c.outbox)
    assert (await read(c))['last_error']=='delivery_unverified'
    async with c.factory() as db:
        async with tenant_transaction(db,c.tenant):await outbox.receipt(db,c.tenant,c.meta,PHONE,'provider-id','delivered',c.outbox)
    row=await read(c);assert row['status']=='sent' and row['external_delivery_id']=='provider-id' and not row['last_error']


@pytest.mark.parametrize('case',['bot_off','old_window','no_window','future_window','expired_reply','newer_event','human_pause','previous_attempt','oversize_payload'])
async def test_stale_or_unauthorized_reply_never_reaches_provider(legacy,case):
    c=legacy;calls=[]
    async def send(*args):calls.append(1);return 'MUST-NOT-SEND'
    async with c.migration.begin() as db:
        if case=='bot_off':await db.execute(text('UPDATE whatsapp_channels SET bot_enabled=false WHERE id=:id'),{'id':c.meta})
        if case in {'old_window','no_window','future_window'}:
            e={} if case=='no_window' else {'provider_at':int((datetime.now(timezone.utc)+timedelta(hours=-25 if case=='old_window' else 1)).timestamp())}
            await db.execute(text('UPDATE inbound_events SET envelope=CAST(:e AS jsonb) WHERE id=:id'),{'id':c.event,'e':json.dumps(e)})
        if case=='expired_reply':await db.execute(text("UPDATE inbound_events SET received_at=now()-interval '11 minutes' WHERE id=:id"),{'id':c.event})
        if case=='newer_event':await db.execute(text("INSERT INTO inbound_events(tenant_id,channel_id,external_id,sender_e164,message_text) VALUES(:t,:c,'newer',:p,'nao espera')"),{'t':c.tenant,'c':c.meta,'p':PHONE})
        if case=='human_pause':await db.execute(text('INSERT INTO automation_contact_state(tenant_id,contact_e164,human_paused) VALUES(:t,:p,true)'),{'t':c.tenant,'p':PHONE})
        if case=='previous_attempt':await db.execute(text('UPDATE message_outbox SET attempts=1 WHERE id=:id'),{'id':c.outbox})
        if case=='oversize_payload':await db.execute(text('UPDATE message_outbox SET body=:body WHERE id=:id'),{'id':c.outbox,'body':'🙂'*601})
    assert await outbox.process({'legacy_meta_sender':send},str(c.tenant),str(c.outbox)) in {'suppressed','verification_required'}
    assert not calls and (await read(c))['status']=='dead'


async def test_automation_consent_does_not_interrupt_customer_requested_dialogue(legacy):
    c=legacy;calls=[]
    async with c.migration.begin() as db:await db.execute(text('UPDATE contact_consents SET operational_opt_in=false,marketing_opt_in=false WHERE tenant_id=:t'),{'t':c.tenant})
    async def send(*args):calls.append(1);return 'synthetic-id'
    assert await outbox.process({'legacy_meta_sender':send},str(c.tenant),str(c.outbox))=='sent' and calls==[1]


async def test_crash_after_reservation_cannot_repeat_attempt(legacy):
    c=legacy
    async def killed(*args):raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):await outbox.process({'legacy_meta_sender':killed},str(c.tenant),str(c.outbox))
    assert (await read(c))['last_error']=='delivery_unverified'
    async def no_send(*args):pytest.fail('Replay after crash')
    assert await outbox.process({'legacy_meta_sender':no_send},str(c.tenant),str(c.outbox))=='unavailable'
