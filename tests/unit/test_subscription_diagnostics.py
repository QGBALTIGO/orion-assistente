"""Synthetic diagnostic authority, passive reads and immutable confirmation."""
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException, Response
from fastapi.routing import APIRoute
import httpx
from pydantic import ValidationError
import pytest

from app import subscription_diagnostics as api
from app.catalog_api import Actor, authenticate
from app.subscription_device_schemas import DeviceSettings, EffectRequest
from app.subscription_diagnostics_schemas import PrepareMessage, ConfirmMessage, DiagnosticIntent, ProxyResult, ProxyAuthorize

NOW = datetime(2026, 11, 4, 20, tzinfo=timezone.utc)
TENANT, OWNER, DEVICE, GENERATION, CHANNEL = (uuid4() for _ in range(5))
KEY, REQUEST, EFFECT, CLAIM_OWNER = (uuid4() for _ in range(4))
OWN = "5567999999902"
ACTOR = Actor("member", account_id=OWNER)
FIXTURE = json.loads((Path(__file__).parents[1]/"fixtures/subscription_diagnostics_contract.json").read_text())


def device(**overrides):
    return {"id": DEVICE, "tenant_id": TENANT, "channel_id": CHANNEL, "owner_account_id": OWNER,
        "generation": GENERATION, "revision": 3, "removed_at": None, "status": "connected", "name": "Synthetic device",
        "settings": DeviceSettings().model_dump(mode="json"), "proxy_secret_encrypted": None,
        "runtime": {"status": "connected", "phone": OWN, "phone_verified": True}, "runtime_at": NOW-timedelta(seconds=3)} | overrides


def intention(**overrides):
    return {"id": REQUEST, "tenant_id": TENANT, "device_id": DEVICE, "generation": GENERATION, "settings_revision": 3,
        "actor_account_id": OWNER, "idempotency_key": KEY, "kind": "message_test", "request_fingerprint": "b"*64,
        "confirmation_version": "a"*64, "status": "prepared", "created_at": NOW-timedelta(seconds=2),
        "expires_at": NOW+timedelta(seconds=118), "execution_expires_at": None, "effect_id": None, "confirmed_at": None,
        "started_at": None, "finished_at": None, "claimed_owner": None, "claim_token": None, "claim_fence": 0,
        "claim_expires_at": None, "result": None,
        "snapshot": {"phone": OWN, "self_phone": OWN, "recipient_mode": "self", "text": api.TEST_TEXT, "device_version": api.device_version(device())}} | overrides


def effect(**overrides):
    return {"id": EFFECT, "tenant_id": TENANT, "device_id": DEVICE, "generation": GENERATION, "settings_revision": 3,
        "event_id": uuid4(), "type": "send_text", "purpose": "diagnostic_test", "payload": {"phone": OWN, "text": api.TEST_TEXT},
        "status": "pending", "attempts": 0, "authorized_at": None, "claimed_owner": None, "claimed_fence": None,
        "created_at": NOW-timedelta(seconds=1), "due_at": NOW-timedelta(seconds=1), "expires_at": NOW+timedelta(seconds=119),
        "provider_id": None, "error_code": None, "finished_at": None, "previous_effect": None} | overrides


def admitted(**overrides):
    return intention(status="pending", effect_id=EFFECT, confirmed_at=NOW-timedelta(seconds=1),
        execution_expires_at=NOW+timedelta(seconds=119)) | overrides


class Rows:
    def __init__(self, rows): self.rows = rows
    def mappings(self): return self
    def one(self): assert len(self.rows) == 1; return self.rows[0]
    def one_or_none(self): return self.rows[0] if self.rows else None
    def scalar_one(self): return self.one()
    def scalar_one_or_none(self): return self.one_or_none()
    def all(self): return self.rows


