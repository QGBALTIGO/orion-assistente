"""Frozen authority and transport evidence, with no external send/provider."""
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

from fastapi import HTTPException
import pytest

from app import subscription_campaign_runtime as runtime
from app import subscription_campaign_store as store
from app import subscription_sent_messages as history
from app.subscription_device_schemas import EffectRequest
from test_subscription_campaign_policy import Rows, definition

NOW = datetime(2026, 11, 4, 12, tzinfo=timezone.utc)
TENANT, OWNER, DEVICE, GENERATION, CHANNEL, GROUP, CAMPAIGN, RECIPIENT, EFFECT, CLAIM, EVENT = (uuid4() for _ in range(11))
PHONE = "5567999999901"


def device(**changes):
    return {"tenant_id": TENANT, "id": DEVICE, "owner_account_id": OWNER, "channel_id": CHANNEL,
        "generation": GENERATION, "revision": 3, "removed_at": None, "status": "connected"} | changes


def campaign(**changes):
    return {"tenant_id": TENANT, "id": CAMPAIGN, "owner_account_id": OWNER, "status": "running", "archived_at": None,
        "run_version": "a"*64, "definition": definition(), "template_id": None, "template_revision": None,
        "snapshot": {"consent": {"confirmed": True}, "devices": [{"id": str(DEVICE), "channel_id": str(CHANNEL),
            "generation": str(GENERATION), "settings_revision": 3, "owner_account_id": str(OWNER)}], "template": None}} | changes


def recipient(**changes):
    return {"tenant_id": TENANT, "id": RECIPIENT, "campaign_id": CAMPAIGN, "run_version": "a"*64,
        "device_id": DEVICE, "generation": GENERATION, "settings_revision": 3, "status": "running",
        "plan": [{"type": "text", "text": "Conteúdo confirmado"}], "phone": PHONE,
        "source_group_ids": [GROUP], "permission_version": 0, "admitted_at": NOW-timedelta(seconds=2),
        "finished_at": None, "error_code": None} | changes


def effect(**changes):
    return {"tenant_id": TENANT, "id": EFFECT, "device_id": DEVICE, "generation": GENERATION,
        "settings_revision": 3, "event_id": EVENT, "type": "campaign_action", "purpose": "campaign_action",
        "campaign_id": CAMPAIGN, "campaign_recipient_id": RECIPIENT, "campaign_part_index": 0,
        "payload": {"phone": PHONE, "action": {"type": "text", "text": "Conteúdo confirmado"}},
        "status": "pending", "attempts": 0, "claimed_owner": None, "claimed_fence": None,
        "due_at": NOW-timedelta(seconds=2), "created_at": NOW-timedelta(seconds=2), "expires_at": NOW+timedelta(minutes=5),
        "previous_effect": None, "authorized_at": None, "finished_at": None, "error_code": None} | changes


class DB:
    def __init__(self, *, campaign_row=None, recipient_row=None, effects=None, allowed=None, previous=None):
        self.campaign = campaign_row or campaign()
        self.recipient = recipient_row or recipient()
        self.effects = effects or [effect()]
        self.allowed = {"revoked": False, "version": 0, "member": True} if allowed is None else allowed
        self.previous, self.calls = previous, []

    async def execute(self, query, params=None):
        sql, params = str(query), params or {}
        self.calls.append((sql, params))
        if sql == "SELECT clock_timestamp()": return Rows([NOW])
        if sql.startswith("SELECT * FROM subscription_campaigns"): return Rows([self.campaign])
        if sql.startswith("SELECT * FROM subscription_campaign_recipients"): return Rows([self.recipient])
        if sql.startswith("SELECT COALESCE(p.revoked"): return Rows([self.allowed])
        if sql.startswith("SELECT id,status FROM subscription_device_effects"): return Rows([self.previous] if self.previous else [])
        if sql.startswith("SELECT * FROM subscription_device_effects"): return Rows(self.effects)
        if sql.startswith("UPDATE subscription_device_effects SET status='running'"):
            item = next(row for row in self.effects if row["id"] == params["id"])
            item.update(status="running", attempts=1, authorized_at=params["now"], claimed_owner=params["owner"], claimed_fence=params["fence"])
            return Rows([])
        if sql.startswith("UPDATE subscription_device_effects SET status='not_sent'"):
            for item in self.effects:
                if item["status"] == "pending" and ("code" not in params or item["id"] == params["id"]):
                    item.update(status="not_sent", error_code=params.get("code", "CAMPAIGN_PREDECESSOR_FAILED"), finished_at=params["now"])
            return Rows([])
        if sql.startswith("UPDATE subscription_campaign_recipients SET status="):
            self.recipient.update(status=params["status"], error_code=params["error"],
                finished_at=None if params["status"] in {"pending", "running"} else self.recipient["finished_at"] or params["now"])
            return Rows([])
        if sql.startswith("UPDATE subscription_campaigns c SET status='completed'"): return Rows([])
        raise AssertionError(sql)


