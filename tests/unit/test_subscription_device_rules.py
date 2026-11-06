"""Observed device policy boundaries, independent of transport/provider calls."""
from datetime import date, datetime, timezone
from copy import deepcopy

import pytest

from app.subscription_device_rules import (brazil_holiday, business_state,
    effects_for_event, transcription_parts)
from app.subscription_device_schemas import DeviceSettings


def config():
    return DeviceSettings().model_dump(mode="json")


def at(value):
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def office(settings):
    settings["business_hours"].update(enabled=True, outside_message="Fechado", break_message="Pausa")
    settings["business_hours"]["days"][0].update(enabled=True, start="09:00", break_start="12:00", break_end="13:00", end="18:00")
    return settings


@pytest.mark.parametrize("clock,state,text", [
    ("08:59", "closed", "Fechado"), ("09:00", "open", None),
    ("11:59", "open", None), ("12:00", "break", "Pausa"),
    ("12:59", "break", "Pausa"), ("13:00", "open", None),
    ("17:59", "open", None), ("18:00", "closed", "Fechado"),
])
def test_business_hours_respect_local_timezone_and_end_exclusive(clock, state, text):
    settings = office(config())
    now = at(f"2026-11-09T{clock}:00-03:00")
    assert business_state(settings, now) == state
    effects = effects_for_event(settings, {"kind": "text", "phone": "5567999999901"}, now=now)
    assert [item["payload"]["text"] for item in effects] == ([] if text is None else [text])


def test_enabled_empty_week_closes_every_day_and_disabled_hours_leave_engine_open():
    settings = config()
    settings["business_hours"].update(enabled=True, outside_message="Ausente")
    assert business_state(settings, at("2026-11-09T15:00:00Z")) == "closed"
    settings["business_hours"]["enabled"] = False
    assert business_state(settings, at("2026-11-09T15:00:00Z")) == "open"


@pytest.mark.parametrize("day,expected", [
    (date(2026, 4, 3), True), (date(2027, 3, 26), True), (date(2026, 11, 20), True),
    (date(2026, 1, 1), True), (date(2026, 2, 17), False), (date(2026, 6, 4), False),
    (date(2026, 10, 30), False), (date(2026, 11, 3), False),
])
def test_national_holidays_exclude_optional_federal_closures(day, expected):
    assert brazil_holiday(day) is expected


def test_holiday_is_evaluated_in_device_timezone():
    settings = config()
    settings["business_hours"].update(enabled=True, holiday_off=True)
    settings["business_hours"]["days"][3].update(enabled=True, start="00:00", end="23:59")
    settings["business_hours"]["days"][4].update(enabled=True, start="00:00", end="23:59")
    assert business_state(settings, at("2026-11-20T02:58:00Z")) == "open"
    assert business_state(settings, at("2026-11-20T03:00:00Z")) == "closed"


def call(phone="5567999999901"):
    return {"kind": "call", "phone": phone, "call_id": "CALL-001", "call_from": "5567999999901@s.whatsapp.net"}


def test_call_whitelist_and_acceptance_windows_have_precedence():
    settings = config()
    settings["calls"].update(reject_enabled=True, reply_enabled=True, schedule_enabled=True,
                             holiday_off=True, whitelist=["5567999999902"])
    settings["calls"]["days"][4].update(enabled=True, start="09:00", end="18:00")
    assert effects_for_event(settings, call(), now=at("2026-11-13T09:00:00-03:00")) == []
    assert [item["type"] for item in effects_for_event(settings, call(), now=at("2026-11-13T18:00:00-03:00"))] == ["reject_call", "send_text"]
    assert [item["type"] for item in effects_for_event(settings, call(), now=at("2026-11-20T10:00:00-03:00"))] == ["reject_call", "send_text"]
    assert effects_for_event(settings, call("5567999999902"), now=at("2026-11-20T10:00:00-03:00")) == []


def test_hidden_phone_call_can_be_rejected_but_never_receives_text():
    settings = config()
    settings["calls"].update(reject_enabled=True, reply_enabled=True, whitelist=["5567999999901"])
    event = {**call(None), "call_from": "123456789012345@lid"}
    effects = effects_for_event(settings, event)
    assert len(effects) == 1 and effects[0]["type"] == "reject_call"
    assert effects[0]["payload"]["call_from"] == event["call_from"]
    assert effects[0]["payload"]["phone"] is None


def test_chatbot_hours_switch_does_not_override_call_or_transcription_settings():
    settings = office(config())
    settings["business_hours"]["block_replies_outside_hours"] = True
    settings["calls"].update(reject_enabled=True, reply_enabled=True)
    settings["transcription"].update(enabled=True, max_minutes=30, reply_enabled=False)
    now = at("2026-11-09T18:00:00-03:00")
    assert [item["type"] for item in effects_for_event(settings, call(), now=now)] == ["reject_call", "send_text"]
    audio = {"kind": "audio", "phone": "5567999999901", "message_id": "AUDIO-1"}
    effects = effects_for_event(settings, audio, now=now, audio_available=True)
    assert [item["type"] for item in effects] == ["send_text", "transcribe"]
    assert effects[-1]["payload"]["max_seconds"] == 1800
    assert effects_for_event(settings, audio, now=now, audio_available=False)[0]["purpose"] == "absence"
    assert all(item["purpose"] != "chatbot" for item in effects)


def test_disabled_default_is_inert_for_every_incoming_event():
    settings = config()
    for event in ({"kind": "text", "phone": "5567999999901"},
                  {"kind": "audio", "phone": "5567999999901", "message_id": "A1"}, call()):
        assert effects_for_event(settings, event, audio_available=True) == []


def test_status_outbound_and_unverified_message_never_generate_reply():
    settings = office(config())
    for kind in ("status", "outbound", "unknown"):
        assert effects_for_event(settings, {"kind": kind, "phone": "5567999999901"}) == []
    assert effects_for_event(settings, {"kind": "text"}) == []


def test_transcript_preserves_all_unicode_text_template_and_footer_in_transport_sized_parts():
    settings = config()
    settings["transcription"].update(template="{name}\n{transcript}\n{footer}", footer="Rodapé")
    event = {"phone": "5567999999901", "name": "Pessoa"}
    source = ("á😀fim\n" * 20000)[:120000]
    before = deepcopy(settings)
    chunks = transcription_parts(settings, event, source)
    assert "".join(chunks) == "Pessoa\n" + source.rstrip() + "\n\nRodapé"
    assert all(len(chunk.encode("utf-16-le")) // 2 <= 32000 for chunk in chunks)
    assert settings == before and len(chunks) > 3
    with pytest.raises(ValueError):
        transcription_parts(settings, event, "x" * 120001)
