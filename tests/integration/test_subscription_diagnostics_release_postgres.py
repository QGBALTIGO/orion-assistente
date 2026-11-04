"""Validate diagnostic startup guards using the actual restricted PostgreSQL roles."""
import asyncpg
import pytest

from test_subscription_devices_release_postgres import isolated_settings
from scripts import backup_and_migrate_subscription_diagnostics as release
from scripts import wait_for_subscription_diagnostics_schema as barrier

pytestmark = pytest.mark.integration


def test_diagnostic_release_validates_real_rls_keys_claims_and_immutable_preview(isolated_settings):
    assert release.schema_ready(isolated_settings.migration_url)


async def test_runtime_reads_only_catalogs_while_support_updates_only_lifecycle(isolated_settings):
    assert await barrier.probe_schema(isolated_settings.runtime, 3)
    db = await asyncpg.connect(isolated_settings.runtime.dsn, timeout=3, command_timeout=3,
        server_settings={"default_transaction_read_only": "on", "search_path": "pg_catalog"})
    try:
        result = await db.fetchrow("""SELECT
          current_user AS role, current_setting('default_transaction_read_only') AS readonly,
          has_any_column_privilege(current_user,'public.subscription_diagnostic_requests','SELECT') AS ledger,
          has_any_column_privilege(current_user,'public.subscription_diagnostic_requests','INSERT') AS can_insert,
          has_any_column_privilege(current_user,'public.subscription_diagnostic_requests','UPDATE') AS can_update,
          has_table_privilege(current_user,'public.subscription_diagnostic_requests','DELETE') AS can_delete,
          has_table_privilege('app_support','public.subscription_diagnostic_requests','SELECT') AS support_read,
          has_table_privilege('app_support','public.subscription_diagnostic_requests','INSERT') AS support_append,
          has_table_privilege('app_support','public.subscription_diagnostic_requests','UPDATE') AS support_table_update,
          has_column_privilege('app_support','public.subscription_diagnostic_requests','snapshot','UPDATE') AS support_snapshot_update,
          has_column_privilege('app_support','public.subscription_diagnostic_requests','confirmation_version','UPDATE') AS support_confirmation_update,
          has_column_privilege('app_support','public.subscription_diagnostic_requests','idempotency_key','UPDATE') AS support_key_update,
          has_column_privilege('app_support','public.subscription_diagnostic_requests','status','UPDATE') AS support_lifecycle_update,
          has_column_privilege('app_support','public.subscription_diagnostic_requests','claim_fence','UPDATE') AS support_claim_update,
          has_table_privilege('app_support','public.subscription_diagnostic_requests','DELETE') AS support_delete
        """)
        assert dict(result) == {"role": "app_runtime", "readonly": "on", "ledger": False,
            "can_insert": False, "can_update": False, "can_delete": False,
            "support_read": True, "support_append": True, "support_table_update": False,
            "support_snapshot_update": False, "support_confirmation_update": False,
            "support_key_update": False, "support_lifecycle_update": True,
            "support_claim_update": True, "support_delete": False}
    finally:
        db.terminate()


@pytest.mark.parametrize("guard", ["uq_subscription_diagnostic_key", "uq_subscription_diagnostic_effect",
    "fk_subscription_diagnostic_device", "fk_subscription_diagnostic_actor", "fk_subscription_diagnostic_effect",
    "subscription_diagnostic_expiry_check", "subscription_diagnostic_kind_check",
    "subscription_diagnostic_claim_check", "subscription_diagnostic_result_check",
    "ix_subscription_diagnostic_active", "ix_subscription_diagnostic_device"])
async def test_missing_diagnostic_guard_is_not_ready_without_altering_schema(isolated_settings, guard):
    db = await asyncpg.connect(isolated_settings.runtime.dsn, timeout=3, command_timeout=3,
        server_settings={"default_transaction_read_only": "on", "search_path": "pg_catalog"})
    try:
        query = barrier.SCHEMA_QUERY.replace(guard, "missing_diagnostic_guard")
        assert query != barrier.SCHEMA_QUERY
        assert (await db.fetchrow(query))["schema_ready"] is False
    finally:
        db.terminate()
