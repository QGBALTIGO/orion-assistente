"""Stable registry alias for the existing socket; never copies session material."""
import os
import secrets
from uuid import UUID, NAMESPACE_URL, uuid5
from fastapi import HTTPException, Request
from sqlalchemy import text


def channel_id(tenant):
    return uuid5(NAMESPACE_URL, 'orion:preserved-primary-qr:' + str(UUID(str(tenant))))


def is_alias(tenant, channel):
    return channel_id(tenant) == channel


def configured_tenant():
    try:
        return UUID(os.environ['QR_AI_TENANT_ID'])
    except (ValueError, KeyError):
        raise HTTPException(503, 'Sessão principal ainda não vinculada à central') from None


def enabled():
    return os.getenv('QR_LEGACY_CENTRAL_ENABLED', '0') == '1'


def authenticate(request):
    key = os.getenv('QR_BRIDGE_TOKEN', '')
    supplied = request.headers.get('x-orion-bridge-key', '')
    if len(key) < 32 or not secrets.compare_digest(key, supplied):
        raise HTTPException(401, 'Acesso negado')


async def adopted(db, tenant, channel):
    if not is_alias(tenant, channel):
        return False
    return (await db.execute(text("""SELECT EXISTS(SELECT 1 FROM inbox_channel_links
        WHERE tenant_id=:t AND channel_id=:c AND provider='qr')"""), {'t': tenant, 'c': channel})).scalar_one()


async def registry_adopted(tenant, channel):
    from app.database import get_session_factory, tenant_transaction
    async with get_session_factory()() as db:
        async with tenant_transaction(db, tenant):
            return await adopted(db, tenant, channel)


async def require_managed_path():
    """A persisted adoption cannot be bypassed by an old process/env flag."""
    if not enabled():
        return False
    tenant = configured_tenant()
    return await registry_adopted(tenant, channel_id(tenant))


async def bootstrap(request: Request):
    authenticate(request)
    if not enabled():
        raise HTTPException(423, 'Transição da sessão principal não habilitada')
    from app.catalog_api import support_session_factory
    from app.qr_channels import credential_hash
    tenant = configured_tenant()
    async with support_session_factory()() as db:
        row = (await db.execute(text("""SELECT c.id,c.tenant_id,c.enabled,c.assistant_enabled,c.allowed_senders,c.request_fingerprint
          FROM qr_channels c JOIN tenant_memberships m ON m.tenant_id=c.tenant_id AND m.account_id=c.owner_account_id
          JOIN tenants t ON t.id=c.tenant_id WHERE c.id=:c AND c.tenant_id=:t AND c.credential_hash=:key
          AND c.enabled AND t.status='active' AND m.active AND m.role='owner'"""),
          {'c': channel_id(tenant), 't': tenant, 'key': credential_hash(os.environ['QR_BRIDGE_TOKEN'])})).mappings().one_or_none()
    if row is None:
        raise HTTPException(409, 'Preparação da sessão principal pendente')
    return {'channel_id': str(row['id']), 'tenant_id': str(tenant),
        'legacy_transport': True, 'allowed_senders': row['allowed_senders'], 'session_digest':row['request_fingerprint']}
