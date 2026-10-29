"""Reviewed private-backup-gated 0045 migration for plan trial/checkout metadata.

No tenant/account payments are mutated. Fails closed when source schema,
database identity, backup storage or role grants differ from reviewed state.
"""
from __future__ import annotations
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
from scripts.backup_and_migrate_cakto import schema_ready as cakto_ready

PREVIOUS="0044_single_whatsapp_connection"
TARGET="0045_plan_trials_checkout"


@contextmanager
def _lock(raw):
    engine=create_engine(raw,poolclass=NullPool,connect_args={
        "options":"-c statement_timeout=30000 -c lock_timeout=10000"})
    try:
        with engine.connect() as db:
            db.execute(text("SET ROLE app_migrator"))
            if db.execute(text("SELECT current_user")).scalar_one()!="app_migrator":
                raise RuntimeError("orion_plan_migrator_role_missing")
            db.commit()
            held=db.execute(text("SELECT pg_try_advisory_lock(1330792783,45)")).scalar_one()
            db.commit()
            if not held:
                raise RuntimeError("orion_plan_release_in_progress")
            try:
                yield
            finally:
                db.execute(text("SELECT pg_advisory_unlock(1330792783,45)"))
                db.commit()
    finally:
        engine.dispose()


def schema_ready(raw):
    engine=create_engine(raw,poolclass=NullPool,connect_args={
        "options":"-c default_transaction_read_only=on -c statement_timeout=10000"})
    try:
        with engine.connect() as db:
            tables=("orion_plan_trial_requests","orion_cakto_checkout_intents",
                    "orion_cakto_bindings")
            for name in tables:
                row=db.execute(text("""
                    SELECT relrowsecurity,relforcerowsecurity,
                           has_table_privilege('app_support',oid,'SELECT'),
                           has_table_privilege('app_support',oid,'INSERT'),
                           has_table_privilege('app_runtime',oid,'SELECT')
                    FROM pg_class WHERE oid=to_regclass(:table)
                """),{"table":"public."+name}).first()
                if row is None or tuple(row)!=(True,True,True,True,False):
                    return False
            fields=db.execute(text("""
                SELECT count(*) FROM information_schema.columns
                WHERE table_schema='public' AND table_name='cakto_webhook_events'
                  AND column_name IN ('callback_hash','amount_cents','currency')
            """)).scalar_one()
            if fields!=3:
                return False
            plans=db.execute(text("""
                SELECT code,monthly_cents,max_professionals FROM plans
                WHERE code IN ('orion_essencial','orion_profissional','orion_elite')
            """)).all()
            return sorted(tuple(x) for x in plans)==sorted([
                ("orion_essencial",2990,2),("orion_profissional",8990,6),
                ("orion_elite",13990,10)])
    finally:
        engine.dispose()


def main():
    try:
        raw,url=connection_values()
        with _lock(raw):
            current=migrated_version(raw)
            if current in {TARGET,"0046_orion_direct_checkout"}:
                return 0 if schema_ready(raw) else 3
            if current!=PREVIOUS or not cakto_ready(raw):
                print("orion_plan_unreviewed_source",file=sys.stderr)
                return 2
            with tempfile.TemporaryDirectory(prefix="orion-plan-schema-") as folder:
                perform_backup(url,storage_values(),Path(folder)/"backup.dump",
                    backup_context="plan-trials-0045",schema_revision=PREVIOUS,
                    preserve_ownership=True)
            if migrated_version(raw)!=PREVIOUS or not cakto_ready(raw):
                return 3
            result=subprocess.run([sys.executable,"-m","alembic","upgrade",TARGET],
                env={**os.environ,"ORION_MIGRATION_SWITCH_ROLE":"1"},
                check=False,timeout=600,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            if result.returncode or migrated_version(raw)!=TARGET or not schema_ready(raw):
                print("orion_plan_0045_verification_failed",file=sys.stderr)
                return 3
            print("orion_plan_0045_schema_ready",flush=True)
            return 0
    except Exception:
        print("orion_plan_0045_release_failed_closed",file=sys.stderr)
        return 3


if __name__=="__main__":
    raise SystemExit(main())
