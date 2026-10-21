"""One active business per personal account, without changing legacy records.

All membership writers serialize on the same account lock before checking or
locking membership rows. Platform operators keep their internal privileges;
their ordinary panel still needs one explicitly resolved business context.
"""
import os
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text


MULTIPLE_BUSINESSES_MESSAGE = (
    "Sua conta está vinculada a mais de uma empresa. "
    "Entre em contato com o suporte para regularizar o vínculo."
)
SINGLE_BUSINESS_MESSAGE = "Cada conta pode ter apenas uma empresa. Sua conta já possui uma empresa vinculada."
INACTIVE_BUSINESS_MESSAGE = (
    "Sua conta já possui uma empresa cadastrada, mas o acesso está inativo. "
    "Entre em contato com o suporte para regularizar o vínculo."
)
OPERATOR_CONTEXT_MESSAGE = (
    "Este acesso administrativo não possui uma empresa definida. "
    "Entre com a conta da empresa para continuar."
)
_MULTIPLE = "__multiple_businesses__"


def reject_multiple_businesses():
    raise HTTPException(409, MULTIPLE_BUSINESSES_MESSAGE)


async def lock_account_business(db, account_id):
    """Call inside the writer's transaction, before locking membership rows."""
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:scope,0))"),
                     {"scope": "account-business:" + str(account_id)})


async def active_business_ids(db, account_id):
    return (await db.execute(text("""SELECT tenant_id FROM tenant_memberships
        WHERE account_id=:account AND active ORDER BY tenant_id LIMIT 2"""),
        {"account": account_id})).scalars().all()


async def ensure_membership_target(db, account_id, tenant_id):
    """The caller must hold lock_account_business until the write commits."""
    ids = await active_business_ids(db, account_id)
    if len(ids) > 1:
        reject_multiple_businesses()
    if ids and ids[0] != tenant_id:
        raise HTTPException(409, SINGLE_BUSINESS_MESSAGE)


async def has_business_history(db, account_id):
    # Revoking access cannot make a second registration available. Historical
    # staff memberships alone do not prevent a first business registration.
    return bool((await db.execute(text("""SELECT
        EXISTS(SELECT 1 FROM tenant_memberships WHERE account_id=:account AND role='owner')
        OR EXISTS(SELECT 1 FROM business_onboarding_creations WHERE account_id=:account)"""),
        {"account": account_id})).scalar_one())


async def ensure_can_create_business(db, account_id):
    """Use only for new requests; an identical creation replay is still valid."""
    ids = await active_business_ids(db, account_id)
    if len(ids) > 1:
        reject_multiple_businesses()
    if ids:
        raise HTTPException(409, SINGLE_BUSINESS_MESSAGE)
    if await has_business_history(db, account_id):
        raise HTTPException(409, INACTIVE_BUSINESS_MESSAGE)


async def account_membership_role(db, account_id, tenant_id, *, lock=False):
    # Keep this to one statement and take the original SHARE lock where the
    # caller must hold access through a write. A legacy conflict is never an
    # invitation to pick an arbitrary membership, including via a route UUID.
    locking = " FOR SHARE" if lock else ""
    role = (await db.execute(text("""SELECT CASE WHEN
        (SELECT count(*) FROM (SELECT 1 FROM tenant_memberships
            WHERE account_id=:account AND active LIMIT 2) active_businesses)>1
        THEN '__multiple_businesses__' ELSE
        (SELECT role FROM tenant_memberships
            WHERE account_id=:account AND tenant_id=:tenant AND active""" + locking + ") END"),
        {"account": account_id, "tenant": tenant_id})).scalar_one_or_none()
    if role == _MULTIPLE:
        reject_multiple_businesses()
    return role


async def panel_businesses(db, actor):
    """Return zero/one authorized context; never enumerate platform tenants."""
    account_id = getattr(actor, "account_id", None)
    if isinstance(account_id, UUID):
        rows = (await db.execute(text("""SELECT t.id,t.name,t.slug,t.status,t.timezone,m.role
            FROM tenant_memberships m JOIN tenants t ON t.id=m.tenant_id
            WHERE m.account_id=:account AND m.active ORDER BY t.id LIMIT 2"""),
            {"account": account_id})).mappings().all()
        if len(rows) > 1:
            reject_multiple_businesses()
        if rows:
            return rows
        if actor.role not in {"support", "admin"}:
            if await has_business_history(db, account_id):
                raise HTTPException(409, INACTIVE_BUSINESS_MESSAGE)
            return []
    elif actor.role == "viewer" and isinstance(getattr(actor, "tenant_id", None), UUID):
        row = (await db.execute(text("""SELECT id,name,slug,status,timezone,'viewer' AS role
            FROM tenants WHERE id=:tenant"""), {"tenant": actor.tenant_id})).mappings().first()
        if row is None:
            raise HTTPException(403, "Empresa não vinculada ao acesso")
        return [row]
    elif actor.role not in {"support", "admin"}:
        return []

    configured = os.getenv("PANEL_OPERATOR_TENANT_ID", "").strip()
    if configured:
        try:
            tenant_id = UUID(configured)
        except ValueError:
            raise HTTPException(409, OPERATOR_CONTEXT_MESSAGE) from None
        rows = (await db.execute(text("""SELECT id,name,slug,status,timezone,:role AS role
            FROM tenants WHERE id=:tenant"""), {"tenant": tenant_id, "role": actor.role})).mappings().all()
    else:
        rows = (await db.execute(text("""SELECT id,name,slug,status,timezone,:role AS role
            FROM tenants ORDER BY id LIMIT 2"""), {"role": actor.role})).mappings().all()
    if len(rows) != 1:
        raise HTTPException(409, OPERATOR_CONTEXT_MESSAGE)
    return rows
