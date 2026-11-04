"""New member onboarding over real HTTP auth, Redis and role-separated PostgreSQL."""
import asyncio
import json
import os
import secrets
from types import SimpleNamespace
from urllib.parse import urlparse
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException
import httpx
import pytest
import pytest_asyncio
from redis.asyncio import from_url
from sqlalchemy import text
from sqlalchemy.engine import make_url

from app import booking_api, catalog_api, commercial_api, onboarding_api, onboarding_service
from app import plan_management, plan_permissions
from app.database import tenant_transaction
from app.panel_auth import COOKIE, _hash_cookie
from app.plan_catalog import RECOVERY_FEATURES
from app.plan_routing import include_plan_router
from test_onboarding_postgres import onboarding_db, new_business_context  # noqa: F401

pytestmark = pytest.mark.integration

METRICS = {"operating_age": "1_to_3_years", "team_size": "1_2",
           "monthly_revenue": "prefer_not_to_say", "monthly_volume": "up_to_50"}
GOALS = {"main_challenge": "whatsapp", "desired_outcome": "automate_whatsapp",
         "respondent_role": "owner"}


@pytest.fixture(autouse=True)
def disposable_environment():
    for name, role in (("APP_DATABASE_URL", "app_runtime"),
                       ("MIGRATION_DATABASE_URL", "app_migrator"),
                       ("SUPPORT_DATABASE_URL", "app_support")):
        raw = os.getenv(name)
        if not raw:
            if os.getenv("REQUIRE_POSTGRES_TESTS") == "1":
                pytest.fail("Disposable PostgreSQL and Redis are required")
            pytest.skip("Disposable PostgreSQL is unavailable")
        url = make_url(raw)
        if url.host not in {"localhost", "127.0.0.1", "::1"} or url.username != role:
            pytest.fail("This onboarding regression may run only on disposable loopback databases")
    redis = urlparse(os.getenv("REDIS_URL", ""))
    if redis.scheme not in {"redis", "rediss"} or redis.hostname not in {"localhost", "127.0.0.1", "::1"}:
        pytest.fail("A disposable loopback Redis is required for actual cookie authentication")


@pytest_asyncio.fixture
async def new_owner_http(onboarding_db, monkeypatch):
    ctx = new_business_context(onboarding_db)
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    cache = from_url(os.environ["REDIS_URL"], decode_responses=True)
    key = _hash_cookie(token)
    async with ctx.migration.begin() as db:
        account = (await db.execute(text("""UPDATE panel_accounts SET verified_at=now()
            WHERE id=:a RETURNING role,auth_version"""), {"a": ctx.actor.account_id})).mappings().one()
    assert account["role"] == "member"
    await cache.set(key, json.dumps({"role": "member", "auth_version": account["auth_version"],
        "account_id": str(ctx.actor.account_id), "csrf": csrf}), ex=300)
    monkeypatch.setenv("SUPPORT_API_TOKEN", "synthetic-onboarding-http-" + "x" * 40)
    application = FastAPI()
    for router in (onboarding_api.router, plan_management.router, booking_api.router,
                   commercial_api.router, catalog_api.router):
        include_plan_router(application, router)
    # No authentication, membership, plan, Redis or SQL dependency is overridden.
    # The fixture supplies only synthetic records and actual test DB factories.
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application),
            base_url="https://test", cookies={COOKIE: token},
            headers={"Origin": "https://test", "x-orion-csrf": csrf}) as client:
            yield SimpleNamespace(ctx=ctx, client=client)
    finally:
        await cache.delete(key)
        await cache.aclose()


def path(state):
    return f"/api/v1/tenants/{state['tenant_id']}/onboarding"


def completion(state):
    return {"expected_version": state["version"], "expected_snapshot": state["snapshot"],
            "idempotency_key": str(uuid4()), "activate_catalog": True, "acknowledged": True}


async def save(case, state, step, data):
    response = await case.client.put(path(state) + "/steps/" + step, json={
        "expected_version": state["version"], "idempotency_key": str(uuid4()), "data": data})
    assert response.status_code == 200, response.text
    return response.json()


