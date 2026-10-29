"""Isolated browser grants follow the canonical entitlement DTO and catalogue."""
from datetime import datetime, timezone

import pytest

from app.plan_catalog import CODES
from app.plan_permissions import derive_plan
from scripts.plan_fixture import synthetic_plan_state


@pytest.mark.parametrize("code", [*CODES, None])
def test_synthetic_plan_matches_the_real_pure_resolver(code):
    tenant = "10000000-0000-4000-8000-000000000001"
    snapshot = {"tenant_id": tenant, "tenant_status": "active", "version": 1,
                "active_professionals": 0,
                "manual": {"plan_code": code, "started_at": "2020-01-01T00:00:00Z",
                           "expires_at": None}}
    if code is None:
        snapshot["manual"] = None
    assert synthetic_plan_state(tenant, code) == derive_plan(
        snapshot, now=datetime(2026, 10, 29, tzinfo=timezone.utc))


def test_unknown_fixture_plan_does_not_fall_back_to_access():
    with pytest.raises(KeyError):
        synthetic_plan_state("synthetic-tenant", "unknown")
