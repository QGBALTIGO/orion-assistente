"""Untrusted provider data, sealed previews and owner-first import admission."""
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from uuid import UUID, uuid4

from cryptography.fernet import Fernet
from fastapi import FastAPI, HTTPException, Response
import httpx
from pydantic import ValidationError
import pytest

from app import subscription_group_contacts as api
from app import subscription_group_contacts_schemas as schemas
from app import subscription_group_contacts_store as store
from app import plan_permissions, plan_routing
from app.catalog_api import Actor, authenticate
from test_subscription_workspace import snapshot as plan_snapshot, NOW as PLAN_NOW

NOW = datetime(2026, 11, 5, 18, tzinfo=timezone.utc)
TENANT, OWNER, DEVICE, GENERATION, CHANNEL, LOCAL, KEY = (uuid4() for _ in range(7))
ACTOR = Actor("member", account_id=OWNER)
REMOTE = "120363111111111111@g.us"
PHONE = "+5567999999901"
ROW = {"id": DEVICE, "tenant_id": TENANT, "owner_account_id": OWNER, "channel_id": CHANNEL,
       "generation": GENERATION, "revision": 2, "status": "connected", "name": "Dispositivo sintético"}


def members(**changes):
    return {**store.transport_scope(ROW), "group": {"id": REMOTE, "name": "Grupo sintético", "member_count": 3},
        "members": [{"phone": PHONE, "name": "", "role": "admin"}], "total": 3,
        "unavailable_count": 1, "duplicate_count": 1} | changes


def payload(**changes):
    return {"idempotency_key": str(KEY), "preview_token": "a" * 100, "phones": [PHONE],
        "target_group_id": None, "expected_version": None, "new_group_name": "Grupo: Sintético"} | changes


@pytest.fixture
def cipher(monkeypatch):
    value = Fernet(Fernet.generate_key())
    monkeypatch.setattr(store.channels, "_cipher", lambda: value)
    return value


def preview():
    return store.create_preview(TENANT, ACTOR, ROW, schemas.BridgeMembers.model_validate(members()), NOW)[0]


def app():
    value = FastAPI()
    for route in api.router.routes:
        value.add_api_route(route.path, route.endpoint, methods=route.methods, status_code=route.status_code)
    value.dependency_overrides[authenticate] = lambda: ACTOR
    return value


def url(suffix):
    return api.BASE.format(tenant_id=TENANT) + suffix


@pytest.mark.parametrize("changes", [
    {"phones": []}, {"phones": [PHONE, PHONE]}, {"phones": ["123456789012@lid"]},
    {"phones": ["5567999999901"]}, {"phones": ["+012345678"]}, {"phones": ["+１２３４５６７８９"]},
    {"preview_token": "x" * (schemas.MAX_TOKEN + 1)}, {"new_group_name": "🙂" * 51},
    {"new_group_name": "\x00"}, {"new_group_name": " "}, {"new_group_name": "x\ny"},
    {"expected_version": 1}, {"target_group_id": str(LOCAL)},
    {"target_group_id": str(LOCAL), "new_group_name": None, "expected_version": True},
    {"target_group_id": str(LOCAL), "new_group_name": None, "expected_version": 2**63},
    {"tenant_id": str(uuid4())}, {"members": [{"phone": PHONE, "name": "forged"}]},
])
def test_import_rejects_ambiguous_destinations_unverified_identities_and_unbounded_input(changes):
    with pytest.raises(ValidationError):
        schemas.ImportContacts.model_validate(payload(**changes))


def test_import_preserves_missing_remote_name_and_accepts_exact_utf16_boundary():
    assert schemas.Member.model_validate(members()["members"][0]).name == ""
    assert schemas.ImportContacts.model_validate(payload(new_group_name="🙂" * 50)).new_group_name == "🙂" * 50
    value = schemas.ImportContacts.model_validate(payload(target_group_id=LOCAL, expected_version=3, new_group_name=None))
    assert value.target_group_id == LOCAL and value.expected_version == 3


@pytest.mark.parametrize("changes", [
    {"members": [{"phone": PHONE, "name": "", "role": "superadmin"}]},
    {"members": [{"phone": "123456789012@lid", "name": "", "role": "member"}]},
    {"members": [{"phone": PHONE, "name": "", "role": "member"}] * 2, "duplicate_count": 0},
    {"total": 4}, {"unavailable_count": -1}, {"duplicate_count": True},
    {"group": {"id": "https://example.test", "name": "Grupo", "member_count": 3}},
    {"group": {"id": REMOTE, "name": "Grupo", "member_count": 2}},
    {"tenant_id": str(TENANT)},
])
def test_provider_response_cannot_invent_phones_counts_roles_or_another_authority(changes):
    with pytest.raises(ValidationError):
        schemas.BridgeMembers.model_validate(members(**changes))


