"""Self-reported business priorities: quick onboarding only, no channel side-effects."""
from copy import deepcopy
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app import onboarding_schemas as schemas, onboarding_service as service


PRIORITIES = {
    "main_challenge": "whatsapp",
    "desired_outcome": "automate_whatsapp",
    "respondent_role": "partner",
}
METRICS = {
    "operating_age": "under_6_months", "team_size": "1_2",
    "monthly_revenue": "prefer_not_to_say", "monthly_volume": "up_to_50",
}
BASE = {"niche": "barbearia", "activity": "services", "modality": "online",
        "business_metrics": METRICS}
FACTS = {key: [] for key in ("professionals", "services", "links", "hours",
                            "breaks", "products", "variants")}


def test_new_step_is_independent_from_existing_whatsapp_channels_contract():
    assert schemas.STEPS["goals"] is schemas.BusinessPriorities
    assert schemas.STEPS["channels"] is schemas.Channels
    assert service.LABELS["goals"] == "Desafios e objetivos"
    assert "goals" not in service.LEGACY_LABELS
    assert service.LEGACY_LABELS["channels"] == "Canais independentes"


@pytest.mark.parametrize("key", ("main_challenge", "desired_outcome", "respondent_role"))
def test_each_question_is_required_and_invalid_answers_do_not_validate(key):
    assert schemas.BusinessPriorities.model_validate(PRIORITIES).model_dump() == PRIORITIES
    without = dict(PRIORITIES)
    del without[key]
    with pytest.raises(ValidationError):
        schemas.BusinessPriorities.model_validate(without)
    with pytest.raises(ValidationError):
        schemas.BusinessPriorities.model_validate({**PRIORITIES, key: "injected-invalid-choice"})
    with pytest.raises(ValidationError):
        schemas.BusinessPriorities.model_validate({**PRIORITIES, "account_role": "admin"})


@pytest.mark.parametrize("challenge", ("scheduling", "cancellations", "whatsapp", "finance",
                                      "new_customers", "loyalty", "team"))
def test_all_business_difficulties_are_accepted(challenge):
    assert schemas.BusinessPriorities.model_validate(
        {**PRIORITIES, "main_challenge": challenge}).main_challenge == challenge


@pytest.mark.parametrize("outcome", ("save_time", "grow_revenue", "professional_service",
                                    "automate_whatsapp", "better_control", "more_bookings"))
def test_all_desired_results_are_accepted(outcome):
    assert schemas.BusinessPriorities.model_validate(
        {**PRIORITIES, "desired_outcome": outcome}).desired_outcome == outcome


@pytest.mark.parametrize("respondent_role", ("owner", "partner", "manager", "other"))
def test_role_is_self_reported_without_granting_any_account_permissions(respondent_role):
    assert schemas.BusinessPriorities.model_validate(
        {**PRIORITIES, "respondent_role": respondent_role}
    ).respondent_role == respondent_role


def test_only_new_unfinished_quick_setups_require_goals_before_completion():
    config = dict(BASE)
    assert service.readiness(config, FACTS, profile_only=True) == []
    pending = service.readiness(config, FACTS, profile_only=True, goals_required=True)
    assert len(pending) == 1
    assert pending[0]["step"] == "goals"
    assert pending[0]["code"] == "business_priorities"
    config["business_priorities"] = dict(PRIORITIES)
    assert service.readiness(config, FACTS, profile_only=True, goals_required=True) == []
    assert {p["code"] for p in service.readiness(config, FACTS, staged=False)} >= {
        "professional", "service", "scheduling_bindings"
    }


@pytest.mark.asyncio
async def test_saving_answers_touches_only_private_onboarding_configuration():
    class NoDatabase:
        def __getattr__(self, attr):
            raise AssertionError("Goals must not query the database for a channel or membership")

    meta = {
        "configuration": {**BASE, "custom_field": "keep", "reviewed": False},
        "managed_ids": {"services": {"historical-service": True}},
        "version": 5,
    }
    previous = deepcopy(meta)
    result = await service._apply_step(
        NoDatabase(), uuid4(), meta, "goals",
        schemas.BusinessPriorities.model_validate(PRIORITIES), None)
    assert result == {}
    assert meta["configuration"]["business_priorities"] == PRIORITIES
    assert meta["configuration"]["business_metrics"] == previous["configuration"]["business_metrics"]
    assert meta["configuration"]["custom_field"] == "keep"
    assert meta["managed_ids"] == previous["managed_ids"]
    assert meta["version"] == previous["version"]
    assert "account_role" not in meta["configuration"]
    assert "channels" not in meta["configuration"]
