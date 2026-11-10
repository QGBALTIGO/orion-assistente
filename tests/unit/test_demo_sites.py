"""Commercial previews: real authorization, honest unknowns and data isolation."""
import copy
import hashlib
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import demo_sites as demos
from app.panel_auth import panel_actor


def sample():
    return {
        "source_id": "synthetic:barber-" + uuid4().hex,
        "catalogue": {
            "site": {"title": "Barbearia sintética", "city": "Cidade de exemplo",
                     "contact_phone": "+5511999991234"},
            "services": [{"id": "cut", "name": "Corte", "duration_min": None,
                          "price_cents": None, "image_url": "https://images.example.org/cut.webp"}],
            "professionals": [],
            "opening_hours": [{"weekday": 0, "intervals": None}, {"weekday": 6, "intervals": []}],
            "timezone": "America/Sao_Paulo",
        },
        "source_urls": ["https://example.org/public-business"],
        "pending_fields": ["Valores e equipe a confirmar"],
        "review_notes": "NOTA_INTERNA_NAO_PUBLICAR",
    }


def api():
    app = FastAPI()
    app.include_router(demos.router)
    return app


def test_missing_facts_remain_unknown_and_import_cannot_publish():
    value = demos.DemoCreate.model_validate(sample())
    assert value.catalogue.services[0].price_cents is None
    assert value.catalogue.services[0].duration_min is None
    assert value.catalogue.professionals == []
    assert value.catalogue.opening_hours[0].intervals is None
    assert value.catalogue.opening_hours[1].intervals == []
    assert value.status == "draft" and value.expires_at is None
    with pytest.raises(ValidationError):
        demos.DemoCreate.model_validate({**sample(), "status": "published"})


@pytest.mark.parametrize("changes", [
    {"tenant_id": str(uuid4())}, {"booking_enabled": True}, {"customer_phone": "+5511999991234"},
    {"source_urls": ["http://127.0.0.1/private"]}, {"source_urls": ["http://localhost/private"]},
])
def test_private_authority_and_internal_origins_are_rejected(changes):
    with pytest.raises(ValidationError):
        demos.DemoCreate.model_validate({**sample(), **changes})


@pytest.mark.parametrize("url", [
    "javascript:alert(1)", "data:image/svg+xml;base64,AAAA", "//example.org/image.webp",
    "https://user:password@example.org/image.webp", "https://10.0.0.1/image.webp",
    "https://example.org\\@localhost/image.webp", "/media/../../secret",
])
def test_image_links_cannot_execute_or_target_private_hosts(url):
    data = sample()
    data["catalogue"]["services"][0]["image_url"] = url
    with pytest.raises(ValidationError):
        demos.DemoCreate.model_validate(data)


def test_catalogue_rejects_private_fields_invalid_links_and_false_zeroes():
    data = sample()
    data["catalogue"]["site"]["review_notes"] = "private"
    with pytest.raises(ValidationError):
        demos.DemoCreate.model_validate(data)
    data = sample()
    data["catalogue"]["services"][0]["professional_ids"] = ["not-in-this-demo"]
    with pytest.raises(ValidationError):
        demos.DemoCreate.model_validate(data)
    for field, value in (("price_cents", False), ("price_cents", -1), ("duration_min", 0)):
        data = sample()
        data["catalogue"]["services"][0][field] = value
        with pytest.raises(ValidationError):
            demos.DemoCreate.model_validate(data)


@pytest.mark.parametrize("claims", [
    {"role": "member", "account_id": str(uuid4())},
    {"role": "support", "account_id": str(uuid4())},
    {"role": "admin", "account_id": str(uuid4()), "auth_provider": "password", "email_verified": False},
])
def test_non_admin_cannot_list_read_import_or_publish(monkeypatch, claims):
    app = api()
    app.dependency_overrides[panel_actor] = lambda: claims
    monkeypatch.setattr(demos, "_storage", lambda: pytest.fail("Unauthorized access reached storage"))
    with TestClient(app) as client:
        assert client.get("/api/v1/admin/demos").status_code == 403
        assert client.get("/api/v1/admin/demos/" + str(uuid4())).status_code == 403
        assert client.post("/api/v1/admin/demos", json=sample()).status_code == 403
        assert client.patch("/api/v1/admin/demos/" + str(uuid4()) + "/status",
                            json={"expected_version": 1, "status": "published"}).status_code == 403


def test_unauthenticated_admin_routes_require_session(monkeypatch):
    monkeypatch.setattr(demos, "_storage", lambda: pytest.fail("Unauthenticated access reached storage"))
    with TestClient(api()) as client:
        assert client.get("/api/v1/admin/demos").status_code == 401
        assert client.post("/api/v1/admin/demos", json=sample()).status_code == 401


