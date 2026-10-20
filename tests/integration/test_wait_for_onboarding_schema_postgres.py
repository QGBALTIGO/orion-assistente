"""Real app_runtime metadata query; no fixture rows, DDL or public function calls."""
import os

import asyncpg
import pytest

from scripts import wait_for_onboarding_schema as gate

pytestmark = pytest.mark.integration


@pytest.fixture
def runtime_settings():
    if not os.getenv("APP_DATABASE_URL"):
        if os.getenv("REQUIRE_POSTGRES_TESTS") == "1": pytest.fail("Mandatory runtime PostgreSQL URL missing")
        pytest.skip("Real migrated PostgreSQL runtime connection required")
    return gate.Settings.from_environment()


async def test_actual_runtime_metadata_is_ready_without_access_to_revision_or_private_rows(runtime_settings):
    assert await gate.probe_schema(runtime_settings, 3)
    connection = await asyncpg.connect(runtime_settings.dsn, timeout=3, command_timeout=3,
        server_settings={"default_transaction_read_only": "on", "search_path": "pg_catalog"})
    try:
        privileges = await connection.fetchrow("""SELECT
            pg_catalog.has_table_privilege(current_user,'public.alembic_version','SELECT') AS revision_rows,
            pg_catalog.has_table_privilege(current_user,'public.panel_accounts','SELECT') AS accounts,
            pg_catalog.has_column_privilege(current_user,'public.qr_channels','credential_encrypted','SELECT') AS secret,
            current_setting('default_transaction_read_only') AS readonly""")
        assert dict(privileges) == {"revision_rows": False, "accounts": False, "secret": False, "readonly": "on"}
    finally:
        connection.terminate()


async def test_every_missing_table_or_function_marker_fails_real_catalog_check_without_ddl(runtime_settings):
    connection = await asyncpg.connect(runtime_settings.dsn, timeout=3, command_timeout=3,
        server_settings={"default_transaction_read_only": "on", "search_path": "pg_catalog"})
    try:
        # Substitute only a required metadata identifier, never alter shared CI
        # schema. This checks the actual SQL's missing-object/NULL semantics.
        for index in range(len(gate.TABLES)):
            required = list(gate.TABLES); required[index] = "orion_missing_qualification_table"
            row = await connection.fetchrow(gate.SCHEMA_QUERY, required, list(gate.FUNCTIONS))
            assert row["tables_ready"] is False and row["functions_ready"] is True
        for index in range(len(gate.FUNCTIONS)):
            required = list(gate.FUNCTIONS); required[index] = "public.orion_missing_qualification_function(uuid)"
            row = await connection.fetchrow(gate.SCHEMA_QUERY, list(gate.TABLES), required)
            assert row["functions_ready"] is False and row["tables_ready"] is True
    finally:
        connection.terminate()