@pytest.fixture
def authority(monkeypatch):
    calls = []
    async def workspace(db, tenant, **kwargs):
        calls.append((tenant, kwargs))
    async def check_device(db, tenant, identity, **kwargs):
        assert tenant == TENANT and identity == DEVICE and kwargs["actor_id"] == OWNER
        return device(), {"enabled": True}
    async def order(*args): return True
    monkeypatch.setattr(store.shared, "workspace", workspace)
    monkeypatch.setattr(store.shared, "check_device", check_device)
    monkeypatch.setattr(history, "message_authority", order)
    return calls


@pytest.mark.asyncio
async def test_claim_once_then_pure_final_recheck_returns_the_identical_bound_permit(authority):
    db = DB()
    request = EffectRequest(operation_id=EFFECT)
    first = await runtime.authorize_campaign_effect(db, device(), db.effects[0], CLAIM, 8, request)
    assert first == {"authorized": True, "operation_id": str(EFFECT), "type": "campaign_action", "purpose": "campaign_action",
        "device_id": str(DEVICE), "generation": str(GENERATION), "settings_revision": 3,
        "campaign_id": str(CAMPAIGN), "recipient_id": str(RECIPIENT), "run_version": "a"*64, "part_index": 0,
        "payload": {"phone": PHONE, "action": {"type": "text", "text": "Conteúdo confirmado"}}}
    assert not (await runtime.authorize_campaign_effect(db, device(), db.effects[0], CLAIM, 8, request))["authorized"]
    before = len(db.calls)
    rechecked = await runtime.authorize_campaign_effect(db, device(), db.effects[0], CLAIM, 8,
        request.model_copy(update={"recheck": True}))
    assert rechecked == first and db.effects[0]["attempts"] == 1
    assert all(sql.startswith("SELECT") for sql, _ in db.calls[before:])
    assert len([sql for sql, _ in db.calls if sql.startswith("UPDATE subscription_device_effects")]) == 1
    assert all(item == (TENANT, {"feature": "campaigns.manage"}) for item in authority)


@pytest.mark.asyncio
@pytest.mark.parametrize("part,changes,code", [
    ("campaign", {"status": "paused"}, "CAMPAIGN_PAUSED"),
    ("campaign", {"status": "cancelled", "archived_at": NOW}, "CAMPAIGN_CANCELLED"),
    ("campaign", {"owner_account_id": uuid4()}, "CAMPAIGN_REVOKED"),
    ("campaign", {"status": "completed"}, "CAMPAIGN_REVOKED"),
    ("campaign", {"run_version": "b"*64}, "CAMPAIGN_CONTEXT_CHANGED"),
    ("effect", {"payload": {"phone": PHONE, "action": {"type": "text", "text": "Alterado"}}}, "CAMPAIGN_CONTEXT_CHANGED"),
    ("effect", {"purpose": "absence"}, "CAMPAIGN_CONTEXT_CHANGED"),
    ("effect", {"settings_revision": 4}, "CAMPAIGN_CONTEXT_CHANGED"),
    ("effect", {"generation": uuid4()}, "CAMPAIGN_CONTEXT_CHANGED"),
    ("effect", {"expires_at": NOW}, "CAMPAIGN_EFFECT_EXPIRED"),
    ("effect", {"previous_effect": uuid4()}, "CAMPAIGN_CONTEXT_CHANGED"),
    ("recipient", {"permission_version": 1}, "CAMPAIGN_OPTED_OUT"),
    ("recipient", {"generation": uuid4()}, "CAMPAIGN_CONTEXT_CHANGED"),
    ("recipient", {"status": "cancelled"}, "CAMPAIGN_REVOKED"),
    ("device", {"removed_at": NOW}, "CAMPAIGN_CONTEXT_CHANGED"),
    ("device", {"status": "disabled"}, "CAMPAIGN_CONTEXT_CHANGED"),
    ("allowed", {"revoked": True}, "CAMPAIGN_OPTED_OUT"),
    ("allowed", {"member": False}, "CAMPAIGN_MEMBER_REMOVED"),
])
async def test_final_authority_rechecks_every_frozen_context_without_writes(authority, part, changes, code):
    db, current = DB(), device()
    target = {"campaign": db.campaign, "effect": db.effects[0], "recipient": db.recipient, "device": current, "allowed": db.allowed}[part]
    target.update(changes)
    reason, result = await runtime.effect_authority(db, current, db.effects[0], NOW)
    assert reason == code and result is None
    assert all(sql.startswith("SELECT") for sql, _ in db.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [{"claimed_owner": uuid4()}, {"claimed_fence": 9}, {"attempts": 2}, {"status": "accepted"}, {"status": "uncertain"}])
