"""Native admin authentication with fake storage only; no provisioning or sends."""
import base64
import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException
import httpx
import pytest
from sqlalchemy.exc import SQLAlchemyError

from app import account_auth, legacy_admin_identity as identity, panel_auth


def encoded_hash(rounds=220000, salt=b'synthetic-testing-salt', expected=b'x' * 32):
    return ('pbkdf2_sha256$' + str(rounds) + '$' + base64.urlsafe_b64encode(salt).decode()
            + '$' + base64.urlsafe_b64encode(expected).decode())


class Store:
    def __init__(self):
        self.entries = {}

    async def get(self, key): return self.entries.get(key)
    async def set(self, key, value, **kwargs): self.entries[key] = value; return True
    async def delete(self, key): self.entries.pop(key, None)
    async def aclose(self): pass
    def pipeline(self): return self
    def incr(self, key): self.counter = key; return self
    def expire(self, key, seconds): return self
    async def execute(self): self.entries[self.counter] = str(int(self.entries.get(self.counter, '0')) + 1)


class Database:
    def __init__(self, row=None):
        self.row, self.calls, self.error = row, [], None

    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass

    async def execute(self, statement, parameters):
        query = str(statement)
        assert query.lstrip().startswith('SELECT'), 'Authentication must never provision an identity'
        self.calls.append((query, parameters))
        if self.error:
            raise self.error
        row = self.row
        if ':provider' in query:
            if (row is None or row['auth_provider'] != parameters['provider'] or row['role'] != 'admin'
                    or row['legacy_username'] != parameters['username']
                    or row['legacy_credential_digest'] != parameters['digest']
                    or row['email'] is not None or row['password_hash'] is not None
                    or parameters['account'] is not None and row['id'] != parameters['account']):
                row = None
        elif 'COALESCE(to_jsonb(' in query and row and row['auth_provider'] != 'password':
            row = None
        return SimpleNamespace(mappings=lambda: SimpleNamespace(one_or_none=lambda: row, first=lambda: row))


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv('PANEL_ADMIN_USERNAME', 'admin')
    monkeypatch.setenv('PANEL_ADMIN_PASSWORD_HASH', encoded_hash())
    monkeypatch.setenv('PANEL_LEGACY_ADMIN_IDENTITY_ENABLED', '1')
    username, digest = identity.configuration_binding()
    row = {'id': uuid4(), 'name': 'Administrador', 'role': 'admin', 'auth_version': 4,
           'auth_provider': 'legacy_admin', 'legacy_username': username,
           'legacy_credential_digest': digest, 'email': None, 'password_hash': None}
    db, store, app = Database(row), Store(), FastAPI()
    monkeypatch.setattr(identity, '_storage', lambda: lambda: db)
    monkeypatch.setattr(account_auth, '_storage', lambda: lambda: db)
    monkeypatch.setattr(panel_auth, '_redis', lambda: store)
    monkeypatch.setattr(panel_auth, '_password_valid', lambda password: password == 'synthetic-password')
    app.include_router(panel_auth.router)

    @app.post('/protected')
    async def protected(actor=Depends(panel_auth.panel_actor)):
        return {'role': actor['role']}

    return SimpleNamespace(db=db, store=store, app=app, row=row)


def test_binding_is_domain_separated_normalized_and_changes_with_environment_generation(monkeypatch):
    monkeypatch.setenv('PANEL_ADMIN_USERNAME', '  ADMIN  ')
    monkeypatch.setenv('PANEL_ADMIN_PASSWORD_HASH', encoded_hash())
    username, digest = identity.configuration_binding()
    assert username == 'admin'
    assert digest == hashlib.sha256(b'orion:legacy-admin-identity:v1\0admin\0' + encoded_hash().encode()).hexdigest()
    monkeypatch.setenv('PANEL_ADMIN_PASSWORD_HASH', encoded_hash(salt=b'another-real-generation'))
    assert identity.configuration_binding()[1] != digest
    monkeypatch.setenv('PANEL_ADMIN_USERNAME', 'another-admin')
    assert identity.configuration_binding()[0] == 'another-admin'


@pytest.mark.parametrize('value', [
    '', 'synthetic-not-a-hash', encoded_hash(rounds=199999), encoded_hash(rounds=800001),
    encoded_hash().replace('pbkdf2_sha256', 'argon2'), encoded_hash(salt=b''),
    encoded_hash(expected=b'short'), encoded_hash().replace('$c3lu', '$!3lu'),
    encoded_hash() + ' ', ' ' + encoded_hash(),
])
def test_malformed_configuration_never_leaks_the_environment_value(monkeypatch, value):
    monkeypatch.setenv('PANEL_ADMIN_PASSWORD_HASH', value)
    with pytest.raises(ValueError) as error:
        identity.configuration_binding()
    assert str(error.value) == 'invalid_legacy_admin_configuration'


