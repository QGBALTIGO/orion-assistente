"""Recovery, admission envelopes and owner-first HTTP parsing."""
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.routing import APIRoute
import httpx
import pytest

from app import subscription_campaigns as api
from app import subscription_campaign_store as store
from app import subscription_campaign_schemas as schemas
from app.catalog_api import Actor, authenticate
from test_subscription_campaign_policy import Rows, definition

NOW = datetime(2026, 11, 4, 12, tzinfo=timezone.utc)
TENANT, OWNER, KEY, REQUEST, CAMPAIGN, DEVICE, GENERATION, ASSET = (uuid4() for _ in range(8))
ACTOR = Actor("member", account_id=OWNER)


def operation(**changes):
    return {"id": REQUEST, "tenant_id": TENANT, "actor_account_id": OWNER, "idempotency_key": KEY,
        "kind": "review", "status": "prepared", "campaign_id": CAMPAIGN, "group_id": None,
        "request_fingerprint": store.fingerprint({"expected_version": 1, "idempotency_key": str(KEY)}),
        "review_version": "a"*64, "result": None, "snapshot": {}, "preview": {"review_version": "a"*64},
        "created_at": NOW-timedelta(seconds=1), "expires_at": NOW+timedelta(seconds=119), "confirmed_at": None} | changes


class RequestDB:
    def __init__(self, row=None): self.row, self.calls = row, []
    async def execute(self, query, params=None):
        sql = str(query)
        self.calls.append((sql, params or {}))
        if sql.startswith("SELECT * FROM subscription_campaign_requests"):
            assert params["t"] == TENANT and params["key"] == KEY
            return Rows([self.row] if self.row else [])
        if sql == "SELECT clock_timestamp()": return Rows([NOW])
        raise AssertionError(sql)


def public_app():
    app = FastAPI()
    # The production include_plan_router mutates route.dependencies; mount
    # endpoints without inheriting another test's shared plan dependency.
    for route in api.router.routes:
        if route.path.startswith(api.BASE):
            app.add_api_route(route.path, route.endpoint, methods=route.methods, status_code=route.status_code)
    app.dependency_overrides[authenticate] = lambda: ACTOR
    return app


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix,method", [("", "POST"), ("/groups", "POST"),
    (f"/{CAMPAIGN}/review", "POST"), (f"/{CAMPAIGN}/confirm", "POST"), (f"/groups/{uuid4()}/permissions", "POST")])
async def test_ownership_is_checked_before_malformed_json_or_large_body(monkeypatch, suffix, method):
    @asynccontextmanager
    async def denied(*args):
        raise HTTPException(403, "owner required")
        yield
    monkeypatch.setattr(store, "owner_transaction", denied)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=public_app()), base_url="https://test") as client:
        result = await client.request(method, f"/api/v1/tenants/{TENANT}/subscription-whatsapp/campaigns"+suffix,
            content=b"not-json"*(schemas.MAX_BODY//8+1), headers={"content-type": "application/json"})
    assert result.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["missing", "prepared", "expired", "succeeded"])
async def test_get_recovery_is_readonly_and_negative_lookup_is_never_cached(monkeypatch, state):
    row = None if state == "missing" else operation()
    if state == "expired": row["expires_at"] = NOW
    if state == "succeeded": row.update(status="succeeded", result={"campaign": {"id": str(CAMPAIGN)}}, confirmed_at=NOW)
    db = RequestDB(row)
    before = deepcopy(row)
    @asynccontextmanager
    async def boundary(tenant, actor):
        assert tenant == TENANT and actor == ACTOR
        yield db
    monkeypatch.setattr(store, "owner_transaction", boundary)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=public_app()), base_url="https://test") as client:
        result = await client.get(f"/api/v1/tenants/{TENANT}/subscription-whatsapp/campaigns/requests/{KEY}")
    assert result.headers["cache-control"] == "no-store"
    assert result.status_code == (404 if state == "missing" else 200)
    if row:
        assert result.json()["status"] == state
        assert result.json()["replayed"] is True
        assert result.json()["preview"] == (row["preview"] if state == "prepared" else None)
    assert row == before and all(sql.startswith("SELECT") for sql, _ in db.calls)


@pytest.mark.asyncio
async def test_existing_crud_fingerprint_prevents_corrected_body_from_overwriting_an_admitted_request():
    body = {"idempotency_key": str(KEY), "campaign": definition()}
    row = operation(kind="create", status="succeeded", request_fingerprint=store.fingerprint(body))
    db = RequestDB(row)
    assert await store.existing_request(db, TENANT, ACTOR, KEY, "create", body) == row
    with pytest.raises(HTTPException) as error:
        await store.existing_request(db, TENANT, ACTOR, KEY, "create", body | {"campaign": definition(title="Alterado")})
    assert error.value.detail["code"] == "CAMPAIGN_IDEMPOTENCY_CONFLICT"
    assert await store.request_row(db, TENANT, ACTOR, KEY) == row
    assert all(sql.startswith("SELECT") for sql, _ in db.calls)


@pytest.mark.asyncio
async def test_request_recovery_cannot_transfer_to_another_current_owner():
    db = RequestDB(operation(actor_account_id=uuid4()))
    with pytest.raises(HTTPException) as error:
        await store.request_row(db, TENANT, ACTOR, KEY)
    assert error.value.status_code == 404 and error.value.detail["code"] == "CAMPAIGN_NOT_FOUND"


