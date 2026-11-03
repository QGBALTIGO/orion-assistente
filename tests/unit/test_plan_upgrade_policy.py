"""Synthetic upgrade rules: no tenant/customer/provider changes."""
from copy import deepcopy
from dataclasses import FrozenInstanceError

import pytest

from app.plan_upgrade_policy import evaluate_manual_upgrade


def state(code="essencial", **changes):
    return {
        "code": code,
        "source": "manual",
        "status": "active",
        "active": True,
        "platform_workspace": False,
        "manual_assignment": {"plan_code": code, "active": True},
        "commercial_subscription": None,
        **changes,
    }


@pytest.mark.parametrize("current,target", [
    ("essencial", "profissional"), ("essencial", "elite"),
    ("profissional", "elite"),
])
def test_owner_can_request_higher_tier_without_losing_current_access(current, target):
    snapshot = state(current)
    original = deepcopy(snapshot)
    result = evaluate_manual_upgrade(snapshot, target, requester_is_owner=True)
    assert result.allowed and result.payment_required
    assert result.preserve_current_until_paid is True
    assert result.reason == "MANUAL_UPGRADE_ALLOWED"
    assert (result.current_plan, result.target_plan) == (current, target)
    assert snapshot == original


@pytest.mark.parametrize("code", ["essencial", "profissional", "elite"])
def test_same_plan_never_creates_duplicate_checkout(code):
    result = evaluate_manual_upgrade(state(code), code, requester_is_owner=True)
    assert not result.allowed and not result.payment_required
    assert result.reason == "ALREADY_ON_PLAN"


@pytest.mark.parametrize("current,target", [
    ("elite", "profissional"), ("elite", "essencial"),
    ("profissional", "essencial"),
])
def test_downgrade_is_not_accidentally_authorized(current, target):
    result = evaluate_manual_upgrade(state(current), target, requester_is_owner=True)
    assert not result.allowed and result.reason == "DOWNGRADE_NOT_SUPPORTED"


@pytest.mark.parametrize("target", [None, "", "admin", "pro", "ELITE", 1, {}, []])
def test_unrecognized_target_fails_closed(target):
    result = evaluate_manual_upgrade(state(), target, requester_is_owner=True)
    assert not result.allowed and result.reason == "UNKNOWN_TARGET_PLAN"


@pytest.mark.parametrize("owner", [False, None, 1, "true"])
def test_only_explicit_verified_owner_can_start_upgrade(owner):
    result = evaluate_manual_upgrade(state(), "elite", requester_is_owner=owner)
    assert not result.allowed and result.reason == "OWNER_REQUIRED"


@pytest.mark.parametrize("changes", [
    {"active": False}, {"active": "true"}, {"status": "tenant_inactive"},
    {"status": "expired"}, {"status": "suspended"},
    {"source": "pix_prepaid"}, {"source": "cakto"}, {"source": "trial"},
    {"source": "platform_admin"}, {"platform_workspace": True},
    {"manual_assignment": None}, {"manual_assignment": {}},
    {"manual_assignment": {"plan_code": "elite", "active": True}},
    {"manual_assignment": {"plan_code": "essencial", "active": False}},
    {"code": "unrecognized"},
    {"commercial_subscription": {}},
    {"commercial_subscription": {"status": "active"}},
    {"commercial_subscription": {"status": "canceled"}},
])
def test_missing_or_conflicting_billing_state_cannot_start_checkout(changes):
    snapshot = state(**changes)
    original = deepcopy(snapshot)
    result = evaluate_manual_upgrade(snapshot, "elite", requester_is_owner=True)
    assert not result.allowed and not result.payment_required
    assert result.preserve_current_until_paid
    assert snapshot == original


@pytest.mark.parametrize("snapshot", [None, [], "active"])
def test_invalid_snapshot_fails_closed(snapshot):
    result = evaluate_manual_upgrade(snapshot, "elite", requester_is_owner=True)
    assert result.reason == "INVALID_PLAN_STATE" and not result.allowed


def test_result_is_immutable_and_cannot_be_reinterpreted_as_payment_confirmation():
    result = evaluate_manual_upgrade(state(), "elite", requester_is_owner=True)
    with pytest.raises(FrozenInstanceError):
        result.payment_required = False
    assert result.payment_required is True
    assert result.preserve_current_until_paid is True
