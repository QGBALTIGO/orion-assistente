"""Exercise the real preview route and ASR adapter with generated PCM, no network/DB."""
import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from starlette.requests import Request

from app import catalog_api, groq_audio, panel_auth, qr_ai, qr_profile, tenant_access
from app.catalog_api import Actor, authenticate


WAV = (Path(__file__).parents[1] / "fixtures/audio/synthetic-price-pt.wav").read_bytes()


class MemoryRedis:
    """A transaction-aware double; use the production admission/dedupe implementation."""
    def __init__(self):
        self.values, self.locks, self.transactions = {}, {}, []
        self.closed = 0

    async def get(self, key):
        return self.values.get(key)

    async def exists(self, key):
        return key in self.values

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def delete(self, key):
        self.values.pop(key, None)

    async def aclose(self):
        self.closed += 1

    def lock(self, key, **kwargs):
        lock = self.locks.setdefault(key, asyncio.Lock())
        @asynccontextmanager
        async def held():
            async with lock:
                yield
        return held()

    def pipeline(self, transaction=False):
        owner = self
        class Pipeline:
            def __init__(self):
                self.items = []
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                return False
            def set(self, key, value, **kwargs):
                self.items.append((key, value, kwargs))
                return self
            async def execute(self):
                assert transaction is True
                # No await between the grouped operations, matching one MULTI/EXEC.
                owner.transactions.append(tuple(self.items))
                for key, value, kwargs in self.items:
                    if not kwargs.get("nx") or key not in owner.values:
                        owner.values[key] = value
                return [True] * len(self.items)
        return Pipeline()


@pytest.fixture
def preview(monkeypatch):
    import redis.asyncio as redis_asyncio
    original_client = httpx.AsyncClient
    redis = MemoryRedis()
    tenant, account = uuid4(), uuid4()
    actor = Actor("member", account_id=account)
    events, provider_calls, turns = [], [], []
    provider_payload = {"text": " Quanto custa cabelo e barba? ", "duration": 2.8}
    monkeypatch.setenv("GROQ_PREVIEW_ENABLED", "1")
    monkeypatch.setenv("QR_AI_AUDIO_ENABLED", "1")
    monkeypatch.setenv("GROQ_API_KEY", "synthetic-no-real-provider")
    monkeypatch.setenv("BUSINESS_PROFILES_ENABLED", "1")
    monkeypatch.setattr(redis_asyncio, "from_url", lambda *a, **k: redis)

    def provider(request):
        events.append("asr")
        provider_calls.append(request)
        assert request.url == "https://api.groq.com/openai/v1/audio/transcriptions"
        assert b"verbose_json" in request.content and b"whisper-large-v3-turbo" in request.content
        assert WAV in request.content, "the generated PCM file must traverse the actual multipart adapter"
        return httpx.Response(200, content=json.dumps(provider_payload), headers={"Content-Type": "application/json"})

    monkeypatch.setattr(groq_audio.httpx, "AsyncClient", lambda **kwargs: original_client(
        transport=httpx.MockTransport(provider), **kwargs))

    async def turn(t, phone, body, r, namespace, preview=False):
        events.append("turn")
        turns.append({"tenant": t, "phone": phone, "body": body,
                      "namespace": namespace, "preview": preview})
        assert r is redis
        return {"answer": "Corte e barba: R$ 55,00", "step": "catalog"}
    monkeypatch.setattr(qr_ai, "turn", turn)
    app = FastAPI()
    app.include_router(qr_ai.router)
    app.dependency_overrides[authenticate] = lambda: actor

    # Exercise the production membership resolver. A viewer membership may read
    # and simulate, but cannot select a tenant absent from this scoped relation.
    class MembershipSession:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def execute(self, statement, params):
            events.append("membership")
            assert "tenant_memberships" in str(statement) and "active" in str(statement)
            assert params["account"] == actor.account_id
            role = "viewer" if params["tenant"] == tenant else None
            return SimpleNamespace(scalar_one_or_none=lambda: role)
    monkeypatch.setattr(catalog_api, "support_session_factory", lambda: MembershipSession)
    class ProfileSession:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
    monkeypatch.setattr(qr_profile, "get_session_factory", lambda: ProfileSession)
    async def unpublished(db, t):
        events.append("profile")
        assert t == tenant
        return None
    monkeypatch.setattr(qr_profile, "published_profile_snapshot", unpublished)

    def url(t=None, request_id=None, session="audio-session-001", **params):
        query = {"session": session, "request_id": str(request_id or uuid4()), **params}
        return f"/api/v1/tenants/{t or tenant}/ai/conversation-preview/audio?" + str(httpx.QueryParams(query))
    async def post(path=None, body=WAV, media="audio/wav"):
        async with original_client(transport=httpx.ASGITransport(app=app), base_url="https://test") as client:
            return await client.post(path or url(), content=body, headers={"Content-Type": media})
    return SimpleNamespace(**locals())


