"""Back up the published schema before adding the service archive marker."""
from contextlib import contextmanager
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from scripts.apply_variable_combo_migration import migrated_version
from scripts.backup_and_migrate_commissions import connection_values, storage_values, perform_backup
from scripts import backup_and_migrate_customer_legacy_history as previous
from scripts.wait_for_service_archive_schema import schema_ready as contract_ready

PREVIOUS = "0078_customer_legacy_history"
TARGET = "0079_service_archive"


@contextmanager
def release_lock(raw):
    engine = create_engine(raw, poolclass=NullPool, connect_args={
        "options": "-c statement_timeout=30000 -c lock_timeout=10000"})
    try:
        with engine.connect() as db:
            if db.execute(text("SELECT current_user")).scalar_one() != "app_migrator":
                db.execute(text("SET ROLE app_migrator"))
                db.commit()
            if not db.execute(text("SELECT pg_try_advisory_lock(1330792783,79)")).scalar_one():
                raise RuntimeError("service_archive_migration_busy")
            db.commit()
            try:
                yield
            finally:
                db.execute(text("SELECT pg_advisory_unlock(1330792783,79)"))
                db.commit()
    finally:
        engine.dispose()


def schema_ready(raw):
    return migrated_version(raw) == TARGET and contract_ready(raw)


def main():
    try:
        raw, url = connection_values()
        with release_lock(raw):
            current = migrated_version(raw)
            if current == TARGET:
                valid = schema_ready(raw)
                print("orion_service_archive_schema_ready" if valid else "orion_service_archive_schema_invalid", flush=True)
                return 0 if valid else 3
            if current != PREVIOUS or not previous.schema_ready(raw):
                print("orion_service_archive_source_schema_invalid", file=sys.stderr)
                return 3
            with tempfile.TemporaryDirectory(prefix="orion-service-archive-0079-") as folder:
                perform_backup(url, storage_values(), Path(folder) / "backup.dump",
                    backup_context="service-archive-0079", schema_revision=PREVIOUS,
                    preserve_ownership=True)
            if migrated_version(raw) != PREVIOUS or not previous.schema_ready(raw):
                print("orion_service_archive_source_changed_after_backup", file=sys.stderr)
                return 3
            subprocess.run([sys.executable, "-m", "alembic", "upgrade", TARGET],
                env={**os.environ, "ORION_MIGRATION_SWITCH_ROLE": "1"},
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=600, check=True)
            valid = schema_ready(raw)
            print("orion_service_archive_schema_ready" if valid else "orion_service_archive_schema_invalid", flush=True)
            return 0 if valid else 3
    except Exception:
        print("orion_service_archive_release_failed_closed", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
