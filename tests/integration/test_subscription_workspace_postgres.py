"""Workspace isolation with actual member cookies, RLS, billing and plan locks."""
import asyncio
from uuid import UUID, uuid4

from fastapi import HTTPException
import pytest
import pytest_asyncio
from sqlalchemy import text

from app import (account_settings, business_profile_api, management_preferences,
                 onboarding_schemas, onboarding_service, orion_direct_checkout,
                 plan_permissions, prepaid_pix_api)
from app.database import tenant_transaction
from app.plan_routing import include_plan_router
from app.workspace_policy import SUBSCRIPTION_WORKSPACE_FEATURES
from plan_fixtures import seed_fixture_plan
from test_onboarding_completion_plan_postgres import (
    completion, disposable_environment, new_owner_http, onboarding_db, path, ready, save,
)  # noqa: F401 - shared real PostgreSQL/Redis fixtures

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def workspace_http(new_owner_http):
    case = new_owner_http
    for router in (business_profile_api.account_router, business_profile_api.router,
                   account_settings.router, management_preferences.router,
                   prepaid_pix_api.router, orion_direct_checkout.router):
        include_plan_router(case.app, router)
    return case


async def grant(case, tenant, code="elite"):
    async with case.ctx.migration.begin() as db:
        case.ctx.plan_admins.append(await seed_fixture_plan(db, (tenant,), code))


async def permissions(case, tenant):
    response = await case.client.get(f"/api/v1/tenants/{tenant}/management/permissions")
    assert response.status_code == 200, response.text
    return response.json()["effective_plan"]


def assert_workspace_denied(response):
    assert response.status_code == 403, response.text
    assert response.json()["detail"]["code"] == "WORKSPACE_FEATURE_UNAVAILABLE"


@pytest.mark.asyncio
@pytest.mark.parametrize("offering", ["subscriptions", "one_time_sales", "both"])
@pytest.mark.parametrize("existing_plan", [False, True])
async def test_real_member_keeps_account_but_cannot_access_operations_or_buy_a_plan(
        workspace_http, offering, existing_plan):
    case = workspace_http
    state = await ready(case, offering=offering)
    tenant = UUID(state["tenant_id"])
    if existing_plan:
        await grant(case, tenant)
    done = await case.client.post(path(state) + "/complete", json=completion(state))
    assert done.status_code == 200 and done.json()["status"] == "complete", done.text

    businesses = await case.client.get("/api/v1/account/businesses")
    assert businesses.status_code == 200, businesses.text
    assert len(businesses.json()) == 1
    assert businesses.json()[0]["id"] == str(tenant)
    assert businesses.json()[0]["workspace_profile"] == "subscription_services"
    effective = await permissions(case, tenant)
    assert effective["workspace_profile"] == "subscription_services"
    assert set(effective["features"]) == SUBSCRIPTION_WORKSPACE_FEATURES
    assert effective["code"] == ("elite" if existing_plan else None)
    assert effective["source"] == ("manual" if existing_plan else "none")

    identity = await case.client.get("/api/v1/account/business-identity")
    assert identity.status_code == 200 and identity.json()["can_edit"] is True
    current = identity.json()
    edited = await case.client.put("/api/v1/account/business-identity", json={
        "name": "Empresa sintética atualizada", "contact_phone": "+5567999999902",
        "expected_name": current["name"], "expected_phone": current["contact_phone"]})
    assert edited.status_code == 200, edited.text
    assert (await case.client.get("/api/v1/account/business-identity")).json()["name"] == "Empresa sintética atualizada"
    assert (await case.client.get(path(state))).json()["configuration"]["subscription_offering"] == offering

    prefix = f"/api/v1/tenants/{tenant}"
    for suffix in ("/management/plan-overview", "/management/plan-pix-config",
                   "/management/plan-direct-config", "/management/preferences",
                   "/management/notifications", "/business/profile", "/professionals",
                   "/services", "/working_hours", "/appointments", "/commerce/products"):
        assert_workspace_denied(await case.client.get(prefix + suffix))
    payer = {"name": "Pessoa Sintética", "email": "synthetic@example.test",
             "phone": "+5567999999901", "doc_type": "cpf", "doc_number": "52998224725",
             "fingerprint": "synthetic-test-fingerprint"}
    requests = (
        ("/management/plan-trial", {"full_name": "Pessoa Sintética",
            "company_name": "Empresa Sintética", "whatsapp": "+5567999999901", "privacy_ack": True}),
        ("/management/plan-checkout", {"plan_code": "elite"}),
        ("/management/plan-direct-prepare", {"plan_code": "elite"}),
        ("/management/plan-pix-charge", {"plan_code": "elite", "payer": payer, "consent_one_time": True}),
    )
    for suffix, payload in requests:
        assert_workspace_denied(await case.client.post(prefix + suffix, json=payload))

    # The same resolver protects jobs/public services, independent of HTTP UI.
    async with case.ctx.runtime() as db:
        async with tenant_transaction(db, tenant):
            for feature in ("agenda.manage", "site.manage", "notifications.basic", "reception.ai"):
                assert await plan_permissions.has_feature(db, tenant, feature) is False
            with pytest.raises(HTTPException) as denied:
                await plan_permissions.require_feature(db, tenant, "plan.manage")
            assert denied.value.detail["code"] == "WORKSPACE_FEATURE_UNAVAILABLE"

    foreign = await case.client.get(f"/api/v1/tenants/{case.ctx.b}/management/permissions")
    assert foreign.status_code == 403
    async with case.ctx.migration.connect() as db:
        for table in ("orion_plan_trial_requests", "orion_cakto_checkout_intents", "orion_prepaid_pix_orders", "professionals",
                      "services", "working_hours", "commercial_products", "appointments"):
            assert (await db.execute(text(f"SELECT count(*) FROM {table} WHERE tenant_id=:t"),
                                     {"t": tenant})).scalar_one() == 0, table
        row = (await db.execute(text("SELECT manual_plan_code FROM tenant_plan_controls WHERE tenant_id=:t"),
                               {"t": tenant})).scalar_one_or_none()
        assert row == ("elite" if existing_plan else None)


