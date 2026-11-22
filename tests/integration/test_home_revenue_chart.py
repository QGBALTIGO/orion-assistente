"""Exercise the shipped chart query against isolated PostgreSQL tables."""
from datetime import date, datetime
import os
import unittest
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.management_models import hourly_revenue

DSN = os.getenv("ORION_HOME_CHART_TEST_DSN")


@unittest.skipUnless(DSN, "requires the isolated Orion chart database")
class HourlyRevenueTest(unittest.IsolatedAsyncioTestCase):
    async def test_hours_are_local_and_totals_include_only_completed_appointments(self):
        tenant = UUID(int=1)
        other_tenant = UUID(int=2)
        first_professional = UUID(int=3)
        second_professional = UUID(int=4)
        day = date(2026, 11, 22)
        engine = create_async_engine(DSN)
        try:
            async with engine.begin() as db:
                await db.execute(text("CREATE TEMP TABLE tenants (id uuid, timezone text) ON COMMIT DROP"))
                await db.execute(text("""
                    CREATE TEMP TABLE appointments (
                        tenant_id uuid, professional_id uuid, start_at timestamptz,
                        status text, total_cents bigint
                    ) ON COMMIT DROP
                """))
                await db.execute(text("INSERT INTO tenants VALUES (:tenant, 'America/Campo_Grande')"), {"tenant": tenant})
                examples = [
                    (tenant, first_professional, "2026-11-22T03:59:00+00:00", "completed", 90000),
                    (tenant, first_professional, "2026-11-22T04:00:00+00:00", "completed", 500),
                    (tenant, first_professional, "2026-11-22T11:05:00+00:00", "completed", 6000),
                    (tenant, second_professional, "2026-11-22T11:59:00+00:00", "completed", 4999),
                    (tenant, first_professional, "2026-11-22T11:28:00+00:00", "confirmed", 100000),
                    (other_tenant, first_professional, "2026-11-22T11:37:00+00:00", "completed", 100000),
                    (tenant, second_professional, "2026-11-23T03:59:00+00:00", "completed", 7000),
                    (tenant, first_professional, "2026-11-23T04:00:00+00:00", "completed", 100000),
                ]
                await db.execute(text("INSERT INTO appointments VALUES (:tenant, :professional, :start_at, :status, :cents)"), [
                    {"tenant": owner, "professional": professional, "start_at": datetime.fromisoformat(start),
                     "status": status, "cents": cents}
                    for owner, professional, start, status, cents in examples
                ])
                result = await hourly_revenue(db, tenant, day)
                self.assertEqual(result["granularity"], "hour")
                self.assertEqual(result["period"], {"start_date": day, "end_date": day, "timezone": "America/Campo_Grande"})
                self.assertEqual(result["items"], [
                    {"hour": 0, "completed": 1, "revenue_cents": 500},
                    {"hour": 7, "completed": 2, "revenue_cents": 10999},
                    {"hour": 23, "completed": 1, "revenue_cents": 7000},
                ])
                filtered = await hourly_revenue(db, tenant, day, first_professional)
                self.assertEqual(filtered["items"], [
                    {"hour": 0, "completed": 1, "revenue_cents": 500},
                    {"hour": 7, "completed": 1, "revenue_cents": 6000},
                ])
        finally:
            await engine.dispose()
