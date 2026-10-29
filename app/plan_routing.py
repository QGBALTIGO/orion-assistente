"""Attach the central plan dependency to the existing tenant routes.

Membership authentication remains authoritative. Public booking actions are
checked in the booking engine, not authenticated as a business account here.
"""
from uuid import UUID

from fastapi import Depends, HTTPException, Request
from fastapi.routing import APIRoute

from app.catalog_api import Actor, authenticate
from app.plan_permissions import bind_feature, reset_feature, require_tenant_feature

TENANT_PREFIX = "/api/v1/tenants/{tenant_id}"


def feature_for_path(relative, method="GET", *, resource=None, step_id=None):
    """Explicit classification; an unclassified future tenant API fails closed."""
    if relative in {"/management/permissions", "/management/plan-overview", "/management/subscription",
                    "/management/plan-trial", "/management/plan-checkout"} or relative.startswith("/management/plan-direct-"):
        return "plan.manage"
    if relative.startswith("/onboarding"):
        return "business.setup"
    if relative in {"/{resource}", "/{resource}/{item_id}"}:
        if resource is None:
            return "workspace.access"
        return {"professionals": "professionals.manage", "services": "services.manage",
            "professional_services": "services.manage", "working_hours": "agenda.manage",
            "working_breaks": "agenda.manage", "time_off": "agenda.manage",
            "schedule_blocks": "agenda.manage", "authorized_staff": "settings.manage"}.get(resource)
    if relative in {"/services"}:
        return "services.manage"
    if relative.startswith(("/availability", "/appointments", "/export/appointments", "/management/appointments", "/management/block-series", "/management/booking-selection")):
        return "agenda.manage"
    if relative.startswith("/commerce"):
        return "commerce.admin"
    if relative.startswith("/google"):
        return "calendar.manage"
    if relative.startswith("/business/memberships"):
        return "settings.manage"
    if relative.startswith("/business/profile"):
        return "site.manage" if relative.endswith(("/publish", "/rollback")) else "business.setup"
    if relative == "/audit":
        return "account.manage"
    if relative.startswith(("/ai/", "/workflows/")):
        return "reception.ai"
    if relative.startswith("/campaigns"):
        return "marketing.campaigns"
    if relative.startswith("/crm/consent"):
        return "account.manage"
    if relative.startswith("/inbox/"):
        return "conversations.manual"
    if relative.startswith("/qr-channels"):
        return "conversations.manual"
    if relative.startswith("/appointment-communications"):
        return "notifications.basic"
    if relative.startswith("/appointment-reviews"):
        return "notifications.basic"
    if relative == "/media":
        return "site.manage"
    if relative.startswith("/management/"):
        suffix = relative[len("/management/"):]
        if suffix.startswith("dashboard"):
            return "dashboard.basic"
        if suffix.startswith("customers"):
            return "clients.manage"
        if suffix.startswith("professionals/commission-rules"):
            return "commissions.advanced"
        if suffix.startswith("professionals"):
            return "professionals.manage"
        if suffix.startswith("finance/") and suffix.split("/")[1] in {"commissions", "dre", "settlements", "advances"}:
            return "finance.advanced" if suffix.startswith("finance/dre") else "commissions.advanced"
        if suffix.startswith("finance"):
            return "finance.basic"
        if suffix.startswith("goals"):
            return "goals.manage"
        if suffix.startswith("performance"):
            return "performance.advanced"
        if suffix.startswith("loyalty/coupons"):
            return "coupons.manage"
        if suffix.startswith("loyalty"):
            return "loyalty.manage"
        if suffix.startswith("ai/"):
            return "marketing.ai" if suffix.endswith("generate-campaign") else "consultant.ai"
        if suffix.startswith("marketing/") and (suffix.endswith(("/reconcile", "/pause")) or
                suffix in {"marketing/consent", "marketing/contact-pause"}):
            return "account.manage"  # Stop/reconcile existing work without executing it.
        if suffix.startswith(("marketing/automations", "marketing/jobs", "marketing/queue")) or suffix == "marketing/execution":
            return "notifications.basic"  # Mixed fields/events have domain guards.
        if suffix.startswith("marketing"):
            return "marketing.campaigns"
        if suffix.startswith("site"):
            return "site.manage"
        if suffix.startswith(("service-packages", "services/")):
            return "services.manage"
        if suffix.startswith("team"):
            return "settings.manage"
        if suffix == "preferences":
            return "business.setup"  # Mixed advanced fields have domain guards.
        if suffix.startswith("notifications"):
            return "account.manage"
        if suffix.startswith("inbox/"):
            return "conversations.manual"
        if suffix.startswith("communication/"):
            return "notifications.basic"
        if suffix.startswith("workflows"):
            return "reception.automation"
    return None


def exclusive_professional_operation(relative, method, params):
    if method in {"GET", "HEAD", "OPTIONS"}:
        return False
    return (relative.startswith("/management/professionals") or
            relative in {"/management/plan-trial", "/management/plan-checkout"} or
            relative.startswith("/management/plan-direct-") or
            relative.startswith("/onboarding/complete") or
            relative.startswith("/onboarding/steps/") and params.get("step_id") == "professionals" or
            params.get("resource") == "professionals")


async def enforce_endpoint_plan(request: Request, tenant_id: UUID, actor: Actor = Depends(authenticate)):
    route = request.scope.get("route")
    path = getattr(route, "path", None) or getattr(route, "path_format", "")
    # FastAPI's included-route context and ordinary APIRoute both expose the
    # resolved path. Fall back to the already matched literal request path.
    relative = path.split(TENANT_PREFIX, 1)[-1] if TENANT_PREFIX in path else "/" + request.url.path.partition("/tenants/")[2].partition("/")[2]
    template = "/{resource}/{item_id}" if "item_id" in request.path_params and "resource" in request.path_params else "/{resource}" if "resource" in request.path_params else relative
    feature = feature_for_path(template, request.method, resource=request.path_params.get("resource"), step_id=request.path_params.get("step_id"))
    from app.catalog_api import authorize
    # Other endpoints also recheck publication/membership/owner rules. This
    # prevents disclosing plan state for a tenant not owned by this request.
    authorize(actor, tenant_id, write=False)
    if feature is None:
        from app.catalog_schemas import RESOURCE_MODELS
        if template in {"/{resource}", "/{resource}/{item_id}"} and request.path_params.get("resource") not in RESOURCE_MODELS:
            raise HTTPException(404, "Recurso não encontrado")
        raise HTTPException(403, "Recurso indisponível para este plano")
    bypass = actor.role in {"admin", "support"} and not getattr(actor, "maintenance", False)
    token = bind_feature(tenant_id, feature,
        exclusive=exclusive_professional_operation(relative, request.method, request.path_params), bypass=bypass)
    try:
        if not bypass:
            await require_tenant_feature(tenant_id, feature)
        yield
    finally:
        reset_feature(token)


def include_plan_router(app, router):
    """Use the router's normal dependency interface before including it.

    FastAPI compiles inherited dependencies when including/using these routes.
    No middleware, alternative authentication or endpoint duplication is added.
    """
    for route in router.routes:
        if isinstance(route, APIRoute) and route.path.startswith(TENANT_PREFIX):
            relative = route.path[len(TENANT_PREFIX):]
            if feature_for_path(relative) is None:
                raise RuntimeError("Tenant route requires an explicit plan policy: " + route.path)
            if not any(dep.dependency is enforce_endpoint_plan for dep in route.dependencies):
                route.dependencies.append(Depends(enforce_endpoint_plan))
    app.include_router(router)
