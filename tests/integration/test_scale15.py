"""15 tenants sintéticos, concorrência real e isolamento RLS obrigatório."""
import asyncio
import os
from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker

from app.database import tenant_transaction


@pytest.mark.integration
@pytest.mark.asyncio
async def test_fifteen_tenants_simultaneously_isolated():
    migrator=os.getenv("MIGRATION_DATABASE_URL")
    runtime=os.getenv("APP_DATABASE_URL")
    if not migrator or not runtime:
        if os.getenv("REQUIRE_POSTGRES_TESTS")=="1":pytest.fail("Postgres real necessário")
        pytest.skip("Postgres não configurado")
    admin=create_async_engine(migrator.replace("postgresql+psycopg://","postgresql+asyncpg://",1))
    eng=create_async_engine(runtime,pool_size=20,max_overflow=0)
    factory=async_sessionmaker(eng)
    ids=[uuid4() for _ in range(15)]
    period=date.today().replace(day=1)
    try:
        async with admin.begin() as s:
            for index,tenant in enumerate(ids):
                await s.execute(text("""
                    INSERT INTO tenants (id,name,slug) VALUES (:t,'Testes sintéticos',:slug)
                """),{"t":tenant,"slug":"f7-"+str(tenant)})
                await s.execute(text("""
                    INSERT INTO whatsapp_channels
                    (tenant_id,phone_number_id,business_number,status)
                    VALUES (:t,:phone,'+5567999999999','testing')
                """),{"t":tenant,"phone":"f7-number-"+str(tenant)})
        async def check(tenant,index):
            async with factory() as s:
                async with tenant_transaction(s,tenant):
                    await s.execute(text("""
                        INSERT INTO usage_counters
                        (tenant_id,period,inbound_messages)
                        VALUES (:t,:period,:n)
                    """),{"t":tenant,"period":period,"n":index+1})
                    assert (await s.execute(text(
                        "SELECT tenant_id,inbound_messages FROM usage_counters"
                    ))).all()==[(tenant,index+1)]
                    assert (await s.execute(text(
                        "SELECT tenant_id FROM whatsapp_channels"
                    ))).all()==[(tenant,)]
        await asyncio.gather(*(check(tid,i) for i,tid in enumerate(ids)))
        async with eng.connect() as s:
            assert (await s.execute(text("SELECT id FROM usage_counters"))).all()==[]
        async with admin.connect() as s:
            values=(await s.execute(text("""
                SELECT inbound_messages FROM usage_counters WHERE tenant_id=ANY(:ids)
            """),{"ids":ids})).scalars().all()
            assert sorted(values)==list(range(1,16))
    finally:
        async with admin.begin() as s:
            for table in ("usage_counters","whatsapp_channels"):
                await s.execute(text(f"DELETE FROM {table} WHERE tenant_id=ANY(:ids)"),{"ids":ids})
            await s.execute(text("DELETE FROM tenants WHERE id=ANY(:ids)"),{"ids":ids})
        await eng.dispose()
        await admin.dispose()
