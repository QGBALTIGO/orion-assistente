"""Actual HTTP/auth/Redis/PG setup writes preserve the JSON-safe service contract."""
import json
import os
import secrets
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
import httpx
import pytest
import pytest_asyncio
from redis.asyncio import from_url
from sqlalchemy import text

from app import onboarding_api as api, onboarding_service as service
from app.panel_auth import COOKIE, _hash_cookie
from test_onboarding_postgres import onboarding_db  # noqa: F401

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def authenticated_http(onboarding_db, monkeypatch):
    ctx = onboarding_db
    url = os.getenv("REDIS_URL")
    if not url:
        if os.getenv("REQUIRE_REDIS_TESTS") == "1": pytest.fail("Real disposable Redis required")
        pytest.skip("Actual onboarding HTTP authentication requires Redis")
    cache = from_url(url, decode_responses=True)
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    key = _hash_cookie(token)
    async with ctx.migration.connect() as db:
        account = (await db.execute(text("SELECT role,auth_version FROM panel_accounts WHERE id=:a"),
            {"a": ctx.actor.account_id})).mappings().one()
    await cache.set(key, json.dumps({"role": account["role"], "auth_version": account["auth_version"],
        "account_id": str(ctx.actor.account_id), "csrf": csrf}), ex=300)
    monkeypatch.setenv("SUPPORT_API_TOKEN", "synthetic-http-only-" + "x" * 40)
    app = FastAPI(); app.include_router(api.router)
    # No dependency override or replacement of authentication, Redis or domain
    # services. The existing fixture supplies real role-separated DB factories.
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test",
        cookies={COOKIE: token}, headers={"Origin": "https://test", "x-orion-csrf": csrf}) as client:
        try: yield ctx, client, csrf
        finally:
            await cache.delete(key)
            await cache.aclose()


def same_response(response, content, status=200, *, exact_body=True):
    legacy = JSONResponse(jsonable_encoder(content), status_code=status)
    assert response.status_code == status
    if exact_body:
        assert response.content == legacy.body
    assert response.json() == jsonable_encoder(content)
    assert sorted(response.headers.raw) == sorted(legacy.raw_headers)


async def assert_current(ctx, client, tenant):
    current = await service.get_state(ctx.actor, tenant)
    same_response(await client.get(f"/api/v1/tenants/{tenant}/onboarding"), current)
    return current


