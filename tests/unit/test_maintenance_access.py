"""Short-lived maintenance never escalates client permissions or leaks credentials."""
import json
import time
from uuid import uuid4

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app import maintenance_access
from app.panel_auth import ROUTE_MEMBERSHIP, VerifiedRouteMembership


def request(path="/auth/me", *, tenant=None, method="GET"):
    scope = {"type": "http", "method": method, "path": path,
             "scheme": "https", "server": ("orion.test", 443), "headers": []}
    if tenant is not None:
        scope["path_params"] = {"tenant_id": str(tenant)}
    return Request(scope)


def record(actor, account, seconds=300):
    return json.dumps({
        "actor_id": str(actor), "account_id": str(account),
        "auth_version": 5, "company": "Barbearia sintética",
        "expires_at": int(time.time()) + seconds, "reason": "Suporte autorizado",
    })


@pytest.fixture
def admin(monkeypatch):
    monkeypatch.setenv("PANEL_OWNER_EMAIL", "dono@example.invalid")
    return {"role": "admin", "auth_provider": "legacy_admin",
            "account_id": str(uuid4()), "csrf": "csrf-test", "email_verified": False}


def test_platform_owner_and_cookie_binding(admin):
    assert maintenance_access._platform_owner(admin)
    assert maintenance_access._platform_owner({
        **admin, "auth_provider": "password",
        "email": "dono@example.invalid", "email_verified": True,
    })
    assert not maintenance_access._platform_owner({
        **admin, "auth_provider": "password", "email_verified": False,
    })
    assert not maintenance_access._platform_owner({**admin, "role": "member"})
    assert not maintenance_access._platform_owner({
        **admin, "auth_provider": "password", "email": "outro@example.invalid",
    })
    token = "x" * 43
    assert maintenance_access.maintenance_key(token) != maintenance_access.maintenance_guard_key(token)
    assert maintenance_access.maintenance_key(token) == maintenance_access.maintenance_key(token)
    with pytest.raises(HTTPException) as err:
        maintenance_access.maintenance_key("invalido")
    assert err.value.status_code == 401


@pytest.mark.asyncio
async def test_effective_scope_is_exact_member_and_actual_membership(admin, monkeypatch):
    account, tenant = uuid4(), uuid4()
    async def current(a, version, t):
        assert a == str(account) and version == 5 and t == tenant
        return {"role": "member", "auth_version": version,
                "registration_pending": False, "name": "Cliente sintético",
                "email": "sintetico@example.invalid", "verified_at": None,
                "membership_role": "viewer", "active_business_count": 1}
    monkeypatch.setattr(maintenance_access, "current_account_and_membership", current)
    req = request("/api/v1/tenants/" + str(tenant) + "/management/customers", tenant=tenant)
    claims = await maintenance_access.effective_maintenance_claims(
        req, admin, record(admin["account_id"], account))
    assert claims["role"] == "member"
    assert claims["account_id"] == str(account)
    assert claims["auth_provider"] == "password"
    assert claims["maintenance_actor_id"] == admin["account_id"]
    assert claims["maintenance"]["active"]
    assert claims["email_verified"] is False
    assert isinstance(req.scope[ROUTE_MEMBERSHIP], VerifiedRouteMembership)
    assert req.scope[ROUTE_MEMBERSHIP].role == "viewer"
    assert req.scope[ROUTE_MEMBERSHIP].tenant_id == tenant
    assert req.scope[ROUTE_MEMBERSHIP].account_id == account


@pytest.mark.asyncio
async def test_effective_scope_rejects_expired_wrong_actor_and_foreign_tenant(admin, monkeypatch):
    account, tenant = uuid4(), uuid4()
    async def foreign(a,version,t):
        return {"role": "member", "registration_pending": False,
                "verified_at": None, "name": "Teste", "email": "t@example.invalid",
                "membership_role": None, "active_business_count": 1}
    monkeypatch.setattr(maintenance_access, "current_account_and_membership", foreign)
    req = request("/api/v1/tenants/" + str(tenant), tenant=tenant)
    with pytest.raises(HTTPException) as stale:
        await maintenance_access.effective_maintenance_claims(
            req, admin, record(admin["account_id"], account, seconds=-1))
    assert stale.value.status_code == 401
    with pytest.raises(HTTPException) as wrong:
        await maintenance_access.effective_maintenance_claims(
            req, admin, record(uuid4(), account))
    assert wrong.value.status_code == 401
    with pytest.raises(HTTPException) as blocked:
        await maintenance_access.effective_maintenance_claims(
            req, admin, record(admin["account_id"], account))
    assert blocked.value.status_code == 403
    assert ROUTE_MEMBERSHIP not in req.scope


def test_maintenance_exit_preserves_owner_identity_without_querying_customer(admin):
    customer = uuid4()
    claims = maintenance_access.end_maintenance_claims(
        admin, record(admin["account_id"], customer))
    assert claims["role"] == "admin"
    assert claims["account_id"] == str(customer)
    assert claims["maintenance_actor_id"] == admin["account_id"]
    with pytest.raises(HTTPException):
        maintenance_access.end_maintenance_claims(
            {**admin, "role": "member"}, record(admin["account_id"], customer))


def test_maintenance_start_requires_reason_version_and_consent():
    with pytest.raises(Exception):
        maintenance_access.MaintenanceStart(expected_version=1,reason="curto",confirmation="ACESSAR")
    with pytest.raises(Exception):
        maintenance_access.MaintenanceStart(expected_version=0,reason="Manutenção autorizada",confirmation="ACESSAR")
    with pytest.raises(Exception):
        maintenance_access.MaintenanceStart(expected_version=1,reason="Manutenção autorizada",confirmation="OK")
    assert maintenance_access.MaintenanceStart(
        expected_version=2,reason="Manutenção autorizada",confirmation="ACESSAR").expected_version == 2


@pytest.mark.asyncio
async def test_password_change_is_forbidden_during_maintenance(admin):
    from app.account_auth import ChangeOwnPassword, change_own_password
    claims = {**admin, "role": "member", "auth_provider": "password",
              "maintenance": {"active": True}}
    data = ChangeOwnPassword(
        current_password="fake-password",
        new_password="new-fake-password",confirm_password="new-fake-password")
    with pytest.raises(HTTPException) as err:
        await change_own_password(data, request("/auth/account/password", method="POST"), claims)
    assert err.value.status_code == 403
