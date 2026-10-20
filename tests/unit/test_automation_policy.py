from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.automation_policy import delivery_reason, render_template, schedule_for, send_window

NOW = datetime(2026, 10, 19, 14, tzinfo=timezone.utc)


@pytest.mark.parametrize("template", ["{cliente.__class__}", "{cliente[secret]}", "{cliente!r}", "{valor:>10}", "{unknown}", "{", "}", " "])
def test_templates_never_interpret_code_or_invent_fields(template):
    with pytest.raises(ValueError):
        render_template(template, {"cliente": "Pessoa sintética"})


@pytest.mark.parametrize("missing", [None, "", " ", 35, {"value": "Preço"}, "x"*501])
def test_missing_or_nonscalar_fact_never_becomes_a_message(missing):
    with pytest.raises(ValueError):
        render_template("Olá {cliente}", {"cliente": missing})


def test_templates_preserve_real_fact_values_and_escape_literal_braces():
    assert render_template("{{Aviso}} {cliente}: {servico} por {valor}", {"cliente": "Pessoa", "servico": "Serviço", "valor": "R$ 35,00"}) == "{Aviso} Pessoa: Serviço por R$ 35,00"


def test_whatsapp_utf16_budget_is_checked_before_transport():
    assert len(render_template("😀"*600, {})) == 600
    with pytest.raises(ValueError):
        render_template("😀"*601, {})


@pytest.mark.parametrize("hour,minute,expected_day,expected_hour", [(8, 59, 19, 9), (9, 0, 19, 9), (17, 59, 19, 17), (18, 0, 20, 9), (23, 0, 20, 9)])
def test_window_boundaries_use_real_local_timezone(hour, minute, expected_day, expected_hour):
    moment = NOW.replace(hour=hour, minute=minute)
    result = send_window(moment, "UTC")
    assert result.day == expected_day and result.hour == expected_hour


def test_window_does_not_use_server_timezone():
    # 12:00 UTC is 08:00 in Cuiabá; defer to 09:00 local (13:00 UTC).
    assert send_window(NOW.replace(hour=12), "America/Cuiaba") == NOW.replace(hour=13)
    with pytest.raises(ValueError):
        send_window(NOW.replace(tzinfo=None), "UTC")


@pytest.mark.parametrize("event,offset", [("reminder_24h", 24), ("reminder_2h", 2), ("reminder_1h", 1)])
def test_reminders_keep_only_the_configured_short_deadline(event, offset):
    start = NOW + timedelta(hours=offset+1)
    value = schedule_for(event, {"status": "confirmed", "start_at": start}, NOW, "UTC")
    assert value.due_at == NOW + timedelta(hours=1)
    assert value.expires_at == value.due_at + timedelta(minutes=15)
    assert schedule_for(event, {"status": "cancelled", "start_at": start}, NOW, "UTC") is None


@pytest.mark.parametrize("event", ["booking_confirmed", "rescheduled", "after_service", "loyalty_reward"])
def test_notification_requires_real_trigger_instead_of_creation_now(event):
    assert schedule_for(event, {}, NOW, "UTC") is None
    status = "completed" if event == "after_service" else "confirmed"
    result = schedule_for(event, {"occurred_at": NOW, "status": status}, NOW, "UTC")
    assert result.due_at == NOW and result.expires_at == NOW+timedelta(hours=24)


@pytest.mark.parametrize("event,days", [("inactive_60d", 60), ("cancelled_7d", 7)])
def test_recovery_never_sends_to_someone_with_a_future_booking(event, days):
    facts = {"occurred_at": NOW-timedelta(days=days), "has_future_booking": False}
    assert schedule_for(event, facts, NOW, "UTC").due_at == NOW
    assert schedule_for(event, {**facts, "has_future_booking": True}, NOW, "UTC") is None


@pytest.mark.parametrize("event,offset", [("birthday_7d", 7), ("birthday_2d", 2)])
def test_birthdays_require_registered_date_and_exact_local_day(event, offset):
    target = (NOW+timedelta(days=offset)).date().replace(year=1990)
    assert schedule_for(event, {"birth_date": target}, NOW, "UTC") is not None
    assert schedule_for(event, {"birth_date": target+timedelta(days=1)}, NOW, "UTC") is None
    assert schedule_for(event, {}, NOW, "UTC") is None


def context(event="campaign"):
    version = NOW-timedelta(days=1)
    job = {"event": event, "settings_version": 1, "preferences_version": 2, "consent_version": version,
           "interaction_revision": 3, "due_at": NOW-timedelta(minutes=1), "expires_at": NOW+timedelta(hours=1)}
    consent = {"updated_at": version, "operational_opt_in": True, "marketing_opt_in": True}
    state = {"human_paused": False, "revision": 3}
    settings = {"enabled": True, "version": 1}
    return job, consent, state, settings


@pytest.mark.parametrize("change,reason", [("disabled", "execution_paused"), ("settings", "configuration_changed"),
    ("preferences", "configuration_changed"), ("expired", "expired"), ("future", "not_due"),
    ("consent", "consent_changed"), ("consent_version", "consent_changed"),
    ("human", "conversation_changed"), ("revision", "conversation_changed")])
def test_all_authority_changes_stop_before_claim(change, reason):
    job, consent, state, settings = context()
    pref = 2
    if change == "disabled": settings["enabled"] = False
    if change == "settings": settings["version"] = 2
    if change == "preferences": pref = 3
    if change == "expired": job["expires_at"] = NOW
    if change == "future": job["due_at"] = NOW+timedelta(seconds=1)
    if change == "consent": consent["marketing_opt_in"] = False
    if change == "consent_version": consent["updated_at"] = NOW
    if change == "human": state["human_paused"] = True
    if change == "revision": state["revision"] = 4
    assert delivery_reason(job, consent, state, settings, now=NOW, preferences_version=pref) == reason


@pytest.mark.parametrize("event", ["campaign", "after_service", "loyalty_reward", "birthday_7d", "inactive_60d"])
def test_operational_consent_never_authorizes_promotional_purpose(event):
    job, consent, state, settings = context(event)
    consent["marketing_opt_in"] = False
    assert delivery_reason(job, consent, state, settings, now=NOW, preferences_version=2) == "consent_changed"


def test_marketing_consent_never_authorizes_operational_reminder():
    job, consent, state, settings = context("reminder_1h")
    consent["operational_opt_in"] = False
    assert delivery_reason(job, consent, state, settings, now=NOW, preferences_version=2) == "consent_changed"


@pytest.mark.parametrize("status,version", [("paused", 2), ("draft", 2), ("approved", 3)])
def test_changed_campaign_never_uses_old_approved_message(status, version):
    job, consent, state, settings = context()
    job.update(campaign_id=uuid4(), campaign_version=2)
    assert delivery_reason(job, consent, state, settings, now=NOW, preferences_version=2, campaign={"status": status, "version": version}) == "campaign_paused"
