"""Backed-up, fail-closed, one-time rollout for globally unique short links.

The WhatsApp messages remain on /a# capabilities until this script has
verified migration 0054 in production and the *separate* rollout flag is set.
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
from scripts.backup_and_migrate_commissions import (
    connection_values,storage_values,perform_backup)
from scripts.backup_and_migrate_central_notifications import schema_ready as previous_ready

PREVIOUS = "0053_central_notifications"
TARGET = "0054_appointment_short_links"
FLAG = "ORION_RUN_SHORT_LINK_MIGRATION"
RESOLVER = "public.orion_resolve_short_appointment(text,text,text)"
ALLOCATOR = "public.orion_assign_appointment_short_code(text,uuid,uuid)"


@contextmanager
def release_lock(raw):
    engine=create_engine(raw,poolclass=NullPool,
        connect_args={"options":"-c statement_timeout=30000 -c lock_timeout=10000"})
    try:
        with engine.connect() as db:
            db.execute(text("SET ROLE app_migrator"))
            db.commit()
            if not db.execute(text("SELECT pg_try_advisory_lock(1330792783,54)")).scalar_one():
                raise RuntimeError("short_link_migration_busy")
            db.commit()
            try:yield
            finally:
                db.execute(text("SELECT pg_advisory_unlock(1330792783,54)"))
                db.commit()
    finally:engine.dispose()


def schema_ready(raw):
    engine=create_engine(raw,poolclass=NullPool,
        connect_args={"options":"-c default_transaction_read_only=on -c statement_timeout=30000"})
    try:
        with engine.connect() as db:
            table=db.execute(text("""
                SELECT c.relrowsecurity,c.relforcerowsecurity,
                    has_table_privilege('app_runtime',c.oid,'SELECT'),
                    has_table_privilege('app_support',c.oid,'SELECT')
                FROM pg_class c WHERE c.oid=to_regclass('public.appointment_short_codes')
            """)).first()
            if not table or tuple(table)!=(True,True,True,False):
                return False
            trigger=db.execute(text("""
                SELECT t.tgenabled='O',p.prosecdef,
                    pg_get_userbyid(p.proowner)
                FROM pg_trigger t JOIN pg_proc p ON p.oid=t.tgfoid
                WHERE t.tgrelid='public.appointments'::regclass
                  AND t.tgname='trg_appointment_short_code' AND NOT t.tgisinternal
            """)).first()
            if not trigger or tuple(trigger)!=(True,True,'app_migrator'):
                return False
            functions=db.execute(text("""
                SELECT p.proname,p.prosecdef,pg_get_userbyid(p.proowner),
                    p.proconfig=ARRAY['search_path=pg_catalog']::text[],
                    has_function_privilege('app_support',p.oid,'EXECUTE'),
                    has_function_privilege('app_runtime',p.oid,'EXECUTE')
                FROM pg_proc p
                WHERE p.oid IN (to_regprocedure(:resolve),to_regprocedure(:assign))
            """),{"resolve":RESOLVER,"assign":ALLOCATOR}).all()
            expected={
              'orion_resolve_short_appointment':(True,'app_migrator',True,True,False),
              'orion_assign_appointment_short_code':(True,'app_migrator',True,False,False)}
            if len(functions)!=2 or any(
                tuple(row[1:])!=expected.get(row[0]) for row in functions):
                return False
            coverage=db.execute(text("""
                SELECT EXISTS(
                    SELECT 1 FROM public.appointments a
                    LEFT JOIN public.appointment_short_codes s
                        ON s.tenant_id=a.tenant_id AND s.appointment_id=a.id
                    WHERE s.code IS NULL
                    LIMIT 1
                )
            """)).scalar_one()
            return coverage is False and previous_ready(raw)
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
            with tempfile.TemporaryDirectory(prefix="orion-short-links-") as folder:
                perform_backup(url,storage_values(),Path(folder)/"backup.dump",
                               backup_context="short-appointment-links-0054",
                               schema_revision=PREVIOUS,preserve_ownership=True)
            if migrated_version(raw)!=PREVIOUS or not previous_ready(raw):
                return 3
            outcome=subprocess.run([sys.executable,"-m","alembic","upgrade",TARGET],
                env={**os.environ,"ORION_MIGRATION_SWITCH_ROLE":"1"},
                timeout=600,check=False,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            if outcome.returncode or migrated_version(raw)!=TARGET or not schema_ready(raw):
                return 3
            print("orion_short_links_0054_backup_schema_verified",flush=True)
            return 0
    except Exception:
        print("orion_short_links_0054_failed_closed",file=sys.stderr)
        return 3


if __name__=="__main__":
    raise SystemExit(main())
