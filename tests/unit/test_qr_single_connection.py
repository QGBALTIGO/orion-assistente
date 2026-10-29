"""Single identity, fail-closed legacy adapters and deny-all provisioning."""
from types import SimpleNamespace
from uuid import uuid4

from fastapi import HTTPException, Response
from cryptography.fernet import Fernet
import httpx
import pytest

from app import qr_channels as channels, qr_legacy
from app.catalog_api import Actor
from test_qr_admin_identity import native, registered  # noqa: F401

HTTP_CLIENT = httpx.AsyncClient
PRIVATE_CONTROL = channels._bridge_control


def row(tenant, *, enabled=False, identity=None):
    return {"id": identity or uuid4(), "tenant_id": tenant, "enabled": enabled}


def test_canonical_resolution_never_uses_order_and_preserves_disabled_identity():
    tenant = uuid4()
    one = row(tenant)
    assert channels._canonical_candidate([one], None) == (one, "ready")
    assert channels._canonical_candidate([], None) == (None, "missing")
    history = row(tenant)
    assert channels._canonical_candidate([one, history], None) == (None, "ambiguous")
    active = row(tenant, enabled=True)
    assert channels._canonical_candidate([one, active, history], None) == (active, "ready")
    assert channels._canonical_candidate([active, one], one["id"]) == (one, "ready")
    assert channels._canonical_candidate([active, row(tenant, enabled=True)], None) == (None, "ambiguous")


def test_exact_alias_with_inactive_history_is_kept_but_multiple_live_sockets_block():
    tenant = uuid4()
    alias = row(tenant, enabled=True, identity=qr_legacy.channel_id(tenant))
    history = row(tenant)
    assert channels._canonical_candidate([history, alias], None) == (alias, "ready")
    history["enabled"] = True
    assert channels._canonical_candidate([alias, history], None) == (None, "ambiguous")


@pytest.mark.asyncio
async def test_explicit_provision_is_idempotent_across_different_keys_and_returns_no_secret(native):
    state = native
    first = await channels.provision_connection(state.tenant, channels.ProvisionConnection(idempotency_key=uuid4()), Response(), state.actor)
    second = await channels.provision_connection(state.tenant, channels.ProvisionConnection(idempotency_key=uuid4()), Response(), state.actor)
    assert first == second and first["canonical_state"] == "ready"
    assert len(first["items"]) == 1 and first["connection"]["enabled"] is False
    assert first["connection"]["assistant_enabled"] is False
    assert first["connection"]["assistant_activation_allowed"] is False
    assert state.db.canonical == next(iter(state.db.rows))
    assert not {"allowed_senders", "credential", "credential_encrypted", "credential_hash"} & first["connection"].keys()
    assert next(iter(state.db.rows.values()))["allowed_senders"] == []
    assert len([sql for sql, _ in state.db.calls if "INSERT INTO qr_channels" in sql]) == 1
    assert all("FOR UPDATE" in sql for sql, _ in state.db.calls if "SELECT id,status,whatsapp_channel_id" in sql)
    assert not state.network


@pytest.mark.asyncio
async def test_existing_disabled_canonical_is_reused_and_second_legacy_create_is_rejected(native):
    state = native
    existing, _ = await registered(state)
    result = await channels.provision_connection(state.tenant, channels.ProvisionConnection(idempotency_key=uuid4()), Response(), state.actor)
    assert result["connection"]["id"] == str(existing["id"])
    assert len(state.db.rows) == 1 and not state.db.rows[existing["id"]]["enabled"]
    with pytest.raises(HTTPException) as caught:
        await registered(state)
    assert caught.value.status_code == 409 and len(state.db.rows) == 1


@pytest.mark.asyncio
async def test_hidden_history_cannot_start_pair_or_activate(native):
    state = native
    canonical, _ = await registered(state)
    historical = {**state.db.rows[canonical["id"]], "id": uuid4()}
    state.db.rows[historical["id"]] = historical
    for action in ("start", "status", "qr", "stop"):
        with pytest.raises(HTTPException) as caught:
            await channels._control(state.tenant, historical["id"], state.actor, action, 1)
        assert caught.value.status_code == 409
    with pytest.raises(HTTPException) as caught:
        await channels.pair_control(state.tenant, historical["id"],
            channels.ChannelPairCommand(expected_revision=1, phone="5567999999901"), Response(), state.actor)
    assert caught.value.status_code == 409
    with pytest.raises(HTTPException):
        await channels.change_state(state.tenant, historical["id"], channels.ChannelState(expected_revision=1, enabled=True), state.actor)
    assert not state.network


