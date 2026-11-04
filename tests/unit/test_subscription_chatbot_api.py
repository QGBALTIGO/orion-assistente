"""Request and permission boundaries for the public Chatbot API.

All storage/ownership/feature dependencies are synthetic. PostgreSQL policy,
locks, graph execution and real external side effects are covered elsewhere.
"""
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
import json
from pathlib import Path
import sys
from types import ModuleType
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException
from fastapi.routing import APIRoute
import httpx
from pydantic import Field
import pytest

from app import catalog_api, plan_permissions, plan_routing
from app import subscription_chatbot as api, subscription_chatbot_schemas as schemas
from app import subscription_chatbot_store as store
from app.catalog_api import Actor, authenticate
from app.catalog_schemas import StrictModel

CONTRACT = json.loads((Path(__file__).parents[1] / "fixtures/subscription_chatbot_contract.json").read_text())
TENANT = UUID("10000000-0000-4000-8000-000000000001")
OTHER_TENANT = UUID("10000000-0000-4000-8000-000000000002")
ACCOUNT = UUID("90000000-0000-4000-8000-000000000001")
RULE = UUID(CONTRACT["detail_response"]["rule"]["id"])
BASE = f"/api/v1/tenants/{TENANT}/subscription-whatsapp/chatbot"
OWNER = Actor("member", TENANT, ACCOUNT, "owner")
SECRET = "SYNTHETIC_PRIVATE_SENTINEL_DO_NOT_REFLECT"


class Body(StrictModel):
    value: str = Field(min_length=1, max_length=100)


class StreamingRequest:
    def __init__(self, *chunks, headers=None, failure=None):
        self.headers = {"content-type": "application/json", **(headers or {})}
        self.chunks, self.failure, self.reads = chunks, failure, 0

    async def stream(self):
        for chunk in self.chunks:
            self.reads += 1
            yield chunk
        if self.failure:
            raise self.failure


def create_payload():
    return {"expected_chatbot_revision": 1, "idempotency_key": str(uuid4()),
            "rule": deepcopy(CONTRACT["rule_defaults"])}


async def parse(payload, model=schemas.CreateRule):
    raw = json.dumps(payload, ensure_ascii=True).encode()
    return await api.bounded_json(StreamingRequest(raw), model)


@pytest.mark.parametrize("content_type", ["application/json", "application/json; charset=utf-8", " Application/JSON ; Charset=UTF-8"])
async def test_json_content_type_and_exact_byte_limit_accept_chunked_utf8(content_type):
    raw = '{"value":"ação 😀"}'.encode()
    request = StreamingRequest(raw[:14], raw[14:17], raw[17:], headers={"content-type": content_type})
    result = await api.bounded_json(request, Body, limit=len(raw))
    assert result.value == "ação 😀" and request.reads == 3


@pytest.mark.parametrize("content_type", ["", "text/plain", "application/jsonp", "application/x-www-form-urlencoded", "multipart/form-data"])
async def test_unsupported_content_type_is_refused_before_consuming_bytes(content_type):
    request = StreamingRequest(SECRET.encode(), headers={"content-type": content_type})
    with pytest.raises(HTTPException) as error:
        await api.bounded_json(request, Body)
    assert error.value.status_code == 415 and request.reads == 0
    assert SECRET not in str(error.value.detail)


@pytest.mark.parametrize("declared", ["-1", "+4", "1.5", "abc", "1234567890", "１２", " 4", "1001"])
async def test_invalid_or_oversized_declared_length_fails_before_read(declared):
    request = StreamingRequest(b'{"value":"ok"}', headers={"content-length": declared})
    with pytest.raises(HTTPException) as error:
        await api.bounded_json(request, Body, limit=1000)
    assert error.value.status_code == 413 and request.reads == 0


