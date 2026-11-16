"""Exercise bulk-message DDL/grants against disposable PostgreSQL catalogs."""
from contextlib import asynccontextmanager

import asyncpg
import pytest
from sqlalchemy.engine import make_url
from sqlalchemy import create_engine, text

from test_subscription_devices_release_postgres import isolated_settings
from scripts import backup_and_migrate_subscription_bulk as release
from scripts import backup_and_migrate_subscription_scheduled_messages as previous_release
from scripts import wait_for_subscription_bulk_schema as barrier
from scripts import wait_for_subscription_scheduled_messages_schema as previous_barrier

pytestmark = pytest.mark.integration
TABLES = barrier.PRIVATE_TABLES


def test_real_release_catalog_keeps_all_published_predecessors(isolated_settings):
    assert previous_release.schema_ready(isolated_settings.migration_url)
    if not release.schema_ready(isolated_settings.migration_url):
        # Only schema metadata is reported. Diagnose the independent sections
        # and PostgreSQL's normalized CHECK definitions before any drift test.
        engine = create_engine(isolated_settings.migration_url)
        try:
            with engine.connect() as db:
                sections = {name: db.execute(text(getattr(barrier, name))).scalar()
                    for name in ("TABLE_CONTRACT", "COLUMN_CONTRACT", "KEY_CONTRACT",
                        "FOREIGN_KEY_CONTRACT", "CHECK_CONTRACT", "INDEX_CONTRACT", "RECIPIENT_UNIQUENESS_CONTRACT")}
                missing = []
                for table, name, fragments in barrier.CHECKS:
                    definition = db.execute(text("""SELECT lower(pg_get_constraintdef(oid)) FROM pg_constraint
                        WHERE conrelid=to_regclass(:table) AND conname=:name"""),
                        {"table": "public."+table, "name": name}).scalar()
                    absent = [fragment for fragment in fragments if not definition or fragment not in definition]
                    if absent:
                        missing.append({"constraint": name, "missing": absent, "definition": definition})
            pytest.fail(str({"sections": sections, "missing_check_fragments": missing}))
        finally:
            engine.dispose()


async def test_runtime_probe_is_readonly_and_never_needs_private_data(isolated_settings):
    assert await previous_barrier.probe_schema(isolated_settings.runtime, 3)
    assert await barrier.probe_schema(isolated_settings.runtime, 3)
    db = await asyncpg.connect(isolated_settings.runtime.dsn, timeout=3, command_timeout=3,
        server_settings={"default_transaction_read_only": "on", "search_path": "pg_catalog"})
    try:
        row = await db.fetchrow("""SELECT current_user AS role, session_user AS authenticated,
            current_setting('default_transaction_read_only') AS readonly,
            has_table_privilege(current_user,'public.alembic_version','SELECT') AS revision,
            has_any_column_privilege(current_user,'public.subscription_device_effects','SELECT') AS effects,
            has_any_column_privilege(current_user,'public.subscription_bulk_requests','SELECT') AS requests""")
        assert dict(row) == {"role": "app_runtime", "authenticated": "app_runtime", "readonly": "on",
            "revision": False, "effects": False, "requests": False}
    finally:
        db.terminate()


@pytest.mark.parametrize("table", TABLES)
async def test_private_bulk_tables_deny_runtime_even_with_tenant_set(isolated_settings, table):
    db = await asyncpg.connect(isolated_settings.runtime.dsn, timeout=3, command_timeout=3,
        server_settings={"default_transaction_read_only": "on", "search_path": "pg_catalog"})
    try:
        row = await db.fetchrow("""SELECT
            has_any_column_privilege('app_runtime',$1,'SELECT,INSERT,UPDATE,REFERENCES') AS runtime_columns,
            has_table_privilege('app_runtime',$1,'DELETE,TRUNCATE,TRIGGER') AS runtime_table,
            has_table_privilege('app_support',$1,'SELECT') AS support_read,
            has_table_privilege('app_support',$1,'INSERT') AS support_append,
            has_table_privilege('app_support',$1,'UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') AS support_general_write
        """, 'public.'+table)
        assert dict(row) == {"runtime_columns": False, "runtime_table": False, "support_read": True,
            "support_append": True, "support_general_write": False}
        await db.execute("SELECT set_config('app.tenant_id','11111111-1111-4111-8111-111111111111',false)")
        with pytest.raises(asyncpg.InsufficientPrivilegeError) as denied:
            await db.fetch('SELECT 1 FROM public.'+table+' LIMIT 1')
        assert denied.value.sqlstate == '42501'
    finally:
        db.terminate()


@asynccontextmanager
async def temporary_catalog_change(settings):
    parsed = make_url(settings.migration_url)
    db = await asyncpg.connect(parsed.set(drivername='postgresql').render_as_string(hide_password=False),
        timeout=3, command_timeout=12, server_settings={"search_path": "pg_catalog", "statement_timeout": "12000", "lock_timeout": "3000"})
    try:
        await db.execute('BEGIN')
        assert await db.fetchval(barrier.CONTRACT_QUERY) is True
        yield db
    finally:
        try:
            await db.execute('ROLLBACK')
        finally:
            db.terminate()


