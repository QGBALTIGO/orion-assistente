"""0039 -> 0040 after a verified private backup; never send or schedule messages.

Repeated deployments perform read-only schema checks. The only DDL is the
reviewed, nullable professional contact column and its validation constraint.
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
from scripts.backup_and_migrate_commissions import connection_values, storage_values, perform_backup
from scripts.backup_and_migrate_registration import schema_contract_ready as registration_contract_ready

PREVIOUS = "0039_registration_confirmation"
TARGET = "0040_professional_phone"
LOCK_SCOPE, LOCK_RELEASE = 0x4F52494F, 40


def normalized(value):
    return "".join((value or "").split())


@contextmanager
def release_lock(raw):
    engine = create_engine(raw, poolclass=NullPool, connect_args={
        "options": "-c statement_timeout=30000 -c lock_timeout=10000"})
    try:
        with engine.connect() as db:
            db.execute(text("SET ROLE app_migrator"))
            if db.execute(text("SELECT current_user")).scalar_one() != "app_migrator":
                raise RuntimeError("professional_phone_migrator_role_required")
            db.commit()
            acquired = db.execute(text("SELECT pg_try_advisory_lock(:scope,:release)"),
                                  {"scope": LOCK_SCOPE, "release": LOCK_RELEASE}).scalar_one()
            db.commit()
            if not acquired:
                raise RuntimeError("professional_phone_release_in_progress")
            try:
                yield
            finally:
                db.execute(text("SELECT pg_advisory_unlock(:scope,:release)"),
                           {"scope": LOCK_SCOPE, "release": LOCK_RELEASE})
                db.commit()
    finally:
        engine.dispose()


def professional_contract_ready(raw, *, expect_phone):
    """Check the existing tenant boundary as well as the exact additive column."""
    engine = create_engine(raw, poolclass=NullPool, connect_args={
        "options": "-c default_transaction_read_only=on -c statement_timeout=10000 -c lock_timeout=3000"})
    try:
        with engine.connect() as db:
            table = db.execute(text("""
                SELECT relkind,relrowsecurity,relforcerowsecurity,pg_get_userbyid(relowner),
                  has_table_privilege('app_runtime',oid,'SELECT')
                    AND has_table_privilege('app_runtime',oid,'INSERT')
                    AND has_table_privilege('app_runtime',oid,'UPDATE')
                    AND has_table_privilege('app_runtime',oid,'DELETE'),
                  has_table_privilege('app_runtime',oid,'TRUNCATE,REFERENCES,TRIGGER'),
                  has_table_privilege('app_support',oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER'),
                  EXISTS(SELECT 1 FROM aclexplode(COALESCE(relacl,acldefault('r',relowner))) a
                    WHERE a.grantee NOT IN(relowner,(SELECT oid FROM pg_roles WHERE rolname='app_runtime'))
                      OR (a.grantee=(SELECT oid FROM pg_roles WHERE rolname='app_runtime') AND a.is_grantable)),
                  EXISTS(SELECT 1 FROM pg_attribute a WHERE a.attrelid=c.oid AND a.attacl IS NOT NULL)
                FROM pg_class c WHERE c.relnamespace='public'::regnamespace AND c.relname='professionals'
            """)).first()
            if tuple(table or ()) != ('r', True, True, 'app_migrator', True, False, False, False, False):
                return False
            policies = db.execute(text("""
                SELECT polname,polcmd,polpermissive,
                  ARRAY(SELECT rolname FROM pg_roles WHERE oid=ANY(polroles) ORDER BY rolname),
                  pg_get_expr(polqual,polrelid),pg_get_expr(polwithcheck,polrelid)
                FROM pg_policy WHERE polrelid='public.professionals'::regclass
            """)).all()
            tenant_rule = normalized("(tenant_id = (NULLIF(current_setting('app.tenant_id'::text, true), ''::text))::uuid)")
            expected = {'professionals_migrator': ('app_migrator', 'true'),
                        'professionals_runtime': ('app_runtime', tenant_rule)}
            if len(policies) != 2:
                return False
            for name, command, permissive, roles, using, check in policies:
                if name not in expected:
                    return False
                role, rule = expected[name]
                if command != '*' or not permissive or roles != [role] or normalized(using) != rule or normalized(check) != rule:
                    return False
            column = db.execute(text("""
                SELECT udt_name,is_nullable,character_maximum_length,column_default
                FROM information_schema.columns WHERE table_schema='public'
                  AND table_name='professionals' AND column_name='phone_e164'
            """)).first()
            constraint = db.execute(text("""
                SELECT convalidated,condeferrable,condeferred,pg_get_constraintdef(oid)
                FROM pg_constraint WHERE conrelid='public.professionals'::regclass
                  AND conname='ck_professional_phone_e164'
            """)).first()
            if not expect_phone:
                return column is None and constraint is None
            version = db.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            # Migration 0052 extends this check to BR+PY. Require the exact,
            # reviewed SQL for that revision rather than weakening the guard.
            baseline = r"CHECK (((phone_e164 IS NULL) OR ((phone_e164)::text ~ '^\+55[1-9][0-9]{9,10}
                    and constraint is not None and tuple(constraint[:3]) == (True, False, False)
                    and normalized(constraint[3]) == normalized(expected_check))
    finally:
        engine.dispose()


def schema_contract_ready(raw):
    return registration_contract_ready(raw) and professional_contract_ready(raw, expect_phone=True)


def source_contract_ready(raw):
    return registration_contract_ready(raw) and professional_contract_ready(raw, expect_phone=False)


def main():
    try:
        raw, url = connection_values()
        with release_lock(raw):
            current = migrated_version(raw)
            if current not in {PREVIOUS, TARGET}:
                print("orion_professional_phone_unreviewed_schema_revision", file=sys.stderr)
                return 2
            if current == TARGET:
                if not schema_contract_ready(raw):
                    print("orion_professional_phone_schema_contract_failed", file=sys.stderr)
                    return 3
                print("orion_professional_phone_schema_ready", flush=True)
                return 0
            if not source_contract_ready(raw):
                print("orion_professional_phone_source_contract_failed", file=sys.stderr)
                return 3
            with tempfile.TemporaryDirectory(prefix="orion-professional-phone-") as folder:
                perform_backup(url, storage_values(), Path(folder) / "backup.dump",
                               backup_context="professional-phone-0040", schema_revision=PREVIOUS,
                               preserve_ownership=True)
            if migrated_version(raw) != PREVIOUS or not source_contract_ready(raw):
                print("orion_professional_phone_source_changed_after_backup", file=sys.stderr)
                return 3
            result = subprocess.run([sys.executable, "-m", "alembic", "upgrade", TARGET],
                                    env={**os.environ, "ORION_MIGRATION_SWITCH_ROLE": "1"},
                                    check=False, timeout=600, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
            if result.returncode or migrated_version(raw) != TARGET or not schema_contract_ready(raw):
                print("orion_professional_phone_upgrade_failed", file=sys.stderr)
                return 3
            print("orion_professional_phone_schema_0040_verified_after_backup", flush=True)
            return 0
    except Exception:
        print("orion_professional_phone_predeploy_failed_closed", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
::text)))"
            paraguay = r"CHECK (((phone_e164 IS NULL) OR ((phone_e164)::text ~ '^(\+55[1-9][0-9]{9,10}|\+5959[0-9]{8})
                    and constraint is not None and tuple(constraint[:3]) == (True, False, False)
                    and normalized(constraint[3]) == normalized(expected_check))
    finally:
        engine.dispose()


def schema_contract_ready(raw):
    return registration_contract_ready(raw) and professional_contract_ready(raw, expect_phone=True)


def source_contract_ready(raw):
    return registration_contract_ready(raw) and professional_contract_ready(raw, expect_phone=False)


def main():
    try:
        raw, url = connection_values()
        with release_lock(raw):
            current = migrated_version(raw)
            if current not in {PREVIOUS, TARGET}:
                print("orion_professional_phone_unreviewed_schema_revision", file=sys.stderr)
                return 2
            if current == TARGET:
                if not schema_contract_ready(raw):
                    print("orion_professional_phone_schema_contract_failed", file=sys.stderr)
                    return 3
                print("orion_professional_phone_schema_ready", flush=True)
                return 0
            if not source_contract_ready(raw):
                print("orion_professional_phone_source_contract_failed", file=sys.stderr)
                return 3
            with tempfile.TemporaryDirectory(prefix="orion-professional-phone-") as folder:
                perform_backup(url, storage_values(), Path(folder) / "backup.dump",
                               backup_context="professional-phone-0040", schema_revision=PREVIOUS,
                               preserve_ownership=True)
            if migrated_version(raw) != PREVIOUS or not source_contract_ready(raw):
                print("orion_professional_phone_source_changed_after_backup", file=sys.stderr)
                return 3
            result = subprocess.run([sys.executable, "-m", "alembic", "upgrade", TARGET],
                                    env={**os.environ, "ORION_MIGRATION_SWITCH_ROLE": "1"},
                                    check=False, timeout=600, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
            if result.returncode or migrated_version(raw) != TARGET or not schema_contract_ready(raw):
                print("orion_professional_phone_upgrade_failed", file=sys.stderr)
                return 3
            print("orion_professional_phone_schema_0040_verified_after_backup", flush=True)
            return 0
    except Exception:
        print("orion_professional_phone_predeploy_failed_closed", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
::text)))"
            expected_check = paraguay if version == "0052_paraguay_phone" else baseline
            return (tuple(column or ()) == ('varchar', 'YES', 16, None)
                    and constraint is not None and tuple(constraint[:3]) == (True, False, False)
                    and normalized(constraint[3]) == normalized(expected_check))
    finally:
        engine.dispose()


def schema_contract_ready(raw):
    return registration_contract_ready(raw) and professional_contract_ready(raw, expect_phone=True)


def source_contract_ready(raw):
    return registration_contract_ready(raw) and professional_contract_ready(raw, expect_phone=False)


def main():
    try:
        raw, url = connection_values()
        with release_lock(raw):
            current = migrated_version(raw)
            if current not in {PREVIOUS, TARGET}:
                print("orion_professional_phone_unreviewed_schema_revision", file=sys.stderr)
                return 2
            if current == TARGET:
                if not schema_contract_ready(raw):
                    print("orion_professional_phone_schema_contract_failed", file=sys.stderr)
                    return 3
                print("orion_professional_phone_schema_ready", flush=True)
                return 0
            if not source_contract_ready(raw):
                print("orion_professional_phone_source_contract_failed", file=sys.stderr)
                return 3
            with tempfile.TemporaryDirectory(prefix="orion-professional-phone-") as folder:
                perform_backup(url, storage_values(), Path(folder) / "backup.dump",
                               backup_context="professional-phone-0040", schema_revision=PREVIOUS,
                               preserve_ownership=True)
            if migrated_version(raw) != PREVIOUS or not source_contract_ready(raw):
                print("orion_professional_phone_source_changed_after_backup", file=sys.stderr)
                return 3
            result = subprocess.run([sys.executable, "-m", "alembic", "upgrade", TARGET],
                                    env={**os.environ, "ORION_MIGRATION_SWITCH_ROLE": "1"},
                                    check=False, timeout=600, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
            if result.returncode or migrated_version(raw) != TARGET or not schema_contract_ready(raw):
                print("orion_professional_phone_upgrade_failed", file=sys.stderr)
                return 3
            print("orion_professional_phone_schema_0040_verified_after_backup", flush=True)
            return 0
    except Exception:
        print("orion_professional_phone_predeploy_failed_closed", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
