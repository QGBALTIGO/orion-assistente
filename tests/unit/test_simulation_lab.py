"""Independent smoke tests for the synthetic simulation harness, not Orion E2E."""
import json
import random

import pytest

from simulation_lab.orion_lab import (
    COMPANY_TYPES, DURATIONS, QUOTAS, TOTAL, bookable,
    company_profile, digest, ident, make_journey, make_scenario,
    money_result, read_jsonl_gz, write_jsonl_gz,
)
from simulation_lab.utterances import render_message, OPERATION


def booking_state():
    return {
        "start_min": 540, "duration_min": 40, "weekday": 0, "day_off": 6,
        "opens_min": 540, "closes_min": 1080, "lunch": [720, 780],
        "manual_override": False, "blocked": False, "block_interval": [0, 0],
        "existing": False, "existing_interval": [0, 0],
    }


def test_quotas_are_exact_and_preserve_separate_domains():
    assert TOTAL == 100_000
    assert QUOTAS["agenda"] == 22_000
    assert QUOTAS["tenant_security"] == 8_000
    assert len(QUOTAS) == 11


def test_fixture_covers_many_business_models_without_reachable_phones():
    assert len(COMPANY_TYPES) == 16
    profiles = [company_profile(i, random.Random(10 + i)) for i in range(64)]
    assert all(p["synthetic"] and p["professionals"] for p in profiles)
    assert len({p["tenant_id"] for p in profiles}) == 64
    assert all(1 <= len(p["professionals"]) <= 10 for p in profiles)


def test_peixoto_grid_40_minutes_no_overlap():
    s = booking_state()
    assert bookable(s)  # 09:00 to 09:40
    s["start_min"] = 580  # 09:40 to 10:20
    assert bookable(s)
    s["start_min"] = 540
    s["duration_min"] = 60  # 09:00 to 10:00
    assert bookable(s)
    s["existing"] = True
    s["existing_interval"] = [540, 600]
    s["start_min"] = 580
    s["duration_min"] = 40
    assert not bookable(s)  # a second reservation at 09:40 must be denied
    s["start_min"] = 600
    assert bookable(s)


def test_lunch_and_closing_boundaries():
    s = booking_state()
    s["start_min"], s["duration_min"] = 700, 40
    assert not bookable(s)
    s["start_min"], s["duration_min"] = 660, 60
    assert bookable(s)  # ends exactly at lunch
    s["start_min"], s["duration_min"] = 1050, 45
    assert not bookable(s)
    s["start_min"], s["duration_min"] = 1040, 40
    assert bookable(s)


def test_manual_override_does_not_ignore_existing_appointment():
    s = booking_state()
    s.update({"start_min": 1170, "manual_override": True,
              "existing": True, "existing_interval": [1140, 1200]})
    assert not bookable(s)
    s["existing"] = False
    assert bookable(s)


def test_block_respects_partial_interval():
    s = booking_state()
    s["blocked"] = True
    s["block_interval"] = [600, 650]
    assert bookable(s)
    s["start_min"] = 580
    assert not bookable(s)


def test_day_off_requires_explicit_manual_override():
    s = booking_state()
    s.update({"weekday": 6})
    assert not bookable(s)
    s["manual_override"] = True
    assert bookable(s)


@pytest.mark.parametrize("duration", DURATIONS)
def test_supported_duration_is_accounted_for(duration):
    s = booking_state()
    s["duration_min"] = duration
    assert bookable(s) is (duration <= 180 and
                            not (540 < 780 and 720 < 540 + duration))


def test_percentage_commission_and_cents_rounding():
    s = {"service_cents": 4000, "product_cents": 0,
         "discount_cents": 0, "commission_bp": 4000}
    assert money_result(s) == {
        "total_cents": 4000, "service_commission_cents": 1600,
        "commission_base_cents": 4000,
    }
    s.update({"service_cents": 6500, "product_cents": 2000, "discount_cents": 1000})
    assert money_result(s) == {
        "total_cents": 7500, "service_commission_cents": 2200,
        "commission_base_cents": 5500,
    }


def test_fixtures_exclude_customer_identity_from_semantic_signature():
    rng = random.Random(42)
    a = make_scenario("agenda", 1, rng, tenants=1000)
    assert a["synthetic"] is True
    assert not a["executed_against_orion"]
    assert a["expected"]["send_real_messages"] is False
    assert a["semantic_signature"] == digest({"area": a["area"], "state": a["state"]})
    assert "example.invalid" not in a["message"]
    assert "tenant_id" not in a["message"]


def test_deterministic_semantic_variations_do_not_only_change_names():
    first = make_scenario("agenda", 4, random.Random(14), 2048)
    second = make_scenario("agenda", 4, random.Random(14), 2048)
    third = make_scenario("agenda", 5, random.Random(15), 2048)
    assert first == second
    assert first["state"] != third["state"]
    assert first["semantic_signature"] != third["semantic_signature"]


def test_intent_messages_carry_real_service_and_time():
    rng = random.Random(21)
    case = make_scenario("agenda", 2, rng, tenants=2048)
    assert case["expected_logical_operation"] == OPERATION["agenda"]
    assert case["message"]
    assert case["state"]["service_names"][0].replace("_", " ") in case["message"]


def test_10_multistep_journey_styles():
    journeys = [make_journey(i, random.Random(i), 2048) for i in range(10)]
    assert len({j["style"] for j in journeys}) == 10
    assert all(len(j["steps"]) >= 5 for j in journeys)
    assert all(not j["executed_against_orion"] for j in journeys)


def test_jsonl_gzip_roundtrip_preserves_utf8(tmp_path):
    rows = [{"mensagem": "Quero horário amanhã", "id": ident("test", i)}
            for i in range(3)]
    p = tmp_path / "fixture.jsonl.gz"
    write_jsonl_gz(p, rows)
    assert list(read_jsonl_gz(p)) == rows
    assert json.loads(json.dumps(rows, ensure_ascii=False)) == rows


def test_schema_does_not_presume_paid_api_calls():
    s = make_scenario("whatsapp_ai_notifications", 12, random.Random(30), 2048)
    assert s["oracle"] == "synthetic_reference_only"
    assert s["expected"]["external_write_allowed"] is False
    assert "ai_conversations_executed" not in s or s["ai_conversations_executed"] == 0