@pytest.mark.parametrize("mutation", [
    "GRANT SELECT ON public.subscription_bulk_batches TO app_runtime",
    "GRANT SELECT(phone) ON public.subscription_bulk_recipients TO app_runtime",
    "GRANT UPDATE ON public.subscription_bulk_requests TO app_support",
    "GRANT UPDATE(snapshot) ON public.subscription_bulk_batches TO app_support",
    "GRANT UPDATE(interval_seconds) ON public.subscription_bulk_batches TO app_support",
    "GRANT UPDATE(run_version) ON public.subscription_bulk_batches TO app_support",
    "GRANT UPDATE(context) ON public.subscription_bulk_recipients TO app_support",
    "GRANT UPDATE(plan) ON public.subscription_bulk_recipients TO app_support",
    "GRANT UPDATE(origins) ON public.subscription_bulk_recipients TO app_support",
    "GRANT UPDATE(manual_consent) ON public.subscription_bulk_recipients TO app_support",
    "GRANT UPDATE(status) ON public.subscription_bulk_recipients TO app_support WITH GRANT OPTION",
    "REVOKE UPDATE(status) ON public.subscription_bulk_batches FROM app_support",
    "GRANT SELECT ON public.subscription_bulk_requests TO PUBLIC",
    "GRANT UPDATE(plan) ON public.subscription_bulk_recipients TO PUBLIC",
    "ALTER TABLE public.subscription_bulk_batches NO FORCE ROW LEVEL SECURITY",
    "ALTER TABLE public.subscription_bulk_recipients DISABLE ROW LEVEL SECURITY",
    "ALTER POLICY subscription_bulk_recipients_runtime ON public.subscription_bulk_recipients USING(true)",
    "CREATE POLICY bulk_unscoped_runtime ON public.subscription_bulk_batches FOR SELECT TO app_runtime USING(true)",
    "ALTER POLICY subscription_bulk_requests_support ON public.subscription_bulk_requests WITH CHECK(false)",
    "ALTER TABLE public.subscription_bulk_requests ALTER COLUMN idempotency_key DROP NOT NULL",
    "ALTER TABLE public.subscription_bulk_recipients ALTER COLUMN context DROP NOT NULL",
    "ALTER TABLE public.subscription_bulk_recipients ALTER COLUMN plan_hash DROP NOT NULL",
    "GRANT SELECT ON public.customers TO app_support",
    "DROP POLICY customers_subscription_schedule_support ON public.customers",
])
async def test_privilege_rls_or_required_column_drift_refuses_startup(isolated_settings, mutation):
    async with temporary_catalog_change(isolated_settings) as db:
        await db.execute(mutation)
        assert await db.fetchval(barrier.CONTRACT_QUERY) is False


@pytest.mark.parametrize("table,constraint", [(table, name) for table, name, _ in barrier.CHECKS
    if name not in {"subscription_device_events_kind_check", "subscription_device_effects_type_check"}])
async def test_same_named_constraint_cannot_drop_a_reviewed_guard(isolated_settings, table, constraint):
    async with temporary_catalog_change(isolated_settings) as db:
        await db.execute(f"ALTER TABLE public.{table} DROP CONSTRAINT {constraint}, ADD CONSTRAINT {constraint} CHECK(true)")
        assert await db.fetchval(barrier.CONTRACT_QUERY) is False


@pytest.mark.parametrize("mutation", [
    "ALTER TABLE public.subscription_bulk_requests DROP CONSTRAINT uq_subscription_bulk_request_key",
    "ALTER TABLE public.subscription_bulk_recipients DROP CONSTRAINT uq_subscription_bulk_recipient_key",
    "ALTER TABLE public.subscription_bulk_recipients DROP CONSTRAINT uq_subscription_bulk_recipient_order",
    "ALTER TABLE public.subscription_device_effects DROP CONSTRAINT uq_subscription_device_effect_bulk_part",
    "ALTER TABLE public.subscription_bulk_recipients DROP CONSTRAINT fk_subscription_bulk_recipient_batch, "
      "ADD CONSTRAINT fk_subscription_bulk_recipient_batch FOREIGN KEY(bulk_id) REFERENCES public.subscription_bulk_batches(id)",
    "ALTER TABLE public.subscription_bulk_recipients DROP CONSTRAINT fk_subscription_bulk_recipient_version",
    "ALTER TABLE public.subscription_device_effects DROP CONSTRAINT fk_subscription_device_effect_bulk_recipient, "
      "ADD CONSTRAINT fk_subscription_device_effect_bulk_recipient FOREIGN KEY(tenant_id,bulk_recipient_id) "
      "REFERENCES public.subscription_bulk_recipients(tenant_id,id)",
    "DROP INDEX public.ix_subscription_bulk_pending",
    "DROP INDEX public.ix_subscription_bulk_recipient_pending",
    "DROP INDEX public.ix_subscription_bulk_recipient_rate",
    "DROP INDEX public.ix_subscription_bulk_recipient_history",
])
async def test_missing_keys_scoping_or_dispatch_indexes_refuse_startup(isolated_settings, mutation):
    async with temporary_catalog_change(isolated_settings) as db:
        await db.execute(mutation)
        assert await db.fetchval(barrier.CONTRACT_QUERY) is False


@pytest.mark.parametrize("definition", ["tenant_id,bulk_id,phone", "tenant_id,bulk_id,lower(phone::text)"])
async def test_extra_phone_uniqueness_must_not_break_intentional_duplicate_rows(isolated_settings, definition):
    async with temporary_catalog_change(isolated_settings) as db:
        await db.execute("CREATE UNIQUE INDEX bulk_wrong_phone_uniqueness ON public.subscription_bulk_recipients ("+definition+")")
        assert await db.fetchval(barrier.CONTRACT_QUERY) is False


async def test_published_schedule_scope_remains_mandatory(isolated_settings):
    async with temporary_catalog_change(isolated_settings) as db:
        await db.execute("ALTER TABLE public.subscription_device_effects DROP CONSTRAINT subscription_device_effect_schedule_scope")
        assert await db.fetchval(barrier.CONTRACT_QUERY) is False
