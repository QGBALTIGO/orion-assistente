"""Real Redis reuse with real PostgreSQL account revocation; no production URL."""
import asyncio
import json
import os
import secrets
from urllib.parse import urlsplit

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from redis.asyncio import from_url
from sqlalchemy import text

from app import panel_auth
from tests.integration.test_onboarding_postgres import onboarding_db

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def session_case(monkeypatch):
    url = os.getenv("ORION_INTAKE_TEST_REDIS_URL")
    required = os.getenv("ORION_REQUIRE_INTAKE_REDIS_TEST") == "1"
    if not url:
        if required:
            pytest.fail("Explicit ephemeral session Redis required")
        pytest.skip("Explicit ephemeral Redis not configured")
    parsed = urlsplit(url)
    if (parsed.scheme != "redis" or parsed.hostname not in {"localhost", "127.0.0.1", "::1", "redis"}
            or parsed.path != "/15" or parsed.username or parsed.password or parsed.query or parsed.fragment):
        pytest.fail("Session test requires the designated local Redis database 15")
    store = from_url(url, decode_responses=True, socket_connect_timeout=2, socket_timeout=3, max_connections=100)
    try:
        await store.ping()
    except Exception:
        await store.aclose()
        if required:
            pytest.fail("Required ephemeral Redis unavailable")
        pytest.skip("Ephemeral Redis unavailable")
    app = FastAPI()
    app.include_router(panel_auth.router)
    monkeypatch.setattr(panel_auth, "_redis", lambda: store)
    await panel_auth.start_session_store(app)
    keys = []

    async def put(claims):
        token = secrets.token_urlsafe(32)
        key = panel_auth._hash_cookie(token)
        keys.append(key)
        await store.set(key, json.dumps(claims), ex=120)
        return {"cookie": f"{panel_auth.COOKIE}={token}"}, key

    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test") as client:
            yield store, client, put
    finally:
        if keys:
            await store.delete(*keys)  # Only random keys owned by this test, never FLUSHDB.
        await panel_auth.close_session_store(app)


@pytest.mark.asyncio
async def test_real_pool_reuses_tcp_connections_without_mixing_sessions(session_case):
    store, client, put = session_case
    sessions = [await put({"role": "support", "csrf": "synthetic-csrf-" + str(i)}) for i in range(50)]

    async def check(index):
        response = await client.get("/auth/me", headers=sessions[index][0])
        assert response.status_code == 200
        assert response.json()["csrf"] == "synthetic-csrf-" + str(index)

    await asyncio.gather(*(check(i) for i in range(50)))
    connections = (await store.info("stats"))["total_connections_received"]
    for _ in range(20):
        await check(0)
    assert (await store.info("stats"))["total_connections_received"] == connections
    await store.delete(sessions[0][1])
    assert (await client.get("/auth/me", headers=sessions[0][0])).status_code == 401
    await check(1)


@pytest.mark.asyncio
async def test_real_pool_checks_postgres_account_version_on_each_request(session_case, onboarding_db):
    store, client, put = session_case
    ctx = onboarding_db
    async with ctx.migration.connect() as db:
        version = (await db.execute(text("SELECT auth_version FROM panel_accounts WHERE id=:id"),
                                    {"id": ctx.actor.account_id})).scalar_one()
    headers, key = await put({"role": "member", "account_id": str(ctx.actor.account_id),
                              "auth_version": version, "csrf": "synthetic-csrf"})
    assert (await client.get("/auth/me", headers=headers)).status_code == 200
    async with ctx.migration.begin() as db:
        await db.execute(text("UPDATE panel_accounts SET auth_version=auth_version+1 WHERE id=:id"),
                         {"id": ctx.actor.account_id})
    assert await store.exists(key)  # Session still exists, but its account version was revoked.
    assert (await client.get("/auth/me", headers=headers)).status_code == 401
    assert (await client.get("/auth/me", headers=headers)).status_code == 401
