"""Publish internal appointment metadata after a verified private backup.

The API pre-deploy owns this additive migration. Runtime API/worker processes
only inspect the catalog contract and never change the schema.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool
from scripts.apply_variable_combo_migration import migrated_version
from scripts.backup_and_migrate_commissions import connection_values, storage_values, perform_backup
from scripts.wait_for_appointment_operations_schema import CONTRACT_QUERY

PREVIOUS = "0065_subscription_sent_messages"
TARGET = "0066_appointment_operations"


def schema_ready(raw):
    engine = create_engine(raw, poolclass=NullPool, connect_args={
        "options": "-c default_transaction_read_only=on -c statement_timeout=12000",
    })
    try:
        with engine.connect() as db:
            return db.execute(text(CONTRACT_QUERY)).scalar() is True
    finally:
        engine.dispose()


def main():
    try:
        raw, url = connection_values()
        current = migrated_version(raw)
        if current in {TARGET, "0067_subscription_diagnostics"}:
            valid = schema_ready(raw)
            print("orion_appointment_operations_schema_ready" if valid else "orion_appointment_operations_schema_invalid", flush=True)
            return 0 if valid else 3
        if current != PREVIOUS:
            print("orion_appointment_operations_unreviewed_source_revision", file=sys.stderr)
            return 3
        from scripts.backup_and_migrate_subscription_sent_messages import schema_ready as previous_ready
        if not previous_ready(raw):
            print("orion_appointment_operations_source_schema_invalid", file=sys.stderr)
            return 3
        with tempfile.TemporaryDirectory(prefix="orion-appointment-operations-0066-") as folder:
            perform_backup(url, storage_values(), Path(folder) / "backup.dump",
                           backup_context="appointment-operations-0066",
                           schema_revision=PREVIOUS, preserve_ownership=True)
        current = migrated_version(raw)
        if current in {TARGET, "0067_subscription_diagnostics"}:
            return 0 if schema_ready(raw) else 3
        if current != PREVIOUS or not previous_ready(raw):
            return 3
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", TARGET],
                       env={**os.environ, "ORION_MIGRATION_SWITCH_ROLE": "1"},
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=600, check=True)
        valid = migrated_version(raw) == TARGET and schema_ready(raw)
        print("orion_appointment_operations_schema_ready" if valid else "orion_appointment_operations_schema_invalid", flush=True)
        return 0 if valid else 3
    except Exception:
        print("orion_appointment_operations_schema_release_failed_closed", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