@pytest.mark.asyncio
async def test_ambiguous_registry_does_not_choose_or_create(native):
    state = native
    first, _ = await registered(state)
    state.db.canonical = None
    state.db.rows[uuid4()] = {**state.db.rows[first["id"]], "id": uuid4()}
    with pytest.raises(HTTPException) as caught:
        await channels.provision_connection(state.tenant, channels.ProvisionConnection(idempotency_key=uuid4()), Response(), state.actor)
    assert caught.value.status_code == 409 and len(state.db.rows) == 2 and state.db.canonical is None


@pytest.mark.asyncio
async def test_deny_all_policy_cannot_activate_assistant(native,monkeypatch):
    from plan_fixture import install_plan_fixture
    install_plan_fixture(monkeypatch,"elite")
    state = native
    body = await channels.provision_connection(state.tenant, channels.ProvisionConnection(idempotency_key=uuid4()), Response(), state.actor)
    identity = state.db.canonical
    with pytest.raises(HTTPException) as caught:
        await channels.change_state(state.tenant, identity, channels.ChannelState(expected_revision=1, assistant_enabled=True), state.actor)
    assert caught.value.status_code == 409
    assert state.db.rows[identity]["assistant_enabled"] is False


@pytest.mark.parametrize("status,phone,verified,expected", [
    ("connected", "5567999999901", True, "5567999999901"),
    ("connected", "5567999999901", False, None),
    ("connected", "12345@lid", True, None),
    ("qr_ready", "5567999999901", True, None),
])
def test_phone_only_discloses_confirmed_provider_pn(status, phone, verified, expected):
    source = {"id": uuid4(), "revision": 1, "enabled": True, "assistant_enabled": False}
    result = channels._control_result(source, {"status": status, "phone": phone, "phone_verified": verified}, "status")
    assert result["phone"] == expected and result["phone_verified"] is (expected is not None)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,status,allowed", [("recover", "logged_out", True), ("recover", "pair_failed", True),
    ("recover", "connected", False), ("recover", "pairing", False), ("reconnect", "qr_expired", True),
    ("reconnect", "pair_expired", True), ("reconnect", "pair_failed", False),
    ("reconnect", "connecting", False), ("reconnect", "error", False)])
async def test_preparation_requires_explicit_mode_current_verified_state_and_native_owner(native, monkeypatch, mode, status, allowed):
    state = native
    existing, _ = await registered(state)
    alias = qr_legacy.channel_id(state.tenant)
    source = state.db.rows.pop(existing["id"])
    source.update(id=alias, enabled=True)
    state.db.rows[alias] = source
    state.db.canonical = alias
    async def binding(_): return True
    calls = []
    async def provider(_, action, method, payload=None):
        calls.append((action, method))
        return {"status": status if action == "status" else "connecting"}
    monkeypatch.setattr(channels, "_legacy_binding_valid", binding)
    monkeypatch.setattr(channels, "_bridge_control", provider)
    command = channels.PrepareConnection(expected_revision=1, mode=mode)
    if allowed:
        result = await channels.prepare_control(state.tenant, alias, command, Response(), state.actor)
        assert result["status"] == "connecting" and calls == [("status", "GET"), (mode, "POST")]
    else:
        with pytest.raises(HTTPException) as caught:
            await channels.prepare_control(state.tenant, alias, command, Response(), state.actor)
        assert caught.value.status_code == 409 and calls == [("status", "GET")]
    calls.clear()
    with pytest.raises(HTTPException) as caught:
        await channels.prepare_control(state.tenant, alias, command, Response(), Actor("member", account_id=state.account))
    assert caught.value.status_code == 403 and not calls