def test_real_panel_session_enforces_csrf_and_same_origin_before_any_demo_write(monkeypatch):
    from app import panel_auth

    claims = {"role": "admin", "auth_provider": "legacy_admin", "account_id": str(uuid4()),
              "auth_version": 1, "csrf": "synthetic-csrf-only"}

    class Cache:
        async def get(self, key):
            return None if key.startswith("orion:maintenance") else json.dumps(claims)

    async def identity(account_id, version):
        assert account_id == claims["account_id"] and version == 1
        return {"name": "Administrador sintético"}

    touched = []

    def storage():
        touched.append(True)
        raise HTTPException(418, "Authorized test boundary")

    monkeypatch.setattr(panel_auth, "_request_redis", lambda request: (Cache(), False))
    monkeypatch.setattr("app.legacy_admin_identity.current_identity", identity)
    monkeypatch.setattr(demos, "_storage", storage)
    with TestClient(api()) as client:
        client.cookies.set(panel_auth.COOKIE, secrets.token_urlsafe(32))
        url = "/api/v1/admin/demos/" + str(uuid4()) + "/status"
        payload = {"expected_version": 1, "status": "published"}
        assert client.patch(url, json=payload).status_code == 403
        assert client.patch(url, json=payload, headers={"X-Orion-CSRF": "wrong"}).status_code == 403
        assert client.patch(url, json=payload, headers={"X-Orion-CSRF": claims["csrf"],
                            "Origin": "https://another.example.org"}).status_code == 403
        assert touched == []
        assert client.patch(url, json=payload, headers={"X-Orion-CSRF": claims["csrf"],
                            "Origin": "http://testserver"}).status_code == 418
        assert touched == [True]


def public_storage(monkeypatch, record):
    class Result:
        def scalar_one_or_none(self):
            return record

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def execute(self, sql, params):
            assert str(sql) == "SELECT public.read_commercial_demo(:token)"
            assert demos.TOKEN_PATTERN.fullmatch(params["token"])
            return Result()

    monkeypatch.setattr(demos, "get_session_factory", lambda: Session)


def live_record():
    payload = demos.DemoCreate.model_validate(sample())
    return {"catalogue": payload.catalogue.model_dump(mode="json"), "status": "published",
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
            "source_id": "PRIVATE_SOURCE", "review_notes": "PRIVATE_NOTES", "created_by": str(uuid4())}


def test_public_output_is_read_only_whitelist_and_page_is_not_indexed(monkeypatch):
    public_storage(monkeypatch, live_record())
    token = secrets.token_urlsafe(32)
    with TestClient(api()) as client:
        result = client.get("/api/v1/public/demos/" + token)
        assert result.status_code == 200
        data = result.json()
        assert data["demo"] is True and data["preview"] is True and data["booking_enabled"] is False
        assert data["services"][0]["price_cents"] is None
        assert data["professionals"] == []
        assert data["booking_horizon_days"] == 30
        assert not {"source_id", "review_notes", "created_by", "source_urls", "pending_fields", "tenant_id"}.intersection(data)
        assert "PRIVATE_" not in result.text and "NOTA_INTERNA" not in result.text
        page = client.get("/demo/" + token)
        assert page.status_code == 200
        assert "noindex" in page.headers["x-robots-tag"] and "noindex" in page.text
        assert "Demonstração • Barbearia sintética" in page.text
        for suffix in ("", "/appointments", "/availability"):
            assert client.post("/api/v1/public/demos/" + token + suffix, json={}).status_code in {404, 405}


@pytest.mark.parametrize("changes", [
    {"status": "draft"}, {"status": "archived"},
    {"expires_at": "2001-01-01T00:00:00+00:00"},
    {"expires_at": "2001-01-01T00:00:00"},
])
def test_unpublished_archived_expired_and_invalid_snapshots_are_unavailable(monkeypatch, changes):
    public_storage(monkeypatch, {**live_record(), **changes})
    with TestClient(api()) as client:
        assert client.get("/api/v1/public/demos/" + secrets.token_urlsafe(32)).status_code == 404


def test_catalogue_extra_metadata_fails_closed_even_if_database_record_is_wrong(monkeypatch):
    record = live_record()
    record["catalogue"]["private_owner_email"] = "private@example.org"
    public_storage(monkeypatch, record)
    with TestClient(api()) as client:
        response = client.get("/api/v1/public/demos/" + secrets.token_urlsafe(32))
        assert response.status_code == 404 and "private@example.org" not in response.text


