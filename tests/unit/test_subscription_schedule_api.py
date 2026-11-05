"""Owner-first parsing, recoverable reviews, immutable replay and private assets."""
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import timedelta
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, Response
import httpx
import pytest

from app import subscription_scheduled_messages as api
from app import subscription_schedule_store as store
from app import subscription_schedule_schemas as schemas
from app.catalog_api import authenticate
from test_subscription_schedule_policy import ACTOR, DEVICE, KEY, NOW, OWNER, RUN, SCHEDULE, TENANT, Rows, definition

REQUEST, GENERATION, ASSET = (uuid4() for _ in range(3))
ROOT = f"/api/v1/tenants/{TENANT}/subscription-whatsapp/scheduled-messages"


def operation(**changes):
    return {"id": REQUEST, "tenant_id": TENANT, "actor_account_id": OWNER, "idempotency_key": KEY,
        "kind": "review_create", "status": "prepared", "schedule_id": None, "run_id": None,
        "request_fingerprint": store.fingerprint({"idempotency_key": str(KEY), "schedule": definition()}),
        "review_version": "a" * 64, "result": None, "snapshot": {}, "preview": {"review_version": "a" * 64},
        "created_at": NOW - timedelta(seconds=1), "expires_at": NOW + timedelta(seconds=119), "confirmed_at": None} | changes


class RequestDB:
    def __init__(self, row=None): self.row, self.calls = row, []
    async def execute(self, query, params=None):
        sql = str(query)
        self.calls.append((sql, params or {}))
        if sql.startswith("SELECT * FROM subscription_schedule_requests"):
            assert params["t"] == TENANT and params["key"] == KEY
            return Rows([self.row] if self.row else [])
        if sql == "SELECT clock_timestamp()": return Rows([NOW])
        raise AssertionError(sql)


def public_app():
    app = FastAPI()
    for route in api.router.routes:
        if route.path.startswith(api.BASE):
            app.add_api_route(route.path, route.endpoint, methods=route.methods, status_code=route.status_code)
    app.dependency_overrides[authenticate] = lambda: ACTOR
    return app


def client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=public_app()), base_url="https://test")


def confirm_body(*, pending=False):
    return {"idempotency_key": str(KEY), "review_version": "a" * 64,
        "confirmation": "ENVIAR PENDENTES" if pending else "AGENDAR", "consent_confirmed": True}


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", ["/review", f"/{SCHEDULE}/review", "/confirm", "/pending/review", "/pending/confirm",
    "/audience", f"/{SCHEDULE}/actions"])
