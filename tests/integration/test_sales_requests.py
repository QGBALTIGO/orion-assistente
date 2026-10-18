"""PostgreSQL/Redis proofs for public contact capture and privileged follow-up."""
import asyncio
import hashlib
import hmac
import os
from types import SimpleNamespace
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException
import httpx
import pytest
import pytest_asyncio
from redis.asyncio import from_url
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from app import sales_requests_api as api
from app.catalog_api import Actor, authenticate, support_session_factory

pytestmark = pytest.mark.integration
ORIGIN = "https://sales-test.invalid"
PATH = "/api/v1/admin/sales-requests"


class UnlimitedTestCache:
    async def eval(self, *_):
        return 0
    async def aclose(self):
        pass


@pytest_asyncio.fixture
async def sales_db(monkeypatch):
    if not all(os.getenv(key) for key in ("APP_DATABASE_URL", "MIGRATION_DATABASE_URL", "SUPPORT_DATABASE_URL")):
        if os.getenv("REQUIRE_POSTGRES_TESTS") == "1":
            pytest.fail("Sales requests require PostgreSQL at migration0018")
        pytest.skip("Requires real PostgreSQL and migration0018")
    tag = uuid4().hex
    monkeypatch.setenv("PANEL_PUBLIC_URL", ORIGIN)
    monkeypatch.setenv("SUPPORT_API_TOKEN", "sales-test-key-" + tag + "z" * 32)
    monkeypatch.setattr(api, "_redis", UnlimitedTestCache)
    support_session_factory.cache_clear()
    migrator = create_async_engine(os.environ["MIGRATION_DATABASE_URL"].replace("postgresql+psycopg://", "postgresql+asyncpg://", 1))
    runtime = create_async_engine(os.environ["APP_DATABASE_URL"].replace("postgresql://", "postgresql+asyncpg://", 1))
    selected = {"actor": Actor("support")}
    async def auth():
        return selected["actor"]
    from app.main import app as main_app
    app = FastAPI()
    app.include_router(main_app.router)
    app.dependency_overrides[authenticate] = auth
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        try:
            yield {"tag": tag, "client": client, "migrator": migrator, "runtime": runtime, "selected": selected}
        finally:
            async with migrator.begin() as db:
                await db.execute(text("DELETE FROM sales_requests WHERE email LIKE :pattern"), {"pattern": "%" + tag + "@example.invalid"})
            factory = support_session_factory()
            await factory.kw["bind"].dispose()
            support_session_factory.cache_clear()
            await migrator.dispose()
            await runtime.dispose()


def payload(d, label="one", **changes):
    return {"name": "Pessoa Sintética", "email": label + "-" + d["tag"] + "@example.invalid",
            "phone": "(11) 91234-5678", "business_name": "Empresa sintética", "segment": "outro",
            "intent": "trial", "consent": True, "website": "", **changes}


async def submit(d, body=None, identity=None):
    return await d["client"].post("/api/public/sales-requests", json=body or payload(d),
        headers={"Origin": ORIGIN, "Idempotency-Key": str(identity or uuid4())})


async def row(d, identity):
    async with d["migrator"].connect() as db:
        value = (await db.execute(text("SELECT * FROM sales_requests WHERE id=:id"), {"id": UUID(identity)})).mappings().one()
        return dict(value)


