"""Apply the reviewed dental workflows after a verified private backup."""
import base64
import binascii
import json
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
from scripts.backup_and_migrate_exceptional_overlap import TARGET as LEGACY, release_lock
from scripts.check_dental_key_history import ciphertext_exists
from scripts.wait_for_dental_schema import schema_ready as dental_contract
from scripts.wait_for_exceptional_overlap_schema import schema_ready as overlap_contract


LABORATORIES = "0086_dental_laboratories"
PREVIOUS = "0088_dental_sales_opportunities"
SALES_PATIENT = "0089_dental_sales_patient_link"
TARGET = "0091_dental_insurance_plans"
WORKFLOW_CONTRACT = """
SELECT COALESCE((
    SELECT count(*)=2 AND bool_and(
      c.relrowsecurity AND c.relforcerowsecurity
      AND has_table_privilege('app_runtime',c.oid,'SELECT')
      AND has_table_privilege('app_runtime',c.oid,'INSERT')
      AND has_table_privilege('app_runtime',c.oid,'UPDATE')
      AND NOT has_table_privilege('app_runtime',c.oid,'DELETE,TRUNCATE')
      AND NOT has_table_privilege('app_support',c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE')
    ) FROM pg_catalog.pg_class c
    WHERE c.oid IN (pg_catalog.to_regclass('public.dental_intake_templates'),
                    pg_catalog.to_regclass('public.dental_laboratories'))
),false) AND EXISTS (
    SELECT 1 FROM pg_catalog.pg_constraint
    WHERE conrelid=pg_catalog.to_regclass('public.dental_records')
      AND conname='fk_dental_prosthesis_predecessor' AND convalidated
) AND (
    SELECT count(*)=4 AND bool_and(indisvalid AND indisready)
    FROM pg_catalog.pg_index
    WHERE indexrelid IN (
      pg_catalog.to_regclass('public.ix_dental_supply_movement_history'),
      pg_catalog.to_regclass('public.uq_dental_prosthesis_successor'),
      pg_catalog.to_regclass('public.ix_dental_prosthesis_worklist'),
      pg_catalog.to_regclass('public.uq_dental_laboratory_name')
    )
)
"""


def predecessor_contract(raw):
    return overlap_contract(raw) and dental_contract(raw)


VISIT_CONTRACT = """
SELECT EXISTS (
    SELECT 1 FROM pg_class t WHERE t.oid=to_regclass('public.dental_visit_patient_events')
      AND t.relrowsecurity AND t.relforcerowsecurity
      AND has_table_privilege('app_runtime',t.oid,'SELECT')
      AND has_table_privilege('app_runtime',t.oid,'INSERT')
      AND NOT has_table_privilege('app_runtime',t.oid,'UPDATE,DELETE,TRUNCATE')
      AND NOT has_table_privilege('app_support',t.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE')
) AND (SELECT count(*)=3 FROM pg_policy WHERE polrelid=to_regclass('public.dental_visit_patient_events'))
AND (SELECT count(*)=2 FROM pg_constraint WHERE conrelid=to_regclass('public.dental_visit_patient_events')
       AND contype='f' AND array_length(conkey,1)=2 AND convalidated)
AND EXISTS (SELECT 1 FROM pg_index WHERE indexrelid=to_regclass('public.ix_dental_patient_visits')
            AND indisvalid AND indisready)
"""


SALES_CONTRACT = """
SELECT EXISTS (
    SELECT 1 FROM pg_class t WHERE t.oid=to_regclass('public.dental_sales_opportunities')
      AND t.relrowsecurity AND t.relforcerowsecurity
      AND has_table_privilege('app_runtime',t.oid,'SELECT')
      AND has_table_privilege('app_runtime',t.oid,'INSERT')
      AND NOT has_table_privilege('app_runtime',t.oid,'UPDATE,DELETE,TRUNCATE')
      AND NOT has_table_privilege('app_support',t.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE')
) AND (SELECT count(*)=3 FROM pg_policy WHERE polrelid=to_regclass('public.dental_sales_opportunities'))
AND EXISTS (SELECT 1 FROM pg_index WHERE indexrelid=to_regclass('public.ix_dental_sales_created')
            AND indisvalid AND indisready)
AND EXISTS (SELECT 1 FROM pg_proc p WHERE p.oid=to_regprocedure('public.dental_sales_owner()')
            AND p.prosecdef AND p.proconfig=ARRAY['search_path=pg_catalog']
            AND has_function_privilege('app_runtime',p.oid,'EXECUTE')
            AND NOT has_function_privilege('app_support',p.oid,'EXECUTE'))
"""


SALES_PATIENT_CONTRACT = """
SELECT EXISTS (
    SELECT 1 FROM pg_attribute
    WHERE attrelid=to_regclass('public.dental_sales_opportunities')
      AND attname='patient_id' AND NOT attisdropped
) AND EXISTS (
    SELECT 1 FROM pg_constraint WHERE conrelid=to_regclass('public.dental_sales_opportunities')
      AND conname='fk_dental_sales_patient' AND convalidated
) AND EXISTS (
    SELECT 1 FROM pg_index WHERE indexrelid=to_regclass('public.ix_dental_sales_patient')
      AND indisvalid AND indisready
) AND (SELECT count(*)=2 AND bool_and(
      p.prosecdef AND p.proconfig=ARRAY['search_path=pg_catalog']
      AND has_function_privilege('app_runtime',p.oid,'EXECUTE')
      AND NOT has_function_privilege('app_support',p.oid,'EXECUTE'))
    FROM pg_proc p WHERE p.oid IN (
      to_regprocedure('public.dental_sales_patient_identity(uuid)'),
      to_regprocedure('public.dental_sales_patient_options(text,integer,integer)')))
"""