@pytest.mark.asyncio
async def test_generated_wav_traverses_route_provider_and_account_scoped_engine_once(preview):
    h = preview
    request_id = uuid4()
    first = await h.post(h.url(request_id=request_id))
    second = await h.post(h.url(request_id=request_id))
    assert first.status_code == second.status_code == 200
    assert first.headers["cache-control"] == "no-store"
    assert first.json() == {"answer": "Corte e barba: R$ 55,00", "step": "catalog",
                            "transcript": "Quanto custa cabelo e barba?", "input_kind": "audio"}
    assert second.json() == {"answer": None, "step": "duplicate"}
    assert len(h.provider_calls) == len(h.turns) == 1
    assert h.events.index("membership") < h.events.index("asr") < h.events.index("turn")
    assert h.turns[0]["tenant"] == h.tenant and h.turns[0]["namespace"] == f"preview:{h.account}:audio-session-001"
    assert h.turns[0]["body"] == "Quanto custa cabelo e barba?" and h.turns[0]["preview"]
    assert len(h.redis.transactions) == 1
    assert len(h.redis.transactions[0]) == 1, "preview dedupe must not write legacy real-WA keys"
    assert "audio:" + str(request_id) in h.redis.transactions[0][0][0]


@pytest.mark.asyncio
async def test_tenant_membership_and_feature_checks_precede_any_transcription(preview, monkeypatch):
    h = preview
    assert (await h.post(h.url(t=uuid4()))).status_code == 403
    monkeypatch.setenv("QR_AI_AUDIO_ENABLED", "0")
    assert (await h.post()).status_code == 423
    monkeypatch.setenv("QR_AI_AUDIO_ENABLED", "1")
    monkeypatch.setenv("GROQ_PREVIEW_ENABLED", "0")
    assert (await h.post()).status_code == 423
    assert not h.provider_calls and not h.turns and not h.redis.transactions


@pytest.mark.asyncio
async def test_request_identity_and_disabled_profile_override_fail_before_asr(preview, monkeypatch):
    h = preview
    for query in ({"session": "x", "request_id": str(uuid4())},
                  {"session": "audio-session-001", "request_id": "not-a-uuid"},
                  {"session": "audio-session-001"}):
        path = f"/api/v1/tenants/{h.tenant}/ai/conversation-preview/audio?" + str(httpx.QueryParams(query))
        assert (await h.post(path)).status_code == 422
    monkeypatch.setenv("BUSINESS_PROFILES_ENABLED", "0")
    assert (await h.post(h.url(profile_version=str(uuid4())))).status_code == 423
    assert not h.provider_calls and not h.turns


@pytest.mark.asyncio
@pytest.mark.parametrize("payload,status", [
    ({"text": "sim"}, 502), ({"text": "sim", "duration": True}, 502),
    ({"text": "sim", "duration": float("nan")}, 502),
    ({"text": "sim", "duration": float("inf")}, 502),
    ({"text": "sim", "duration": 121}, 413),
    ({"text": "sim, mas não confirme. " * 100, "duration": 10}, 422),
])
async def test_route_never_executes_partial_or_unverified_audio(preview, payload, status):
    h = preview
    h.provider_payload.clear()
    h.provider_payload.update(payload)
    result = await h.post()
    assert result.status_code == status
    assert len(h.provider_calls) == 1 and not h.turns and not h.redis.transactions
    assert qr_profile.preview_snapshot.get() is None


@pytest.mark.asyncio
async def test_malformed_and_oversized_raw_audio_never_reaches_provider_or_engine(preview):
    h = preview
    assert (await h.post(body=WAV[:100])).status_code == 415
    assert (await h.post(body=WAV, media="audio/ogg")).status_code == 415
    assert (await h.post(body=b"RIFF" + b"x" * 2_000_000)).status_code == 413
    assert not h.provider_calls and not h.turns and not h.redis.transactions


@pytest.mark.asyncio
async def test_actual_preview_buffer_is_erased_after_provider_returns(preview, monkeypatch):
    captured = []
    validate = groq_audio.validate_audio
    def inspect(body, kind):
        captured.append(body)
        validate(body, kind)
    monkeypatch.setattr(groq_audio, "validate_audio", inspect)
    assert (await preview.post()).status_code == 200
    assert len(captured) == 1 and len(captured[0]) == len(WAV)
    assert not any(captured[0]), "raw preview media must not remain in the application buffer"


@pytest.mark.asyncio
async def test_run_locked_duplicate_group_is_rejected_before_prepare_and_marks_one_atomic_group(preview):
    h = preview
    prepared = []
    async def prepare():
        prepared.append(True)
        return {"text": "grupo transcrito"}
    namespace = "preview:group-001"
    ids = ["group-1", "group-2", "group-3"]
    first = await qr_ai.run_locked(h.tenant, "5567999999999", "", namespace,
                                  message_id=ids[0], message_ids=ids, preview=True, prepare=prepare)
    assert first["input_kind"] == "audio"
    assert len(h.redis.transactions) == 1 and len(h.redis.transactions[0]) == 3
    overlap = await qr_ai.run_locked(h.tenant, "5567999999999", "", namespace,
                                    message_id=ids[1], message_ids=[ids[1], "new-4"], preview=True, prepare=prepare)
    assert overlap["step"] == "duplicate" and len(prepared) == len(h.turns) == 1
    assert not any("new-4" in key for key in h.redis.values), "never partially consume a rejected group"


