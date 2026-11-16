"""Preparation can only re-read its original live grant, never acquire another."""
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
from uuid import uuid4

from fastapi import HTTPException
import pytest
from pydantic import ValidationError

from app import inbox_control, inbox_send_recheck as inbox, subscription_devices as devices
from app.subscription_device_schemas import EffectRequest
from app.inbox_runtime import fingerprint
from test_subscription_campaign_policy import Rows


NOW = datetime.now(timezone.utc)
TENANT, CHANNEL, DEVICE, GENERATION, ACCOUNT, OWNER, OPERATION, CONTROL = (uuid4() for _ in range(8))
PHONE = "5567999999901"


def identity(**changes):
    return SimpleNamespace(tenant_id=TENANT, channel_id=CHANNEL, owner_account_id=ACCOUNT,
        lease_owner=OWNER, fence=5, assistant_enabled=True, **changes)


class DB:
    def __init__(self):
        self.now, self.calls, self.revoked = NOW, [], False
        self.message = {"id": OPERATION, "tenant_id": TENANT, "control_id": CONTROL, "direction": "out", "status": "sending",
            "source": "assistant", "body": "Resposta sintética", "fingerprint": fingerprint(PHONE, "Resposta sintética"),
            "control_revision": 3, "envelope": {"version": 1, "kind": "text"}}
    async def execute(self, statement, values=None):
        sql = str(statement); self.calls.append((sql, deepcopy(values)))
        if sql.startswith("SELECT * FROM inbox_messages"): return Rows([deepcopy(self.message)] if self.message else [])
        if sql == "SELECT clock_timestamp()": return Rows([self.now])
        if sql.startswith("UPDATE inbox_messages SET envelope="):
            self.message["envelope"].update(json.loads(values["grant"])); return Rows([])
        if sql.startswith("SELECT COALESCE((SELECT revoked"): return Rows([self.revoked])
        if sql.startswith("SELECT status FROM subscription_device_effects"): return Rows(["accepted"])
        raise AssertionError(sql)


def slot(**changes):
    return inbox_control.SendSlot(phone=PHONE, operation_id=OPERATION, source="assistant", control_revision=3,
        text="Resposta sintética", fingerprint=fingerprint(PHONE, "Resposta sintética"), **changes)


def row():
    return {"id": CONTROL, "revision": 3, "pending_operation": OPERATION,
        "pending_fingerprint": fingerprint(PHONE, "Resposta sintética"), "mode": "ai"}


@pytest.mark.asyncio
async def test_inbox_repeated_checks_preserve_every_persisted_field_and_original_expiry():
    db, current, payload = DB(), row(), slot()
    await inbox.bind(db, identity(), current, payload)
    before = deepcopy(db.message)
    db.calls.clear()
    for _ in range(3): await inbox.recheck(db, identity(), current, payload)
    assert db.message == before and all(sql.startswith("SELECT") for sql, _ in db.calls)
    assert db.message["envelope"]["version"] == 1
    await inbox.bind(db, identity(), current, payload)
    assert db.message == before


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["missing", "expired", "owner", "fence", "account", "revision", "channel", "operation",
    "fingerprint", "text", "source", "control", "accepted", "uncertain", "no_body", "forged_phone"])
async def test_inbox_recheck_cannot_adopt_another_or_expired_attempt(changed):
    db, current, payload, who = DB(), row(), slot(), identity()
    await inbox.bind(db, who, current, payload)
    if changed == "missing": db.message["envelope"].pop(inbox.KEY)
    elif changed == "expired": db.now += timedelta(seconds=31)
    elif changed in {"owner", "account", "channel"}: setattr(who, {"owner":"lease_owner", "account":"owner_account_id", "channel":"channel_id"}[changed], uuid4())
    elif changed == "fence": who.fence += 1
    elif changed == "revision": current["revision"] += 1
    elif changed == "operation": current["pending_operation"] = uuid4()
    elif changed == "fingerprint": current["pending_fingerprint"] = "b"*64
    elif changed == "text": payload = payload.model_copy(update={"text":"Outro texto"})
    elif changed == "source": payload = payload.model_copy(update={"source":"manual"})
    elif changed == "control": db.message["control_id"] = uuid4()
    elif changed in {"accepted", "uncertain"}: db.message["status"] = changed
    elif changed == "no_body": payload = payload.model_copy(update={"text":None})
    else: payload = payload.model_copy(update={"phone":"5567999999902"})
    before = deepcopy(db.message); db.calls.clear()
    with pytest.raises(HTTPException): await inbox.recheck(db, who, current, payload)
    assert db.message == before and all(sql.startswith("SELECT") for sql, _ in db.calls)


@pytest.mark.parametrize("value", ["true", 1, None])
def test_new_wire_recheck_is_opt_in_strict_boolean(value):
    with pytest.raises(ValidationError): slot(recheck=value)


