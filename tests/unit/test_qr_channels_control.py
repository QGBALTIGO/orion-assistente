"""Owner proxies: real routing/transport validation with no socket or network."""
from uuid import uuid4
from types import SimpleNamespace
import json

from cryptography.fernet import Fernet
from fastapi import FastAPI, HTTPException
import httpx
import pytest

from app import qr_channels as channels
from app.catalog_api import Actor, authenticate

CLIENT = httpx.AsyncClient


@pytest.fixture
def control(monkeypatch):
    tenant, channel, account = uuid4(), uuid4(), uuid4()
    actor = Actor("member", account_id=account)
    key = "synthetic-private-channel-" + "x"*40
    cipher = Fernet(Fernet.generate_key())
    row = {"id": channel, "tenant_id": tenant, "owner_account_id": account, "provider": "baileys",
           "label": "Canal teste", "enabled": True, "assistant_enabled": False, "revision": 3,
           "allowed_senders": ["5567999999901"], "credential_encrypted": cipher.encrypt(key.encode()).decode(),
           "credential_hash": channels.credential_hash(key)}
    checks, network = [], []
    async def owned(t, c, who, revision=None):
        checks.append((t, c, who, revision))
        if t != tenant or c != channel or who != actor:
            raise HTTPException(403)
        if revision is not None and revision != row["revision"]:
            raise HTTPException(409)
        return dict(row)
    def provider(request):
        network.append(request)
        assert request.headers["x-orion-channel-key"] == key
        assert "x-orion-bridge-key" not in request.headers
        if request.url.path.endswith("/qr"):
            return httpx.Response(200, json={"image": "data:image/png;base64,iVBORw0KGgo=", "expires_in_ms": 30000, "credential": key})
        if request.url.path.endswith("/pair"):
            return httpx.Response(200, json={"status": "pairing", "code": "ABCD-EFGH", "expires_in_ms": 90000,
                                           "credential": key, "phone": "not-for-browser", "fence": 100})
        return httpx.Response(200, json={"status": "qr_ready", "capabilities": {"pair": True, "secret": key},
                                       "key": key, "phone": "not-for-browser", "fence": 100})
    monkeypatch.setenv("QR_CHANNELS_ENABLED", "1")
    monkeypatch.setenv("QR_BRIDGE_URL", "http://bridge.railway.internal:3000")
    monkeypatch.setattr(channels, "_cipher", lambda: cipher)
    monkeypatch.setattr(channels, "_owned_channel", owned)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: CLIENT(transport=httpx.MockTransport(provider), **kwargs))
    api = FastAPI(); api.include_router(channels.router)
    api.dependency_overrides[authenticate] = lambda: actor
    return tenant, channel, row, key, checks, network, api


async def call(state, action, *, method="GET", payload=None, tenant=None):
    t, channel, *_, api = state
    async with CLIENT(transport=httpx.ASGITransport(app=api), base_url="https://test") as client:
        return await client.request(method, f"/api/v1/tenants/{tenant or t}/qr-channels/{channel}/{action}", json=payload)


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["status", "qr", "start", "stop"])
async def test_owner_controls_use_only_fixed_private_route_and_strip_provider_fields(control, action):
    *_, checks, network, api = control
    result = await call(control, action, method="GET" if action in {"status", "qr"} else "POST",
                        payload=None if action in {"status", "qr"} else {"expected_revision": 3})
    assert result.status_code == 200 and result.headers["cache-control"] == "no-store"
    assert len(checks) == 2 and checks[-1][-1] == 3
    assert len(network) == 1 and network[0].url.host == "bridge.railway.internal"
    assert network[0].url.path == f"/internal/channels/{control[1]}/{action}"
    if action in {"start", "stop"}: assert network[0].content == b"{}"
    assert control[3] not in result.text and "not-for-browser" not in result.text and "fence" not in result.text
    if action != "qr":
        assert result.json()["phone"] is None and result.json()["phone_verified"] is False
    assert result.json()["assistant_enabled"] is False
    assert result.json()["revision"] == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["status", "qr", "stop"])
async def test_disabled_channel_read_and_stop_do_not_initialize_socket(control, action):
    control[2]["enabled"] = False
    result = await call(control, action, method="POST" if action == "stop" else "GET",
                        payload={"expected_revision": 3} if action == "stop" else None)
    assert result.status_code == 200 and not control[5]
    if action == "qr": assert result.json()["image"] is None
    else: assert result.json()["status"] == "disabled"


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [{}, {"expected_revision": 2}, {"expected_revision": 3, "url": "https://example.test"},
                                  {"expected_revision": 3, "key": "browser-key"}])
async def test_stale_or_untrusted_start_never_reaches_bridge(control, body):
    assert (await call(control, "start", method="POST", payload=body)).status_code in {409, 422}
    assert not control[5]


