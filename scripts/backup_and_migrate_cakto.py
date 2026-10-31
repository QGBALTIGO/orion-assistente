"""One-time, backup-gated 0043 schema cutover. No plan activation or data edits."""
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

PREVIOUS = "0042_admin_account_controls"
TARGET = "0043_cakto_events"


def schema_ready(raw: str) -> bool:
    engine = create_engine(raw, poolclass=NullPool, connect_args={
        "options": "-c default_transaction_read_only=on -c statement_timeout=10000"
    })
    try:
        with engine.connect() as db:
            row = db.execute(text("""
                SELECT c.relrowsecurity,c.relforcerowsecurity,
                       has_table_privilege('app_support',c.oid,'SELECT'),
                       has_table_privilege('app_support',c.oid,'INSERT'),
                       has_table_privilege('app_runtime',c.oid,'SELECT')
                FROM pg_class c
                WHERE c.oid=to_regclass('public.cakto_webhook_events')
            """)).first()
            return row is not None and tuple(row) == (True, True, True, True, False)
    finally:
        engine.dispose()


def main() -> int:
    try:
        raw, url = connection_values()  # pins reviewed host/database and owner.
        current = migrated_version(raw)
        # The existing Railway predeploy also calls this verifier after the
        # reviewed WhatsApp migration. Never replay 0043 DDL on that schema.
        if current in {TARGET, "0044_single_whatsapp_connection", "0045_plan_trials_checkout",
                       "0046_orion_direct_checkout", "0047_appointment_access", "0048_appointment_notifications", "0049_preserved_phone_guard", "0050_plan_permissions", "0051_central_protocol", "0052_paraguay_phone", "0053_central_notifications", "0054_appointment_short_links", "0055_combo_duration_override", "0056_one_click_appointment_links"}:
            return 0 if schema_ready(raw) else 3
        if current != PREVIOUS:
            print("orion_cakto_unreviewed_database_revision", file=sys.stderr)
            return 2
        with tempfile.TemporaryDirectory(prefix="orion-cakto-release-") as folder:
            perform_backup(url, storage_values(), Path(folder) / "backup.dump",
                           backup_context="cakto-receipts-0043",
                           schema_revision=PREVIOUS, preserve_ownership=True)
        if migrated_version(raw) != PREVIOUS:
            return 3
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", TARGET],
            env={**os.environ, "ORION_MIGRATION_SWITCH_ROLE": "1"},
            check=False, timeout=600,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if result.returncode or migrated_version(raw) != TARGET or not schema_ready(raw):
            print("orion_cakto_schema_verification_failed", file=sys.stderr)
            return 3
        print("orion_cakto_receipts_0043_ready")
        return 0
    except Exception:
        print("orion_cakto_schema_release_failed_closed", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
