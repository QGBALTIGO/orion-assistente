"""Private owner-first requests, exact replay and immutable bulk confirmation."""
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import timedelta
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, Response
import httpx
import pytest

from app import subscription_bulk_messages as api
from app import subscription_bulk_store as store
from app import subscription_bulk_schemas as schemas
from app.catalog_api import authenticate
from test_subscription_bulk_policy import ACTOR, BULK, DEVICE, GENERATION, KEY, NOW, OWNER, PHONE, TENANT, Rows, definition

REQUEST, ASSET = uuid4(), uuid4()
ROOT = f"/api/v1/tenants/{TENANT}/subscription-whatsapp/bulk-messages"


def review_body():
    return schemas.ReviewBulk(idempotency_key=KEY, batch=definition()).model_dump(mode="json")


def operation(**changes):
    return {"id": REQUEST, "tenant_id": TENANT, "actor_account_id": OWNER, "idempotency_key": KEY,
        "kind": "review", "status": "prepared", "bulk_id": None,
        "request_fingerprint": store.fingerprint(review_body()), "review_version": "a" * 64,
        "result": None, "snapshot": {}, "preview": {"review_version": "a" * 64},
        "created_at": NOW - timedelta(seconds=1), "expires_at": NOW + timedelta(seconds=119),
        "confirmed_at": None} | changes


class RequestDB:
    def __init__(self, row=None): self.row, self.calls = row, []
    async def execute(self, query, params=None):
        sql = str(query)
        self.calls.append((sql, params or {}))
        if sql.startswith("SELECT * FROM subscription_bulk_requests"):
            assert params["t"] == TENANT and params["key"] == KEY
            return Rows([self.row] if self.row else [])
        if sql == "SELECT clock_timestamp()": return Rows([NOW])
        raise AssertionError(sql)


def client():
    app = FastAPI()
    for route in api.router.routes:
        if route.path.startswith(api.BASE):
            app.add_api_route(route.path, route.endpoint, methods=route.methods, status_code=route.status_code)
    app.dependency_overrides[authenticate] = lambda: ACTOR
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test")


def boundary(monkeypatch, db):
    @asynccontextmanager
    async def owner(*args): yield db
    monkeypatch.setattr(store, "owner_transaction", owner)


