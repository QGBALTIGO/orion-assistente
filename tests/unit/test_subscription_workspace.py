"""The subscription business shell never inherits an operating plan's features."""
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

from fastapi import APIRouter, FastAPI, HTTPException
import httpx
import pytest

from app import catalog_api, onboarding_service, plan_management, plan_permissions, plan_routing
from app.plan_catalog import ALL_FEATURES, PLAN_FEATURES, RECOVERY_FEATURES
from app.workspace_policy import SUBSCRIPTION_WORKSPACE_FEATURES

NOW = datetime(2026, 11, 4, tzinfo=timezone.utc)


def snapshot(niche="servico_assinaturas", grant="none", **changes):
    base = {"tenant_id": str(uuid4()), "tenant_status": "active", "niche": niche,
            "active_professionals": 2, "version": 3}
    if grant in {"essencial", "profissional", "elite"}:
        base["manual"] = {"plan_code": grant, "started_at": NOW-timedelta(days=1),
                          "expires_at": None}
    elif grant == "trial":
        base["trial"] = {"status": "active", "started_at": NOW-timedelta(days=1),
                         "ends_at": NOW+timedelta(days=2)}
    elif grant == "pix":
        base["prepaid"] = {"plan_code": "elite", "last_verified_payment_at": NOW-timedelta(hours=1),
                           "paid_until": NOW+timedelta(days=30)}
    elif grant == "cakto":
        base["commercial"] = {"code": "orion_elite", "status": "active", "cakto_bound": True,
                              "last_verified_payment_at": NOW-timedelta(hours=1)}
    return {**base, **changes}


@pytest.mark.parametrize("grant", ["none", "essencial", "profissional", "elite", "trial", "pix", "cakto"])
def test_subscription_profile_masks_every_operational_feature_without_changing_billing(grant):
    source = snapshot(grant=grant)
    before = deepcopy(source)
    restricted = plan_permissions.derive_plan(source, NOW)
    standard = plan_permissions.derive_plan({**source, "niche": "barbearia"}, NOW)
    assert source == before
    assert restricted["workspace_profile"] == "subscription_services"
    assert set(restricted["features"]) == SUBSCRIPTION_WORKSPACE_FEATURES
    assert restricted["permissions"] == {
        feature: feature in SUBSCRIPTION_WORKSPACE_FEATURES for feature in sorted(ALL_FEATURES)}
    for field in ("source", "code", "active", "name", "valid_until", "manual_assignment",
                  "commercial_subscription", "trial", "limits"):
        assert restricted[field] == standard[field], field
    assert restricted["revision"] != standard["revision"]


@pytest.mark.parametrize("niche", [None, "barbearia", "clinica", "odontologia", "outro", "legacy"])
@pytest.mark.parametrize("grant", ["none", "essencial", "profissional", "elite"])
def test_other_niches_keep_the_same_plan_feature_matrix(niche, grant):
    state = plan_permissions.derive_plan(snapshot(niche, grant), NOW)
    expected = PLAN_FEATURES.get(grant, frozenset()) | RECOVERY_FEATURES
    assert state["workspace_profile"] == "standard"
    assert set(state["features"]) == expected


def test_platform_workspace_keeps_its_existing_authority_even_if_profile_niche_matches():
    state = plan_permissions.derive_plan(snapshot(platform_workspace=True), NOW)
    assert state["workspace_profile"] == "standard"
    assert set(state["features"]) == ALL_FEATURES
    assert state["source"] == "platform_admin"


@pytest.mark.parametrize("grant", ["none", "elite"])
def test_whatsapp_guidance_is_available_for_active_subscription_company(grant):
    state = plan_permissions.derive_plan(snapshot(grant=grant), NOW)
    assert state["workspace_profile"] == "subscription_services"
    # Commercial entitlement activity is distinct from the active company.
    assert state["active"] is (grant != "none")
    assert state["permissions"]["whatsapp_guidance.view"] is True
    assert state["permissions"]["plan.manage"] is False


@pytest.mark.parametrize("status", ["paused", "suspended", "canceled"])
def test_whatsapp_guidance_closes_when_subscription_company_is_inactive(status):
    state = plan_permissions.derive_plan(snapshot(grant="elite", tenant_status=status), NOW)
    assert state["permissions"]["whatsapp_guidance.view"] is False
    assert set(state["features"]) == {"account.manage", "business.setup"}


@pytest.mark.parametrize("grant", ["none", "essencial", "profissional", "elite"])
def test_standard_commercial_plan_does_not_grant_whatsapp_guidance(grant):
    state = plan_permissions.derive_plan(snapshot(niche="barbearia", grant=grant), NOW)
    assert state["workspace_profile"] == "standard"
    assert state["permissions"]["whatsapp_guidance.view"] is False
    assert set(state["features"]) == PLAN_FEATURES.get(grant, frozenset()) | RECOVERY_FEATURES


