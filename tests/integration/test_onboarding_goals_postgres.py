"""Real tenant-scoped onboarding priorities, versioning and channel isolation."""
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import text

from app import onboarding_schemas as schemas, onboarding_service as service
from app.database import tenant_transaction
from test_onboarding_postgres import (  # noqa: F401
    onboarding_db, creation, new_business_context, save
)

pytestmark = pytest.mark.integration

METRICS = {
    "operating_age": "under_6_months", "team_size": "1_2",
    "monthly_revenue": "prefer_not_to_say", "monthly_volume": "up_to_50",
}
GOALS = {
    "main_challenge": "whatsapp",
    "desired_outcome": "automate_whatsapp",
    "respondent_role": "manager",
}


@pytest.mark.asyncio
async def test_quick_goals_are_required_then_saved_once_and_completed_without_fake_catalog(onboarding_db):
    ctx = new_business_context(onboarding_db)
    created = await service.create_business(ctx.actor, creation())
    tenant = UUID(created["tenant_id"])
    from plan_fixtures import seed_fixture_plan
    async with ctx.migration.begin() as db:
        ctx.plan_admins.append(await seed_fixture_plan(db,(tenant,),"essencial"))
    address = await save(ctx, created, "location", {"modality": "online"})
    profiled = await save(ctx, address, "profile", METRICS)
    assert profiled["current_step"] == "goals"
    assert [p["step"] for p in profiled["pending"]] == ["goals"]
    assert "goals" in {item["id"] for item in profiled["steps"]}
    assert "channels" not in {item["id"] for item in profiled["steps"]}

    with pytest.raises(HTTPException) as incomplete:
        await service.complete(ctx.actor, tenant, schemas.Complete(
            expected_version=profiled["version"], expected_snapshot=profiled["snapshot"],
            idempotency_key=str(uuid4()), activate_catalog=True, acknowledged=True))
    assert incomplete.value.status_code == 409

    key = str(uuid4())
    answer = await save(ctx, profiled, "goals", GOALS, key=key)
    replay = await save(ctx, profiled, "goals", GOALS, key=key)
    assert answer["version"] == profiled["version"] + 1 == replay["version"]
    assert answer["last_operation_key"] == key
    assert answer["current_step"] == "review"
    assert answer["pending"] == []
    assert answer["configuration"]["business_priorities"] == GOALS

    async with ctx.migration.connect() as db:
        config = (await db.execute(text(
            "SELECT configuration FROM business_onboarding WHERE tenant_id=:t"
        ), {"t": tenant})).scalar_one()
        assert config["business_priorities"] == GOALS
        for table in ("whatsapp_channels", "google_connections",
                      "business_profile_versions", "management_site_preferences",
                      "professionals", "services", "commercial_products"):
            count = (await db.execute(text(
                f"SELECT count(*) FROM {table} WHERE tenant_id=:t"
            ), {"t": tenant})).scalar_one()
            assert count == 0, table
        membership = (await db.execute(text(
            "SELECT role FROM tenant_memberships WHERE tenant_id=:t AND account_id=:a"
        ), {"t": tenant, "a": ctx.actor.account_id})).scalar_one()
        assert membership == "owner"  # questionnaire manager does not grant permissions

    done = await service.complete(ctx.actor, tenant, schemas.Complete(
        expected_version=answer["version"], expected_snapshot=answer["snapshot"],
        idempotency_key=str(uuid4()), activate_catalog=True, acknowledged=True))
    assert done["status"] == "complete"
    assert done["configuration"]["business_priorities"] == GOALS
    assert done["channels"]["whatsapp"]["status"] == "not_configured"
    async with ctx.runtime() as db:
        with pytest.raises(HTTPException):
            async with tenant_transaction(db, tenant):
                await service.require_operational_ready(db, tenant, "appointments")


@pytest.mark.asyncio
async def test_original_catalog_mode_refuses_goals_without_impacting_saved_operations(onboarding_db):
    ctx = new_business_context(onboarding_db)
    state = await service.create_business(ctx.actor, creation())
    tenant = UUID(state["tenant_id"])
    state = await save(ctx, state, "location", {"modality": "online"})
    state = await save(ctx, state, "professionals", {
        "item": {"name": "Profissional existente"}})
    state = await save(ctx, state, "catalog", {
        "kind": "service",
        "item": {"name": "Serviço existente", "price_cents": 3000, "duration_min": 30},
        "professional_ids": [state["result"]["id"]],
    })
    assert "catalog" in {item["id"] for item in state["steps"]}
    assert "goals" not in {item["id"] for item in state["steps"]}
    with pytest.raises(HTTPException) as blocked:
        await save(ctx, state, "goals", GOALS)
    assert blocked.value.status_code == 409
    current = await service.get_state(ctx.actor, tenant)
    assert current["version"] == state["version"]
    assert "business_priorities" not in current["configuration"]


@pytest.mark.asyncio
async def test_company_hours_save_without_professionals_or_booking_changes(onboarding_db):
    ctx = new_business_context(onboarding_db)
    state = await service.create_business(ctx.actor, creation())
    tenant = UUID(state["tenant_id"])
    days = [{"weekday": day, "open": day < 5,
             **({"start_time": "09:00", "end_time": "18:00",
                 "break_start": "12:00", "break_end": "13:00"}
                if day < 5 else {})} for day in range(7)]
    key = str(uuid4())
    saved = await save(ctx, state, "hours", {"days": days}, key=key)
    replay = await save(ctx, state, "hours", {"days": days}, key=key)
    assert saved["version"] == replay["version"] == state["version"] + 1
    assert saved["configuration"]["business_opening_hours"]["days"][0]["start_time"] == "09:00:00"
    assert saved["configuration"]["business_opening_hours"]["days"][6]["open"] is False
    assert saved["last_operation_key"] == key
    async with ctx.migration.connect() as db:
        for table in ("professionals", "working_hours", "working_breaks"):
            assert (await db.execute(text(
                f"SELECT count(*) FROM {table} WHERE tenant_id=:t"
            ), {"t": tenant})).scalar_one() == 0
    with pytest.raises(Exception):
        await save(ctx, saved, "hours", {"days": [
            {"weekday": 0, "open": True, "start_time": "20:00", "end_time": "09:00"}
        ]})
    refreshed = await service.get_state(ctx.actor, tenant)
    assert refreshed["version"] == saved["version"]
