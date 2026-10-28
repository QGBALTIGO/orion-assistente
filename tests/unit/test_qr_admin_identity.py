"""Native ADMIN QR ownership boundaries with synthetic DB/provider responses."""
import json
from types import SimpleNamespace
from uuid import uuid4

from cryptography.fernet import Fernet
from fastapi import FastAPI, HTTPException, Response
import httpx
import pytest

from app import qr_channels as channels, qr_legacy
from app.catalog_api import Actor, authenticate


class Result:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def mappings(self):
        return self

    def one_or_none(self):
        return self.value

    def one(self):
        assert self.value is not None
        return self.value

    def all(self):
        return self.value


class Database:
    def __init__(self, tenant, account):
        self.tenant, self.account = tenant, account
        self.memberships = {(tenant, account): "owner"}
        self.rows = {}
        self.calls = []

    def begin(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def execute(self, statement, params):
        sql = str(statement)
        self.calls.append((sql, params))
        if "pg_advisory_xact_lock" in sql:
            return Result(None)
        if "SELECT role FROM tenant_memberships" in sql:
            assert "AND active" in sql
            return Result(self.memberships.get((params["tenant"], params["account"])))
        if "SELECT * FROM qr_channels" in sql:
            return Result(next((row for row in self.rows.values()
                                if row["tenant_id"] == params["tenant"]
                                and row["idempotency_key"] == params["key"]), None))
        if "INSERT INTO qr_channels" in sql:
            row = {"id": uuid4(), "tenant_id": params["tenant"], "owner_account_id": params["account"],
                   "provider": params["provider"], "label": params["label"], "enabled": False,
                   "assistant_enabled": False, "revision": 1,
                   "allowed_senders": json.loads(params["senders"]),
                   "credential_hash": params["digest"], "credential_encrypted": params["encrypted"],
                   "idempotency_key": params["key"], "request_fingerprint": params["fingerprint"]}
            self.rows[row["id"]] = row
            return Result(row)
        if "SELECT c.* FROM qr_channels" in sql:
            row = self.rows.get(params["id"])
            if row and (row["tenant_id"] != params["tenant"] or row["owner_account_id"] != params["account"]):
                row = None
            return Result(row)
        if "UPDATE qr_channels" in sql:
            row = self.rows.get(params["id"])
            if not row or row["tenant_id"] != params["tenant"] or row["owner_account_id"] != params["account"] or row["revision"] != params["revision"]:
                return Result(None)
            row["revision"] += 1
            if params["enabled"] is not None:
                row["enabled"] = params["enabled"]
            if params["assistant"] is not None:
                row["assistant_enabled"] = params["assistant"]
            return Result({key: row[key] for key in (*channels.SAFE_FIELDS, "allowed_senders")})
        if "FROM qr_channels WHERE tenant_id=:tenant ORDER BY" in sql:
            return Result([row for row in self.rows.values() if row["tenant_id"] == params["tenant"]])
        raise AssertionError(f"Unexpected DB operation: {sql}")


@pytest.fixture
def native(monkeypatch):
    from app import legacy_admin_identity

    tenant, account = uuid4(), uuid4()
    db = Database(tenant, account)
    state = SimpleNamespace(tenant=tenant, account=account, db=db,
                            actor=Actor("admin", account_id=account), verified=True,
                            identity_error=None, identity_calls=[], network=[])

    async def lookup(session, account_id=None):
        assert session is db
        state.identity_calls.append(account_id)
        if state.identity_error:
            raise state.identity_error
        return {"id": account_id, "auth_provider": "legacy_admin", "role": "admin"} if state.verified and account_id == account else None

    async def no_bridge(*args):
        state.network.append(args)
        raise AssertionError("No real transport is allowed in this test")

    monkeypatch.setattr(legacy_admin_identity, "lookup_identity", lookup)
    monkeypatch.setattr(channels, "support_session_factory", lambda: lambda: db)
    monkeypatch.setattr(channels, "_bridge_control", no_bridge)
    monkeypatch.setenv("QR_CHANNELS_ENABLED", "1")
    monkeypatch.setenv("INTEGRATION_FERNET_KEY", Fernet.generate_key().decode())
    return state


async def registered(state):
    payload = channels.CreateChannel(label="QR sintetico", allowed_senders=["5567999999901"],
                                     idempotency_key=uuid4())
    row = await channels.create_channel(state.db, state.tenant, payload, state.actor)
    return row, payload


@pytest.mark.asyncio
async def test_native_admin_uses_real_owner_id_and_keeps_idempotent_credential_contract(native):
    state = native
    first, payload = await registered(state)
    repeated = await channels.create_channel(state.db, state.tenant, payload, state.actor)
    assert first["owner_account_id"] == state.account
    assert first["enabled"] is False and first["assistant_enabled"] is False
    assert repeated["id"] == first["id"] and repeated["credential"] is None
    assert repeated["credential_issued"] is False
    stored = state.db.rows[first["id"]]
    assert first["credential"] not in stored["credential_encrypted"]
    assert channels._cipher().decrypt(stored["credential_encrypted"].encode()).decode() == first["credential"]
    assert len([sql for sql, _ in state.db.calls if "INSERT INTO" in sql]) == 1
    membership = [sql for sql, _ in state.db.calls if "SELECT role FROM" in sql]
    assert membership and all("FOR UPDATE" in sql for sql in membership)
    assert not state.network


@pytest.mark.asyncio
@pytest.mark.parametrize("role,identity", [
    ("support", "uuid"), ("support", None), ("viewer", "uuid"),
    ("admin", None), ("admin", "text"), ("member", None), ("member", "text"),
])
async def test_support_reader_and_non_uuid_admin_cannot_get_owner_authority(native, role, identity):
    state = native
    account = state.account if identity == "uuid" else str(state.account) if identity == "text" else None
    with pytest.raises(HTTPException) as caught:
        await channels._owner(state.db, state.tenant, Actor(role, account_id=account))
    assert caught.value.status_code == 403
    assert not state.identity_calls and not state.db.calls and not state.network


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["unverified_provider", "disabled_opt_in", "stale_configuration"])
async def test_unverified_native_identity_never_reaches_membership_or_channel_write(native, failure):
    state = native
    state.verified = False
    if failure == "stale_configuration":
        state.identity_error = ValueError("invalid native identity configuration")
    with pytest.raises(HTTPException) as caught:
        await registered(state)
    assert caught.value.status_code == 403
    assert not any("SELECT role FROM" in sql or "INSERT INTO" in sql for sql, _ in state.db.calls)
    assert not state.db.rows and not state.network


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", [{"id": uuid4()}, {"auth_provider": "email"}, {"role": "member"}])
async def test_identity_metadata_must_match_the_native_admin_account(native, monkeypatch, changed):
    from app import legacy_admin_identity

    state = native

    async def mismatched(*args, **kwargs):
        return {"id": state.account, "auth_provider": "legacy_admin", "role": "admin", **changed}

    monkeypatch.setattr(legacy_admin_identity, "lookup_identity", mismatched)
    with pytest.raises(HTTPException) as caught:
        await channels._owner(state.db, state.tenant, state.actor)
    assert caught.value.status_code == 403 and not state.db.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("membership", [None, "viewer", "editor"])
