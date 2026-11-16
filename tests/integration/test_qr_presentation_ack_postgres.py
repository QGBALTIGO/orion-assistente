"""Presentation ACK uses real channel auth, RLS, control rows and Redis Lua.

Inference is replaced by a presentation-only response; no transport or booking
operation is available. The isolated runner denies all external networking.
"""
import hashlib
import json
import os
import time
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
import httpx
import pytest
import pytest_asyncio
from redis.asyncio import from_url
from sqlalchemy import text

from app import database, inbox_control as control, qr_ai, qr_channels, qr_legacy
from app import qr_assistant_interactions as choices, qr_channel_scope as scope
from tests.integration.test_automation_postgres import automation_db, PHONE  # noqa: F401
from tests.integration.test_inbox_tools_postgres import inbox_db  # noqa: F401

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def ack_case(inbox_db, monkeypatch):
    c = inbox_db
    channel, owner = qr_legacy.channel_id(c.tenant), uuid4()
    secret = 'synthetic-presentation-ack-' + str(uuid4())
    for key, value in {'INBOX_CONTROL_ENABLED': '0', 'QR_LEGACY_CENTRAL_ENABLED': '1',
                       'QR_AI_ENABLED': '1', 'QR_AI_TENANT_ID': str(c.tenant)}.items():
        monkeypatch.setenv(key, value)
    for module in (database, control, qr_ai):
        monkeypatch.setattr(module, 'get_session_factory', lambda: c.factory)
    async with c.migration.begin() as db:
        await db.execute(text("UPDATE tenants SET status='active',bot_enabled=true WHERE id=:t"), {'t': c.tenant})
        await db.execute(text("UPDATE tenant_plan_controls SET manual_plan_code='elite' WHERE tenant_id=:t"), {'t': c.tenant})
        await db.execute(text("""INSERT INTO qr_channels(id,tenant_id,owner_account_id,provider,label,
            credential_hash,credential_encrypted,allowed_senders,enabled,assistant_enabled,
            lease_owner,lease_expires_at,fence,idempotency_key,request_fingerprint)
            VALUES(:id,:t,:a,'baileys','Synthetic ACK',:key,'unused-synthetic',CAST(:senders AS jsonb),
            true,true,:owner,clock_timestamp()+interval '10 minutes',4,:registration,:fingerprint)"""),
            {'id': channel, 't': c.tenant, 'a': c.account, 'key': qr_channels.credential_hash(secret),
             'senders': json.dumps([PHONE.removeprefix('+')]), 'owner': owner,
             'registration': uuid4(), 'fingerprint': 'b'*64})
        await db.execute(text("""INSERT INTO inbox_channel_links(tenant_id,channel_id,provider,qr_channel_id)
            VALUES(:t,:c,'qr',:c)"""), {'t': c.tenant, 'c': channel})
    redis = from_url(os.environ['REDIS_URL'], socket_connect_timeout=2, socket_timeout=3)
    await redis.ping()
    state_key = f'orion:qr:ai:{c.tenant}:{PHONE.removeprefix("+")}'

    async def presentation_only(tenant, phone, _body, namespace, _message_id, **kwargs):
        assert scope.current().central_adopted and control.enabled()
        assert namespace == phone == PHONE.removeprefix('+')
        summary = 'Confira o resumo sintético antes de confirmar.'
        await redis.set(state_key, json.dumps({'step': 'confirm', 'confirmation_summary': summary,
            'confirmation_expires_at': time.time()+240, 'confirmation_message_id': 'synthetic-input',
            'hold_snapshot': 'a'*64, 'hold': str(uuid4())}), ex=240)
        return await choices.present(redis, tenant, phone, namespace,
            {'answer': summary, 'step': 'confirm'}, kwargs['capabilities'])

    monkeypatch.setattr(qr_ai, 'run_locked', presentation_only)
    api = FastAPI(); api.include_router(qr_ai.router); api.include_router(control.router)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url='https://synthetic.test')
    headers = {'X-Orion-Channel-Key': secret, 'X-Orion-Channel-Owner': str(owner), 'X-Orion-Channel-Fence': '4'}
    prefix = f'/internal/qr/channels/{channel}'

    async def post(path, payload, **kwargs):
        return await client.post(prefix+path, json=payload, headers={**headers, **kwargs.get('headers', {})})

    async def prepare():
        response = await post('/ai/reply', {'phone': PHONE.removeprefix('+'), 'message_id': 'synthetic-input',
            'text': 'Resumo sintético', 'intake_revision': 1, 'capabilities': {'assistant_interactions': 1}})
        assert response.status_code == 200, response.text
        result = response.json()
        return {'phone': PHONE.removeprefix('+'), 'presentation_id': result['presentation']['id'],
                'provider_id': 'synthetic-provider'}, result['control_revision']

    try:
        yield SimpleNamespace(db=c, channel=channel, owner=owner, redis=redis, key=state_key,
                              post=post, prepare=prepare)
    finally:
        await client.aclose()
        await redis.delete(state_key, state_key+':presentation')
        await redis.aclose()
        assert scope.current() is None and control._assistant_scope.get() is None
        assert not c.sent