@pytest.mark.asyncio
async def test_real_group_retains_legacy_dedupe_and_never_executes_failed_preparation(preview):
    h = preview
    prepared = []
    async def prepare():
        prepared.append(True)
        raise HTTPException(502, "ASR indisponível")
    args = (h.tenant, "5567999999999", "", "5567999999999")
    with pytest.raises(HTTPException):
        await qr_ai.run_locked(*args, message_id="real-1", message_ids=["real-1", "real-2"], prepare=prepare)
    assert not h.turns and not h.redis.transactions
    await qr_ai.run_locked(*args, "real-1", message_ids=["real-1", "real-2"])
    assert len(h.redis.transactions) == 1 and len(h.redis.transactions[0]) == 4
    assert f"orion:qr:ai:done:{h.tenant}:real-2" in h.redis.values
    result = await qr_ai.run_locked(h.tenant, "5567999999999", "", "other-namespace",
                                    "real-2", prepare=prepare)
    assert result["step"] == "duplicate" and len(prepared) == 1


@pytest.mark.asyncio
async def test_same_request_id_isolated_by_authenticated_account_session_and_tenant(preview):
    h = preview
    request_id = "audio:" + str(uuid4())
    namespaces = [f"preview:{h.account}:session-one", f"preview:{uuid4()}:session-one", f"preview:{h.account}:session-two"]
    for namespace in namespaces:
        result = await qr_ai.run_locked(h.tenant, "5567999999999", "teste", namespace, request_id, preview=True)
        assert result["step"] == "catalog"
    result = await qr_ai.run_locked(uuid4(), "5567999999999", "teste", namespaces[0], request_id, preview=True)
    assert result["step"] == "catalog" and len(h.turns) == 4


@pytest.mark.asyncio
async def test_concurrent_duplicate_waits_for_first_result_before_asr(preview, monkeypatch):
    h = preview
    entered, release = asyncio.Event(), asyncio.Event()
    prepared = []
    async def prepare():
        prepared.append(True)
        entered.set()
        await release.wait()
        return {"text": "só uma interpretação"}
    args = (h.tenant, "5567999999999", "", "preview:serialized", "same-id")
    first = asyncio.create_task(qr_ai.run_locked(*args, preview=True, prepare=prepare))
    await entered.wait()
    second = asyncio.create_task(qr_ai.run_locked(*args, preview=True, prepare=prepare))
    await asyncio.sleep(0)
    assert not second.done() and len(prepared) == 1
    release.set()
    results = await asyncio.gather(first, second)
    assert [x["step"] for x in results] == ["catalog", "duplicate"]
    assert len(h.turns) == 1


@pytest.mark.asyncio
async def test_cookie_auth_keeps_account_identity_and_requires_csrf_before_preview(monkeypatch):
    account, tenant = uuid4(), uuid4()
    monkeypatch.setenv("SUPPORT_API_TOKEN", "synthetic-support-token-over-32-characters")
    claims = {"role": "member", "account_id": str(account), "auth_version": 4, "csrf": "synthetic-csrf"}
    class SessionRedis:
        async def get(self, key):
            return json.dumps(claims)
        async def aclose(self):
            pass
    monkeypatch.setattr(panel_auth, "_redis", SessionRedis)
    from app import account_auth
    async def current(account_id, version):
        assert UUID(account_id) == account and version == 4
        return {"role": "member", "name": "Cliente", "email": "local@example.invalid"}
    monkeypatch.setattr(account_auth, "current_account", current)
    def request(csrf=None):
        headers = [(b"host", b"test"), (b"origin", b"https://test"),
                   (b"cookie", ("__Host-orion_session=" + "x" * 43).encode())]
        if csrf is not None:
            headers.append((b"x-orion-csrf", csrf.encode()))
        return Request({"type": "http", "method": "POST", "path": f"/api/v1/tenants/{tenant}/ai/conversation-preview/audio",
                        "headers": headers, "scheme": "https", "server": ("test", 443), "query_string": b""})
    for csrf in (None, "wrong"):
        with pytest.raises(HTTPException) as denied:
            await authenticate(request(csrf), None)
        assert denied.value.status_code == 403
    actor = await authenticate(request("synthetic-csrf"), None)
    assert actor == Actor("member", account_id=account) and actor.tenant_id is None
    claims["account_id"] = "invalid-uuid"
    with pytest.raises(HTTPException) as denied:
        await authenticate(request("synthetic-csrf"), None)
    assert denied.value.status_code == 401
