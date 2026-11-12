"""0074 -> 0075: private verified backup, one additive Alembic migration.

This module runs only in Railway's pre-deploy, never from the HTTP API or
worker. A duplicate pre-deploy is idempotent; unrelated schema heads fail closed.
No customer identities, passwords, dumps or provider responses are logged.
"""
from contextlib import contextmanager
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from scripts.apply_variable_combo_migration import migrated_version
from scripts.backup_and_migrate_commissions import (
    connection_values, storage_values, perform_backup,
)
from scripts import backup_and_migrate_fixed_client_series as predecessor
from scripts.wait_for_fixed_client_monthly_schema import CONTRACT_QUERY

PREVIOUS = "0074_fixed_client_series"
TARGET = "0075_fixed_client_monthly"


def schema_ready(raw):
    # The pre-deploy migrator may read Alembic's marker; app_runtime may not.
    current = migrated_version(raw)
    # Readiness checks of the 0075 contract remain useful after these reviewed
    # additive releases. Require each successor's complete contract, including
    # the monthly predecessor, rather than accepting an arbitrary newer head.
    # The migration entry point below still accepts only PREVIOUS or TARGET.
    if current == "0076_dental_clinic":
        from scripts.wait_for_dental_schema import schema_ready as successor_ready
        return successor_ready(raw) is True
    if current == "0077_commercial_demos":
        from scripts.wait_for_commercial_demos_schema import schema_ready as successor_ready
        return successor_ready(raw) is True
    if current == "0078_customer_legacy_history":
        from scripts.wait_for_customer_legacy_history_schema import schema_ready as successor_ready
        return successor_ready(raw) is True
    if current != TARGET:
        return False
    engine = create_engine(raw, poolclass=NullPool, connect_args={
        "options": "-c default_transaction_read_only=on -c statement_timeout=12000",
    })
    try:
        with engine.connect() as db:
            return db.execute(text(CONTRACT_QUERY)).scalar() is True
    finally:
        engine.dispose()


@contextmanager
def release_lock(raw):
    """A separate PostgreSQL lock serializes backups and 0075 migration."""
    engine = create_engine(raw, poolclass=NullPool, connect_args={
        "options": "-c statement_timeout=30000 -c lock_timeout=10000",
    })
    try:
        with engine.connect() as db:
            if db.execute(text("SELECT current_user")).scalar_one() != "app_migrator":
                db.execute(text("SET ROLE app_migrator"))
                db.commit()
            if not db.execute(text("SELECT pg_try_advisory_lock(1330792783,75)")).scalar_one():
                raise RuntimeError("fixed_client_monthly_release_busy")
            db.commit()
            try:
                yield
            finally:
                db.execute(text("SELECT pg_advisory_unlock(1330792783,75)"))
                db.commit()
    finally:
        engine.dispose()


def main():
    try:
        raw, url = connection_values()
        current = migrated_version(raw)
        if current == TARGET:
            ready = schema_ready(raw)
            print("orion_fixed_client_monthly_schema_ready" if ready else
                  "orion_fixed_client_monthly_schema_invalid", flush=True)
            return 0 if ready else 3
        if current != PREVIOUS:
            print("orion_fixed_client_monthly_unreviewed_source_revision", file=sys.stderr)
            return 3
        with release_lock(raw):
            current = migrated_version(raw)
            if current == TARGET:
                return 0 if schema_ready(raw) else 3
            if current != PREVIOUS or not predecessor.schema_ready(raw):
                return 3
            with tempfile.TemporaryDirectory(prefix="orion-fixed-client-monthly-0075-") as folder:
                # Only a full ownership-preserving S3 archive with verified
                # checksum/readback authorizes the schema mutation.
                perform_backup(url, storage_values(), Path(folder) / "backup.dump",
                               backup_context="fixed-client-monthly-0075",
                               schema_revision=PREVIOUS, preserve_ownership=True)
            current = migrated_version(raw)
            if current == TARGET:
                return 0 if schema_ready(raw) else 3
            if current != PREVIOUS or not predecessor.schema_ready(raw):
                return 3
            print("orion_fixed_client_monthly_migration_start source=" + PREVIOUS +
                  " target=" + TARGET, flush=True)
            subprocess.run([sys.executable, "-m", "alembic", "upgrade", TARGET],
                           env={**os.environ, "ORION_MIGRATION_SWITCH_ROLE": "1"},
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=600, check=True)
            ready = migrated_version(raw) == TARGET and schema_ready(raw)
            print("orion_fixed_client_monthly_schema_ready" if ready else
                  "orion_fixed_client_monthly_schema_invalid", flush=True)
            return 0 if ready else 3
    except Exception:
        print("orion_fixed_client_monthly_release_failed_closed", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
