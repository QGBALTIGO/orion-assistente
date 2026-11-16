"""Compatible-format reads leave owner SQL before invoking the lease broker."""
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

from fastapi import HTTPException
import pytest

from app import subscription_url_button_transport as transport
from app import subscription_direct_messages as api
from app import subscription_direct_store as store
from test_subscription_direct_api import client, confirmation, operation, ROOT
from test_subscription_bulk_api import RequestDB
from test_subscription_bulk_policy import ACTOR, KEY, NOW, OWNER, TENANT
from test_subscription_url_button import message


def setup(monkeypatch, *, row=None, status_changes=None):
    device_id, generation, channel, lease_owner = (uuid4() for _ in range(4))
    device = {"id": device_id, "generation": generation, "revision": 3, "channel_id": channel,
        "owner_account_id": OWNER, "status": "connected"}
    record = {"id": channel, "tenant_id": TENANT, "owner_account_id": OWNER, "enabled": True,
        "lease_owner": lease_owner, "lease_expires_at": datetime.now(timezone.utc)+timedelta(seconds=30),
        "fence": 8, "credential_hash": "a"*64}
    data = {"channel_id": str(channel), "device_id": str(device_id), "generation": str(generation),
        "settings_revision": 3, "applied_revision": 3, "fence": 8, "status": "connected",
        "connection_health": {"state": "connected"}, "capabilities": {"url_button": True}}
    data.update(status_changes or {})
    state = SimpleNamespace(db=RequestDB(row), depth=0, calls=[], device=device, record=record, data=data,
        mutate=None, snapshots=0)
    @asynccontextmanager
    async def owner(tenant, actor, **kwargs):
        assert tenant == TENANT and actor == ACTOR and kwargs == {"feature": "direct_messages.manage"}
        state.depth += 1
        try: yield state.db
        finally: state.depth -= 1
    async def check_device(db, tenant, identity, *, actor_id):
        assert state.depth == 1 and db is state.db and tenant == TENANT and identity == device_id and actor_id == OWNER
        if state.device["owner_account_id"] != actor_id:
            raise store.fail("DIRECT_NOT_FOUND", 404)
        state.snapshots += 1
        return deepcopy(state.device), deepcopy(state.record)
    async def control(row, action, method, payload=None):
        assert state.depth == 0, "A bridge lease callback would deadlock under owner SQL locks"
        assert row["id"] == channel and row["tenant_id"] == TENANT and action == "device/status" and method == "GET" and payload is None
        state.calls.append((action, method))
        if state.mutate: state.mutate()
        return deepcopy(state.data)
    monkeypatch.setattr(store.shared, "owner_transaction", owner)
    monkeypatch.setattr(store.shared, "check_device", check_device)
    monkeypatch.setattr(transport.channels, "_bridge_control", control)
    return state


def body(state):
    return {"idempotency_key": str(KEY), "message": message(device_id=str(state.device["id"]))}


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [{"capabilities": {}}, {"capabilities": {"url_button": False}},
    {"capabilities": {"url_button": 1}}, {"capabilities": {"url_button": "true"}},
    {"channel_id": str(uuid4())}, {"device_id": str(uuid4())}, {"generation": str(uuid4())},
    {"settings_revision": 4}, {"applied_revision": 2}, {"fence": 9}, {"fence": True},
    {"status": "degraded"}, {"connection_health": {"state": "needs_attention"}}])
async def test_older_or_mismatched_bridge_never_creates_cta_review(monkeypatch, changes):
    state = setup(monkeypatch, status_changes=changes)
    async with client() as http:
        result = await http.post(ROOT+"/review", json=body(state))
    assert result.status_code == 409 and result.json()["detail"]["code"] == "DIRECT_FORMAT_UNAVAILABLE"
    assert state.calls == [("device/status", "GET")] and state.snapshots == 1
    assert all(sql.startswith("SELECT") for sql, _ in state.db.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["generation", "revision", "owner", "fence", "lease_owner", "credential", "expired"])
