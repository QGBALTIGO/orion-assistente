"""Frozen occurrence authority, transport evidence and shared admission limits.

All transports and catalog reads are synthetic; no provider or recipient is
contacted. PostgreSQL integration tests separately exercise the real SQL.
"""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import json
from uuid import uuid4

from fastapi import HTTPException
import pytest

from app import subscription_schedule_policy as policy
from app import subscription_schedule_runtime as runtime
from app import subscription_schedule_store as store
from app import subscription_sent_messages as history
from app.subscription_device_schemas import EffectRequest
from test_subscription_campaign_policy import Rows

NOW = datetime(2026, 11, 5, 12, tzinfo=timezone.utc)
TENANT, OWNER, DEVICE, GENERATION, CHANNEL, GROUP, SCHEDULE, RUN, RECIPIENT, EFFECT, CLAIM, EVENT = (
    uuid4() for _ in range(12))
PHONE = "5567999999901"


def device(**changes):
    return {"tenant_id": TENANT, "id": DEVICE, "owner_account_id": OWNER, "channel_id": CHANNEL,
        "generation": GENERATION, "revision": 3, "removed_at": None, "status": "connected", "runtime": {}} | changes


def schedule(**changes):
    return {"tenant_id": TENANT, "id": SCHEDULE, "owner_account_id": OWNER, "status": "active",
        "version": 1, "device_id": DEVICE, "run_version": "a" * 64, "template_id": None,
        "template_revision": None, "anchor_date": date(2026, 11, 5), "anchor_time": "12:00",
        "timezone": "UTC", "interval_seconds": 60, "repeat_every_30_days": False} | changes


def run(**changes):
    return {"tenant_id": TENANT, "id": RUN, "schedule_id": SCHEDULE, "occurrence_index": 0,
        "version": 2, "schedule_version": 1, "run_version": "a" * 64, "status": "running",
        "due_at": NOW - timedelta(minutes=1), "local_date": NOW.date(), "timezone": "UTC",
        "first_admitted_at": NOW - timedelta(seconds=2), "manual_released_at": None,
        "finished_at": None, "error_code": None,
        "snapshot": {"definition": {"start_date": "2026-11-05", "start_time": "11:59", "timezone": "UTC"},
            "consent": {"confirmed": True, "actor_account_id": str(OWNER)},
            "device": {"id": str(DEVICE), "channel_id": str(CHANNEL), "generation": str(GENERATION),
                "settings_revision": 3, "owner_account_id": str(OWNER)}, "template": None}} | changes


def recipient(**changes):
    row = {"tenant_id": TENANT, "id": RECIPIENT, "schedule_id": SCHEDULE, "run_id": RUN,
        "run_version": "a" * 64, "device_id": DEVICE, "generation": GENERATION, "settings_revision": 3,
        "status": "running", "plan": [{"type": "text", "text": "Conteúdo confirmado"}],
        "phone": PHONE, "name": "Pessoa sintética", "permission_version": 0,
        "origins": [{"kind": "group_member", "group_id": str(GROUP), "phone": PHONE,
            "created_at": "2026-11-01T12:00:00+00:00", "updated_at": "2026-11-01T12:00:00+00:00"}],
        "admitted_at": NOW - timedelta(seconds=2), "finished_at": None, "error_code": None} | changes
    row.setdefault("plan_hash", store.fingerprint(row["plan"]))
    return row


def effect(**changes):
    return {"tenant_id": TENANT, "id": EFFECT, "device_id": DEVICE, "generation": GENERATION,
        "settings_revision": 3, "event_id": EVENT, "type": "scheduled_action", "purpose": "scheduled_action",
        "schedule_id": SCHEDULE, "schedule_run_id": RUN, "schedule_recipient_id": RECIPIENT,
        "schedule_part_index": 0, "payload": {"phone": PHONE, "action": {"type": "text", "text": "Conteúdo confirmado"}},
        "status": "pending", "attempts": 0, "claimed_owner": None, "claimed_fence": None,
        "due_at": NOW - timedelta(seconds=2), "created_at": NOW - timedelta(seconds=2),
        "expires_at": NOW + timedelta(minutes=5), "previous_effect": None, "authorized_at": None,
        "finished_at": None, "error_code": None} | changes