def test_preview_is_private_bound_to_the_exact_scope_and_five_minute_lifetime(cipher):
    token = preview()
    restored = store.decode_preview(token, TENANT, ACTOR, NOW + timedelta(seconds=299))
    assert restored.members[0].name == "" and restored.members[0].phone == PHONE
    assert restored.purpose == store.PURPOSE and restored.expires_at - restored.issued_at == 300
    store.validate_preview_scope(restored, ROW)
    for row in (ROW | {"generation": uuid4()}, ROW | {"revision": 3}, ROW | {"channel_id": uuid4()}, ROW | {"id": uuid4()}):
        with pytest.raises(HTTPException) as error:
            store.validate_preview_scope(restored, row)
        assert error.value.detail["code"] == "GROUP_CONTACTS_CONTEXT_CHANGED"
    with pytest.raises(HTTPException) as expired:
        store.decode_preview(token, TENANT, ACTOR, NOW + timedelta(seconds=300))
    assert expired.value.status_code == 410


@pytest.mark.parametrize("change", ["tamper", "tenant", "actor", "purpose", "future", "lifetime", "key"])
def test_preview_rejects_tampering_foreign_contexts_and_other_cipher_uses(cipher, monkeypatch, change):
    token, tenant, actor, now = preview(), TENANT, ACTOR, NOW
    if change == "tamper":
        token = token[:45] + ("b" if token[45] != "b" else "a") + token[46:]
    elif change == "tenant":
        tenant = uuid4()
    elif change == "actor":
        actor = Actor("member", account_id=uuid4())
    elif change == "key":
        monkeypatch.setattr(store.channels, "_cipher", lambda: Fernet(Fernet.generate_key()))
    else:
        value = json.loads(cipher.decrypt(token.encode()))
        if change == "purpose": value["purpose"] = "another.integration.credential"
        if change == "future": value.update(issued_at=value["issued_at"]+2, expires_at=value["expires_at"]+2)
        if change == "lifetime": value["expires_at"] += 1
        token = cipher.encrypt(json.dumps(value).encode()).decode()
    with pytest.raises(HTTPException) as error:
        store.decode_preview(token, tenant, actor, now)
    assert error.value.status_code == 422 and error.value.detail["code"] == "GROUP_CONTACTS_PREVIEW_INVALID"


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [b"not-json", b"x" * (schemas.MAX_BODY + 1)])
async def test_owner_is_checked_before_import_body_is_decoded(monkeypatch, body):
    @asynccontextmanager
    async def denied(*args):
        raise HTTPException(403, "owner required")
        yield
    monkeypatch.setattr(store, "owner_transaction", denied)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app()), base_url="https://test") as client:
        response = await client.post(url("/import"), content=body, headers={"Content-Type": "application/json"})
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_authorized_oversize_body_is_refused_before_any_write(monkeypatch):
    @asynccontextmanager
    async def allowed(*args): yield object()
    monkeypatch.setattr(store, "owner_transaction", allowed)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app()), base_url="https://test") as client:
        response = await client.post(url("/import"), content=b"x" * (schemas.MAX_BODY + 1))
    assert response.status_code == 413 and response.headers["cache-control"] == "no-store"


async def install_reads(monkeypatch, *, provider=None, before=None, after=None):
    boundaries, calls = [], []
    @asynccontextmanager
    async def boundary(tenant, actor):
        assert (tenant, actor) == (TENANT, ACTOR)
        boundaries.append("enter")
        try: yield object()
        finally: boundaries.append("exit")
    async def device(db, tenant, identity, *, actor=None):
        assert (tenant, identity, actor) == (TENANT, DEVICE, ACTOR)
        if len(calls) and after:
            if isinstance(after, Exception): raise after
            return ROW | after, {"id": CHANNEL, "enabled": True, "allowed_senders": []}
        return ROW | (before or {}), {"id": CHANNEL, "enabled": True, "allowed_senders": []}
    async def bridge(transport, action, method, data):
        assert boundaries == ["enter", "exit"]
        calls.append((transport, action, method, data))
        if isinstance(provider, Exception): raise provider
        return provider if provider is not None else members()
    async def clock(db): return NOW
    monkeypatch.setattr(store, "owner_transaction", boundary)
    monkeypatch.setattr(store.devices, "_device", device)
    monkeypatch.setattr(store.channels, "_bridge_control", bridge)
    monkeypatch.setattr(store, "clock", clock)
    return boundaries, calls