@pytest.mark.asyncio
async def test_subscription_setup_cannot_create_operational_drafts_even_with_existing_plan(workspace_http):
    case = workspace_http
    state = await ready(case, offering="both")
    tenant = UUID(state["tenant_id"])
    await grant(case, tenant)
    steps = (
        ("professionals", {"item": {"name": "Pessoa sintética"}}),
        ("catalog", {"kind": "product", "item": {"name": "Produto sintético"},
                     "variant": {"name": "Padrão", "price_kind": "on_request"}}),
        ("channels", {"assistant_name": "Assistente sintética", "style_rules": {"style": "direct"},
                      "capabilities": ["catalog_answers"], "expected_profile_generation": 0}),
    )
    for step, data in steps:
        payload = onboarding_schemas.SaveStep(expected_version=state["version"],
            idempotency_key=str(uuid4()), data=data)
        assert_workspace_denied(await case.client.put(path(state) + "/steps/" + step,
                                                      json=payload.model_dump(mode="json")))
        # Direct internal service callers receive the same denial before writes.
        with pytest.raises(HTTPException) as denied:
            await onboarding_service.save_step(case.ctx.actor, tenant, step, payload)
        assert denied.value.status_code == 403
        assert denied.value.detail["code"] == "WORKSPACE_FEATURE_UNAVAILABLE"
    after = await case.client.get(path(state))
    assert after.status_code == 200
    assert after.json() == {key: value for key, value in state.items() if key != "result"}
    done = await case.client.post(path(state) + "/complete", json=completion(state))
    assert done.status_code == 200 and done.json()["status"] == "complete", done.text
    async with case.ctx.migration.connect() as db:
        for table in ("professionals", "commercial_products", "working_hours", "whatsapp_channels"):
            assert (await db.execute(text(f"SELECT count(*) FROM {table} WHERE tenant_id=:t"),
                                     {"t": tenant})).scalar_one() == 0