class DB:
    def __init__(self, *, schedule_row=None, run_row=None, recipient_row=None, effects=None, allowed=None, previous=None):
        self.schedule = schedule_row or schedule()
        self.run = run_row or run()
        self.recipient = recipient_row or recipient()
        self.effects = [effect()] if effects is None else effects
        self.allowed = {"revoked": False, "version": 0} if allowed is None else allowed
        self.source_valid = True
        self.current_device, self.channel_enabled, self.template = device(), True, None
        self.previous, self.calls, self.source_checks = previous, [], []

    async def execute(self, query, params=None):
        sql, params = str(query), params or {}
        self.calls.append((sql, params))
        if sql == "SELECT clock_timestamp()": return Rows([NOW])
        if sql.startswith("SELECT * FROM subscription_schedules"):
            return Rows([self.schedule] if params["id"] == self.schedule["id"] else [])
        if sql.startswith("SELECT * FROM subscription_schedule_runs"):
            return Rows([self.run] if "id" not in params or params["id"] == self.run["id"] else [])
        if sql.startswith("SELECT * FROM subscription_schedule_recipients"):
            return Rows([self.recipient] if params["id"] == self.recipient["id"] else [])
        if sql.startswith("SELECT r.id FROM subscription_schedule_recipients r"):
            eligible = (self.recipient["status"] == "running" and self.recipient["admitted_at"] is not None
                and (params["run"] is None or self.recipient["run_id"] == params["run"])
                and self.effects and not any(item["status"] in {"pending", "running"} for item in self.effects))
            return Rows([self.recipient["id"]] if eligible else [])
        if sql.startswith("SELECT COALESCE(p.revoked"): return Rows([self.allowed])
        if sql.startswith("SELECT id,status FROM subscription_device_effects"):
            return Rows([self.previous] if self.previous else [])
        if sql.startswith("SELECT * FROM subscription_device_effects"): return Rows(self.effects)
        if sql.startswith("UPDATE subscription_device_effects SET status='running'"):
            item = next(row for row in self.effects if row["id"] == params["id"])
            item.update(status="running", attempts=1, authorized_at=params["now"],
                claimed_owner=params["owner"], claimed_fence=params["fence"])
            return Rows([])
        if sql.startswith("UPDATE subscription_device_effects SET status='not_sent'"):
            affected = []
            for item in self.effects:
                if item["status"] == "pending" and ("id" not in params or "code" not in params or item["id"] == params["id"]):
                    item.update(status="not_sent", error_code=params.get("code", "SCHEDULE_PREDECESSOR_FAILED"),
                        finished_at=params["now"])
                    affected.append(item["schedule_recipient_id"])
            return Rows(affected if "RETURNING" in sql else [])
        if sql.startswith("UPDATE subscription_schedule_recipients SET status='cancelled'"):
            if self.recipient["status"] == "pending" and self.recipient["admitted_at"] is None:
                self.recipient.update(status="cancelled", error_code=params["code"], finished_at=params["now"])
            return Rows([])
        if sql.startswith("UPDATE subscription_schedule_recipients SET error_code="):
            if self.recipient["status"] == "running" and self.recipient["admitted_at"] is not None:
                self.recipient["error_code"] = params["code"]
            return Rows([])
        if sql.startswith("UPDATE subscription_schedule_recipients SET status="):
            self.recipient.update(status=params["status"], error_code=params["error"], finished_at=None
                if params["status"] in {"pending", "running"} else self.recipient["finished_at"] or params["now"])
            return Rows([])
        if sql.startswith("UPDATE subscription_schedule_runs SET"):
            self.run["error_code"] = "SCHEDULE_RECURRENCE_INVALID" if self.run["due_at"] is None else params["code"]
            return Rows([])
        if sql.startswith("WITH summaries AS (") or sql.startswith("UPDATE subscription_schedules s SET"):
            return Rows([])  # Actual projection SQL is covered by PostgreSQL integration.
        raise AssertionError(sql)


@pytest.fixture
def authority(monkeypatch):
    calls = []
    async def workspace(db, tenant, **kwargs): calls.append((tenant, kwargs))
    async def check_device(db, tenant, identity, **kwargs):
        assert tenant == TENANT and identity == DEVICE and kwargs["actor_id"] == OWNER
        return db.current_device, {"enabled": db.channel_enabled}
    async def template_snapshot(db, tenant, identity): return db.template
    async def sources(db, tenant, origins, phone):
        db.source_checks.append((tenant, deepcopy(origins), phone))
        return db.source_valid
    async def order(*args): return True
    monkeypatch.setattr(store.shared, "workspace", workspace)
    monkeypatch.setattr(store.shared, "check_device", check_device)
    monkeypatch.setattr(store, "template_snapshot", template_snapshot)
    monkeypatch.setattr(store, "sources_valid", sources)
    monkeypatch.setattr(history, "message_authority", order)
    return calls