@pytest.mark.parametrize("declared", [None, "1", "20"])
async def test_actual_stream_budget_is_authoritative_even_when_length_is_missing_or_false(declared):
    headers = {} if declared is None else {"content-length": declared}
    request = StreamingRequest(b'{"value":"', SECRET.encode(), b'"}', b"not consumed", headers=headers)
    with pytest.raises(HTTPException) as error:
        await api.bounded_json(request, Body, limit=20)
    assert error.value.status_code == 413 and request.reads == 2
    assert SECRET not in str(error.value.detail)


@pytest.mark.parametrize("raw", [b'{"value":"one","value":"two"}',
    b'{"value":"ok","nested":{"key":1,"key":2}}',
    b'{"value":NaN}', b'{"value":Infinity}', b'{"value":-Infinity}',
    b'{"value":"\xff"}', b'{broken', b'', b'null', b'[]'])
async def test_malformed_duplicate_nonfinite_invalid_utf8_or_wrong_root_is_422(raw):
    with pytest.raises(HTTPException) as error:
        await api.bounded_json(StreamingRequest(raw), Body)
    assert error.value.status_code == 422


async def test_deep_json_is_refused_without_echoing_the_request_or_recursion_trace():
    raw = b'{"value":' + b'[' * 1200 + SECRET.encode() + b']' * 1200 + b'}'
    with pytest.raises(HTTPException) as error:
        await api.bounded_json(StreamingRequest(raw), Body)
    assert error.value.status_code == 422
    assert SECRET not in str(error.value.detail) and "Recursion" not in str(error.value.detail)


@pytest.mark.parametrize("bad", ["tag", "extra", "uuid", "keyword", "url", "root"])
async def test_validation_errors_never_reflect_payload_values_unknown_keys_or_private_urls(bad):
    payload = create_payload()
    if bad == "tag":
        payload["rule"]["response"]["actions"][0]["type"] = SECRET
    elif bad == "extra":
        payload["rule"][SECRET] = "private value"
    elif bad == "uuid":
        payload["idempotency_key"] = SECRET
    elif bad == "keyword":
        payload["rule"]["keyword"] = SECRET * 20
    elif bad == "url":
        payload["rule"]["response"]["actions"][0] = {"type": "url", "url": f"https://user:{SECRET}@example.com/reply"}
    else:
        payload = [SECRET]
    with pytest.raises(HTTPException) as error:
        await parse(payload)
    assert error.value.status_code == 422
    public_error = json.dumps(error.value.detail, ensure_ascii=False)
    assert SECRET not in public_error and "private value" not in public_error
    assert "https://user:" not in public_error and "input" not in public_error


