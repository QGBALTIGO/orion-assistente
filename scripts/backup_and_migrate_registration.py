"""0038 -> 0039 with a verified private backup; never enqueue or send mail."""
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
from scripts.backup_and_migrate_email_outbox import source_contract_ready, schema_contract_ready as email_contract_ready

PREVIOUS='0038_email_outbox'
TARGET='0039_registration_confirmation'


@contextmanager
def release_lock(raw):
    engine=create_engine(raw,poolclass=NullPool,connect_args={'options':'-c statement_timeout=30000 -c lock_timeout=10000'})
    try:
        with engine.connect() as db:
            db.execute(text('SET ROLE app_migrator'))
            assert db.execute(text('SELECT current_user')).scalar_one()=='app_migrator'
            db.commit()
            acquired=db.execute(text('SELECT pg_try_advisory_lock(1330792783,39)')).scalar_one()
            db.commit()
            if not acquired: raise RuntimeError('registration_release_in_progress')
            try: yield
            finally:
                db.execute(text('SELECT pg_advisory_unlock(1330792783,39)'));db.commit()
    finally: engine.dispose()


def schema_contract_ready(raw):
    if not source_contract_ready(raw) or not email_contract_ready(raw): return False
    engine=create_engine(raw,poolclass=NullPool,connect_args={'options':'-c default_transaction_read_only=on -c statement_timeout=10000'})
    try:
        with engine.connect() as db:
            column=db.execute(text("""SELECT data_type,is_nullable,column_default FROM information_schema.columns
                WHERE table_schema='public' AND table_name='panel_accounts' AND column_name='registration_pending'""")).first()
            if tuple(column or ())!=('boolean','NO','false'): return False
            constraint=db.execute(text("""SELECT convalidated,condeferrable,condeferred,pg_get_constraintdef(oid)
                FROM pg_constraint WHERE conrelid='public.panel_accounts'::regclass AND conname='ck_panel_registration_pending'""")).first()
            expected="CHECK (((NOT registration_pending) OR (((auth_provider)::text = 'password'::text) AND ((role)::text = 'member'::text) AND (verified_at IS NULL))))"
            if not constraint or tuple(constraint[:3])!=(True,False,False): return False
            if ''.join(constraint[3].split())!=''.join(expected.split()): return False
            security=db.execute(text("""SELECT relrowsecurity AND relforcerowsecurity AND
                pg_get_userbyid(relowner)='app_migrator' AND
                has_table_privilege('app_support',oid,'SELECT,INSERT,UPDATE') AND
                NOT has_table_privilege('app_runtime',oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE')
                FROM pg_class WHERE oid='public.panel_accounts'::regclass""")).scalar_one()
            return bool(security)
    finally: engine.dispose()


def main():
    try:
        raw,url=connection_values()
        with release_lock(raw):
            current=migrated_version(raw)
            if current not in {PREVIOUS,TARGET} or not source_contract_ready(raw) or not email_contract_ready(raw):
                print('orion_registration_source_contract_failed',flush=True);return 3
            if current==TARGET:
                if not schema_contract_ready(raw):return 3
                print('orion_registration_schema_ready',flush=True);return 0
            with tempfile.TemporaryDirectory(prefix='orion-registration-') as folder:
                perform_backup(url,storage_values(),Path(folder)/'backup.dump',backup_context='registration-0039',
                    schema_revision=PREVIOUS,preserve_ownership=True)
            if migrated_version(raw)!=PREVIOUS or not source_contract_ready(raw) or not email_contract_ready(raw):return 3
            result=subprocess.run([sys.executable,'-m','alembic','upgrade',TARGET],
                env={**os.environ,'ORION_MIGRATION_SWITCH_ROLE':'1'},timeout=600,
                stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=False)
            if result.returncode or migrated_version(raw)!=TARGET or not schema_contract_ready(raw):
                print('orion_registration_upgrade_failed',flush=True);return 3
            print('orion_registration_schema_0039_verified_after_backup',flush=True);return 0
    except Exception:
        print('orion_registration_predeploy_failed_closed',file=sys.stderr);return 3


def predeploy_main():
    """Preserve the Railway predeploy command and use only reviewed migrations.

    Each release verifies its own private backup, schema and role boundaries.
    A current 0044 deployment performs read-only checks without another DDL.
    """
    from scripts import backup_and_migrate_professional_phone as contacts
    from scripts import backup_and_migrate_quick_onboarding as quick
    from scripts import backup_and_migrate_admin_accounts as accounts
    from scripts import backup_and_migrate_single_whatsapp as single
    from scripts import backup_and_migrate_cakto as cakto
    from scripts import backup_and_migrate_plans as plans
    from scripts import backup_and_migrate_direct_checkout as direct
    from scripts import backup_and_migrate_appointment_communications as appointment
    from scripts import backup_and_migrate_plan_permissions as permissions
    from scripts import backup_and_migrate_central_appointment_confirmation as central
    def finish_permissions(result):
        if result:return result
        checked=permissions.main()
        if checked:return checked
        return central.main() if os.getenv('ORION_RUN_CENTRAL_APPOINTMENT_MIGRATION')=='1' else 0
    def finish_direct():
        result=direct.main()
        if result:return result
        return finish_permissions(appointment.main()) if os.getenv('ORION_RUN_APPOINTMENT_COMMUNICATION_MIGRATION')=='1' else 3
    try:
        raw, _url = connection_values()
        current = migrated_version(raw)
        if current == central.TARGET:
            return central.main()
        if current == permissions.TARGET:
            return finish_permissions(0)
        if current in {appointment.TARGET,appointment.COMMUNICATIONS_PREVIOUS}:
            return finish_permissions(appointment.main())
        if current == direct.TARGET:
            return finish_direct()
        if current == plans.TARGET:
            result = plans.main()
            return result if result else finish_direct()
        if current == single.TARGET:
            result = single.main()
            if result: return result
            result = plans.main()
            return result if result else finish_direct()
        if current == PREVIOUS:
            result = main()
            if result:
                return result
        elif current not in {TARGET, contacts.TARGET, quick.TARGET, accounts.TARGET, cakto.TARGET}:
            print('orion_registration_unreviewed_schema_revision', file=sys.stderr)
            return 2
        if current in {PREVIOUS, TARGET}:
            result = contacts.main()
            if result:
                return result
        if current not in {accounts.TARGET, cakto.TARGET}:
            result = quick.main()
            if result:
                return result
        if current != cakto.TARGET:
            result = accounts.main()
            if result:
                return result
        result = cakto.main()
        if result:
            return result
        result = single.main()
        if result: return result
        result = plans.main()
        return result if result else finish_direct()
    except Exception:
        print('orion_registration_predeploy_failed_closed', file=sys.stderr)
        return 3


if __name__=='__main__':raise SystemExit(predeploy_main())
