"""Publication policy and infrastructure failures without external traffic."""
from types import SimpleNamespace
from uuid import uuid4

from fastapi import HTTPException
import pytest
from pydantic import ValidationError

from app.management_schemas import SiteSave
from app.public_site_api import Publication
from app.site_publication import validate_slug
from app.tenant_access import TenantAccess, resolve_site_access


@pytest.mark.parametrize("value", ["admin", "api", "panel", "internal", "login", "webhooks"])
def test_reserved_address_is_rejected_by_draft_and_publication(value):
    with pytest.raises(HTTPException) as exc:
        validate_slug(value)
    assert exc.value.status_code == 422
    with pytest.raises(ValidationError):
        SiteSave(title="Empresa sintética", slug=value, expected_version=0)


@pytest.mark.parametrize("value", [None, "x", "UPPERCASE", "../site", "test_name", "x" * 81])
def test_invalid_public_address_cannot_be_reserved(value):
    with pytest.raises(HTTPException):
        validate_slug(value)


def test_restore_contract_is_explicit_strict_and_backwards_compatible():
    assert Publication(expected_version=1, enabled=True).restore_previous is False
    assert Publication(expected_version=1, enabled=True, restore_previous=True).restore_previous is True
    with pytest.raises(ValidationError):
        Publication(expected_version=1, enabled=True, restore_previous="true")


@pytest.mark.asyncio
async def test_platform_identity_without_explicit_owner_membership_does_not_gain_publication(monkeypatch):
    async def diagnostic(*args):
        return TenantAccess("support")
    monkeypatch.setattr("app.tenant_access.resolve_tenant_access", diagnostic)
    result = await resolve_site_access(SimpleNamespace(role="support"), uuid4())
    assert result.role == "support" and result.account_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize("membership,expected", [("owner", "owner"), ("editor", "admin"), (None, "admin")])
async def test_administrator_can_publish_only_as_explicit_current_owner(monkeypatch, membership, expected):
    account, tenant = uuid4(), uuid4()
    async def diagnostic(*args):
        return TenantAccess("admin", account)
    class Session:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
    async def role(db, requested_account, requested_tenant):
        assert requested_account == account and requested_tenant == tenant
        return membership
    monkeypatch.setattr("app.tenant_access.resolve_tenant_access", diagnostic)
    monkeypatch.setattr("app.catalog_api.support_session_factory", lambda: Session)
    monkeypatch.setattr("app.tenant_access.account_membership_role", role)
    assert (await resolve_site_access(SimpleNamespace(role="admin", account_id=account), tenant)).role == expected
