"""One-time backed-up production migration for optional combo duration.

This is a pre-deploy step, never an API request or worker startup operation.
All checks fail closed; no secrets, customer data or driver exceptions are
printed. The current 0054 schema stays usable while 0055 is being applied.
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
    connection_values, storage_values, perform_backup,
)
from scripts.backup_and_migrate_short_links import schema_ready as short_links_ready

PREVIOUS = "0054_appointment_short_links"
TARGET = "0055_combo_duration_override"


@contextmanager
def release_lock(raw):
    engine = create_engine(
        raw, poolclass=NullPool,
        connect_args={"options": "-c statement_timeout=30000 -c lock_timeout=10000"},
    )
    try:
        with engine.connect() as db:
            db.execute(text("SET ROLE app_migrator"))
            assert db.execute(text("SELECT current_user")).scalar_one() == "app_migrator"
            db.commit()
            if not db.execute(text("SELECT pg_try_advisory_lock(1330792783,55)")).scalar_one():
                raise RuntimeError("combo_duration_schema_busy")
            db.commit()
            try:
                yield
            finally:
                db.execute(text("SELECT pg_advisory_unlock(1330792783,55)"))
                db.commit()
    finally:
        engine.dispose()


def schema_ready(raw):
    engine = create_engine(
        raw, poolclass=NullPool,
        connect_args={"options": "-c default_transaction_read_only=on -c statement_timeout=10000"},
    )
    try:
        with engine.connect() as db:
            column = db.execute(text("""
                SELECT data_type,is_nullable,column_default
                FROM information_schema.columns
                WHERE table_schema='public' AND table_name='service_packages'
                  AND column_name='duration_override_min'
            """)).first()
            if not column or tuple(column) != ("integer", "YES", None):
                return False
            constraint = db.execute(text("""
                SELECT convalidated,pg_get_constraintdef(oid)
                FROM pg_constraint
                WHERE conrelid='public.service_packages'::regclass
                  AND conname='ck_combo_duration_override'
            """)).first()
            if not constraint or not constraint[0] or not all(
                part in constraint[1] for part in
                ("duration_override_min", "kind", "1440")
            ):
                return False
            definition = db.execute(text("""
                SELECT pg_get_functiondef(
                    to_regprocedure('public.refresh_service_package(uuid,uuid)')
                )
            """)).scalar_one_or_none()
            return bool(definition and "p.duration_override_min" in definition
                        and "variable_source" in definition
                        and "p.synthetic_service_id" in definition)
    finally:
        engine.dispose()


def main():
    try:
        raw, url = connection_values()
        with release_lock(raw):
            version = migrated_version(raw)
            if version == TARGET:
                if schema_ready(raw):
                    print("orion_combo_duration_0055_schema_verified", flush=True)
                    return 0
                return 3
            if version != PREVIOUS or not short_links_ready(raw):
                print("orion_combo_duration_unreviewed_source_revision", file=sys.stderr)
                return 2
            with tempfile.TemporaryDirectory(prefix="orion-combo-duration-") as folder:
                perform_backup(
                    url, storage_values(), Path(folder) / "backup.dump",
                    backup_context="combo-duration-0055",
                    schema_revision=PREVIOUS,
                    preserve_ownership=True,
                )
            if migrated_version(raw) != PREVIOUS or not short_links_ready(raw):
                return 3
            result = subprocess.run(
                [sys.executable, "-m", "alembic", "upgrade", TARGET],
                env={**os.environ, "ORION_MIGRATION_SWITCH_ROLE": "1"},
                check=False, timeout=600,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            if result.returncode or migrated_version(raw) != TARGET or not schema_ready(raw):
                return 3
            print("orion_combo_duration_0055_backup_and_schema_verified", flush=True)
            return 0
    except Exception:
        print("orion_combo_duration_0055_failed_closed", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
