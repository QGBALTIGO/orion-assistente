"""0040 -> 0041: reviewed quick onboarding activation after a private backup.

Only adds the scoped, SECURITY DEFINER function. No user data edits, messages,
catalog activation, publication or new tenant memberships. Repeat deploys
perform read-only contract checks and never create another backup.
"""
from contextlib import contextmanager
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from scripts.apply_variable_combo_migration import migrated_version
from scripts.backup_and_migrate_commissions import (
    connection_values, storage_values, perform_backup,
)
from scripts.backup_and_migrate_professional_phone import (
    schema_contract_ready as phone_contract_ready,
)

PREVIOUS = "0040_professional_phone"
TARGET = "0041_quick_onboarding_activation"
LOCK_SCOPE, LOCK_RELEASE = 0x4F52494F, 41


@contextmanager
def release_lock(raw):
    engine = create_engine(raw, poolclass=NullPool, connect_args={
        "options": "-c statement_timeout=30000 -c lock_timeout=10000"})
    try:
        with engine.connect() as db:
            db.execute(text("SET ROLE app_migrator"))
            if db.execute(text("SELECT current_user")).scalar_one() != "app_migrator":
                raise RuntimeError("quick_onboarding_migrator_role_required")
            db.commit()
            acquired = db.execute(text("SELECT pg_try_advisory_lock(:scope,:release)"),
                {"scope": LOCK_SCOPE, "release": LOCK_RELEASE}).scalar_one()
            db.commit()
            if not acquired:
                raise RuntimeError("quick_onboarding_release_in_progress")
            try:
                yield
            finally:
                db.execute(text("SELECT pg_advisory_unlock(:scope,:release)"),
                           {"scope": LOCK_SCOPE, "release": LOCK_RELEASE})
                db.commit()
    finally:
        engine.dispose()


def schema_contract_ready(raw):
    if not phone_contract_ready(raw):
        return False
    engine = create_engine(raw, poolclass=NullPool, connect_args={
        "options": "-c default_transaction_read_only=on -c statement_timeout=10000 -c lock_timeout=3000"})
    try:
        with engine.connect() as db:
            row = db.execute(text("""
                SELECT p.prokind,p.prosecdef,p.proconfig,
                       pg_get_userbyid(p.proowner),
                       p.prorettype='pg_catalog.void'::regtype,
                       has_function_privilege('app_runtime',p.oid,'EXECUTE'),
                       has_function_privilege('app_support',p.oid,'EXECUTE'),
                       EXISTS (
                         SELECT 1 FROM aclexplode(COALESCE(
                           p.proacl,acldefault('f',p.proowner))) a
                         WHERE a.grantee=0 AND a.privilege_type='EXECUTE'
                       )
                FROM pg_proc p
                WHERE p.oid=to_regprocedure('public.onboarding_activate_quick_tenant(uuid)')
            """)).first()
            return tuple(row or ()) == (
                'f', True, ['search_path=pg_catalog, public'],
                'app_migrator', True, True, False, False,
            )
    finally:
        engine.dispose()


def main():
    try:
        raw, url = connection_values()
        with release_lock(raw):
            current = migrated_version(raw)
            if current not in {PREVIOUS, TARGET}:
                print("orion_quick_onboarding_unreviewed_schema_revision", file=sys.stderr)
                return 2
            if current == TARGET:
                if not schema_contract_ready(raw):
                    print("orion_quick_onboarding_schema_contract_failed", file=sys.stderr)
                    return 3
                print("orion_quick_onboarding_schema_ready", flush=True)
                return 0
            if not phone_contract_ready(raw):
                print("orion_quick_onboarding_source_contract_failed", file=sys.stderr)
                return 3
            with tempfile.TemporaryDirectory(prefix="orion-quick-onboarding-") as folder:
                perform_backup(
                    url, storage_values(), Path(folder)/"backup.dump",
                    backup_context="quick-onboarding-0041", schema_revision=PREVIOUS,
                    preserve_ownership=True,
                )
            if migrated_version(raw) != PREVIOUS or not phone_contract_ready(raw):
                print("orion_quick_onboarding_source_changed_after_backup", file=sys.stderr)
                return 3
            result = subprocess.run(
                [sys.executable, "-m", "alembic", "upgrade", TARGET],
                env={**os.environ, "ORION_MIGRATION_SWITCH_ROLE": "1"},
                check=False, timeout=600, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if result.returncode or migrated_version(raw) != TARGET or not schema_contract_ready(raw):
                print("orion_quick_onboarding_upgrade_failed", file=sys.stderr)
                return 3
            print("orion_quick_onboarding_schema_0041_verified_after_backup", flush=True)
            return 0
    except Exception:
        print("orion_quick_onboarding_predeploy_failed_closed", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