@pytest.mark.asyncio
async def test_claim_once_then_readonly_final_recheck_preserves_exact_occurrence_permit(authority):
    db, request = DB(), EffectRequest(operation_id=EFFECT)
    first = await runtime.authorize_schedule_effect(db, device(), db.effects[0], CLAIM, 8, request)
    assert first == {"authorized": True, "operation_id": str(EFFECT), "type": "scheduled_action", "purpose": "scheduled_action",
        "device_id": str(DEVICE), "generation": str(GENERATION), "settings_revision": 3,
        "schedule_id": str(SCHEDULE), "run_id": str(RUN), "recipient_id": str(RECIPIENT),
        "run_version": "a" * 64, "part_index": 0,
        "payload": {"phone": PHONE, "action": {"type": "text", "text": "Conteúdo confirmado"}}}
    assert not (await runtime.authorize_schedule_effect(db, device(), db.effects[0], CLAIM, 8, request))["authorized"]
    before = len(db.calls)
    rechecked = await runtime.authorize_schedule_effect(db, device(), db.effects[0], CLAIM, 8,
        request.model_copy(update={"recheck": True}))
    assert rechecked == first and db.effects[0]["attempts"] == 1
    assert all(sql.startswith("SELECT") for sql, _ in db.calls[before:])
    assert len([sql for sql, _ in db.calls if sql.startswith("UPDATE subscription_device_effects")]) == 1
    assert all(item == (TENANT, {"feature": "scheduled_messages.manage"}) for item in authority)
    assert db.source_checks == [(TENANT, db.recipient["origins"], PHONE)] * 2