@pytest.mark.asyncio
@pytest.mark.parametrize("is_new", [False, True])
async def test_operational_readiness_cannot_skip_subscription_niche_for_legacy_businesses(monkeypatch, is_new):
    tenant = uuid4()
    config = {"niche": "servico_assinaturas"}
    class Result:
        def mappings(self): return self
        def first(self): return {"configuration": config, "is_new": is_new, "completed_at": None}
    class DB:
        async def execute(self, query, params):
            assert params == {"t": tenant}
            return Result()
    async def effective(db, tenant_id, *, lock):
        assert tenant_id == tenant and lock is None
        return plan_permissions.derive_plan(snapshot(grant="elite"), NOW)
    monkeypatch.setattr(plan_permissions, "plan_state", effective)
    monkeypatch.setenv("ONBOARDING_ENABLED", "1")
    for capability in ("appointments", "products", "public_site", "whatsapp", "assistant", "google"):
        with pytest.raises(HTTPException) as denied:
            await onboarding_service.require_operational_ready(DB(), tenant, capability)
        assert denied.value.status_code == 403
        assert denied.value.detail["code"] == "WORKSPACE_FEATURE_UNAVAILABLE"
    config["niche"] = "barbearia"
    if not is_new:
        assert await onboarding_service.require_operational_ready(DB(), tenant, "appointments") is None
    else:
        with pytest.raises(HTTPException) as pending:
            await onboarding_service.require_operational_ready(DB(), tenant, "appointments")
        assert pending.value.status_code == 409


@pytest.mark.asyncio
async def test_onboarding_operational_guards_preserve_effective_platform_workspace(monkeypatch):
    tenant = uuid4()
    async def effective(db, tenant_id, *, lock):
        assert tenant_id == tenant and lock is None
        return plan_permissions.derive_plan(snapshot(platform_workspace=True), NOW)
    monkeypatch.setattr(plan_permissions, "plan_state", effective)
    for feature in (None, "agenda.manage", "products"):
        assert await onboarding_service._require_workspace_operation(
            object(), tenant, {"niche": "servico_assinaturas"}, feature) is None


@pytest.mark.asyncio
async def test_disabled_onboarding_keeps_no_query_contract_but_workspace_features_stay_masked(monkeypatch):
    class DB:
        async def execute(self, *_args):
            raise AssertionError("Disabled onboarding must not query metadata")
    monkeypatch.setenv("ONBOARDING_ENABLED", "0")
    assert await onboarding_service.require_operational_ready(DB(), uuid4(), "appointments") is None
    effective = plan_permissions.derive_plan(snapshot(grant="elite"), NOW)
    assert set(effective["features"]) == SUBSCRIPTION_WORKSPACE_FEATURES


@pytest.fixture
def stored_plan(monkeypatch):
    case = SimpleNamespace(data=snapshot(grant="elite"), queries=[])
    case.tenant = UUID(case.data["tenant_id"])

    class Result:
        def __init__(self, value=None):
            self.value = value
        def scalar_one(self):
            return self.value
        def scalar_one_or_none(self):
            return self.value

    class DB:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_args):
            pass
        @asynccontextmanager
        async def begin(self):
            yield self
        async def execute(self, query, params=None):
            sql = str(query)
            case.queries.append((sql, params))
            if "pg_advisory_xact_lock" in sql:
                return Result()
            if "orion_plan_snapshot" in sql:
                assert params == {"tenant": case.tenant}
                return Result(case.data)
            if "orion_prepaid_plan_snapshot" in sql:
                return Result(case.data.get("prepaid"))
            if "FROM tenant_memberships" in sql:
                return Result("owner")
            raise AssertionError("Unexpected SQL: " + sql)

    case.db = DB()
    monkeypatch.setattr(catalog_api, "support_session_factory", lambda: DB)
    return case


@pytest.mark.asyncio
async def test_live_niche_changes_effective_revision_and_hides_preexisting_active_grant(stored_plan):
    case = stored_plan
    first = await plan_permissions.plan_state(case.db, case.tenant)
    assert first["code"] == "elite" and first["permissions"]["plan.manage"] is False
    with pytest.raises(HTTPException) as denied:
        await plan_permissions.require_feature(case.db, case.tenant, "agenda.manage")
    assert denied.value.detail["code"] == "WORKSPACE_FEATURE_UNAVAILABLE"
    assert await plan_permissions.has_feature(case.db, case.tenant, "notifications.basic") is False
    case.data["niche"] = "barbearia"
    normal = await plan_permissions.require_feature(case.db, case.tenant, "agenda.manage")
    assert normal["code"] == "elite" and normal["workspace_profile"] == "standard"
    assert normal["revision"] != first["revision"]
    assert all(params.get("tenant", case.tenant) == case.tenant for _, params in case.queries)