async def test_authority_changes_during_fresh_get_fail_before_review_snapshot(monkeypatch, changed):
    state = setup(monkeypatch)
    def mutate():
        if changed == "generation": state.device["generation"] = uuid4()
        elif changed == "revision": state.device["revision"] += 1
        elif changed == "owner": state.device["owner_account_id"] = uuid4()
        elif changed == "fence": state.record["fence"] += 1
        elif changed == "lease_owner": state.record["lease_owner"] = uuid4()
        elif changed == "credential": state.record["credential_hash"] = "b"*64
        else: state.record["lease_expires_at"] = datetime.now(timezone.utc)-timedelta(seconds=1)
    state.mutate = mutate
    async with client() as http:
        result = await http.post(ROOT+"/review", json=body(state))
    assert result.status_code == (404 if changed == "owner" else 409)
    assert state.calls == [("device/status", "GET")]
    assert all(sql.startswith("SELECT") for sql, _ in state.db.calls)


@pytest.mark.asyncio
async def test_valid_support_is_read_outside_sql_and_rechecked_before_capture(monkeypatch):
    state = setup(monkeypatch)
    async def capture(db, tenant, actor, value, key):
        assert state.depth == 1 and state.snapshots == 2 and state.calls == [("device/status", "GET")]
        return {"message": value, "rows": [], "counts": {"eligible": 1}}
    async def save(db, tenant, actor, key, kind, values, **kwargs):
        return operation(snapshot=kwargs["snapshot"], preview=kwargs["preview"])
    monkeypatch.setattr(store, "capture_review", capture)
    monkeypatch.setattr(store, "review_preview", lambda *args: {"review_version": "a"*64})
    monkeypatch.setattr(store.bulk, "save_request", save)
    async with client() as http:
        result = await http.post(ROOT+"/review", json=body(state))
    assert result.status_code == 201, result.text
    assert state.depth == 0 and state.snapshots == 2


@pytest.mark.asyncio
async def test_known_review_replay_does_not_need_any_transport_call(monkeypatch):
    state = setup(monkeypatch)
    normalized = api.schemas.ReviewDirectMessage.model_validate(body(state)).model_dump(mode="json")
    state.db.row = operation(request_fingerprint=store.fingerprint(normalized))
    async with client() as http:
        result = await http.post(ROOT+"/review", json=body(state))
    assert result.status_code == 201 and result.json()["replayed"] is True
    assert state.calls == [] and state.snapshots == 0


@pytest.mark.asyncio
async def test_successful_cta_confirm_replay_remains_available_when_transport_is_down(monkeypatch):
    state = setup(monkeypatch)
    state.db.row = operation(status="succeeded", expires_at=NOW-timedelta(days=1), snapshot={"message": body(state)["message"]},
        result={"send": {"send_id": str(uuid4()), "version": 1}, "saved_template": None})
    async with client() as http:
        result = await http.post(ROOT+"/confirm", json=confirmation())
    assert result.status_code == 202 and result.json()["replayed"] is True
    assert state.calls == [] and state.snapshots == 0


@pytest.mark.asyncio
async def test_missing_support_at_confirm_preserves_prepared_request_without_admission(monkeypatch):
    state = setup(monkeypatch, status_changes={"capabilities": {}})
    state.db.row = operation(snapshot={"message": body(state)["message"]})
    before = deepcopy(state.db.row)
    async with client() as http:
        result = await http.post(ROOT+"/confirm", json=confirmation())
    assert result.status_code == 409 and result.json()["detail"]["code"] == "DIRECT_FORMAT_UNAVAILABLE"
    assert state.db.row == before and all(sql.startswith("SELECT") for sql, _ in state.db.calls)


@pytest.mark.asyncio
async def test_expired_fresh_proof_is_not_reused_for_admission(monkeypatch):
    state = setup(monkeypatch)
    async with store.owner_transaction(TENANT, ACTOR) as db:
        expected = await transport.snapshot(db, TENANT, ACTOR, state.device["id"])
    proof = await transport.compatible(expected)
    monkeypatch.setattr(transport.time, "monotonic", lambda: proof.verified_at+21)
    async with store.owner_transaction(TENANT, ACTOR) as db:
        with pytest.raises(HTTPException):
            await transport.revalidate(db, TENANT, ACTOR, state.device["id"], proof)


@pytest.mark.asyncio
async def test_transport_snapshot_repr_never_contains_the_credential_envelope(monkeypatch):
    state = setup(monkeypatch)
    state.record["credential_encrypted"] = "SYNTHETIC_PRIVATE_ENVELOPE_DO_NOT_LOG"
    async with store.owner_transaction(TENANT, ACTOR) as db:
        expected = await transport.snapshot(db, TENANT, ACTOR, state.device["id"])
    assert state.record["credential_encrypted"] not in repr(expected)
