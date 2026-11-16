"""Backup-gated rollout 0052 -> 0053 for platform appointment notifications.

Nothing is sent while migrating; the separate central delivery flag remains
off until API, worker, and bridge are confirmed on the same release.
"""
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
from scripts.backup_and_migrate_paraguay_phone import schema_ready as previous_ready

PREVIOUS = "0052_paraguay_phone"
TARGET = "0053_central_notifications"
FLAG = "ORION_RUN_CENTRAL_NOTIFICATIONS_MIGRATION"
FUNCTION = "public.orion_authorize_central_appointment_notification(uuid,uuid,uuid,text,text,text)"


@contextmanager
def release_lock(raw):
    engine = create_engine(raw,poolclass=NullPool,
        connect_args={"options":"-c statement_timeout=30000 -c lock_timeout=10000"})
    try:
        with engine.connect() as db:
            db.execute(text("SET ROLE app_migrator"))
            db.commit()
            if not db.execute(text("SELECT pg_try_advisory_lock(1330792783,53)")).scalar_one():
                raise RuntimeError("central_notification_migration_busy")
            db.commit()
            try:yield
            finally:
                db.execute(text("SELECT pg_advisory_unlock(1330792783,53)"))
                db.commit()
    finally:engine.dispose()


def schema_ready(raw):
    engine = create_engine(raw,poolclass=NullPool,
        connect_args={"options":"-c default_transaction_read_only=on -c statement_timeout=10000"})
    try:
        with engine.connect() as db:
            fields=db.execute(text("""
                SELECT column_name,is_nullable FROM information_schema.columns
                WHERE table_schema='public' AND table_name='appointment_notifications'
                AND column_name IN ('channel_id','central_channel_id','body_sha256')
            """)).all()
            if dict(fields)!={'channel_id':'YES','central_channel_id':'YES','body_sha256':'YES'}:
                return False
            check=db.execute(text("""
                SELECT convalidated,pg_get_constraintdef(oid) FROM pg_constraint
                WHERE conrelid='public.appointment_notifications'::regclass
                  AND conname='ck_appointment_notice_exactly_one_channel'
            """)).first()
            proc=db.execute(text("""
                SELECT p.prosecdef,pg_get_userbyid(p.proowner),
                       p.proconfig=ARRAY['search_path=pg_catalog']::text[],
                       has_function_privilege('app_support',p.oid,'EXECUTE'),
                       has_function_privilege('app_runtime',p.oid,'EXECUTE'),
                       NOT EXISTS(SELECT 1 FROM aclexplode(
                         coalesce(p.proacl,acldefault('f',p.proowner))) a
                         WHERE a.grantee=0 AND a.privilege_type='EXECUTE')
                FROM pg_proc p WHERE p.oid=to_regprocedure(:name)
            """),{'name':FUNCTION}).first()
            return bool(check and check[0] and proc and tuple(proc)==
                (True,'app_migrator',True,True,False,True) and previous_ready(raw))
    finally:engine.dispose()


def main():
    try:
        raw,url=connection_values()
        with release_lock(raw):
            current=migrated_version(raw)
            if current==TARGET:
                return 0 if schema_ready(raw) else 3
            if current!=PREVIOUS or not previous_ready(raw):
                return 2
            if os.getenv(FLAG)!='1':
                return 0
            with tempfile.TemporaryDirectory(prefix='orion-central-notices-') as folder:
                perform_backup(url,storage_values(),Path(folder)/'backup.dump',
                    backup_context='central-notification-0053',schema_revision=PREVIOUS,
                    preserve_ownership=True)
            if migrated_version(raw)!=PREVIOUS or not previous_ready(raw):
                return 3
            run=subprocess.run([sys.executable,'-m','alembic','upgrade',TARGET],
                env={**os.environ,'ORION_MIGRATION_SWITCH_ROLE':'1'},
                timeout=600,check=False,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            if run.returncode or migrated_version(raw)!=TARGET or not schema_ready(raw):
                return 3
            print('orion_central_notifications_0053_backup_schema_verified',flush=True)
            return 0
    except Exception:
        print('orion_central_notifications_0053_failed_closed',file=sys.stderr)
        return 3


if __name__=='__main__':
    raise SystemExit(main())