@pytest.mark.asyncio
async def test_actual_http_create_all_steps_replay_and_complete_keep_state_and_database(authenticated_http):
    ctx, client, _csrf = authenticated_http
    payload = {"name": "Empresa JSON sintética", "timezone": "America/Cuiaba", "niche": "barbearia",
        "activity": "both", "idempotency_key": str(uuid4())}
    created = await client.post("/api/v1/onboarding/businesses", json=payload)
    assert created.status_code == 201
    tenant = UUID(created.json()["tenant_id"]); base = f"/api/v1/tenants/{tenant}/onboarding"
    state = await service.get_state(ctx.actor, tenant)
    same_response(created, state, 201)
    same_response(await client.post("/api/v1/onboarding/businesses", json=payload), state, 201)
    same_response(await client.get("/api/v1/onboarding/entry"), await service.entry(ctx.actor))

    async def save(step, data):
        nonlocal state
        body = {"expected_version": state["version"], "idempotency_key": str(uuid4()), "data": data}
        response = await client.put(base + "/steps/" + step, json=body)
        assert response.status_code == 200
        current = await service.get_state(ctx.actor, tenant)
        async with ctx.migration.connect() as db:
            result = (await db.execute(text("SELECT result FROM business_onboarding_operations WHERE tenant_id=:t AND idempotency_key=:k"),
                {"t": tenant, "k": body["idempotency_key"]})).scalar_one()
        # save_step adds result after _state's encoder: all seven real mutation
        # families must still return native JSON here, not Decimal/UUID objects.
        # JSONB does not preserve Python insertion order. Compare its content,
        # retaining strict bytes for unchanged service-to-HTTP states elsewhere.
        same_response(response, {**current, "result": result}, exact_body=False)
        replay = await client.put(base + "/steps/" + step, json=body)
        assert replay.json() == response.json() and replay.status_code == 200
        assert sorted(replay.headers.raw) == sorted(response.headers.raw)
        same_response(await client.get(base), current)
        state = current
        return result

    await save("business", {"name": "Empresa JSON revisada", "timezone": "America/Cuiaba", "niche": "barbearia", "activity": "both"})
    await save("location", {"modality": "online"})
    professional = (await save("professionals", {"item": {"name": "Pessoa sintética", "commission_percent": "12.50"}, "self_provider": True}))["id"]
    item = await save("catalog", {"kind": "service", "item": {"name": "Serviço sintético", "price_cents": 3500,
        "duration_min": 40, "prep_min": 5, "cleanup_min": 10}, "professional_ids": [professional]})
    product = await save("catalog", {"kind": "product", "item": {"name": "Produto sintético"},
        "variant": {"name": "Padrão", "price_kind": "on_request"}})
    await save("hours", {"professional_id": professional, "weekdays": [0, 2], "start_time": "09:00",
        "end_time": "18:00", "break_start": "12:00", "break_end": "13:00"})
    await save("channels", {"assistant_name": "Assistente sintética", "style_rules": {},
        "capabilities": ["catalog_answers"], "expected_profile_generation": state["channels"]["assistant"]["generation"]})
    await save("review", {"acknowledged": True})
    assert state["catalog"]["professionals"][0]["commission_percent"] == 12.5
    assert state["catalog"]["hours"][0]["start_time"] == "09:00:00"
    assert state["catalog"]["products"][0]["variants"][0]["unit_price_cents"] is None
    assert not state["pending"]
    stale = await client.put(base + "/steps/location", json={"expected_version": 0,
        "idempotency_key": str(uuid4()), "data": {"modality": "client_address"}})
    assert stale.status_code == 409 and stale.json() == {"detail": "A configuração mudou. Atualize antes de salvar"}
    assert await assert_current(ctx, client, tenant) == state
    finish = {"expected_version": state["version"], "expected_snapshot": state["snapshot"], "idempotency_key": str(uuid4()),
        "activate_catalog": True, "acknowledged": True}
    completed = await client.post(base + "/complete", json=finish)
    state = await service.get_state(ctx.actor, tenant)
    same_response(completed, state)
    same_response(await client.post(base + "/complete", json=finish), state)
    assert state["status"] == "complete" and not state["required"]
    async with ctx.migration.connect() as db:
        assert (await db.execute(text("SELECT count(*) FROM business_onboarding_creations WHERE tenant_id=:t"), {"t": tenant})).scalar_one() == 1
        assert (await db.execute(text("SELECT count(*) FROM tenant_memberships WHERE tenant_id=:t AND role='owner' AND active"), {"t": tenant})).scalar_one() == 1
        actual = (await db.execute(text("SELECT id,tenant_id,price_cents,duration_min,active FROM services WHERE id=:s"), {"s": UUID(item["id"])})).mappings().one()
        assert dict(actual) == {"id": UUID(item["id"]), "tenant_id": tenant, "price_cents": 3500, "duration_min": 40, "active": True}
        assert (await db.execute(text("SELECT id FROM commercial_variants WHERE tenant_id=:t AND id=:v AND product_id=:p"),
            {"t": tenant, "v": UUID(product["variant_id"]), "p": UUID(product["id"])})).scalar_one() == UUID(product["variant_id"])
        for table in ("appointments", "whatsapp_channels", "google_connections"):
            assert (await db.execute(text(f"SELECT count(*) FROM {table} WHERE tenant_id=:t"), {"t": tenant})).scalar_one() == 0


@pytest.mark.asyncio
async def test_actual_http_csrf_membership_and_foreign_scope_still_reject_without_setup_writes(authenticated_http):
    ctx, client, _csrf = authenticated_http
    base = f"/api/v1/tenants/{ctx.a}/onboarding"
    unchanged = await service.get_state(ctx.actor, ctx.a)
    body = {"expected_version": 0, "idempotency_key": str(uuid4()), "data": {"modality": "online"}}
    denied = await client.put(base + "/steps/location", json=body, headers={"x-orion-csrf": "wrong-synthetic"})
    assert denied.status_code == 403 and denied.json() == {"detail": "Confirmação de segurança ausente"}
    foreign = await client.get(f"/api/v1/tenants/{ctx.b}/onboarding")
    assert foreign.status_code == 403
    assert await assert_current(ctx, client, ctx.a) == unchanged
    async with ctx.migration.begin() as db:
        await db.execute(text("UPDATE tenant_memberships SET role='viewer' WHERE tenant_id=:t AND account_id=:a"),
            {"t": ctx.a, "a": ctx.actor.account_id})
    assert (await client.get(base)).status_code == 200
    assert (await client.put(base + "/steps/location", json=body)).status_code == 403
    async with ctx.migration.begin() as db:
        await db.execute(text("UPDATE tenant_memberships SET active=false WHERE tenant_id=:t AND account_id=:a"),
            {"t": ctx.a, "a": ctx.actor.account_id})
    assert (await client.get(base)).status_code == 403
    async with ctx.migration.connect() as db:
        for table in ("business_onboarding", "business_onboarding_operations"):
            assert (await db.execute(text(f"SELECT count(*) FROM {table} WHERE tenant_id IN (:a,:b)"), {"a": ctx.a, "b": ctx.b})).scalar_one() == 0
