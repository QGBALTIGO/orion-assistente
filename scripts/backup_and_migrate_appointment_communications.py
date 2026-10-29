"""Backup-gated additive appointment rollout. Never alters WhatsApp auth volumes."""
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
from scripts.backup_and_migrate_direct_checkout import schema_ready as previous_ready

PREVIOUS='0046_orion_direct_checkout'
COMMUNICATIONS_PREVIOUS='0048_appointment_notifications'
TARGET='0049_preserved_phone_guard'
TABLES=('appointment_access','appointment_reviews','appointment_communication_settings',
    'appointment_notifications','appointment_confirmation_receipts','appointment_change_proposals','appointment_return_intervals')


@contextmanager
def release_lock(raw):
    engine=create_engine(raw,poolclass=NullPool,connect_args={'options':'-c statement_timeout=30000 -c lock_timeout=10000'})
    try:
        with engine.connect() as db:
            db.execute(text('SET ROLE app_migrator'))
            if db.execute(text('SELECT current_user')).scalar_one()!='app_migrator':raise RuntimeError('role_denied')
            db.commit()
            if not db.execute(text('SELECT pg_try_advisory_lock(1330792783,48)')).scalar_one():raise RuntimeError('release_busy')
            db.commit()
            try:yield
            finally:
                db.execute(text('SELECT pg_advisory_unlock(1330792783,48)'));db.commit()
    finally:engine.dispose()


def schema_ready(raw,*,preserved_phone=True):
    engine=create_engine(raw,poolclass=NullPool,connect_args={'options':'-c default_transaction_read_only=on -c statement_timeout=10000'})
    try:
        with engine.connect() as db:
            for table in TABLES:
                row=db.execute(text('''SELECT relrowsecurity,relforcerowsecurity,
                    has_table_privilege('app_runtime',oid,'SELECT'),has_table_privilege('app_runtime',oid,'INSERT'),
                    has_table_privilege('app_runtime',oid,'UPDATE'),has_table_privilege('app_support',oid,'SELECT')
                    FROM pg_class WHERE oid=to_regclass(:table)'''),{'table':'public.'+table}).first()
                if row is None or tuple(row)!=(True,True,True,True,True,False):return False
            required=(('appointments','customer_access_blocked'),('appointment_change_proposals','actor_account_id'),
                      ('appointment_change_proposals','idempotency_key'),('appointment_communication_settings','reschedule_acceptance'))
            for table,column in required:
                exists=db.execute(text("SELECT EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name=:table AND column_name=:column)"),{'table':table,'column':column}).scalar_one()
                if not exists:return False
            cleanup=db.execute(text("SELECT to_regprocedure('public.orion_clean_appointment_payloads(integer)') IS NOT NULL")).scalar_one()
            phone_guard=not preserved_phone or db.execute(text("SELECT to_regprocedure('public.orion_preserved_notice_guard(uuid,uuid,uuid,uuid,bigint,text,text)') IS NOT NULL")).scalar_one()
            return bool(cleanup and phone_guard) and previous_ready(raw)
    finally:engine.dispose()


def main():
    try:
        raw,url=connection_values()
        with release_lock(raw):
            current=migrated_version(raw)
            if current==TARGET:return 0 if schema_ready(raw) else 3
            if os.getenv('ORION_RUN_APPOINTMENT_COMMUNICATION_MIGRATION')!='1':
                return 0 if current==PREVIOUS and previous_ready(raw) else 3
            if current not in {PREVIOUS,COMMUNICATIONS_PREVIOUS} or not previous_ready(raw):return 2
            if current==COMMUNICATIONS_PREVIOUS and not schema_ready(raw,preserved_phone=False):return 3
            with tempfile.TemporaryDirectory(prefix='orion-appointment-release-') as folder:
                perform_backup(url,storage_values(),Path(folder)/'backup.dump',backup_context='appointment-communications-0049',
                    schema_revision=current,preserve_ownership=True)
            if migrated_version(raw)!=current:return 3
            run=subprocess.run([sys.executable,'-m','alembic','upgrade',TARGET],
                env={**os.environ,'ORION_MIGRATION_SWITCH_ROLE':'1'},check=False,timeout=600,
                stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            if run.returncode or migrated_version(raw)!=TARGET or not schema_ready(raw):return 3
            print('orion_appointment_0049_backup_schema_verified',flush=True)
            return 0
    except Exception:
        print('orion_appointment_0049_failed_closed',file=sys.stderr)
        return 3


if __name__=='__main__':raise SystemExit(main())
