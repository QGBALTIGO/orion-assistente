"""0072 catalog, privilege and drift guards on disposable PostgreSQL only."""
from contextlib import asynccontextmanager

import asyncpg
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from test_subscription_devices_release_postgres import isolated_settings
from scripts import backup_and_migrate_subscription_group_add as release
from scripts import backup_and_migrate_subscription_direct as previous_release
from scripts import wait_for_subscription_group_add_schema as barrier
from scripts import wait_for_subscription_direct_schema as previous_barrier

pytestmark = pytest.mark.integration
BATCH = "subscription_group_add_batches"
ITEM = "subscription_group_add_items"


def test_0072_catalog_preserves_0071_and_the_reviewed_group_membership_contract(isolated_settings):
    assert previous_release.schema_ready(isolated_settings.migration_url)
    if not release.schema_ready(isolated_settings.migration_url):
        # Diagnose catalog definitions only, never source contacts or credentials.
        engine = create_engine(isolated_settings.migration_url)
        try:
            with engine.connect() as db:
                sections = {name: db.execute(text(getattr(barrier, name))).scalar() for name in (
                    "TABLE_CONTRACT", "COLUMN_CONTRACT", "KEY_CONTRACT", "FOREIGN_KEY_CONTRACT",
                    "CHECK_CONTRACT", "INDEX_CONTRACT", "SETTLEMENT_CONTRACT", "EXACT_DOMAINS_CONTRACT")}
                definitions = db.execute(text("""SELECT conname,pg_get_constraintdef(oid) AS definition
                    FROM pg_constraint WHERE conrelid IN
                    ('public.subscription_group_add_batches'::regclass,'public.subscription_group_add_items'::regclass)
                    AND contype='c' ORDER BY conname""")).mappings().all()
                predicate = db.execute(text("""SELECT pg_get_expr(indpred,indrelid)
                    FROM pg_index WHERE indexrelid='public.uq_subscription_group_add_active_device'::regclass""")).scalar()
            pytest.fail(str({"sections": sections, "checks": [dict(row) for row in definitions], "active_predicate": predicate}))
        finally:
            engine.dispose()


async def test_runtime_can_probe_without_private_rows_and_support_cannot_rewrite_intent(isolated_settings):
    assert await previous_barrier.probe_schema(isolated_settings.runtime, 3)
    assert await barrier.probe_schema(isolated_settings.runtime, 3)
    db = await asyncpg.connect(isolated_settings.runtime.dsn, timeout=3, command_timeout=3,
        server_settings={"default_transaction_read_only": "on", "search_path": "pg_catalog"})
    try:
        assert await db.fetchval("SELECT current_user") == "app_runtime"
        assert await db.fetchval("SELECT session_user") == "app_runtime"
        assert await db.fetchval("SELECT current_setting('default_transaction_read_only')") == "on"
        assert not await db.fetchval("SELECT has_table_privilege(current_user,'public.alembic_version','SELECT')")
        for table in (BATCH, ITEM):
            row = await db.fetchrow("""SELECT
                has_any_column_privilege('app_runtime',$1,'SELECT,INSERT,UPDATE,REFERENCES') AS runtime_columns,
                has_table_privilege('app_runtime',$1,'DELETE,TRUNCATE,TRIGGER') AS runtime_write,
                has_table_privilege('app_support',$1,'SELECT') AS support_read,
                has_table_privilege('app_support',$1,'INSERT') AS support_append,
                has_table_privilege('app_support',$1,'UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') AS support_write
                """, "public." + table)
            assert dict(row) == {"runtime_columns": False, "runtime_write": False,
                "support_read": True, "support_append": True, "support_write": False}
            await db.execute("SELECT set_config('app.tenant_id','11111111-1111-4111-8111-111111111111',false)")
            with pytest.raises(asyncpg.InsufficientPrivilegeError) as denied:
                await db.fetch("SELECT 1 FROM public." + table + " LIMIT 1")
            assert denied.value.sqlstate == "42501"
        for table, columns in (
            (BATCH, ("owner_account_id", "device_id", "generation", "settings_revision", "destination_group_id",
                     "source_hash", "idempotency_key", "request_fingerprint")),
            (ITEM, ("batch_id", "device_id", "generation", "settings_revision", "phone", "claim_token",
                    "permission_version", "source_context")),
        ):
            for column in columns:
                assert not await db.fetchval("SELECT has_column_privilege('app_support',$1,$2,'UPDATE')", "public." + table, column)
        assert await db.fetchval("SELECT has_column_privilege('app_support',$1,'provider_settled','UPDATE')", "public." + ITEM)
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


