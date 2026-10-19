"""The 10+0 contract retains real PG connections while runtime scope stays transactional."""
import asyncio
import os

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.database import engine_pool_options
from test_onboarding_postgres import onboarding_db

pytestmark = pytest.mark.integration


async def wave(engine, role, tenant):
    ready, acquired = asyncio.Event(), []

    async def connection():
        async with engine.connect() as db:
            async with db.begin():
                scope = (await db.execute(text("SELECT current_setting('app.tenant_id',true)"))).scalar_one()
                assert scope in (None, "")
                if role == "app_runtime":
                    assert not (await db.execute(text("SELECT id FROM tenants"))).scalars().all()
                await db.execute(text("SELECT set_config('app.tenant_id',:tenant,true)"), {"tenant": str(tenant)})
                row = (await db.execute(text("""SELECT pg_backend_pid() AS pid,current_user AS role,
                    current_setting('app.tenant_id',true) AS tenant"""))).mappings().one()
                assert row["role"] == role and row["tenant"] == str(tenant)
                if role == "app_runtime":
                    # Deliberately no WHERE: FORCE RLS must retain this connection's tenant.
                    visible = (await db.execute(text("SELECT id FROM tenants"))).scalars().all()
                    assert visible == [tenant]
                acquired.append(row["pid"])
                if len(acquired) == 10:
                    ready.set()
                await asyncio.wait_for(ready.wait(), 10)
        return row["pid"]

    return set(await asyncio.wait_for(asyncio.gather(*(connection() for _ in range(10))), 15))


@pytest.mark.asyncio
async def test_both_ten_role_pools_reuse_backend_ids_and_reset_runtime_tenant(onboarding_db, monkeypatch):
    ctx = onboarding_db
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("ORION_DB_POOL_SIZE", "10")
    monkeypatch.setenv("ORION_DB_MAX_OVERFLOW", "0")
    engines = []
    try:
        for name, role in (("APP_DATABASE_URL", "app_runtime"), ("SUPPORT_DATABASE_URL", "app_support")):
            url = os.environ[name].replace("postgresql://", "postgresql+asyncpg://", 1)
            assert make_url(url).username == role
            engine = create_async_engine(url, **engine_pool_options())
            engines.append(engine)
            first = await wave(engine, role, ctx.a)
            second = await wave(engine, role, ctx.b)
            third = await wave(engine, role, ctx.a)
            assert len(first) == 10 and first == second == third
            assert engine.pool.size() == 10 and engine.pool.overflow() == 0
    finally:
        for engine in engines:
            await engine.dispose()