@pytest.mark.asyncio
@pytest.mark.parametrize("target,changes,code", [
    ("schedule", {"status": "cancelled"}, "SCHEDULE_CANCELLED"),
    ("schedule", {"status": "completed"}, "SCHEDULE_REVOKED"),
    ("schedule", {"owner_account_id": uuid4()}, "SCHEDULE_REVOKED"),
    ("schedule", {"version": 2}, "SCHEDULE_CONTEXT_CHANGED"),
    ("schedule", {"run_version": "b" * 64}, "SCHEDULE_CONTEXT_CHANGED"),
    ("schedule", {"device_id": uuid4()}, "SCHEDULE_CONTEXT_CHANGED"),
    ("run", {"status": "pending"}, "SCHEDULE_REVOKED"),
    ("run", {"status": "cancelled"}, "SCHEDULE_CANCELLED"),
    ("run", {"schedule_id": uuid4()}, "SCHEDULE_CONTEXT_CHANGED"),
    ("run", {"run_version": "b" * 64}, "SCHEDULE_CONTEXT_CHANGED"),
    ("run", {"due_at": None}, "SCHEDULE_REVOKED"),
    ("run", {"due_at": NOW + timedelta(seconds=1)}, "SCHEDULE_REVOKED"),
    ("run", {"first_admitted_at": None}, "SCHEDULE_CONTEXT_CHANGED"),
    ("run", {"error_code": "SCHEDULE_CANCELLED"}, "SCHEDULE_CANCELLED"),
    ("effect", {"payload": {"phone": PHONE, "action": {"type": "text", "text": "Alterado"}}}, "SCHEDULE_CONTEXT_CHANGED"),
    ("effect", {"purpose": "campaign_action"}, "SCHEDULE_CONTEXT_CHANGED"),
    ("effect", {"type": "send_text"}, "SCHEDULE_CONTEXT_CHANGED"),
    ("effect", {"tenant_id": uuid4()}, "SCHEDULE_CONTEXT_CHANGED"),
    ("effect", {"device_id": uuid4()}, "SCHEDULE_CONTEXT_CHANGED"),
    ("effect", {"settings_revision": 4}, "SCHEDULE_CONTEXT_CHANGED"),
    ("effect", {"generation": uuid4()}, "SCHEDULE_CONTEXT_CHANGED"),
    ("effect", {"expires_at": NOW}, "SCHEDULE_EFFECT_EXPIRED"),
    ("effect", {"previous_effect": uuid4()}, "SCHEDULE_CONTEXT_CHANGED"),
    ("effect", {"schedule_part_index": True}, "SCHEDULE_CONTEXT_CHANGED"),
    ("effect", {"schedule_part_index": -1}, "SCHEDULE_CONTEXT_CHANGED"),
    ("effect", {"schedule_part_index": 1}, "SCHEDULE_CONTEXT_CHANGED"),
    ("effect", {"schedule_run_id": uuid4()}, "SCHEDULE_CONTEXT_CHANGED"),
    ("effect", {"schedule_recipient_id": uuid4()}, "SCHEDULE_REVOKED"),
    ("recipient", {"permission_version": 1}, "SCHEDULE_OPTED_OUT"),
    ("recipient", {"generation": uuid4()}, "SCHEDULE_CONTEXT_CHANGED"),
    ("recipient", {"device_id": uuid4()}, "SCHEDULE_CONTEXT_CHANGED"),
    ("recipient", {"run_version": "b" * 64}, "SCHEDULE_CONTEXT_CHANGED"),
    ("recipient", {"plan_hash": "b" * 64}, "SCHEDULE_CONTEXT_CHANGED"),
    ("recipient", {"status": "cancelled"}, "SCHEDULE_REVOKED"),
    ("recipient", {"admitted_at": None}, "SCHEDULE_CONTEXT_CHANGED"),
    ("device", {"removed_at": NOW}, "SCHEDULE_CONTEXT_CHANGED"),
    ("device", {"status": "disabled"}, "SCHEDULE_CONTEXT_CHANGED"),
    ("allowed", {"revoked": True}, "SCHEDULE_OPTED_OUT"),
    ("current_device", {"revision": 4}, "SCHEDULE_CONTEXT_CHANGED"),
    ("current_device", {"generation": uuid4()}, "SCHEDULE_CONTEXT_CHANGED"),
    ("current_device", {"channel_id": uuid4()}, "SCHEDULE_CONTEXT_CHANGED"),
    ("current_device", {"owner_account_id": uuid4()}, "SCHEDULE_CONTEXT_CHANGED"),
])
async def test_final_authority_rechecks_all_frozen_bindings_without_writing(authority, target, changes, code):
    db, current = DB(), device()
    row = {"schedule": db.schedule, "run": db.run, "effect": db.effects[0], "recipient": db.recipient,
        "device": current, "allowed": db.allowed, "current_device": db.current_device}[target]
    row.update(changes)
    assert await runtime.schedule_effect_authority(db, current, db.effects[0], NOW) == (code, None)
    assert all(sql.startswith("SELECT") for sql, _ in db.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("consent", [{"confirmed": False}, {"confirmed": True, "actor_account_id": str(uuid4())}, {}])
async def test_exact_owner_confirmation_remains_required_at_transport_boundary(authority, consent):
    db = DB()
    db.run["snapshot"]["consent"] = consent
    assert await runtime.schedule_effect_authority(db, device(), db.effects[0], NOW) == ("SCHEDULE_REVOKED", None)


@pytest.mark.asyncio
async def test_template_content_revision_and_frozen_source_are_revalidated(authority):
    db = DB()
    template = {"id": str(uuid4()), "revision": 2, "body": "Confirmado"}
    db.schedule.update(template_id=template["id"], template_revision=2)
    db.run["snapshot"]["template"] = deepcopy(template)
    db.template = deepcopy(template)
    assert (await runtime.schedule_effect_authority(db, device(), db.effects[0], NOW))[0] is None
    db.template["body"] = "Diferente"
    assert (await runtime.schedule_effect_authority(db, device(), db.effects[0], NOW))[0] == "SCHEDULE_TEMPLATE_CHANGED"
    db.template = deepcopy(template)
    db.source_valid = False
    assert (await runtime.schedule_effect_authority(db, device(), db.effects[0], NOW))[0] == "SCHEDULE_SOURCE_REMOVED"


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [{"claimed_owner": uuid4()}, {"claimed_fence": 9}, {"attempts": 2},
    {"status": "accepted"}, {"status": "uncertain"}, {"status": "not_sent"}])
async def test_finished_foreign_or_retried_claim_cannot_get_a_new_permit(authority, change):
    item = effect(status="running", attempts=1, claimed_owner=CLAIM, claimed_fence=8, authorized_at=NOW) | change
    db = DB(effects=[item])
    result = await runtime.authorize_schedule_effect(db, device(), item, CLAIM, 8,
        EffectRequest(operation_id=EFFECT, recheck=True))
    assert not result["authorized"] and db.calls == [("SELECT clock_timestamp()", {})]