@pytest.mark.parametrize("mutation", [
    "GRANT SELECT ON public.subscription_group_add_batches TO app_runtime",
    "GRANT SELECT(phone) ON public.subscription_group_add_items TO app_runtime",
    "GRANT UPDATE ON public.subscription_group_add_items TO app_support",
    "GRANT UPDATE(destination_group_id) ON public.subscription_group_add_batches TO app_support",
    "GRANT UPDATE(claim_token) ON public.subscription_group_add_items TO app_support",
    "GRANT UPDATE(status) ON public.subscription_group_add_items TO app_support WITH GRANT OPTION",
    "GRANT SELECT ON public.subscription_group_add_batches TO PUBLIC",
    "GRANT UPDATE(provider_settled) ON public.subscription_group_add_items TO PUBLIC",
    "ALTER TABLE public.subscription_group_add_batches NO FORCE ROW LEVEL SECURITY",
    "ALTER TABLE public.subscription_group_add_items DISABLE ROW LEVEL SECURITY",
    "ALTER POLICY subscription_group_add_items_runtime ON public.subscription_group_add_items USING(true)",
    "CREATE POLICY group_add_unscoped_runtime ON public.subscription_group_add_batches FOR SELECT TO app_runtime USING(true)",
    "ALTER POLICY subscription_group_add_batches_support ON public.subscription_group_add_batches WITH CHECK(false)",
])
async def test_rls_or_privilege_drift_refuses_api_and_worker_startup(isolated_settings, mutation):
    async with temporary_catalog_change(isolated_settings) as db:
        await db.execute(mutation)
        assert await db.fetchval(barrier.CONTRACT_QUERY) is False


@pytest.mark.parametrize("mutation", [
    "ALTER TABLE public.subscription_group_add_items ALTER COLUMN provider_settled DROP DEFAULT",
    "ALTER TABLE public.subscription_group_add_items ALTER COLUMN provider_settled SET DEFAULT true",
    "ALTER TABLE public.subscription_group_add_items ALTER COLUMN provider_settled DROP NOT NULL",
    "ALTER TABLE public.subscription_group_add_items DROP COLUMN provider_settled CASCADE",
    "REVOKE UPDATE(provider_settled) ON public.subscription_group_add_items FROM app_support",
])
async def test_provider_completion_cannot_default_to_proved_or_lose_its_required_contract(isolated_settings, mutation):
    async with temporary_catalog_change(isolated_settings) as db:
        await db.execute(mutation)
        assert await db.fetchval(barrier.CONTRACT_QUERY) is False


@pytest.mark.parametrize("mutation", [
    "ALTER TABLE public.subscription_group_add_batches DROP CONSTRAINT uq_subscription_group_add_key",
    "ALTER TABLE public.subscription_group_add_items DROP CONSTRAINT uq_subscription_group_add_ordinal",
    "ALTER TABLE public.subscription_group_add_items DROP CONSTRAINT uq_subscription_group_add_phone",
    "ALTER TABLE public.subscription_group_add_items DROP CONSTRAINT fk_subscription_group_add_item_context, "
      "ADD CONSTRAINT fk_subscription_group_add_item_context FOREIGN KEY(tenant_id,batch_id) "
      "REFERENCES public.subscription_group_add_batches(tenant_id,id)",
    "ALTER TABLE public.subscription_group_add_items DROP CONSTRAINT fk_subscription_group_add_item_context, "
      "ADD CONSTRAINT fk_subscription_group_add_item_context FOREIGN KEY(tenant_id,batch_id,device_id,generation,settings_revision) "
      "REFERENCES public.subscription_group_add_batches(tenant_id,id,device_id,generation,settings_revision) ON DELETE CASCADE",
    "ALTER TABLE public.subscription_group_add_items ALTER CONSTRAINT fk_subscription_group_add_item_context DEFERRABLE INITIALLY DEFERRED",
    "ALTER TABLE public.subscription_group_add_batches DROP CONSTRAINT fk_subscription_group_add_channel, "
      "ADD CONSTRAINT fk_subscription_group_add_channel FOREIGN KEY(tenant_id,channel_id) "
      "REFERENCES public.qr_channels(tenant_id,id) NOT VALID",
])
async def test_tenant_snapshot_identity_idempotency_and_receipt_foreign_keys_are_mandatory(isolated_settings, mutation):
    async with temporary_catalog_change(isolated_settings) as db:
        await db.execute(mutation)
        assert await db.fetchval(barrier.CONTRACT_QUERY) is False