class DB:
    def __init__(self, operation=None, outbound=None, recent=(), previous=None, busy=False, tenant_status="active"):
        self.operation, self.outbound, self.recent, self.previous, self.busy = operation, outbound, list(recent), previous, busy
        self.calls = []
        self.tenant_status = tenant_status
    async def execute(self, query, params=None):
        sql = str(query)
        self.calls.append((sql, params or {}))
        if sql == "SELECT clock_timestamp()": return Rows([NOW])
        if sql.startswith("SELECT timezone"): return Rows(["UTC"])
        if sql.startswith("SELECT status FROM tenants"): return Rows([self.tenant_status])
        if sql.startswith("SELECT EXISTS"): return Rows([self.busy])
        if sql.startswith("SELECT * FROM subscription_device_effects"): return Rows([self.outbound] if self.outbound else [])
        if sql.startswith("SELECT e.*,"): return Rows(self.recent)
        if sql.startswith("SELECT id,result,snapshot"): return Rows([self.previous] if self.previous else [])
        if sql.startswith("SELECT * FROM subscription_diagnostic_requests") or sql.startswith("SELECT r.*"):
            return Rows([self.operation] if self.operation else [])
        if sql.startswith("SELECT * FROM subscription_devices"): return Rows([device()])
        if sql.startswith("UPDATE subscription_device_effects"):
            return Rows([])
        raise AssertionError(sql)


@pytest.mark.parametrize("value", ["+55 (67) 99999-9902", "55 67 99999-9902", "+5567999999902", OWN])
def test_phone_punctuation_is_canonicalized_before_idempotency(value):
    payload = PrepareMessage(idempotency_key=KEY, expected_version="a"*64, phone=value)
    assert payload.phone == OWN


@pytest.mark.parametrize("value", [None, "", "   "])
def test_blank_phone_selects_only_verified_self(value):
    assert PrepareMessage(idempotency_key=KEY, expected_version="a"*64, phone=value).phone is None


@pytest.mark.parametrize("value", ["5567@s.whatsapp.net", "x@lid", "https://example.test", "000000000", "+55\n67999999902", "１"*10, "1"*41])
def test_phone_cannot_contain_jids_urls_controls_or_unbounded_content(value):
    with pytest.raises(ValidationError):
        PrepareMessage(idempotency_key=KEY, expected_version="a"*64, phone=value)


@pytest.mark.parametrize("model,body", [(ConfirmMessage, FIXTURE["confirm_request"]), (DiagnosticIntent, FIXTURE["proxy_request"])])
@pytest.mark.parametrize("extra", [{"phone": OWN}, {"text": "override"}, {"url": "http://127.0.0.1/"}, {"allowSelf": True}, {"proxy_secret": "private"}])
def test_confirmation_and_proxy_cannot_override_effect_content_or_network(model, body, extra):
    with pytest.raises(ValidationError):
        model.model_validate(body | extra)


@pytest.mark.parametrize("field", ["revision", "generation", "owner_account_id", "channel_id"])
def test_device_version_binds_authorization_context(field):
    row = device()
    altered = row | {field: 4 if field == "revision" else uuid4()}
    assert api.device_version(row) != api.device_version(altered)
    assert api.device_version(row) == api.device_version(row | {"runtime_at": NOW, "status": "disconnected"})


def test_confirmation_replay_returns_existing_effect_despite_new_version_or_expiry():
    operation = admitted(expires_at=NOW-timedelta(seconds=1))
    request = ConfirmMessage(idempotency_key=KEY, confirmation_version="a"*64, confirmation="ENVIAR")
    api.confirm_context(operation, device(revision=99), request, NOW)
    with pytest.raises(HTTPException) as failure:
        api.confirm_context(operation, device(), request.model_copy(update={"confirmation_version": "b"*64}), NOW)
    assert failure.value.detail["code"] == "DIAGNOSTIC_CONFIRMATION_INVALID"


def test_expired_preparation_is_distinct_from_uncertain_send_and_never_revived():
    request = ConfirmMessage(idempotency_key=KEY, confirmation_version="a"*64, confirmation="ENVIAR")
    with pytest.raises(HTTPException) as failure:
        api.confirm_context(intention(expires_at=NOW), device(), request, NOW)
    assert failure.value.status_code == 410
    assert failure.value.detail["code"] == "DIAGNOSTIC_PREVIEW_EXPIRED"


@pytest.mark.parametrize("live", [None, {"status": "connected", "phone": OWN, "phone_verified": False, "applied_revision": 3},
    {"status": "connected", "phone": OWN, "phone_verified": True, "applied_revision": 2}])
