"""Read the real migrated PostgreSQL catalogs using separate restricted roles."""
from dataclasses import dataclass, field
import os

import asyncpg
import pytest
from sqlalchemy.engine import make_url

from scripts import backup_and_migrate_subscription_chatbot as release
from scripts import wait_for_subscription_chatbot_schema as barrier

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


async def test_runtime_sees_metadata_but_not_rules_files_backups_or_private_effects(isolated_settings):
    assert await barrier.probe_schema(isolated_settings.runtime, 3)
    db = await asyncpg.connect(isolated_settings.runtime.dsn, timeout=3, command_timeout=3,
        server_settings={"default_transaction_read_only": "on", "search_path": "pg_catalog"})
    try:
        for table in ("assets", "templates", "runs", "cooldowns", "selections", "previews"):
            assert not await db.fetchval("SELECT has_any_column_privilege(current_user,$1,'SELECT')",
                                         "public.subscription_chatbot_" + table)
        for table, column in (("rules", "response"), ("rules", "keyword"), ("jobs", "payload"),
                              ("jobs", "result"), ("backups", "snapshot")):
            assert not await db.fetchval("SELECT has_column_privilege(current_user,$1,$2,'SELECT')",
                                         "public.subscription_chatbot_" + table, column)
        for table, column in (("state", "revision"), ("rules", "enabled"), ("jobs", "status"),
                              ("backups", "rules_count")):
            assert await db.fetchval("SELECT has_column_privilege(current_user,$1,$2,'SELECT')",
                                     "public.subscription_chatbot_" + table, column)
        assert not await db.fetchval("SELECT has_column_privilege(current_user,'public.subscription_device_effects','result','SELECT')")
    finally:
        db.terminate()


@pytest.mark.parametrize("old,new", [
    ("uq_subscription_chatbot_daily_backup", "missing_daily_snapshot_guard"),
    ("subscription_device_effect_chatbot_scope", "missing_effect_scope_guard"),
    ("('subscription_chatbot_templates','subscription_chatbot_assets',2)",
     "('subscription_chatbot_templates','missing_asset_parent',2)"),
])
async def test_missing_catalog_guard_fails_closed_without_any_ddl(isolated_settings, old, new):
    db = await asyncpg.connect(isolated_settings.runtime.dsn, timeout=3, command_timeout=3,
        server_settings={"default_transaction_read_only": "on", "search_path": "pg_catalog"})
    try:
        query = barrier.SCHEMA_QUERY.replace(old, new)
        assert query != barrier.SCHEMA_QUERY
        assert (await db.fetchrow(query))["schema_ready"] is False
    finally:
        db.terminate()
