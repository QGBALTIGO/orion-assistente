"""Calendar, bounded frozen content and public projections for scheduled messages."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

from fastapi import HTTPException
from pydantic import ValidationError
import pytest

from app import subscription_schedule_policy as policy
from app import subscription_schedule_schemas as schemas
from app import subscription_schedule_store as store
from app.catalog_api import Actor

NOW = datetime(2026, 11, 5, 12, tzinfo=timezone.utc)
TENANT, OWNER, DEVICE, GROUP, SCHEDULE, RUN, KEY = (uuid4() for _ in range(7))
ACTOR = Actor("member", account_id=OWNER)
PHONE = "5567999999901"


def definition(**changes):
    return {"title": "Aviso sintético", "device_id": str(DEVICE), "group_ids": [str(GROUP)],
        "contact_keys": [], "all_contacts": False, "start_date": "2026-11-05", "start_time": "14:00",
        "interval_seconds": 60, "repeat_every_30_days": False,
        "message_type": "text", "text": "Olá, {name}!", "template_id": None} | changes


class Rows:
    def __init__(self, rows): self.rows = rows
    def mappings(self): return self
    def one(self): assert len(self.rows) == 1; return self.rows[0]
    def one_or_none(self): assert len(self.rows) <= 1; return self.rows[0] if self.rows else None
    def all(self): return self.rows
    def scalar_one(self): return self.one()
    def scalar_one_or_none(self): return self.one_or_none()


@pytest.mark.parametrize("change", [
    {"title": " "}, {"title": "x" * 121}, {"timezone": "UTC"}, {"interval_seconds": 0},
    {"interval_seconds": 15}, {"interval_seconds": 61}, {"interval_seconds": True},
    {"repeat_every_30_days": "false"}, {"all_contacts": 1}, {"all_contacts": True, "contact_keys": ["phone:" + PHONE]},
    {"group_ids": []}, {"group_ids": [str(GROUP), str(GROUP)]},
    {"contact_keys": ["phone:" + PHONE, "phone:" + PHONE]}, {"contact_keys": ["+" + PHONE]},
    {"contact_keys": ["phone:0123456789"]}, {"contact_keys": ["phone:12345@lid"]},
    {"start_time": "24:00"}, {"start_time": "7:30"}, {"start_time": "14:00:01"},
    {"text": "{phone}"}, {"text": "{name.__class__}"}, {"text": "{name!r}"}, {"text": "{name:10}"},
    {"text": "{"}, {"text": "x" * 1001}, {"text": "😀" * 501},
    {"text": "válido", "template_id": str(uuid4())}, {"message_type": "template", "template_id": str(uuid4())},
])
def test_definition_rejects_ambiguous_or_unbounded_input(change):
    with pytest.raises(ValidationError):
        schemas.ScheduleDefinition.model_validate(definition(**change))


@pytest.mark.parametrize("interval", schemas.INTERVALS)
def test_observed_intervals_and_the_utf16_boundary_remain_available(interval):
    value = schemas.ScheduleDefinition.model_validate(definition(interval_seconds=interval, text="😀" * 500))
    assert value.interval_seconds == interval and len(value.text.encode("utf-16-le")) == 2000


def test_all_contacts_is_an_explicit_frozen_selection_not_an_injected_phone_list():
    value = schemas.ScheduleDefinition.model_validate(definition(group_ids=[], all_contacts=True))
    assert value.all_contacts and not value.contact_keys
    with pytest.raises(ValidationError):
        schemas.ReviewSchedule(idempotency_key=KEY, schedule=definition(), contacts=[{"phone": PHONE}])


@pytest.mark.parametrize("change", [{"start_date": "2026-11-05"}, {"interval_seconds": 20},
    {"start_date": "2026-11-05", "start_time": "14:00", "interval_seconds": True}])
def test_estimates_require_complete_strict_calendar_fields(change):
    with pytest.raises(ValidationError):
        schemas.AudienceRequest.model_validate(change)


@pytest.mark.parametrize("change", [{"text": "override"}, {"url": "https://example.invalid"},
    {"consent_confirmed": False}, {"consent_confirmed": "true"}, {"confirmation": "SIM"}])
def test_confirmation_only_attests_one_prepared_snapshot(change):
    value = {"idempotency_key": str(KEY), "review_version": "a" * 64, "confirmation": "AGENDAR", "consent_confirmed": True}
    with pytest.raises(ValidationError):
        schemas.ConfirmSchedule.model_validate(value | change)


@pytest.mark.parametrize("value", [0, True, "1", 2**63, 10**100])
def test_optimistic_versions_fit_exactly_the_postgres_bigint_domain(value):
    with pytest.raises(ValidationError):
        schemas.EditSchedule(idempotency_key=KEY, run_id=RUN, expected_version=value,
            expected_run_version=1, schedule=definition())


@pytest.mark.parametrize("day,clock", [("2026-03-08", "02:30"), ("2026-11-01", "01:30")])
def test_initial_dst_gap_and_fold_are_never_silently_shifted(day, clock):
    with pytest.raises(ValueError):
        policy.start_at(definition(start_date=day, start_time=clock) | {"timezone": "America/New_York"})


def test_recurrence_uses_thirty_local_days_and_skips_missed_cycles():
    value = definition(start_date="2026-01-31", start_time="14:00") | {"timezone": "UTC"}
    index, day, due = policy.next_occurrence(value, datetime(2026, 2, 1, tzinfo=timezone.utc))
    assert (index, day, due) == (1, date(2026, 3, 2), datetime(2026, 3, 2, 14, tzinfo=timezone.utc))
    index, day, due = policy.next_occurrence(value, datetime(2026, 6, 5, tzinfo=timezone.utc), after_index=1)
    assert index == 5 and day == date(2026, 6, 30) and due > datetime(2026, 6, 5, tzinfo=timezone.utc)


def test_recurrence_at_exact_clock_is_strictly_future_without_same_cycle_replay():
    value = definition(start_date="2026-10-06", start_time="12:00") | {"timezone": "UTC"}
    assert policy.next_occurrence(value, NOW) == (2, date(2026, 12, 5), datetime(2026, 12, 5, 12, tzinfo=timezone.utc))


@pytest.mark.parametrize("anchor,clock,now,day", [
    ("2026-02-06", "02:30", datetime(2026, 3, 7, 12, tzinfo=timezone.utc), date(2026, 3, 8)),
    ("2026-10-02", "01:30", datetime(2026, 10, 31, 12, tzinfo=timezone.utc), date(2026, 11, 1)),
])
def test_invalid_future_dst_cycle_preserves_local_date_and_has_no_invented_utc(anchor, clock, now, day):
    value = definition(start_date=anchor, start_time=clock) | {"timezone": "America/New_York"}
    assert policy.next_occurrence(value, now) == (1, day, None)


def test_estimate_counts_first_recipient_and_can_cross_midnight():
    due = datetime(2026, 11, 5, 23, 59, tzinfo=timezone.utc)
    assert policy.estimate_completion(due, 1, 60) == due + timedelta(seconds=60)
    assert policy.estimate_completion(due, 3, 60) == datetime(2026, 11, 6, 0, 2, tzinfo=timezone.utc)
    assert policy.estimate_completion(due, 0, 60) is None
    assert policy.estimate_completion(None, 1, 60) is None
    with pytest.raises(ValueError):
        policy.estimate_completion(datetime.max.replace(tzinfo=timezone.utc), 1, 60)


def test_personalization_is_once_only_and_checks_utf16_after_expansion():
    assert policy.render_text("Olá {name}", {"name": "Ana {phone}"}) == "Olá Ana {phone}"
    with pytest.raises(ValueError):
        policy.render_text("x" * 990 + "{name}", {"name": "😀" * 6})


@pytest.mark.parametrize("kind", schemas.KINDS)
def test_templates_share_all_eight_existing_transport_plans_without_content_loss(kind):
    item = {"kind": kind, "body": "Olá {name}" if kind in {"text", "audio", "image", "video", "document"} else "",
        "followup_body": "Fim" if kind in {"audio", "image", "video", "document"} else "",
        "asset": {"id": str(uuid4()), "kind": kind, "file_name": "arquivo"} if kind not in {"text", "location"} else None,
        "location": {"latitude": -20.4, "longitude": -54.6} if kind == "location" else None}
    before = deepcopy(item)
    plan = policy.frozen_plan(definition(message_type="template", text=None), item, {"name": "Ana"})
    assert plan[0]["type"] == kind and 1 <= len(plan) <= 3 and item == before
    if kind == "audio":
        assert [part["type"] for part in plan] == ["audio", "text", "text"]
        assert plan[1]["text"] == "Olá Ana"


def test_run_buckets_respect_local_day_explicit_release_and_uncertain_history():
    run = {"status": "pending", "due_at": NOW - timedelta(days=1), "local_date": date(2026, 11, 4), "timezone": "UTC",
        "first_admitted_at": None, "manual_released_at": None, "error_code": None}
    assert policy.run_projection(run, NOW)["bucket"] == "past_due"
    assert policy.run_projection(run | {"manual_released_at": NOW}, NOW)["active_filter"] == "attention"
    assert policy.run_projection(run | {"status": "running"}, NOW)["active_filter"] == "queued"
    assert policy.run_projection(run | {"status": "running", "effect_running": True}, NOW)["active_filter"] == "processing"
    assert policy.run_projection(run | {"status": "uncertain", "error_code": "MESSAGE_OUTCOME_UNCERTAIN"}, NOW)["bucket"] == "history"
    assert policy.run_projection(run | {"due_at": None, "error_code": "SCHEDULE_RECURRENCE_INVALID"}, NOW)["active_filter"] == "attention"
    future = run | {"due_at": NOW + timedelta(days=1), "local_date": date(2026, 11, 6)}
    assert policy.run_projection(future, NOW)["active_filter"] == "upcoming"
    assert policy.run_projection(future, NOW, False)["attention_reason"] == "SCHEDULE_DEVICE_UNAVAILABLE"


@pytest.mark.parametrize("code", ["SCHEDULE_REVOKED", "SCHEDULE_CANCELLED", "SCHEDULE_OPTED_OUT",
    "SCHEDULE_SOURCE_REMOVED", "SCHEDULE_CONTEXT_CHANGED", "SCHEDULE_TEMPLATE_CHANGED"])
def test_revocation_preserves_partial_delivery_and_never_hides_uncertain(code):
    part = {"status": "not_sent", "error_code": code, "expires_at": NOW + timedelta(minutes=5)}
    recipient = {"status": "running", "plan": [{}]}
    assert policy.recipient_state(recipient, [part], NOW) == ("cancelled", code)
    recipient["plan"].append({})
    assert policy.recipient_state(recipient, [{"status": "accepted"}, part], NOW) == ("failed", code)
    assert policy.recipient_state(recipient, [{"status": "uncertain"}, part], NOW) == ("uncertain", "MESSAGE_OUTCOME_UNCERTAIN")


def test_expired_effect_projection_has_scheduled_code_without_mutation():
    parts = [{"status": "pending", "expires_at": NOW}]
    before = deepcopy(parts)
    assert policy.recipient_state({"status": "running", "plan": [{}]}, parts, NOW) == ("failed", "SCHEDULE_EFFECT_EXPIRED")
    assert parts == before


@pytest.mark.parametrize("expected,run_expected,marker", [(1, 1, True), (2, 1, True), (1, 3, False), (3, 1, False)])
def test_only_proven_stale_both_versions_produce_bound_rejection(expected, run_expected, marker):
    payload = schemas.EditSchedule(idempotency_key=KEY, run_id=RUN, expected_version=expected,
        expected_run_version=run_expected, schedule=definition())
    with pytest.raises(HTTPException) as error:
        store.check_versions({"id": SCHEDULE, "version": 2}, {"id": RUN, "version": 2}, payload, "review_edit")
    assert ("rejection" in error.value.detail) is marker
    if marker:
        assert error.value.detail["rejection"] == {"reason": "stale_version", "idempotency_key": str(KEY),
            "kind": "review_edit", "schedule_id": str(SCHEDULE), "run_id": str(RUN),
            "expected_version": expected, "expected_run_version": run_expected}


def test_json_budget_includes_postgres_separators_and_frozen_metadata_excludes_plans():
    value = {"many": [1, 2, 3]}
    with pytest.raises(HTTPException) as error:
        store.bounded_json(value, len(store.encode(value).encode()))
    assert error.value.detail["code"] == "SCHEDULE_LIMIT"
    snapshot = {"definition": definition(), "device": {}, "template": None, "facts": {}, "groups": [],
        "contacts": [{"phone": PHONE, "plan": [{"text": "x" * 1000}]}]}
    metadata = store.metadata_snapshot(snapshot, ACTOR, KEY, NOW)
    assert "contacts" not in metadata and metadata["consent"]["confirmed"] is True
    metadata["definition"]["title"] = "Changed copy"
    assert snapshot["definition"]["title"] == "Aviso sintético"


@pytest.mark.asyncio
async def test_audience_limit_fails_before_materializing_ten_thousand_plans():
    class DB:
        calls = 0
        async def execute(self, query, params):
            self.calls += 1
            assert "count(*) FROM selected" in str(query)
            return Rows([{"total": 10001, "direct": 0, "from_groups": 10001, "duplicates": 0, "blocked": 0}])
    db = DB()
    with pytest.raises(HTTPException) as error:
        await store.selected_contacts(db, TENANT, definition())
    assert error.value.detail["code"] == "SCHEDULE_LIMIT" and db.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("verified,eligible", [(True, 0), (False, 1)])
async def test_review_blocks_only_the_canonical_verified_self_number(monkeypatch, verified, eligible):
    class DB:
        async def execute(self, query, params=None):
            assert "contact_phone" in str(query)
            return Rows([{"name": "Empresa", "email": "owner@example.invalid", "contact_phone": None}])
    async def clock(db): return NOW
    async def zone(db, tenant): return "UTC"
    async def groups(*args): return [{"id": str(GROUP), "version": 1, "name": "Grupo", "member_count": 1}]
    async def device(*args, **kwargs):
        return {"id": DEVICE, "name": "Aparelho", "generation": uuid4(), "revision": 1, "status": "connected",
            "owner_account_id": OWNER, "channel_id": uuid4(), "runtime": {"phone": PHONE, "phone_verified": verified}}, {"enabled": True}
    async def contacts(*args):
        return [{"phone": PHONE, "name": "Ana", "origins": [{"kind": "customer", "id": str(uuid4()), "phone": PHONE, "version": 1}],
                 "permission_version": 0, "blocked": False}], {"total": 1, "direct": 0, "from_groups": 1, "duplicates": 0, "blocked": 0, "eligible": 1}
    monkeypatch.setattr(store, "clock", clock)
    monkeypatch.setattr(store, "timezone_for", zone)
    monkeypatch.setattr(store.campaigns, "group_summaries", groups)
    monkeypatch.setattr(store.shared, "check_device", device)
    monkeypatch.setattr(store, "selected_contacts", contacts)
    snapshot = await store.capture_review(DB(), TENANT, ACTOR, definition(start_time="00:00"))
    assert snapshot["counts"]["eligible"] == eligible
    assert snapshot["contacts"][0]["blocked"] is verified
    assert snapshot["due_at"] == "2026-11-05T00:00:00+00:00"  # Earlier today remains valid.
    assert snapshot["contacts"][0]["plan"] == [{"type": "text", "text": "Olá, Ana!"}]
    with pytest.raises(HTTPException) as error:
        await store.capture_review(DB(), TENANT, ACTOR, definition(start_date="2026-11-04"))
    assert error.value.status_code == 422


@pytest.mark.asyncio
async def test_source_budget_refuses_unknown_or_unbounded_origins_before_database():
    class DB:
        async def execute(self, *args): raise AssertionError("Invalid origins must not reach SQL")
    for value in (None, [], {}, [{}] * 102):
        assert not await store.sources_valid(DB(), TENANT, value, PHONE)
    with pytest.raises(ValueError):
        store.source_valid_sql("r); DELETE FROM customers; --")