@pytest.mark.asyncio
async def test_only_the_exact_accepted_previous_part_and_shared_order_allow_next_part(authority, monkeypatch):
    first, second = uuid4(), effect(schedule_part_index=1, previous_effect=uuid4())
    db = DB(recipient_row=recipient(plan=[{"type": "audio"}, second["payload"]["action"]]),
        effects=[second], previous={"id": first, "status": "accepted"})
    assert (await runtime.schedule_effect_authority(db, device(), second, NOW))[0] == "SCHEDULE_PREDECESSOR_FAILED"
    second["previous_effect"] = first
    db.previous["status"] = "uncertain"
    assert (await runtime.schedule_effect_authority(db, device(), second, NOW))[0] == "SCHEDULE_PREDECESSOR_FAILED"
    db.previous["status"] = "accepted"
    assert (await runtime.schedule_effect_authority(db, device(), second, NOW))[0] is None
    async def busy(*args): return False
    monkeypatch.setattr(history, "message_authority", busy)
    assert (await runtime.schedule_effect_authority(db, device(), second, NOW))[0] == "SCHEDULE_BUSY"


@pytest.mark.parametrize("value,expected", [
    ({"phone": PHONE, "phone_verified": True}, PHONE),
    ({"phone": PHONE, "phone_verified": False}, None),
    ({"phone": PHONE, "phone_verified": "true"}, None),
    ({"phone": "+" + PHONE, "phone_verified": True}, None),
    ({"phone": PHONE + "@s.whatsapp.net", "phone_verified": True}, None),
    ({"phone": "1" * 16, "phone_verified": True}, None),
    ({"phone": 5567999999901, "phone_verified": True}, None),
    ({}, None), ([], None), (None, None),
])
def test_only_canonical_explicitly_verified_runtime_phone_is_self(value, expected):
    assert runtime.verified_self_phone(device(runtime=value)) == expected


@pytest.mark.asyncio
async def test_changed_verified_own_phone_revokes_an_already_claimed_part(authority):
    db, current = DB(), device(runtime={"phone": PHONE, "phone_verified": False})
    request = EffectRequest(operation_id=EFFECT)
    assert (await runtime.authorize_schedule_effect(db, current, db.effects[0], CLAIM, 8, request))["authorized"]
    before = deepcopy(db.effects[0])
    current["runtime"]["phone_verified"] = True
    result = await runtime.authorize_schedule_effect(db, current, db.effects[0], CLAIM, 8,
        request.model_copy(update={"recheck": True}))
    assert not result["authorized"] and result["error_code"] == "SCHEDULE_SELF_CONTACT"
    assert db.effects[0] == before


@pytest.mark.asyncio
async def test_lost_feature_stops_admission_and_denies_assets_without_querying_content(monkeypatch):
    async def revoked(*args, **kwargs): raise HTTPException(403, "revoked")
    monkeypatch.setattr(store.shared, "workspace", revoked)
    db = DB()
    assert await runtime.admit_due_schedules(db, device()) == []
    assert await runtime.schedule_effect_authority(db, device(), db.effects[0], NOW) == ("SCHEDULE_REVOKED", None)
    assert db.calls == []


@pytest.mark.asyncio
async def test_live_campaign_or_scheduled_sequence_prevents_second_admission(authority, monkeypatch):
    calls = []
    async def nothing(*args): pass
    for name in ("reap_schedules", "invalidate_schedule_contacts", "materialize_next_occurrences"):
        monkeypatch.setattr(runtime, name, nothing)
    class LiveDB:
        async def execute(self, query, params=None):
            sql = str(query)
            calls.append(sql)
            if sql == "SELECT clock_timestamp()": return Rows([NOW])
            assert "subscription_campaign_recipients" in sql and "subscription_schedule_recipients" in sql
            assert "admitted_at IS NOT NULL AND status='running'" in sql
            return Rows([RECIPIENT])
    assert await runtime.admit_due_schedules(LiveDB(), device()) == []
    assert len(calls) == 2  # No capacity, candidate, event or effect creation.


