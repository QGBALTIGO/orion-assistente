"""Real managed HTTP route/shared ASR with synthetic bytes and an offline provider."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI, HTTPException
import httpx
import pytest

from app import qr_channel_audio as audio, groq_audio
from app.qr_channels import ChannelIdentity

PHONE = "5567999999901"
SYNTHETIC = b"OggS-synthetic-not-a-real-customer-voice"


@pytest.fixture
def flow(monkeypatch):
    for name in ("QR_CHANNELS_ENABLED", "QR_AI_AUDIO_ENABLED"):
        monkeypatch.setenv(name, "1")
    monkeypatch.setenv("QR_AI_ENABLED", "0")
    monkeypatch.setenv("QR_AI_TENANT_ID", str(uuid4()))
    monkeypatch.setenv("QR_AI_ALLOWED_SENDERS", "")
    monkeypatch.setenv("GROQ_API_KEY", "synthetic-only-no-live-key")
    state = SimpleNamespace(identity=ChannelIdentity(uuid4(), uuid4(), uuid4(), "baileys", 3, uuid4(), True, True),
        auth=[], calls=[], buffers=[], ready=True, auth_error=None, response={"text": " Quero corte e barba ", "duration": 4.5},
        provider_status=200, after_provider=None)
    original_client = httpx.AsyncClient
    async def authorize(request, *, phone):
        state.auth.append((request.headers.get("x-orion-channel-owner"), request.headers.get("x-orion-channel-fence"), phone))
        if state.auth_error: raise state.auth_error
        return state.identity
    async def ready(identity): return state.ready
    async def provider(request):
        state.calls.append(request)
        assert request.url == "https://api.groq.com/openai/v1/audio/transcriptions"
        assert request.headers["authorization"] == "Bearer synthetic-only-no-live-key"
        assert SYNTHETIC in request.content and b"verbose_json" in request.content
        if state.after_provider: await state.after_provider()
        return httpx.Response(state.provider_status, json=state.response)
    validator = groq_audio.validate_audio
    def validate(body, kind): state.buffers.append(body); validator(body, kind)
    monkeypatch.setattr(audio, "authenticated_channel", authorize)
    monkeypatch.setattr(audio, "_assistant_ready", ready)
    monkeypatch.setattr(groq_audio, "validate_audio", validate)
    monkeypatch.setattr(groq_audio.httpx, "AsyncClient", lambda **kw: original_client(transport=httpx.MockTransport(provider), **kw))
    app = FastAPI(); app.include_router(audio.router)
    state.app = app
    state.client = original_client
    state.url = f"/internal/qr/channels/{state.identity.channel_id}/audio"
    state.headers = {"X-Orion-Phone": PHONE, "Content-Type": "audio/ogg; codecs=opus",
                    "X-Orion-Channel-Key": "synthetic-channel-key", "X-Orion-Channel-Owner": str(state.identity.lease_owner),
                    "X-Orion-Channel-Fence": "3"}
    async def post(*, body=SYNTHETIC, headers=None):
        async with original_client(transport=httpx.ASGITransport(app=app), base_url="https://test") as client:
            return await client.post(state.url, headers=state.headers if headers is None else headers, content=body)
    state.post = post
    return state


@pytest.mark.asyncio
async def test_managed_audio_uses_current_channel_not_legacy_tenant_allowlist_or_switch(flow):
    response = await flow.post()
    assert response.status_code == 200 and response.json() == {"text": "Quero corte e barba", "truncated": False}
    assert len(flow.calls) == 1 and len(flow.auth) == 2
    assert flow.auth == [(str(flow.identity.lease_owner), "3", PHONE)] * 2
    assert not {"tenant_id", "channel_id", "phone", "message_id"} & response.json().keys()
    assert flow.buffers and set(flow.buffers[0]) == {0}


@pytest.mark.asyncio
@pytest.mark.parametrize("flag", ["QR_CHANNELS_ENABLED", "QR_AI_AUDIO_ENABLED"])
async def test_managed_audio_default_off_never_reads_auth_schema_or_provider(flow, monkeypatch, flag):
    monkeypatch.delenv(flag, raising=False)
    response = await flow.post()
    assert response.status_code == 423 and not flow.auth and not flow.calls and not flow.buffers


@pytest.mark.asyncio
@pytest.mark.parametrize("phone", ["", "120363@g.us", "12345@lid", "5567999999901suffix"])
async def test_invalid_phone_is_rejected_before_asr(flow, phone):
    response = await flow.post(headers={**flow.headers, "X-Orion-Phone": phone})
    assert response.status_code == 403 and not flow.auth and not flow.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 409])
async def test_bad_credential_foreign_sender_or_stale_fence_has_no_provider_effect(flow, status):
    flow.auth_error = HTTPException(status, "synthetic denied")
    assert (await flow.post()).status_code == status and not flow.calls and not flow.buffers


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["assistant", "readiness", "provider", "sender", "channel"])
async def test_ineligible_channel_never_transcribes(flow, change):
    if change == "readiness": flow.ready = False
    else:
        key, value = {"assistant": ("assistant_enabled", False), "provider": ("provider", "other"),
                      "sender": ("allowed_sender", False), "channel": ("channel_id", uuid4())}[change]
        flow.identity = replace(flow.identity, **{key: value})
    assert (await flow.post()).status_code in {403, 423} and not flow.calls and not flow.buffers


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["fence", "owner", "assistant", "sender", "readiness", "global_audio_off"])
async def test_change_during_asr_never_returns_transcript_or_retries(flow, monkeypatch, change):
    async def changed():
        if change == "readiness": flow.ready = False
        elif change == "global_audio_off": monkeypatch.setenv("QR_AI_AUDIO_ENABLED", "0")
        else:
            key, value = {"fence": ("fence", 4), "owner": ("lease_owner", uuid4()),
                          "assistant": ("assistant_enabled", False), "sender": ("allowed_sender", False)}[change]
            flow.identity = replace(flow.identity, **{key: value})
    flow.after_provider = changed
    response = await flow.post()
    assert response.status_code in {403, 409, 423} and "Quero corte" not in response.text and len(flow.calls) == 1
    assert set(flow.buffers[0]) == {0}


@pytest.mark.asyncio
@pytest.mark.parametrize("body,kind,status", [(b"", "audio/ogg", 422), (b"not-audio", "audio/ogg", 415),
    (SYNTHETIC, "text/plain", 415), (SYNTHETIC+b"x"*2_000_000, "audio/ogg", 413)],
    ids=["empty", "bad-container", "unsupported-mime", "oversized"])
async def test_managed_media_limits_reuse_shared_validation(flow, body, kind, status):
    response = await flow.post(body=body, headers={**flow.headers, "Content-Type": kind})
    assert response.status_code == status and not flow.calls
    assert all(not b or set(b) == {0} for b in flow.buffers)


@pytest.mark.asyncio
@pytest.mark.parametrize("payload,status", [({"text": "sim", "duration": 121}, 413),
    ({"text": "sim", "duration": True}, 502), ({"text": "sim", "duration": None}, 502),
    ({"text": "", "duration": 4}, 502), ({"text": "x"*1501, "duration": 4}, 422)])
async def test_unverified_duration_or_partial_long_text_never_routes(flow, payload, status):
    flow.response = payload
    response = await flow.post()
    assert response.status_code == status and len(flow.calls) == 1 and set(flow.buffers[0]) == {0}


@pytest.mark.asyncio
async def test_provider_error_has_one_attempt_and_wipes_media(flow):
    flow.provider_status = 429
    response = await flow.post()
    assert response.status_code == 502 and len(flow.calls) == 1 and set(flow.buffers[0]) == {0}
    assert "synthetic-only-no-live-key" not in response.text


@pytest.mark.asyncio
async def test_timeout_has_one_attempt_and_no_partial_or_late_text(flow):
    async def fail(): raise TimeoutError("synthetic-private-timeout")
    flow.after_provider = fail
    response = await flow.post()
    assert response.status_code == 503 and len(flow.calls) == 1 and set(flow.buffers[0]) == {0}
    assert "synthetic-private-timeout" not in response.text and "Quero corte" not in response.text


@pytest.mark.asyncio
async def test_cancelled_request_never_leaves_media_or_returns_late_text(flow):
    entered = asyncio.Event()
    async def wait(): entered.set(); await asyncio.Event().wait()
    flow.after_provider = wait
    task = asyncio.create_task(flow.post())
    await asyncio.wait_for(entered.wait(), 3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    assert len(flow.calls) == 1 and set(flow.buffers[0]) == {0}
