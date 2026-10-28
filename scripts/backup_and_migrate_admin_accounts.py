"""Reviewed, private-backup-gated migration for account lifecycle tables."""
from contextlib import contextmanager
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from sqlalchemy import create_engine,text
from sqlalchemy.pool import NullPool
from scripts.apply_variable_combo_migration import migrated_version
from scripts.backup_and_migrate_commissions import connection_values,storage_values,perform_backup
from scripts.backup_and_migrate_quick_onboarding import schema_contract_ready as quick_ready

PREVIOUS="0041_quick_onboarding_activation"
TARGET="0042_admin_account_controls"
LOCK_SCOPE,LOCK_RELEASE=0x4F52494F,42

@contextmanager
def release_lock(raw):
    engine=create_engine(raw,poolclass=NullPool,connect_args={
        "options":"-c statement_timeout=30000 -c lock_timeout=10000"})
    try:
        with engine.connect() as db:
            db.execute(text("SET ROLE app_migrator"))
            if db.execute(text("SELECT current_user")).scalar_one()!="app_migrator":
                raise RuntimeError("admin_migrator_role_required")
            db.commit()
            if not db.execute(text("SELECT pg_try_advisory_lock(:s,:r)"),
                              {"s":LOCK_SCOPE,"r":LOCK_RELEASE}).scalar_one():
                raise RuntimeError("admin_release_in_progress")
            db.commit()
            try:yield
            finally:
                db.execute(text("SELECT pg_advisory_unlock(:s,:r)"),
                           {"s":LOCK_SCOPE,"r":LOCK_RELEASE})
                db.commit()
    finally:engine.dispose()

def schema_ready(raw):
    if not quick_ready(raw):return False
    engine=create_engine(raw,poolclass=NullPool,connect_args={
        "options":"-c default_transaction_read_only=on -c statement_timeout=10000"})
    try:
        with engine.connect() as db:
            rows=db.execute(text("""
                SELECT c.relname,c.relrowsecurity,c.relforcerowsecurity,
                       has_table_privilege('app_support',c.oid,'SELECT'),
                       has_table_privilege('app_runtime',c.oid,'SELECT')
                FROM pg_class c WHERE c.oid IN (
                    to_regclass('public.admin_account_controls'),
                    to_regclass('public.admin_account_audit'))
            """)).all()
            return len(rows)==2 and all(r[1:] == (True,True,True,False) for r in rows)
    finally:engine.dispose()

def main():
    try:
        raw,url=connection_values()
        with release_lock(raw):
            current=migrated_version(raw)
            if current not in {PREVIOUS,TARGET}:return 2
            if current==TARGET:return 0 if schema_ready(raw) else 3
            if not quick_ready(raw):return 3
            with tempfile.TemporaryDirectory(prefix="orion-admin-lifecycle-") as folder:
                perform_backup(url,storage_values(),Path(folder)/"backup.dump",
                    backup_context="admin-accounts-0042",schema_revision=PREVIOUS,
                    preserve_ownership=True)
            if migrated_version(raw)!=PREVIOUS or not quick_ready(raw):return 3
            result=subprocess.run([sys.executable,"-m","alembic","upgrade",TARGET],
                env={**os.environ,"ORION_MIGRATION_SWITCH_ROLE":"1"},
                check=False,timeout=600,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            return 0 if result.returncode==0 and migrated_version(raw)==TARGET and schema_ready(raw) else 3
    except Exception:
        print("orion_admin_accounts_release_failed_closed",file=sys.stderr)
        return 3

if __name__=="__main__":
    raise SystemExit(main())
