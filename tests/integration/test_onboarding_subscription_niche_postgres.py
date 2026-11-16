"""Real creation, replay and quick completion of subscription business profiles."""
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import text

from app import onboarding_schemas as schemas, onboarding_service as service
from app.database import tenant_transaction
from test_onboarding_postgres import onboarding_db, new_business_context, save  # noqa: F401

pytestmark = pytest.mark.integration

METRICS = {"operating_age": "not_started", "team_size": "none",
           "monthly_revenue": "none", "monthly_volume": "none"}
GOALS = {"main_challenge": "whatsapp", "desired_outcome": "automate_whatsapp",
         "respondent_role": "owner"}


@pytest.mark.asyncio
@pytest.mark.parametrize("offering", ["subscriptions", "one_time_sales", "both"])
async def test_choice_persists_replays_completes_and_is_cleared_after_niche_change(onboarding_db, offering):
    ctx = new_business_context(onboarding_db)
    payload = schemas.CreateBusiness(
        name="Assinaturas sintéticas", timezone="America/Cuiaba",
        niche="servico_assinaturas", activity="products", subscription_offering=offering,
        contact_phone="+5567999999901", idempotency_key=str(uuid4()))
    created = await service.create_business(ctx.actor, payload)
    tenant = UUID(created["tenant_id"])
    creation_replay = await service.create_business(ctx.actor, payload)
    reloaded = await service.get_state(ctx.actor, tenant)
    assert creation_replay["tenant_id"] == reloaded["tenant_id"] == created["tenant_id"]
    assert creation_replay["version"] == reloaded["version"] == created["version"]
    assert reloaded["configuration"]["subscription_offering"] == offering
    assert reloaded["configuration"]["activity"] == "products"

    alternate = {"subscriptions": "one_time_sales", "one_time_sales": "both",
                 "both": "subscriptions"}[offering]
    changed_identity = {**payload.model_dump(mode="json", exclude={"idempotency_key"}),
                        "subscription_offering": alternate}
    key = str(uuid4())
    changed = await save(ctx, reloaded, "business", changed_identity, key=key)
    replay = await save(ctx, reloaded, "business", changed_identity, key=key)
    assert changed["version"] == replay["version"] == reloaded["version"] + 1
    assert replay["configuration"]["subscription_offering"] == alternate
    assert replay["last_operation_key"] == key

    state = await save(ctx, changed, "location", {"modality": "online"})
    state = await save(ctx, state, "profile", METRICS)
    state = await save(ctx, state, "goals", GOALS)
    assert state["pending"] == []
    done = await service.complete(ctx.actor, tenant, schemas.Complete(
        expected_version=state["version"], expected_snapshot=state["snapshot"],
        idempotency_key=str(uuid4()), activate_catalog=True, acknowledged=True))
    assert done["status"] == "complete"
    assert done["configuration"]["subscription_offering"] == alternate
    assert done["channels"]["whatsapp"]["status"] == "not_configured"
    async with ctx.runtime() as db:
        with pytest.raises(HTTPException) as no_agenda:
            async with tenant_transaction(db, tenant):
                await service.require_operational_ready(db, tenant, "appointments")
        assert no_agenda.value.status_code == 403
        assert no_agenda.value.detail["code"] == "WORKSPACE_FEATURE_UNAVAILABLE"

    async with ctx.migration.connect() as db:
        config = (await db.execute(text(
            "SELECT configuration FROM business_onboarding WHERE tenant_id=:t"
        ), {"t": tenant})).scalar_one()
        assert config["subscription_offering"] == alternate
        assert (await db.execute(text("SELECT status FROM tenants WHERE id=:t"),
                                 {"t": tenant})).scalar_one() == "active"
        for table in ("professionals", "services", "commercial_products", "appointments"):
            assert (await db.execute(text(f"SELECT count(*) FROM {table} WHERE tenant_id=:t"),
                                     {"t": tenant})).scalar_one() == 0, table

    other_identity = {**changed_identity, "niche": "outro", "other_niche": "Loja sintética"}
    other_identity.pop("subscription_offering")
    leave_key = str(uuid4())
    left = await save(ctx, done, "business", other_identity, key=leave_key)
    left_replay = await save(ctx, done, "business", other_identity, key=leave_key)
    assert left["version"] == left_replay["version"] == done["version"] + 1
    assert "subscription_offering" not in left_replay["configuration"]
    current = await service.get_state(ctx.actor, tenant)
    assert current["configuration"]["niche"] == "outro"
    assert "subscription_offering" not in current["configuration"]
    # Replaying the older successful edit must not restore the removed choice.
    older_replay = await save(ctx, reloaded, "business", changed_identity, key=key)
    assert older_replay["version"] == left["version"]
    assert "subscription_offering" not in older_replay["configuration"]
    async with ctx.migration.connect() as db:
        assert (await db.execute(text("""SELECT NOT (configuration ? 'subscription_offering')
            FROM business_onboarding WHERE tenant_id=:t"""), {"t": tenant})).scalar_one() is True
