"""Mocked provider contract; automated tests never create public links."""
from dataclasses import replace
from uuid import UUID

from fastapi import FastAPI, HTTPException
import httpx
import pytest

from app import affiliate_shortener as links
from app.catalog_api import Actor, authenticate

TENANT = UUID("10000000-0000-4000-8000-000000000001")
OTHER = UUID("10000000-0000-4000-8000-000000000002")
OWNER = Actor("member", TENANT, UUID("20000000-0000-4000-8000-000000000001"), "owner")
KEY = "spoo_test_example_only_123456789"
ID = "507f1f77bcf86cd799439011"
URL = "https://example.com/path?q=1&ref=synthetic#anchor"

@pytest.fixture
def ctx(monkeypatch):
    from app import affiliate_workspace
    state = {"actor": OWNER, "calls": [], "data": {"alias": "teste", "short_url": "https://spoo.me/teste", "long_url": URL, "owner_id": ID}}
    monkeypatch.setenv("ORION_AFFILIATE_SHORTENER_ENABLED", "1")
    async def owner(actor, tenant_id):
        if actor != OWNER or tenant_id != TENANT:
            raise HTTPException(403)
    async def limit(tenant_id):
        assert tenant_id == TENANT
    async def provider(method, path, key, **kwargs):
        state["calls"].append((method, path, key, kwargs))
        return state["data"]
    monkeypatch.setattr(affiliate_workspace, "affiliate_owner", owner)
    monkeypatch.setattr(links, "rate_limit", limit)
    monkeypatch.setattr(links, "provider", provider)
    app = FastAPI()
    app.include_router(affiliate_workspace.router, prefix="/api/v1")
    app.dependency_overrides[authenticate] = lambda: state["actor"]
    state["app"] = app
    return state

async def post(ctx, action, payload=None, tenant=TENANT):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=ctx["app"]), base_url="https://orion.test") as client:
        return await client.post(f"/api/v1/afiliados/{tenant}/api/links/{action}", json={"api_key": KEY, **(payload or {})})

@pytest.mark.asyncio
async def test_create_preserves_destination_and_private_stats(ctx):
    response = await post(ctx, "create", {"url": URL, "alias": "teste", "accepted": True})
    assert response.status_code == 200
    assert response.json()["short_url"] == "https://spoo.me/teste"
    assert response.headers["cache-control"] == "no-store"
    assert KEY not in response.text
    assert ctx["calls"] == [("POST", "/shorten", KEY, {"payload": {"long_url": URL, "alias": "teste", "private_stats": True}, "expected": 201})]

@pytest.mark.asyncio
async def test_auto_alias(ctx):
    response = await post(ctx, "create", {"url": URL, "accepted": True})
    assert response.status_code == 200
    assert "alias" not in ctx["calls"][0][3]["payload"]

@pytest.mark.parametrize("url", ["javascript:alert(1)", "file:///etc/passwd", "https://localhost/a",
    "http://127.0.0.1/a", "https://host.local/a", "http://[::1]/", "https://foo@example.com/a",
    "https://example.com:1234/a", "https://spoo.me/foo", "https://mercadolivre.com.br/oferta",
    "https://example.com/a b", "https://example.com\\@private.test/a"])
@pytest.mark.asyncio
async def test_unsafe_destinations(ctx, url):
    assert (await post(ctx, "create", {"url": url, "accepted": True})).status_code == 422
    assert ctx["calls"] == []

@pytest.mark.parametrize("alias", ["ab", "a b", "a/b", "ábc", "a"*17])
@pytest.mark.asyncio
async def test_invalid_alias(ctx, alias):
    assert (await post(ctx, "create", {"url": URL, "alias": alias, "accepted": True})).status_code == 422
    assert ctx["calls"] == []

@pytest.mark.asyncio
async def test_opt_in_and_feature_flag(ctx, monkeypatch):
    assert (await post(ctx, "create", {"url": URL})).status_code == 422
    monkeypatch.delenv("ORION_AFFILIATE_SHORTENER_ENABLED")
    assert (await post(ctx, "list")).status_code == 503
    assert ctx["calls"] == []

@pytest.mark.parametrize("actor,tenant", [(replace(OWNER, membership_role="editor"), TENANT),
    (replace(OWNER, maintenance=True), TENANT), (Actor("support"), TENANT), (OWNER, OTHER)])
@pytest.mark.asyncio
async def test_tenant_and_owner_scope(ctx, actor, tenant):
    ctx["actor"] = actor
    assert (await post(ctx, "list", tenant=tenant)).status_code == 403
    assert ctx["calls"] == []

@pytest.mark.asyncio
async def test_unconfirmed_provider_response(ctx):
    ctx["data"]["short_url"] = "https://orionagenda.pro/teste"
    response = await post(ctx, "create", {"url": URL, "accepted": True})
    assert response.status_code == 502
    assert "orionagenda.pro" not in response.text

@pytest.mark.asyncio
async def test_history_and_real_counts(ctx):
    ctx["data"] = {"page": 1, "hasNext": False, "total": 2, "items": [
        {"id": ID, "alias": "teste", "long_url": URL, "status": "ACTIVE", "total_clicks": 0},
        {"id": ID, "alias": "outra", "long_url": URL, "domain": "other.example"}]}
    data = (await post(ctx, "list", {"search": "teste"})).json()
    assert data["items"][0]["total_clicks"] == 0
    assert data["omitted"] == 1
    assert data["total"] == 2
    ctx["data"] = {"page": 1, "hasNext": False, "total": 1, "items": [{"id": ID, "alias": "teste", "long_url": URL}]}
    assert (await post(ctx, "list")).json()["items"][0]["total_clicks"] is None

@pytest.mark.asyncio
async def test_stats_and_management(ctx):
    ctx["data"] = {"url_id": ID, "summary": {"total_clicks": 3, "unique_clicks": 2}}
    assert (await post(ctx, "stats", {"id": ID})).json() == {"total_clicks": 3, "unique_clicks": 2, "period": "7d"}
    ctx["data"] = {"id": ID, "status": "INACTIVE"}
    assert (await post(ctx, "status", {"id": ID, "status": "INACTIVE"})).status_code == 200
    ctx["data"] = {"id": ID, "message": "URL deleted"}
    assert (await post(ctx, "delete", {"id": ID})).json()["deleted"] is True
    assert (await post(ctx, "delete", {"id": "../evil"})).status_code == 422

@pytest.mark.parametrize("status,expected", [(401,401),(403,403),(404,404),(409,409),(429,429),(500,502),(302,502)])
@pytest.mark.asyncio
async def test_provider_errors_and_no_redirect(monkeypatch, status, expected):
    calls = []
    real_client = httpx.AsyncClient
    async def handler(request):
        calls.append(request)
        assert request.url.host == "spoo.me"
        assert request.headers["authorization"] == "Bearer " + KEY
        return httpx.Response(status, json={"detail": KEY}, headers={"location": "https://untrusted.example"})
    monkeypatch.setattr(links.httpx, "AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    with pytest.raises(HTTPException) as exc:
        await links.provider("POST", "/shorten", KEY, payload={"long_url": URL}, expected=201)
    assert exc.value.status_code == expected
    assert KEY not in str(exc.value.detail)
    assert len(calls) == 1

@pytest.mark.asyncio
async def test_missing_redis_fails_closed(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    with pytest.raises(HTTPException) as exc:
        await links.rate_limit(TENANT)
    assert exc.value.status_code == 503
