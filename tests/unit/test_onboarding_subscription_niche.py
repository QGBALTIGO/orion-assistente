"""Subscription business classification without changing operational engines."""
from copy import deepcopy
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app import onboarding_schemas as schemas, onboarding_service as service


LEGACY = {
    "name": "Empresa sintética", "timezone": "America/Cuiaba",
    "niche": "barbearia", "activity": "services",
    "contact_phone": "+5567999999901",
}


def identity(offering="subscriptions", **changes):
    return {**LEGACY, "niche": "servico_assinaturas", "activity": "products",
            "subscription_offering": offering, **changes}


@pytest.mark.parametrize("offering", ["subscriptions", "one_time_sales", "both"])
def test_each_subscription_offering_is_distinct_and_uses_commerce_not_appointments(offering):
    value = schemas.Identity.model_validate(identity(offering))
    creation = schemas.CreateBusiness(**identity(offering), idempotency_key="synthetic-create")
    assert value.activity == creation.activity == "products"
    assert value.model_dump(mode="json")["subscription_offering"] == offering
    assert creation.model_dump(mode="json")["subscription_offering"] == offering


@pytest.mark.parametrize("changes", [
    {"subscription_offering": None}, {"subscription_offering": "unknown"},
    {"subscription_offering": True}, {"activity": "services"}, {"activity": "both"},
    {"niche": "barbearia"}, {"niche": "outro", "other_niche": "Negócio existente"},
])
def test_subscription_choices_cannot_be_missing_or_crossed_with_another_niche(changes):
    with pytest.raises(ValidationError):
        schemas.Identity.model_validate(identity(**changes))


def test_legacy_create_and_edit_digests_remain_identical_to_before_the_new_field():
    expected = {**LEGACY, "other_niche": None}
    value = schemas.Identity.model_validate(LEGACY)
    creation = schemas.CreateBusiness(**LEGACY, idempotency_key="legacy-business-create")
    assert value.model_dump(mode="json") == expected
    values = creation.model_dump(mode="json", exclude={"idempotency_key"})
    assert values == expected
    # Captured from the unmodified production schema before this addition.
    assert service.digest({"account": UUID(int=1), "data": values}) == (
        "69d87951e3dce29e37841619f64de89d81932d968864f81ad93fe7e626fcc08f")
    assert service.digest({"actor": UUID(int=1), "step": "business",
                           "data": value.model_dump(mode="json"), "supplied": LEGACY}) == (
        "a5b9db9f96b7f138e3c4528f4434af3dd62e65eb6cdd1c0d4146ece7488b89bb")


@pytest.mark.asyncio
async def test_business_save_preserves_other_configuration_and_clears_offering_when_leaving_niche():
    class IdentityOnlyDB:
        calls = 0

        async def execute(self, statement, params):
            assert str(statement) == "SELECT public.onboarding_update_identity(:t,:name,:tz)"
            self.calls += 1

    db = IdentityOnlyDB()
    meta = {"configuration": {"niche": "barbearia", "activity": "services",
                              "business_metrics": {"operating_age": "not_started"},
                              "custom_field": "preserve"},
            "managed_ids": {"services": {"historical-service": True}}, "version": 3}
    before = deepcopy(meta)
    await service._apply_step(db, uuid4(), meta, "business",
                              schemas.Identity.model_validate(identity("both")), None)
    assert meta["configuration"]["subscription_offering"] == "both"
    assert meta["configuration"]["activity"] == "products"
    await service._apply_step(db, uuid4(), meta, "business",
                              schemas.Identity.model_validate(LEGACY), None)
    assert "subscription_offering" not in meta["configuration"]
    assert meta["configuration"]["activity"] == "services"
    assert meta["configuration"]["business_metrics"] == before["configuration"]["business_metrics"]
    assert meta["configuration"]["custom_field"] == "preserve"
    assert meta["managed_ids"] == before["managed_ids"] and meta["version"] == 3
    assert db.calls == 2


@pytest.mark.parametrize("offering", [None, "invalid", ["subscriptions"]])
def test_incomplete_saved_subscription_choice_remains_pending(offering):
    config = {"niche": "servico_assinaturas", "activity": "products",
              "subscription_offering": offering, "modality": "online",
              "business_metrics": {"operating_age": "not_started", "team_size": "none",
                                   "monthly_revenue": "none", "monthly_volume": "none"}}
    pending = service.readiness(config, {}, profile_only=True)
    assert [(row["step"], row["code"]) for row in pending] == [
        ("business", "subscription_offering")]