@pytest.mark.asyncio
async def test_niche_edit_serializes_with_operations_and_restores_existing_plan_and_catalog(
        workspace_http, monkeypatch):
    case = workspace_http
    state = await ready(case, activity="products")
    tenant = UUID(state["tenant_id"])
    await grant(case, tenant)
    state = await save(case, state, "catalog", {"kind": "product",
        "item": {"name": "Produto que deve permanecer"},
        "variant": {"name": "Padrão", "price_kind": "on_request"}})
    done = await case.client.post(path(state) + "/complete", json=completion(state))
    assert done.status_code == 200, done.text
    state = done.json()
    before = await permissions(case, tenant)
    assert before["workspace_profile"] == "standard" and before["permissions"]["commerce.admin"]
    async with case.ctx.migration.connect() as db:
        original_plan = dict((await db.execute(text("SELECT * FROM tenant_plan_controls WHERE tenant_id=:t"),
                                               {"t": tenant})).mappings().one())
        original_catalog = [dict(row) for row in (await db.execute(text(
            "SELECT * FROM commercial_products WHERE tenant_id=:t ORDER BY id"), {"t": tenant})).mappings().all()]
    assert original_catalog and original_catalog[0]["active"]

    operation_locked, release_operation, edit_waiting = (asyncio.Event() for _ in range(3))
    original_lock = plan_permissions.lock_plan

    async def observe_lock(db, tenant_id, *, exclusive=False):
        if tenant_id == tenant and exclusive:
            edit_waiting.set()
        await original_lock(db, tenant_id, exclusive=exclusive)

    monkeypatch.setattr(plan_permissions, "lock_plan", observe_lock)

    async def in_flight_operation():
        token = plan_permissions.bind_feature(tenant, "commerce.admin")
        try:
            async with case.ctx.runtime() as db:
                async with tenant_transaction(db, tenant):
                    operation_locked.set()
                    await asyncio.wait_for(release_operation.wait(), 15)
        finally:
            plan_permissions.reset_feature(token)

    operation = asyncio.create_task(in_flight_operation())
    edit = None
    try:
        await asyncio.wait_for(operation_locked.wait(), 10)
        data = {"name": state["name"], "timezone": state["timezone"], "niche": "servico_assinaturas",
                "activity": "products", "subscription_offering": "both", "contact_phone": "+5567999999901"}
        edit = asyncio.create_task(case.client.put(path(state) + "/steps/business", json={
            "expected_version": state["version"], "idempotency_key": str(uuid4()), "data": data}))
        await asyncio.wait_for(edit_waiting.wait(), 10)
        assert not edit.done()
        async with case.ctx.migration.connect() as db:
            assert (await db.execute(text("SELECT configuration->>'niche' FROM business_onboarding WHERE tenant_id=:t"),
                                     {"t": tenant})).scalar_one() == "outro"
        release_operation.set()
        await asyncio.wait_for(operation, 10)
        changed = await asyncio.wait_for(edit, 10)
        assert changed.status_code == 200, changed.text
    finally:
        release_operation.set()
        if not operation.done():
            operation.cancel()
        if edit is not None and not edit.done():
            edit.cancel()
        await asyncio.gather(operation, *([edit] if edit is not None else []), return_exceptions=True)
    restricted = await permissions(case, tenant)
    assert restricted["workspace_profile"] == "subscription_services"
    assert restricted["revision"] != before["revision"]
    assert_workspace_denied(await case.client.get(f"/api/v1/tenants/{tenant}/commerce/products"))

    restored = await save(case, changed.json(), "business", {"name": state["name"],
        "timezone": state["timezone"], "niche": "outro", "other_niche": "Empresa sintética",
        "activity": "products", "contact_phone": "+5567999999901"})
    assert restored["status"] == "complete"
    assert "subscription_offering" not in restored["configuration"]
    final = await permissions(case, tenant)
    assert final["workspace_profile"] == "standard"
    assert final["features"] == before["features"] and final["code"] == "elite"
    assert (await case.client.get(f"/api/v1/tenants/{tenant}/commerce/products")).status_code == 200
    async with case.ctx.migration.connect() as db:
        assert dict((await db.execute(text("SELECT * FROM tenant_plan_controls WHERE tenant_id=:t"),
                                      {"t": tenant})).mappings().one()) == original_plan
        assert [dict(row) for row in (await db.execute(text(
            "SELECT * FROM commercial_products WHERE tenant_id=:t ORDER BY id"),
            {"t": tenant})).mappings().all()] == original_catalog
