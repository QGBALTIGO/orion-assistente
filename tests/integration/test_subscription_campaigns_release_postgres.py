"""Validate the 0068 release contract on disposable PostgreSQL, never production."""
from contextlib import asynccontextmanager

import asyncpg
import pytest
from sqlalchemy.engine import make_url

from test_subscription_devices_release_postgres import isolated_settings
from scripts import backup_and_migrate_subscription_campaigns as release
from scripts import backup_and_migrate_subscription_diagnostics as previous_release
from scripts import wait_for_subscription_campaigns_schema as barrier
from scripts import wait_for_subscription_diagnostics_schema as previous_barrier

pytestmark = pytest.mark.integration

TABLES = (
    "subscription_campaign_groups", "subscription_campaign_members", "subscription_campaign_permissions",
    "subscription_campaigns", "subscription_campaign_sources", "subscription_campaign_recipients",
    "subscription_campaign_requests",
)


def test_release_validates_published_predecessors_and_real_campaign_catalog(isolated_settings):
    assert previous_release.schema_ready(isolated_settings.migration_url)
    assert release.schema_ready(isolated_settings.migration_url)


async def test_runtime_schema_barrier_needs_only_restricted_catalog_access(isolated_settings):
    assert await previous_barrier.probe_schema(isolated_settings.runtime, 3)
    assert await barrier.probe_schema(isolated_settings.runtime, 3)
    db = await asyncpg.connect(isolated_settings.runtime.dsn, timeout=3, command_timeout=3,
        server_settings={"default_transaction_read_only": "on", "search_path": "pg_catalog"})
    try:
        row = await db.fetchrow("""SELECT current_user AS role, session_user AS authenticated,
          current_setting('default_transaction_read_only') AS readonly,
          has_table_privilege(current_user,'public.alembic_version','SELECT') AS revision,
          has_column_privilege(current_user,'public.appointments','booking_mode','SELECT') AS agenda,
          has_any_column_privilege(current_user,'public.subscription_diagnostic_requests','SELECT') AS diagnostics,
          has_any_column_privilege(current_user,'public.subscription_device_effects','SELECT') AS effects""")
        assert dict(row) == {"role": "app_runtime", "authenticated": "app_runtime", "readonly": "on",
                             "revision": False, "agenda": True, "diagnostics": False, "effects": False}
    finally:
        db.terminate()


@pytest.mark.parametrize("table", TABLES)
async def test_runtime_cannot_read_or_mutate_any_private_campaign_table(isolated_settings, table):
    db = await asyncpg.connect(isolated_settings.runtime.dsn, timeout=3, command_timeout=3,
        server_settings={"default_transaction_read_only": "on", "search_path": "pg_catalog"})
    try:
        row = await db.fetchrow("""SELECT
          has_any_column_privilege('app_runtime',$1,'SELECT,INSERT,UPDATE,REFERENCES') AS runtime_columns,
          has_table_privilege('app_runtime',$1,'DELETE,TRUNCATE,TRIGGER') AS runtime_table,
          has_table_privilege('app_support',$1,'SELECT') AS support_read,
          has_table_privilege('app_support',$1,'INSERT') AS support_append,
          has_table_privilege('app_support',$1,'UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') AS support_general_write
        """, "public." + table)
        assert dict(row) == {"runtime_columns": False, "runtime_table": False,
                             "support_read": True, "support_append": True, "support_general_write": False}
        # RLS policies do not accidentally grant access, even with a tenant set.
        await db.execute("SELECT set_config('app.tenant_id','11111111-1111-4111-8111-111111111111',false)")
        with pytest.raises(asyncpg.InsufficientPrivilegeError) as denied:
            await db.fetch("SELECT 1 FROM public." + table + " LIMIT 1")
        assert denied.value.sqlstate == "42501"
    finally:
        db.terminate()