def setup_device(monkeypatch):
    db = DB()
    device = {"id": DEVICE, "tenant_id": TENANT, "generation": GENERATION, "revision": 3, "removed_at": None}
    effect = {"id": OPERATION, "tenant_id": TENANT, "device_id": DEVICE, "type": "send_text", "purpose": "absence",
        "status": "running", "generation": GENERATION, "settings_revision": 3, "claimed_owner": OWNER, "claimed_fence": 5,
        "attempts": 1, "authorized_at": NOW, "expires_at": NOW+timedelta(minutes=5), "due_at": NOW-timedelta(seconds=1),
        "previous_effect": None, "payload": {"phone": PHONE, "text": "Mensagem sintética"}}
    state = SimpleNamespace(db=db, device=device, effect=effect, policy=True, authority=True)
    @asynccontextmanager
    async def private(request): yield db, device, {}, OWNER, 5
    async def get(*args): return deepcopy(effect)
    async def policy(*args): return state.policy
    async def authority(*args): return state.authority
    async def forbidden(*args): pytest.fail("Read-only recheck tried to reserve a cooldown")
    monkeypatch.setattr(devices, "_private_transaction", private)
    monkeypatch.setattr(devices, "_effect", get)
    monkeypatch.setattr(devices, "_policy_still_allows", policy)
    monkeypatch.setattr(devices, "_reserve_cooldown", forbidden)
    from app import subscription_sent_messages
    monkeypatch.setattr(subscription_sent_messages, "message_authority", authority)
    return state


@pytest.mark.asyncio
async def test_device_running_recheck_preserves_claim_payload_attempt_counter_and_phase(monkeypatch):
    s = setup_device(monkeypatch); before = deepcopy(s.effect)
    for _ in range(3):
        value = await devices.authorize_effect(CHANNEL, EffectRequest(operation_id=OPERATION, recheck=True), None)
        assert value["authorized"] and value["payload"] == before["payload"]
    assert s.effect == before and all(sql.startswith("SELECT") for sql, _ in s.db.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["owner", "fence", "generation", "revision", "removed", "expired", "policy", "authority",
    "optout", "pending", "accepted", "uncertain", "webhook", "reject_call", "transcribe"])
async def test_device_recheck_denial_never_reclaims_or_relabels_an_uncertain_attempt(monkeypatch, changed):
    s = setup_device(monkeypatch)
    if changed == "owner": s.effect["claimed_owner"] = uuid4()
    elif changed == "fence": s.effect["claimed_fence"] += 1
    elif changed == "generation": s.effect["generation"] = uuid4()
    elif changed == "revision": s.effect["settings_revision"] += 1
    elif changed == "removed": s.device["removed_at"] = NOW
    elif changed == "expired": s.effect["expires_at"] = NOW-timedelta(seconds=1)
    elif changed in {"policy", "authority"}: setattr(s, changed, False)
    elif changed == "optout": s.db.revoked = True
    elif changed in {"pending", "accepted", "uncertain"}: s.effect["status"] = changed
    else: s.effect["type"] = changed
    before = deepcopy(s.effect)
    value = await devices.authorize_effect(CHANNEL, EffectRequest(operation_id=OPERATION, recheck=True), None)
    assert value["authorized"] is False and s.effect == before
    assert all(sql.startswith("SELECT") for sql, _ in s.db.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", [None, "expired", "consent_revoked", "conversation_changed", "approval_revoked"])
async def test_automatic_recheck_uses_the_current_producer_policy_without_mutating_it(monkeypatch, reason):
    from app import automation_runtime
    job = {"id":OPERATION,"contact_e164":"+"+PHONE,"body":"Resposta sintética","event":"campaign"}
    calls = []
    class ProducerDB(DB):
        async def execute(self, statement, values=None):
            if str(statement).startswith("SELECT * FROM automation_jobs"):
                self.calls.append((str(statement),values))
                assert values == {"t":TENANT,"id":OPERATION,"channel":CHANNEL}
                return Rows([deepcopy(job)])
            return await super().execute(statement,values)
    async def settings(db, tenant):
        assert tenant == TENANT; calls.append("current_settings"); return {"version":7}
    async def current(db, tenant, value, setting):
        assert tenant == TENANT and value == job and setting == {"version":7}
        calls.append("current_policy"); return reason
    monkeypatch.setattr(automation_runtime,"execution_enabled",lambda:True)
    monkeypatch.setattr(automation_runtime,"settings_for",settings)
    monkeypatch.setattr(automation_runtime,"job_reason",current)
    db = ProducerDB(); before = deepcopy(job)
    request = slot().model_copy(update={"source":"automation","control_revision":None,"recheck":True})
    if reason:
        with pytest.raises(HTTPException): await inbox.automatic_current(db,identity(),request)
    else: await inbox.automatic_current(db,identity(),request)
    assert job == before and calls == ["current_settings","current_policy"]
    assert all(sql.startswith("SELECT") for sql,_ in db.calls)
