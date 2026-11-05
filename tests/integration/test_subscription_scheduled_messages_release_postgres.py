"""Exercise scheduled-message DDL/grants against disposable PostgreSQL catalogs."""
from contextlib import asynccontextmanager

import asyncpg
import pytest
from sqlalchemy.engine import make_url

from test_subscription_devices_release_postgres import isolated_settings
from scripts import backup_and_migrate_subscription_scheduled_messages as release
from scripts import backup_and_migrate_subscription_campaigns as previous_release
from scripts import wait_for_subscription_scheduled_messages_schema as barrier
from scripts import wait_for_subscription_campaigns_schema as previous_barrier

pytestmark = pytest.mark.integration
TABLES = ("subscription_schedules", "subscription_schedule_runs",
          "subscription_schedule_recipients", "subscription_schedule_requests")


def test_real_release_catalog_keeps_all_published_predecessors(isolated_settings):
    assert previous_release.schema_ready(isolated_settings.migration_url)
    assert release.schema_ready(isolated_settings.migration_url)


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
            has_any_column_privilege(current_user,'public.subscription_schedule_requests','SELECT') AS requests""")
        assert dict(row) == {"role": "app_runtime", "authenticated": "app_runtime", "readonly": "on",
            "revision": False, "effects": False, "requests": False}
    finally:
        db.terminate()


@pytest.mark.parametrize("table", TABLES)
async def test_private_schedule_tables_deny_runtime_even_with_tenant_set(isolated_settings, table):
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


@pytest.mark.parametrize('mutation', [
    'GRANT SELECT ON public.subscription_schedules TO app_runtime',
    'GRANT SELECT(phone) ON public.subscription_schedule_recipients TO app_runtime',
    'GRANT UPDATE ON public.subscription_schedule_requests TO app_support',
    'GRANT UPDATE(snapshot) ON public.subscription_schedule_requests TO app_support',
    'GRANT UPDATE(plan) ON public.subscription_schedule_recipients TO app_support',
    'GRANT UPDATE(origins) ON public.subscription_schedule_recipients TO app_support',
    'GRANT UPDATE(status) ON public.subscription_schedule_recipients TO app_support WITH GRANT OPTION',
    'REVOKE UPDATE(status) ON public.subscription_schedule_runs FROM app_support',
    'GRANT SELECT ON public.subscription_schedule_requests TO PUBLIC',
    'GRANT UPDATE(plan) ON public.subscription_schedule_recipients TO PUBLIC',
    'ALTER TABLE public.subscription_schedule_runs NO FORCE ROW LEVEL SECURITY',
    'ALTER TABLE public.subscription_schedules DISABLE ROW LEVEL SECURITY',
    'ALTER POLICY subscription_schedule_recipients_runtime ON public.subscription_schedule_recipients USING(true)',
    'CREATE POLICY schedule_unscoped_runtime ON public.subscription_schedules FOR SELECT TO app_runtime USING(true)',
    'ALTER POLICY subscription_schedule_requests_support ON public.subscription_schedule_requests WITH CHECK(false)',
    'ALTER TABLE public.subscription_schedule_requests ALTER COLUMN idempotency_key DROP NOT NULL',
    'ALTER TABLE public.subscription_schedule_recipients ALTER COLUMN plan_hash DROP NOT NULL',
    'ALTER TABLE public.subscription_schedule_runs ALTER COLUMN source_run_id TYPE text USING source_run_id::text',
])
async def test_drift_in_privileges_rls_or_required_schema_refuses_startup(isolated_settings, mutation):
    async with temporary_catalog_change(isolated_settings) as db:
        if 'source_run_id TYPE' in mutation:
            await db.execute('ALTER TABLE public.subscription_schedule_runs DROP CONSTRAINT fk_subscription_schedule_run_source, DROP CONSTRAINT subscription_schedule_run_origin')
        await db.execute(mutation)
        assert await db.fetchval(barrier.CONTRACT_QUERY) is False


@pytest.mark.parametrize('table,constraint', [(table, name) for table, name, _ in barrier.CHECKS
    if name not in {'subscription_device_events_kind_check', 'subscription_device_effects_type_check'}])
async def test_same_constraint_name_with_missing_guard_is_not_accepted(isolated_settings, table, constraint):
    async with temporary_catalog_change(isolated_settings) as db:
        await db.execute(f'ALTER TABLE public.{table} DROP CONSTRAINT {constraint}, ADD CONSTRAINT {constraint} CHECK(true)')
        assert await db.fetchval(barrier.CONTRACT_QUERY) is False


@pytest.mark.parametrize('mutation', [
    'ALTER TABLE public.subscription_schedule_requests DROP CONSTRAINT uq_subscription_schedule_request_key',
    'ALTER TABLE public.subscription_schedule_recipients DROP CONSTRAINT uq_subscription_schedule_recipient_phone',
    'ALTER TABLE public.subscription_schedule_recipients DROP CONSTRAINT uq_subscription_schedule_recipient_order',
    'ALTER TABLE public.subscription_schedule_runs DROP CONSTRAINT uq_subscription_schedule_run_occurrence',
    'ALTER TABLE public.subscription_device_effects DROP CONSTRAINT uq_subscription_device_effect_schedule_part',
    'ALTER TABLE public.subscription_schedule_recipients DROP CONSTRAINT fk_subscription_schedule_recipient_run, '
        'ADD CONSTRAINT fk_subscription_schedule_recipient_run FOREIGN KEY(run_id) REFERENCES public.subscription_schedule_runs(id)',
    'ALTER TABLE public.subscription_device_effects DROP CONSTRAINT fk_subscription_device_effect_schedule_recipient, '
        'ADD CONSTRAINT fk_subscription_device_effect_schedule_recipient FOREIGN KEY(tenant_id,schedule_recipient_id) '
        'REFERENCES public.subscription_schedule_recipients(tenant_id,id)',
    'DROP INDEX public.ix_subscription_schedule_run_due',
    'DROP INDEX public.ix_subscription_schedule_recipient_pending',
    'DROP INDEX public.ix_subscription_schedule_recipient_rate',
])
async def test_removed_uniqueness_scoping_or_dispatch_index_is_not_ready(isolated_settings, mutation):
    async with temporary_catalog_change(isolated_settings) as db:
        await db.execute(mutation)
        assert await db.fetchval(barrier.CONTRACT_QUERY) is False


async def test_previous_campaign_guard_is_required_by_composed_barrier(isolated_settings):
    async with temporary_catalog_change(isolated_settings) as db:
        await db.execute('ALTER TABLE public.subscription_device_effects DROP CONSTRAINT subscription_device_effect_campaign_scope')
        assert await db.fetchval(barrier.CONTRACT_QUERY) is False
