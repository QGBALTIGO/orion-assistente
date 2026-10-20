"""Parity on real tenant-scoped catalog facts and the final activation gate."""
from uuid import UUID, uuid4

from fastapi import HTTPException
import pytest
from sqlalchemy import text

from app import onboarding_service as service, onboarding_schemas as schemas
from app.database import tenant_transaction
from test_onboarding_postgres import onboarding_db, configured, save  # noqa: F401

pytestmark = pytest.mark.integration


def original_bindings(pros, services, facts):
    fit_services, fit_pros = set(), set()
    for item in services:
        duration = item.get("duration_min", 0) + item.get("prep_min", 0) + item.get("cleanup_min", 0)
        if duration <= 0 or type(item.get("price_cents")) is not int: continue
        for pro in pros:
            if not any(str(link["service_id"]) == str(item["id"]) and str(link["professional_id"]) == str(pro["id"])
                for link in facts["links"]): continue
            for hour in facts["hours"]:
                if str(hour["professional_id"]) != str(pro["id"]): continue
                pauses = sorted((service._minutes(p["start_time"]), service._minutes(p["end_time"]))
                    for p in facts["breaks"] if str(p["working_hour_id"]) == str(hour["id"]))
                cursor, end = service._minutes(hour["start_time"]), service._minutes(hour["end_time"])
                for start, stop in pauses + [(end, end)]:
                    if start-cursor >= duration:
                        fit_services.add(str(item["id"])); fit_pros.add(str(pro["id"]))
                    cursor = max(cursor, stop)
    return fit_services, fit_pros


async def parity(ctx, state, *, staged=True):
    tenant = UUID(state["tenant_id"])
    async with ctx.runtime() as db:
        async with tenant_transaction(db, tenant):
            facts = await service._facts(db, tenant)
            meta = (await db.execute(text("SELECT managed_ids FROM business_onboarding WHERE tenant_id=:t"), {"t": tenant})).mappings().one()
    managed = meta["managed_ids"]
    pros = [p for p in facts["professionals"] if service._usable(p, "professionals", managed, staged=staged) and p["accepts_appointments"]]
    services = [s for s in facts["services"] if service._usable(s, "services", managed, staged=staged)]
    assert service._fitting_bindings(pros, services, facts) == original_bindings(pros, services, facts)
    return facts


def completion(state):
    return schemas.Complete(expected_version=state["version"], expected_snapshot=state["snapshot"],
        idempotency_key=str(uuid4()), activate_catalog=True, acknowledged=True)


@pytest.mark.asyncio
async def test_staged_then_published_real_facts_keep_parity_and_explicit_activation(onboarding_db):
    ctx = onboarding_db; state = await configured(ctx, activity="both"); tenant = UUID(state["tenant_id"])
    assert not state["pending"]
    facts = await parity(ctx, state)
    assert all(not p["active"] for p in facts["professionals"]) and all(not s["active"] for s in facts["services"])
    async with ctx.migration.connect() as db:
        assert (await db.execute(text("SELECT status,bot_enabled FROM tenants WHERE id=:t"), {"t": tenant})).mappings().one() == {"status": "paused", "bot_enabled": False}
    done = await service.complete(ctx.actor, tenant, completion(state))
    assert done["status"] == "complete" and not done["pending"]
    facts = await parity(ctx, done, staged=False)
    assert all(p["active"] for p in facts["professionals"]) and all(s["active"] for s in facts["services"])
    async with ctx.runtime() as db:
        async with tenant_transaction(db, tenant):
            await service.require_operational_ready(db, tenant, "appointments")
            await service.require_operational_ready(db, tenant, "product_orders")
    async with ctx.migration.connect() as db:
        for table in ("appointments", "whatsapp_channels", "google_connections", "business_profile_versions"):
            assert (await db.execute(text(f"SELECT count(*) FROM {table} WHERE tenant_id=:t"), {"t": tenant})).scalar_one() == 0


