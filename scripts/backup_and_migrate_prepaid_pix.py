"""Backup-gated, forward-only schema migration for manually renewable Pix.

No provider POST, payment, tenant activation, event processing, or financial edits.
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
from scripts.backup_and_migrate_commissions import connection_values,storage_values,perform_backup

PREVIOUS="0058_professional_scope"
TARGET="0059_prepaid_pix"


def schema_ready(raw: str) -> bool:
    engine=create_engine(raw,poolclass=NullPool,connect_args={
        "options":"-c default_transaction_read_only=on -c statement_timeout=12000"
    })
    try:
        with engine.connect() as db:
            data=db.execute(text("""
              SELECT c.relname,c.relrowsecurity,c.relforcerowsecurity,
                     has_table_privilege('app_support',c.oid,'SELECT,INSERT,UPDATE'),
                     has_table_privilege('app_runtime',c.oid,'SELECT')
              FROM pg_class c
              WHERE c.oid IN (to_regclass('public.orion_prepaid_pix_orders'),
                              to_regclass('public.orion_prepaid_pix_access'))
            """)).all()
            checks={row[0]:tuple(row[1:]) for row in data}
            function=db.execute(text("""
              SELECT to_regprocedure('public.orion_prepaid_plan_snapshot(uuid)') IS NOT NULL,
                     has_function_privilege('app_support',
                         'public.orion_prepaid_plan_snapshot(uuid)','EXECUTE'),
                     has_function_privilege('app_runtime',
                         'public.orion_prepaid_plan_snapshot(uuid)','EXECUTE')
            """)).one()
            return (checks.get("orion_prepaid_pix_orders")==(True,True,True,False)
                    and checks.get("orion_prepaid_pix_access")==(True,True,True,False)
                    and tuple(function)==(True,True,True))
    finally:
        engine.dispose()


def main() -> int:
    try:
        raw,url=connection_values()
        version=migrated_version(raw)
        if version in {TARGET, "0060_manual_start_times", "0061_appointment_consent", "0062_subscription_devices", "0063_subscription_chatbot", "0064_subscription_templates", "0065_subscription_sent_messages"}:
            ok=schema_ready(raw)
            print("orion_prepaid_schema_ready" if ok else "orion_prepaid_schema_invalid",flush=True)
            return 0 if ok else 3
        if version!=PREVIOUS:
            print("orion_prepaid_unreviewed_source_revision",file=sys.stderr)
            return 3
        with tempfile.TemporaryDirectory(prefix="orion-prepaid-0059-") as folder:
            perform_backup(url,storage_values(),Path(folder)/"backup.dump",
                           backup_context="prepaid-pix-0059",
                           schema_revision=PREVIOUS,preserve_ownership=True)
        if migrated_version(raw)!=PREVIOUS:
            return 3
        subprocess.run([sys.executable,"-m","alembic","upgrade",TARGET],
                       env={**os.environ,"ORION_MIGRATION_SWITCH_ROLE":"1"},
                       timeout=600,check=True)
        valid=migrated_version(raw)==TARGET and schema_ready(raw)
        print("orion_prepaid_schema_ready" if valid else "orion_prepaid_schema_invalid",flush=True)
        return 0 if valid else 3
    except Exception:
        print("orion_prepaid_schema_release_failed_closed",file=sys.stderr)
        return 3

if __name__=="__main__":
    raise SystemExit(main())
