"""First-party lead guards; no mail, external bridge, or database calls."""
import asyncio
import json
from uuid import uuid1, uuid4

from fastapi import FastAPI, HTTPException
import httpx
from pydantic import ValidationError
import pytest
import pytest_asyncio

from app import panel_auth, sales_requests_api as api
from app.catalog_api import Actor, authenticate
from app.sales_requests_schemas import SalesRequest, UpdateSalesRequest

BASE = "https://orion.test"
SECRET = "synthetic-sales-support-key-" + "x" * 40


def payload(**changes):
    return {"name": "Pessoa Sintética", "email": "person@example.invalid", "phone": "(11) 91234-5678",
            "business_name": "Negócio sintético", "segment": "outro", "intent": "trial", "consent": True,
            "website": "", **changes}


def headers(**changes):
    return {"Origin": BASE, "Idempotency-Key": str(uuid4()), **changes}


@pytest_asyncio.fixture
async def client(monkeypatch):
    monkeypatch.setenv("PANEL_PUBLIC_URL", BASE)
    monkeypatch.setenv("SUPPORT_API_TOKEN", SECRET)
    touched = []
    def forbidden_storage():
        touched.append("storage")
        raise AssertionError("Rejected public request reached storage")
    monkeypatch.setattr(api, "support_session_factory", forbidden_storage)
    app = FastAPI()
    app.include_router(api.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as c:
        yield c, touched, app


def test_normalizes_contact_only_without_business_or_subscription_authority():
    data = SalesRequest.model_validate(payload(name="  Pessoa   Sintética  ", email="  Person@Example.INVALID "))
    assert data.name == "Pessoa Sintética"
    assert data.email == "person@example.invalid"
    assert data.phone == "+5511912345678"
    optional = SalesRequest.model_validate(payload(phone="", business_name=""))
    assert optional.phone is None and optional.business_name is None
    for addition in ({"tenant_id": str(uuid4())}, {"role": "admin"}, {"trial_ends_at": "2099-01-01"}, {"status": "contacted"}):
        with pytest.raises(ValidationError):
            SalesRequest.model_validate(payload(**addition))


@pytest.mark.parametrize("changes", [
    {"name": "A"}, {"name": "x" * 121}, {"name": "person\x00secret"}, {"name": "secret\u202eevil"},
    {"email": "person"}, {"email": "x" * 65 + "@example.invalid"}, {"email": "name@-example.invalid"},
    {"email": ".name@example.invalid"}, {"email": "name.@example.invalid"}, {"email": "na..me@example.invalid"},
    {"email": {"value": "person@example.invalid"}}, {"phone": "+1 212 555 1234"}, {"phone": "5511999999999@lid"},
    {"business_name": "x" * 161}, {"segment": "arbitrary"}, {"intent": "free_forever"},
    {"consent": False}, {"consent": "true"}, {"consent": 1}, {"website": "x" * 201},
])
def test_contact_schema_rejects_invalid_or_hidden_data(changes):
    with pytest.raises(ValidationError):
        SalesRequest.model_validate(payload(**changes))


@pytest.mark.parametrize("changes", [
    {"status": "active", "expected_version": 1}, {"status": "contacted", "expected_version": 0},
    {"status": "contacted", "expected_version": True}, {"status": "contacted", "expected_version": "1"},
    {"status": "contacted", "expected_version": 1, "name": "Overwrite"},
])
def test_admin_changes_cannot_modify_contact_payload(changes):
    with pytest.raises(ValidationError):
        UpdateSalesRequest.model_validate(changes)


@pytest.mark.parametrize("origin", [None, "null", "https://evil.test", "http://orion.test", "https://orion.test:444",
    "https://orion.test.evil.test", "https://user@orion.test", "https://orion.test/path", "https://orion.test?x=1"])
async def test_foreign_or_missing_origin_never_reaches_storage(client, origin):
    c, touched, _ = client
    h = headers()
    if origin is None:
        h.pop("Origin")
    else:
        h["Origin"] = origin
    h["X-Forwarded-Host"] = "evil.test"
    r = await c.post("/api/public/sales-requests", json=payload(), headers=h)
    assert r.status_code == 403
    assert not touched


async def test_fetch_metadata_rejects_cross_origin_and_invalid_configuration(client, monkeypatch):
    c, touched, _ = client
    for site in ("cross-site", "same-site"):
        r = await c.post("/api/public/sales-requests", json=payload(), headers=headers(**{"Sec-Fetch-Site": site}))
        assert r.status_code == 403
    monkeypatch.setenv("PANEL_PUBLIC_URL", "https://orion.test/unexpected-path")
    r = await c.post("/api/public/sales-requests", json=payload(), headers=headers())
    assert r.status_code == 503
    assert not touched


@pytest.mark.parametrize("key", ["", "not-an-id", str(uuid1())])
async def test_public_request_requires_random_idempotency_key(client, key):
    c, touched, _ = client
    r = await c.post("/api/public/sales-requests", json=payload(), headers=headers(**{"Idempotency-Key": key}))
    assert r.status_code == 422
    assert not touched


async def test_invalid_payload_and_honeypot_do_not_echo_values_or_keys(client):
    c, touched, _ = client
    marker = "private-contact@example.invalid"
    bodies = [payload(email=marker, consent=False), payload(email=marker, website="https://spam.invalid"),
              payload(email=marker, **{marker: "hidden-secret"})]
    for body in bodies:
        r = await c.post("/api/public/sales-requests", json=body, headers=headers())
        assert r.status_code == 422
        assert marker not in r.text and "hidden-secret" not in r.text and "spam.invalid" not in r.text
        assert r.headers["cache-control"] == "no-store"
    assert not touched


async def test_content_type_size_and_duplicate_keys_rejected_before_storage(client):
    c, touched, _ = client
    for body, content_type, code in [("x", "text/plain", 415), ("x" * 8193, "application/json", 413),
            ('{"name":"a","name":"b"}', "application/json", 422), ("[" * 1500, "application/json", 422)]:
        r = await c.post("/api/public/sales-requests", content=body, headers=headers(**{"Content-Type": content_type}))
        assert r.status_code == code
    async def stream():
        yield b"x" * 5000
        yield b"x" * 4000
    r = await c.post("/api/public/sales-requests", content=stream(), headers=headers(**{"Content-Type": "application/json"}))
    assert r.status_code == 413
    assert not touched


async def test_redis_failure_is_fail_closed_and_hides_infrastructure_details(client, monkeypatch):
    c, touched, _ = client
    class BrokenCache:
        async def eval(self, *_):
            raise OSError("redis://private-secret@internal-host")
        async def aclose(self):
            pass
    monkeypatch.setattr(api, "_redis", BrokenCache)
    r = await c.post("/api/public/sales-requests", json=payload(), headers=headers())
    assert r.status_code == 503
    assert "private-secret" not in r.text and "internal-host" not in r.text
    assert not touched


async def test_redis_timeout_cancels_request_before_database(client, monkeypatch):
    c, touched, _ = client
    cancelled = []
    class SlowCache:
        async def eval(self, *_):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.append(True)
        async def aclose(self):
            pass
    monkeypatch.setattr(api, "_redis", SlowCache)
    monkeypatch.setattr(api, "REDIS_TIMEOUT", .02)
    r = await c.post("/api/public/sales-requests", json=payload(), headers=headers())
    assert r.status_code == 503 and cancelled == [True] and not touched


async def test_atomic_limit_uses_private_peer_identity_and_returns_retry_after(client, monkeypatch):
    c, touched, _ = client
    calls = []
    class LimitedCache:
        async def eval(self, *args):
            calls.append(args)
            return 90
        async def aclose(self):
            pass
    monkeypatch.setattr(api, "_redis", LimitedCache)
    r = await c.post("/api/public/sales-requests", json=payload(), headers=headers(**{"X-Forwarded-For": "evil-spoof"}))
    assert r.status_code == 429 and r.headers["retry-after"] == "90"
    assert not touched
    representation = repr(calls)
    assert "person@example.invalid" not in representation and "evil-spoof" not in representation and "127.0.0.1" not in representation
    assert calls[0][1] == 2 and calls[0][-4:] == (20, 600, 5, 3600)


async def test_missing_storage_and_database_errors_do_not_return_false_success(client, monkeypatch):
    c, _, _ = client
    async def no_limit(*_):
        pass
    monkeypatch.setattr(api, "submission_limit", no_limit)
    def unavailable():
        raise RuntimeError("SUPPORT_DATABASE_URL=postgresql://secret@private-host")
    monkeypatch.setattr(api, "support_session_factory", unavailable)
    r = await c.post("/api/public/sales-requests", json=payload(), headers=headers())
    assert r.status_code == 503 and "request_id" not in r.json()
    assert "secret" not in r.text and "private-host" not in r.text


async def test_admin_read_and_mutation_reject_anonymous_and_members(client):
    c, touched, app = client
    path = "/api/v1/admin/sales-requests"
    assert (await c.get(path)).status_code == 401
    async def member():
        return Actor("member", account_id=uuid4(), membership_role="owner")
    app.dependency_overrides[authenticate] = member
    assert (await c.get(path)).status_code == 403
    assert (await c.patch(path + "/" + str(uuid4()), json={"status": "contacted", "expected_version": 1})).status_code == 403
    assert not touched


async def test_admin_cookie_mutation_requires_existing_csrf_before_storage(client, monkeypatch):
    c, touched, _ = client
    class SessionCache:
        async def get(self, _):
            return json.dumps({"role": "support", "csrf": "synthetic-csrf"})
        async def aclose(self):
            pass
    monkeypatch.setattr(panel_auth, "_redis", SessionCache)
    c.cookies.set(panel_auth.COOKIE, "x" * 43)
    path = "/api/v1/admin/sales-requests/" + str(uuid4())
    r = await c.patch(path, json={"status": "contacted", "expected_version": 1}, headers={"Origin": BASE})
    assert r.status_code == 403
    r = await c.patch(path, json={"status": "contacted", "expected_version": 1},
                      headers={"Origin": "https://evil.test", "X-Orion-CSRF": "synthetic-csrf"})
    assert r.status_code == 403
    assert not touched
