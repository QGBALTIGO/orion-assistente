"""Backup-gated migration to 0046 for the isolated, support-only direct checkout ledger.

No payment or customer data is created by this release step. Before applying DDL,
preserve the existing private backup and verify the intended database role.
"""
from __future__ import annotations
from contextlib import contextmanager
from pathlib import Path
import os
import subprocess
import sys
import tempfile
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool
from scripts.apply_variable_combo_migration import migrated_version
from scripts.backup_and_migrate_commissions import connection_values, storage_values, perform_backup
from scripts.backup_and_migrate_plans import schema_ready as plans_ready

PREVIOUS = "0045_plan_trials_checkout"
TARGET = "0046_orion_direct_checkout"


@contextmanager
def _release_lock(raw):
    engine = create_engine(raw, poolclass=NullPool,
                           connect_args={"options":"-c statement_timeout=30000 -c lock_timeout=10000"})
    try:
        with engine.connect() as db:
            db.execute(text("SET ROLE app_migrator"))
            if db.execute(text("SELECT current_user")).scalar_one() != "app_migrator":
                raise RuntimeError("orion_direct_migration_role_denied")
            db.commit()
            held = db.execute(text("SELECT pg_try_advisory_lock(1330792783,46)")).scalar_one()
            db.commit()
            if not held:
                raise RuntimeError("orion_direct_migration_lock_busy")
            try:
                yield
            finally:
                db.execute(text("SELECT pg_advisory_unlock(1330792783,46)"))
                db.commit()
    finally:
        engine.dispose()


def schema_ready(raw):
    engine = create_engine(raw, poolclass=NullPool,
                           connect_args={"options":"-c default_transaction_read_only=on -c statement_timeout=10000"})
    try:
        with engine.connect() as db:
            security = db.execute(text("""
                SELECT relrowsecurity,relforcerowsecurity,
                       has_table_privilege('app_support',oid,'SELECT,INSERT,UPDATE'),
                       has_table_privilege('app_runtime',oid,'SELECT,INSERT,UPDATE,DELETE')
                FROM pg_class WHERE oid=to_regclass('public.orion_direct_payment_attempts')
            """)).first()
            if security is None or tuple(security) != (True,True,True,False):
                return False
            columns = db.execute(text("""
                SELECT count(*) FROM information_schema.columns
                WHERE table_schema='public' AND table_name='orion_direct_payment_attempts'
                  AND column_name IN ('id','intent_id','tenant_id','account_id','method',
                                      'state','external_order_id','created_at','updated_at')
            """)).scalar_one()
            return columns == 9 and plans_ready(raw)
    finally:
        engine.dispose()


def main():
    try:
        raw, url = connection_values()
        with _release_lock(raw):
            current = migrated_version(raw)
            if current in {TARGET,"0047_appointment_access","0048_appointment_notifications"}:
                return 0 if schema_ready(raw) else 3
            if current != PREVIOUS or not plans_ready(raw):
                print("orion_direct_unreviewed_source", file=sys.stderr)
                return 2
            with tempfile.TemporaryDirectory(prefix="orion-direct-payment-") as folder:
                perform_backup(url, storage_values(), Path(folder)/"backup.dump",
                               backup_context="direct-payments-0046",
                               schema_revision=PREVIOUS, preserve_ownership=True)
            if migrated_version(raw) != PREVIOUS or not plans_ready(raw):
                return 3
            result = subprocess.run([sys.executable,"-m","alembic","upgrade",TARGET],
                                    env={**os.environ,"ORION_MIGRATION_SWITCH_ROLE":"1"},
                                    check=False,timeout=600,stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
            if result.returncode or migrated_version(raw) != TARGET or not schema_ready(raw):
                print("orion_direct_migration_verify_failed", file=sys.stderr)
                return 3
            print("orion_direct_0046_backup_schema_verified", flush=True)
            return 0
    except Exception:
        print("orion_direct_0046_fail_closed", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