@pytest.mark.asyncio
async def test_cross_tenant_control_cannot_use_stored_secret(control):
    assert (await call(control, "qr", tenant=uuid4())).status_code == 403
    assert not control[5]


@pytest.mark.asyncio
async def test_revoked_owner_or_changed_revision_during_request_discards_qr(control, monkeypatch):
    old = channels._owned_channel; calls = 0
    async def owned(*args):
        nonlocal calls
        calls += 1
        if calls == 2: raise HTTPException(403)
        return await old(*args)
    monkeypatch.setattr(channels, "_owned_channel", owned)
    result = await call(control, "qr")
    assert result.status_code == 403 and "image" not in result.json()
    assert len(control[5]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["https://example.test", "http://127.0.0.1:3000", "http://x.railway.internal:3000/path",
                                "http://u:p@x.railway.internal:3000", "http://x.railway.internal:bad", "http://[broken"])
async def test_public_or_malformed_bridge_address_is_refused(control, monkeypatch, url):
    monkeypatch.setenv("QR_BRIDGE_URL", url)
    result = await call(control, "status")
    assert result.status_code == 503 and not control[5]


@pytest.mark.asyncio
@pytest.mark.parametrize("data", [{"image": "<script>bad</script>", "expires_in_ms": 30000},
                                   {"image": "data:image/svg+xml;base64,xxx", "expires_in_ms": 30000},
                                   {"image": "data:image/png;base64,iVBORw0KGgo=", "expires_in_ms": 45001},
                                   {"image": "data:image/png;base64,iVBORw0KGgo=", "expires_in_ms": 0},
                                   {"image": None, "expires_in_ms": True}])
async def test_invalid_qr_never_reaches_browser(control, monkeypatch, data):
    async def provider(*args): return data
    monkeypatch.setattr(channels, "_bridge_control", provider)
    assert (await call(control, "qr")).status_code == 503


@pytest.mark.asyncio
async def test_pairing_uses_only_owned_channel_and_private_canonical_phone(control):
    result = await call(control, "pair", method="POST", payload={"expected_revision": 3, "phone": "5567999999901"})
    assert result.status_code == 200 and result.headers["cache-control"] == "no-store"
    body = result.json()
    assert body["status"] == "pairing" and body["code"] == "ABCD-EFGH"
    assert 0 < body["expires_in_ms"] <= 90000
    assert body["channel_id"] == str(control[1]) and body["revision"] == 3
    assert body["enabled"] is True and body["assistant_enabled"] is False
    assert len(control[4]) == 3 and all(check[-1] == 3 for check in control[4])
    assert [r.url.path.rsplit("/", 1)[-1] for r in control[5]] == ["status", "pair"]
    assert json.loads(control[5][-1].content) == {"phone": "5567999999901"}
    assert control[3] not in result.text and "phone" not in body and "fence" not in body


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {}, {"expected_revision": 3}, {"expected_revision": 2, "phone": "5567999999901"},
    {"expected_revision": True, "phone": "5567999999901"},
    *({"expected_revision": 3, "phone": value} for value in [None, 5567999999901, "", "1234567", "0"*10,
       "+5567999999901", "(67) 99999-9901", "9"*16, "5567999999901\n"]),
    {"expected_revision": 3, "phone": "5567999999901", "tenant_id": str(uuid4())},
    {"expected_revision": 3, "phone": "5567999999901", "key": "injected"},
])
async def test_invalid_or_stale_pairing_never_reaches_bridge(control, payload):
    result = await call(control, "pair", method="POST", payload=payload)
    assert result.status_code in {409, 422} and not control[5]


@pytest.mark.asyncio
async def test_disabled_and_legacy_pairing_never_start_or_reset_socket(control, monkeypatch):
    control[2]["enabled"] = False
    payload = {"expected_revision": 3, "phone": "5567999999901"}
    assert (await call(control, "pair", method="POST", payload=payload)).status_code == 409
    control[2]["enabled"] = True
    from app import qr_legacy
    monkeypatch.setattr(qr_legacy, "is_alias", lambda *_: True)
    assert (await call(control, "pair", method="POST", payload=payload)).status_code == 409
    assert not control[5]
    assert (await call(control, "status")).status_code == 409


@pytest.mark.asyncio
async def test_cross_tenant_pairing_never_reads_credentials_or_calls_provider(control):
    result = await call(control, "pair", method="POST", tenant=uuid4(),
                        payload={"expected_revision": 3, "phone": "5567999999901"})
    assert result.status_code == 403 and not control[5]