def test_malformed_token_does_not_reach_database(monkeypatch):
    monkeypatch.setattr(demos, "get_session_factory", lambda: pytest.fail("Malformed token reached database"))
    with TestClient(api()) as client:
        assert client.get("/api/v1/public/demos/not-a-token").status_code == 404


def test_bundled_webp_asset_and_absence_or_traversal(monkeypatch, tmp_path):
    directory = tmp_path / "demo-assets"
    directory.mkdir()
    content = b"RIFF\x04\x00\x00\x00WEBP"
    filename = hashlib.sha256(content).hexdigest() + ".webp"
    (directory / filename).write_bytes(content)
    (tmp_path / "private.txt").write_text("private", encoding="utf-8")
    symlink_name = "c" * 64 + ".webp"
    (directory / symlink_name).symlink_to(tmp_path / "private.txt")
    monkeypatch.setattr(demos, "STATIC", tmp_path)
    with TestClient(api()) as client:
        result = client.get("/demo-assets/" + filename)
        assert result.status_code == 200 and result.content == content
        assert result.headers["content-type"] == "image/webp"
        assert result.headers["x-content-type-options"] == "nosniff"
        assert "immutable" in result.headers["cache-control"]
        for invalid in ("d" * 64 + ".webp", "a" * 64 + ".svg", "short.webp", symlink_name,
                        "%2e%2e%2fprivate.txt", "%2fetc%2fpasswd", "a" * 64 + ".WEBP"):
            response = client.get("/demo-assets/" + invalid)
            assert response.status_code == 404 and "private" not in response.text


def async_url(value):
    return value.replace("postgresql+psycopg://", "postgresql+asyncpg://", 1).replace(
        "postgresql://", "postgresql+asyncpg://", 1).replace("postgres://", "postgresql+asyncpg://", 1)


@pytest_asyncio.fixture
async def demo_pg(monkeypatch):
    keys = ("APP_DATABASE_URL", "MIGRATION_DATABASE_URL", "SUPPORT_DATABASE_URL")
    if not all(os.getenv(key) for key in keys):
        if os.getenv("REQUIRE_POSTGRES_TESTS") == "1":
            pytest.fail("Commercial demos require PostgreSQL roles and migration 0077")
        pytest.skip("Requires local synthetic PostgreSQL with migration 0077")
    engines = {key: create_async_engine(async_url(os.environ[key])) for key in keys}
    actor = uuid4()
    async with engines["MIGRATION_DATABASE_URL"].begin() as db:
        await db.execute(text("""INSERT INTO panel_accounts(id,name,email,password_hash)
            VALUES(:id,'Administrador sintético',:email,'synthetic-test-not-a-hash')"""),
            {"id": actor, "email": actor.hex + "@example.invalid"})
    monkeypatch.setattr(demos, "_storage", lambda: async_sessionmaker(engines["SUPPORT_DATABASE_URL"], expire_on_commit=False))
    monkeypatch.setattr(demos, "get_session_factory", lambda: async_sessionmaker(engines["APP_DATABASE_URL"], expire_on_commit=False))
    app = api()
    app.dependency_overrides[panel_actor] = lambda: {
        "role": "admin", "account_id": str(actor), "auth_provider": "legacy_admin",
    }
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            yield {"client": client, "actor": actor, "migrator": engines["MIGRATION_DATABASE_URL"],
                   "runtime": engines["APP_DATABASE_URL"], "support": engines["SUPPORT_DATABASE_URL"]}
    finally:
        async with engines["MIGRATION_DATABASE_URL"].begin() as db:
            await db.execute(text("DELETE FROM commercial_demo_events WHERE actor_id=:id"), {"id": actor})
            await db.execute(text("DELETE FROM commercial_demo_sites WHERE created_by=:id"), {"id": actor})
            await db.execute(text("DELETE FROM panel_accounts WHERE id=:id"), {"id": actor})
        for engine in engines.values():
            await engine.dispose()


