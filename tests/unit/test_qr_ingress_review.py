"""Real panel authentication and the narrowly bound no-replay operator proxy."""
import hashlib
import json
from copy import deepcopy
from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest
from fastapi import HTTPException

from app import panel_auth, qr_ingress_review as review
from app.main import app

ACTOR = UUID("a0f20f4f-fb32-4999-874d-d2916cf1af63")
TENANT = UUID("4a895e77-2aa6-4b03-97c6-dbe427c238b8")
CHANNEL = UUID("284722f9-1a2d-4996-a390-447e41ac342d")
OWNER = UUID("cc102ac0-bc89-4ea2-a963-a4bb902f3d89")
OPERATION = "3570002d-8506-42cf-b1d2-8f0371ad77dd"
TRACE = "a" * 20
CSRF = "synthetic-session-csrf"
SCOPE = dict(tenant_id=str(TENANT), channel_id=str(CHANNEL), session_digest="b" * 64,
             auth_epoch="c" * 64, owner=str(OWNER), fence=7)
BINDING = {key: value for key, value in SCOPE.items() if key != "auth_epoch"}
DRY = dict(version=1, mode="dry_run", operation_id=OPERATION, traces=[])
RESULT = dict(version=1, mode="dry_run", scope=SCOPE, snapshot_sha256="d" * 64,
              entries=[dict(trace=TRACE, fingerprint="e" * 64, at=1794000000000, sequence=1,
                            state="blocked", blocked_reason="business_started", phase=None,
                            eligible_as=["operator_attested_prepare_failed"])],
              total_entries=1, truncated=False, mutation=False)
APPLY = dict(version=1, mode="apply", operation_id=OPERATION,
             expected=dict(scope=SCOPE, snapshot_sha256="d" * 64),
             entries=[dict(trace=TRACE, fingerprint="e" * 64, disposition="do_not_replay",
                           evidence=dict(kind="operator_attested_prepare_failed", receipt_sha256="f" * 64,
                                         build_sha="1" * 40, event_at=1794000000050))])
APPLIED = dict(version=1, mode="apply", operation_id=OPERATION, replayed=False, mutation=True,
               before_snapshot_sha256="d" * 64, after_snapshot_sha256="2" * 64,
               after_entries_sha256="3" * 64,
               backup=dict(reference=OPERATION, sha256="4" * 64, bytes=2048), review_required=True)


@pytest.fixture
def auth(monkeypatch):
    claims = dict(role="admin", account_id=str(ACTOR), auth_provider="legacy_admin", auth_version=1, csrf=CSRF)
    class Store:
        async def get(self, key):
            return json.dumps(claims) if key == panel_auth._hash_cookie("a" * 43) else None
    monkeypatch.setattr(panel_auth, "_request_redis", lambda request: (Store(), False))
    from app import legacy_admin_identity, account_auth
    async def identity(account, version):
        assert account == str(ACTOR) and version == 1
        return dict(name="Synthetic platform owner")
    async def account(*args):
        return dict(role=claims["role"], name="Synthetic member", email="owner@example.invalid", verified_at=True)
    monkeypatch.setattr(legacy_admin_identity, "current_identity", identity)
    monkeypatch.setattr(account_auth, "current_account", account)
    return claims


@pytest.fixture
def backend(monkeypatch):
    calls = []
    async def binding():
        calls.append("binding")
        return deepcopy(BINDING)
    async def private(body, actor):
        calls.append((body, actor))
        return deepcopy(RESULT if body["mode"] == "dry_run" else APPLIED)
    monkeypatch.setattr(review, "_primary_binding", binding)
    monkeypatch.setattr(review, "_private_review", private)
    return calls


def client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test",
                            cookies={panel_auth.COOKIE: "a" * 43})


@pytest.mark.asyncio
async def test_actual_platform_session_csrf_and_origin_are_required_before_proxy(auth, backend):
    async with client() as web:
        missing = await web.post(review.PUBLIC_PATH, json=DRY)
        assert missing.status_code == 403
        foreign = await web.post(review.PUBLIC_PATH, json=DRY, headers={"X-Orion-CSRF": CSRF, "Origin": "https://foreign.invalid"})
        assert foreign.status_code == 403
        assert backend == []
        okay = await web.post(review.PUBLIC_PATH, json=DRY, headers={"X-Orion-CSRF": CSRF, "Origin": "https://test"})
        assert okay.status_code == 200 and okay.json() == RESULT
        assert okay.headers["cache-control"] == "no-store"
        assert backend == ["binding", (DRY, ACTOR)]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test") as web:
        assert (await web.post(review.PUBLIC_PATH, json=DRY, headers={"Authorization": "Bearer synthetic-support-token"})).status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("role,provider", [("support", None), ("member", "password"), ("admin", "password")])