@pytest.mark.parametrize("table,constraint", [
    (BATCH, "subscription_group_add_destination"),
    (BATCH, "subscription_group_add_batches_total_check"),
    (BATCH, "subscription_group_add_batch_terminal"),
    (ITEM, "subscription_group_add_items_phone_check"),
    (ITEM, "subscription_group_add_items_source_context_check"),
    (ITEM, "subscription_group_add_claim"),
    (ITEM, "subscription_group_add_item_terminal"),
    (ITEM, "subscription_group_add_dispatch"),
])
async def test_same_named_constraint_cannot_remove_a_reviewed_intent_or_dispatch_guard(isolated_settings, table, constraint):
    async with temporary_catalog_change(isolated_settings) as db:
        await db.execute(f"ALTER TABLE public.{table} DROP CONSTRAINT {constraint}, ADD CONSTRAINT {constraint} CHECK(true)")
        assert await db.fetchval(barrier.CONTRACT_QUERY) is False


@pytest.mark.parametrize("table,constraint,expression", [
    (BATCH, "subscription_group_add_batches_source_check", "source IN ('contacts','group','other')"),
    (BATCH, "subscription_group_add_batches_status_check", "status IN ('pending','running','completed','partial','unknown','cancelled','other')"),
    (ITEM, "subscription_group_add_items_status_check", "status IN ('pending','running','added','already_member','not_added','unknown','cancelled','other')"),
    (BATCH, "subscription_group_add_batches_source_check", "source IN ('contacts','group') OR source IS NOT NULL"),
    (BATCH, "subscription_group_add_batches_status_check", "status IN ('pending','running','completed','partial','unknown','cancelled') OR status IS NOT NULL"),
    (ITEM, "subscription_group_add_items_status_check", "status IN ('pending','running','added','already_member','not_added','unknown','cancelled') OR status IS NOT NULL"),
])
async def test_domains_cannot_be_broadened_while_retaining_expected_fragments(isolated_settings, table, constraint, expression):
    async with temporary_catalog_change(isolated_settings) as db:
        await db.execute(f"ALTER TABLE public.{table} DROP CONSTRAINT {constraint}, ADD CONSTRAINT {constraint} CHECK({expression})")
        assert await db.fetchval(barrier.CONTRACT_QUERY) is False


@pytest.mark.parametrize("definition", [
    "CREATE INDEX uq_subscription_group_add_active_device ON public.subscription_group_add_batches(tenant_id,device_id) WHERE status IN ('pending','running')",
    "CREATE UNIQUE INDEX uq_subscription_group_add_active_device ON public.subscription_group_add_batches(tenant_id,device_id) WHERE status IN ('pending','running') AND total>1",
    "CREATE UNIQUE INDEX uq_subscription_group_add_active_device ON public.subscription_group_add_batches(tenant_id,device_id) WHERE status IN ('pending','running','unknown')",
])
async def test_active_device_guard_requires_uniqueness_and_the_exact_admission_domain(isolated_settings, definition):
    async with temporary_catalog_change(isolated_settings) as db:
        await db.execute("DROP INDEX public.uq_subscription_group_add_active_device")
        await db.execute(definition)
        assert await db.fetchval(barrier.CONTRACT_QUERY) is False


@pytest.mark.parametrize("index", ["ix_subscription_group_add_due", "ix_subscription_group_add_item_next", "ix_subscription_group_add_item_unknown"])
async def test_dispatch_and_receipt_indexes_cannot_disappear(isolated_settings, index):
    async with temporary_catalog_change(isolated_settings) as db:
        await db.execute("DROP INDEX public." + index)
        assert await db.fetchval(barrier.CONTRACT_QUERY) is False


async def test_group_add_readiness_also_refuses_a_broken_published_direct_origin_contract(isolated_settings):
    async with temporary_catalog_change(isolated_settings) as db:
        await db.execute("ALTER TABLE public.subscription_bulk_requests DROP CONSTRAINT fk_subscription_bulk_request_origin")
        assert await db.fetchval(barrier.CONTRACT_QUERY) is False
