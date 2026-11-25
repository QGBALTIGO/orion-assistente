"""A real affiliate company is created independently from appointment setup."""
from dataclasses import replace
from uuid import UUID, uuid4

from cryptography.fernet import Fernet
from fastapi import HTTPException
import pytest
from sqlalchemy import text

from app import onboarding_schemas as schemas, onboarding_service as service
from app.affiliate_workspace import affiliate_owner
from app.catalog_api import Actor
from test_onboarding_postgres import onboarding_db  # noqa: F401


@pytest.mark.integration
async def test_affiliate_creation_is_idempotent_and_only_verified_owner_has_access(onboarding_db, monkeypatch):
    ctx = onboarding_db
    monkeypatch.setenv("ORION_AFFILIATE_WORKSPACE_KEY", Fernet.generate_key().decode())
    payload = schemas.CreateBusiness(
        name="Ofertas sintéticas", timezone="America/Cuiaba", niche="afiliados",
        activity="products", affiliate_activity="offers", idempotency_key=str(uuid4()),
    )
    with pytest.raises(HTTPException) as unverified:
        await service.create_business(ctx.new_actor, payload)
    assert unverified.value.status_code == 403
    async with ctx.migration.begin() as db:
        await db.execute(text("UPDATE panel_accounts SET verified_at=now() WHERE id=:id"),
                         {"id": ctx.new_actor.account_id})
    state = await service.create_business(ctx.new_actor, payload)
    assert state["status"] == "complete" and state["required"] is False
    assert [s["id"] for s in state["steps"]] == ["business"]
    assert state["configuration"]["affiliate_activity"] == "offers"
    assert (await service.create_business(ctx.new_actor, payload))["tenant_id"] == state["tenant_id"]
    assert (await service.entry(ctx.new_actor))["kind"] == "affiliate"
    tenant = UUID(state["tenant_id"])
    owner = replace(ctx.new_actor, tenant_id=tenant, membership_role="owner")
    assert await affiliate_owner(owner, tenant) == payload.name
    for actor in (replace(owner, membership_role="editor"), Actor("support"),
                  replace(owner, account_id=ctx.other)):
        with pytest.raises(HTTPException) as denied:
            await affiliate_owner(actor, tenant)
        assert denied.value.status_code == 403
    async with ctx.migration.connect() as db:
        for table in ("professionals", "services", "commercial_products", "appointments"):
            assert (await db.execute(text(f"SELECT count(*) FROM {table} WHERE tenant_id=:t"),
                                     {"t": tenant})).scalar_one() == 0