async def test_native_identity_alone_does_not_grant_tenant_ownership(native, membership):
    state = native
    state.db.memberships[(state.tenant, state.account)] = membership
    with pytest.raises(HTTPException) as caught:
        await registered(state)
    assert caught.value.status_code == 403 and not state.db.rows
    listing = await channels.list_channels(state.tenant, Response(), state.actor)
    assert listing["access"]["can_manage"] is False
    assert not any(listing["capabilities"][key] for key in ("start", "stop", "qr", "change_state"))
    assert not state.network


@pytest.mark.asyncio
async def test_native_owner_capabilities_and_allowlist_remain_channel_owner_scoped(native):
    state = native
    own, _ = await registered(state)
    other_id = uuid4()
    state.db.rows[other_id] = {**state.db.rows[own["id"]], "id": other_id, "owner_account_id": uuid4(),
                               "allowed_senders": ["5567999999902"]}
    state.db.calls.clear()
    listing = await channels.list_channels(state.tenant, Response(), state.actor)
    assert listing["access"]["can_manage"] is True
    assert all(listing["capabilities"][key] for key in ("start", "stop", "qr", "change_state"))
    by_id = {item["id"]: item for item in listing["items"]}
    assert by_id[str(own["id"])]["can_manage"] is True
    assert by_id[str(own["id"])]["allowed_senders"] == ["5567999999901"]
    assert by_id[str(other_id)]["can_manage"] is False and "allowed_senders" not in by_id[str(other_id)]
    assert "credential_hash" not in str(listing) and own["credential"] not in str(listing)
    assert all("FOR UPDATE" not in sql for sql, _ in state.db.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["membership_revoked", "wrong_provider"])
