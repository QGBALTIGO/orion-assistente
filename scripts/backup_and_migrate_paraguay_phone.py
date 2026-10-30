"""One-time, opt-in 0051→0052 phone extension with private backup.

Preserve every existing +55 customer, business, subscription, booking, and QR
session. Only additive CHECK alternatives and the guarded lookup are changed.
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
from scripts.backup_and_migrate_central_appointment_confirmation import schema_ready as previous_ready

PREVIOUS = "0051_central_protocol"
TARGET = "0052_paraguay_phone"
FLAG = "ORION_RUN_PARAGUAY_PHONE_MIGRATION"


@contextmanager
def release_lock(raw):
    engine = create_engine(raw,poolclass=NullPool,
                           connect_args={"options":"-c statement_timeout=30000 -c lock_timeout=10000"})
    try:
        with engine.connect() as db:
            db.execute(text("SET ROLE app_migrator"));db.commit()
            if not db.execute(text("SELECT pg_try_advisory_lock(1330792783,52)")).scalar_one():
                raise RuntimeError("paraguay_phone_release_busy")
            db.commit()
            try:yield
            finally:
                db.execute(text("SELECT pg_advisory_unlock(1330792783,52)"));db.commit()
    finally:engine.dispose()


def schema_ready(raw):
    engine=create_engine(raw,poolclass=NullPool,
                         connect_args={"options":"-c default_transaction_read_only=on -c statement_timeout=10000"})
    try:
        with engine.connect() as db:
            for table,name in (
                ("customers","ck_customer_phone"),
                ("contact_consents","ck_consent_phone"),
                ("professionals","ck_professional_phone_e164"),
            ):
                rule=db.execute(text("""
                    SELECT pg_get_constraintdef(c.oid) FROM pg_constraint c
                    WHERE c.conrelid=to_regclass(:table) AND c.conname=:name AND c.contype='c'
                """),{"table":"public."+table,"name":name}).scalar_one_or_none()
                if not rule or "5959" not in rule or "+55" not in rule:
                    return False
            details=db.execute(text("""
                SELECT pg_get_functiondef(p.oid),p.prosecdef,
                    pg_get_userbyid(p.proowner),
                    has_function_privilege('app_support',p.oid,'EXECUTE'),
                    has_function_privilege('app_runtime',p.oid,'EXECUTE')
                FROM pg_proc p
                WHERE p.oid=to_regprocedure('public.orion_resolve_central_appointment(text,text)')
            """)).first()
            return bool(details and "5959" in details[0] and details[1] and
                        details[2]=="app_migrator" and details[3] and not details[4] and
                        previous_ready(raw))
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
            if os.getenv(FLAG)!="1":
                return 0
            with tempfile.TemporaryDirectory(prefix="orion-phone-595-") as folder:
                perform_backup(url,storage_values(),Path(folder)/"backup.dump",
                               backup_context="phone-paraguay-0052",schema_revision=PREVIOUS,
                               preserve_ownership=True)
            if migrated_version(raw)!=PREVIOUS or not previous_ready(raw):
                return 3
            run=subprocess.run([sys.executable,"-m","alembic","upgrade",TARGET],
                               env={**os.environ,"ORION_MIGRATION_SWITCH_ROLE":"1"},
                               timeout=600,check=False,stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
            if run.returncode or migrated_version(raw)!=TARGET or not schema_ready(raw):
                return 3
            print("orion_paraguay_phone_0052_backup_schema_verified",flush=True)
            return 0
    except Exception:
        print("orion_paraguay_phone_0052_failed_closed",file=sys.stderr)
        return 3


if __name__=="__main__":
    raise SystemExit(main())
