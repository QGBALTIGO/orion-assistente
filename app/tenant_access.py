"""Resolve account membership on the server; a customer/model never selects authority."""
from dataclasses import dataclass
from contextlib import asynccontextmanager
import os
from uuid import UUID

from fastapi import HTTPException
from app.account_business import account_membership_role


@dataclass(frozen=True)
class TenantAccess:
    role: str
    account_id: UUID | None = None


def check_access(role: str | None, operation: str) -> None:
    allowed = {
        "read": {"owner", "editor", "viewer", "support", "admin"},
        "draft": {"owner", "editor", "support", "admin"},
        "publish": {"owner", "support", "admin"},
    }
    if operation not in allowed or role not in allowed[operation]:
        raise HTTPException(403, "Operação não autorizada para este estabelecimento")


async def resolve_tenant_access(actor, tenant_id: UUID, operation: str = "read") -> TenantAccess:
    account_id = getattr(actor, "account_id", None)
    if actor.role in {"support", "admin"}:
        check_access(actor.role, operation)
        return TenantAccess(actor.role, account_id)
    # The old API viewer remains read-only in its fixed, server-configured scope.
    if actor.role == "viewer" and getattr(actor, "tenant_id", None) == tenant_id:
        check_access("viewer", operation)
        return TenantAccess("viewer")
    if actor.role != "member" or not isinstance(account_id, UUID):
        raise HTTPException(403, "Conta sem acesso a este estabelecimento")
    if os.getenv("BUSINESS_PROFILES_ENABLED", "0") != "1":
        raise HTTPException(423, "O acesso por estabelecimento ainda não está habilitado")
    from app.catalog_api import support_session_factory
    async with support_session_factory()() as session:
        row = await account_membership_role(session, account_id, tenant_id)
    check_access(row, operation)
    return TenantAccess(row, account_id)


async def resolve_site_access(actor, tenant_id: UUID) -> TenantAccess:
    """Platform privileges are diagnostic, never publication authority.

    The same administrator may also own their one demonstration business. Only
    a current, explicit owner membership gives that identity publication rights.
    Other tenant APIs retain their existing support/admin permission contract.
    """
    access = await resolve_tenant_access(actor, tenant_id)
    if access.role in {"admin", "support"} and isinstance(access.account_id, UUID):
        from app.catalog_api import support_session_factory
        async with support_session_factory()() as db:
            membership = await account_membership_role(db, access.account_id, tenant_id)
        if membership == "owner":
            return TenantAccess("owner", access.account_id)
    return access


@asynccontextmanager
async def site_publication_access(actor, tenant_id: UUID):
    """Keep an actual owner membership valid through the publication commit."""
    access = await resolve_site_access(actor, tenant_id)
    if access.role == "owner" and isinstance(access.account_id, UUID):
        from app.catalog_api import support_session_factory
        async with support_session_factory()() as db:
            async with db.begin():
                if await account_membership_role(db, access.account_id, tenant_id, lock=True) != "owner":
                    raise HTTPException(403, "Seu acesso não permite publicar este site")
                yield access
    else:
        yield access
