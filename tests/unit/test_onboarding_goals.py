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

def test_quick_release_is_in_existing_predeploy_chain_and_rechecks_current_schema(monkeypatch):
    """The production command dispatches 0041 without re-running old DDL."""
    from scripts import backup_and_migrate_registration as registration
    from scripts import backup_and_migrate_professional_phone as contacts
    from scripts import backup_and_migrate_quick_onboarding as quick
    from scripts import backup_and_migrate_admin_accounts as accounts
    from scripts import backup_and_migrate_single_whatsapp as single
    from scripts import backup_and_migrate_cakto as cakto
    from contextlib import contextmanager

    calls=[]
    original_main=quick.main
    monkeypatch.setattr(registration,"connection_values",lambda:("synthetic",object()))
    monkeypatch.setattr(registration,"migrated_version",lambda _:quick.TARGET)
    monkeypatch.setattr(registration,"main",lambda:calls.append("old-registration") or 0)
    monkeypatch.setattr(contacts,"main",lambda:calls.append("old-phone") or 0)
    monkeypatch.setattr(quick,"main",lambda:calls.append("quick") or 0)
    monkeypatch.setattr(accounts,"main",lambda:calls.append("admin-accounts") or 0)
    monkeypatch.setattr(single,"main",lambda:calls.append("single-whatsapp") or 0)
    monkeypatch.setattr(cakto,"main",lambda:calls.append("cakto") or 0)
    from scripts import backup_and_migrate_plans as plans, backup_and_migrate_direct_checkout as direct
    from scripts import backup_and_migrate_appointment_communications as appointment, backup_and_migrate_plan_permissions as permissions
    monkeypatch.setenv('ORION_RUN_APPOINTMENT_COMMUNICATION_MIGRATION','1')
    for module,label in ((plans,'plans'),(direct,'direct'),(appointment,'appointment'),(permissions,'permissions')):
        monkeypatch.setattr(module,'main',lambda label=label:calls.append(label) or 0)
    assert registration.predeploy_main()==0
    assert calls==["quick","admin-accounts","cakto","single-whatsapp","plans","direct","appointment","permissions"]

    monkeypatch.setattr(quick,"connection_values",lambda:("synthetic",object()))
    monkeypatch.setattr(quick,"migrated_version",lambda _:quick.TARGET)
    @contextmanager
    def locked(_):
        yield
    monkeypatch.setattr(quick,"release_lock",locked)
    monkeypatch.setattr(quick,"schema_contract_ready",lambda _:True)
    monkeypatch.setattr(quick,"perform_backup",lambda *a,**k:pytest.fail("Unnecessary backup"))
    monkeypatch.setattr(quick.subprocess,"run",lambda *a,**k:pytest.fail("Unexpected DDL"))
    assert original_main()==0


def test_quick_release_refuses_unknown_schema_before_backup_or_migration(monkeypatch):
    from scripts import backup_and_migrate_quick_onboarding as quick
    from contextlib import contextmanager

    monkeypatch.setattr(quick,"connection_values",lambda:("synthetic",object()))
    monkeypatch.setattr(quick,"migrated_version",lambda _:"0042_unreviewed")
    @contextmanager
    def locked(_):
        yield
    monkeypatch.setattr(quick,"release_lock",locked)
    monkeypatch.setattr(quick,"perform_backup",lambda *a,**k:pytest.fail("Unreviewed backup"))
    monkeypatch.setattr(quick.subprocess,"run",lambda *a,**k:pytest.fail("Unreviewed DDL"))
    assert quick.main()==2
