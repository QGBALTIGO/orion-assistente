"""Publish the additive audience schema after a verified private backup.

Used only by the API's configured pre-deploy command. Worker/API startup never
opens a migration connection. Existing customer/group rows are not rewritten.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

from scripts.apply_variable_combo_migration import migrated_version
from scripts.backup_and_migrate_commissions import connection_values, perform_backup, storage_values
from scripts.backup_and_migrate_dental_workflows import (
    complete_contract, verify_key_provisioning, SALES_PATIENT_CONTRACT, INSURANCE_CONTRACT,
)
from scripts.backup_and_migrate_exceptional_overlap import release_lock


PREVIOUS = "0091_dental_insurance_plans"
TARGET = "0092_subscription_audience"
AUDIENCE_CONTRACT = """
SELECT COALESCE((
  SELECT count(*)=3 AND bool_and(
    c.relrowsecurity AND c.relforcerowsecurity
    AND has_table_privilege('app_support',c.oid,'SELECT')
    AND has_table_privilege('app_support',c.oid,'INSERT')
    AND NOT has_table_privilege('app_support',c.oid,'TRUNCATE')
    AND NOT has_table_privilege('app_runtime',c.oid,'INSERT,UPDATE,DELETE,TRUNCATE')
  ) FROM pg_class c WHERE c.oid IN (
    to_regclass('public.subscription_audience_contacts'),
    to_regclass('public.subscription_audience_group_links'),
    to_regclass('public.subscription_audience_operations'))
),false)
AND has_table_privilege('app_support',to_regclass('public.subscription_audience_contacts'),'UPDATE')
AND NOT has_table_privilege('app_support',to_regclass('public.subscription_audience_contacts'),'DELETE')
AND has_table_privilege('app_support',to_regclass('public.subscription_audience_group_links'),'UPDATE')
AND has_table_privilege('app_support',to_regclass('public.subscription_audience_group_links'),'DELETE')
AND NOT has_table_privilege('app_support',to_regclass('public.subscription_audience_operations'),'UPDATE,DELETE')
AND NOT has_table_privilege('app_runtime',to_regclass('public.subscription_audience_contacts'),'SELECT')
AND has_column_privilege('app_runtime','public.subscription_audience_contacts','chatbot_enabled','SELECT')
AND has_column_privilege('app_runtime','public.subscription_audience_contacts','audio_transcription_enabled','SELECT')
AND NOT has_column_privilege('app_runtime','public.subscription_audience_contacts','service_username','SELECT')
AND NOT has_column_privilege('app_runtime','public.subscription_audience_contacts','service_password_encrypted','SELECT')
AND NOT has_table_privilege('app_runtime',to_regclass('public.subscription_audience_group_links'),'SELECT')
AND NOT has_table_privilege('app_runtime',to_regclass('public.subscription_audience_operations'),'SELECT')
AND (SELECT count(*)=7 FROM pg_policy WHERE polrelid IN (
    to_regclass('public.subscription_audience_contacts'),
    to_regclass('public.subscription_audience_group_links'),
    to_regclass('public.subscription_audience_operations')))
AND EXISTS(SELECT 1 FROM pg_policy
  WHERE polrelid=to_regclass('public.subscription_audience_contacts')
    AND polname='subscription_audience_contacts_runtime' AND polcmd='r'
    AND pg_get_expr(polqual,polrelid) LIKE '%app.tenant_id%')
AND (SELECT count(*)=3 FROM pg_constraint
  WHERE conrelid IN (to_regclass('public.subscription_audience_group_links'),
                    to_regclass('public.subscription_audience_operations'))
    AND contype='f' AND array_length(conkey,1)=2 AND convalidated)
AND (SELECT count(*)=3 FROM pg_constraint
  WHERE conrelid=to_regclass('public.subscription_audience_contacts')
    AND contype='u' AND array_length(conkey,1)=2 AND convalidated)
AND EXISTS(SELECT 1 FROM pg_constraint
  WHERE conrelid=to_regclass('public.subscription_audience_operations')
    AND contype='u' AND pg_get_constraintdef(oid)='UNIQUE (tenant_id, idempotency_key)')
AND COALESCE((SELECT count(*)=2 AND bool_and(indisvalid AND indisready)
  FROM pg_index WHERE indexrelid IN (
    to_regclass('public.ix_subscription_audience_contacts_active'),
    to_regclass('public.ix_subscription_audience_group_contact'))),false)
"""


def read_contract(raw: str, query: str) -> bool:
    engine = create_engine(raw, poolclass=NullPool, connect_args={
        "options": "-c default_transaction_read_only=on -c statement_timeout=12000"
    })
    try:
        with engine.connect() as db:
            return db.scalar(text(query)) is True
    finally:
        engine.dispose()


def audience_contract(raw: str) -> bool:
    return read_contract(raw, AUDIENCE_CONTRACT)


def preserved_contract(raw: str) -> bool:
    # Inspect contracts independently of the old release gate's pinned head.
    return (complete_contract(raw, include_visits=True, include_sales=True)
            and read_contract(raw, "SELECT COALESCE((" + SALES_PATIENT_CONTRACT
                              + "),false) AND COALESCE((" + INSURANCE_CONTRACT + "),false)"))


def schema_ready(raw: str) -> bool:
    return (migrated_version(raw) == TARGET
            and preserved_contract(raw)
            and audience_contract(raw))


def main() -> int:
    try:
        raw, url = connection_values()
        runtime = make_url(os.environ["APP_DATABASE_URL"])
        if (runtime.host, runtime.port or 5432, runtime.database) != (url.host, url.port or 5432, url.database):
            raise RuntimeError("subscription_audience_database_scope_mismatch")
        with release_lock(raw):
            current = migrated_version(raw)
            if current not in (PREVIOUS, TARGET):
                raise RuntimeError("subscription_audience_source_schema_not_approved")
            verify_key_provisioning(raw)
            if not preserved_contract(raw):
                raise RuntimeError("subscription_audience_predecessor_contract_invalid")
            if current != TARGET:
                with tempfile.TemporaryDirectory(prefix="orion-subscription-audience-") as folder:
                    perform_backup(url, storage_values(), Path(folder) / "backup.dump",
                                   backup_context="subscription-audience-0092", schema_revision=current,
                                   preserve_ownership=True)
                if migrated_version(raw) != current or not preserved_contract(raw):
                    raise RuntimeError("subscription_audience_source_changed_after_backup")
                subprocess.run([sys.executable, "-m", "alembic", "upgrade", TARGET],
                               env={**os.environ, "ORION_MIGRATION_SWITCH_ROLE": "1"},
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=600, check=True)
            if not schema_ready(raw):
                raise RuntimeError("subscription_audience_schema_invalid")
        print("orion_subscription_audience_schema_ready", flush=True)
        return 0
    except Exception:
        print("orion_subscription_audience_release_failed_closed", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