@pytest.mark.asyncio
@pytest.mark.parametrize("capabilities", [None, {}, {"pair": False}, {"pair": "true"}, {"pair": 1}])
async def test_pairing_discovery_fails_closed_with_old_or_unavailable_bridge(control, monkeypatch, capabilities):
    calls = []
    async def provider(row, action, method, payload=None):
        calls.append(action)
        assert action == "status"
        return {"status": "qr_ready", "capabilities": capabilities}
    monkeypatch.setattr(channels, "_bridge_control", provider)
    result = await call(control, "pair", method="POST", payload={"expected_revision": 3, "phone": "5567999999901"})
    assert result.status_code == 409 and calls == ["status"]
    assert (await call(control, "status")).json()["capabilities"]["pair"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("data", [
    {"status": "pairing", "code": "ABCD-EFGH", "expires_in_ms": value} for value in [None, 0, -1, True, 1.5, 90001, "90000"]
] + [{"status": "pairing", "code": value, "expires_in_ms": 90000} for value in [None, "abcd-efgh", "ABCDEFGH", "<script>", "ABCD-EFGH\n"]]
  + [{"status": "connected", "code": "ABCD-EFGH", "expires_in_ms": 90000}])
async def test_untrusted_pairing_code_or_ttl_is_discarded(control, monkeypatch, data):
    async def provider(row, action, method, payload=None):
        return {"status": "qr_ready", "capabilities": {"pair": True}} if action == "status" else data
    monkeypatch.setattr(channels, "_bridge_control", provider)
    result = await call(control, "pair", method="POST", payload={"expected_revision": 3, "phone": "5567999999901"})
    assert result.status_code == 503 and "code" not in result.json()


@pytest.mark.asyncio
@pytest.mark.parametrize("revoked_after", [2, 3])
async def test_owner_or_revision_changed_in_flight_discards_pairing(control, monkeypatch, revoked_after):
    old = channels._owned_channel
    async def owned(*args):
        if len(control[4]) + 1 == revoked_after:
            raise HTTPException(403)
        return await old(*args)
    monkeypatch.setattr(channels, "_owned_channel", owned)
    result = await call(control, "pair", method="POST", payload={"expected_revision": 3, "phone": "5567999999901"})
    assert result.status_code == 403 and "code" not in result.json()
    assert len(control[5]) == revoked_after - 1


@pytest.mark.asyncio
async def test_pairing_code_expired_during_owner_recheck_is_never_returned(control, monkeypatch):
    ticks = iter([100.0, 191.0])
    monkeypatch.setattr(channels, "monotonic", lambda: next(ticks))
    result = await call(control, "pair", method="POST", payload={"expected_revision": 3, "phone": "5567999999901"})
    assert result.status_code == 409 and "code" not in result.json()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["pairing", "pair_expired", "pair_failed"])
async def test_status_discloses_pairing_state_without_phone_or_code(control, monkeypatch, status):
    async def provider(*_):
        return {"status": status, "capabilities": {"pair": True}, "code": "ABCD-EFGH", "phone": "5567999999901"}
    monkeypatch.setattr(channels, "_bridge_control", provider)
    body = (await call(control, "status")).json()
    assert body["status"] == status and body["capabilities"] == {"pair": True}
    assert "code" not in body and body["phone"] is None and body["phone_verified"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["owner", "editor", "viewer", "support"])
async def test_list_authority_and_allowlist_are_server_derived(monkeypatch, role):
    tenant, account = uuid4(), uuid4()
    actor = Actor("support" if role == "support" else "member", account_id=account)
    rows = [{**dict.fromkeys(channels.SAFE_FIELDS), "id": uuid4(), "tenant_id": tenant,
             "owner_account_id": account, "allowed_senders": ["5567999999901"]},
            {**dict.fromkeys(channels.SAFE_FIELDS), "id": uuid4(), "tenant_id": tenant,
             "owner_account_id": uuid4(), "allowed_senders": ["5567999999902"]}]
    class DB:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def execute(self, *args):
            return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: rows))
    async def access(*args): return SimpleNamespace(role=role)
    monkeypatch.setenv("QR_CHANNELS_ENABLED", "1")
    monkeypatch.setattr(channels, "resolve_tenant_access", access)
    monkeypatch.setattr(channels, "support_session_factory", lambda: DB)
    async def canonical(*args, **kwargs): return rows[0], "ready"
    monkeypatch.setattr(channels, "_canonical", canonical)
    api = FastAPI(); api.include_router(channels.router); api.dependency_overrides[authenticate] = lambda: actor
    async with CLIENT(transport=httpx.ASGITransport(app=api), base_url="https://test") as client:
        result = await client.get(f"/api/v1/tenants/{tenant}/qr-channels")
    assert result.status_code == 200
    body = result.json(); assert body["access"]["can_manage"] is (role == "owner")
    assert body["capabilities"]["pair"] is (role == "owner")
    assert "allowed_senders" not in body["items"][0]
    assert len(body["items"]) == 1 and body["connection"] == body["items"][0]
