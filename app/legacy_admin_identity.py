"""Read-only binding to the real environment administrator, never provisioning.

The database holds a domain-separated credential-generation digest, not a copy
of the environment password hash. Only a fresh legacy login may gain identity.
"""
import base64
import hashlib
import os
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

PROVIDER = 'legacy_admin'
DIGEST_DOMAIN = b'orion:legacy-admin-identity:v1\0'


def enabled():
    return os.getenv('PANEL_LEGACY_ADMIN_IDENTITY_ENABLED', '0') == '1'


def configuration_binding():
    """Exact, validated server binding; usable by a separate explicit operator."""
    try:
        username = os.getenv('PANEL_ADMIN_USERNAME', 'admin').strip().lower()
        encoded = os.environ['PANEL_ADMIN_PASSWORD_HASH']
        if (not 1 <= len(username) <= 254 or any(ord(c) < 32 or ord(c) == 127 for c in username)
                or not 1 <= len(encoded) <= 4096 or encoded != encoded.strip()):
            raise ValueError()
        algorithm, rounds, salt, expected = encoded.split('$')
        if algorithm != 'pbkdf2_sha256' or not 200_000 <= int(rounds) <= 800_000:
            raise ValueError()
        decoded_salt = base64.b64decode(salt, altchars=b'-_', validate=True)
        decoded_expected = base64.b64decode(expected, altchars=b'-_', validate=True)
        if not decoded_salt or len(decoded_expected) != 32:
            raise ValueError()
    except (KeyError, ValueError, TypeError):
        raise ValueError('invalid_legacy_admin_configuration') from None
    digest = hashlib.sha256(DIGEST_DOMAIN + username.encode('utf-8') + b'\0' + encoded.encode('utf-8')).hexdigest()
    return username, digest


def configured_digest():
    """Trusted runtime context: absent opt-in must not authorize native sends."""
    return configuration_binding()[1] if enabled() else None


async def lookup_identity(db, account_id=None):
    """No writes; a role or a client-supplied UUID alone never proves identity."""
    if not enabled():
        return None
    username, digest = configuration_binding()
    try:
        identifier = UUID(str(account_id)) if account_id is not None else None
    except (ValueError, TypeError, AttributeError):
        return None
    try:
        row = (await db.execute(text('''SELECT id,name,role,auth_version,auth_provider
          FROM public.panel_accounts WHERE auth_provider=:provider AND role='admin'
            AND email IS NULL AND password_hash IS NULL
            AND legacy_username=:username AND legacy_credential_digest=:digest
            AND (CAST(:account AS uuid) IS NULL OR id=:account)'''),
            {'provider': PROVIDER, 'username': username, 'digest': digest, 'account': identifier})).mappings().one_or_none()
    except SQLAlchemyError:
        raise HTTPException(503, 'Identidade administrativa temporariamente indisponível') from None
    return dict(row) if row else None


def _storage():
    from app.catalog_api import support_session_factory
    return support_session_factory()


async def login_identity():
    """Called only after the ordinary environment password has been validated."""
    if not enabled():
        return None
    try:
        configuration_binding()
        async with _storage()() as db:
            row = await lookup_identity(db)
    except ValueError:
        raise HTTPException(503, 'Identidade administrativa ainda não configurada') from None
    if row is None:
        return None  # Preserve the ordinary support login until explicit setup.
    return {'id': str(row['id']), 'name': row['name'], 'email': None, 'role': 'admin',
            'version': row['auth_version'], 'auth_provider': PROVIDER}


async def current_identity(account_id, version):
    """Revalidate the credential generation and account on every native request."""
    if not enabled() or type(version) is not int or version < 1:
        raise HTTPException(401, 'Sua sessão expirou. Entre novamente')
    try:
        configuration_binding()
        async with _storage()() as db:
            row = await lookup_identity(db, account_id=account_id)
    except ValueError:
        raise HTTPException(401, 'Sua sessão expirou. Entre novamente') from None
    if row is None or row['auth_version'] != version:
        raise HTTPException(401, 'Sua sessão expirou. Entre novamente')
    return {**row, 'email': None}
