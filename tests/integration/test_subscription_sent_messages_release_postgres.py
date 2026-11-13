"""Check deployed history constraints using the actual restricted PG roles."""
import asyncpg
import pytest

from test_subscription_devices_release_postgres import isolated_settings
from scripts import backup_and_migrate_subscription_sent_messages as release
from scripts import wait_for_subscription_sent_messages_schema as barrier

pytestmark = pytest.mark.integration


def test_history_release_validates_real_rls_private_columns_keys_and_indexes(isolated_settings):
    assert release.schema_ready(isolated_settings.migration_url)


async def test_runtime_readiness_reads_only_catalogs_and_has_no_history_privileges(isolated_settings):
    assert await barrier.probe_schema(isolated_settings.runtime, 3)
    db = await asyncpg.connect(isolated_settings.runtime.dsn, timeout=3, command_timeout=3,
        server_settings={"default_transaction_read_only": "on", "search_path": "pg_catalog"})
    try:
        result = await db.fetchrow("""SELECT
          current_user AS role, current_setting('default_transaction_read_only') AS readonly,
          has_any_column_privilege(current_user,'public.subscription_sent_message_resends','SELECT') AS history,
          has_any_column_privilege(current_user,'public.subscription_sent_message_resends','INSERT') AS can_insert,
          has_any_column_privilege(current_user,'public.subscription_sent_message_resends','UPDATE') AS can_update,
          has_table_privilege(current_user,'public.subscription_sent_message_resends','DELETE') AS can_delete,
          has_column_privilege(current_user,'public.subscription_device_effects','audio_duration_seconds','SELECT') AS duration,
          has_column_privilege(current_user,'public.subscription_device_effects','audio_completed_at','SELECT') AS completion,
          has_table_privilege('app_support','public.subscription_sent_message_resends','SELECT') AS support_read,
          has_table_privilege('app_support','public.subscription_sent_message_resends','INSERT') AS support_append,
          has_any_column_privilege('app_support','public.subscription_sent_message_resends','UPDATE') AS support_update,
          has_table_privilege('app_support','public.subscription_sent_message_resends','DELETE') AS support_delete
        """)
        assert dict(result) == {"role": "app_runtime", "readonly": "on", "history": False,
            "can_insert": False, "can_update": False, "can_delete": False, "duration": False,
            "completion": False, "support_read": True, "support_append": True,
            "support_update": False, "support_delete": False}
    finally:
        db.terminate()


@pytest.mark.parametrize("guard", ["uq_subscription_sent_resend_source",
    "uq_subscription_sent_resend_key", "subscription_device_effect_audio_completion_check",
    "ix_subscription_sent_history", "ix_subscription_sent_resend_root"])
async def test_missing_history_guard_is_not_ready_without_ddl(isolated_settings, guard):
    db = await asyncpg.connect(isolated_settings.runtime.dsn, timeout=3, command_timeout=3,
        server_settings={"default_transaction_read_only": "on", "search_path": "pg_catalog"})
    try:
        query = barrier.SCHEMA_QUERY.replace(guard, "missing_history_guard")
        assert query != barrier.SCHEMA_QUERY
        assert (await db.fetchrow(query))["schema_ready"] is False
    finally:
        db.terminate()
