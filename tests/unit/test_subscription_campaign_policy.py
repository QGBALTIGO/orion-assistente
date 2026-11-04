"""Calendar, consent, snapshot budgets and immutable campaign content."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
from uuid import uuid4

from fastapi import HTTPException
from pydantic import ValidationError
import pytest

from app import subscription_campaign_policy as policy
from app import subscription_campaign_schemas as schemas
from app import subscription_campaign_store as store
from app.catalog_api import Actor

NOW = datetime(2026, 11, 4, 12, tzinfo=timezone.utc)
TENANT, OWNER, DEVICE, GROUP, CAMPAIGN, KEY = (uuid4() for _ in range(6))
ACTOR = Actor("member", account_id=OWNER)


def definition(**changes):
    return {"title": "Campanha sintética", "device_ids": [str(DEVICE)], "group_ids": [str(GROUP)],
        "message_type": "text", "text": "Olá, {name}! {phone}", "template_id": None,
        "start_date": "2026-11-04", "start_time": "00:00", "timezone": "UTC", "window_start": "08:00",
        "window_end": "18:00", "weekdays": [0, 1, 2, 3, 4, 5, 6], "interval_seconds": 60} | changes


@pytest.mark.parametrize("value", ["+55 (67) 99999-9901", "55 67 99999-9901", "+5567999999901", "5567999999901"])
def test_phone_presentation_has_one_canonical_recipient(value):
    assert schemas.Member(name="Pessoa", phone=value).phone == "5567999999901"


@pytest.mark.parametrize("value", ["5567@s.whatsapp.net", "123@lid", "https://example.test", "012345678", "1"*16,
    "12345", "+55\n67999999901", "１"*10, "55.67999999901", "55 6799999901 ramal 3"])
def test_phone_requires_country_code_domain_without_jid_url_extension_or_controls(value):
    with pytest.raises(ValidationError):
        schemas.Member(name="Pessoa", phone=value)


@pytest.mark.parametrize("change", [{"interval_seconds": 29}, {"interval_seconds": 61}, {"interval_seconds": True},
    {"weekdays": []}, {"weekdays": [1, 1]}, {"weekdays": [True]}, {"device_ids": []}, {"group_ids": []},
    {"device_ids": [str(DEVICE), str(DEVICE)]}, {"timezone": "No/Such_Zone"}, {"window_start": "18:00"},
    {"text": "{missing}"}, {"text": "{name.__class__}"}, {"text": "{phone!r}"}, {"text": "{name:20}"},
    {"text": "😀"*1751}, {"text": "x"*3501}, {"message_type": "template", "template_id": str(uuid4())}])
def test_definition_rejects_ambiguous_or_unbounded_content(change):
    with pytest.raises(ValidationError):
        schemas.CampaignDefinition.model_validate(definition(**change))


@pytest.mark.parametrize("day,clock", [("2026-03-08", "02:30"), ("2026-11-01", "01:30")])
def test_start_rejects_dst_gap_and_fold(day, clock):
    with pytest.raises(ValidationError):
        schemas.CampaignDefinition.model_validate(definition(start_date=day, start_time=clock, timezone="America/New_York"))


@pytest.mark.parametrize("change", [
    {"start_date": "9999-12-31", "window_start": "22:00", "window_end": "02:00"},
    {"start_date": "0001-01-01", "start_time": "00:00", "timezone": "Asia/Tokyo"},
    {"start_date": "9999-12-31", "start_time": "23:59", "window_start": "00:00", "window_end": "01:00"},
])
def test_extreme_calendar_overflow_is_a_validation_error_not_http_500(change):
    with pytest.raises(ValidationError):
        schemas.CampaignDefinition.model_validate(definition(**change))


def test_overnight_window_belongs_to_start_weekday_and_waits_without_catchup():
    value = definition(start_date="2026-11-01", window_start="22:00", window_end="02:00", weekdays=[0])
    assert policy.active_window(value, datetime(2026, 11, 2, 1, tzinfo=timezone.utc)) == (
        datetime(2026, 11, 1, 22, tzinfo=timezone.utc), datetime(2026, 11, 2, 2, tzinfo=timezone.utc))
    assert policy.active_window(value, datetime(2026, 11, 2, 2, tzinfo=timezone.utc)) is None
    assert policy.next_window(value, datetime(2026, 11, 2, 2, tzinfo=timezone.utc)) == datetime(2026, 11, 8, 22, tzinfo=timezone.utc)


def test_future_dst_invalid_window_is_skipped_without_shifting_the_wall_clock():
    value = definition(start_date="2026-03-01", timezone="America/New_York", window_start="02:30", window_end="04:00", weekdays=[0])
    now = datetime(2026, 3, 8, 5, tzinfo=timezone.utc)
    assert policy.next_window(value, now) == datetime(2026, 3, 15, 6, 30, tzinfo=timezone.utc)


def test_substitution_is_once_only_and_counts_utf16_after_expansion():
    facts = {"name": "Pessoa {phone}", "phone": "+5567999999901"}
    assert policy.render_text("{name}: {phone}", facts) == "Pessoa {phone}: +5567999999901"
    with pytest.raises(ValueError):
        policy.render_text("x"*3495 + "{name}", facts)


def asset(kind):
    return {"id": str(uuid4()), "kind": kind, "mime": "application/octet-stream", "size": 20,
        "sha256": "a"*64, "width": None, "height": None, "duration_ms": None, "file_name": "arquivo", "thumbnail": None}


@pytest.mark.parametrize("kind", schemas.KINDS)
def test_all_eight_library_types_freeze_real_content_without_http_or_rule_expansion(kind):
    item = {"id": str(uuid4()), "revision": 2, "name": "Template", "kind": kind,
        "body": "Olá, {name}" if kind in {"text", "image", "video", "audio", "document"} else "",
        "followup_body": "Concluído" if kind in {"image", "video", "audio", "document"} else "",
        "location": {"latitude": -20.4, "longitude": -54.6} if kind == "location" else None,
        "asset": asset(kind) if kind not in {"text", "location"} else None, "file_name": None}
    before = deepcopy(item)
    plan = policy.frozen_plan(definition(message_type="template", text=None, template_id=item["id"]), item,
        {"name": "Contato {phone}", "phone_number": "5567999999901"})
    assert plan[0]["type"] == kind and 1 <= len(plan) <= 3
    assert item == before
    if kind == "audio":
        assert [part["type"] for part in plan] == ["audio", "text", "text"]
        assert plan[0]["caption"] == "" and plan[1]["text"] == "Olá, Contato {phone}"
    if item["asset"]:
        assert plan[0]["asset"] == item["asset"] and plan[0]["asset"] is not item["asset"]
    assert all("url" not in json.dumps(part) for part in plan)


@pytest.mark.parametrize("extra", [{"text": "override"}, {"phone": "5567999999901"}, {"url": "http://127.0.0.1"},
    {"campaign": definition()}, {"consent_confirmed": False}, {"consent_confirmed": "true"}])
def test_confirm_only_attests_the_exact_prepared_snapshot(extra):
    value = {"idempotency_key": str(KEY), "review_version": "a"*64, "confirmation": "AGENDAR", "consent_confirmed": True}
    with pytest.raises(ValidationError):
        schemas.ConfirmCampaign.model_validate(value | extra)


def test_group_import_does_not_accept_a_consent_override_and_permission_is_separate():
    with pytest.raises(ValidationError):
        schemas.CreateGroup(idempotency_key=KEY, name="Grupo", members=[], consent=True)
    with pytest.raises(ValidationError):
        schemas.GroupPermissions(idempotency_key=KEY, expected_version=1,
            contacts=[{"phone": "5567999999901", "version": 0}], evidence="Autorizou hoje", confirmation="SIM")
    assert schemas.CreateGroup(idempotency_key=KEY, name="Grupo", members=[]).members == []


@pytest.mark.parametrize("version", [0, 2**63-1])
def test_permission_versions_preserve_the_complete_postgres_bigint_domain(version):
    value = schemas.PermissionContact(phone="5567999999901", version=version)
    assert value.version == version


@pytest.mark.parametrize("expected,marker", [(1, True), (2, False), (9, False)])
def test_only_proven_stale_version_has_bound_final_rejection(expected, marker):
    request = schemas.ReviewCampaign(idempotency_key=KEY, expected_version=expected)
    if expected == 2:
        assert store.check_version({"version": 2}, request, "review", campaign_id=CAMPAIGN) is None
    else:
        with pytest.raises(HTTPException) as error:
            store.check_version({"version": 2}, request, "review", campaign_id=CAMPAIGN)
        assert ("rejection" in error.value.detail) is marker
        if marker:
            assert error.value.detail["rejection"] == {"reason": "stale_version", "idempotency_key": str(KEY),
                "kind": "review", "campaign_id": str(CAMPAIGN), "group_id": None, "expected_version": expected}


def test_json_budget_accounts_for_postgres_separator_bytes_and_errors_are_not_cached():
    value = {"many": [1, 2, 3]}
    wire = len(store.encode(value).encode())
    with pytest.raises(HTTPException) as error:
        store.bounded_json(value, wire)
    assert error.value.status_code == 422 and error.value.detail["code"] == "CAMPAIGN_LIMIT"
    assert error.value.headers["Cache-Control"] == "no-store"
    assert store.fail("CAMPAIGN_NOT_FOUND", 404).headers["Cache-Control"] == "no-store"


def test_expansion_budget_stops_valid_individual_plans_before_half_gigabyte_batch():
    template = {"kind": "text", "body": "{name}"*166, "followup_body": ""}
    contact = {"name": "漢"*100, "phone": "5567999999901", "groups": [str(GROUP)], "permission_version": 0, "blocked": False}
    plan = policy.frozen_plan(definition(message_type="template", text=None), template, {"name": contact["name"]})
    assert len(plan[0]["text"]) == 16600
    store.bounded_json(plan, 131072)
    total, contacts = 2, 0
    with pytest.raises(HTTPException) as error:
        for _ in range(10000):
            total = store.expanded_budget(total, contact, plan)
            contacts += 1
    assert error.value.detail["code"] == "CAMPAIGN_LIMIT" and total <= schemas.MAX_EXPANDED_BYTES
    assert 1000 < contacts < 1400


@pytest.mark.parametrize("value", ["Factory", "localtime"])
def test_server_pseudozones_are_not_valid_campaign_timezones(value):
    with pytest.raises(ValidationError):
        schemas.CampaignDefinition.model_validate(definition(timezone=value))


class Rows:
    def __init__(self, rows): self.rows = rows
    def mappings(self): return self
    def scalars(self): return self
    def one(self): assert len(self.rows) == 1; return self.rows[0]
    def one_or_none(self): assert len(self.rows) <= 1; return self.rows[0] if self.rows else None
    def all(self): return self.rows
    def scalar_one(self): return self.one()
    def scalar_one_or_none(self): return self.one_or_none()


@pytest.mark.asyncio
async def test_large_audience_is_rejected_by_aggregate_before_any_contact_materialization(monkeypatch):
    calls = []
    class DB:
        async def execute(self, query, params):
            sql = str(query)
            calls.append(sql)
            assert "count(DISTINCT m.phone)" in sql
            return Rows([{"contacts": 10001, "links": 500000}])
    async def references(*args, **kwargs): return [], [], None
    monkeypatch.setattr(store, "validate_references", references)
    with pytest.raises(HTTPException) as error:
        await store.capture_review(DB(), {"tenant_id": TENANT, "definition": definition()}, ACTOR)
    assert error.value.detail["code"] == "CAMPAIGN_LIMIT" and len(calls) == 1


@pytest.mark.parametrize("seconds,campaign_seconds,count,ready", [(5, 60, 0, False), (6, 29, 0, False),
    (6, 30, 9, True), (60, 60, 10, False), (90, 90, 0, True)])
def test_pacing_uses_terminal_time_not_slow_admission_time(seconds, campaign_seconds, count, ready):
    assert policy.admission_ready(NOW, NOW-timedelta(seconds=seconds), NOW-timedelta(seconds=campaign_seconds), 30, count) is ready


def test_slow_transport_never_creates_a_catchup_burst():
    admitted = NOW-timedelta(seconds=95)
    finished = NOW-timedelta(seconds=5)
    assert admitted < NOW-timedelta(seconds=60)
    assert not policy.admission_ready(NOW, finished, finished, 30, 0)
    assert not policy.admission_ready(finished+timedelta(seconds=29), finished, finished, 30, 0)
    assert policy.admission_ready(finished+timedelta(seconds=30), finished, finished, 30, 0)


@pytest.mark.parametrize("states,expected", [(["accepted"], "sent"), (["not_sent"], "failed"),
    (["uncertain"], "uncertain"), (["running"], "pending"), (["accepted", "not_sent"], "failed"),
    (["accepted", "uncertain", "pending"], "uncertain"), (["accepted", "pending"], "pending")])
def test_part_sequence_keeps_partial_and_uncertain_outcomes(states, expected):
    recipient = {"status": "running", "plan": [{} for _ in states]}
    parts = [{"status": state, "expires_at": NOW+timedelta(seconds=30), "authorized_at": NOW,
              "error_code": "MESSAGE_CONTENT_INVALID" if state == "not_sent" else None} for state in states]
    original = deepcopy(parts)
    assert policy.recipient_state(recipient, parts, NOW)[0] == expected
    assert parts == original


def test_expired_pending_and_interrupted_running_are_projected_without_writes():
    recipient = {"status": "running", "plan": [{}]}
    pending = {"status": "pending", "expires_at": NOW}
    assert policy.recipient_state(recipient, [pending], NOW) == ("failed", "CAMPAIGN_EFFECT_EXPIRED")
    running = {"status": "running", "expires_at": NOW+timedelta(minutes=5), "authorized_at": NOW-timedelta(seconds=181)}
    assert policy.recipient_state(recipient, [running], NOW) == ("uncertain", "MESSAGE_OUTCOME_UNCERTAIN")
    assert running["status"] == "running" and pending["status"] == "pending"
