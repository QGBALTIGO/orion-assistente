"""Frozen bulk-row authority and real receipt semantics with synthetic transport."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from uuid import uuid4

from fastapi import HTTPException
import pytest

from app import subscription_bulk_runtime as runtime
from app import subscription_bulk_store as store
from app import subscription_sent_messages as history
from app.subscription_device_schemas import EffectRequest
from test_subscription_campaign_policy import Rows

NOW = datetime(2026, 11, 5, 14, tzinfo=timezone.utc)
TENANT, OWNER, DEVICE, GENERATION, CHANNEL, BATCH, RECIPIENT, ROW, EFFECT, EVENT, CLAIM = (
    uuid4() for _ in range(11))
PHONE = "5567999999901"


def device(**changes):
    return {"tenant_id": TENANT, "id": DEVICE, "owner_account_id": OWNER, "channel_id": CHANNEL,
        "generation": GENERATION, "revision": 3, "removed_at": None, "status": "connected", "runtime": {}} | changes


def batch(**changes):
    return {"id": BATCH, "tenant_id": TENANT, "owner_account_id": OWNER, "version": 2,
        "run_version": "a" * 64, "status": "running", "interval_seconds": 10,
        "confirmed_at": NOW - timedelta(minutes=1), "first_admitted_at": NOW - timedelta(seconds=2),
        "last_admitted_at": NOW - timedelta(seconds=2), "next_admission_at": NOW,
        "finished_at": None, "cancelled_at": None, "error_code": None,
        "snapshot": {"consent": {"confirmed": True, "actor_account_id": str(OWNER)}}} | changes


def recipient(**changes):
    row = {"id": RECIPIENT, "tenant_id": TENANT, "bulk_id": BATCH, "row_key": ROW, "ordinal": 1,
        "run_version": "a" * 64, "device_id": DEVICE, "generation": GENERATION, "settings_revision": 3,
        "phone": PHONE, "name": "Contato sintético", "source": "manual", "origins": [], "manual_consent": True,
        "permission_version": 0, "template_id": None, "template_revision": None,
        "context": {"device": {"id": str(DEVICE), "generation": str(GENERATION), "settings_revision": 3,
            "channel_id": str(CHANNEL), "owner_account_id": str(OWNER)}, "template": None},
        "plan": [{"type": "text", "text": "Literal {name}"}], "status": "running",
        "admitted_at": NOW - timedelta(seconds=2), "finished_at": None, "error_code": None} | changes
    row.setdefault("plan_hash", store.fingerprint(row["plan"]))
    return row


def effect(**changes):
    return {"id": EFFECT, "tenant_id": TENANT, "device_id": DEVICE, "generation": GENERATION,
        "settings_revision": 3, "event_id": EVENT, "type": "bulk_action", "purpose": "bulk_action",
        "bulk_id": BATCH, "bulk_recipient_id": RECIPIENT, "bulk_part_index": 0,
        "payload": {"phone": PHONE, "action": {"type": "text", "text": "Literal {name}"}},
        "previous_effect": None, "status": "pending", "attempts": 0, "claimed_owner": None,
        "claimed_fence": None, "authorized_at": None, "due_at": NOW - timedelta(seconds=2),
        "expires_at": NOW + timedelta(minutes=5), "finished_at": None, "error_code": None} | changes


class DB:
    def __init__(self, *, batch_row=None, recipient_row=None, effects=None):
        self.batch = batch_row or batch()
        self.recipient = recipient_row or recipient()
        self.effects = [effect()] if effects is None else effects
        self.current_device, self.channel_enabled, self.template = device(), True, None
        self.allowed, self.source_valid, self.previous = {"revoked": False, "version": 0}, True, None
        self.calls, self.source_checks, self.events, self.created = [], [], [], []

    async def execute(self, query, params=None):
        sql, p = str(query), params or {}
        self.calls.append((sql, p))
        if sql == "SELECT clock_timestamp()": return Rows([NOW])
        if sql.startswith("SELECT * FROM subscription_bulk_batches"):
            return Rows([self.batch] if p["id"] == self.batch["id"] else [])
        if sql.startswith("SELECT * FROM subscription_bulk_recipients"):
            return Rows([self.recipient] if p["id"] == self.recipient["id"] else [])
        if sql.startswith("SELECT * FROM subscription_device_effects"): return Rows(self.effects)
        if sql.startswith("SELECT COALESCE(p.revoked"): return Rows([self.allowed])
        if sql.startswith("SELECT id,status FROM subscription_device_effects"):
            return Rows([self.previous] if self.previous else [])
        if sql.startswith("SELECT r.id FROM subscription_bulk_recipients"):
            ready = (self.recipient["status"] == "running" and self.recipient["admitted_at"] is not None
                and (p["bulk"] is None or p["bulk"] == self.recipient["bulk_id"])
                and self.effects and not any(e["status"] in {"pending", "running"} for e in self.effects))
            return Rows([self.recipient["id"]] if ready else [])
        if sql.startswith("UPDATE subscription_device_effects SET status='running'"):
            target = next(e for e in self.effects if e["id"] == p["id"])
            target.update(status="running", attempts=1, authorized_at=p["now"],
                claimed_owner=p["owner"], claimed_fence=p["fence"])
            return Rows([])
        if sql.startswith("UPDATE subscription_device_effects SET status='not_sent'"):
            changed = []
            for e in self.effects:
                if e["status"] != "pending": continue
                if "WHERE tenant_id=:t AND id=:id" in sql and e["id"] != p["id"]: continue
                if "bulk_recipient_id=:id" in sql and e["bulk_recipient_id"] != p["id"]: continue
                e.update(status="not_sent", error_code=p.get("code", "BULK_PREDECESSOR_FAILED"), finished_at=p["now"])
                changed.append(e["bulk_recipient_id"])
            return Rows(changed if "RETURNING" in sql else [])
        if sql.startswith("UPDATE subscription_device_effects SET\n"):
            changed = []
            for e in self.effects:
                if (e["status"] == "pending" and e["expires_at"] <= p["now"]
                        or e["status"] == "running" and e["authorized_at"] < p["now"] - timedelta(seconds=180)):
                    status = "uncertain" if e["status"] == "running" else "not_sent"
                    e.update(status=status, error_code="MESSAGE_OUTCOME_UNCERTAIN" if status == "uncertain"
                        else "BULK_EFFECT_EXPIRED", finished_at=p["now"])
                    changed.append(e["bulk_recipient_id"])
            return Rows(changed)
        if sql.startswith("UPDATE subscription_bulk_recipients SET status='cancelled'"):
            if self.recipient["status"] == "pending" and self.recipient["admitted_at"] is None:
                self.recipient.update(status="cancelled", error_code=p["code"], finished_at=p["now"])
            return Rows([])
        if sql.startswith("UPDATE subscription_bulk_recipients SET error_code="):
            if self.recipient["status"] == "running" and self.recipient["admitted_at"] is not None:
                self.recipient["error_code"] = p["code"]
            return Rows([])
        if sql.startswith("UPDATE subscription_bulk_recipients SET status='running'"):
            self.recipient.update(status="running", admitted_at=p["now"])
            return Rows([])
        if sql.startswith("UPDATE subscription_bulk_recipients SET status="):
            self.recipient.update(status=p["status"], error_code=p["error"], finished_at=None
                if p["status"] in {"pending", "running"} else self.recipient["finished_at"] or p["now"])
            return Rows([])
        if sql.startswith("UPDATE subscription_bulk_batches SET error_code="):
            self.batch.update(error_code=p["code"], cancelled_at=p["now"], version=self.batch["version"] + 1)
            return Rows([])
        if sql.startswith("UPDATE subscription_bulk_batches SET status='running'"):
            self.batch.update(status="running", first_admitted_at=self.batch["first_admitted_at"] or p["now"],
                last_admitted_at=p["now"], next_admission_at=p["next"], version=self.batch["version"] + 1)
            return Rows([])
        if sql.startswith("INSERT INTO subscription_device_events"):
            self.events.append(p); return Rows([])
        if sql.startswith("INSERT INTO subscription_device_effects"):
            self.created.append(p); return Rows([])
        if sql.startswith("WITH summaries AS ("):
            return Rows([])  # Real aggregate projection is verified on disposable PostgreSQL.
        raise AssertionError(sql)


@pytest.fixture
def authority(monkeypatch):
    calls = []
    async def workspace(db, tenant, **kwargs): calls.append((tenant, kwargs))
    async def check_device(db, tenant, identity, **kwargs):
        assert tenant == TENANT and kwargs["actor_id"] == OWNER
        return db.current_device, {"enabled": db.channel_enabled}
    async def template_snapshot(db, tenant, identity): return db.template
    async def sources(db, tenant, origins, phone, **kwargs):
        db.source_checks.append((tenant, deepcopy(origins), phone, kwargs)); return db.source_valid
    async def order(*args): return True
    monkeypatch.setattr(store.shared, "workspace", workspace)
    monkeypatch.setattr(store.shared, "check_device", check_device)
    monkeypatch.setattr(store, "template_snapshot", template_snapshot)
    monkeypatch.setattr(store, "sources_valid", sources)
    monkeypatch.setattr(history, "message_authority", order)
    return calls


async def test_claim_once_then_readonly_recheck_preserves_frozen_row_identity(authority):
    db, request = DB(), EffectRequest(operation_id=EFFECT)
    first = await runtime.authorize_bulk_effect(db, device(), db.effects[0], CLAIM, 8, request)
    assert first == {"authorized": True, "operation_id": str(EFFECT), "type": "bulk_action", "purpose": "bulk_action",
        "device_id": str(DEVICE), "generation": str(GENERATION), "settings_revision": 3,
        "batch_id": str(BATCH), "recipient_id": str(RECIPIENT), "run_version": "a" * 64, "part_index": 0,
        "payload": {"phone": PHONE, "action": {"type": "text", "text": "Literal {name}"}}}
    assert not (await runtime.authorize_bulk_effect(db, device(), db.effects[0], CLAIM, 8, request))["authorized"]
    before = len(db.calls)
    assert await runtime.authorize_bulk_effect(db, device(), db.effects[0], CLAIM, 8,
        request.model_copy(update={"recheck": True})) == first
    assert db.effects[0]["attempts"] == 1
    assert all(sql.lstrip().startswith("SELECT") for sql, _ in db.calls[before:])
    assert db.source_checks[-1][-1] == {"source": "manual", "manual_consent": True}
    assert authority[-1][1] == {"feature": "bulk_messages.manage"}


@pytest.mark.parametrize("field,value,code", [
    ("tenant_id", uuid4(), "BULK_CONTEXT_CHANGED"), ("device_id", uuid4(), "BULK_CONTEXT_CHANGED"),
    ("generation", uuid4(), "BULK_CONTEXT_CHANGED"), ("settings_revision", 2, "BULK_CONTEXT_CHANGED"),
    ("type", "campaign_action", "BULK_CONTEXT_CHANGED"), ("purpose", "campaign_action", "BULK_CONTEXT_CHANGED"),
    ("expires_at", NOW, "BULK_EFFECT_EXPIRED"), ("bulk_id", uuid4(), "BULK_CONTEXT_CHANGED"),
    ("bulk_recipient_id", uuid4(), "BULK_REVOKED"), ("bulk_part_index", True, "BULK_CONTEXT_CHANGED"),
    ("bulk_part_index", -1, "BULK_CONTEXT_CHANGED"), ("bulk_part_index", 1, "BULK_CONTEXT_CHANGED"),
    ("previous_effect", uuid4(), "BULK_CONTEXT_CHANGED"),
    ("payload", {"phone": "5567999999902", "action": {"type": "text", "text": "Literal {name}"}}, "BULK_CONTEXT_CHANGED"),
])
async def test_foreign_stale_or_substituted_effect_has_no_authority(authority, field, value, code):
    db = DB()
    row = {**db.effects[0], field: value}
    assert await runtime.bulk_effect_authority(db, device(), row, NOW) == (code, None)
    assert all(sql.lstrip().startswith("SELECT") for sql, _ in db.calls)


@pytest.mark.parametrize("field,value,code", [
    ("owner_account_id", uuid4(), "BULK_REVOKED"), ("status", "pending", "BULK_REVOKED"),
    ("status", "completed", "BULK_REVOKED"), ("status", "cancelled", "BULK_CANCELLED"),
    ("cancelled_at", NOW, "BULK_CANCELLED"), ("run_version", "b" * 64, "BULK_CONTEXT_CHANGED"),
    ("first_admitted_at", None, "BULK_CONTEXT_CHANGED"), ("error_code", "BULK_REVOKED", "BULK_REVOKED"),
    ("snapshot", {"consent": {"confirmed": False, "actor_account_id": str(OWNER)}}, "BULK_REVOKED"),
])
async def test_batch_scope_consent_and_terminal_state_are_rechecked(authority, field, value, code):
    db = DB(batch_row=batch(**{field: value}))
    assert await runtime.bulk_effect_authority(db, device(), db.effects[0], NOW) == (code, None)


@pytest.mark.parametrize("field,value,code", [
    ("status", "sent", "BULK_REVOKED"), ("run_version", "b" * 64, "BULK_CONTEXT_CHANGED"),
    ("admitted_at", None, "BULK_CONTEXT_CHANGED"), ("device_id", uuid4(), "BULK_CONTEXT_CHANGED"),
    ("generation", uuid4(), "BULK_CONTEXT_CHANGED"), ("settings_revision", 2, "BULK_CONTEXT_CHANGED"),
    ("manual_consent", False, "BULK_CONTEXT_CHANGED"), ("plan_hash", "b" * 64, "BULK_CONTEXT_CHANGED"),
    ("error_code", "BULK_OPTED_OUT", "BULK_OPTED_OUT"),
    ("context", {"device": {}, "template": None}, "BULK_CONTEXT_CHANGED"),
])
async def test_row_context_is_immutable_even_for_intentional_duplicate_phone(authority, field, value, code):
    db = DB(recipient_row=recipient(**{field: value}))
    assert await runtime.bulk_effect_authority(db, device(), db.effects[0], NOW) == (code, None)


@pytest.mark.parametrize("revoked,version", [(True, 0), (False, 1)])
async def test_manual_consent_never_overrides_shared_optout(authority, revoked, version):
    db = DB(); db.allowed = {"revoked": revoked, "version": version}
    assert await runtime.bulk_effect_authority(db, device(), db.effects[0], NOW) == ("BULK_OPTED_OUT", None)
    assert db.source_checks == []


async def test_removed_frozen_catalog_source_is_rejected_without_live_expansion(authority):
    db = DB(recipient_row=recipient(source="catalog", manual_consent=False,
        origins=[{"kind": "customer", "customer_id": str(uuid4()), "phone": PHONE, "version": 1}]))
    db.source_valid = False
    assert await runtime.bulk_effect_authority(db, device(), db.effects[0], NOW) == ("BULK_SOURCE_REMOVED", None)
    assert db.source_checks[-1][1] == db.recipient["origins"]


async def test_current_self_phone_is_blocked_only_when_verified(authority):
    db = DB()
    assert (await runtime.bulk_effect_authority(db, device(runtime={"phone": PHONE}), db.effects[0], NOW))[0] is None
    assert (await runtime.bulk_effect_authority(db, device(runtime={"phone": PHONE, "phone_verified": True}),
        db.effects[0], NOW))[0] == "BULK_SELF_CONTACT"


async def test_template_revision_and_assets_are_revalidated(authority):
    identity = uuid4(); template = {"id": str(identity), "revision": 2, "content": {"type": "text"}}
    row = recipient(template_id=identity, template_revision=2)
    row["context"]["template"] = deepcopy(template)
    db = DB(recipient_row=row); db.template = deepcopy(template)
    assert (await runtime.bulk_effect_authority(db, device(), db.effects[0], NOW))[0] is None
    db.template["revision"] = 3
    assert (await runtime.bulk_effect_authority(db, device(), db.effects[0], NOW))[0] == "BULK_TEMPLATE_CHANGED"


@pytest.mark.parametrize("state,matching,expected", [("accepted", True, None), ("pending", True, "BULK_PREDECESSOR_FAILED"),
    ("uncertain", True, "BULK_PREDECESSOR_FAILED"), ("accepted", False, "BULK_PREDECESSOR_FAILED")])
async def test_second_part_requires_the_exact_accepted_predecessor(authority, state, matching, expected):
    prior = uuid4(); plan = [{"type": "text", "text": "A"}, {"type": "text", "text": "B"}]
    db = DB(recipient_row=recipient(plan=plan))
    db.previous = {"id": prior if matching else uuid4(), "status": state}
    item = effect(bulk_part_index=1, previous_effect=prior, payload={"phone": PHONE, "action": plan[1]})
    assert (await runtime.bulk_effect_authority(db, device(), item, NOW))[0] == expected


@pytest.mark.parametrize("owner,fence", [(uuid4(), 8), (CLAIM, 9)])
async def test_wrong_transport_claim_cannot_recheck(authority, owner, fence):
    row = effect(status="running", attempts=1, claimed_owner=CLAIM, claimed_fence=8, authorized_at=NOW)
    db = DB(effects=[row])
    result = await runtime.authorize_bulk_effect(db, device(), row, owner, fence,
        EffectRequest(operation_id=EFFECT, recheck=True))
    assert not result["authorized"] and row["attempts"] == 1


@pytest.mark.parametrize("state,error,expected", [("accepted", None, "sent"),
    ("uncertain", "MESSAGE_OUTCOME_UNCERTAIN", "uncertain"), ("not_sent", "settings_changed", "failed"),
    ("not_sent", "BULK_SOURCE_REMOVED", "cancelled")])
async def test_receipt_projection_preserves_attempts_and_never_creates_replay(authority, state, error, expected):
    row = effect(status=state, error_code=error, attempts=1 if state in {"accepted", "uncertain"} else 0, finished_at=NOW)
    db = DB(effects=[row]); before = deepcopy(row)
    assert await runtime.after_bulk_effect_finish(db, device(), row, state) == []
    assert db.recipient["status"] == expected and row == before and db.created == []


async def test_partial_acceptance_then_failure_closes_only_pending_tail(authority):
    plan = [{"type": "text", "text": str(i)} for i in range(3)]
    parts = [effect(id=uuid4(), bulk_part_index=i, status=s, attempts=1 if s != "pending" else 0,
        finished_at=NOW if s != "pending" else None, error_code="transport_failed" if s == "failed" else None)
        for i, s in enumerate(["accepted", "failed", "pending"])]
    db = DB(recipient_row=recipient(plan=plan), effects=parts); before = deepcopy(parts[:2])
    await runtime.refresh_recipient(db, TENANT, RECIPIENT)
    assert parts[:2] == before and parts[2]["status"] == "not_sent"
    assert parts[2]["error_code"] == "BULK_PREDECESSOR_FAILED" and parts[2]["attempts"] == 0
    assert db.recipient["status"] == "failed" and db.created == []


async def test_cancel_after_external_settings_closure_reconciles_without_waiting_for_old_device(authority):
    row = effect(status="not_sent", error_code="settings_changed", finished_at=NOW)
    db = DB(effects=[row]); before = deepcopy(row)
    await runtime.cancel_bulk_pending(db, db.batch, "BULK_CANCELLED")
    assert db.recipient["status"] == "failed" and db.recipient["error_code"] == "settings_changed"
    assert row == before and db.batch["cancelled_at"] == NOW and db.created == []


async def test_cancel_preserves_claimed_part_and_revokes_future_transport_recheck(authority):
    row = effect(status="running", attempts=1, claimed_owner=CLAIM, claimed_fence=8, authorized_at=NOW)
    db = DB(effects=[row]); before = deepcopy(row)
    await runtime.cancel_bulk_pending(db, db.batch, "BULK_CANCELLED")
    assert row == before and db.recipient["status"] == "running"
    assert db.recipient["error_code"] == "BULK_CANCELLED"
    result = await runtime.authorize_bulk_effect(db, device(), row, CLAIM, 8, EffectRequest(operation_id=EFFECT, recheck=True))
    assert not result["authorized"] and result["error_code"] == "BULK_CANCELLED" and row == before


@pytest.mark.parametrize("claimed,expected", [(False, "failed"), (True, "uncertain")])
async def test_expired_or_interrupted_sequence_is_terminal_without_retry(authority, claimed, expected):
    row = effect(status="running" if claimed else "pending", attempts=int(claimed),
        authorized_at=NOW-timedelta(seconds=181) if claimed else None, expires_at=NOW-timedelta(seconds=1))
    db = DB(effects=[row])
    await runtime.reap_bulk(db, TENANT, NOW)
    assert db.recipient["status"] == expected and row["attempts"] == int(claimed) and db.created == []


async def test_global_reaping_visits_all_modules_even_without_feature_permissions(monkeypatch):
    from app import subscription_campaign_runtime as campaigns, subscription_schedule_runtime as schedules
    seen = []
    async def campaign(db, tenant, now): seen.append(("campaign", tenant, now))
    async def scheduled(db, tenant, now): seen.append(("scheduled", tenant, now))
    async def bulk(db, tenant, now): seen.append(("bulk", tenant, now))
    monkeypatch.setattr(campaigns, "reap_campaigns", campaign)
    monkeypatch.setattr(schedules, "reap_schedules", scheduled)
    monkeypatch.setattr(runtime, "reap_bulk", bulk)
    await runtime.reap_outbound_work(object(), TENANT, NOW)
    assert seen == [(name, TENANT, NOW) for name in ("campaign", "scheduled", "bulk")]


@pytest.mark.parametrize("interval,tenant_age,batch_age,recent,expected", [
    (10, 6, 10, 9, True), (60, 6, 60, 9, True), (11, 6, 11, 9, True),
    (10, 5, 10, 9, False), (10, 6, 9, 9, False), (10, 6, 10, 10, False),
    (9, 60, 60, 0, False), (61, 60, 61, 0, False), (True, 60, 60, 0, False),
])
def test_shared_rate_and_spacing_start_after_the_completed_sequence(interval, tenant_age, batch_age, recent, expected):
    assert runtime.admission_ready(NOW, NOW-timedelta(seconds=tenant_age),
        NOW-timedelta(seconds=batch_age), interval, recent) is expected


async def test_frozen_parts_are_created_once_with_linked_rows_and_literal_text(authority):
    plan = [{"type": "text", "text": "Literal {name}"}, {"type": "text", "text": "Segundo conteúdo"}]
    db = DB(batch_row=batch(status="pending", first_admitted_at=None),
        recipient_row=recipient(status="pending", admitted_at=None, plan=plan), effects=[])
    ids = await runtime.create_effects(db, device(), db.batch, db.recipient, NOW)
    assert len(ids) == 2 and len(set(ids)) == 2 and len(db.events) == 1
    assert db.created[0]["previous"] is None and db.created[1]["previous"] == ids[0]
    assert [json.loads(row["payload"])["action"] for row in db.created] == plan
    assert all(row["bulk"] == BATCH and row["recipient"] == RECIPIENT for row in db.created)
    assert [row["part"] for row in db.created] == [0, 1]
    assert all(row["expiry"] == NOW+timedelta(minutes=10) for row in db.created)
    assert db.recipient["status"] == "running" and db.recipient["admitted_at"] == NOW
