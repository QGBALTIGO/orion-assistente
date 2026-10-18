from uuid import uuid4

import httpx
import pytest

from app import automation_transport as transport


@pytest.mark.parametrize("url", ["https://public.example", "http://localhost:3000", "http://qr.railway.internal:80",
    "http://user:secret@qr.railway.internal:3000", "http://qr.railway.internal:3000/path", "http://qr.railway.internal:3000?x=1",
    "http://qr.railway.internal:3000#fragment", "file:///tmp/socket", "http://qr.railway.internal:bad"])
def test_bridge_destination_is_fixed_private_configuration(url, monkeypatch):
    monkeypatch.setenv("QR_BRIDGE_URL", url)
    with pytest.raises(ValueError): transport.bridge_origin()


@pytest.mark.asyncio
@pytest.mark.parametrize("body,phone", [(" ", "+5567999999901"), ("😀"*601, "+5567999999901"), ("Texto", "group@g.us"), ("Texto", "5567999999901")])
async def test_invalid_message_or_noncanonical_contact_never_loads_credentials(body, phone, monkeypatch):
    monkeypatch.setenv("MARKETING_EXECUTION_ENABLED", "1")
    async def forbidden(*args, **kwargs): pytest.fail("invalid request must not reach bridge")
    monkeypatch.setattr(transport, "_request", forbidden)
    with pytest.raises(ValueError):
        await transport.send_automation(uuid4(), uuid4(), phone, body, uuid4())


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["send", "inspect", "wrong_operation", "unknown", "timeout"])
async def test_one_authenticated_request_no_redirect_retry_or_autostart(mode, monkeypatch):
    from app import qr_channels
    monkeypatch.setenv("MARKETING_EXECUTION_ENABLED", "1")
    monkeypatch.setenv("QR_BRIDGE_URL", "http://qr.railway.internal:3000")
    tenant, channel, operation = uuid4(), uuid4(), uuid4()
    requests = []
    async def credentials(t, c, *, read_only=False):
        assert (t, c) == (tenant, channel)
        assert read_only is (mode == "inspect")
        return {"key": "synthetic-private-channel-key", "path": f"/internal/channels/{channel}/send"}
    monkeypatch.setattr(qr_channels, "outbound_channel_credentials", credentials)
    def handler(request):
        requests.append(request)
        assert request.headers["X-Orion-Channel-Key"] == "synthetic-private-channel-key"
        if mode == "timeout": raise httpx.ReadTimeout("synthetic", request=request)
        return httpx.Response(200, json={"operation_id": str(uuid4() if mode == "wrong_operation" else operation),
            "state": "uncertain" if mode == "unknown" else "accepted", "accepted": mode != "unknown", "delivery": "unknown"})
    original = httpx.AsyncClient
    monkeypatch.setattr(transport.httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    if mode in {"wrong_operation", "unknown", "timeout"}:
        with pytest.raises((ValueError, httpx.TimeoutException)):
            await transport.send_automation(tenant, channel, "+5567999999901", "Mensagem sintética", operation)
    elif mode == "send":
        assert await transport.send_automation(tenant, channel, "+5567999999901", "Mensagem sintética", operation) == str(operation)
    else:
        assert (await transport.inspect_automation(tenant, channel, operation))["delivery"] == "unknown"
    assert len(requests) == 1
    assert requests[0].method == ("GET" if mode == "inspect" else "POST")
    assert "/start" not in requests[0].url.path