def test_self_identity_cannot_fall_back_to_business_test_phone_or_stored_runtime(live):
    row = device()
    row["settings"]["test_phone_e164"] = "+"+OWN
    with pytest.raises(HTTPException):
        api.verified_self(row, {"enabled": True}, live)


@pytest.fixture
def trusted(monkeypatch):
    calls = []
    async def workspace(db, tenant, **kwargs):
        calls.append((tenant, kwargs))
    async def owner(*args, **kwargs): return True
    monkeypatch.setattr(api.store, "workspace", workspace)
    monkeypatch.setattr(api.channels, "_owner_authorized", owner)
    return calls


@pytest.mark.asyncio
async def test_confirmed_diagnostic_gets_one_claim_and_purpose_bound_grant(trusted):
    db = DB(admitted(), effect())
    request = EffectRequest(operation_id=EFFECT)
    result = await api.authorize_diagnostic_effect(db, device(), db.outbound, CLAIM_OWNER, 7, request)
    assert result["authorized"] and result["purpose"] == "diagnostic_test" and result["confirmed"] is True
    assert result["diagnostic_request_id"] == str(REQUEST) and result["self_phone"] == OWN
    assert result["recipient_mode"] == "self" and result["payload"] == {"phone": OWN, "text": api.TEST_TEXT}
    assert len([sql for sql, _ in db.calls if sql.startswith("UPDATE")]) == 1
    assert trusted == [(TENANT, {"feature": "diagnostics.manage"})]


@pytest.mark.asyncio
async def test_running_diagnostic_recheck_uses_same_claim_and_has_no_writes(trusted):
    outbound = effect(status="running", attempts=1, authorized_at=NOW, claimed_owner=CLAIM_OWNER, claimed_fence=7)
    db = DB(admitted(), outbound)
    result = await api.authorize_diagnostic_effect(db, device(), outbound, CLAIM_OWNER, 7,
        EffectRequest(operation_id=EFFECT, recheck=True))
    assert result["authorized"]
    assert all(sql.startswith("SELECT") for sql, _ in db.calls)
    assert outbound["attempts"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [{"claimed_owner": uuid4()}, {"claimed_fence": 8}, {"attempts": 2}, {"status": "accepted"}])