async def test_adopted_ack_without_global_switch_uses_same_sql_revision_as_reply(ack_case):
    c = ack_case
    payload, revision = await c.prepare()
    assert revision == 1 and scope.current() is None and not control.enabled()
    response = await c.post('/ai/presentation-ack', payload)
    assert response.status_code == 200, response.text
    assert response.json() == {'acknowledged': True}
    record = json.loads(await c.redis.get(c.key+':presentation'))
    assert record['control_revision'] == revision and record['provider_id'] == payload['provider_id']
    assert scope.current() is None and not control.enabled()


@pytest.mark.parametrize('global_control', ['0', '1'])
async def test_ack_pending_own_send_is_blocked_then_succeeds_after_known_finish(ack_case, monkeypatch, global_control):
    c = ack_case
    monkeypatch.setenv('INBOX_CONTROL_ENABLED', global_control)
    payload, revision = await c.prepare()
    operation = str(uuid4()); body = 'Resumo sintético'
    slot = {'phone': payload['phone'], 'operation_id': operation, 'source': 'assistant', 'text': body,
            'fingerprint': hashlib.sha256(json.dumps([payload['phone'], body], ensure_ascii=False,
                separators=(',', ':')).encode()).hexdigest(),
            'control_revision': revision}
    authorized = await c.post('/send-authorize', slot)
    assert authorized.status_code == 200, authorized.text
    response = await c.post('/ai/presentation-ack', payload)
    assert response.status_code == 423, response.text
    assert json.loads(await c.redis.get(c.key+':presentation'))['provider_id'] is None
    finished = await c.post('/send-finish', {**slot, 'accepted': True, 'provider_id': payload['provider_id']})
    assert finished.status_code == 200, finished.text
    response = await c.post('/ai/presentation-ack', payload)
    assert response.status_code == 200, response.text


@pytest.mark.parametrize('change, expected', [('human', 423), ('revision', 409), ('owner', 409),
    ('fence', 409), ('expiry', 409), ('assistant', 403), ('tenant', 403), ('sender', 403)])
async def test_ack_preserves_current_control_and_channel_authority(ack_case, change, expected):
    c = ack_case; payload, _ = await c.prepare()
    async with c.db.migration.begin() as db:
        if change in {'human', 'revision'}:
            await db.execute(text("""UPDATE inbox_controls SET revision=revision+1,
                mode=CASE WHEN :human THEN 'human' ELSE mode END
                WHERE tenant_id=:t AND channel_id=:c"""),
                {'t': c.db.tenant, 'c': c.channel, 'human': change == 'human'})
        elif change in {'owner', 'fence', 'expiry', 'assistant'}:
            expression = {'owner': 'lease_owner=gen_random_uuid()', 'fence': 'fence=fence+1',
                'expiry': "lease_expires_at=clock_timestamp()-interval '1 second'", 'assistant': 'assistant_enabled=false'}[change]
            await db.execute(text(f'UPDATE qr_channels SET {expression} WHERE id=:c'), {'c': c.channel})
        elif change == 'tenant':
            await db.execute(text("UPDATE tenants SET bot_enabled=false WHERE id=:t"), {'t': c.db.tenant})
        else:
            payload = {**payload, 'phone': '5567999999902'}
    response = await c.post('/ai/presentation-ack', payload)
    assert response.status_code == expected, response.text
    assert json.loads(await c.redis.get(c.key+':presentation'))['provider_id'] is None
    assert scope.current() is None and control._assistant_scope.get() is None


async def test_ack_revalidates_revocation_after_initial_authentication(ack_case, monkeypatch):
    c = ack_case; payload, _ = await c.prepare()
    original = qr_ai._managed_assistant_ready

    async def revoked_after_read(identity):
        ready = await original(identity)
        async with c.db.migration.begin() as db:
            await db.execute(text('UPDATE qr_channels SET fence=fence+1 WHERE id=:c'), {'c': c.channel})
        return ready

    monkeypatch.setattr(qr_ai, '_managed_assistant_ready', revoked_after_read)
    response = await c.post('/ai/presentation-ack', payload)
    assert response.status_code == 409, response.text
    assert json.loads(await c.redis.get(c.key+':presentation'))['provider_id'] is None
    assert scope.current() is None


async def test_ack_scope_is_reset_when_redis_initialization_fails(ack_case, monkeypatch):
    import redis.asyncio
    c = ack_case; payload, _ = await c.prepare()

    def unavailable(*args, **kwargs):
        assert scope.current() is not None and control.enabled()
        raise RuntimeError('Synthetic Redis initialization failure')

    monkeypatch.setattr(redis.asyncio, 'from_url', unavailable)
    with pytest.raises(RuntimeError, match='Synthetic Redis initialization failure'):
        await c.post('/ai/presentation-ack', payload)
    assert scope.current() is None and control._assistant_scope.get() is None
