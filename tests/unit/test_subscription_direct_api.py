"""Owner-first direct API boundaries and origin-scoped request recovery."""
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import timedelta
from uuid import uuid4

from fastapi import FastAPI, HTTPException
import httpx
import pytest

from app import subscription_direct_messages as api
from app import subscription_direct_schemas as schemas
from app import subscription_direct_store as store
from app.catalog_api import authenticate
from test_subscription_bulk_api import RequestDB, operation as bulk_operation
from test_subscription_bulk_policy import ACTOR, BULK, KEY, NOW, OWNER, TENANT
from test_subscription_direct_policy import message

ROOT = f"/api/v1/tenants/{TENANT}/subscription-whatsapp/direct-messages"


def body():
    return schemas.ReviewDirectMessage(idempotency_key=KEY, message=message()).model_dump(mode="json")


def operation(**changes):
    return bulk_operation(origin="direct", request_fingerprint=store.fingerprint(body())) | changes


def confirmation(**changes):
    return {"idempotency_key": str(KEY), "review_version": "a" * 64,
            "confirmation": "ENVIAR", "consent_confirmed": True} | changes


def client():
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[authenticate] = lambda: ACTOR
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test")


def boundary(monkeypatch, db):
    @asynccontextmanager
    async def owner(tenant, actor, **kwargs):
        assert tenant == TENANT and actor == ACTOR and kwargs == {"feature": "direct_messages.manage"}
        yield db
    monkeypatch.setattr(store.shared, "owner_transaction", owner)


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", ["/review", "/confirm", f"/{BULK}/actions"])
async def test_owner_is_checked_before_reading_invalid_or_oversized_private_body(monkeypatch, suffix):
    @asynccontextmanager
    async def denied(*args, **kwargs):
        raise HTTPException(403, "owner required")
        yield
    monkeypatch.setattr(store.shared, "owner_transaction", denied)
    async with client() as http:
        response = await http.post(ROOT + suffix, content=b"not-json" * schemas.MAX_BODY)
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_bounded_body_and_strict_confirmation_never_create_a_request(monkeypatch):
    db = RequestDB()
    boundary(monkeypatch, db)
    async with client() as http:
        over = await http.post(ROOT + "/review", content=b"x" * (schemas.MAX_BODY + 1))
        invalid = await http.post(ROOT + "/review", json=body() | {"unexpected": "data"})
        responses = [await http.post(ROOT + "/confirm", json=confirmation(consent_confirmed=value))
                     for value in (False, "true", 1)]
    assert over.status_code == 413 and invalid.status_code == 422
    assert all(response.status_code == 422 for response in responses)
    assert db.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["prepared", "expired", "succeeded", "missing"])
