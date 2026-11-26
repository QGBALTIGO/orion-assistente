"""Back up the approved live schema and apply only the additive admin-password table.

No credential is read from the environment here and no password is changed.
"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from sqlalchemy.engine import make_url
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool
from app import legacy_admin_identity
from scripts.apply_variable_combo_migration import migrated_version
from scripts.backup_and_migrate_commissions import connection_values, perform_backup, storage_values
from scripts.backup_and_migrate_exceptional_overlap import release_lock
from scripts.backup_and_migrate_subscription_audience import read_contract
from scripts import backup_and_migrate_plan_cycle as previous

SOURCE="0098_manual_pix_successor"
TARGET="0099_admin_personal_password"
CONTRACT="""
SELECT coalesce((
  SELECT c.relrowsecurity AND c.relforcerowsecurity
   AND has_table_privilege('app_support',c.oid,'SELECT')
   AND has_table_privilege('app_support',c.oid,'INSERT')
   AND has_table_privilege('app_support',c.oid,'UPDATE')
   AND NOT has_table_privilege('app_support',c.oid,'DELETE,TRUNCATE')
   AND NOT has_table_privilege('app_runtime',c.oid,'SELECT,INSERT,UPDATE,DELETE')
   AND EXISTS(SELECT 1 FROM pg_policy p WHERE p.polrelid=c.oid AND p.polname='admin_personal_password_support')
  FROM pg_class c WHERE c.oid=to_regclass('public.orion_legacy_admin_passwords')
),false)
"""

def identity_ready(raw):
    # Read-only verification of the existing identity. Never log the binding.
    if not legacy_admin_identity.enabled():
        return False
    username,digest=legacy_admin_identity.configuration_binding()
    engine=create_engine(raw,poolclass=NullPool,connect_args={"options":"-c default_transaction_read_only=on -c statement_timeout=5000"})
    try:
        with engine.connect() as db:
            return db.execute(text("""
                SELECT count(*)=1 FROM public.panel_accounts
                WHERE role='admin' AND auth_provider='legacy_admin'
                  AND email IS NULL AND password_hash IS NULL
                  AND legacy_username=:username AND legacy_credential_digest=:digest
            """),{"username":username,"digest":digest}).scalar_one() is True
    finally:
        engine.dispose()

def main():
    try:
        raw,url=connection_values()
        runtime=make_url(os.environ["APP_DATABASE_URL"])
        if (runtime.host,runtime.port or 5432,runtime.database)!=(url.host,url.port or 5432,url.database):
            raise RuntimeError("database_scope_mismatch")
        with release_lock(raw):
            current=migrated_version(raw)
            if (current not in (SOURCE,TARGET) or not read_contract(raw,previous.CONTRACT)
                    or not previous.preserved_contract(raw) or not previous.audience_contract(raw)
                    or not identity_ready(raw)):
                raise RuntimeError("unapproved_predecessor")
            if current==SOURCE:
                with tempfile.TemporaryDirectory(prefix="orion-admin-personal-password-") as folder:
                    perform_backup(url,storage_values(),Path(folder)/"backup.dump",
                                   backup_context="admin-personal-password",schema_revision=SOURCE,preserve_ownership=True)
                if migrated_version(raw)!=SOURCE:
                    raise RuntimeError("predecessor_changed")
                subprocess.run([sys.executable,"-m","alembic","upgrade",TARGET],
                               env={**os.environ,"ORION_MIGRATION_SWITCH_ROLE":"1"},
                               stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=600,check=True)
            if migrated_version(raw)!=TARGET or not read_contract(raw,CONTRACT):
                raise RuntimeError("admin_password_schema_not_ready")
        print("orion_admin_personal_password_schema_ready",flush=True)
        return 0
    except Exception:
        print("orion_admin_personal_password_release_failed_closed",file=sys.stderr,flush=True)
        return 3

if __name__=="__main__":
    raise SystemExit(main())