@pytest.mark.asyncio
async def test_checkout_rechecks_workspace_under_current_plan_lock(stored_plan):
    case = stored_plan
    with pytest.raises(HTTPException) as denied:
        await plan_management.require_checkout_available(case.db, case.tenant, uuid4())
    assert denied.value.status_code == 403
    assert denied.value.detail["code"] == "WORKSPACE_FEATURE_UNAVAILABLE"
    assert denied.value.detail["feature"] == "plan.manage"
    assert not any(sql.startswith(("INSERT", "UPDATE", "DELETE")) for sql, _ in case.queries)


@pytest.mark.asyncio
@pytest.mark.parametrize("when", ["before", "during"])
async def test_shared_recovery_features_cannot_bypass_workspace_route_guard(stored_plan, when):
    case = stored_plan
    case.data["niche"] = "barbearia" if when == "during" else "servico_assinaturas"
    token = plan_permissions.bind_feature(case.tenant, "business.setup", workspace_allowed=False)
    try:
        # A nested helper cannot make its restricted parent route available.
        nested = plan_permissions.bind_feature(case.tenant, "account.manage")
        try:
            with pytest.raises(HTTPException) as denied:
                async with plan_permissions.plan_transaction_guard(case.db, case.tenant):
                    assert when == "during"
                    case.data["niche"] = "servico_assinaturas"
            assert denied.value.detail["code"] == "WORKSPACE_FEATURE_UNAVAILABLE"
        finally:
            plan_permissions.reset_feature(nested)
    finally:
        plan_permissions.reset_feature(token)
    assert plan_permissions._scope.get() is None


@pytest.mark.asyncio
async def test_actual_route_dependency_allows_only_shell_metadata_and_private_setup(stored_plan):
    case = stored_plan
    application, router, called = FastAPI(), APIRouter(), []
    specs = [
        ("/management/permissions", "GET", 200), ("/onboarding", "GET", 200),
        ("/onboarding/complete", "POST", 200),
        ("/management/plan-overview", "GET", 403), ("/management/plan-trial", "POST", 403),
        ("/management/preferences", "GET", 403), ("/management/preferences", "PUT", 403),
        ("/management/notifications", "GET", 403), ("/business/profile", "GET", 403),
        ("/management/dashboard", "GET", 403), ("/audit", "GET", 403),
    ]

    async def endpoint(tenant_id: UUID):
        called.append(tenant_id)
        return {"ok": True}

    for relative, method, _ in specs:
        router.add_api_route(plan_routing.TENANT_PREFIX + relative, endpoint, methods=[method])

    @router.put(plan_routing.TENANT_PREFIX + "/onboarding/steps/{step_id}")
    async def step(tenant_id: UUID, step_id: str):
        if step_id == "business":
            assert plan_permissions._scope.get().exclusive is True
        called.append(tenant_id)
        return {"ok": True}

    plan_routing.include_plan_router(application, router)
    actor = catalog_api.Actor("member", case.tenant, uuid4(), "owner")
    application.dependency_overrides[catalog_api.authenticate] = lambda: actor
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="https://test") as client:
        prefix = "/api/v1/tenants/" + str(case.tenant)
        for relative, method, expected in specs:
            response = await client.request(method, prefix + relative)
            assert response.status_code == expected, (relative, response.text)
            if expected == 403:
                assert response.json()["detail"]["code"] == "WORKSPACE_FEATURE_UNAVAILABLE"
        for step_id in ("business", "location", "profile", "goals", "review", "hours"):
            assert (await client.put(prefix + "/onboarding/steps/" + step_id)).status_code == 200
        for step_id in ("professionals", "catalog", "channels"):
            assert (await client.put(prefix + "/onboarding/steps/" + step_id)).status_code == 403
        assert len(called) == 9
        # Platform administration keeps its explicit diagnostics privilege.
        actor = catalog_api.Actor("admin", account_id=uuid4())
        assert (await client.get(prefix + "/management/dashboard")).status_code == 200
        actor = catalog_api.Actor("member", case.tenant, uuid4(), "owner", maintenance=True)
        assert (await client.get(prefix + "/management/dashboard")).status_code == 403
        assert (await client.get(f"/api/v1/tenants/{uuid4()}/management/permissions")).status_code == 403
    assert plan_permissions._scope.get() is None