CONTRACT_PATH = Path(__file__).resolve().parents[1] / "fixtures/subscription_group_contacts_contract.json"


async def disconnected_options_from_real_producer(monkeypatch, *, status="disconnected"):
    """Exercise the actual options endpoint and device DTO, not a handcrafted availability flag."""
    contract = json.loads(CONTRACT_PATH.read_text())
    tenant, owner, channel, generation = (UUID(f"00000000-0000-4000-8000-{n:012d}") for n in (1, 2, 3, 4))
    actor = Actor("member", account_id=owner)
    row = {"id": UUID(contract["options"]["devices"][0]["id"]), "tenant_id": tenant,
        "owner_account_id": owner, "channel_id": channel, "generation": generation,
        "revision": 2, "status": status, "name": "Dispositivo sintético"}

    class Result:
        def mappings(self): return self
        def all(self): return [row]
    class Database:
        async def execute(self, statement, params):
            assert "FROM subscription_devices" in str(statement)
            assert params == {"t": tenant, "a": owner}
            return Result()
    @asynccontextmanager
    async def owner_transaction(actual_tenant, actual_actor):
        assert (actual_tenant, actual_actor) == (tenant, actor)
        yield Database()
    async def eligible(*args):
        # A campaign may accept work before a device connects; live reads cannot.
        return [{"id": row["id"], "available": True}]
    async def groups(*args): return deepcopy(contract["options"]["groups"])
    async def clock(*args): return NOW
    monkeypatch.setattr(store, "owner_transaction", owner_transaction)
    monkeypatch.setattr(store.campaigns, "device_options", eligible)
    monkeypatch.setattr(store.campaigns, "group_summaries", groups)
    monkeypatch.setattr(store, "clock", clock)
    return await api.options(tenant, Response(), actor)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["stopped", "connecting", "qr", "pairing", "disconnected", "error", "disabled", "removing", "removed"])
async def test_real_options_producer_does_not_offer_offline_devices_for_reads(monkeypatch, status):
    value = await disconnected_options_from_real_producer(monkeypatch, status=status)
    device = value["devices"][0]
    assert device["status"] == status and device["available"] is False
    assert device["blocked_reason"] == store.ERRORS["GROUP_CONTACTS_DEVICE_UNAVAILABLE"]
    if status == "disconnected":
        assert value == json.loads(CONTRACT_PATH.read_text())["options_disconnected"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["stopped", "connecting", "qr", "pairing", "disconnected", "error"])
async def test_offline_groups_and_members_reads_stop_before_the_bridge(monkeypatch, status):
    _, calls = await install_reads(monkeypatch, before={"status": status})
    for selected_group in (None, REMOTE):
        with pytest.raises(HTTPException) as error:
            await api.read_groups(TENANT, ACTOR, DEVICE, selected_group=selected_group)
        assert error.value.status_code == 409
        assert error.value.detail["code"] == "GROUP_CONTACTS_DEVICE_UNAVAILABLE"
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("selected_group", [None, REMOTE])
async def test_disconnect_during_a_read_cannot_publish_groups_or_preview(monkeypatch, selected_group):
    provider = members() if selected_group else {**store.transport_scope(ROW), "groups": [members()["group"]]}
    _, calls = await install_reads(monkeypatch, provider=provider, after={"status": "disconnected"})
    with pytest.raises(HTTPException) as error:
        await api.read_groups(TENANT, ACTOR, DEVICE, selected_group=selected_group)
    assert error.value.status_code == 409 and error.value.detail["code"] == "GROUP_CONTACTS_DEVICE_UNAVAILABLE"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_valid_preview_local_admission_survives_disconnect_without_a_bridge_read(monkeypatch, cipher):
    token = preview()
    _, calls = await install_reads(monkeypatch, before={"status": "disconnected"})
    row, _ = await store.scope(object(), TENANT, DEVICE, ACTOR)
    sealed = store.decode_preview(token, TENANT, ACTOR, NOW)
    store.validate_preview_scope(sealed, row)
    assert row["status"] == "disconnected" and sealed.members[0].phone == PHONE
    assert calls == []