@pytest.mark.parametrize('username', ['', '  ', 'bad\nadmin', 'x' * 255])
def test_native_username_requires_bounded_nonempty_server_identity(monkeypatch, username):
    monkeypatch.setenv('PANEL_ADMIN_USERNAME', username)
    monkeypatch.setenv('PANEL_ADMIN_PASSWORD_HASH', encoded_hash())
    with pytest.raises(ValueError, match='^invalid_legacy_admin_configuration$'):
        identity.configuration_binding()


async def test_disabled_native_identity_never_opens_storage(monkeypatch):
    monkeypatch.delenv('PANEL_LEGACY_ADMIN_IDENTITY_ENABLED', raising=False)
    monkeypatch.delenv('PANEL_ADMIN_PASSWORD_HASH', raising=False)
    monkeypatch.setattr(identity, '_storage', lambda: pytest.fail('Disabled identity touched storage'))
    assert identity.configured_digest() is None
    assert await identity.lookup_identity(None) is None
    assert await identity.login_identity() is None


async def test_lookup_is_readonly_and_requires_exact_provider_username_digest_and_uuid(configured):
    row = await identity.lookup_identity(configured.db, configured.row['id'])
    assert row['id'] == configured.row['id']
    query, parameters = configured.db.calls[-1]
    assert "role='admin'" in query and 'email IS NULL AND password_hash IS NULL' in query
    assert parameters['provider'] == 'legacy_admin'
    assert parameters['username'] == 'admin'
    assert parameters['digest'] == identity.configured_digest()
    assert await identity.lookup_identity(configured.db, uuid4()) is None
    before = len(configured.db.calls)
    assert await identity.lookup_identity(configured.db, 'invalid-uuid') is None
    assert len(configured.db.calls) == before


async def test_database_failure_is_reported_without_query_or_credentials(configured):
    configured.db.error = SQLAlchemyError('synthetic-sensitive-connection-details')
    with pytest.raises(HTTPException) as error:
        await identity.lookup_identity(configured.db)
    assert error.value.status_code == 503
    assert 'synthetic-sensitive' not in error.value.detail


async def login(client):
    return await client.post('/auth/login', json={'username': 'admin', 'password': 'synthetic-password'},
                             headers={'origin': 'https://test'})


async def test_only_fresh_authenticated_login_gains_the_native_identity(configured):
    old_token = 'A' * 43
    original = json.dumps({'role': 'support', 'csrf': 'old-synthetic-csrf'})
    configured.store.entries[panel_auth._hash_cookie(old_token)] = original
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=configured.app), base_url='https://test') as client:
        prior = await client.get('/auth/me', headers={'cookie': f'{panel_auth.COOKIE}={old_token}'})
        assert prior.json()['role'] == 'support' and prior.json()['account'] is False
        assert configured.db.calls == []
        signed = await login(client)
        assert signed.status_code == 200 and signed.json()['role'] == 'admin'
        token = client.cookies.get(panel_auth.COOKIE)
        claims = json.loads(configured.store.entries[panel_auth._hash_cookie(token)])
        assert claims['account_id'] == str(configured.row['id'])
        assert claims['auth_version'] == 4 and claims['auth_provider'] == 'legacy_admin'
        assert 'digest' not in json.dumps(claims) and encoded_hash() not in json.dumps(claims)
        for _ in range(2):
            current = await client.get('/auth/me')
            assert current.status_code == 200 and current.json()['role'] == 'admin'
            assert current.json()['account'] is True and current.json()['email'] is None
        assert len(configured.db.calls) == 3  # Fresh login plus live validation for each request.
        assert configured.store.entries[panel_auth._hash_cookie(old_token)] == original
        assert all(value in signed.headers['set-cookie'] for value in ('HttpOnly', 'Secure', 'SameSite=strict'))


@pytest.mark.parametrize('mode', ['disabled', 'not_provisioned'])
async def test_native_identity_is_opt_in_and_absent_row_keeps_the_support_login(configured, monkeypatch, mode):
    if mode == 'disabled':
        monkeypatch.setenv('PANEL_LEGACY_ADMIN_IDENTITY_ENABLED', '0')
    else:
        configured.db.row = None
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=configured.app), base_url='https://test') as client:
        signed = await login(client)
        assert signed.status_code == 200 and signed.json()['role'] == 'support'
        current = await client.get('/auth/me')
        assert current.json()['account'] is False
        claims = json.loads(configured.store.entries[panel_auth._hash_cookie(client.cookies.get(panel_auth.COOKIE))])
        assert 'account_id' not in claims and 'auth_provider' not in claims