async def test_recovery_is_read_only_and_returns_original_send_without_bulk_identity(monkeypatch, status):
    row = None if status == "missing" else operation()
    if status == "expired":
        row["expires_at"] = NOW
    if status == "succeeded":
        row.update(status="succeeded", bulk_id=BULK, result={"send": {"send_id": str(BULK)}, "saved_template": None})
    db, before = RequestDB(row), deepcopy(row)
    boundary(monkeypatch, db)
    async with client() as http:
        response = await http.get(ROOT + f"/requests/{KEY}")
    assert response.status_code == (404 if status == "missing" else 200)
    assert response.headers["cache-control"] == "no-store"
    if row:
        assert response.json()["status"] == status and response.json()["replayed"] is True
        assert "batch_id" not in response.json() and "snapshot" not in response.json()
        assert response.json()["send_id"] == (str(BULK) if status == "succeeded" else None)
    assert row == before and all(sql.startswith("SELECT") for sql, _ in db.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("path,method,status,code", [
    ("/review", "post", 409, "DIRECT_IDEMPOTENCY_CONFLICT"),
    (f"/{BULK}/actions", "post", 409, "DIRECT_IDEMPOTENCY_CONFLICT"),
    ("/confirm", "post", 404, "DIRECT_NOT_FOUND"),
    (f"/requests/{KEY}", "get", 404, "DIRECT_NOT_FOUND"),
])
async def test_bulk_request_key_never_leaks_or_rebinds_into_direct_origin(monkeypatch, path, method, status, code):
    db = RequestDB(operation(origin="bulk"))
    boundary(monkeypatch, db)
    values = (body() if path == "/review" else confirmation() if path == "/confirm" else
              {"idempotency_key": str(KEY), "action": "cancel", "expected_version": 1, "confirmation": "CANCELAR"})
    async with client() as http:
        response = await http.get(ROOT + path) if method == "get" else await http.post(ROOT + path, json=values)
    assert response.status_code == status and response.json()["detail"]["code"] == code
    assert all(sql.startswith("SELECT") for sql, _ in db.calls)
    assert str(BULK) not in str(response.json())


@pytest.mark.asyncio
async def test_another_account_request_stays_private_even_with_exact_key(monkeypatch):
    db = RequestDB(operation(actor_account_id=uuid4()))
    boundary(monkeypatch, db)
    async with client() as http:
        response = await http.post(ROOT + "/review", json=body())
    assert response.status_code == 404 and response.json()["detail"]["code"] == "DIRECT_NOT_FOUND"


@pytest.mark.asyncio
async def test_successful_confirmation_replays_after_expiry_without_recapture_or_template_insert(monkeypatch):
    result = {"send": {"send_id": str(BULK), "version": 1},
              "saved_template": {"id": str(uuid4()), "name": "Modelo", "kind": "text", "revision": 1}}
    db = RequestDB(operation(status="succeeded", bulk_id=BULK, expires_at=NOW - timedelta(days=1), result=result))
    boundary(monkeypatch, db)
    async with client() as http:
        response = await http.post(ROOT + "/confirm", json=confirmation())
    assert response.status_code == 202 and response.json()["replayed"] is True
    assert response.json()["result"] == result and response.json()["send_id"] == str(BULK)
    assert all(sql.startswith("SELECT") for sql, _ in db.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("changes,status,code", [
    ({"expires_at": NOW}, 410, "DIRECT_REVIEW_EXPIRED"),
    ({"review_version": "b" * 64}, 409, "DIRECT_IDEMPOTENCY_CONFLICT"),
    ({"kind": "cancel"}, 409, "DIRECT_IDEMPOTENCY_CONFLICT"),
])
async def test_invalid_confirmation_fails_before_recapture(monkeypatch, changes, status, code):
    db = RequestDB(operation(**changes))
    boundary(monkeypatch, db)
    async with client() as http:
        response = await http.post(ROOT + "/confirm", json=confirmation())
    assert response.status_code == status and response.json()["detail"]["code"] == code
    assert all(sql.startswith("SELECT") for sql, _ in db.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["message", "template_library_revision", "media", "permission_version"])
async def test_snapshot_change_requires_new_review_before_any_send_or_template_write(monkeypatch, changed):
    snapshot = {"message": body()["message"], "template_library_revision": 7,
                "media": "original-hash", "permission_version": 1, "review_request_id": str(bulk_operation()["id"])}
    digest = store.fingerprint(snapshot)
    db = RequestDB(operation(snapshot=snapshot, review_version=digest))
    boundary(monkeypatch, db)
    async def capture(*args):
        return snapshot | {changed: "changed"}
    monkeypatch.setattr(store, "capture_review", capture)
    async with client() as http:
        response = await http.post(ROOT + "/confirm", json=confirmation(review_version=digest))
    assert response.status_code == 409 and response.json()["detail"]["code"] == "DIRECT_CONFLICT"
    assert all(sql.startswith("SELECT") for sql, _ in db.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("expected,stale", [(1, True), (3, False)])
async def test_cancel_conflict_proof_uses_exact_direct_send_and_request(monkeypatch, expected, stale):
    db = RequestDB()
    boundary(monkeypatch, db)
    async def row(*args, **kwargs):
        return {"id": BULK, "owner_account_id": OWNER, "origin": "direct", "version": 2}
    monkeypatch.setattr(store.bulk, "batch_row", row)
    async with client() as http:
        response = await http.post(ROOT + f"/{BULK}/actions", json={"idempotency_key": str(KEY),
            "action": "cancel", "expected_version": expected, "confirmation": "CANCELAR"})
    assert response.status_code == 409 and response.json()["detail"]["code"] == "DIRECT_CONFLICT"
    detail = response.json()["detail"]
    assert ("rejection" in detail) is stale
    if stale:
        assert detail["rejection"] == {"reason": "stale_version", "idempotency_key": str(KEY), "kind": "cancel",
                                       "send_id": str(BULK), "expected_version": expected}


@pytest.mark.asyncio
async def test_direct_cannot_open_or_cancel_a_bulk_batch(monkeypatch):
    db = RequestDB()
    boundary(monkeypatch, db)
    async def row(*args, **kwargs):
        return {"id": BULK, "owner_account_id": OWNER, "origin": "bulk", "version": 1}
    monkeypatch.setattr(store.bulk, "batch_row", row)
    async with client() as http:
        read = await http.get(ROOT + f"/{BULK}")
        cancel = await http.post(ROOT + f"/{BULK}/actions", json={"idempotency_key": str(KEY), "action": "cancel",
            "expected_version": 1, "confirmation": "CANCELAR"})
    assert read.status_code == cancel.status_code == 404
    assert all(sql.startswith("SELECT") for sql, _ in db.calls)