@pytest.mark.asyncio
async def test_each_professional_needs_own_link_and_fitting_uninterrupted_shift(onboarding_db):
    ctx = onboarding_db; state = await configured(ctx); tenant = UUID(state["tenant_id"])
    state = await save(ctx, state, "professionals", {"item": {"name": "Second synthetic professional"}})
    pro = UUID(state["result"]["id"]); item = UUID(state["catalog"]["services"][0]["id"]); hour = uuid4()
    await parity(ctx, state)
    assert any(p["code"] == "scheduling_bindings" for p in state["pending"])
    with pytest.raises(HTTPException) as caught: await service.complete(ctx.actor, tenant, completion(state))
    assert caught.value.status_code == 409
    async with ctx.migration.begin() as db:
        await db.execute(text("INSERT INTO professional_services(tenant_id,professional_id,service_id) VALUES(:t,:p,:s)"), {"t": tenant, "p": pro, "s": item})
        await db.execute(text("INSERT INTO working_hours(id,tenant_id,professional_id,weekday,start_time,end_time) VALUES(:id,:t,:p,1,'09:00','09:30')"), {"id": hour, "t": tenant, "p": pro})
    state = await service.get_state(ctx.actor, tenant); await parity(ctx, state)
    assert any(p["code"] == "scheduling_bindings" for p in state["pending"])
    async with ctx.migration.begin() as db:
        await db.execute(text("UPDATE working_hours SET end_time='10:00' WHERE tenant_id=:t AND id=:id"), {"t": tenant, "id": hour})
        await db.execute(text("INSERT INTO working_breaks(tenant_id,working_hour_id,start_time,end_time) VALUES(:t,:h,'09:00','09:10')"), {"t": tenant, "h": hour})
    state = await service.get_state(ctx.actor, tenant); await parity(ctx, state)
    assert any(p["code"] == "scheduling_bindings" for p in state["pending"])
    async with ctx.migration.begin() as db:
        await db.execute(text("UPDATE working_breaks SET end_time='09:05' WHERE tenant_id=:t AND working_hour_id=:h"), {"t": tenant, "h": hour})
    fresh = await service.get_state(ctx.actor, tenant); await parity(ctx, fresh)
    assert not fresh["pending"] and state["snapshot"] != fresh["snapshot"]
    with pytest.raises(HTTPException) as stale: await service.complete(ctx.actor, tenant, completion(state))
    assert stale.value.status_code == 409
    done = await service.complete(ctx.actor, tenant, completion(fresh))
    assert done["status"] == "complete" and not done["pending"]


@pytest.mark.asyncio
async def test_200_professional_facts_match_original_and_remain_tenant_scoped(onboarding_db):
    ctx = onboarding_db; state = await configured(ctx); tenant = UUID(state["tenant_id"])
    item = UUID(state["catalog"]["services"][0]["id"])
    rows = [{"t": tenant, "p": uuid4(), "s": item} for _ in range(199)]
    foreign = uuid4()
    async with ctx.migration.begin() as db:
        await db.execute(text("INSERT INTO professionals(id,tenant_id,name) VALUES(:p,:t,'Synthetic scale professional')"), rows)
        await db.execute(text("INSERT INTO professional_services(tenant_id,professional_id,service_id) VALUES(:t,:p,:s)"), rows)
        await db.execute(text("INSERT INTO working_hours(tenant_id,professional_id,weekday,start_time,end_time) VALUES(:t,:p,1,'09:00','18:00')"), rows)
        await db.execute(text("INSERT INTO professionals(id,tenant_id,name) VALUES(:p,:t,'Foreign synthetic professional')"), {"t": ctx.b, "p": foreign})
    state = await service.get_state(ctx.actor, tenant); facts = await parity(ctx, state)
    assert len(facts["professionals"]) == 200 and not state["pending"]
    assert foreign not in {p["id"] for p in facts["professionals"]}
    assert all(p["tenant_id"] == tenant for p in facts["professionals"])
    with pytest.raises(HTTPException) as denied: await service.get_state(ctx.actor, ctx.b)
    assert denied.value.status_code == 403