async def test_terminal_or_foreign_claim_cannot_obtain_a_fresh_authorization(authority, change):
    item = effect(status="running", attempts=1, claimed_owner=CLAIM, claimed_fence=8, authorized_at=NOW) | change
    db = DB(effects=[item])
    result = await runtime.authorize_campaign_effect(db, device(), item, CLAIM, 8, EffectRequest(operation_id=EFFECT, recheck=True))
    assert not result["authorized"] and db.calls == [("SELECT clock_timestamp()", {})]


@pytest.mark.asyncio
async def test_part_two_requires_exact_accepted_predecessor_and_shared_contact_order(authority, monkeypatch):
    first, second = uuid4(), effect(campaign_part_index=1, previous_effect=uuid4())
    row = recipient(plan=[{"type": "audio"}, second["payload"]["action"]])
    db = DB(recipient_row=row, effects=[second], previous={"id": first, "status": "accepted"})
    assert (await runtime.effect_authority(db, device(), second, NOW))[0] == "CAMPAIGN_PREDECESSOR_FAILED"
    second["previous_effect"] = first
    db.previous["status"] = "uncertain"
    assert (await runtime.effect_authority(db, device(), second, NOW))[0] == "CAMPAIGN_PREDECESSOR_FAILED"
    db.previous["status"] = "accepted"
    assert (await runtime.effect_authority(db, device(), second, NOW))[0] is None
    async def busy(*args): return False
    monkeypatch.setattr(history, "message_authority", busy)
    assert (await runtime.effect_authority(db, device(), second, NOW))[0] == "CAMPAIGN_BUSY"


@pytest.mark.asyncio
async def test_lost_feature_does_not_break_poll_and_authority_is_denied(monkeypatch):
    async def revoked(*args, **kwargs): raise HTTPException(403, "revoked")
    monkeypatch.setattr(store.shared, "workspace", revoked)
    db = DB()
    assert await runtime.admit_due_campaigns(db, device()) == [] and db.calls == []
    assert await runtime.effect_authority(db, device(), db.effects[0], NOW) == ("CAMPAIGN_REVOKED", None)
    assert db.calls == []


@pytest.mark.asyncio
async def test_slow_live_recipient_prevents_any_second_admission_even_after_original_interval(authority, monkeypatch):
    calls = []
    async def nothing(*args): pass
    monkeypatch.setattr(runtime, "reap_campaigns", nothing)
    monkeypatch.setattr(runtime, "invalidate_audiences", nothing)
    class LiveDB:
        async def execute(self, query, params=None):
            sql = str(query)
            calls.append(sql)
            if sql == "SELECT clock_timestamp()": return Rows([NOW])
            assert "admitted_at IS NOT NULL AND status='running'" in sql
            return Rows([RECIPIENT])
    assert await runtime.admit_due_campaigns(LiveDB(), device()) == []
    assert len(calls) == 2  # No count, candidate, event or effect INSERT.


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal,expected", [("not_sent", "failed"), ("uncertain", "uncertain")])
async def test_partial_failure_closes_only_pending_tail_and_keeps_all_transport_evidence(authority, terminal, expected):
    parts = [effect(id=uuid4(), status="accepted", attempts=1, campaign_part_index=0, finished_at=NOW-timedelta(seconds=1)),
        effect(id=uuid4(), status=terminal, attempts=1, campaign_part_index=1, error_code="MESSAGE_CONTENT_INVALID", finished_at=NOW),
        effect(id=uuid4(), status="pending", campaign_part_index=2)]
    protected = deepcopy(parts[:2])
    db = DB(effects=parts, recipient_row=recipient(plan=[{"type": "text"}]*3))
    assert await runtime.after_campaign_effect_finish(db, device(), parts[1], terminal) == []
    assert parts[:2] == protected and parts[2]["status"] == "not_sent" and parts[2]["attempts"] == 0
    assert db.recipient["status"] == expected and db.recipient["finished_at"] == NOW
    assert all("INSERT" not in sql for sql, _ in db.calls)


