"""Real admin password -> Redis cookie -> current identity -> owner publication.

Only isolated PostgreSQL/Redis and synthetic credentials are used. No Actor
override, account provisioning, provider operation or real message is involved.
"""
import base64
import hashlib
import json
import os

from fastapi import Request
import httpx
import pytest
import pytest_asyncio
from redis.asyncio import Redis
from sqlalchemy import text

from app import legacy_admin_identity, panel_auth
from app.catalog_api import authenticate
from test_public_catalog import public_db

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def native_publication(public_db, monkeypatch):
    d = public_db
    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        if os.getenv("REQUIRE_REDIS_TESTS") == "1":
            pytest.fail("Native administrator publication requires isolated Redis")
        pytest.skip("Requires isolated Redis")
    password = "entirely-synthetic-native-administrator-password"
    salt = b"synthetic-site-publication-admin"
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)
    encoded = "pbkdf2_sha256$200000$" + base64.urlsafe_b64encode(salt).decode() + "$" + base64.urlsafe_b64encode(digest).decode()
    monkeypatch.setenv("PANEL_ADMIN_USERNAME", "synthetic-publication-admin")
    monkeypatch.setenv("PANEL_ADMIN_PASSWORD_HASH", encoded)
    monkeypatch.setenv("PANEL_LEGACY_ADMIN_IDENTITY_ENABLED", "0")
    monkeypatch.setattr(panel_auth, "_redis", lambda: Redis.from_url(redis_url, socket_connect_timeout=3, socket_timeout=3))
    d["app"].dependency_overrides.pop(authenticate)
    d["app"].include_router(panel_auth.router)
    sessions = []
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=d["app"], client=("192.0.2.52", 43210)), base_url="https://test") as client:
        async def login():
            response = await client.post("/auth/login", headers={"Origin": "https://test"}, json={
                "username": "synthetic-publication-admin", "password": password})
            assert response.status_code == 200, response.text
            token = client.cookies.get(panel_auth.COOKIE)
            sessions.append(token)
            return response.json(), token
        async def bind(*, matching=True):
            monkeypatch.setenv("PANEL_LEGACY_ADMIN_IDENTITY_ENABLED", "1")
            username, credential_digest = legacy_admin_identity.configuration_binding()
            async with d["migrator"].begin() as db:
                await db.execute(text("""UPDATE panel_accounts SET role='admin',email=NULL,password_hash=NULL,
                    auth_provider='legacy_admin',legacy_username=:username,legacy_credential_digest=:digest
                    WHERE id=:id"""), {"username": username, "digest": credential_digest if matching else "a" * 64, "id": d["owner"]})
        cache = Redis.from_url(redis_url, socket_connect_timeout=3, socket_timeout=3)
        try:
            yield {**d, "native_client": client, "native_login": login, "bind_native": bind, "native_cache": cache}
        finally:
            for token in sessions:
                await cache.delete(panel_auth._hash_cookie(token))
            await cache.aclose()


async def save_native_draft(d, csrf):
    result = await d["native_client"].put(d["base"] + "/site", headers={"Origin": "https://test", "X-Orion-CSRF": csrf},
        json={"expected_version": 0, "title": "Demonstração nativa sintética", "slug": d["slug"]})
    assert result.status_code == 200, result.text
    return result.json()