async def ready(case, *, offering=None, activity="services"):
    payload = {"name": "Empresa HTTP sintética", "timezone": "America/Cuiaba",
               "niche": "servico_assinaturas" if offering else "outro",
               "activity": "products" if offering else activity,
               "contact_phone": "+5567999999901", "idempotency_key": str(uuid4())}
    if offering:
        payload["subscription_offering"] = offering
    else:
        payload["other_niche"] = "Empresa sintética"
    created = await case.client.post("/api/v1/onboarding/businesses", json=payload)
    assert created.status_code == 201, created.text
    state = await save(case, created.json(), "location", {"modality": "online"})
    state = await save(case, state, "profile", METRICS)
    state = await save(case, state, "goals", GOALS)
    assert state["current_step"] == "review" and state["pending"] == []
    return state


async def assert_no_operational_access(case, state):
    prefix = f"/api/v1/tenants/{state['tenant_id']}"
    permissions = await case.client.get(prefix + "/management/permissions")
    assert permissions.status_code == 200, permissions.text
    effective = permissions.json()["effective_plan"]
    assert effective["source"] == "none" and effective["code"] is None
    assert effective["active"] is False and set(effective["features"]) == RECOVERY_FEATURES
    assert effective["limits"]["active_professionals"] == 0
    assert effective["trial"] is None and effective["manual_assignment"] is None
    overview = await case.client.get(prefix + "/management/plan-overview")
    assert overview.status_code == 200 and overview.json()["can_manage_plan"] is True
    assert overview.json()["trial"] is None and overview.json()["subscription"] is None
    for suffix, feature in (("/professionals", "professionals.manage"),
                            ("/working_hours", "agenda.manage"),
                            ("/appointments", "agenda.manage"),
                            ("/commerce/products", "commerce.admin")):
        denied = await case.client.get(prefix + suffix)
        assert denied.status_code == 403, denied.text
        assert denied.json()["detail"]["code"] == "PLAN_FEATURE_UNAVAILABLE"
        assert denied.json()["detail"]["feature"] == feature
    tenant = UUID(state["tenant_id"])
    async with case.ctx.runtime() as db:
        for capability in ("appointments", "products", "public_site"):
            with pytest.raises(HTTPException) as pending:
                async with tenant_transaction(db, tenant):
                    await onboarding_service.require_operational_ready(db, tenant, capability)
            assert pending.value.status_code == 409
        for feature in ("site.manage", "notifications.basic", "reception.ai"):
            with pytest.raises(HTTPException) as denied:
                async with tenant_transaction(db, tenant):
                    await plan_permissions.require_feature(db, tenant, feature)
            assert denied.value.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("offering,activity", [
    ("subscriptions", "products"), ("one_time_sales", "products"), ("both", "products"),
    (None, "services"), (None, "products"), (None, "both"),
])
async def test_new_owner_without_plan_can_finish_and_replay_but_cannot_operate(new_owner_http, offering, activity):
    case = new_owner_http
    state = await ready(case, offering=offering, activity=activity)
    finish = completion(state)
    # Simultaneous clicks/retries commit exactly one completion operation.
    responses = await asyncio.wait_for(asyncio.gather(*(
        case.client.post(path(state) + "/complete", json=finish) for _ in range(2))), 30)
    assert all(response.status_code == 200 for response in responses), [r.text for r in responses]
    done = responses[0].json()
    assert responses[1].json() == done
    assert done["version"] == state["version"] + 1 and done["status"] == "complete"
    assert done["required"] is False
    assert done["configuration"]["business_metrics"] == METRICS
    assert done["configuration"].get("subscription_offering") == offering
    reloaded = await case.client.get(path(state))
    assert reloaded.status_code == 200 and reloaded.json() == done
    entry = await case.client.get("/api/v1/onboarding/entry")
    assert entry.status_code == 200 and entry.json()["kind"] == "dashboard"
    stale = await case.client.post(path(state) + "/complete", json={
        **finish, "idempotency_key": str(uuid4())})
    assert stale.status_code == 409
    await assert_no_operational_access(case, done)

    async with case.ctx.migration.connect() as db:
        tenant = UUID(state["tenant_id"])
        assert (await db.execute(text("SELECT status FROM tenants WHERE id=:t"),
                                 {"t": tenant})).scalar_one() == "active"
        assert (await db.execute(text("""SELECT count(*) FROM business_onboarding_operations
            WHERE tenant_id=:t AND step='complete'"""), {"t": tenant})).scalar_one() == 1
        for table in ("tenant_plan_controls", "subscriptions", "orion_plan_trial_requests",
                      "professionals", "services", "commercial_products", "commercial_variants",
                      "working_hours", "appointments", "whatsapp_channels", "google_connections",
                      "management_site_preferences", "business_profile_versions"):
            assert (await db.execute(text(f"SELECT count(*) FROM {table} WHERE tenant_id=:t"),
                                     {"t": tenant})).scalar_one() == 0, table