INSURANCE_CONTRACT = """
SELECT EXISTS (
    SELECT 1 FROM pg_class t WHERE t.oid=to_regclass('public.dental_insurance_plans')
      AND t.relrowsecurity AND t.relforcerowsecurity
      AND has_table_privilege('app_runtime',t.oid,'SELECT')
      AND has_table_privilege('app_runtime',t.oid,'INSERT')
      AND has_table_privilege('app_runtime',t.oid,'UPDATE')
      AND NOT has_table_privilege('app_runtime',t.oid,'DELETE,TRUNCATE')
      AND NOT has_table_privilege('app_support',t.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE')
) AND (SELECT count(*)=2 FROM pg_policy WHERE polrelid=to_regclass('public.dental_insurance_plans'))
AND EXISTS (SELECT 1 FROM pg_index WHERE indexrelid=to_regclass('public.uq_dental_insurance_plan')
            AND indisvalid AND indisready AND indisunique)
"""


def complete_contract(raw, include_visits, include_sales=False):
    if not predecessor_contract(raw):
        return False
    engine = create_engine(raw, poolclass=NullPool, connect_args={
        "options": "-c default_transaction_read_only=on -c statement_timeout=12000"
    })
    try:
        with engine.connect() as db:
            return (db.scalar(text(WORKFLOW_CONTRACT)) is True
                    and (not include_visits or db.scalar(text(VISIT_CONTRACT)) is True)
                    and (not include_sales or db.scalar(text(SALES_CONTRACT)) is True))
    finally:
        engine.dispose()


def workflows_ready(raw):
    revision = migrated_version(raw)
    if revision not in {SALES_PATIENT, TARGET} or not complete_contract(raw, include_visits=True, include_sales=True):
        return False
    engine = create_engine(raw, poolclass=NullPool, connect_args={
        "options": "-c default_transaction_read_only=on -c statement_timeout=12000"
    })
    try:
        with engine.connect() as db:
            return (db.scalar(text(SALES_PATIENT_CONTRACT)) is True
                    and (revision != TARGET or db.scalar(text(INSURANCE_CONTRACT)) is True))
    finally:
        engine.dispose()


def source_ready(raw, revision):
    if revision == SALES_PATIENT:
        return workflows_ready(raw)
    if revision in (LABORATORIES, "0087_dental_patient_visits", PREVIOUS):
        return complete_contract(raw, include_visits=revision != LABORATORIES,
                                 include_sales=revision == PREVIOUS)
    return predecessor_contract(raw)


def verify_key_provisioning(raw):
    configured = os.environ.get("DENTAL_DATA_KEY_B64", "")
    if configured:
        try:
            valid = len(base64.b64decode(configured, validate=True)) == 32
        except (binascii.Error, ValueError):
            valid = False
        if not valid:
            raise RuntimeError("dental_configured_key_is_invalid")
        return

    first_key_allowed = not ciphertext_exists(raw)
    print(json.dumps({"check": "orion_dental_initial_key", "verified": True,
                      "first_key_allowed": first_key_allowed}), flush=True)
    if not first_key_allowed:
        raise RuntimeError("dental_original_key_required")


def main():
    try:
        raw, url = connection_values()
        runtime = make_url(os.environ["APP_DATABASE_URL"])
        if (runtime.host, runtime.port or 5432, runtime.database) != (url.host, url.port or 5432, url.database):
            raise RuntimeError("dental_workflow_database_scope_mismatch")
        with release_lock(raw):
            current = migrated_version(raw)
            if current not in (LEGACY, LABORATORIES, "0087_dental_patient_visits", PREVIOUS, SALES_PATIENT, TARGET):
                raise RuntimeError("dental_workflow_source_schema_not_approved")
            verify_key_provisioning(raw)
            if current != TARGET:
                if not source_ready(raw, current):
                    raise RuntimeError("dental_workflow_source_schema_not_approved")
                with tempfile.TemporaryDirectory(prefix="orion-dental-workflows-") as folder:
                    perform_backup(url, storage_values(), Path(folder) / "backup.dump",
                                   backup_context="dental-insurance-plans-0091", schema_revision=current,
                                   preserve_ownership=True)
                if migrated_version(raw) != current or not source_ready(raw, current):
                    raise RuntimeError("dental_workflow_source_changed_after_backup")
                subprocess.run([sys.executable, "-m", "alembic", "upgrade", TARGET],
                               env={**os.environ, "ORION_MIGRATION_SWITCH_ROLE": "1"},
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=600, check=True)
            if migrated_version(raw) != TARGET or not workflows_ready(raw):
                raise RuntimeError("dental_workflow_schema_invalid")
        print("orion_dental_workflow_schema_ready", flush=True)
        return 0
    except Exception:
        print("orion_dental_workflow_release_failed_closed", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
