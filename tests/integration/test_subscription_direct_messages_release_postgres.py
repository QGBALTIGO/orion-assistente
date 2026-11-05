"""Origin isolation and startup drift checks against disposable PostgreSQL 16."""
from contextlib import asynccontextmanager

import asyncpg
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from test_subscription_devices_release_postgres import isolated_settings
from scripts import backup_and_migrate_subscription_direct as release
from scripts import backup_and_migrate_subscription_bulk as previous_release
from scripts import wait_for_subscription_direct_schema as barrier
from scripts import wait_for_subscription_bulk_schema as previous_barrier

pytestmark = pytest.mark.integration


def test_origin_catalog_preserves_the_complete_published_bulk_contract(isolated_settings):
    assert previous_release.schema_ready(isolated_settings.migration_url)
    if not release.schema_ready(isolated_settings.migration_url):
        engine = create_engine(isolated_settings.migration_url)
        try:
            with engine.connect() as db:
                sections = {name: db.execute(text(getattr(barrier, name))).scalar()
                    for name in ("COLUMN_CONTRACT", "KEY_CONTRACT", "FOREIGN_KEY_CONTRACT",
                                 "DEFAULT_CONTRACT", "CHECK_CONTRACT")}
                guards = db.execute(text("""SELECT conname,pg_get_constraintdef(oid) AS definition
                    FROM pg_constraint WHERE conname IN
                    ('subscription_bulk_batch_origin','subscription_bulk_request_origin')
                    ORDER BY conname""")).mappings().all()
            pytest.fail(str({"sections": sections, "guards": [dict(row) for row in guards]}))
        finally:
            engine.dispose()


async def test_runtime_probe_requires_no_origin_or_private_row_grants(isolated_settings):
    assert await previous_barrier.probe_schema(isolated_settings.runtime, 3)
    assert await barrier.probe_schema(isolated_settings.runtime, 3)
    db = await asyncpg.connect(isolated_settings.runtime.dsn, timeout=3, command_timeout=3,
        server_settings={"default_transaction_read_only": "on", "search_path": "pg_catalog"})
    try:
        for table in barrier.PRIVATE_TABLES:
            row = await db.fetchrow("""SELECT
                has_column_privilege('app_runtime',$1,'origin','SELECT,INSERT,UPDATE') AS runtime_origin,
                has_column_privilege('app_support',$1,'origin','SELECT') AS support_read,
                has_column_privilege('app_support',$1,'origin','INSERT') AS support_append,
                has_column_privilege('app_support',$1,'origin','UPDATE') AS support_rewrite""", "public."+table)
            assert dict(row) == {"runtime_origin": False, "support_read": True,
                                 "support_append": True, "support_rewrite": False}
    finally:
        db.terminate()


@asynccontextmanager
async def temporary_catalog_change(settings):
    parsed = make_url(settings.migration_url)
    db = await asyncpg.connect(parsed.set(drivername="postgresql").render_as_string(hide_password=False),
        timeout=3, command_timeout=12,
        server_settings={"search_path": "pg_catalog", "statement_timeout": "12000", "lock_timeout": "3000"})
    try:
        await db.execute("BEGIN")
        assert await db.fetchval(barrier.CONTRACT_QUERY) is True
        yield db
    finally:
        try:
            await db.execute("ROLLBACK")
        finally:
            db.terminate()


@pytest.mark.parametrize("table", barrier.PRIVATE_TABLES)
@pytest.mark.parametrize("change", [
    "ALTER COLUMN origin DROP DEFAULT",
    "ALTER COLUMN origin SET DEFAULT 'direct'",
    "ALTER COLUMN origin DROP NOT NULL",
    "DROP COLUMN origin CASCADE",
    "NO FORCE ROW LEVEL SECURITY",
])
async def test_missing_origin_defaults_columns_or_rls_refuse_startup(isolated_settings, table, change):
    async with temporary_catalog_change(isolated_settings) as db:
        await db.execute(f"ALTER TABLE public.{table} {change}")
        assert await db.fetchval(barrier.CONTRACT_QUERY) is False


@pytest.mark.parametrize("table", barrier.PRIVATE_TABLES)
@pytest.mark.parametrize("grant", ["SELECT(origin) TO app_runtime", "UPDATE(origin) TO app_support",
                                   "SELECT(origin) TO PUBLIC"])
async def test_origin_data_cannot_gain_runtime_access_or_support_rewrite(isolated_settings, table, grant):
    async with temporary_catalog_change(isolated_settings) as db:
        await db.execute(f"GRANT {grant.split(' TO ')[0]} ON public.{table} TO {grant.split(' TO ')[1]}")
        assert await db.fetchval(barrier.CONTRACT_QUERY) is False


@pytest.mark.parametrize("table,constraint", barrier.CHECKS)
@pytest.mark.parametrize("guard", ["true", "origin IN ('bulk','direct','other')",
                                    "origin IN ('bulk','direct') OR origin IS NOT NULL"])
async def test_origin_domain_cannot_be_removed_or_broadened(isolated_settings, table, constraint, guard):
    async with temporary_catalog_change(isolated_settings) as db:
        await db.execute(f"ALTER TABLE public.{table} DROP CONSTRAINT {constraint}, ADD CONSTRAINT {constraint} CHECK({guard})")
        assert await db.fetchval(barrier.CONTRACT_QUERY) is False


@pytest.mark.parametrize("mutation", [
    "ALTER TABLE public.subscription_bulk_batches DROP CONSTRAINT uq_subscription_bulk_batch_origin CASCADE",
    "ALTER TABLE public.subscription_bulk_requests DROP CONSTRAINT fk_subscription_bulk_request_origin",
    "ALTER TABLE public.subscription_bulk_requests DROP CONSTRAINT fk_subscription_bulk_request_origin, "
      "ADD CONSTRAINT fk_subscription_bulk_request_origin FOREIGN KEY(tenant_id,bulk_id) "
      "REFERENCES public.subscription_bulk_batches(tenant_id,id)",
    "ALTER TABLE public.subscription_bulk_requests DROP CONSTRAINT fk_subscription_bulk_request_origin, "
      "ADD CONSTRAINT fk_subscription_bulk_request_origin FOREIGN KEY(tenant_id,bulk_id,origin) "
      "REFERENCES public.subscription_bulk_batches(tenant_id,id,origin) ON DELETE CASCADE",
    "ALTER TABLE public.subscription_bulk_recipients DROP CONSTRAINT subscription_bulk_recipient_source",
])
async def test_origin_link_and_predecessor_guards_remain_mandatory(isolated_settings, mutation):
    async with temporary_catalog_change(isolated_settings) as db:
        await db.execute(mutation)
        assert await db.fetchval(barrier.CONTRACT_QUERY) is False
