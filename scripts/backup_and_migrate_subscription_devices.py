"""Verified-backup release from 0061 to isolated subscription device records.

Adds schema only. No existing channel is adopted, connected, disconnected or
enabled, and no WhatsApp message, webhook or transcription is executed.
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
from scripts.wait_for_subscription_devices_schema import CONTRACT_QUERY

PREVIOUS = "0061_appointment_consent"
TARGET = "0062_subscription_devices"


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
        if current in {TARGET, "0063_subscription_chatbot", "0064_subscription_templates", "0065_subscription_sent_messages", "0066_appointment_operations", "0067_subscription_diagnostics", "0068_subscription_campaigns", "0069_subscription_schedules"}:
            valid = schema_ready(raw)
            print("orion_subscription_devices_schema_ready" if valid else "orion_subscription_devices_schema_invalid", flush=True)
            return 0 if valid else 3
        if current != PREVIOUS:
            print("orion_subscription_devices_unreviewed_source_revision", file=sys.stderr)
            return 3
        from scripts.backup_and_migrate_appointment_consent import schema_ready as previous_ready
        if not previous_ready(raw):
            print("orion_subscription_devices_source_schema_invalid", file=sys.stderr)
            return 3
        with tempfile.TemporaryDirectory(prefix="orion-subscription-devices-0062-") as folder:
            perform_backup(url, storage_values(), Path(folder) / "backup.dump",
                           backup_context="subscription-devices-0062",
                           schema_revision=PREVIOUS, preserve_ownership=True)
        current = migrated_version(raw)
        if current in {TARGET, "0063_subscription_chatbot", "0064_subscription_templates", "0065_subscription_sent_messages", "0066_appointment_operations", "0067_subscription_diagnostics", "0068_subscription_campaigns", "0069_subscription_schedules"}:
            return 0 if schema_ready(raw) else 3
        if current != PREVIOUS:
            return 3
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", TARGET],
                       env={**os.environ, "ORION_MIGRATION_SWITCH_ROLE": "1"},
                       timeout=600, check=True)
        valid = migrated_version(raw) == TARGET and schema_ready(raw)
        print("orion_subscription_devices_schema_ready" if valid else "orion_subscription_devices_schema_invalid", flush=True)
        return 0 if valid else 3
    except Exception:
        print("orion_subscription_devices_schema_release_failed_closed", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
