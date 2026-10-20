"""Connection reuse must never turn session storage into an authorization cache."""
import json
import secrets

import httpx
import pytest
from fastapi import Depends, FastAPI, HTTPException, Request
from redis.exceptions import RedisError

from app import panel_auth


class Store:
    def __init__(self):
        self.entries = {}
        self.reads = []
        self.closed = 0
        self.unavailable = False

    async def get(self, key):
        self.reads.append(key)
        if self.unavailable:
            raise RedisError("synthetic outage")
        return self.entries.get(key)

    async def set(self, key, value, **options):
        self.entries[key] = value
        return True

    async def delete(self, key):
        self.entries.pop(key, None)

    async def aclose(self):
        self.closed += 1


def application():
    app = FastAPI()
    app.include_router(panel_auth.router)

    @app.post("/protected")
    async def protected(actor=Depends(panel_auth.panel_actor)):
        return {"role": actor["role"]}

    return app


@pytest.mark.asyncio
async def test_session_client_has_a_bounded_pool(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    store = panel_auth._redis()
    try:
        assert store.connection_pool.max_connections == 100
        assert store.connection_pool.connection_kwargs["decode_responses"] is True
        assert store.connection_pool.connection_kwargs["socket_timeout"] == 3
    finally:
        await store.aclose()


@pytest.mark.asyncio
async def test_pool_survives_requests_but_logout_and_csrf_remain_live(monkeypatch):
    store, app = Store(), application()
    created = []

    def factory():
        created.append(store)
        return store

    monkeypatch.setattr(panel_auth, "_redis", factory)
    monkeypatch.setattr(panel_auth, "_password_valid", lambda value: value == "synthetic-password")
    monkeypatch.setenv("PANEL_ADMIN_USERNAME", "admin")
    monkeypatch.setenv("PANEL_ADMIN_PASSWORD_HASH", "synthetic-configured")
    await panel_auth.start_session_store(app)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test") as client:
            signed = await client.post("/auth/login", json={"username": "admin", "password": "synthetic-password"})
            assert signed.status_code == 200
            csrf = signed.json()["csrf"]
            token = client.cookies.get(panel_auth.COOKIE)
            assert (await client.get("/auth/me")).status_code == 200
            assert (await client.post("/protected")).status_code == 403
            assert (await client.post("/protected", headers={"x-orion-csrf": csrf, "origin": "https://elsewhere.test"})).status_code == 403
            assert (await client.post("/protected", headers={"x-orion-csrf": csrf})).status_code == 200
            assert (await client.post("/auth/logout", headers={"x-orion-csrf": csrf})).status_code == 200
            assert (await client.get("/auth/me", headers={"cookie": f"{panel_auth.COOKIE}={token}"})).status_code == 401
            assert panel_auth._hash_cookie(token) not in store.entries
        assert len(created) == 1 and store.closed == 0
        assert len(store.reads) == 7  # login attempt counter plus each authenticated request
        with pytest.raises(RuntimeError, match="already started"):
            await panel_auth.start_session_store(app)
    finally:
        await panel_auth.close_session_store(app)
    assert store.closed == 1 and not hasattr(app.state, "panel_session_redis")
    await panel_auth.close_session_store(app)
    assert store.closed == 1


@pytest.mark.asyncio
async def test_shared_pool_does_not_keep_deleted_claims_or_survive_redis_outage_as_auth(monkeypatch):
    store, app = Store(), application()
    monkeypatch.setattr(panel_auth, "_redis", lambda: store)
    token = secrets.token_urlsafe(32)
    key = panel_auth._hash_cookie(token)
    store.entries[key] = json.dumps({"role": "support", "csrf": "synthetic-csrf"})
    await panel_auth.start_session_store(app)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test",
                                    headers={"cookie": f"{panel_auth.COOKIE}={token}"}) as client:
            assert (await client.get("/auth/me")).status_code == 200
            store.unavailable = True
            assert (await client.get("/auth/me")).status_code == 503
            assert store.closed == 0
            store.unavailable = False
            await store.delete(key)
            assert (await client.get("/auth/me")).status_code == 401
            assert store.reads == [key, key, key]
    finally:
        await panel_auth.close_session_store(app)


@pytest.mark.asyncio
async def test_no_lifespan_fallback_still_closes_its_owned_client(monkeypatch):
    store, app = Store(), application()
    monkeypatch.setattr(panel_auth, "_redis", lambda: store)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test") as client:
        result = await client.get("/auth/me", headers={"cookie": f"{panel_auth.COOKIE}=" + "A" * 43})
        assert result.status_code == 401
    assert store.closed == 1 and not hasattr(app.state, "panel_session_redis")


@pytest.mark.asyncio
async def test_direct_request_without_application_retains_owned_client_fallback(monkeypatch):
    store = Store()
    monkeypatch.setattr(panel_auth, "_redis", lambda: store)
    request = Request({"type": "http", "method": "GET",
                       "headers": [(b"cookie", (panel_auth.COOKIE + "=" + "A" * 43).encode())]})
    with pytest.raises(HTTPException) as error:
        await panel_auth.panel_actor(request)
    assert error.value.status_code == 401 and store.closed == 1


@pytest.mark.asyncio
async def test_actual_application_lifespan_owns_and_releases_the_session_client(monkeypatch):
    from app import main
    store = Store()
    calls = []

    async def probe():
        calls.append("provider-probe")

    monkeypatch.setattr(main, "probe_groq_provider", probe)
    monkeypatch.setattr(panel_auth, "_redis", lambda: store)
    assert not hasattr(main.app.state, "panel_session_redis")
    async with main.app.router.lifespan_context(main.app):
        assert main.app.state.panel_session_redis is store
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="https://test") as client:
            assert (await client.get("/health")).status_code == 200
            assert (await client.get("/auth/me", headers={"cookie": f"{panel_auth.COOKIE}=" + "A" * 43})).status_code == 401
        assert store.closed == 0 and len(store.reads) == 1
    assert calls == ["provider-probe"] and store.closed == 1
    assert not hasattr(main.app.state, "panel_session_redis")