@pytest.mark.asyncio
async def test_members_read_revalidates_after_network_and_returns_only_the_public_snapshot(monkeypatch, cipher):
    boundaries, calls = await install_reads(monkeypatch)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app()), base_url="https://test") as client:
        response = await client.get(url("/members"), params={"device_id": str(DEVICE), "group_id": REMOTE})
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    value = response.json()
    assert value["members"] == members()["members"] and value["total"] == 3
    assert set(value["device"]) == {"id", "name", "status", "available", "blocked_reason", "version"}
    assert "generation" not in value and "settings_revision" not in value and "channel_id" not in value
    assert boundaries == ["enter", "exit", "enter", "exit"]
    assert calls[0][1:] == ("device/group-contacts/members", "POST", {**store.transport_scope(ROW), "group_id": REMOTE})
    assert store.decode_preview(value["preview_token"], TENANT, ACTOR, NOW).group.id == REMOTE


@pytest.mark.asyncio
@pytest.mark.parametrize("after", [{"revision": 3}, {"generation": uuid4()}, HTTPException(403, "revoked")])
async def test_late_provider_success_cannot_survive_revocation_or_device_replacement(monkeypatch, cipher, after):
    await install_reads(monkeypatch, after=after)
    with pytest.raises(HTTPException) as error:
        await api.read_groups(TENANT, ACTOR, DEVICE, selected_group=REMOTE)
    assert error.value.status_code in {403, 409}


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", [HTTPException(409, "private data"), TimeoutError(),
    members(total=4), members(generation=str(uuid4())),
    members(group={"id": "120363222222222222@g.us", "name": "Other", "member_count": 3}),
    {"secret": "not public"}])
async def test_unavailable_or_mismatched_transport_cannot_publish_private_or_stale_data(monkeypatch, cipher, provider):
    await install_reads(monkeypatch, provider=provider)
    with pytest.raises(HTTPException) as error:
        await api.read_groups(TENANT, ACTOR, DEVICE, selected_group=REMOTE)
    assert error.value.status_code in {409, 503}
    assert "private data" not in str(error.value.detail) and "secret" not in str(error.value.detail)


@pytest.mark.parametrize("niche,company,expected", [("servico_assinaturas", "active", True),
    ("servico_assinaturas", "paused", False), ("barbearia", "active", False)])
def test_feature_uses_active_subscription_business_even_without_commercial_plan(niche, company, expected):
    state = plan_permissions.derive_plan(plan_snapshot(niche=niche, grant="none", tenant_status=company), PLAN_NOW)
    assert state["permissions"][store.FEATURE] is expected
    assert state["active"] is False


def test_every_route_uses_the_new_feature_and_the_existing_subscription_boundary():
    for route in api.router.routes:
        relative = route.path.removeprefix(plan_routing.TENANT_PREFIX)
        for method in route.methods:
            assert plan_routing.feature_for_path(relative, method) == store.FEATURE
            assert plan_routing.subscription_workspace_route(relative, method, {})
    assert not plan_routing.subscription_workspace_route("/qr-channels", "GET", {})


@pytest.mark.asyncio
@pytest.mark.parametrize("reason,expected", [
    ("provider_timeout", "GROUP_CONTACTS_BRIDGE_TIMEOUT"),
    ("read_busy", "GROUP_CONTACTS_READ_BUSY"),
    ("context_changed", "GROUP_CONTACTS_CONTEXT_CHANGED"),
    ("invalid_metadata", "GROUP_CONTACTS_PROVIDER_DATA_UNAVAILABLE"),
    ("provider_failed", "GROUP_CONTACTS_BRIDGE_UNAVAILABLE"),
    ("DO_NOT_LEAK_SECRET", "GROUP_CONTACTS_BRIDGE_UNAVAILABLE"),
])
async def test_group_fetch_maps_only_safe_private_diagnostic_codes(monkeypatch, reason, expected):
    _, calls = await install_reads(monkeypatch,
        provider=HTTPException(409, {"group_read_reason": reason,
                                     "not_exposed": "SENSITIVE-CONTACT-AND-SECRET"}))
    with pytest.raises(HTTPException) as error:
        await api.groups(TENANT, Response(), DEVICE, ACTOR)
    assert error.value.detail["code"] == expected
    assert "SENSITIVE" not in json.dumps(error.value.detail)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_group_contact_read_timeout_has_specific_failure(monkeypatch):
    _, calls = await install_reads(monkeypatch, provider=TimeoutError())
    with pytest.raises(HTTPException) as error:
        await api.groups(TENANT, Response(), DEVICE, ACTOR)
    assert error.value.status_code == 504
    assert error.value.detail["code"] == "GROUP_CONTACTS_BRIDGE_TIMEOUT"
    assert len(calls) == 1