async def test_support_cannot_change_frozen_audiences_and_runtime_cannot_access_effect_scope(isolated_settings):
    db = await asyncpg.connect(isolated_settings.runtime.dsn, timeout=3, command_timeout=3,
        server_settings={"default_transaction_read_only": "on", "search_path": "pg_catalog"})
    try:
        row = await db.fetchrow("""SELECT
          has_any_column_privilege('app_support','public.subscription_campaign_sources','UPDATE') AS sources,
          has_column_privilege('app_support','public.subscription_campaign_recipients','phone','UPDATE') AS phone,
          has_column_privilege('app_support','public.subscription_campaign_recipients','plan','UPDATE') AS plan,
          has_column_privilege('app_support','public.subscription_campaign_recipients','permission_version','UPDATE') AS permission,
          has_column_privilege('app_support','public.subscription_campaign_recipients','status','UPDATE') AS status,
          has_column_privilege('app_support','public.subscription_campaign_requests','snapshot','UPDATE') AS snapshot,
          has_column_privilege('app_support','public.subscription_campaign_requests','review_version','UPDATE') AS review,
          has_column_privilege('app_support','public.subscription_campaign_requests','idempotency_key','UPDATE') AS key,
          has_column_privilege('app_support','public.subscription_campaign_requests','result','UPDATE') AS result,
          has_column_privilege('app_runtime','public.subscription_device_effects','campaign_id','SELECT,UPDATE') AS effect_campaign,
          has_column_privilege('app_runtime','public.subscription_device_effects','campaign_recipient_id','SELECT,UPDATE') AS effect_recipient,
          has_column_privilege('app_runtime','public.subscription_device_effects','campaign_part_index','SELECT,UPDATE') AS effect_part
        """)
        assert dict(row) == {"sources": False, "phone": False, "plan": False, "permission": False,
            "status": True, "snapshot": False, "review": False, "key": False, "result": True,
            "effect_campaign": False, "effect_recipient": False, "effect_part": False}
    finally:
        db.terminate()


@asynccontextmanager
async def temporary_catalog_change(settings):
    """The existing fixture rejects every host except disposable loopback PG."""
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
    "GRANT SELECT ON public.subscription_campaign_groups TO app_runtime",
    "GRANT SELECT(phone) ON public.subscription_campaign_members TO app_runtime",
    "GRANT UPDATE ON public.subscription_campaign_requests TO app_support",
    "GRANT UPDATE(snapshot) ON public.subscription_campaign_requests TO app_support",
    "GRANT UPDATE(member_count) ON public.subscription_campaign_sources TO app_support",
    "GRANT UPDATE(status) ON public.subscription_campaign_recipients TO app_support WITH GRANT OPTION",
    "REVOKE UPDATE(status) ON public.subscription_campaign_recipients FROM app_support",
    "GRANT SELECT ON public.subscription_campaign_permissions TO PUBLIC",
    "GRANT UPDATE(plan) ON public.subscription_campaign_recipients TO PUBLIC",
    "ALTER TABLE public.subscription_campaign_requests NO FORCE ROW LEVEL SECURITY",
    "ALTER TABLE public.subscription_campaign_groups DISABLE ROW LEVEL SECURITY",
    "ALTER POLICY subscription_campaign_members_runtime ON public.subscription_campaign_members USING(true)",
    "CREATE POLICY campaign_unscoped_runtime ON public.subscription_campaigns FOR SELECT TO app_runtime USING(true)",
    "ALTER POLICY subscription_campaign_sources_support ON public.subscription_campaign_sources WITH CHECK(false)",
    "ALTER TABLE public.subscription_campaign_requests ALTER COLUMN idempotency_key DROP NOT NULL",
])
async def test_release_rejects_real_permission_rls_and_required_column_drift(isolated_settings, mutation):
    async with temporary_catalog_change(isolated_settings) as db:
        await db.execute(mutation)
        assert await db.fetchval(barrier.CONTRACT_QUERY) is False