async def test_failed_password_cannot_lookup_or_provision_native_identity(configured, monkeypatch):
    async def missing_account(*args): return None
    monkeypatch.setattr(account_auth, 'authenticate_account', missing_account)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=configured.app), base_url='https://test') as client:
        result = await client.post('/auth/login', json={'username': 'admin', 'password': 'wrong'})
    assert result.status_code == 401 and not configured.db.calls


async def test_bad_native_configuration_does_not_fall_back_to_database_password_login(configured, monkeypatch):
    monkeypatch.setenv('PANEL_ADMIN_PASSWORD_HASH', 'synthetic-sensitive-invalid-hash')
    monkeypatch.setattr(identity, '_storage', lambda: pytest.fail('Invalid configuration opened storage'))
    monkeypatch.setattr(account_auth, 'authenticate_account', lambda *_: pytest.fail('Unexpected password fallback'))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=configured.app), base_url='https://test') as client:
        result = await login(client)
    assert result.status_code == 503 and 'synthetic-sensitive' not in result.text


@pytest.mark.parametrize('change', ['disabled', 'version', 'password', 'invalid_config', 'username', 'provider', 'role', 'removed'])
async def test_native_sessions_revalidate_live_identity_and_never_fall_back(configured, monkeypatch, change):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=configured.app), base_url='https://test') as client:
        assert (await login(client)).status_code == 200
        monkeypatch.setattr(account_auth, 'current_account', lambda *_: pytest.fail('Native session used password account'))
        if change == 'disabled': monkeypatch.setenv('PANEL_LEGACY_ADMIN_IDENTITY_ENABLED', '0')
        elif change == 'version': configured.row['auth_version'] += 1
        elif change == 'password': monkeypatch.setenv('PANEL_ADMIN_PASSWORD_HASH', encoded_hash(salt=b'new-generation'))
        elif change == 'invalid_config': monkeypatch.setenv('PANEL_ADMIN_PASSWORD_HASH', 'synthetic-invalid')
        elif change == 'username': monkeypatch.setenv('PANEL_ADMIN_USERNAME', 'another-admin')
        elif change == 'provider': configured.row['auth_provider'] = 'password'
        elif change == 'role': configured.row['role'] = 'member'
        else: configured.db.row = None
        assert (await client.get('/auth/me')).status_code == 401


@pytest.mark.parametrize('claims', [
    {'role': 'support', 'auth_provider': 'legacy_admin'},
    {'role': 'admin', 'auth_provider': 'legacy_admin'},
    {'role': 'admin', 'auth_provider': 'unknown'},
])
async def test_incomplete_or_wrong_provider_claims_do_not_gain_native_identity(configured, claims):
    token = 'B' * 43
    configured.store.entries[panel_auth._hash_cookie(token)] = json.dumps({**claims, 'csrf': 'synthetic'})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=configured.app), base_url='https://test') as client:
        result = await client.get('/auth/me', headers={'cookie': f'{panel_auth.COOKIE}={token}'})
    assert result.status_code == 401 and configured.db.calls == []


async def test_native_account_cannot_be_used_through_the_generic_password_session(configured):
    token = 'C' * 43
    configured.store.entries[panel_auth._hash_cookie(token)] = json.dumps({'role': 'admin',
        'account_id': str(configured.row['id']), 'auth_version': 4, 'csrf': 'synthetic'})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=configured.app), base_url='https://test') as client:
        result = await client.get('/auth/me', headers={'cookie': f'{panel_auth.COOKIE}={token}'})
    assert result.status_code == 401
    assert "COALESCE(to_jsonb(panel_accounts)->>'auth_provider','password')='password'" in configured.db.calls[-1][0]


async def test_native_sessions_preserve_csrf_origin_and_logout(configured):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=configured.app), base_url='https://test') as client:
        signed = await login(client)
        csrf = signed.json()['csrf']
        assert (await client.post('/protected')).status_code == 403
        assert (await client.post('/protected', headers={'x-orion-csrf': csrf, 'origin': 'https://other.test'})).status_code == 403
        assert (await client.post('/protected', headers={'x-orion-csrf': csrf, 'origin': 'https://test'})).status_code == 200
        assert (await client.post('/auth/logout', headers={'x-orion-csrf': csrf})).status_code == 200
        assert (await client.get('/auth/me')).status_code == 401


@pytest.mark.parametrize('version', [None, '4', True, 0, -1])
async def test_native_claim_version_must_be_a_positive_integer(configured, version):
    with pytest.raises(HTTPException) as error:
        await identity.current_identity(configured.row['id'], version)
    assert error.value.status_code == 401 and not configured.db.calls