@pytest.mark.asyncio
@pytest.mark.parametrize("other_device", [False, True])
async def test_poll_repairs_externally_revoked_recipient_before_consulting_shared_gate(authority, monkeypatch, other_device):
    # A settings change/removal finished an unclaimed effect without invoking
    # this runtime. Its recipient still says running when the next poll starts.
    item = effect(status="not_sent", error_code="settings_changed", finished_at=NOW - timedelta(seconds=20))
    before = deepcopy(item)
    current = device(id=uuid4()) if other_device else device(revision=4)
    gates, candidates = [], []
    class PollDB(DB):
        async def execute(self, query, params=None):
            sql = str(query)
            if sql == "SELECT clock_timestamp()":
                self.calls.append((sql, params or {}))
                return Rows([self.now])
            if sql.startswith("UPDATE subscription_device_effects SET\n        status=CASE"):
                self.calls.append((sql, params))
                return Rows([])  # Already terminal; expiration updates find nothing.
            if sql.startswith("SELECT id FROM ("):
                self.calls.append((sql, params))
                gates.append(self.recipient["status"])
                return Rows([RECIPIENT] if self.recipient["status"] == "running" else [])
            if sql.startswith("SELECT max(finished_at) AS finished"):
                self.calls.append((sql, params))
                return Rows([{"finished": self.recipient["finished_at"], "recent": 1}])
            if sql.startswith("SELECT u.* FROM subscription_schedule_runs u"):
                self.calls.append((sql, params))
                candidates.append(params["d"])
                return Rows([])
            return await super().execute(query, params)
    db = PollDB(effects=[item])
    db.now = NOW
    async def nothing(*args): pass
    monkeypatch.setattr(runtime, "invalidate_schedule_contacts", nothing)
    monkeypatch.setattr(runtime, "materialize_next_occurrences", nothing)
    assert await runtime.admit_due_schedules(db, current) == []
    assert gates == ["failed"] and db.recipient["error_code"] == "settings_changed"
    assert db.recipient["finished_at"] == NOW and item == before
    assert not candidates  # A freshly closed sequence still observes the six-second tenant gap.
    db.now += timedelta(seconds=6)
    assert await runtime.admit_due_schedules(db, current) == []
    assert gates == ["failed", "failed"] and candidates == [current["id"]]
    assert item == before
    assert not any("INSERT" in sql for sql, _ in db.calls)
    assert all(params["t"] == TENANT for sql, params in db.calls if "subscription_schedule_recipients r" in sql)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["pending", "running"])