@pytest.mark.asyncio
async def test_old_support_cookie_requires_fresh_native_login_then_owner_publishes(native_publication):
    d = native_publication; c = d["native_client"]
    old_login, old_token = await d["native_login"]()
    assert old_login["role"] == "support"
    old_key = panel_auth._hash_cookie(old_token)
    original_claims = await d["native_cache"].get(old_key)
    assert "account_id" not in json.loads(original_claims)
    draft = await save_native_draft(d, old_login["csrf"])
    await d["bind_native"]()
    old_status = await c.get(d["base"] + "/site/publication")
    assert old_status.status_code == 200, old_status.text
    assert old_status.json()["ready"] is True and old_status.json()["can_publish"] is False
    assert old_status.json()["reauthentication_required"] is True
    assert old_status.json()["access_reason"] == "administrator_reauthentication_required"
    denied = await c.post(d["base"] + "/site/publication", headers={"Origin": "https://test", "X-Orion-CSRF": old_login["csrf"]},
                          json={"expected_version": draft["version"], "enabled": True})
    assert denied.status_code == 403
    assert await d["native_cache"].get(old_key) == original_claims
    fresh_login, fresh_token = await d["native_login"]()
    assert fresh_token != old_token and fresh_login["role"] == "admin"
    claims = json.loads(await d["native_cache"].get(panel_auth._hash_cookie(fresh_token)))
    assert claims["account_id"] == str(d["owner"]) and claims["auth_provider"] == "legacy_admin"
    current = await c.get("/auth/me")
    assert current.status_code == 200 and current.json()["role"] == "admin" and current.json()["account"] is True
    status = (await c.get(d["base"] + "/site/publication")).json()
    assert status["can_publish"] is True and status["reauthentication_required"] is False
    changed = await c.post(d["base"] + "/site/publication", headers={"Origin": "https://test", "X-Orion-CSRF": fresh_login["csrf"]},
                           json={"expected_version": draft["version"], "enabled": True})
    assert changed.status_code == 200 and changed.json()["published"] is True, changed.text
    foreign = await c.post(f"/api/v1/tenants/{d['b']}/management/site/publication",
        headers={"Origin": "https://test", "X-Orion-CSRF": fresh_login["csrf"]}, json={"expected_version": 1, "enabled": True})
    assert foreign.status_code == 403
    # A second login replaces only the browser cookie; the previous session is
    # still stored and remains read-only for the owner-specific publication API.
    assert await d["native_cache"].get(old_key) == original_claims
    prior = await c.get("/auth/me", headers={"Cookie": f"{panel_auth.COOKIE}={old_token}"})
    assert prior.status_code == 200 and prior.json()["role"] == "support" and prior.json()["account"] is False
    async with d["migrator"].connect() as db:
        assert (await db.execute(text("SELECT actor_account_id FROM public_site_publications WHERE tenant_id=:tenant"), {"tenant": d["a"]})).scalar_one() == d["owner"]


@pytest.mark.asyncio
async def test_missing_native_binding_is_explicit_and_cannot_provision_or_publish(native_publication):
    d = native_publication; c = d["native_client"]
    old_login, old_token = await d["native_login"]()
    draft = await save_native_draft(d, old_login["csrf"])
    await d["bind_native"](matching=False)
    signed, _ = await d["native_login"]()
    assert signed["role"] == "support"  # Existing authentication compatibility remains intact.
    response = await c.get(d["base"] + "/site/publication")
    assert response.status_code == 200, response.text
    status = response.json()
    assert status["can_publish"] is False and status["reauthentication_required"] is True
    assert status["access_reason"] == "administrator_identity_unavailable"
    assert "identidade" in status["access_message"].lower()
    assert "digest" not in response.text and "pbkdf2" not in response.text
    denied = await c.post(d["base"] + "/site/publication", headers={"Origin": "https://test", "X-Orion-CSRF": signed["csrf"]},
                          json={"expected_version": draft["version"], "enabled": True})
    assert denied.status_code == 403
    assert await d["native_cache"].get(panel_auth._hash_cookie(old_token)) is not None
    async with d["migrator"].connect() as db:
        assert (await db.execute(text("SELECT count(*) FROM panel_accounts WHERE auth_provider='legacy_admin' AND id=:id"), {"id": d["owner"]})).scalar_one() == 1
        assert (await db.execute(text("SELECT legacy_credential_digest FROM panel_accounts WHERE id=:id"), {"id": d["owner"]})).scalar_one() == "a" * 64
        assert (await db.execute(text("SELECT count(*) FROM public_site_publications WHERE tenant_id=:tenant"), {"tenant": d["a"]})).scalar_one() == 0
        assert (await db.execute(text("SELECT role FROM tenant_memberships WHERE tenant_id=:tenant AND account_id=:id AND active"), {"tenant": d["a"], "id": d["owner"]})).scalar_one() == "owner"