async def test_support_tenant_owner_and_unverified_admin_cannot_proxy(auth, backend, role, provider, monkeypatch):
    monkeypatch.delenv("PANEL_OWNER_EMAIL", raising=False)
    auth.update(role=role, auth_provider=provider)
    async with client() as web:
        response = await web.post(review.PUBLIC_PATH, json=DRY, headers={"X-Orion-CSRF": CSRF})
    assert response.status_code in {401, 403} and backend == []


@pytest.mark.asyncio
async def test_revoked_platform_identity_cannot_reuse_redis_claim(auth, backend, monkeypatch):
    from app import legacy_admin_identity
    async def revoked(*args):
        raise HTTPException(401, "Sessão expirada")
    monkeypatch.setattr(legacy_admin_identity, "current_identity", revoked)
    async with client() as web:
        response = await web.post(review.PUBLIC_PATH, json=DRY, headers={"X-Orion-CSRF": CSRF})
    assert response.status_code == 401 and backend == []


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {**DRY, "actor_id": str(ACTOR)}, {**DRY, "tenant_id": str(TENANT)},
    {**DRY, "path": "/internal/disconnect"}, {**DRY, "headers": {"X-Orion-Bridge-Key": "injected"}},
    {**DRY, "url": "http://foreign.invalid"}, {**DRY, "version": True},
    {**DRY, "traces": [TRACE, TRACE]}, {**DRY, "traces": [TRACE, "b" * 20, "c" * 20]},
    {**APPLY, "entries": []}, {**APPLY, "entries": [APPLY["entries"][0]] * 2},
    {**APPLY, "entries": [{**APPLY["entries"][0], "disposition": "replay"}]},
])
async def test_closed_request_rejects_extra_authority_replay_and_invalid_selection(auth, backend, payload):
    async with client() as web:
        response = await web.post(review.PUBLIC_PATH, json=payload, headers={"X-Orion-CSRF": CSRF})
    assert response.status_code == 422 and backend == []


@pytest.mark.asyncio
@pytest.mark.parametrize("content,status", [
    ('{"version":1,"mode":"dry_run","operation_id":"'+OPERATION+'","traces":[],"mode":"apply"}', 422),
    ('{"x":NaN}', 422), ("x" * (review.BODY_LIMIT + 1), 413),
])
async def test_duplicate_json_keys_non_json_numbers_and_oversize_body_fail_closed(auth, backend, content, status):
    async with client() as web:
        response = await web.post(review.PUBLIC_PATH, content=content, headers={"X-Orion-CSRF": CSRF, "Content-Type": "application/json"})
    assert response.status_code == status and backend == []


@pytest.mark.asyncio
@pytest.mark.parametrize("key,value", [("tenant_id", str(ACTOR)), ("channel_id", str(ACTOR)),
                                       ("session_digest", "0" * 64), ("owner", str(ACTOR)), ("fence", 8)])
async def test_apply_revalidates_server_primary_scope_before_private_call(auth, backend, key, value):
    payload = deepcopy(APPLY)
    payload["expected"]["scope"][key] = value
    async with client() as web:
        response = await web.post(review.PUBLIC_PATH, json=payload, headers={"X-Orion-CSRF": CSRF})
    assert response.status_code == 409 and backend == ["binding"]


@pytest.mark.asyncio
async def test_apply_forwards_exact_frozen_snapshot_and_server_actor_only(auth, backend):
    async with client() as web:
        response = await web.post(review.PUBLIC_PATH, json=APPLY, headers={"X-Orion-CSRF": CSRF})
    assert response.status_code == 200 and response.json() == APPLIED
    assert backend == ["binding", (APPLY, ACTOR)]


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["secret", "scope", "mutation", "duplicate", "wrong_operation", "no_review", "wrong_backup", "wrong_before"])
async def test_response_allowlist_scope_and_operation_correlation_hide_unexpected_data(auth, backend, monkeypatch, change):
    result = deepcopy(RESULT if change not in {"wrong_operation", "no_review", "wrong_backup", "wrong_before"} else APPLIED)
    payload = DRY
    if change == "secret": result["private_key"] = "must-never-reach-browser"
    if change == "scope": result["scope"]["tenant_id"] = str(ACTOR)
    if change == "mutation": result["mutation"] = 0
    if change == "duplicate": result["entries"] *= 2
    if change == "wrong_operation": result["operation_id"] = str(ACTOR); payload = APPLY
    if change == "no_review": result["review_required"] = False; payload = APPLY
    if change == "wrong_backup": result["backup"]["reference"] = str(ACTOR); payload = APPLY
    if change == "wrong_before": result["before_snapshot_sha256"] = "0" * 64; payload = APPLY
    async def private(*args): return result
    monkeypatch.setattr(review, "_private_review", private)
    async with client() as web:
        response = await web.post(review.PUBLIC_PATH, json=payload, headers={"X-Orion-CSRF": CSRF})
    assert response.status_code in {409, 503}
    assert "must-never-reach-browser" not in response.text


