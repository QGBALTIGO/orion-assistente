"""Explicit grants for synthetic PostgreSQL fixtures; never invoked by the app."""
from uuid import uuid4
from sqlalchemy import text


async def seed_fixture_plan(db, tenants, code="essencial"):
    actor=uuid4()
    await db.execute(text("""INSERT INTO panel_accounts(id,name,email,password_hash,role)
        VALUES(:id,'Admin sintético de homologação',:email,'unused-fixture-hash','admin')"""),
        {"id":actor,"email":f"plan-fixture-{actor}@example.test"})
    for tenant in tenants:
        await db.execute(text("""INSERT INTO tenant_plan_controls
            (tenant_id,manual_plan_code,manual_started_at,manual_actor_id)
            VALUES(:tenant,:code,clock_timestamp()-interval '1 day',:actor)
            ON CONFLICT(tenant_id) DO NOTHING"""),{"tenant":tenant,"code":code,"actor":actor})
    return actor


async def clear_fixture_plans(db, tenants, actors):
    for tenant in tenants:
        await db.execute(text("DELETE FROM tenant_plan_audit WHERE tenant_id=:tenant"),{"tenant":tenant})
        await db.execute(text("DELETE FROM tenant_plan_controls WHERE tenant_id=:tenant"),{"tenant":tenant})
    for actor in actors:
        if actor is not None:
            await db.execute(text("DELETE FROM panel_accounts WHERE id=:actor"),{"actor":actor})