async def test_terminal_reconciliation_leaves_real_unfinished_parts_and_their_recipient_untouched(authority, status):
    parts = [effect(status="accepted", attempts=1, finished_at=NOW),
        effect(id=uuid4(), status=status, schedule_part_index=1, attempts=1 if status == "running" else 0,
            authorized_at=NOW if status == "running" else None)]
    db = DB(effects=parts, recipient_row=recipient(plan=[{"type": "text"}] * 2))
    before = deepcopy((db.recipient, db.effects))
    await runtime.reconcile_terminal_recipients(db, TENANT, now=NOW)
    assert (db.recipient, db.effects) == before
    assert all(sql.startswith("SELECT") for sql, _ in db.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("effect_state,recipient_state", [("accepted", "sent"), ("uncertain", "uncertain")])
async def test_terminal_reconciliation_preserves_existing_receipt_and_uncertainty(authority, effect_state, recipient_state):
    item = effect(status=effect_state, attempts=1, authorized_at=NOW - timedelta(seconds=1), finished_at=NOW)
    before = deepcopy(item)
    db = DB(effects=[item])
    await runtime.reconcile_terminal_recipients(db, TENANT, now=NOW)
    assert db.recipient["status"] == recipient_state and item == before
    assert not any("INSERT" in sql for sql, _ in db.calls)
    assert all("AND status='pending'" in sql for sql, _ in db.calls if sql.startswith("UPDATE subscription_device_effects"))


@pytest.mark.asyncio
async def test_cancel_reconciles_external_terminal_ledger_even_when_no_new_pending_effect_was_closed(authority):
    item = effect(status="not_sent", error_code="settings_changed", finished_at=NOW - timedelta(seconds=1))
    before = deepcopy(item)
    db = DB(effects=[item])
    await runtime.cancel_schedule_pending(db, db.schedule, "SCHEDULE_CANCELLED", run_id=RUN)
    assert db.recipient["status"] == "failed" and db.recipient["error_code"] == "settings_changed"
    assert item == before and not any("INSERT" in sql for sql, _ in db.calls)


@pytest.mark.parametrize("interval", [10, 20, 30, 40, 50, 60])
def test_shared_spacing_starts_at_sequence_finish_and_rolling_limit_is_tenant_wide(interval):
    assert runtime.admission_ready(NOW, NOW - timedelta(seconds=6), NOW - timedelta(seconds=interval), interval, 9)
    assert not runtime.admission_ready(NOW, NOW - timedelta(seconds=5), None, interval, 0)
    assert not runtime.admission_ready(NOW, None, NOW - timedelta(seconds=interval - 1), interval, 0)
    assert not runtime.admission_ready(NOW, None, None, interval, 10)


@pytest.mark.parametrize("interval", [0, 9, 15, 61, True, "10"])
def test_invalid_interval_can_never_be_admitted(interval):
    assert not runtime.admission_ready(NOW, None, None, interval, 0)


def test_unstarted_old_day_requires_release_but_admitted_sequence_can_cross_midnight():
    row = run(local_date="2026-11-04", first_admitted_at=None, timezone="America/Cuiaba")
    assert runtime.past_due_without_confirmation(row, NOW)
    row["manual_released_at"] = NOW
    assert not runtime.past_due_without_confirmation(row, NOW)
    row.update(manual_released_at=None, first_admitted_at=NOW - timedelta(days=1))
    assert not runtime.past_due_without_confirmation(row, NOW)
    # UTC has advanced to tomorrow, but it is still the due day in the tenant.
    row.update(first_admitted_at=None, local_date=date(2026, 11, 4))
    assert not runtime.past_due_without_confirmation(row, datetime(2026, 11, 5, 3, 59, tzinfo=timezone.utc))


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal,expected", [("not_sent", "failed"), ("uncertain", "uncertain")])
async def test_partial_failure_preserves_receipts_and_closes_only_unclaimed_tail(authority, terminal, expected):
    parts = [effect(id=uuid4(), status="accepted", attempts=1, schedule_part_index=0, finished_at=NOW - timedelta(seconds=1)),
        effect(id=uuid4(), status=terminal, attempts=1, schedule_part_index=1, error_code="MESSAGE_CONTENT_INVALID", finished_at=NOW),
        effect(id=uuid4(), status="pending", schedule_part_index=2)]
    protected = deepcopy(parts[:2])
    db = DB(effects=parts, recipient_row=recipient(plan=[{"type": "text"}] * 3))
    assert await runtime.after_schedule_effect_finish(db, device(), parts[1], terminal) == []
    assert parts[:2] == protected and parts[2]["status"] == "not_sent" and parts[2]["attempts"] == 0
    assert db.recipient["status"] == expected and db.recipient["finished_at"] == NOW
    assert all("INSERT" not in sql for sql, _ in db.calls)


@pytest.mark.asyncio
async def test_late_acceptance_corrects_evidence_without_reactivating_failed_tail(authority):
    first = effect(status="accepted", attempts=1, finished_at=NOW)
    tail = effect(id=uuid4(), status="not_sent", schedule_part_index=1, error_code="SCHEDULE_PREDECESSOR_FAILED")
    db = DB(effects=[first, tail], recipient_row=recipient(plan=[{"type": "text"}] * 2,
        status="uncertain", finished_at=NOW - timedelta(seconds=1)))
    before = deepcopy(tail)
    await runtime.after_schedule_effect_finish(db, device(), first, "accepted")
    assert tail == before and db.recipient["status"] == "failed"
    assert not any("INSERT" in sql for sql, _ in db.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["SCHEDULE_SOURCE_REMOVED", "SCHEDULE_OPTED_OUT", "SCHEDULE_CANCELLED", "SCHEDULE_SELF_CONTACT"])
async def test_claimed_sequence_keeps_durable_revocation_after_live_context_is_restored(authority, reason):
    item = effect(status="running", attempts=1, claimed_owner=CLAIM, claimed_fence=8, authorized_at=NOW)
    db = DB(effects=[item], recipient_row=recipient(error_code=reason))
    protected = deepcopy(item)
    await runtime.refresh_recipient(db, TENANT, RECIPIENT)
    assert item == protected and db.recipient["status"] == "running" and db.recipient["error_code"] == reason
    fresh = await runtime.authorize_schedule_effect(db, device(), item, CLAIM, 8,
        EffectRequest(operation_id=EFFECT, recheck=True))
    assert not fresh["authorized"] and fresh["error_code"] == reason and item == protected
    item.update(status="accepted", finished_at=NOW)
    await runtime.after_schedule_effect_finish(db, device(), item, "accepted")
    assert db.recipient["status"] == "sent" and db.recipient["error_code"] is None
    assert item["attempts"] == 1 and not any("INSERT" in sql for sql, _ in db.calls)


@pytest.mark.asyncio
async def test_cancel_series_does_not_materialize_using_stale_active_parent(authority, monkeypatch):
    stale = schedule(repeat_every_30_days=True)
    db = DB(schedule_row=stale | {"status": "cancelled", "version": 2},
        run_row=run(status="pending", first_admitted_at=None),
        recipient_row=recipient(status="pending", admitted_at=None), effects=[])
    async def forbidden(*args): pytest.fail("A cancellation cannot create a later occurrence")
    monkeypatch.setattr(runtime, "materialize_next_occurrences", forbidden)
    await runtime.cancel_schedule_pending(db, stale, "SCHEDULE_CANCELLED")
    assert db.schedule["status"] == "cancelled" and db.recipient["status"] == "cancelled"
    assert db.run["error_code"] == "SCHEDULE_CANCELLED"
    assert not any("INSERT" in sql for sql, _ in db.calls)


@pytest.mark.asyncio
async def test_creating_sequence_chains_exact_frozen_parts_and_allows_midnight_completion():
    calls = []
    class Recorder:
        async def execute(self, query, params=None):
            calls.append((str(query), params))
            return Rows([])
    now = datetime(2026, 11, 5, 23, 59, 59, tzinfo=timezone.utc)
    row = recipient(plan=[{"type": "audio", "asset": {"id": str(uuid4())}}, {"type": "text", "text": "Confirmado"}])
    identities = await runtime.create_effects(Recorder(), device(), schedule(), run(), row, now)
    events = [params for sql, params in calls if sql.startswith("INSERT INTO subscription_device_events")]
    parts = [params for sql, params in calls if sql.startswith("INSERT INTO subscription_device_effects")]
    assert len(events) == 1 and events[0]["provider"] == "scheduled:" + str(RECIPIENT)
    assert json.loads(events[0]["payload"])["run_id"] == str(RUN)
    assert len(identities) == len(parts) == 2 and parts[0]["previous"] is None and parts[1]["previous"] == identities[0]
    assert [json.loads(item["payload"])["action"] for item in parts] == row["plan"]
    assert all(item["run"] == RUN and item["schedule"] == SCHEDULE and item["recipient"] == RECIPIENT for item in parts)
    assert all(item["expiry"] == now + timedelta(minutes=10) for item in parts)


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_local", [False, True])
async def test_recurrence_uses_parent_anchor_and_only_clones_one_future_frozen_revision(authority, invalid_local):
    if invalid_local:
        anchor, clock, zone, now = date(2026, 2, 6), "02:30", "America/New_York", datetime(2026, 2, 10, 12, tzinfo=timezone.utc)
    else:
        anchor, clock, zone, now = date(2026, 1, 1), "12:00", "UTC", NOW
    parent = schedule(anchor_date=anchor, anchor_time=clock, timezone=zone, repeat_every_30_days=True)
    previous = run(status="completed", finished_at=NOW)
    previous["snapshot"]["definition"]["start_date"] = "2026-11-05"  # Edited form date is not the series anchor.
    frozen = deepcopy(previous["snapshot"])
    class RecurrenceDB(DB):
        async def execute(self, query, params=None):
            sql = str(query)
            if sql.startswith("SELECT s.id FROM subscription_schedules"):
                self.calls.append((sql, params))
                return Rows([SCHEDULE])
            if sql.startswith("WITH source AS MATERIALIZED ("):
                self.calls.append((sql, params))
                return Rows([params["run"]])
            return await super().execute(query, params)
    db = RecurrenceDB(schedule_row=parent, run_row=previous)
    await runtime.materialize_next_occurrences(db, device(), now)
    clones = [(sql, params) for sql, params in db.calls if sql.startswith("WITH source AS MATERIALIZED (")]
    assert len(clones) == 1
    sql, params = clones[0]
    expected = policy.next_occurrence({"start_date": anchor, "start_time": clock, "timezone": zone}, now, 0)
    assert (params["index"], params["day"], params["due"]) == expected
    assert params["previous"] == RUN and params["hash"] == "a" * 64 and params["version"] == parent["version"]
    assert json.loads(params["snapshot"]) == frozen and previous["snapshot"] == frozen
    assert "r.run_version=:hash" in sql and "source AS MATERIALIZED" in sql
    assert "r.error_code='SCHEDULE_SELF_CONTACT'" in sql and "bytes<=:maximum" in sql
    assert params["error"] == ("SCHEDULE_RECURRENCE_INVALID" if invalid_local else None)
    if invalid_local:
        assert params["due"] is None
    else:
        assert params["due"] > now and params["index"] > 1  # Missing cycles were skipped.