@pytest.mark.asyncio
async def test_confirm_replay_returns_immutable_result_even_when_context_or_expiry_changed(monkeypatch):
    row = operation(status="succeeded", result={"campaign": {"id": str(CAMPAIGN), "version": 2}},
        expires_at=NOW-timedelta(days=1), confirmed_at=NOW-timedelta(days=1))
    db = RequestDB(row)
    @asynccontextmanager
    async def boundary(*args): yield db
    monkeypatch.setattr(store, "owner_transaction", boundary)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=public_app()), base_url="https://test") as client:
        result = await client.post(f"/api/v1/tenants/{TENANT}/subscription-whatsapp/campaigns/{CAMPAIGN}/confirm", json={
            "idempotency_key": str(KEY), "review_version": "a"*64, "confirmation": "AGENDAR", "consent_confirmed": True})
    assert result.status_code == 202 and result.json()["result"] == row["result"]
    assert result.json()["preview"] is None and result.json()["replayed"]
    assert all(sql.startswith("SELECT") for sql, _ in db.calls)


@pytest.mark.asyncio
async def test_expired_confirmation_never_rebuilds_or_writes_an_audience(monkeypatch):
    db = RequestDB(operation(expires_at=NOW))
    @asynccontextmanager
    async def boundary(*args): yield db
    monkeypatch.setattr(store, "owner_transaction", boundary)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=public_app()), base_url="https://test") as client:
        result = await client.post(f"/api/v1/tenants/{TENANT}/subscription-whatsapp/campaigns/{CAMPAIGN}/confirm", json={
            "idempotency_key": str(KEY), "review_version": "a"*64, "confirmation": "AGENDAR", "consent_confirmed": True})
    assert result.status_code == 410 and result.json()["detail"]["code"] == "CAMPAIGN_REVIEW_EXPIRED"
    assert all(sql.startswith("SELECT") for sql, _ in db.calls)


@pytest.mark.asyncio
async def test_confirmed_detail_preserves_the_complete_reviewed_group_contract(monkeypatch):
    frozen = [{"id": str(uuid4()), "name": "Nome confirmado", "version": 3,
               "member_count": 7, "blocked_count": 2, "archived": False}]
    class DetailDB:
        async def execute(self, query, params=None):
            sql = str(query)
            if sql.startswith("SELECT * FROM subscription_campaigns"):
                return Rows([{"id": CAMPAIGN, "tenant_id": TENANT, "run_version": "a"*64, "snapshot": {"groups": deepcopy(frozen)}}])
            assert sql == "SELECT clock_timestamp()"  # No current-group lookup or historical-group mutation.
            return Rows([NOW])
    @asynccontextmanager
    async def boundary(*args): yield DetailDB()
    async def campaign_dto(*args): return {"id": str(CAMPAIGN), "progress": {"total": 7}}
    async def recipients(*args): return {"items": [], "total": 0}
    monkeypatch.setattr(store, "owner_transaction", boundary)
    monkeypatch.setattr(store, "campaign_dto", campaign_dto)
    monkeypatch.setattr(store, "recipient_page", recipients)
    response = Response()
    result = await api.campaign_detail(TENANT, CAMPAIGN, response, ACTOR)
    assert result["groups"] == frozen
    assert set(result["groups"][0]) == {"id", "name", "version", "member_count", "blocked_count", "archived"}
    assert response.headers["cache-control"] == "no-store"


def asset_request(**changes):
    headers = {"x-orion-device-id": str(DEVICE), "x-orion-device-generation": str(GENERATION),
        "x-orion-device-revision": "1", "x-orion-device-effect": str(REQUEST)} | changes
    return Request({"type": "http", "method": "GET", "path": "/", "headers": [(k.encode(), v.encode()) for k, v in headers.items()]})


@pytest.mark.parametrize("change", [{"x-orion-device-id": str(uuid4())}, {"x-orion-device-generation": str(uuid4())},
    {"x-orion-device-revision": "2"}, {"x-orion-device-effect": str(uuid4())}, {"x-orion-device-revision": "not-an-int"}])
def test_private_media_requires_identical_query_and_header_effect_scope(change):
    with pytest.raises(HTTPException) as error:
        api.asset_header_scope(asset_request(**change), DEVICE, GENERATION, 1, REQUEST)
    assert error.value.status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("revoked", [False, True])
async def test_private_asset_read_reauthorizes_after_storage_without_new_claim_or_send(monkeypatch, revoked):
    from app import subscription_chatbot_media as media
    checks, reads = [], []
    async def authorized(request, asset_id, scope, variant):
        checks.append((asset_id, scope, variant))
        if len(checks) == 2 and revoked:
            raise store.fail("CAMPAIGN_PAUSED")
        return TENANT, {"size": 4, "sha256": "b"*64, "mime": "image/jpeg"}
    def get_media(tenant, identity, **kwargs):
        reads.append((tenant, identity, kwargs))
        return b"test"
    monkeypatch.setattr(api, "authorized_asset", authorized)
    monkeypatch.setattr(media, "get_media", get_media)
    if revoked:
        with pytest.raises(HTTPException) as error:
            await api.campaign_asset(uuid4(), ASSET, asset_request(), DEVICE, GENERATION, REQUEST, 1, "original")
        assert error.value.detail["code"] == "CAMPAIGN_PAUSED"
    else:
        result = await api.campaign_asset(uuid4(), ASSET, asset_request(), DEVICE, GENERATION, REQUEST, 1, "original")
        assert result.body == b"test" and result.headers["cache-control"] == "no-store"
    assert len(checks) == 2 and checks[0] == checks[1]
    assert reads == [(TENANT, ASSET, {"expected_size": 4, "expected_sha256": "b"*64, "variant": "original"})]
