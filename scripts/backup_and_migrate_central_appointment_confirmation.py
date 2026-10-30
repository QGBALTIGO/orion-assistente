"""Opt-in 0050 -> 0051 migration with private backup and function ACL checks."""
from contextlib import contextmanager
from pathlib import Path
import os
import subprocess
import sys
import tempfile

from sqlalchemy import create_engine,text
from sqlalchemy.pool import NullPool
from scripts.apply_variable_combo_migration import migrated_version
from scripts.backup_and_migrate_commissions import connection_values,storage_values,perform_backup
from scripts.backup_and_migrate_plan_permissions import schema_ready as previous_ready

PREVIOUS='0050_plan_permissions'
TARGET='0051_central_appointment_confirmation'
FUNCTION='public.orion_resolve_central_appointment(text,text)'


@contextmanager
def release_lock(raw):
    engine=create_engine(raw,poolclass=NullPool,
                         connect_args={'options':'-c statement_timeout=30000 -c lock_timeout=10000'})
    try:
        with engine.connect() as db:
            db.execute(text('SET ROLE app_migrator'));db.commit()
            if not db.execute(text('SELECT pg_try_advisory_lock(1330792783,51)')).scalar_one():
                raise RuntimeError('central_appointment_migration_busy')
            db.commit()
            try:yield
            finally:
                db.execute(text('SELECT pg_advisory_unlock(1330792783,51)'));db.commit()
    finally:engine.dispose()


def schema_ready(raw):
    engine=create_engine(raw,poolclass=NullPool,
                         connect_args={'options':'-c default_transaction_read_only=on -c statement_timeout=10000'})
    try:
        with engine.connect() as db:
            row=db.execute(text("""SELECT
              p.prosecdef
              AND pg_get_userbyid(p.proowner)='app_migrator'
              AND p.proconfig=ARRAY['search_path=pg_catalog']::text[]
              AND has_function_privilege('app_support',p.oid,'EXECUTE')
              AND NOT has_function_privilege('app_runtime',p.oid,'EXECUTE')
              AND NOT EXISTS (SELECT 1
                  FROM aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a
                  WHERE a.grantee=0 AND a.privilege_type='EXECUTE')
              FROM pg_proc p WHERE p.oid=to_regprocedure(:name)"""),{'name':FUNCTION}).scalar_one_or_none()
            return row is True and previous_ready(raw)
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
            # Unflagged application releases leave the schema unchanged.
            if os.getenv('ORION_RUN_CENTRAL_APPOINTMENT_MIGRATION')!='1':
                return 0
            with tempfile.TemporaryDirectory(prefix='orion-central-confirmation-') as directory:
                perform_backup(url,storage_values(),Path(directory)/'backup.dump',
                               backup_context='central-appointment-0051',
                               schema_revision=PREVIOUS,preserve_ownership=True)
            if migrated_version(raw)!=PREVIOUS or not previous_ready(raw):
                return 3
            run=subprocess.run([sys.executable,'-m','alembic','upgrade',TARGET],
                               env={**os.environ,'ORION_MIGRATION_SWITCH_ROLE':'1'},
                               timeout=600,check=False,stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
            if run.returncode or migrated_version(raw)!=TARGET or not schema_ready(raw):
                return 3
            print('orion_central_confirmation_0051_backup_schema_verified',flush=True)
            return 0
    except Exception:
        print('orion_central_confirmation_0051_failed_closed',file=sys.stderr)
        return 3


if __name__=='__main__':
    raise SystemExit(main())
