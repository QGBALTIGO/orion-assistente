"""One live account/membership lookup is local to a request and its exact route."""
import json
import secrets
from types import SimpleNamespace
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Request
import httpx
import pytest

from app import account_auth, catalog_api, panel_auth
from tests.unit.test_panel_session_pool import Store


@pytest.mark.asyncio
async def test_live_scope_lookup_keeps_revocation_tenant_and_account_separation(monkeypatch):
    tenant, other, account, stranger = (uuid4() for _ in range(4))
    store, app, calls = Store(), FastAPI(), []
    live = {"membership": "owner", "version": 7, "role": "member"}
    monkeypatch.setenv("BUSINESS_PROFILES_ENABLED", "1")
    monkeypatch.setenv("SUPPORT_API_TOKEN", "synthetic-support-token-of-at-least-32-characters")
    monkeypatch.setattr(panel_auth, "_redis", lambda: store)
    monkeypatch.setattr(catalog_api, "support_session_factory", lambda: pytest.fail("No second membership query"))
    async def ordinary(*args):
        pytest.fail("Scoped HTTP authentication must use its single joined lookup")
    monkeypatch.setattr(account_auth, "current_account", ordinary)
    async def joined(identifier, version, route):
        calls.append((identifier, version, route))
        if version != live["version"]:
            raise HTTPException(401, "Sua sessão expirou. Entre novamente")
        return {"role": live["role"], "name": "Pessoa sintética", "email": "synthetic@example.test",
                "membership_role": live["membership"] if route == tenant and identifier == str(account) else None}
    monkeypatch.setattr(account_auth, "current_account_and_membership", joined)
    @app.api_route("/tenants/{tenant_id}/probe", methods=["GET", "POST"])
    async def probe(tenant_id, actor=Depends(catalog_api.authenticate)):
        from uuid import UUID
        catalog_api.authorize(actor, UUID(tenant_id))
        return {"role": actor.membership_role}
    tokens = [secrets.token_urlsafe(32) for _ in range(2)]
    for token, identifier in zip(tokens, (account, stranger)):
        store.entries[panel_auth._hash_cookie(token)] = json.dumps({"role": "member",
            "account_id": str(identifier), "auth_version": 7, "csrf": "synthetic-csrf"})
    headers = {"cookie": f"{panel_auth.COOKIE}={tokens[0]}"}
    path = f"/tenants/{tenant}/probe"
    await panel_auth.start_session_store(app)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test") as client:
            assert (await client.get(path, headers=headers)).json() == {"role": "owner"}
            assert (await client.get(f"/tenants/{other}/probe", headers=headers)).status_code == 403
            assert (await client.get(path, headers={"cookie": f"{panel_auth.COOKIE}={tokens[1]}"})).status_code == 403
            assert (await client.post(path, headers=headers)).status_code == 403
            assert (await client.post(path, headers={**headers, "x-orion-csrf": "synthetic-csrf",
                                                     "origin": "https://elsewhere.test"})).status_code == 403
            assert (await client.post(path, headers={**headers, "x-orion-csrf": "synthetic-csrf"})).status_code == 200
            live["membership"] = "viewer"
            assert (await client.get(path, headers=headers)).json() == {"role": "viewer"}
            live["membership"] = None
            assert (await client.get(path, headers=headers)).status_code == 403
            live["version"] = 8
            assert (await client.get(path, headers=headers)).status_code == 401
            live.update(version=7, role="admin")
            assert (await client.get(path, headers=headers)).status_code == 401
            assert len(calls) == 10 and len(store.reads) == 10
    finally:
        await panel_auth.close_session_store(app)


@pytest.mark.asyncio
@pytest.mark.parametrize("proof_kind", ["dict", "other_account", "other_tenant"])
async def test_unbound_or_json_shaped_proof_does_not_replace_live_membership(monkeypatch, proof_kind):
    account, tenant = uuid4(), uuid4()
    checked = (dict(role="owner", account_id=account, tenant_id=tenant) if proof_kind == "dict" else
               panel_auth.VerifiedRouteMembership(uuid4() if proof_kind == "other_account" else account,
                   uuid4() if proof_kind == "other_tenant" else tenant, "owner"))
    queries = []
    class DB:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def execute(self, query, parameters):
            queries.append(parameters)
            return SimpleNamespace(scalar_one_or_none=lambda: None)
    monkeypatch.setenv("BUSINESS_PROFILES_ENABLED", "1")
    monkeypatch.setattr(catalog_api, "support_session_factory", lambda: DB)
    actor = await catalog_api.scoped_account_actor("member", account, tenant, checked=checked)
    with pytest.raises(HTTPException):
        catalog_api.authorize(actor, tenant)
    assert queries == [{"account": account, "tenant": tenant}]


@pytest.mark.asyncio
async def test_a_prior_scope_proof_is_cleared_before_an_expired_session(monkeypatch):
    store = Store()
    monkeypatch.setattr(panel_auth, "_redis", lambda: store)
    scope = {"type": "http", "method": "GET", "headers": [],
             panel_auth.ROUTE_MEMBERSHIP: panel_auth.VerifiedRouteMembership(uuid4(), uuid4(), "owner")}
    with pytest.raises(HTTPException) as denied:
        await panel_auth.panel_actor(Request(scope))
    assert denied.value.status_code == 401 and panel_auth.ROUTE_MEMBERSHIP not in scope
