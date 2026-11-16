"""Publish 0073 only after a verified private archive of the reviewed 0072.

This pre-deploy entry changes schema only. It never provisions a merchant,
charges a customer, replays a receipt or modifies a plan entitlement.
"""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from scripts.apply_variable_combo_migration import migrated_version
from scripts.backup_and_migrate_commissions import connection_values, storage_values, perform_backup
from scripts.backup_and_migrate_subscription_group_add import schema_ready as previous_ready
from scripts.wait_for_cakto_recovery_schema import CONTRACT_QUERY

PREVIOUS = "0072_subscription_group_add"
TARGET = "0073_cakto_recovery"


def schema_ready(raw):
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
    """Serialize this release; the post-backup revision check is still required."""
    engine = create_engine(raw, poolclass=NullPool, connect_args={
        "options": "-c statement_timeout=30000 -c lock_timeout=10000",
    })
    try:
        with engine.connect() as db:
            db.execute(text("SET ROLE app_migrator"))
            db.commit()
            if not db.execute(text("SELECT pg_try_advisory_lock(1330792783,73)")).scalar_one():
                raise RuntimeError("cakto_recovery_release_busy")
            db.commit()
            try:
                yield
            finally:
                db.execute(text("SELECT pg_advisory_unlock(1330792783,73)"))
                db.commit()
    finally:
        engine.dispose()


def main():
    try:
        raw, url = connection_values()
        current = migrated_version(raw)
        if current == TARGET:
            valid = schema_ready(raw)
            print("orion_cakto_recovery_schema_ready" if valid else
                  "orion_cakto_recovery_schema_invalid", flush=True)
            return 0 if valid else 3
        if current != PREVIOUS:
            print("orion_cakto_recovery_unreviewed_source_revision", file=sys.stderr)
            return 3
        with release_lock(raw):
            current = migrated_version(raw)
            if current == TARGET:
                return 0 if schema_ready(raw) else 3
            if current != PREVIOUS or not previous_ready(raw):
                return 3
            with tempfile.TemporaryDirectory(prefix="orion-cakto-recovery-0073-") as folder:
                perform_backup(url, storage_values(), Path(folder) / "backup.dump",
                               backup_context="cakto-recovery-0073",
                               schema_revision=PREVIOUS, preserve_ownership=True)
            current = migrated_version(raw)
            if current == TARGET:
                return 0 if schema_ready(raw) else 3
            if current != PREVIOUS or not previous_ready(raw):
                return 3
            print("orion_cakto_recovery_migration_start source=" + PREVIOUS +
                  " target=" + TARGET, flush=True)
            subprocess.run([sys.executable, "-m", "alembic", "upgrade", TARGET],
                           env={**os.environ, "ORION_MIGRATION_SWITCH_ROLE": "1"},
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=600, check=True)
            valid = migrated_version(raw) == TARGET and schema_ready(raw)
            print("orion_cakto_recovery_schema_ready" if valid else
                  "orion_cakto_recovery_schema_invalid", flush=True)
            return 0 if valid else 3
    except Exception:
        print("orion_cakto_recovery_schema_release_failed_closed", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
