"""Stage 18: explicit owner authorization, phone format and result validation.
All numbers and sessions in these tests are synthetic.
"""
from uuid import UUID

import pytest
from pydantic import ValidationError

from app import subscription_number_check_schemas as schemas
from app import plan_routing, plan_catalog, workspace_policy

DEVICE = UUID("00000000-0000-4000-8000-000000000018")
GEN = UUID("00000000-0000-4000-8000-000000000019")
PHONE = "+5567999999901"


def sample(**changes):
    return {"device_id": str(DEVICE), "phones": [PHONE],
            "confirmed_authorization": True} | changes


def test_correct_authorized_phone():
    value = schemas.CheckRequest.model_validate(sample())
    assert value.phones == [PHONE] and value.confirmed_authorization is True


@pytest.mark.parametrize("phones", [
    [], [PHONE] * 2, [PHONE] * 6, ["5567999999901"], ["+01234567890"],
    ["+595992820210", "not-a-phone"], ["+5567999999901\n+595992820210"]
])
def test_invalid_and_duplicate_phone_lists_refused(phones):
    with pytest.raises(ValidationError):
        schemas.CheckRequest.model_validate(sample(phones=phones))


@pytest.mark.parametrize("consent", [False, "true", 1, None])
def test_no_implicit_authorization(consent):
    with pytest.raises(ValidationError):
        schemas.CheckRequest.model_validate(sample(confirmed_authorization=consent))


def test_unknown_fields_denied():
    with pytest.raises(ValidationError):
        schemas.CheckRequest.model_validate(sample(internal_permission="ignore"))


def test_verified_bridge_schema_requires_exact_scope_and_only_three_statuses():
    result = {
        "device_id": str(DEVICE), "generation": str(GEN),
        "settings_revision": 2, "items": [{"phone": PHONE, "status": "registered"}]
    }
    assert schemas.BridgeResult.model_validate(result).items[0].status == "registered"
    for bad in [
        result | {"items": [{"phone": PHONE, "status": "fake"}]},
        result | {"items": [{"phone": PHONE, "status": "registered"}] * 2},
        result | {"items": [{"phone": "5567999999901", "status": "registered"}]},
        result | {"device_id": "not-a-uuid"},
    ]:
        with pytest.raises(ValidationError):
            schemas.BridgeResult.model_validate(bad)


def test_feature_only_in_subscription_and_fail_closed_path():
    name = "number_check.manage"
    assert name in plan_catalog.ALL_FEATURES
    assert name in workspace_policy.SUBSCRIPTION_WORKSPACE_FEATURES
    assert name not in plan_catalog.BASE_FEATURES
    assert name not in plan_catalog.ELITE_FEATURES
    assert plan_routing.feature_for_path("/subscription-whatsapp/number-check/check", "POST") == name
    assert plan_routing.subscription_workspace_route("/subscription-whatsapp/number-check/check", "POST", {}) is True