def confirm_body():
    return {"idempotency_key": str(KEY), "review_version": "a" * 64,
            "confirmation": "ENVIAR", "consent_confirmed": True}


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", ["/review", "/confirm", "/audience", f"/{BULK}/actions"])
async def test_owner_is_resolved_before_malformed_or_oversized_private_body(monkeypatch, suffix):
    @asynccontextmanager
    async def denied(*args):
        raise HTTPException(403, "owner required")
        yield
    monkeypatch.setattr(store, "owner_transaction", denied)
    async with client() as http:
        result = await http.post(ROOT + suffix, content=b"not-json" * (schemas.MAX_BODY // 8 + 1))
    assert result.status_code == 403


@pytest.mark.asyncio
async def test_stream_limit_and_invalid_phone_leave_no_operation(monkeypatch):
    db = RequestDB()
    boundary(monkeypatch, db)
    async with client() as http:
        big = await http.post(ROOT + "/review", content=b"x" * (schemas.MAX_BODY + 1))
        body = review_body()
        body["batch"]["items"][0]["phone"] = "0"
        invalid = await http.post(ROOT + "/review", json=body)
        missing = await http.get(ROOT + f"/requests/{KEY}")
    assert big.status_code == 413 and invalid.status_code == 422 and missing.status_code == 404
    assert len(db.calls) == 1 and db.calls[0][0].startswith("SELECT")


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["missing", "prepared", "expired", "succeeded"])
async def test_recovery_only_reads_original_private_operation(monkeypatch, state):
    row = None if state == "missing" else operation()
    if state == "expired": row["expires_at"] = NOW
    if state == "succeeded": row.update(status="succeeded", bulk_id=BULK, result={"batch": {"batch_id": str(BULK)}})
    db, before = RequestDB(row), deepcopy(row)
    boundary(monkeypatch, db)
    async with client() as http:
        result = await http.get(ROOT + f"/requests/{KEY}")
    assert result.status_code == (404 if state == "missing" else 200)
    assert result.headers["cache-control"] == "no-store"
    if row:
        assert result.json()["status"] == state and result.json()["replayed"] is True
        assert result.json()["preview"] == (row["preview"] if state == "prepared" else None)
    assert row == before and all(sql.startswith("SELECT") for sql, _ in db.calls)


@pytest.mark.asyncio
async def test_request_fingerprint_cannot_be_rebound_to_edited_rows_or_another_kind():
    db = RequestDB(operation())
    assert await store.existing_request(db, TENANT, ACTOR, KEY, "review", review_body()) == db.row
    changed = review_body()
    changed["batch"]["items"][0]["text"] = "Outro conteúdo"
    for kind, body in [("review", changed), ("cancel", review_body())]:
        with pytest.raises(HTTPException) as error:
            await store.existing_request(db, TENANT, ACTOR, KEY, kind, body)
        assert error.value.detail["code"] == "BULK_IDEMPOTENCY_CONFLICT"
    with pytest.raises(HTTPException) as error:
        await store.request_row(RequestDB(operation(actor_account_id=uuid4())), TENANT, ACTOR, KEY)
    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_confirmed_replay_survives_expiry_without_recapture_or_insert(monkeypatch):
    db = RequestDB(operation(status="succeeded", bulk_id=BULK, expires_at=NOW - timedelta(days=1),
        result={"batch": {"batch_id": str(BULK), "version": 1}}))
    boundary(monkeypatch, db)
    async with client() as http:
        result = await http.post(ROOT + "/confirm", json=confirm_body())
    assert result.status_code == 202 and result.json()["replayed"] is True
    assert result.json()["result"] == db.row["result"]
    assert all(sql.startswith("SELECT") for sql, _ in db.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("change,status,code", [
    ({"expires_at": NOW}, 410, "BULK_REVIEW_EXPIRED"),
    ({"review_version": "b" * 64}, 409, "BULK_IDEMPOTENCY_CONFLICT"),
    ({"kind": "cancel"}, 409, "BULK_IDEMPOTENCY_CONFLICT"),
])
async def test_confirmation_rejects_expired_or_crossed_authority_before_capture(monkeypatch, change, status, code):
    db = RequestDB(operation(**change))
    boundary(monkeypatch, db)
    async with client() as http:
        result = await http.post(ROOT + "/confirm", json=confirm_body())
    assert result.status_code == status and result.json()["detail"]["code"] == code
    assert all(sql.startswith("SELECT") for sql, _ in db.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["phone", "plan", "permission_version", "device", "template", "origins", "timezone"])
async def test_any_snapshot_drift_prevents_persistence(monkeypatch, changed):
    snapshot = {"definition": definition(), "rows": [{"phone": PHONE, "plan": [{"text": "old"}],
        "permission_version": 0, "device": "device-v1", "template": "template-v1", "origins": ["source-v1"]}],
        "timezone": "UTC", "counts": {"eligible": 1}, "review_request_id": str(REQUEST)}
    digest = store.fingerprint(snapshot)
    db = RequestDB(operation(snapshot=snapshot, review_version=digest))
    boundary(monkeypatch, db)
    async def recapture(*args):
        value = deepcopy(snapshot)
        if changed == "timezone": value[changed] = "America/Cuiaba"
        else: value["rows"][0][changed] = "changed"
        return value
    monkeypatch.setattr(store, "capture_review", recapture)
    async with client() as http:
        result = await http.post(ROOT + "/confirm", json=confirm_body() | {"review_version": digest})
    assert result.status_code == 409 and result.json()["detail"]["code"] == "BULK_CONFLICT"
    assert all(sql.startswith("SELECT") for sql, _ in db.calls)


@pytest.mark.asyncio
async def test_review_only_persists_prepared_request_and_replay_keeps_exact_preview(monkeypatch):
    db, captures, writes = RequestDB(), [], []
    boundary(monkeypatch, db)
    async def capture(*args):
        captures.append(args[-1])
        return {"definition": args[-1], "timezone": "UTC", "counts": {"total": 1, "eligible": 1, "blocked": 0,
            "duplicates": 0, "duplicate_destinations": 0, "unique_destinations": 1}, "rows": [], "facts": {}}
    async def save(*args, **kwargs):
        writes.append(kwargs)
        db.row = operation(snapshot=kwargs["snapshot"], preview=kwargs["preview"],
                           review_version=kwargs["preview"]["review_version"])
        return db.row
    monkeypatch.setattr(store, "capture_review", capture)
    monkeypatch.setattr(store, "save_request", save)
    async with client() as http:
        first = await http.post(ROOT + "/review", json=review_body())
        second = await http.post(ROOT + "/review", json=review_body())
        recovered = await http.get(ROOT + f"/requests/{KEY}")
    assert first.status_code == second.status_code == 201 and recovered.status_code == 200
    assert first.json()["preview"] == second.json()["preview"] == recovered.json()["preview"]
    assert first.json()["batch_id"] is None and len(captures) == len(writes) == 1
    assert all(sql.startswith("SELECT") for sql, _ in db.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("expected,marker", [(1, True), (3, False)])
async def test_cancel_conflict_proof_is_bound_only_to_proven_stale_version(monkeypatch, expected, marker):
    db = RequestDB()
    boundary(monkeypatch, db)
    async def row(*args, **kwargs): return {"id": BULK, "owner_account_id": OWNER, "version": 2}
    monkeypatch.setattr(store, "batch_row", row)
    async with client() as http:
        result = await http.post(ROOT + f"/{BULK}/actions", json={"idempotency_key": str(KEY), "action": "cancel",
            "expected_version": expected, "confirmation": "CANCELAR"})
    assert result.status_code == 409 and result.json()["detail"]["code"] == "BULK_CONFLICT"
    detail = result.json()["detail"]
    assert ("rejection" in detail) is marker
    if marker:
        assert detail["rejection"] == {"reason": "stale_version", "idempotency_key": str(KEY),
            "kind": "cancel", "batch_id": str(BULK), "expected_version": expected}


@pytest.mark.asyncio
async def test_catalog_resolution_does_not_write_requests_or_grants(monkeypatch):
    db = RequestDB()
    boundary(monkeypatch, db)
    async def groups(*args): return []
    async def selected(*args):
        return [{"phone": PHONE, "name": "Pessoa", "origins": [], "permission_version": 4, "blocked": True}], 2
    async def zone(*args): return "UTC"
    monkeypatch.setattr(store.campaigns, "group_summaries", groups)
    monkeypatch.setattr(store, "selected_contacts", selected)
    monkeypatch.setattr(store, "timezone_for", zone)
    async with client() as http:
        result = await http.post(ROOT + "/audience", json={"contact_keys": ["phone:" + PHONE]})
    assert result.status_code == 200 and result.json()["eligible"] == 0 and result.json()["duplicates"] == 2
    assert result.json()["items"][0]["phone_e164"] == "+" + PHONE
    assert all(sql.startswith("SELECT") for sql, _ in db.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", ["", "/contacts", f"/{BULK}/recipients"])
async def test_invalid_search_and_page_are_rejected_before_database(monkeypatch, suffix):
    @asynccontextmanager
    async def impossible(*args):
        raise AssertionError("Invalid query reached owner transaction")
        yield
    monkeypatch.setattr(store, "owner_transaction", impossible)
    async with client() as http:
        assert (await http.get(ROOT + suffix, params={"page": 2**80})).status_code == 422
        assert (await http.get(ROOT + suffix, params={"q": "bad\x00query"})).status_code == 422


@pytest.mark.asyncio
async def test_private_asset_is_reauthorized_after_storage_read_before_returning_bytes(monkeypatch):
    calls = []
    asset = {"size": 4, "sha256": "a" * 64, "mime": "image/png"}
    async def authorize(*args):
        calls.append("authorize")
        if len(calls) > 1: raise store.fail("BULK_REVOKED")
        return TENANT, asset
    from app import subscription_chatbot_media
    def read(*args, **kwargs): return b"test"
    monkeypatch.setattr(api, "authorized_asset", authorize)
    monkeypatch.setattr(subscription_chatbot_media, "get_media", read)
    operation_id = uuid4()
    headers = {"x-orion-device-id": str(DEVICE), "x-orion-device-generation": str(GENERATION),
               "x-orion-device-revision": "1", "x-orion-device-effect": str(operation_id)}
    request = Request({"type": "http", "headers": [(key.encode(), value.encode()) for key, value in headers.items()]})
    with pytest.raises(HTTPException) as error:
        await api.bulk_asset(uuid4(), ASSET, request, DEVICE, GENERATION, operation_id, 1, "original")
    assert error.value.detail["code"] == "BULK_REVOKED" and calls == ["authorize", "authorize"]