async def test_ownership_precedes_malformed_or_unbounded_request_parsing(monkeypatch, suffix):
    @asynccontextmanager
    async def denied(*args):
        raise HTTPException(403, "owner required")
        yield
    monkeypatch.setattr(store, "owner_transaction", denied)
    async with client() as http:
        result = await http.post(ROOT + suffix, content=b"not-json" * (schemas.MAX_BODY // 8 + 1))
    assert result.status_code == 403


@pytest.mark.asyncio
async def test_rejected_review_body_leaves_no_operation_and_remains_correctable_with_same_key(monkeypatch):
    db = RequestDB()
    @asynccontextmanager
    async def boundary(*args): yield db
    monkeypatch.setattr(store, "owner_transaction", boundary)
    async with client() as http:
        bad = await http.post(ROOT + "/review", json={"idempotency_key": str(KEY), "schedule": definition(text="{phone}")})
        recovered = await http.get(ROOT + f"/requests/{KEY}")
    assert bad.status_code == 422 and bad.json()["detail"]["code"] == "SCHEDULE_INVALID"
    assert recovered.status_code == 404 and recovered.headers["cache-control"] == "no-store"
    assert len(db.calls) == 1 and db.calls[0][0].startswith("SELECT")


@pytest.mark.asyncio
async def test_rejected_snapshot_does_not_persist_an_operation(monkeypatch):
    db = RequestDB()
    @asynccontextmanager
    async def boundary(*args): yield db
    async def invalid(*args, **kwargs): raise store.fail("SCHEDULE_CONTACT_CHANGED", 422)
    monkeypatch.setattr(store, "owner_transaction", boundary)
    monkeypatch.setattr(store, "capture_review", invalid)
    async with client() as http:
        result = await http.post(ROOT + "/review", json={"idempotency_key": str(KEY), "schedule": definition()})
    assert result.status_code == 422
    assert all(sql.startswith("SELECT") for sql, _ in db.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["missing", "prepared", "expired", "succeeded"])
async def test_recovery_never_mutates_expiry_or_replays_a_write(monkeypatch, state):
    row = None if state == "missing" else operation()
    if state == "expired": row["expires_at"] = NOW
    if state == "succeeded": row.update(status="succeeded", result={"schedule": {"schedule_id": str(SCHEDULE)}}, confirmed_at=NOW)
    db, before = RequestDB(row), deepcopy(row)
    @asynccontextmanager
    async def boundary(*args): yield db
    monkeypatch.setattr(store, "owner_transaction", boundary)
    async with client() as http:
        result = await http.get(ROOT + f"/requests/{KEY}")
    assert result.status_code == (404 if state == "missing" else 200)
    assert result.headers["cache-control"] == "no-store"
    if row:
        assert result.json()["status"] == state and result.json()["replayed"] is True
        assert result.json()["preview"] == (row["preview"] if state == "prepared" else None)
    assert row == before and all(sql.startswith("SELECT") for sql, _ in db.calls)


@pytest.mark.asyncio
async def test_fingerprint_reserves_the_original_review_when_a_corrected_request_races():
    body = {"idempotency_key": str(KEY), "schedule": definition()}
    db = RequestDB(operation(request_fingerprint=store.fingerprint(body)))
    assert await store.existing_request(db, TENANT, ACTOR, KEY, "review_create", body) == db.row
    with pytest.raises(HTTPException) as error:
        await store.existing_request(db, TENANT, ACTOR, KEY, "review_create", body | {"schedule": definition(title="Outro")})
    assert error.value.detail["code"] == "SCHEDULE_IDEMPOTENCY_CONFLICT"
    assert db.row["request_fingerprint"] == store.fingerprint(body)


@pytest.mark.asyncio
async def test_recovery_does_not_transfer_to_a_different_current_owner():
    with pytest.raises(HTTPException) as error:
        await store.request_row(RequestDB(operation(actor_account_id=uuid4())), TENANT, ACTOR, KEY)
    assert error.value.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("pending", [False, True])
async def test_confirmation_replay_returns_the_original_result_even_after_expiry(monkeypatch, pending):
    row = operation(kind="pending_review" if pending else "review_create", status="succeeded",
        result={"released": [{"schedule_id": str(SCHEDULE), "run_id": str(RUN)}], "total": 1} if pending else
            {"schedule": {"schedule_id": str(SCHEDULE), "run_id": str(RUN), "version": 1}},
        expires_at=NOW - timedelta(days=1), confirmed_at=NOW - timedelta(days=1))
    db = RequestDB(row)
    @asynccontextmanager
    async def boundary(*args): yield db
    monkeypatch.setattr(store, "owner_transaction", boundary)
    async with client() as http:
        result = await http.post(ROOT + ("/pending/confirm" if pending else "/confirm"), json=confirm_body(pending=pending))
    assert result.status_code == 202 and result.json()["result"] == row["result"]
    assert result.json()["replayed"] is True and result.json()["preview"] is None
    assert all(sql.startswith("SELECT") for sql, _ in db.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("pending", [False, True])
async def test_expired_confirmation_cannot_rebuild_or_admit_an_audience(monkeypatch, pending):
    db = RequestDB(operation(kind="pending_review" if pending else "review_create", expires_at=NOW))
    @asynccontextmanager
    async def boundary(*args): yield db
    monkeypatch.setattr(store, "owner_transaction", boundary)
    async with client() as http:
        result = await http.post(ROOT + ("/pending/confirm" if pending else "/confirm"), json=confirm_body(pending=pending))
    assert result.status_code == 410 and result.json()["detail"]["code"] == "SCHEDULE_REVIEW_EXPIRED"
    assert all(sql.startswith("SELECT") for sql, _ in db.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [{"review_version": "b" * 64}, {"kind": "pending_review"}])
async def test_confirm_hash_and_operation_kind_cannot_cross_authority(monkeypatch, change):
    db = RequestDB(operation(**change))
    @asynccontextmanager
    async def boundary(*args): yield db
    monkeypatch.setattr(store, "owner_transaction", boundary)
    async with client() as http:
        result = await http.post(ROOT + "/confirm", json=confirm_body())
    assert result.status_code == 409 and result.json()["detail"]["code"] == "SCHEDULE_IDEMPOTENCY_CONFLICT"
    assert len(db.calls) == 1


@pytest.mark.asyncio
async def test_empty_pending_review_is_recoverable_but_cannot_release_work(monkeypatch):
    db, saved = RequestDB(), []
    @asynccontextmanager
    async def boundary(*args): yield db
    async def targets(*args, **kwargs): return []
    async def save(*args, **kwargs):
        saved.append(kwargs)
        db.row = operation(kind="pending_review", snapshot=kwargs["snapshot"], preview=kwargs["preview"],
            review_version=kwargs["preview"]["review_version"])
        return db.row
    monkeypatch.setattr(store, "owner_transaction", boundary)
    monkeypatch.setattr(store, "pending_targets", targets)
    monkeypatch.setattr(store, "save_request", save)
    async with client() as http:
        review = await http.post(ROOT + "/pending/review", json={"idempotency_key": str(KEY)})
        recovered = await http.get(ROOT + f"/requests/{KEY}")
        confirmed = await http.post(ROOT + "/pending/confirm", json=confirm_body(pending=True) |
                                   {"review_version": review.json()["preview"]["review_version"]})
    assert review.status_code == 201 and review.json()["preview"]["total"] == 0
    assert recovered.json()["preview"]["runs"] == []
    assert confirmed.status_code == 422 and confirmed.json()["detail"]["code"] == "SCHEDULE_NO_ELIGIBLE"
    assert len(saved) == 1 and all(sql.startswith("SELECT") for sql, _ in db.calls)


@pytest.mark.asyncio
async def test_prepared_pending_confirmation_binds_only_original_ids_and_never_future_work(monkeypatch):
    target = {"id": str(RUN), "schedule_id": str(SCHEDULE), "run_version": 1, "version": 1, "snapshot_version": "b" * 64}
    db = RequestDB(operation(kind="pending_review", snapshot={"runs": [target]}))
    seen = []
    @asynccontextmanager
    async def boundary(*args): yield db
    async def changed(*args, **kwargs):
        seen.append(kwargs)
        return [target | {"run_version": 2}]
    monkeypatch.setattr(store, "owner_transaction", boundary)
    monkeypatch.setattr(store, "pending_targets", changed)
    async with client() as http:
        result = await http.post(ROOT + "/pending/confirm", json=confirm_body(pending=True))
    assert result.status_code == 409 and seen == [{"ids": [RUN]}]
    assert all(sql.startswith("SELECT") for sql, _ in db.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", ["", "/contacts", f"/{SCHEDULE}/runs", f"/{SCHEDULE}/recipients"])
async def test_large_page_numbers_are_rejected_before_queries(monkeypatch, suffix):
    @asynccontextmanager
    async def impossible(*args):
        raise AssertionError("Invalid page must not open a transaction")
        yield
    monkeypatch.setattr(store, "owner_transaction", impossible)
    async with client() as http:
        result = await http.get(ROOT + suffix, params={"page": 10**100})
    assert result.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", ["", "/contacts", f"/{SCHEDULE}/recipients"])
async def test_nul_in_search_is_a_public_validation_error_before_postgres(monkeypatch, suffix):
    @asynccontextmanager
    async def impossible(*args):
        raise AssertionError("PostgreSQL cannot accept a NUL text parameter")
        yield
    monkeypatch.setattr(store, "owner_transaction", impossible)
    async with client() as http:
        result = await http.get(ROOT + suffix, params={"q": "Ana\x00"})
    assert result.status_code == 422 and result.json()["detail"]["code"] == "SCHEDULE_INVALID"
    assert result.headers["cache-control"] == "no-store"


def item_row(**changes):
    return {"snapshot": {"definition": definition(), "device": {"id": str(DEVICE), "name": "Aparelho"}},
        "id": RUN, "schedule_id": SCHEDULE, "parent_version": 1, "version": 2, "occurrence_index": 1,
        "series_status": "active", "status": "pending", "n_total": 1, "n_pending": 1,
        "first_admitted_at": None, "manual_released_at": None, "device_available": True,
        "due_at": NOW + timedelta(days=1), "local_date": (NOW + timedelta(days=1)).date(), "timezone": "UTC",
        "device_status": "connected", "created_at": NOW, "error_code": None} | changes


def test_detail_exposes_cancel_running_but_never_edit_or_retry_uncertain():
    pending = store.item_dto(item_row(), NOW)
    assert pending["actions"] == ["edit", "cancel_run", "cancel_series"]
    running = store.item_dto(item_row(status="running", first_admitted_at=NOW), NOW)
    assert running["actions"] == ["cancel_run", "cancel_series"]
    uncertain = store.item_dto(item_row(status="uncertain", first_admitted_at=NOW,
        error_code="MESSAGE_OUTCOME_UNCERTAIN", n_pending=0, n_uncertain=1), NOW)
    assert uncertain["actions"] == ["cancel_series"] and uncertain["bucket"] == "history"
    assert uncertain["attention_message"] == store.ERRORS["MESSAGE_OUTCOME_UNCERTAIN"]
    assert uncertain["progress"]["uncertain"] == 1


def test_invalid_dst_and_future_revocation_remain_editable_attention():
    dst = store.item_dto(item_row(due_at=None, error_code="SCHEDULE_RECURRENCE_INVALID"), NOW)
    assert dst["scheduled_at"] is None and dst["estimated_end_at"] is None and dst["active_filter"] == "attention"
    revoked = store.item_dto(item_row(error_code="SCHEDULE_OPTED_OUT", n_pending=0, n_cancelled=1), NOW)
    assert revoked["status"] == "pending" and revoked["active_filter"] == "attention"
    assert "edit" in revoked["actions"]


def asset_request(**changes):
    headers = {"x-orion-device-id": str(DEVICE), "x-orion-device-generation": str(GENERATION),
        "x-orion-device-revision": "1", "x-orion-device-effect": str(REQUEST)} | changes
    return Request({"type": "http", "method": "GET", "path": "/", "headers": [(key.encode(), value.encode()) for key, value in headers.items()]})


@pytest.mark.parametrize("change", [{"x-orion-device-id": str(uuid4())}, {"x-orion-device-generation": str(uuid4())},
    {"x-orion-device-revision": "2"}, {"x-orion-device-effect": str(uuid4())}, {"x-orion-device-revision": "invalid"}])
def test_private_assets_require_the_identical_query_and_header_context(change):
    with pytest.raises(HTTPException) as error:
        api.asset_header_scope(asset_request(**change), DEVICE, GENERATION, 1, REQUEST)
    assert error.value.status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("revoked", [False, True])
async def test_private_asset_rechecks_authority_after_storage_without_another_claim(monkeypatch, revoked):
    from app import subscription_chatbot_media as media
    checks, reads = [], []
    async def authorized(request, asset_id, scope, variant):
        checks.append((asset_id, scope, variant))
        if len(checks) == 2 and revoked:
            raise store.fail("SCHEDULE_CANCELLED")
        return TENANT, {"size": 4, "sha256": "b" * 64, "mime": "image/jpeg"}
    def get_media(tenant, identity, **kwargs):
        reads.append((tenant, identity, kwargs))
        return b"test"
    monkeypatch.setattr(api, "authorized_asset", authorized)
    monkeypatch.setattr(media, "get_media", get_media)
    if revoked:
        with pytest.raises(HTTPException) as error:
            await api.scheduled_asset(uuid4(), ASSET, asset_request(), DEVICE, GENERATION, REQUEST, 1, "original")
        assert error.value.detail["code"] == "SCHEDULE_CANCELLED"
    else:
        result = await api.scheduled_asset(uuid4(), ASSET, asset_request(), DEVICE, GENERATION, REQUEST, 1, "original")
        assert result.body == b"test" and result.headers["cache-control"] == "no-store"
    assert checks[0] == checks[1] and len(checks) == 2
    assert reads == [(TENANT, ASSET, {"expected_size": 4, "expected_sha256": "b" * 64, "variant": "original"})]