async def test_admin_list_redacts_allowlist_when_ownership_is_no_longer_valid(native, damage):
    state = native
    await registered(state)
    if damage == "membership_revoked":
        state.db.memberships[(state.tenant, state.account)] = None
    else:
        state.verified = False
    listing = await channels.list_channels(state.tenant, Response(), state.actor)
    assert listing["access"]["can_manage"] is False
    assert all(item["can_manage"] is False and "allowed_senders" not in item for item in listing["items"])


@pytest.mark.asyncio
async def test_member_owner_flow_does_not_require_native_admin_identity(native):
    state = native
    state.identity_error = AssertionError("Member flow must not use native ADMIN identity")
    await channels._owner(state.db, state.tenant, Actor("member", account_id=state.account))
    assert not state.identity_calls


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["member", "admin"])
async def test_channel_pairing_preserves_real_owner_authorization_and_configuration(native, monkeypatch, role):
    state = native
    row, _ = await registered(state)
    stored = state.db.rows[row["id"]]
    stored["enabled"] = True
    before = dict(stored)
    calls = []
    async def bridge(owned, action, method, payload=None):
        calls.append((owned["id"], action, method, payload))
        if action == "status":
            return {"status": "qr_ready", "capabilities": {"pair": True}}
        return {"status": "pairing", "code": "ABCD-EFGH", "expires_in_ms": 90000}
    monkeypatch.setattr(channels, "_bridge_control", bridge)
    result = await channels.pair_control(state.tenant, row["id"],
        channels.ChannelPairCommand(expected_revision=stored["revision"], phone="5567999999901"),
        Response(), Actor(role, account_id=state.account))
    assert result["code"] == "ABCD-EFGH" and result["channel_id"] == str(row["id"])
    assert stored == before
    assert calls == [(row["id"], "status", "GET", None), (row["id"], "pair", "POST", {"phone": "5567999999901"})]


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["status", "pair"])
async def test_pairing_discards_response_after_real_owner_membership_is_revoked(native, monkeypatch, phase):
    state = native
    row, _ = await registered(state)
    stored = state.db.rows[row["id"]]
    stored["enabled"] = True
    calls = []
    async def bridge(owned, action, method, payload=None):
        calls.append(action)
        if action == phase:
            state.db.memberships[(state.tenant, state.account)] = None
        if action == "status":
            return {"status": "qr_ready", "capabilities": {"pair": True}}
        return {"status": "pairing", "code": "ABCD-EFGH", "expires_in_ms": 90000}
    monkeypatch.setattr(channels, "_bridge_control", bridge)
    with pytest.raises(HTTPException) as caught:
        await channels.pair_control(state.tenant, row["id"],
            channels.ChannelPairCommand(expected_revision=stored["revision"], phone="5567999999901"), Response(), state.actor)
    assert caught.value.status_code == 403
    assert calls == (["status"] if phase == "status" else ["status", "pair"])


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["editor", "viewer", None])
async def test_non_owner_cannot_request_code_even_with_admin_session(native, role):
    state = native
    row, _ = await registered(state)
    state.db.rows[row["id"]]["enabled"] = True
    state.db.memberships[(state.tenant, state.account)] = role
    with pytest.raises(HTTPException) as caught:
        await channels.pair_control(state.tenant, row["id"],
            channels.ChannelPairCommand(expected_revision=1, phone="5567999999901"), Response(), state.actor)
    assert caught.value.status_code == 403 and not state.network