@pytest.mark.parametrize("table,constraint", [
    ("subscription_campaign_groups", "subscription_campaign_group_values"),
    ("subscription_campaign_members", "subscription_campaign_member_values"),
    ("subscription_campaign_permissions", "subscription_campaign_permission_values"),
    ("subscription_campaigns", "subscription_campaign_values"),
    ("subscription_campaigns", "subscription_campaign_snapshot"),
    ("subscription_campaigns", "subscription_campaign_execution"),
    ("subscription_campaign_sources", "subscription_campaign_source_values"),
    ("subscription_campaign_recipients", "subscription_campaign_recipient_values"),
    ("subscription_campaign_recipients", "subscription_campaign_recipient_terminal"),
    ("subscription_campaign_recipients", "subscription_campaign_recipient_admission"),
    ("subscription_campaign_requests", "subscription_campaign_request_values"),
    ("subscription_campaign_requests", "subscription_campaign_request_review"),
    ("subscription_device_effects", "subscription_device_effect_campaign_scope"),
])
async def test_reusing_constraint_name_with_weakened_check_cannot_pass_release(isolated_settings, table, constraint):
    async with temporary_catalog_change(isolated_settings) as db:
        await db.execute(f"ALTER TABLE public.{table} DROP CONSTRAINT {constraint}, "
                         f"ADD CONSTRAINT {constraint} CHECK(true)")
        assert await db.fetchval(barrier.CONTRACT_QUERY) is False


@pytest.mark.parametrize("mutation", [
    "ALTER TABLE public.subscription_campaign_recipients DROP CONSTRAINT uq_subscription_campaign_recipient_phone",
    "ALTER TABLE public.subscription_campaign_recipients DROP CONSTRAINT uq_subscription_campaign_recipient_order",
    "ALTER TABLE public.subscription_campaign_requests DROP CONSTRAINT uq_subscription_campaign_request_key",
    "ALTER TABLE public.subscription_device_effects DROP CONSTRAINT uq_subscription_device_effect_campaign_part",
    "ALTER TABLE public.subscription_campaign_recipients DROP CONSTRAINT fk_subscription_campaign_recipient_campaign, "
        "ADD CONSTRAINT fk_subscription_campaign_recipient_campaign FOREIGN KEY(campaign_id) REFERENCES public.subscription_campaigns(id)",
    "ALTER TABLE public.subscription_device_effects DROP CONSTRAINT fk_subscription_device_effect_campaign_recipient, "
        "ADD CONSTRAINT fk_subscription_device_effect_campaign_recipient FOREIGN KEY(tenant_id,campaign_recipient_id) "
        "REFERENCES public.subscription_campaign_recipients(tenant_id,id)",
    "DROP INDEX public.ix_subscription_campaign_due",
    "DROP INDEX public.ix_subscription_campaign_recipient_rate",
    "DROP INDEX public.ix_subscription_campaign_recipient_pending",
])
async def test_deduplication_tenant_binding_and_scheduler_indexes_are_required(isolated_settings, mutation):
    async with temporary_catalog_change(isolated_settings) as db:
        await db.execute(mutation)
        assert await db.fetchval(barrier.CONTRACT_QUERY) is False


@pytest.mark.parametrize("guard", ["ck_appointment_booking_mode", "appointments_no_overlap",
                                  "subscription_diagnostic_claim_check", "uq_subscription_diagnostic_key"])
async def test_campaign_barrier_still_requires_published_agenda_and_diagnostics(isolated_settings, guard):
    db = await asyncpg.connect(isolated_settings.runtime.dsn, timeout=3, command_timeout=3,
        server_settings={"default_transaction_read_only": "on", "search_path": "pg_catalog"})
    try:
        query = barrier.SCHEMA_QUERY.replace(guard, "missing_campaign_predecessor_guard")
        assert query != barrier.SCHEMA_QUERY
        assert (await db.fetchrow(query))["schema_ready"] is False
    finally:
        db.terminate()
