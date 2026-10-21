"""Real PG authorization; all accounts, contacts and transports are synthetic."""
import base64
import hashlib

import pytest
import pytest_asyncio
from sqlalchemy import text

from app import legacy_admin_identity as identity
from app import inbox_runtime as runtime, inbox_conversations as api
from app.catalog_api import Actor
from app.database import tenant_transaction
from tests.integration.test_automation_postgres import automation_db, PHONE
from tests.integration.test_inbox_tools_postgres import inbox_db
from tests.integration.test_unified_inbox_postgres import central, assume, enqueue, row

pytestmark = pytest.mark.integration


def encoded_password(value):
    salt = b'synthetic-native-admin-runtime'
    digest = hashlib.pbkdf2_hmac('sha256', value.encode(), salt, 200_000)
    return 'pbkdf2_sha256$200000$' + base64.urlsafe_b64encode(salt).decode() + '$' + base64.urlsafe_b64encode(digest).decode()


@pytest_asyncio.fixture
async def native_operator(central, monkeypatch):
    c = central
    monkeypatch.setenv('PANEL_LEGACY_ADMIN_IDENTITY_ENABLED', '1')
    monkeypatch.setenv('PANEL_ADMIN_USERNAME', 'synthetic-native-admin')
    monkeypatch.setenv('PANEL_ADMIN_PASSWORD_HASH', encoded_password('synthetic-initial-password'))
    username, digest = identity.configuration_binding()
    async with c.migration.begin() as db:
        await db.execute(text("""UPDATE panel_accounts SET role='admin',email=NULL,password_hash=NULL,
            auth_provider='legacy_admin',legacy_username=:username,legacy_credential_digest=:digest
            WHERE id=:account"""), {'username': username, 'digest': digest, 'account': c.account})
    c.actor = Actor('admin', account_id=c.account)
    return c


async def test_native_admin_claim_read_cursor_and_fake_send_use_real_account_id(native_operator):
    c = native_operator
    before = await api.messages(c.tenant, c.channel, PHONE, None, c.actor)
    from uuid import UUID
    cursor = UUID(before['items'][-1]['id'])
    assert await api.mark_read(c.tenant, c.channel, api.ReadThrough(contact_e164=PHONE, message_id=cursor), c.actor) == {'read': True, 'provider_read_receipt': False}
    await assume(c)
    operation, queued = await enqueue(c)
    assert queued['status'] == 'queued' and queued['sent'] is False
    calls = []
    async def fake_sender(*args, **kwargs):
        calls.append(kwargs['message'])
        return {'state': 'accepted', 'accepted': True}
    assert await runtime.process_inbox_message({'inbox_qr_sender': fake_sender}, str(c.tenant), str(operation)) == 'accepted'
    assert len(calls) == 1 and calls[0]['source'] == 'manual'
    stored = await row(c, operation)
    assert stored['actor_account_id'] == c.account and stored['actor_auth_version'] == 1


@pytest.mark.parametrize('revocation', ['password', 'username', 'flag', 'missing_hash', 'auth_version', 'owner'])
async def test_native_admin_queued_work_revalidated_before_any_transport(native_operator, monkeypatch, revocation):
    c = native_operator
    await assume(c)
    operation, _ = await enqueue(c)
    if revocation == 'password':
        monkeypatch.setenv('PANEL_ADMIN_PASSWORD_HASH', encoded_password('synthetic-rotated-password'))
    elif revocation == 'username':
        monkeypatch.setenv('PANEL_ADMIN_USERNAME', 'synthetic-other-admin')
    elif revocation == 'flag':
        monkeypatch.setenv('PANEL_LEGACY_ADMIN_IDENTITY_ENABLED', '0')
    elif revocation == 'missing_hash':
        monkeypatch.delenv('PANEL_ADMIN_PASSWORD_HASH')
    else:
        query = ('UPDATE panel_accounts SET auth_version=auth_version+1 WHERE id=:account'
                 if revocation == 'auth_version' else
                 'UPDATE tenant_memberships SET active=false WHERE tenant_id=:tenant AND account_id=:account')
        async with c.migration.begin() as db:
            await db.execute(text(query), {'tenant': c.tenant, 'account': c.account})
    async def forbidden(*args, **kwargs):
        pytest.fail('revoked native-admin work reached a transport')
    assert await runtime.process_inbox_message({'inbox_qr_sender': forbidden}, str(c.tenant), str(operation)) == 'cancelled_before_send'
    assert (await row(c, operation))['status'] == 'failed'


async def test_native_operator_guard_requires_server_context_and_exact_tenant(native_operator):
    c = native_operator
    async with c.factory() as db:
        async with tenant_transaction(db, c.tenant):
            assert (await db.execute(text('SELECT public.inbox_operator_version(:tenant,:account)'), {'tenant': c.tenant, 'account': c.account})).scalar_one() is None
            assert await runtime.operator_version(db, c.tenant, c.account) == 1
            assert await runtime.operator_version(db, c.other, c.account) is None
    async with c.factory() as db:
        async with tenant_transaction(db, c.tenant):
            assert (await db.execute(text("SELECT NULLIF(current_setting('app.legacy_admin_digest',true),'')"))).scalar_one() is None
