"""Legacy catalog access accepts only a fresh, server-verified route membership."""
from types import SimpleNamespace
from uuid import uuid4

from fastapi import HTTPException
import pytest

from app import catalog_api as catalog


@pytest.mark.parametrize("role", ["owner", "editor", "viewer"])
async def test_fresh_route_membership_limits_catalog_to_exact_tenant(monkeypatch, role):
    tenant, other, account = uuid4(), uuid4(), uuid4()
    calls = []
    class DB:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def execute(self, query, parameters):
            calls.append((str(query), parameters))
            return SimpleNamespace(scalar_one_or_none=lambda: role)
    monkeypatch.setenv("BUSINESS_PROFILES_ENABLED", "1")
    monkeypatch.setattr(catalog, "support_session_factory", lambda: DB)
    actor = await catalog.scoped_account_actor("member", account, str(tenant))
    assert calls[0][1] == {"account": account, "tenant": tenant}
    assert "AND active" in calls[0][0]
    catalog.authorize(actor, tenant)
    if role == "viewer":
        with pytest.raises(HTTPException): catalog.authorize(actor, tenant, write=True)
    else:
        catalog.authorize(actor, tenant, write=True)
    with pytest.raises(HTTPException): catalog.authorize(actor, other)
    with pytest.raises(HTTPException): catalog.support_only(actor)


@pytest.mark.parametrize("route,flag", [(None, "1"), ("bad", "1"), (str(uuid4()), "0")])
async def test_absent_route_or_disabled_profiles_never_load_membership(monkeypatch, route, flag):
    monkeypatch.setenv("BUSINESS_PROFILES_ENABLED", flag)
    monkeypatch.setattr(catalog, "support_session_factory", lambda: pytest.fail("No membership query allowed"))
    actor = await catalog.scoped_account_actor("member", uuid4(), route)
    assert actor.membership_role is None and actor.tenant_id is None
    with pytest.raises(HTTPException): catalog.authorize(actor, uuid4())


async def test_inactive_or_unknown_membership_never_grants_catalog_access(monkeypatch):
    tenant = uuid4()
    class DB:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def execute(self, *args):
            return SimpleNamespace(scalar_one_or_none=lambda: None)
    monkeypatch.setenv("BUSINESS_PROFILES_ENABLED", "1")
    monkeypatch.setattr(catalog, "support_session_factory", lambda: DB)
    actor = await catalog.scoped_account_actor("member", uuid4(), tenant)
    with pytest.raises(HTTPException): catalog.authorize(actor, tenant)


async def test_existing_admin_authority_does_not_depend_on_membership(monkeypatch):
    monkeypatch.setenv("BUSINESS_PROFILES_ENABLED", "1")
    monkeypatch.setattr(catalog, "support_session_factory", lambda: pytest.fail("Existing admin must not query membership"))
    actor = await catalog.scoped_account_actor("admin", uuid4(), uuid4())
    catalog.authorize(actor, uuid4(), write=True)


def test_member_with_route_id_but_no_verified_membership_is_not_a_legacy_viewer():
    tenant = uuid4()
    actor = catalog.Actor("member", tenant, uuid4())
    with pytest.raises(HTTPException): catalog.authorize(actor, tenant)
    catalog.authorize(catalog.Actor("viewer", tenant), tenant)


def test_catalog_editor_cannot_grant_staff_authority():
    tenant, account = uuid4(), uuid4()
    editor = catalog.Actor("member", tenant, account, "editor")
    catalog.authorize_resource(editor, tenant, "services", write=True)
    with pytest.raises(HTTPException):
        catalog.authorize_resource(editor, tenant, "authorized_staff", write=True)
    catalog.authorize_resource(catalog.Actor("member", tenant, account, "owner"), tenant,
                               "authorized_staff", write=True)
