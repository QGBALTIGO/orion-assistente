"""Prepaid Pix RLS and migration; isolated ephemeral PostgreSQL only."""
from uuid import uuid4
import os
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark=pytest.mark.integration

@pytest.mark.asyncio
async def test_0059_pix_ledger_and_entitlement_are_private():
    url=os.getenv("MIGRATION_DATABASE_URL")
    if not url:
        if os.getenv("REQUIRE_POSTGRES_TESTS")=="1":pytest.fail("PostgreSQL CI required")
        pytest.skip("no PostgreSQL")
    engine=create_async_engine(url.replace("postgresql+psycopg://","postgresql+asyncpg://",1))
    try:
        async with engine.connect() as db:
            rows=(await db.execute(text("""
              SELECT relname,relrowsecurity,relforcerowsecurity,
                     has_table_privilege('app_support',oid,'SELECT,INSERT,UPDATE'),
                     has_table_privilege('app_runtime',oid,'SELECT,INSERT,UPDATE,DELETE')
                FROM pg_class
               WHERE oid IN (to_regclass('public.orion_prepaid_pix_orders'),
                             to_regclass('public.orion_prepaid_pix_access'))
            """))).all()
            assert {x[0] for x in rows}=={"orion_prepaid_pix_orders","orion_prepaid_pix_access"}
            assert all(tuple(x[1:])==(True,True,True,False) for x in rows)
            cols=(await db.execute(text("""
                SELECT table_name,column_name FROM information_schema.columns
                 WHERE table_name IN ('orion_prepaid_pix_orders','orion_prepaid_pix_access')
            """))).all()
            assert ("orion_prepaid_pix_orders","callback_hash") in cols
            assert ("orion_prepaid_pix_access","paid_until") in cols
            assert ("orion_prepaid_pix_orders","doc_number") not in cols
            assert ("orion_prepaid_pix_orders","card_token") not in cols
            assert ("orion_prepaid_pix_orders","customer_email") not in cols
            available=(await db.execute(text("""
                SELECT to_regprocedure('public.orion_prepaid_plan_snapshot(uuid)') IS NOT NULL
            """))).scalar_one()
            assert available
            # No grant means unknown tenant never receives any access.
            state=(await db.execute(text("""
                SELECT public.orion_prepaid_plan_snapshot(:tenant)
            """),{"tenant":uuid4()})).scalar_one_or_none()
            assert state is None
    finally:
        await engine.dispose()
