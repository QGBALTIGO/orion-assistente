"""Eligibility for upgrading an administrator-granted Orion plan.

This module has no writes, network calls or entitlement side effects. It only
answers whether the verified owner can START a higher-tier Pix checkout.
The caller must obtain a fresh server-side plan snapshot under the tenant lock.
A positive result is NOT proof of payment and MUST NOT grant paid features.
Paid-to-paid changes, prorating, and recurring subscriptions are out of scope.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

PLAN_ORDER = ("essencial", "profissional", "elite")


@dataclass(frozen=True)
class ManualUpgradeDecision:
    allowed: bool
    reason: str
    current_plan: str | None
    target_plan: str | None
    payment_required: bool
    preserve_current_until_paid: bool = True


def evaluate_manual_upgrade(
    effective_plan: Mapping[str, object] | None,
    target_plan: str,
    *,
    requester_is_owner: bool,
) -> ManualUpgradeDecision:
    """Check a trusted snapshot without changing current access or charging.

    ``requester_is_owner`` must come from current authenticated membership,
    never request JSON. ``effective_plan`` must come from ``plan_state``.
    Unknown state, inactive access and existing recurring contracts fail closed.
    """
    current = None
    target = target_plan if isinstance(target_plan, str) and target_plan in PLAN_ORDER else None

    def deny(reason: str) -> ManualUpgradeDecision:
        return ManualUpgradeDecision(False, reason, current, target, False)

    if requester_is_owner is not True:
        return deny("OWNER_REQUIRED")
    if target is None:
        return deny("UNKNOWN_TARGET_PLAN")
    if not isinstance(effective_plan, Mapping):
        return deny("INVALID_PLAN_STATE")
    code = effective_plan.get("code")
    current = code if isinstance(code, str) and code in PLAN_ORDER else None
    if effective_plan.get("platform_workspace") is True or effective_plan.get("source") == "platform_admin":
        return deny("PLATFORM_WORKSPACE")
    if effective_plan.get("active") is not True or effective_plan.get("status") != "active":
        return deny("INACTIVE_PLAN")
    # This first delivery only covers administrator-granted plans. Existing
    # paid periods must not be discarded or converted by this eligibility rule.
    if effective_plan.get("source") != "manual":
        return deny("NOT_A_MANUAL_PLAN")
    manual = effective_plan.get("manual_assignment")
    if (
        current is None
        or not isinstance(manual, Mapping)
        or manual.get("active") is not True
        or manual.get("plan_code") != current
    ):
        return deny("INVALID_MANUAL_GRANT")
    if effective_plan.get("commercial_subscription") is not None:
        return deny("EXISTING_COMMERCIAL_SUBSCRIPTION")
    if target == current:
        return deny("ALREADY_ON_PLAN")
    if PLAN_ORDER.index(target) < PLAN_ORDER.index(current):
        return deny("DOWNGRADE_NOT_SUPPORTED")
    return ManualUpgradeDecision(True, "MANUAL_UPGRADE_ALLOWED", current, target, True)
