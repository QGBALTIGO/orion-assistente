"""Four-question company profile: strict bands, fast setup, strict operational gates."""
from copy import deepcopy
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app import onboarding_schemas as schemas, onboarding_service as service


PROFILE = {
    "operating_age": "under_6_months",
    "team_size": "1_2",
    "monthly_revenue": "50k_100k",
    "monthly_volume": "51_100",
}
CONFIG = {"niche": "barbearia", "activity": "services", "modality": "online"}


def test_profile_uses_new_versioned_step_without_removing_historical_catalog_api():
    assert schemas.STEPS["profile"] is schemas.BusinessMetrics
    assert schemas.STEPS["catalog"] is schemas.Catalog
    assert service.LABELS["profile"] == "Perfil da empresa"
    assert service.LEGACY_LABELS["catalog"] == "Serviços e produtos"


def test_all_four_estimates_are_required_and_enumerated():
    assert schemas.BusinessMetrics.model_validate(PROFILE).model_dump() == PROFILE
    for key in PROFILE:
        missing = dict(PROFILE)
        missing.pop(key)
        with pytest.raises(ValidationError):
            schemas.BusinessMetrics.model_validate(missing)
        invalid = dict(PROFILE)
        invalid[key] = "100000000"
        with pytest.raises(ValidationError):
            schemas.BusinessMetrics.model_validate(invalid)


def test_profile_bands_cover_missing_revenue_bracket_and_privacy_choice():
    for revenue in ("none", "under_5k", "5k_10k", "10k_25k", "25k_50k",
                    "50k_100k", "over_100k", "prefer_not_to_say"):
        assert schemas.BusinessMetrics.model_validate(
            {**PROFILE, "monthly_revenue": revenue}
        ).monthly_revenue == revenue
    with pytest.raises(ValidationError):
        schemas.BusinessMetrics.model_validate({**PROFILE, "tenant_id": str(uuid4())})


def test_quick_setup_does_not_need_made_up_services_staff_or_revenue():
    config = {**CONFIG, "business_metrics": {**PROFILE, "operating_age": "not_started",
                                             "team_size": "none", "monthly_revenue": "none",
                                             "monthly_volume": "none"}}
    facts = {key: [] for key in (
        "professionals", "services", "links", "hours", "breaks", "products", "variants"
    )}
    assert service.readiness(config, facts, profile_only=True) == []
    strict = {row["code"] for row in service.readiness(config, facts)}
    assert "professional" in strict
    assert "service" in strict
    assert "scheduling_bindings" in strict


def test_missing_or_malformed_profile_blocks_quick_completion():
    config = dict(CONFIG)
    facts = {key: [] for key in (
        "professionals", "services", "links", "hours", "breaks", "products", "variants"
    )}
    assert {row["step"] for row in service.readiness(config, facts, profile_only=True)} == {"profile"}
    config["business_metrics"] = {"operating_age": "not_started"}
    assert "business_metrics" in {row["code"] for row in service.readiness(config, facts, profile_only=True)}
    config["business_metrics"] = PROFILE
    assert service.readiness(config, facts, profile_only=True) == []


@pytest.mark.asyncio
async def test_profile_save_changes_only_private_configuration_without_any_catalog_write():
    class NoDatabase:
        def __getattr__(self, attr):
            raise AssertionError("profile save must not access DB attribute " + attr)

    meta = {
        "configuration": {**CONFIG, "custom_field": "preserve", "reviewed": False},
        "managed_ids": {"services": {"prior-service": True}},
        "current_step": "profile", "version": 5,
    }
    before = deepcopy(meta)
    value = schemas.BusinessMetrics.model_validate(PROFILE)
    result = await service._apply_step(NoDatabase(), uuid4(), meta, "profile", value, None)
    assert result == {}
    assert meta["configuration"]["business_metrics"] == PROFILE
    assert meta["configuration"]["custom_field"] == before["configuration"]["custom_field"]
    assert meta["managed_ids"] == before["managed_ids"]
    assert meta["version"] == 5

def test_completed_legacy_onboarding_is_not_forced_to_answer_new_survey():
    old = {"is_new": True, "completed_at": "historical", "configuration": dict(CONFIG)}
    assert service._uses_quick_profile(old) is False
    pending = {"is_new": True, "completed_at": None, "configuration": dict(CONFIG)}
    assert service._uses_quick_profile(pending) is True
    finished_new = {"is_new": True, "completed_at": "finished",
                    "configuration": {**CONFIG, "business_metrics": PROFILE}}
    assert service._uses_quick_profile(finished_new) is True
    legacy = {"is_new": False, "completed_at": None, "configuration": dict(CONFIG)}
    assert service._uses_quick_profile(legacy) is False
