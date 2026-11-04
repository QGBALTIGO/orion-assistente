"""Read the real migrated PostgreSQL catalogs using separate restricted roles."""
from dataclasses import dataclass, field
import os

import asyncpg
import pytest
from sqlalchemy.engine import make_url

from scripts import backup_and_migrate_subscription_devices as release
from scripts import wait_for_subscription_devices_schema as barrier

pytestmark = pytest.mark.integration


@dataclass(frozen=True)
class IsolatedSettings:
    migration_url: str = field(repr=False)
    runtime: barrier.Settings = field(repr=False)


@pytest.fixture
def isolated_settings():
    migration_url, runtime_url = os.getenv("MIGRATION_DATABASE_URL"), os.getenv("APP_DATABASE_URL")
    if not migration_url or not runtime_url:
        if os.getenv("REQUIRE_POSTGRES_TESTS") == "1":
            pytest.fail("Mandatory isolated PostgreSQL role URLs are missing")
        pytest.skip("Requires migrated loopback PostgreSQL and separate roles")
    for raw, role in ((migration_url, "app_migrator"), (runtime_url, "app_runtime")):
        parsed = make_url(raw)
        if parsed.host not in {"127.0.0.1", "localhost", "::1"} or parsed.username != role:
            pytest.fail("Release tests require the disposable loopback database roles")
    return IsolatedSettings(migration_url, barrier.Settings.from_environment())


def test_release_contract_checks_actual_indexes_grants_ownership_and_foreign_keys(isolated_settings):
    assert release.schema_ready(isolated_settings.migration_url)


async def test_runtime_readiness_does_not_require_secrets_or_message_bodies(isolated_settings):
    assert await barrier.probe_schema(isolated_settings.runtime, 3)
    db = await asyncpg.connect(isolated_settings.runtime.dsn, timeout=3, command_timeout=3,
        server_settings={"default_transaction_read_only": "on", "search_path": "pg_catalog"})
    try:
        result = await db.fetchrow("""SELECT
          current_user AS role, session_user AS authenticated_role,
          current_setting('default_transaction_read_only') AS readonly,
          has_column_privilege(current_user,'public.subscription_devices','name','SELECT') AS metadata,
          has_column_privilege(current_user,'public.subscription_devices','settings','SELECT') AS settings,
          has_column_privilege(current_user,'public.subscription_devices','proxy_secret_encrypted','SELECT') AS secret,
          has_column_privilege(current_user,'public.subscription_device_operations','request_payload','SELECT') AS payload,
          has_any_column_privilege(current_user,'public.subscription_device_events','SELECT') AS events,
          has_any_column_privilege(current_user,'public.subscription_device_effects','SELECT') AS effects,
          has_table_privilege(current_user,'public.subscription_device_audio_usage','SELECT') AS usage,
          has_any_column_privilege(current_user,'public.subscription_devices','UPDATE') AS write,
          has_table_privilege(current_user,'public.subscription_devices','DELETE') AS delete,
          has_table_privilege(current_user,'public.alembic_version','SELECT') AS revision
        """)
        assert dict(result) == {"role": "app_runtime", "authenticated_role": "app_runtime", "readonly": "on",
            "metadata": True, "settings": False, "secret": False, "payload": False,
            "events": False, "effects": False, "usage": True, "write": False, "delete": False, "revision": False}
    finally:
        db.terminate()


async def test_missing_unique_guard_is_not_ready_without_mutating_schema(isolated_settings):
    db = await asyncpg.connect(isolated_settings.runtime.dsn, timeout=3, command_timeout=3,
        server_settings={"default_transaction_read_only": "on", "search_path": "pg_catalog"})
    try:
        query = barrier.SCHEMA_QUERY.replace("uq_subscription_device_active_tenant", "missing_subscription_unique_guard")
        assert query != barrier.SCHEMA_QUERY
        result = await db.fetchrow(query)
        assert result["schema_ready"] is False
    finally:
        db.terminate()
