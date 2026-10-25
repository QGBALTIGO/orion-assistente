"""Fail-closed, one-revision automatic completion schema cutover for Railway.

Called only as an explicitly configured one-time pre-deploy command. Works
with app_migrator or the existing owner credential using the restricted role;
never prints URLs, passwords, or customer details. No backup restore and no
data mutation except the reviewed additive 0031 migration.
"""
from __future__ import annotations

import os
import subprocess
import sys

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

PREVIOUS = "0030_legacy_admin_identity"
TARGET = "0031_auto_complete_appointments"


def migrated_version(url: str) -> str:
    engine = create_engine(url, poolclass=NullPool, connect_args={
        "options": "-c default_transaction_read_only=on -c statement_timeout=10000 -c lock_timeout=3000"
    })
    try:
        with engine.connect() as connection:
            return connection.execute(text("SELECT version_num FROM public.alembic_version")).scalar_one()
    finally:
        engine.dispose()


def main() -> int:
    raw = os.getenv("MIGRATION_DATABASE_URL")
    host, database = os.getenv("ORION_EXPECTED_DATABASE_HOST"), os.getenv("ORION_EXPECTED_DATABASE_NAME")
    if not raw or not host or not database:
        print("auto_completion_migration_missing_reviewed_database_context", file=sys.stderr)
        return 2
    if raw.startswith("postgres://"):
        raw = raw.replace("postgres://", "postgresql+psycopg://", 1)
    elif raw.startswith("postgresql://"):
        raw = raw.replace("postgresql://", "postgresql+psycopg://", 1)
    url = make_url(raw)
    if (url.drivername != "postgresql+psycopg" or url.host != host or url.database != database
            or not url.username or not url.password):
        print("auto_completion_migration_wrong_database", file=sys.stderr)
        return 2
    previous = migrated_version(raw)
    if previous == TARGET:
        print("auto_completion_schema_already_current")
        return 0
    if previous != PREVIOUS:
        print("auto_completion_unreviewed_source_revision", file=sys.stderr)
        return 2
    # Role switch is performed by the already-reviewed Alembic environment.
    # The operation is transactional and upgrades only one revision.
    env = {**os.environ, "ORION_MIGRATION_SWITCH_ROLE": "1"}
    result = subprocess.run([sys.executable, "-m", "alembic", "upgrade", TARGET],
                            env=env, check=False)
    if result.returncode != 0:
        print("auto_completion_schema_upgrade_failed", file=sys.stderr)
        return 3
    if migrated_version(raw) != TARGET:
        print("auto_completion_schema_revision_not_confirmed", file=sys.stderr)
        return 3
    print("auto_completion_schema_0031_ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