async def test_foreign_or_terminal_claim_cannot_reauthorize(trusted, change):
    outbound = effect(status="running", attempts=1, authorized_at=NOW, claimed_owner=CLAIM_OWNER, claimed_fence=7) | change
    db = DB(admitted(), outbound)
    result = await api.authorize_diagnostic_effect(db, device(), outbound, CLAIM_OWNER, 7,
        EffectRequest(operation_id=EFFECT, recheck=True))
    assert result["authorized"] is False and db.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("part,change", [
    ("request", {"actor_account_id": uuid4()}), ("request", {"generation": uuid4()}),
    ("request", {"settings_revision": 4}), ("request", {"confirmed_at": None}),
    ("effect", {"payload": {"phone": "5567999999903", "text": api.TEST_TEXT}}),
    ("effect", {"expires_at": NOW}), ("effect", {"purpose": "absence"}),
    ("device", {"runtime": {"phone": "5567999999903", "phone_verified": True}}),
    ("device", {"runtime": {"phone": OWN, "phone_verified": False}}),
    ("device", {"removed_at": NOW}), ("device", {"revision": 4}),
])
async def test_authority_rejects_changed_snapshot_context_or_identity(trusted, part, change):
    operation, outbound, row = admitted(), effect(), device()
    {"request": operation, "effect": outbound, "device": row}[part].update(change)
    db = DB(operation, outbound)
    assert await api.diagnostic_authority(db, row, outbound, NOW) is None
    assert all(sql.startswith("SELECT") for sql, _ in db.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("state,public", [("accepted", "succeeded"), ("not_sent", "failed"), ("uncertain", "uncertain"), ("running", "running")])
async def test_recovery_projects_transport_outcome_without_writing_ledger(state, public):
    original = admitted()
    outbound = effect(status=state, attempts=1, authorized_at=NOW)
    db = DB(original, outbound)
    saved = deepcopy(original)
    dto = await api.operation_dto(db, original, NOW, replayed=True)
    assert dto["status"] == public and dto["replayed"] is True
    assert dto["message"]["status"] != "delivered" and dto["message"]["effect_id"] == EFFECT
    assert original == saved and all(sql.startswith("SELECT") for sql, _ in db.calls)


def proxy_operation(**overrides):
    return intention(kind="proxy_test", snapshot={"configuration_version": "b"*64}, confirmation_version=None,
        status="running", execution_expires_at=NOW+timedelta(seconds=58), expires_at=NOW+timedelta(seconds=58),
        started_at=NOW-timedelta(seconds=1), claim_expires_at=NOW+timedelta(seconds=29), claimed_owner=CLAIM_OWNER,
        claim_token=uuid4(), claim_fence=1) | overrides


def proxy_result(**overrides):
    return ProxyResult.model_validate({"scope": "configured_proxy", "configuration_version": "b"*64,
        "applied_to_session": None, "state": "reachable", "latency_ms": 25, "target": "tls_reference",
        "observed_at": NOW, "error_code": None} | overrides)


@pytest.mark.parametrize("change", [{"configuration_version": "c"*64}, {"observed_at": NOW+timedelta(minutes=1)},
    {"observed_at": NOW-timedelta(minutes=1)}, {"error_code": "PRIVATE_SECRET"}, {"latency_ms": None}, {"state": "uncertain"}])
def test_proxy_receipt_must_prove_same_scope_bounded_time_and_real_success(change):
    assert not api.valid_proxy_result(proxy_operation(), "succeeded", proxy_result(**change), NOW)


def test_proxy_success_is_bounded_probe_evidence_not_delivery():
    operation, result = proxy_operation(), proxy_result()
    assert api.valid_proxy_result(operation, "succeeded", result, NOW)
    view = api.proxy_view(result.model_dump(mode="json"))
    assert view["status"] == "reachable" and view["applied_to_session"] == "unknown"
    assert view["scope"] == "configured" and "TLS" in view["reason"]


def test_absent_proxy_cannot_be_reported_as_success():
    result = proxy_result(state="not_configured", latency_ms=None, error_code="PROXY_NOT_CONFIGURED")
    assert not api.valid_proxy_result(proxy_operation(), "succeeded", result, NOW)
    assert api.valid_proxy_result(proxy_operation(), "failed", result, NOW)


@pytest.mark.asyncio
@pytest.mark.parametrize("tenant_status", ["active", "inactive"])
async def test_proxy_grant_requires_active_tenant_without_requiring_whatsapp_lease(trusted, tenant_status):
    row = device(status="disconnected")
    operation = proxy_operation(snapshot={"configuration_version": api.configuration_version(row)})
    db = DB(operation, tenant_status=tenant_status)
    payload = ProxyAuthorize(request_id=REQUEST, device_id=DEVICE, generation=GENERATION,
        settings_revision=3, owner=CLAIM_OWNER, claim_token=uuid4())
    if tenant_status == "active":
        await api.proxy_authority(db, row, operation, payload, NOW)
    else:
        with pytest.raises(HTTPException) as rejected:
            await api.proxy_authority(db, row, operation, payload, NOW)
        assert rejected.value.status_code == 403 and rejected.value.detail["code"] == "DIAGNOSTIC_REVOKED"
    assert all(sql.startswith("SELECT") for sql, _ in db.calls)


@pytest.mark.parametrize("body", [{"recheck": True}, {"recheck": False, "claim_fence": 1}])
def test_proxy_recheck_requires_existing_fence_and_first_claim_cannot_forge_one(body):
    with pytest.raises(ValidationError):
        ProxyAuthorize(request_id=REQUEST, device_id=DEVICE, generation=GENERATION,
            settings_revision=3, owner=CLAIM_OWNER, claim_token=uuid4(), **body)


@pytest.mark.asyncio
@pytest.mark.parametrize("data", [None, [], "raw-private-body", {"recorded": True}, {"recorded": False}])
async def test_malformed_private_receipt_is_not_a_public_result(monkeypatch, data):
    async def read(*args, **kwargs): return data
    monkeypatch.setattr(api.channels, "_bridge_control", read)
    assert await api.read_proxy_receipt({}, proxy_operation()) is None


@pytest.mark.asyncio
async def test_expired_proxy_claim_is_unknown_without_reprobe_or_database_write():
    operation = proxy_operation(claim_expires_at=NOW-timedelta(seconds=1))
    db = DB(operation)
    before = deepcopy(operation)
    dto = await api.operation_dto(db, operation, NOW)
    assert dto["status"] == "uncertain" and dto["proxy"]["status"] == "uncertain"
    assert dto["proxy"]["observed_at"] is None and dto["proxy"]["latency_ms"] is None
    assert operation == before and db.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["", "http://127.0.0.1:3000", "http://proxy.railway.internal:3000/redirect", "http://u:p@proxy.railway.internal:3000", "https://example.com/"])
