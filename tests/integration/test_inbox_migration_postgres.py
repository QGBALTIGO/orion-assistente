"""Upgrade/rollback only in a new, randomly named local database.

The owner URL is a dedicated CI opt-in. Operational databases are refused.
"""
import os
from pathlib import Path
import re
import subprocess
import sys
from uuid import uuid4

import pytest
from sqlalchemy.engine import make_url


@pytest.mark.integration
def test_inbox_migrations_roundtrip_and_refuse_data_loss():
    owner_raw = os.getenv("ORION_MIGRATION_TEST_OWNER_URL")
    migration_raw = os.getenv("MIGRATION_DATABASE_URL")
    if not owner_raw or not migration_raw:
        if os.getenv("ORION_REQUIRE_MIGRATION_OPS_TEST") == "1":
            pytest.fail("A local disposable database owner and migrator are required")
        pytest.skip("Disposable local PostgreSQL migration owner not configured")
    owner, migration = make_url(owner_raw), make_url(migration_raw)
    if (owner.host not in {"localhost", "127.0.0.1", "::1"} or owner.database != "postgres"
            or migration.host != owner.host or migration.username != "app_migrator"):
        pytest.fail("Migration roundtrip refuses nonlocal or operational owner targets")
    import psycopg
    from psycopg import sql
    database = "orion_inbox_upgrade_"+uuid4().hex
    assert re.fullmatch(r"orion_inbox_upgrade_[a-f0-9]{32}", database)
    owner_dsn = owner.set(drivername="postgresql").render_as_string(hide_password=False)
    new_migration = migration.set(database=database)
    dsn = new_migration.set(drivername="postgresql").render_as_string(hide_password=False)
    environment = {**os.environ, "MIGRATION_DATABASE_URL": new_migration.render_as_string(hide_password=False)}
    root = Path(__file__).resolve().parents[2]

    def alembic(*args, success=True):
        result = subprocess.run([sys.executable, "-m", "alembic", *args], cwd=root, env=environment,
            capture_output=True, text=True, timeout=90)
        if success:
            assert result.returncode == 0, result.stderr.replace(owner.password or "no-password", "[redacted]").replace(migration.password or "no-password", "[redacted]")
        else:
            assert result.returncode != 0
        return result

    with psycopg.connect(owner_dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {} OWNER app_migrator").format(sql.Identifier(database)))
    try:
        alembic("upgrade", "head")
        with psycopg.connect(dsn) as db:
            tables = db.execute("""SELECT relname,relrowsecurity,relforcerowsecurity FROM pg_class
              WHERE relname IN ('inbox_labels','inbox_quick_replies','inbox_customer_labels','inbox_controls','inbox_control_events',
                'inbox_channel_links','inbox_messages','inbox_provider_receipts')""").fetchall()
            assert len(tables) == 8 and all(enabled and forced for _, enabled, forced in tables)
        alembic("downgrade", "0023_automation_notice_guard")
        alembic("upgrade", "head")
        tenant = uuid4()
        with psycopg.connect(dsn) as db:
            db.execute("INSERT INTO tenants(id,name,slug) VALUES(%s,'Synthetic migration',%s)", (tenant, "migration-"+tenant.hex))
            db.execute("INSERT INTO inbox_labels(tenant_id,name,color) VALUES(%s,'Preserve','#18836b')", (tenant,))
        failed = alembic("downgrade", "0023_automation_notice_guard", success=False)
        assert "Preserve inbox tools data" in failed.stderr
        with psycopg.connect(dsn) as db:
            assert db.execute("SELECT count(*) FROM inbox_labels WHERE tenant_id=%s", (tenant,)).fetchone()[0] == 1
    finally:
        # Exact randomly generated database above, never a configured app DB.
        with psycopg.connect(owner_dsn, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database)))
