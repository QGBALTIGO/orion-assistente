"""Group additions require a subscription company without opening message APIs."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.plan_catalog import ALL_FEATURES, PLAN_FEATURES, RECOVERY_FEATURES
from app.plan_permissions import derive_plan
from app.plan_routing import feature_for_path, subscription_workspace_route

NOW = datetime(2026, 11, 5, 15, tzinfo=timezone.utc)
FEATURE = "group_add.manage"
BASE = "/subscription-whatsapp/group-add"


@pytest.mark.parametrize("status,allowed", [
    ("active", True), ("paused", False), ("suspended", False), ("inactive", False),
])
def test_group_add_access_follows_active_subscription_company(status, allowed):
    plan = derive_plan({"tenant_id": uuid4(), "niche": "servico_assinaturas",
                       "tenant_status": status}, NOW)
    assert FEATURE in ALL_FEATURES
    assert plan["permissions"][FEATURE] is allowed
    assert plan["permissions"]["conversations.manual"] is False
    assert plan["permissions"]["marketing.campaigns"] is False
    assert plan["permissions"]["reception.ai"] is False
    if not allowed:
        assert set(plan["features"]) == {"account.manage", "business.setup"}


@pytest.mark.parametrize("niche", [None, "barbearia", "clinica", "odontologia", "outro"])
@pytest.mark.parametrize("code", ["essencial", "profissional", "elite"])
def test_group_add_feature_does_not_expand_other_niches(niche, code):
    plan = derive_plan({"tenant_id": uuid4(), "niche": niche, "tenant_status": "active",
                       "manual": {"plan_code": code, "started_at": NOW-timedelta(days=1),
                                  "expires_at": None}}, NOW)
    assert plan["permissions"][FEATURE] is False
    assert set(plan["features"]) == PLAN_FEATURES[code] | RECOVERY_FEATURES


@pytest.mark.parametrize("suffix", [
    "", "/options", "/groups", "/preview", "/batches", "/requests/{key}",
    "/batches/{batch_id}",
])
def test_group_add_routes_are_classified_independently_of_bulk_and_inbox(suffix):
    for method in ("GET", "POST", "PATCH", "DELETE"):
        assert feature_for_path(BASE + suffix, method) == FEATURE
        assert subscription_workspace_route(BASE + suffix, method, {})
    assert feature_for_path("/subscription-whatsapp/bulk-messages" + suffix) == "bulk_messages.manage"


@pytest.mark.parametrize("suffix", ["-legacy", "_old", "extra", ".json"])
def test_similarly_named_future_resource_cannot_inherit_group_add_access(suffix):
    assert feature_for_path(BASE + suffix) is None
    assert not subscription_workspace_route(BASE + suffix, "GET", {})


@pytest.mark.parametrize("path", [
    "/management/inbox/conversations", "/inbox/conversations", "/qr-channels",
    "/workflows/{id}", "/management/marketing/campaigns",
])
def test_group_add_does_not_open_legacy_operating_routes(path):
    assert feature_for_path(path) != FEATURE
    assert not subscription_workspace_route(path, "POST", {})


def test_registered_group_add_api_has_a_plan_dependency_for_every_tenant_operation():
    from app.main import app
    from app.plan_routing import enforce_endpoint_plan
    from app.subscription_group_add import router
    prefix = "/api/v1/tenants/{tenant_id}" + BASE
    # The application's public schema resolves included routers even when the
    # installed FastAPI version defers expansion of app.routes.
    assert {path[len(prefix):] for path in app.openapi()["paths"] if path.startswith(prefix)} >= {
        "/options", "/groups", "/preview", "/batches", "/requests/{key}",
        "/batches/{batch_id}",
    }
    routes = [route for route in router.routes if getattr(route, "path", "").startswith(prefix)]
    assert routes
    for route in routes:
        assert any(dependency.dependency is enforce_endpoint_plan for dependency in route.dependencies), route.path
        assert feature_for_path(route.path[len("/api/v1/tenants/{tenant_id}"):]) == FEATURE