async def test_health_reads_only_the_fixed_private_bridge_destination(monkeypatch, url):
    monkeypatch.setenv("QR_BRIDGE_URL", url)
    def forbidden(*args, **kwargs): pytest.fail("Invalid destination must not create an HTTP client")
    monkeypatch.setattr(api.httpx, "AsyncClient", forbidden)
    result = await api.read_server()
    assert result["state"] == "unknown" and result["latency_ms"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("path", [f"/{DEVICE}/test-messages/prepare", f"/{DEVICE}/test-messages/{REQUEST}/confirm", f"/{DEVICE}/proxy-tests"])
async def test_owner_authentication_precedes_body_decoding(monkeypatch, path):
    @asynccontextmanager
    async def denied(*args, **kwargs):
        raise HTTPException(403, "Owner required")
        yield
    monkeypatch.setattr(api, "owner_transaction", denied)
    app = FastAPI()
    # Avoid inheriting plan dependencies attached to global route objects by
    # unrelated app.main collection; these tests exercise their own authority.
    for route in api.router.routes:
        if isinstance(route, APIRoute):
            app.add_api_route(route.path, route.endpoint, methods=list(route.methods))
    app.dependency_overrides[authenticate] = lambda: ACTOR
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic") as client:
        response = await client.post(api.BASE.replace("{tenant_id}", str(TENANT))+path,
            content=b"invalid and private JSON", headers={"Content-Type": "application/json"})
    assert response.status_code == 403 and "private" not in response.text


@pytest.mark.asyncio
async def test_report_keeps_invalid_stored_runtime_unknown_and_executes_only_selects(monkeypatch):
    row = device(runtime={"status": "unexpected-provider-detail"})
    db = DB()
    @asynccontextmanager
    async def owned(*args, **kwargs): yield db
    async def scope(*args, **kwargs): return row, {"enabled": True}
    async def offline(*args): return None
    async def health(): return {"state": "unavailable", "latency_ms": None, "observed_at": NOW, "measurement": "api_to_bridge_health"}
    def forbidden(*args, **kwargs): pytest.fail("A report cannot mutate devices or execute effects")
    monkeypatch.setattr(api, "owner_transaction", owned)
    monkeypatch.setattr(api, "scope", scope)
    monkeypatch.setattr(api, "read_status", offline)
    monkeypatch.setattr(api, "read_server", health)
    monkeypatch.setattr(api.devices, "_refresh", forbidden)
    monkeypatch.setattr(api.devices, "device_event", forbidden)
    monkeypatch.setattr(api.devices, "pending_effects", forbidden)
    response = Response()
    result = await api.diagnostics_report(TENANT, DEVICE, response, ACTOR)
    assert result["connection"]["state"] == "unknown" and result["connection"]["freshness"] == "stale"
    assert result["server"]["latency_ms"] is None and result["recent_messages"] == {"limit": 3, "items": []}
    assert result["proxy"]["mode"] == "direct" and result["proxy"]["can_test"] is False
    assert result["test_message"]["can_prepare"] is False and result["test_message"]["default_recipient_e164"] is None
    assert response.headers["Cache-Control"] == "no-store" and all(sql.startswith("SELECT") for sql, _ in db.calls)


def test_shared_fixture_has_one_key_and_the_fixed_server_text():
    assert FIXTURE["prepare_request"]["idempotency_key"] == FIXTURE["confirm_request"]["idempotency_key"]
    assert FIXTURE["prepare_response"]["preview"]["text"] == api.TEST_TEXT
    assert FIXTURE["prepare_response"]["message"] is None
    ConfirmMessage.model_validate(FIXTURE["confirm_request"])
    assert FIXTURE["detail_response"]["recent_messages"]["limit"] == 3