@pytest.mark.integration
async def test_pg_lifecycle_idempotency_conflicts_expiry_and_no_operational_writes(demo_pg):
    d = demo_pg
    async with d["migrator"].connect() as db:
        before = (await db.execute(text("""SELECT (SELECT count(*) FROM tenants) AS tenants,
            (SELECT count(*) FROM appointments) AS appointments,(SELECT count(*) FROM customers) AS customers,
            (SELECT count(*) FROM tenant_memberships) AS memberships"""))).mappings().one()
    payload = sample()
    created = await d["client"].post("/api/v1/admin/demos", json=payload)
    assert created.status_code == 201, created.text
    item = created.json()["item"]
    assert item["status"] == "draft" and item["version"] == 1
    assert 29 <= (datetime.fromisoformat(item["expires_at"]) - datetime.now(timezone.utc)).days <= 30
    token = item["public_url"].split("/")[-1]
    public = "/api/v1/public/demos/" + token
    assert (await d["client"].get(public)).status_code == 404
    replay = await d["client"].post("/api/v1/admin/demos", json=payload)
    assert replay.status_code == 200 and replay.json()["replayed"] is True
    assert replay.json()["item"]["id"] == item["id"]
    changed = copy.deepcopy(payload)
    changed["review_notes"] = "Different private input"
    assert (await d["client"].post("/api/v1/admin/demos", json=changed)).status_code == 409
    published = await d["client"].patch("/api/v1/admin/demos/" + item["id"] + "/status",
                                      json={"expected_version": 1, "status": "published"})
    assert published.status_code == 200, published.text
    assert published.json()["item"]["version"] == 2
    public_result = await d["client"].get(public)
    assert public_result.status_code == 200 and public_result.json()["booking_enabled"] is False
    assert payload["review_notes"] not in public_result.text and payload["source_id"] not in public_result.text
    stale = await d["client"].patch("/api/v1/admin/demos/" + item["id"] + "/status",
                                  json={"expected_version": 1, "status": "archived"})
    assert stale.status_code == 409
    assert (await d["client"].get(public)).status_code == 200
    archived = await d["client"].patch("/api/v1/admin/demos/" + item["id"] + "/status",
                                     json={"expected_version": 2, "status": "archived"})
    assert archived.status_code == 200 and archived.json()["item"]["version"] == 3
    assert (await d["client"].get(public)).status_code == 404
    async with d["migrator"].begin() as db:
        await db.execute(text("UPDATE commercial_demo_sites SET status='published',expires_at=now()-interval '1 second' WHERE id=:id"),
                         {"id": UUID(item["id"])})
    assert (await d["client"].get(public)).status_code == 404
    expired_publish = await d["client"].patch("/api/v1/admin/demos/" + item["id"] + "/status",
                                            json={"expected_version": 3, "status": "published"})
    assert expired_publish.status_code == 422
    async with d["migrator"].connect() as db:
        after = (await db.execute(text("""SELECT (SELECT count(*) FROM tenants) AS tenants,
            (SELECT count(*) FROM appointments) AS appointments,(SELECT count(*) FROM customers) AS customers,
            (SELECT count(*) FROM tenant_memberships) AS memberships"""))).mappings().one()
        events = (await db.execute(text("SELECT action,version FROM commercial_demo_events WHERE demo_id=:id ORDER BY version"),
                                   {"id": UUID(item["id"])})).all()
    assert dict(before) == dict(after)
    assert events == [("created", 1), ("published", 2), ("archived", 3)]


@pytest.mark.integration
async def test_pg_runtime_cannot_read_private_tables_or_write_and_function_is_scoped(demo_pg):
    d = demo_pg
    result = await d["client"].post("/api/v1/admin/demos", json=sample())
    assert result.status_code == 201, result.text
    item = result.json()["item"]
    token = item["public_url"].split("/")[-1]
    for table in ("commercial_demo_sites", "commercial_demo_events"):
        async with d["runtime"].connect() as db:
            with pytest.raises(DBAPIError):
                await db.execute(text("SELECT * FROM " + table))
        async with d["runtime"].connect() as db:
            with pytest.raises(DBAPIError):
                await db.execute(text("DELETE FROM " + table))
    async with d["runtime"].connect() as db:
        assert (await db.execute(text("SELECT read_commercial_demo(:token)"), {"token": token})).scalar_one() is None
    await d["client"].patch("/api/v1/admin/demos/" + item["id"] + "/status",
                            json={"expected_version": 1, "status": "published"})
    async with d["runtime"].connect() as db:
        envelope = (await db.execute(text("SELECT read_commercial_demo(:token)"), {"token": token})).scalar_one()
        assert set(envelope) == {"catalogue", "status", "expires_at"}
        assert (await db.execute(text("SELECT read_commercial_demo(:token)"), {"token": secrets.token_urlsafe(32)})).scalar_one() is None
        policies = (await db.execute(text("""SELECT relname,relrowsecurity,relforcerowsecurity FROM pg_class
            WHERE oid IN ('public.commercial_demo_sites'::regclass,'public.commercial_demo_events'::regclass)"""))).all()
        assert len(policies) == 2 and all(row[1] and row[2] for row in policies)
        grants = (await db.execute(text("""SELECT has_function_privilege('app_runtime','public.read_commercial_demo(text)','EXECUTE'),
            has_table_privilege('app_runtime','public.commercial_demo_sites','SELECT'),
            has_table_privilege('app_support','public.commercial_demo_events','UPDATE')"""))).one()
        assert tuple(grants) == (True, False, False)