@pytest.mark.asyncio
async def test_completion_keeps_csrf_current_owner_and_tenant_guards(new_owner_http):
    case = new_owner_http
    state = await ready(case, offering="both")
    finish = completion(state)
    for headers in ({"x-orion-csrf": "wrong-synthetic"}, {"Origin": "https://foreign.test"}):
        denied = await case.client.post(path(state) + "/complete", json=finish, headers=headers)
        assert denied.status_code == 403
    foreign = await case.client.post(
        f"/api/v1/tenants/{case.ctx.b}/onboarding/complete", json=finish)
    assert foreign.status_code == 403
    tenant = UUID(state["tenant_id"])
    async with case.ctx.migration.begin() as db:
        await db.execute(text("""UPDATE tenant_memberships SET role='viewer'
            WHERE tenant_id=:t AND account_id=:a"""), {"t": tenant, "a": case.ctx.actor.account_id})
    denied = await case.client.post(path(state) + "/complete", json=finish)
    assert denied.status_code == 403
    current = await case.client.get(path(state))
    assert current.status_code == 200 and current.json()["version"] == state["version"]
    assert current.json()["status"] == "incomplete"
    async with case.ctx.migration.begin() as db:
        await db.execute(text("""UPDATE tenant_memberships SET active=false
            WHERE tenant_id=:t AND account_id=:a"""), {"t": tenant, "a": case.ctx.actor.account_id})
    assert (await case.client.post(path(state) + "/complete", json=finish)).status_code == 403
    async with case.ctx.migration.connect() as db:
        assert (await db.execute(text("""SELECT completed_at IS NULL FROM business_onboarding
            WHERE tenant_id=:t"""), {"t": tenant})).scalar_one() is True
        assert (await db.execute(text("SELECT status FROM tenants WHERE id=:t"),
                                 {"t": tenant})).scalar_one() == "paused"
        assert (await db.execute(text("""SELECT count(*) FROM business_onboarding_operations
            WHERE tenant_id=:t AND step='complete'"""), {"t": tenant})).scalar_one() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["professional", "service", "product"])
async def test_quick_answers_cannot_activate_staged_catalog_without_plan(new_owner_http, kind):
    case = new_owner_http
    state = await ready(case, offering="both")
    if kind in {"professional", "service"}:
        state = await save(case, state, "professionals", {"item": {"name": "Pessoa sintética"}})
        if kind == "service":
            state = await save(case, state, "catalog", {"kind": "service",
                "item": {"name": "Serviço sintético", "price_cents": 3000, "duration_min": 30},
                "professional_ids": [state["result"]["id"]]})
    else:
        state = await save(case, state, "catalog", {"kind": "product",
            "item": {"name": "Produto sintético"},
            "variant": {"name": "Padrão", "price_kind": "on_request"}})
    assert "profile" in {step["id"] for step in state["steps"]} and state["pending"] == []
    finish = completion(state)
    denied = await case.client.post(path(state) + "/complete", json=finish)
    assert denied.status_code == 403, denied.text
    assert denied.json()["detail"]["code"] == "PLAN_FEATURE_UNAVAILABLE"
    assert denied.json()["detail"]["feature"] == "professionals.manage"
    current = await case.client.get(path(state))
    assert current.status_code == 200 and current.json()["version"] == state["version"]
    assert current.json()["snapshot"] == state["snapshot"] and current.json()["status"] == "incomplete"
    async with case.ctx.migration.connect() as db:
        tenant = UUID(state["tenant_id"])
        meta = (await db.execute(text("""SELECT completed_at,managed_ids FROM business_onboarding
            WHERE tenant_id=:t"""), {"t": tenant})).mappings().one()
        assert meta["completed_at"] is None and any(meta["managed_ids"].values())
        assert (await db.execute(text("SELECT status FROM tenants WHERE id=:t"),
                                 {"t": tenant})).scalar_one() == "paused"
        for table in ("professionals", "services", "commercial_products", "commercial_variants"):
            assert (await db.execute(text(f"SELECT count(*) FROM {table} WHERE tenant_id=:t AND active"),
                                     {"t": tenant})).scalar_one() == 0
        assert (await db.execute(text("""SELECT count(*) FROM business_onboarding_operations
            WHERE tenant_id=:t AND idempotency_key=:k"""),
            {"t": tenant, "k": finish["idempotency_key"]})).scalar_one() == 0