def test_python_operator_signature_matches_node_canonical_vector():
    headers = review._assertion(DRY, ACTOR, "synthetic-ingress-review-master-secret-123456", at=1794000000000)
    canonical = b'{"mode":"dry_run","operation_id":"3570002d-8506-42cf-b1d2-8f0371ad77dd","traces":[],"version":1}'
    assert review._canonical(DRY) == canonical
    assert headers["X-Orion-Ops-Actor"] == str(ACTOR)
    assert headers["X-Orion-Ops-At"] == "1794000000000"
    assert len(headers["X-Orion-Ops-Signature"]) == 64
    # Kept as the same fixed vector in ingress-review-auth.test.mjs.
    assert hashlib.sha256(canonical).hexdigest() == "5b4b5950c80106d067a6c500c99a4ec5300c6881d48b7cb30d0a9380db420ec5"
    assert headers["X-Orion-Ops-Signature"] == "b370d34771c4d0849ad7cb2ee234afa443ae2b95308a30cd1f2c9514a9176a63"


@pytest.mark.asyncio
async def test_actual_primary_binding_does_not_require_enabling_paused_assistant(monkeypatch):
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    row = dict(id=CHANNEL, tenant_id=TENANT, lease_owner=OWNER, fence=7, server_now=now,
               lease_expires_at=now+timedelta(seconds=25), request_fingerprint="b" * 64, assistant_enabled=False)
    queries = []
    class DB:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def execute(self, query, params):
            queries.append((str(query), params))
            return SimpleNamespace(mappings=lambda: SimpleNamespace(one_or_none=lambda: row))
    monkeypatch.setattr(review.qr_legacy, "configured_tenant", lambda: TENANT)
    monkeypatch.setattr(review.qr_legacy, "channel_id", lambda tenant: CHANNEL)
    monkeypatch.setattr(review.qr_channels, "enabled", lambda: None)
    monkeypatch.setattr(review.qr_channels, "support_session_factory", lambda: DB)
    async def verified(actual): return actual is row
    monkeypatch.setattr(review.qr_channels, "_legacy_binding_valid", verified)
    assert await review._primary_binding() == BINDING
    assert queries[0][1] == {"tenant": TENANT, "channel": CHANNEL}
    assert "AND c.assistant_enabled" not in queries[0][0]
    assert row["assistant_enabled"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["okay", "redirect", "unknown_error", "known_error", "oversize", "malformed"])
async def test_private_http_is_literal_bounded_no_redirect_and_signs_server_identity(monkeypatch, case):
    real_client = httpx.AsyncClient
    monkeypatch.setenv("QR_BRIDGE_URL", "http://qr-synthetic.railway.internal:3000")
    secret = "synthetic-ingress-review-master-secret-123456"
    monkeypatch.setenv("QR_BRIDGE_TOKEN", secret)
    requests = []
    async def upstream(request):
        requests.append(request)
        assert str(request.url) == "http://qr-synthetic.railway.internal:3000" + review.PRIVATE_PATH
        assert request.method == "POST" and request.content == review._canonical(DRY)
        assert request.headers["x-orion-bridge-key"] == secret
        expected = review._assertion(DRY, ACTOR, secret, at=int(request.headers["x-orion-ops-at"]))
        assert all(request.headers[key] == value for key, value in expected.items())
        if case == "redirect": return httpx.Response(307, headers={"Location": "https://foreign.invalid/leak"}, content=b"")
        if case == "unknown_error": return httpx.Response(500, json={"error": secret, "payload": secret})
        if case == "known_error": return httpx.Response(409, json={"error": "ingress_review_snapshot_conflict", "payload": secret})
        if case == "oversize": return httpx.Response(200, content=b"x" * (review.RESPONSE_LIMIT + 1))
        if case == "malformed": return httpx.Response(200, content=b'{"x":1,"x":2}')
        return httpx.Response(200, json=RESULT)
    def private_client(**options):
        assert options["trust_env"] is False and options["follow_redirects"] is False
        assert options["timeout"] == 18
        return real_client(transport=httpx.MockTransport(upstream), **options)
    monkeypatch.setattr(review, "httpx", SimpleNamespace(AsyncClient=private_client, RequestError=httpx.RequestError))
    if case == "okay":
        assert await review._private_review(DRY, ACTOR) == RESULT
    else:
        with pytest.raises(HTTPException) as error:
            await review._private_review(DRY, ACTOR)
        assert error.value.status_code == (409 if case == "known_error" else 503)
        assert secret not in str(error.value.detail)
    assert len(requests) == 1