@pytest.mark.asyncio
async def test_late_accepted_receipt_does_not_reactivate_a_cancelled_tail(authority):
    first = effect(status="accepted", attempts=1, finished_at=NOW)
    tail = effect(id=uuid4(), status="not_sent", campaign_part_index=1, error_code="CAMPAIGN_PREDECESSOR_FAILED")
    db = DB(effects=[first, tail], recipient_row=recipient(plan=[{"type": "text"}]*2, status="uncertain", finished_at=NOW-timedelta(seconds=1)))
    before = deepcopy(tail)
    await runtime.after_campaign_effect_finish(db, device(), first, "accepted")
    assert tail == before and db.recipient["status"] == "failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["CAMPAIGN_MEMBER_REMOVED", "CAMPAIGN_PAUSED"])
async def test_claimed_recipient_remembers_revocation_even_after_context_is_restored(authority, reason):
    item = effect(status="running", attempts=1, claimed_owner=CLAIM, claimed_fence=8, authorized_at=NOW)
    db = DB(effects=[item], recipient_row=recipient(error_code=reason))
    protected = deepcopy(item)
    await runtime.refresh_recipient(db, TENANT, RECIPIENT)
    assert item == protected and db.recipient["status"] == "running" and db.recipient["error_code"] == reason
    # Campaign is running again and current group membership is present, but
    # neither state transition erases the old sequence's revocation proof.
    fresh = await runtime.authorize_campaign_effect(db, device(), item, CLAIM, 8, EffectRequest(operation_id=EFFECT, recheck=True))
    assert not fresh["authorized"] and fresh["error_code"] == reason
    assert item == protected
    item.update(status="accepted", finished_at=NOW)
    await runtime.after_campaign_effect_finish(db, device(), item, "accepted")
    assert db.recipient["status"] == "sent" and db.recipient["error_code"] is None
    assert item["attempts"] == 1 and not any("INSERT" in sql for sql, _ in db.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("body", ["Não quero mensagens de aniversário", "Não quero parar mensagens", "Quero receber promoções", "Olá"])
async def test_only_unambiguous_marketing_or_all_optout_writes_a_revocation(body):
    db = DB()
    assert not await runtime.record_campaign_optout(db, device(), {"kind": "text", "origin": "contact", "phone": PHONE, "text": body}, EVENT)
    assert db.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,accepted,expected", [("text", False, True), ("audio", True, True), ("audio", False, False)])
async def test_audio_optout_is_bound_to_original_accepted_transcription_event(monkeypatch, kind, accepted, expected):
    calls, invalidations = [], []
    body = "Não quero mais mensagens promocionais"
    class EventDB:
        async def execute(self, query, params=None):
            sql = str(query)
            calls.append((sql, params))
            assert params["t"] == TENANT
            if sql.startswith("SELECT e.kind"):
                assert params["id"] == EVENT and params["d"] == DEVICE and params["g"] == GENERATION
                return Rows([{"kind": kind, "payload": {"text": body, "origin": "contact", "phone": PHONE}}])
            if sql.startswith("SELECT id FROM subscription_device_effects"):
                assert "type='transcribe' AND status='accepted'" in sql and params["id"] == EVENT
                return Rows([EFFECT] if accepted else [])
            assert sql.startswith("INSERT INTO subscription_campaign_permissions")
            assert params["event"] == EVENT and params["phone"] == PHONE
            return Rows([PHONE])
    async def invalidate(db, tenant): invalidations.append(tenant)
    monkeypatch.setattr(runtime, "invalidate_audiences", invalidate)
    assert await runtime.record_campaign_optout(EventDB(), device(),
        {"kind": "text", "origin": "contact", "phone": PHONE, "text": body}, EVENT) is expected
    assert invalidations == ([TENANT] if expected else [])
    assert not any("transcribe_audio" in sql or "subscription_device_effects SET" in sql for sql, _ in calls)
