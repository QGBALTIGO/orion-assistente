"""Actual release/runtime catalog checks on disposable, migrated PostgreSQL.

No backup, migration, provider request or application-data write is performed by
these tests. The dedicated workflow creates its database and roles beforehand.
"""
from dataclasses import dataclass, field
import os

import asyncpg
import pytest
from sqlalchemy.engine import make_url

from scripts import backup_and_migrate_single_whatsapp as release
from scripts import wait_for_single_whatsapp_schema as barrier

pytestmark = pytest.mark.integration


@dataclass(frozen=True)
class IsolatedSettings:
    migration_url: str = field(repr=False)
    runtime: barrier.Settings = field(repr=False)


@pytest.fixture
def isolated_settings():
    migration_url, runtime_url = os.getenv('MIGRATION_DATABASE_URL'), os.getenv('APP_DATABASE_URL')
    if not migration_url or not runtime_url:
        if os.getenv('REQUIRE_POSTGRES_TESTS') == '1':
            pytest.fail('Mandatory isolated PostgreSQL role URLs are missing')
        pytest.skip('Requires migrated loopback PostgreSQL with separate roles')
    for raw, role in ((migration_url, 'app_migrator'), (runtime_url, 'app_runtime')):
        parsed = make_url(raw)
        if parsed.host not in {'127.0.0.1', 'localhost', '::1'} or parsed.username != role:
            pytest.fail('Release qualification requires loopback PostgreSQL and its dedicated roles')
    return IsolatedSettings(migration_url, barrier.Settings.from_environment())


def test_release_verifier_accepts_actual_binding_policy_guards_and_grants(isolated_settings):
    # Do not invoke release.main: its backup/migration path is a separate unit
    # contract. This function performs catalog reads under read-only sessions.
    assert release.schema_contract_ready(isolated_settings.migration_url)


async def test_runtime_barrier_queries_real_catalogs_without_private_row_privileges(isolated_settings):
    assert await barrier.probe_schema(isolated_settings.runtime, 3)
    connection = await asyncpg.connect(isolated_settings.runtime.dsn, timeout=3, command_timeout=3,
        server_settings={'default_transaction_read_only': 'on', 'search_path': 'pg_catalog'})
    try:
        row = await connection.fetchrow("""SELECT current_user AS role, session_user AS authenticated_role,
            current_setting('default_transaction_read_only') AS readonly,
            pg_catalog.has_column_privilege(current_user,'public.tenants','whatsapp_channel_id','SELECT') AS pointer_read,
            pg_catalog.has_column_privilege(current_user,'public.tenants','whatsapp_channel_id','UPDATE') AS pointer_write,
            pg_catalog.has_column_privilege(current_user,'public.qr_channels','credential_encrypted','SELECT') AS secret,
            pg_catalog.has_column_privilege(current_user,'public.qr_channels','allowed_senders','SELECT') AS private_policy,
            pg_catalog.has_table_privilege(current_user,'public.alembic_version','SELECT') AS revisions,
            pg_catalog.has_table_privilege(current_user,'public.panel_accounts','SELECT') AS accounts""")
        assert dict(row) == {'role': 'app_runtime', 'authenticated_role': 'app_runtime', 'readonly': 'on',
            'pointer_read': True, 'pointer_write': False, 'secret': False, 'private_policy': False,
            'revisions': False, 'accounts': False}
    finally:
        connection.terminate()


async def test_missing_guard_is_not_treated_as_ready_by_actual_runtime_sql(isolated_settings):
    connection = await asyncpg.connect(isolated_settings.runtime.dsn, timeout=3, command_timeout=3,
        server_settings={'default_transaction_read_only': 'on', 'search_path': 'pg_catalog'})
    try:
        # Substitute only a required catalog identifier; no shared schema is
        # altered. This proves the real SQL's missing-object/NULL behavior.
        query = barrier.SCHEMA_QUERY.replace('public.qr_single_connection_guard()',
                                             'public.orion_missing_qualification_guard()')
        assert query != barrier.SCHEMA_QUERY
        row = await connection.fetchrow(query)
        assert row['guards_ready'] is False
        assert all(row[key] is True for key in ('pointer_ready', 'binding_ready', 'index_ready',
                                               'policy_ready', 'grants_ready'))
    finally:
        connection.terminate()