async def test_public_capture_persists_consent_without_accounts_trials_or_messages(sales_db):
    d = sales_db
    tables = ("tenants", "panel_accounts", "tenant_memberships", "subscriptions", "appointments", "message_outbox", "billing_events")
    async with d["migrator"].connect() as db:
        before = {table: (await db.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one() for table in tables}
    body = payload(d, name="  Pessoa   Sintética  ", phone="", business_name="")
    response = await submit(d, body)
    assert response.status_code == 201, response.text
    assert set(response.json()) == {"ok", "request_id"} and response.json()["ok"] is True
    assert body["email"] not in response.text and "Pessoa" not in response.text
    assert response.headers["cache-control"] == "no-store"
    saved = await row(d, response.json()["request_id"])
    assert saved["name"] == "Pessoa Sintética" and saved["phone"] is None and saved["business_name"] is None
    assert saved["consent"] is True and saved["consent_version"] == "sales-contact-v1"
    assert saved["status"] == "new" and saved["version"] == 1
    assert saved["created_at"] == saved["consented_at"] and saved["contacted_at"] is None
    async with d["migrator"].connect() as db:
        after = {table: (await db.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one() for table in tables}
    assert after == before
    # Knowing the opaque receipt is not permission to retrieve a lead publicly.
    public = await d["client"].get("/api/public/sales-requests/" + response.json()["request_id"])
    assert public.status_code == 404 and body["email"] not in public.text


async def test_concurrent_idempotent_submission_returns_one_persisted_request(sales_db):
    d, key = sales_db, uuid4()
    results = await asyncio.gather(*(submit(d, identity=key) for _ in range(3)))
    assert [r.status_code for r in results] == [201] * 3
    assert len({r.json()["request_id"] for r in results}) == 1
    async with d["migrator"].connect() as db:
        assert (await db.execute(text("SELECT count(*) FROM sales_requests WHERE idempotency_key=:key"), {"key": key})).scalar_one() == 1
    changed = await submit(d, payload(d, intent="essencial"), key)
    assert changed.status_code == 409
    assert (await row(d, results[0].json()["request_id"]))["intent"] == "trial"


async def test_concurrent_different_payloads_never_overwrite_original_request(sales_db):
    d, key = sales_db, uuid4()
    results = await asyncio.gather(submit(d, payload(d, intent="trial"), key), submit(d, payload(d, intent="contact"), key))
    assert sorted(r.status_code for r in results) == [201, 409]
    success = next(r for r in results if r.status_code == 201)
    saved = await row(d, success.json()["request_id"])
    original_intent = "trial" if results[0].status_code == 201 else "contact"
    assert saved["intent"] == original_intent and saved["version"] == 1


async def test_admin_listing_has_filtered_total_global_counts_and_no_private_keys(sales_db):
    d = sales_db
    ids = []
    for index, intent in enumerate(("trial", "trial", "contact")):
        result = await submit(d, payload(d, str(index), name=d["tag"] + " pessoa " + str(index), intent=intent))
        assert result.status_code == 201, result.text
        ids.append(result.json()["request_id"])
    changed = await d["client"].patch(PATH + "/" + ids[0], json={"status": "contacted", "expected_version": 1})
    assert changed.status_code == 200, changed.text
    r = await d["client"].get(PATH, params={"q": d["tag"], "intent": "trial", "status": "all", "limit": 1})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["total"] == 2 and len(data["items"]) == 1 and data["limit"] == 1 and data["offset"] == 0
    assert data["counts"]["all"] >= 3 and data["counts"]["contacted"] >= 1 and data["access"]["can_update"] is True
    assert not ({"idempotency_key", "payload_hash", "updated_by_account_id"} & set(data["items"][0]))
    second = (await d["client"].get(PATH, params={"q": d["tag"], "intent": "trial", "limit": 1, "offset": 1})).json()
    assert second["items"][0]["id"] != data["items"][0]["id"]
    only_contacted = (await d["client"].get(PATH, params={"q": d["tag"], "status": "contacted"})).json()
    assert only_contacted["total"] == 1 and only_contacted["items"][0]["id"] == ids[0]
    assert (await d["client"].get(PATH, params={"q": "%" + d["tag"]})).json()["total"] == 0
    assert r.headers["cache-control"] == "no-store"


async def test_global_request_permission_does_not_follow_tenant_membership(sales_db):
    d = sales_db
    result = await submit(d)
    identity = result.json()["request_id"]
    for actor in (Actor("member", account_id=uuid4(), membership_role="owner"),
                  Actor("member", account_id=uuid4(), membership_role="editor"), Actor("viewer", tenant_id=uuid4())):
        d["selected"]["actor"] = actor
        response = await d["client"].get(PATH)
        assert response.status_code == 403 and payload(d)["email"] not in response.text
        assert (await d["client"].patch(PATH + "/" + identity, json={"status": "contacted", "expected_version": 1})).status_code == 403
    d["selected"]["actor"] = Actor("admin")
    assert (await d["client"].get(PATH, params={"q": d["tag"]})).json()["total"] == 1
    assert (await row(d, identity))["version"] == 1


async def test_status_updates_are_versioned_and_contact_date_is_not_a_sent_message(sales_db):
    d = sales_db
    result = await submit(d)
    identity = result.json()["request_id"]
    before = await row(d, identity)
    no_change = await d["client"].patch(PATH + "/" + identity, json={"status": "new", "expected_version": 1})
    assert no_change.status_code == 200 and no_change.json()["version"] == 1
    responses = await asyncio.gather(*(d["client"].patch(PATH + "/" + identity,
        json={"status": status, "expected_version": 1}) for status in ("contacted", "closed")))
    assert sorted(r.status_code for r in responses) == [200, 409]
    current = await row(d, identity)
    assert current["version"] == 2 and current["updated_by_role"] == "support"
    assert current["name"] == before["name"] and current["payload_hash"] == before["payload_hash"]
    assert current["consented_at"] == before["consented_at"] and current["created_at"] == before["created_at"]
    if current["status"] != "contacted":
        change = await d["client"].patch(PATH + "/" + identity, json={"status": "contacted", "expected_version": 2})
        assert change.status_code == 200
        current = await row(d, identity)
    assert current["contacted_at"] is not None
    version = current["version"]
    await d["client"].patch(PATH + "/" + identity, json={"status": "closed", "expected_version": version})
    closed = await row(d, identity)
    assert closed["contacted_at"] == current["contacted_at"]
    assert (await d["client"].patch(PATH + "/" + str(uuid4()), json={"status": "closed", "expected_version": 1})).status_code == 404


async def test_postgres_rls_owner_and_column_grants_keep_leads_outside_tenant_role(sales_db):
    d = sales_db
    identity = (await submit(d)).json()["request_id"]
    async with d["migrator"].connect() as db:
        properties = (await db.execute(text("""SELECT c.relrowsecurity,c.relforcerowsecurity,r.rolname
            FROM pg_class c JOIN pg_roles r ON r.oid=c.relowner WHERE c.oid='sales_requests'::regclass"""))).one()
        assert tuple(properties) == (True, True, "app_migrator")
    for tenant in (None, str(uuid4())):
        async with d["runtime"].begin() as db:
            if tenant:
                await db.execute(text("SELECT set_config('app.tenant_id',:tenant,true)"), {"tenant": tenant})
            with pytest.raises(DBAPIError) as blocked:
                await db.execute(text("SELECT name,email FROM sales_requests"))
            assert blocked.value.orig.sqlstate == "42501"
    factory = support_session_factory()
    async with factory() as db:
        async with db.begin():
            assert (await db.execute(text("SELECT count(*) FROM sales_requests WHERE id=:id"), {"id": UUID(identity)})).scalar_one() == 1
            assert not (await db.execute(text("SELECT has_table_privilege(current_user,'sales_requests','DELETE')"))).scalar_one()
    async with factory() as db:
        async with db.begin():
            with pytest.raises(DBAPIError) as blocked:
                await db.execute(text("UPDATE sales_requests SET name='Overwrite' WHERE id=:id"), {"id": UUID(identity)})
            assert blocked.value.orig.sqlstate == "42501"
    assert (await row(d, identity))["name"] == "Pessoa Sintética"


async def test_invalid_consent_honeypot_and_payload_never_persist(sales_db):
    d = sales_db
    for body in (payload(d, consent=False), payload(d, website="https://spam.invalid"),
                 payload(d, tenant_id=str(uuid4())), payload(d, phone="invalid@lid")):
        response = await submit(d, body)
        assert response.status_code == 422
        assert body["email"] not in response.text
    async with d["migrator"].connect() as db:
        assert (await db.execute(text("SELECT count(*) FROM sales_requests WHERE email=:email"),
                                {"email": payload(d)["email"]})).scalar_one() == 0


async def test_real_redis_limits_are_atomic_and_scoped_to_peer_and_email(sales_db, monkeypatch):
    d = sales_db
    url = os.getenv("REDIS_URL")
    if not url:
        if os.getenv("REQUIRE_REDIS_TESTS") == "1":
            pytest.fail("Sales limit requires real Redis")
        pytest.skip("Requires Redis for the atomic quota proof")
    cache = from_url(url, decode_responses=True, socket_timeout=2, socket_connect_timeout=2)
    try:
        await cache.ping()
    except (RedisError, OSError):
        await cache.aclose()
        if os.getenv("REQUIRE_REDIS_TESTS") == "1":
            pytest.fail("Real Redis is unavailable")
        pytest.skip("Requires reachable Redis")
    monkeypatch.setattr(api, "_redis", lambda: from_url(url, decode_responses=True, socket_timeout=2, socket_connect_timeout=2))
    peer = "sales-test-peer-" + d["tag"]
    request = SimpleNamespace(client=SimpleNamespace(host=peer), headers={"x-forwarded-for": "untrusted"})
    emails = [f"{number}-{d['tag']}@example.invalid" for number in range(21)]
    secret = os.environ["SUPPORT_API_TOKEN"].encode()
    all_keys = ["orion:sales-limit:" + kind + ":" + hmac.new(secret, (kind + ":" + value).encode(), hashlib.sha256).hexdigest()
                for kind, value in [("peer", peer), *(('email', email) for email in emails)]]
    second_peer = peer + "-second"
    all_keys.append("orion:sales-limit:peer:" + hmac.new(secret, ("peer:" + second_peer).encode(), hashlib.sha256).hexdigest())
    try:
        results = await asyncio.gather(*(api.submission_limit(request, email) for email in emails), return_exceptions=True)
        assert sum(value is None for value in results) == 20
        rejected = [value for value in results if isinstance(value, HTTPException)]
        assert len(rejected) == 1 and rejected[0].status_code == 429
        # A separate peer can submit, but the independent email quota is shared.
        other = SimpleNamespace(client=SimpleNamespace(host=second_peer), headers={})
        accepted_email = next(email for email, result in zip(emails, results) if result is None)
        for _ in range(4):
            await api.submission_limit(other, accepted_email)
        with pytest.raises(HTTPException) as limited:
            await api.submission_limit(other, accepted_email)
        assert limited.value.status_code == 429
        assert await cache.ttl(all_keys[0]) > 0
    finally:
        await cache.delete(*all_keys)
        await cache.aclose()