@pytest.mark.asyncio
async def test_member_alias_has_metadata_only_and_no_private_transport_access(native, monkeypatch):
    state = native
    existing, _ = await registered(state)
    alias = qr_legacy.channel_id(state.tenant)
    source = state.db.rows.pop(existing["id"])
    source.update(id=alias, enabled=True)
    state.db.rows[alias] = source
    state.db.canonical = alias
    actor = Actor("member", account_id=state.account)
    body = channels._connection_envelope(source, "ready", True, actor)
    assert body["access"]["can_manage"] is False and body["connection"]["can_manage"] is False
    assert not any(body["capabilities"].values())
    for action in ("status", "qr", "start", "stop"):
        with pytest.raises(HTTPException) as caught:
            await channels._control(state.tenant, alias, actor, action, 1)
        assert caught.value.status_code == 403
    assert not state.network


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["status", "start", "qr", "pair", "reconnect", "recover"])
async def test_validated_legacy_adapter_has_fixed_paths_and_never_creates_a_managed_socket(monkeypatch, action):
    tenant = uuid4()
    key = "synthetic-global-secret-" + "x"*40
    cipher = Fernet(Fernet.generate_key())
    source = {"id": qr_legacy.channel_id(tenant), "tenant_id": tenant, "credential_hash": channels.credential_hash(key),
              "credential_encrypted": cipher.encrypt(key.encode()).decode(), "enabled": True, "assistant_enabled": True, "revision": 1}
    async def valid(_): return True
    network = []
    def provider(request):
        network.append(request)
        assert request.headers["x-orion-bridge-key"] == key and "x-orion-channel-key" not in request.headers
        if action in {"reconnect", "recover"}:
            assert request.headers["x-orion-canonical-prepare"] == "1"
        if action == "qr": return httpx.Response(200, json={"image": "data:image/png;base64,aGVsbG8=", "expiresIn": 30})
        if action == "pair": return httpx.Response(200, json={"status": "pairing", "code": "ABCD-EFGH", "expiresIn": 90})
        return httpx.Response(202 if action in {"reconnect", "recover"} else 200, json={"status": "qr_expired", "capabilities": {"pair": True}})
    monkeypatch.setenv("QR_BRIDGE_URL", "http://bridge.railway.internal:3000")
    monkeypatch.setattr(channels, "_legacy_binding_valid", valid)
    monkeypatch.setattr(channels, "_cipher", lambda: cipher)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: HTTP_CLIENT(transport=httpx.MockTransport(provider), **kwargs))
    method = "GET" if action in {"status", "qr"} else "POST"
    data = await PRIVATE_CONTROL(source, action, method, {"phone": "5567999999901"} if action == "pair" else None)
    assert len(network) == 1 and network[0].url.path == "/internal/" + ("status" if action == "start" else action)
    assert network[0].method == ("GET" if action == "start" else method)
    if action in {"qr", "pair"}: assert data["expires_in_ms"] == (30000 if action == "qr" else 90000)


@pytest.mark.asyncio
async def test_unverified_legacy_registration_blocks_before_private_request(monkeypatch):
    tenant = uuid4()
    async def invalid(_): return False
    monkeypatch.setattr(channels, "_legacy_binding_valid", invalid)
    def forbidden(**kwargs): raise AssertionError("Invalid alias must never request a global socket")
    monkeypatch.setattr(httpx, "AsyncClient", forbidden)
    with pytest.raises(HTTPException) as caught:
        await PRIVATE_CONTROL({"id": qr_legacy.channel_id(tenant), "tenant_id": tenant}, "status", "GET")
    assert caught.value.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", [None, "wrong_tenant", "wrong_alias", "wrong_hash", "wrong_cipher", "not_adopted", "disabled"])
async def test_legacy_binding_checks_exact_configuration_credential_and_registration(monkeypatch, damage):
    tenant = uuid4()
    token = "synthetic-legacy-global-key-" + "x"*40
    cipher = Fernet(Fernet.generate_key())
    source = {"id": qr_legacy.channel_id(tenant), "tenant_id": tenant, "credential_hash": channels.credential_hash(token),
              "credential_encrypted": cipher.encrypt(token.encode()).decode()}
    monkeypatch.setenv("QR_LEGACY_CENTRAL_ENABLED", "0" if damage == "disabled" else "1")
    monkeypatch.setenv("QR_AI_TENANT_ID", str(uuid4() if damage == "wrong_tenant" else tenant))
    monkeypatch.setenv("QR_BRIDGE_TOKEN", token)
    monkeypatch.setattr(channels, "_cipher", lambda: cipher)
    async def registry(t, c):
        assert t == tenant and c == qr_legacy.channel_id(tenant)
        return damage != "not_adopted"
    monkeypatch.setattr(qr_legacy, "registry_adopted", registry)
    if damage == "wrong_alias": source["id"] = uuid4()
    if damage == "wrong_hash": source["credential_hash"] = "0"*64
    if damage == "wrong_cipher": source["credential_encrypted"] = cipher.encrypt(b"unrelated-private-token").decode()
    assert await channels._legacy_binding_valid(source) is (damage is None)
