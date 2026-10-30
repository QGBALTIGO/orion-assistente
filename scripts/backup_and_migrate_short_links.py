"""Backup-gated, forward-only 0051 release: global short links + WhatsApp OTP.

No appointment identifiers or old links are changed. Uses a reviewed private
backup and verifies schema/roles before allowing the new runtime.
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
from scripts.backup_and_migrate_plan_permissions import schema_ready as previous_ready

PREVIOUS = "0050_plan_permissions"
TARGET = "0051_short_appointment_links"
TABLES = ("appointment_short_codes", "appointment_short_challenges", "appointment_short_staff_sessions")


@contextmanager
def release_lock(raw):
    engine = create_engine(raw, poolclass=NullPool, connect_args={
        "options": "-c statement_timeout=90000 -c lock_timeout=10000"})
    try:
        with engine.connect() as db:
            db.execute(text("SET ROLE app_migrator"))
            if db.execute(text("SELECT current_user")).scalar_one() != "app_migrator":
                raise RuntimeError("short_links_release_role_required")
            db.commit()
            if not db.execute(text("SELECT pg_try_advisory_lock(1330792783,51)")).scalar_one():
                raise RuntimeError("short_links_release_in_progress")
            db.commit()
            try:
                yield
            finally:
                db.execute(text("SELECT pg_advisory_unlock(1330792783,51)"))
                db.commit()
    finally:
        engine.dispose()


def schema_ready(raw):
    engine = create_engine(raw, poolclass=NullPool, connect_args={
        "options": "-c default_transaction_read_only=on -c statement_timeout=10000"})
    try:
        with engine.connect() as db:
            for table in TABLES:
                row = db.execute(text("""
                    SELECT relrowsecurity AND relforcerowsecurity
                      AND pg_get_userbyid(relowner)='app_migrator'
                      FROM pg_class WHERE oid=to_regclass(:table)
                """), {"table": "public." + table}).scalar_one_or_none()
                if row is not True:
                    return False
            result = db.execute(text("""
                SELECT has_table_privilege('app_runtime','public.appointment_short_codes','SELECT,INSERT')
                  AND has_table_privilege('app_support','public.appointment_short_codes','SELECT')
                  AND NOT has_table_privilege('app_support','public.appointment_short_challenges','SELECT,INSERT,UPDATE,DELETE')
                  AND NOT has_table_privilege('app_runtime','public.appointment_short_codes','UPDATE,DELETE,TRUNCATE')
                  AND has_table_privilege('app_runtime','public.appointment_short_challenges','SELECT,INSERT,UPDATE,DELETE')
                  AND has_table_privilege('app_runtime','public.appointment_short_staff_sessions','SELECT,INSERT,UPDATE,DELETE')
            """)).scalar_one()
            return result is True
    finally:
        engine.dispose()


def main():
    try:
        raw, url = connection_values()
        with release_lock(raw):
            current = migrated_version(raw)
            if current == TARGET:
                if schema_ready(raw):
                    print("orion_short_links_0051_ready", flush=True)
                    return 0
                return 3
            if (current != PREVIOUS or os.getenv("ORION_RUN_SHORT_LINK_MIGRATION") != "1"
                    or not previous_ready(raw)):
                return 3
            with tempfile.TemporaryDirectory(prefix="orion-short-links-") as folder:
                perform_backup(url, storage_values(), Path(folder) / "backup.dump",
                               backup_context="short-links-0051",
                               schema_revision=PREVIOUS, preserve_ownership=True)
            if migrated_version(raw) != PREVIOUS:
                return 3
            run = subprocess.run([sys.executable, "-m", "alembic", "upgrade", TARGET],
                env={**os.environ, "ORION_MIGRATION_SWITCH_ROLE": "1"},
                timeout=600, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if run.returncode or migrated_version(raw) != TARGET or not schema_ready(raw):
                return 3
            print("orion_short_links_0051_backup_schema_verified", flush=True)
            return 0
    except Exception:
        print("orion_short_links_0051_failed_closed", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