async def test_timeout_is_bounded_and_cancellation_is_never_retried_or_hidden():
    timed_out = StreamingRequest(b'{"value":', failure=TimeoutError(SECRET))
    with pytest.raises(HTTPException) as error:
        await api.bounded_json(timed_out, Body)
    assert error.value.status_code == 408 and timed_out.reads == 1
    assert SECRET not in str(error.value.detail)
    cancelled = StreamingRequest(b'{"value":', failure=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await api.bounded_json(cancelled, Body)
    assert cancelled.reads == 1


async def test_valid_payload_keeps_only_model_fields_and_does_not_alter_input():
    payload = create_payload()
    before = deepcopy(payload)
    result = await parse(payload)
    assert result.expected_chatbot_revision == 1 and result.rule.keyword == payload["rule"]["keyword"]
    assert payload == before


PUBLIC_ROUTES = [(route.path.removeprefix(plan_routing.TENANT_PREFIX), method)
    for route in api.router.routes if isinstance(route, APIRoute) for method in sorted(route.methods)]


@pytest.mark.parametrize("relative,method", PUBLIC_ROUTES)
def test_every_public_route_has_explicit_read_or_write_policy(relative, method):
    expected = "account.manage" if method == "GET" or method == "POST" and relative.endswith("/chatbot/exports") else "chatbot.manage"
    assert plan_routing.feature_for_path(relative, method) == expected
    assert plan_routing.subscription_workspace_route(relative, method, {})


@pytest.mark.parametrize("relative", ["/subscription-whatsapp/chatbot-other", "/subscription-whatsapp/chatbots", "/subscription-whatsapp/unknown"])
def test_similar_prefixes_do_not_inherit_chatbot_access(relative):
    assert plan_routing.feature_for_path(relative, "POST") is None
    assert not plan_routing.subscription_workspace_route(relative, "POST", {})


def test_the_paid_plan_tiers_do_not_open_chatbot_for_other_niches():
    from app.plan_catalog import PLAN_FEATURES
    for feature_set in PLAN_FEATURES.values():
        assert "chatbot.manage" not in feature_set
    for niche in [None, "barbearia", "outro"]:
        state = plan_permissions.derive_plan({"tenant_status": "active", "niche": niche})
        assert not state["permissions"]["chatbot.manage"]
    allowed = plan_permissions.derive_plan({"tenant_status": "active", "niche": "servico_assinaturas"})
    paused = plan_permissions.derive_plan({"tenant_status": "paused", "niche": "servico_assinaturas"})
    assert allowed["permissions"]["chatbot.manage"]
    assert not paused["permissions"]["chatbot.manage"] and paused["permissions"]["account.manage"]


class Scalar:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class FakeDatabase:
    def __init__(self, status, events):
        self.status, self.events = status, events

    @asynccontextmanager
    async def begin(self):
        self.events.append("begin")
        try:
            yield
        finally:
            self.events.append("end")

    async def execute(self, _statement, params):
        assert params["t"] == TENANT
        self.events.append("tenant-status")
        return Scalar(self.status)


@pytest.mark.parametrize("profile,grant,write,status,owner,allowed", [
    ("subscription_services", True, True, "active", True, True),
    ("subscription_services", False, True, "active", True, False),
    ("subscription_services", False, False, "active", True, True),
    ("subscription_services", False, False, "paused", True, True),
    ("subscription_services", True, True, "paused", True, False),
    ("subscription_services", True, False, None, True, False),
    ("standard", True, False, "active", True, False),
    ("standard", True, True, "active", True, False),
    ("subscription_services", True, True, "active", False, False),
    ("subscription_services", True, False, "active", False, False),
])
async def test_owner_workspace_and_current_tenant_status_gate_the_transaction(monkeypatch, profile, grant, write, status, owner, allowed):
    events = []
    db = FakeDatabase(status, events)

    @asynccontextmanager
    async def session():
        yield db

    async def state(database, tenant, lock):
        assert database is db and tenant == TENANT and lock == "shared"
        events.append("workspace")
        return {"workspace_profile": profile, "permissions": {"chatbot.manage": grant}}

    async def check_owner(database, tenant, actor):
        assert database is db and tenant == TENANT and actor == OWNER
        events.append("owner")
        if not owner:
            raise HTTPException(403, "Owner revoked")

    monkeypatch.setattr(store, "support_session_factory", lambda: session)
    monkeypatch.setattr(store, "plan_state", state)
    monkeypatch.setattr(store.channels, "_owner", check_owner)

    async def attempt():
        async with store.owner_transaction(TENANT, OWNER, write=write) as current:
            assert current is db
            events.append("entered")

    if allowed:
        await attempt()
        assert events == ["begin", "workspace", "owner", "tenant-status", "entered", "end"]
    else:
        with pytest.raises(HTTPException) as error:
            await attempt()
        assert error.value.status_code == 403 and "entered" not in events
        assert events[-1] == "end"


@pytest.fixture
def isolated_api(monkeypatch):
    """Real FastAPI routing and feature dependency, synthetic domain handlers."""
    state = {"actor": OWNER, "grants": {"account.manage", "chatbot.manage"}, "events": [], "authenticated": True}
    app = FastAPI()
    plan_routing.include_plan_router(app, api.router)

    async def auth():
        state["events"].append("auth")
        if not state["authenticated"]:
            raise HTTPException(401, "Entre novamente")
        return state["actor"]

    async def require(tenant, feature):
        assert tenant == TENANT
        state["events"].append(("feature", feature))
        if feature not in state["grants"]:
            raise HTTPException(403, "Função indisponível")
        return {"workspace_profile": "subscription_services", "permissions": {key: True for key in state["grants"]}}

    @asynccontextmanager
    async def owner_transaction(tenant, actor, *, write=True):
        state["events"].append(("owner", write))
        if actor.account_id != ACCOUNT or actor.tenant_id != TENANT or actor.membership_role != "owner":
            raise HTTPException(403, "Somente o proprietário")
        yield object()

    async def read_catalog(db, tenant, actor):
        state["events"].append("catalog")
        return {"chatbot_revision": 1, "capabilities": {"manage": "chatbot.manage" in state["grants"]}}

    async def mutate(tenant, actor, kind, payload, rule_id=None):
        state["events"].append(("mutation", kind, rule_id))
        async with owner_transaction(tenant, actor):
            return {"kind": kind, "revision": payload.expected_chatbot_revision}

    # Archive implementation is tested separately; no storage module is loaded
    # here simply to verify route/dependency order and the read-only POST policy.
    archive = ModuleType("app.subscription_chatbot_archive")

    async def export(tenant, actor, payload):
        async with owner_transaction(tenant, actor, write=False):
            state["events"].append("export")
            return {"selected": len(payload.ids)}

    async def preview_import(tenant, actor, payload):
        state["events"].append("import-preview")
        return {"mode": payload.mode}

    archive.reserve_export = export
    archive.preview_import = preview_import
    monkeypatch.setitem(sys.modules, "app.subscription_chatbot_archive", archive)
    monkeypatch.setattr(plan_routing, "require_tenant_feature", require)
    monkeypatch.setattr(store, "owner_transaction", owner_transaction)
    monkeypatch.setattr(api, "catalog", read_catalog)
    monkeypatch.setattr(api, "mutate_rule", mutate)
    app.dependency_overrides[authenticate] = auth
    return app, state


async def send(app, method, path, **kwargs):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test") as client:
        return await client.request(method, path, **kwargs)


async def test_http_catalog_read_and_export_remain_available_without_mutation_grant(isolated_api):
    app, state = isolated_api
    state["grants"] = {"account.manage"}
    response = await send(app, "GET", BASE + "/catalog")
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert response.json()["capabilities"]["manage"] is False
    response = await send(app, "POST", BASE + "/exports", json={"ids": [str(RULE)], "format": "json"})
    assert response.status_code == 200 and response.json() == {"selected": 1}
    response = await send(app, "POST", BASE + "/rules", json=create_payload())
    assert response.status_code == 403
    assert ("owner", False) in state["events"] and ("owner", True) not in state["events"]
    assert not any(isinstance(event, tuple) and event[0] == "mutation" for event in state["events"])
    assert plan_permissions._scope.get() is None


async def test_http_read_still_requires_account_feature(isolated_api):
    app, state = isolated_api
    state["grants"] = {"chatbot.manage"}
    response = await send(app, "GET", BASE + "/catalog")
    assert response.status_code == 403 and "catalog" not in state["events"]


@pytest.mark.parametrize("membership", ["editor", "viewer"])
async def test_an_account_membership_is_not_a_substitute_for_owner_authorization(isolated_api, membership):
    app, state = isolated_api
    state["actor"] = Actor("member", TENANT, ACCOUNT, membership)
    response = await send(app, "GET", BASE + "/catalog")
    assert response.status_code == 403 and "catalog" not in state["events"]


async def test_cross_tenant_actor_is_rejected_before_feature_or_domain_access(isolated_api):
    app, state = isolated_api
    state["actor"] = Actor("member", OTHER_TENANT, ACCOUNT, "owner")
    response = await send(app, "GET", BASE + "/catalog")
    assert response.status_code == 403
    assert not any(isinstance(event, tuple) for event in state["events"])


async def test_unauthenticated_request_does_not_consume_its_stream(isolated_api):
    app, state = isolated_api
    state["authenticated"] = False
    chunks = []

    async def stream():
        chunks.append("read")
        yield SECRET.encode()

    response = await send(app, "POST", BASE + "/rules", content=stream(), headers={"content-type": "application/json"})
    assert response.status_code == 401 and chunks == []
    assert state["events"] == ["auth"]
    assert SECRET not in response.text


async def test_large_import_checks_owner_before_consuming_body_or_calling_archive(isolated_api):
    app, state = isolated_api
    state["actor"] = Actor("member", TENANT, ACCOUNT, "editor")
    chunks = []

    async def stream():
        chunks.append("read")
        yield SECRET.encode()

    response = await send(app, "POST", BASE + "/imports/preview", content=stream(), headers={"content-type": "application/json"})
    assert response.status_code == 403 and chunks == []
    assert "import-preview" not in state["events"]


@pytest.mark.parametrize("method,path,kind,fields", [
    ("POST", "/rules", "create", {}),
    ("PUT", "/rules/" + str(RULE), "update", {"expected_revision": 1}),
    ("PATCH", "/rules/" + str(RULE) + "/enabled", "toggle", {"expected_revision": 1, "enabled": False}),
    ("POST", "/rules/" + str(RULE) + "/duplicate", "duplicate", {"expected_revision": 1}),
    ("DELETE", "/rules/" + str(RULE), "delete", {"expected_revision": 1}),
    ("POST", "/delete-all", "delete_all", {"confirm": True}),
])
async def test_versioned_routes_dispatch_the_expected_operation_after_central_write_check(isolated_api, method, path, kind, fields):
    app, state = isolated_api
    payload = create_payload()
    if kind not in {"create", "update"}:
        del payload["rule"]
    payload.update(fields)
    response = await send(app, method, BASE + path, json=payload)
    assert response.status_code == (201 if kind == "create" else 200)
    assert response.json()["kind"] == kind
    assert state["events"].index(("feature", "chatbot.manage")) < next(i for i, event in enumerate(state["events"]) if isinstance(event, tuple) and event[0] == "mutation")
    assert ("owner", True) in state["events"]
    assert plan_permissions._scope.get() is None


async def test_http_422_is_secret_free_and_invalid_input_never_reaches_mutation(isolated_api):
    app, state = isolated_api
    payload = create_payload()
    payload["rule"]["response"]["actions"][0]["type"] = SECRET
    response = await send(app, "POST", BASE + "/rules", json=payload)
    assert response.status_code == 422 and SECRET not in response.text
    assert not any(isinstance(event, tuple) and event[0] == "mutation" for event in state["events"])


@pytest.mark.parametrize("grant", [False, True])
def test_capabilities_describe_real_media_readiness_without_secrets_or_paid_plan_assumptions(monkeypatch, grant):
    from app import subscription_chatbot_media as media
    for key in ["ORION_MEDIA_S3_ENDPOINT", "ORION_MEDIA_S3_BUCKET", "ORION_MEDIA_S3_REGION",
                "ORION_MEDIA_S3_ACCESS_KEY_ID", "ORION_MEDIA_S3_SECRET_ACCESS_KEY"]:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(media, "video_available", lambda: True)
    result = api.capabilities(grant)
    assert result["manage"] is grant and result["restore"] is grant
    assert result["export"] and result["backups"]
    assert result["actions"]["text"] and not result["actions"]["sticker"] and not result["actions"]["shortvideo"]
    for key in ["ORION_MEDIA_S3_ENDPOINT", "ORION_MEDIA_S3_BUCKET", "ORION_MEDIA_S3_REGION",
                "ORION_MEDIA_S3_ACCESS_KEY_ID", "ORION_MEDIA_S3_SECRET_ACCESS_KEY"]:
        monkeypatch.setenv(key, SECRET)
    ready = api.capabilities(grant)
    assert ready["actions"]["sticker"] and ready["actions"]["shortvideo"]
    assert SECRET not in json.dumps(ready)
    monkeypatch.setattr(media, "video_available", lambda: False)
    assert not api.capabilities(grant)["actions"]["shortvideo"]