@pytest.mark.asyncio
async def test_native_admin_cannot_select_another_tenant_or_another_owners_channel(native):
    state = native
    own, _ = await registered(state)
    foreign = uuid4()
    with pytest.raises(HTTPException) as caught:
        await channels._owned_channel(foreign, own["id"], state.actor)
    assert caught.value.status_code == 403
    state.db.rows[own["id"]]["owner_account_id"] = uuid4()
    with pytest.raises(HTTPException) as caught:
        await channels._owned_channel(state.tenant, own["id"], state.actor)
    assert caught.value.status_code == 404 and not state.network


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["membership_revoked", "wrong_provider"])
async def test_native_qr_result_is_discarded_if_authority_changes_in_flight(native, monkeypatch, damage):
    state = native
    row, _ = await registered(state)
    state.db.rows[row["id"]]["enabled"] = True

    async def bridge(*args):
        if damage == "membership_revoked":
            state.db.memberships[(state.tenant, state.account)] = None
        else:
            state.verified = False
        return {"image": "data:image/png;base64,iVBORw0KGgo=", "expires_in_ms": 30000}

    monkeypatch.setattr(channels, "_bridge_control", bridge)
    with pytest.raises(HTTPException) as caught:
        await channels._control(state.tenant, row["id"], state.actor, "qr")
    assert caught.value.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["start", "stop", "qr"])
async def test_native_admin_still_cannot_repair_or_disconnect_the_primary_session(native, action):
    state = native
    row, _ = await registered(state)
    alias = qr_legacy.channel_id(state.tenant)
    state.db.rows[alias] = {**state.db.rows.pop(row["id"]), "id": alias, "enabled": True}
    with pytest.raises(HTTPException) as caught:
        await channels._control(state.tenant, alias, state.actor, action)
    assert caught.value.status_code == 409 and not state.network
    assert state.db.rows[alias]["enabled"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_native_admin_cannot_enable_or_disable_primary_session(native, enabled):
    state = native
    with pytest.raises(HTTPException) as caught:
        await channels.change_state(state.tenant, qr_legacy.channel_id(state.tenant),
                                    channels.ChannelState(expected_revision=1, enabled=enabled), state.actor)
    assert caught.value.status_code == 409
    assert not state.db.calls and not state.network


@pytest.mark.asyncio
async def test_native_owner_can_control_primary_assistant_without_repairing_or_disabling_session(native):
    state = native
    row, _ = await registered(state)
    alias = qr_legacy.channel_id(state.tenant)
    original = {**state.db.rows.pop(row["id"]), "id": alias, "enabled": True}
    state.db.rows[alias] = original
    credential_before = (original["credential_hash"], original["credential_encrypted"])
    result = await channels.change_state(state.tenant, alias,
                                         channels.ChannelState(expected_revision=1, assistant_enabled=True), state.actor)
    assert result["enabled"] is True and result["assistant_enabled"] is True
    assert result["owner_account_id"] == str(state.account)
    assert credential_before == (state.db.rows[alias]["credential_hash"], state.db.rows[alias]["credential_encrypted"])
    assert "credential_encrypted" not in result and "credential_hash" not in result
    assert not state.network


@pytest.mark.asyncio
async def test_no_delete_route_is_added_for_the_existing_primary_qr(native):
    state = native
    api = FastAPI()
    api.include_router(channels.router)
    api.dependency_overrides[authenticate] = lambda: state.actor
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="https://test") as client:
        response = await client.delete(f"/api/v1/tenants/{state.tenant}/qr-channels/{qr_legacy.channel_id(state.tenant)}")
    assert response.status_code == 405
    assert not state.db.calls and not state.network
