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
from scripts.backup_and_migrate_exceptional_overlap import TARGET as PREVIOUS, release_lock
from scripts.check_dental_key_history import ciphertext_exists
from scripts.wait_for_dental_schema import schema_ready as dental_contract
from scripts.wait_for_exceptional_overlap_schema import schema_ready as overlap_contract


TARGET = "0086_dental_laboratories"
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


def workflows_ready(raw):
    if migrated_version(raw) != TARGET or not predecessor_contract(raw):
        return False
    engine = create_engine(raw, poolclass=NullPool, connect_args={
        "options": "-c default_transaction_read_only=on -c statement_timeout=12000"
    })
    try:
        with engine.connect() as db:
            return db.scalar(text(WORKFLOW_CONTRACT)) is True
    finally:
        engine.dispose()


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
            if current not in (PREVIOUS, TARGET):
                raise RuntimeError("dental_workflow_source_schema_not_approved")
            verify_key_provisioning(raw)
            if current != TARGET:
                if not predecessor_contract(raw):
                    raise RuntimeError("dental_workflow_source_schema_not_approved")
                with tempfile.TemporaryDirectory(prefix="orion-dental-workflows-") as folder:
                    perform_backup(url, storage_values(), Path(folder) / "backup.dump",
                                   backup_context="dental-workflows-0086", schema_revision=PREVIOUS,
                                   preserve_ownership=True)
                if migrated_version(raw) != PREVIOUS or not predecessor_contract(raw):
                    raise RuntimeError("dental_workflow_source_changed_after_backup")
                subprocess.run([sys.executable, "-m", "alembic", "upgrade", TARGET],
                               env={**os.environ, "ORION_MIGRATION_SWITCH_ROLE": "1"},
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=600, check=True)
            if not workflows_ready(raw):
                raise RuntimeError("dental_workflow_schema_invalid")
        print("orion_dental_workflow_schema_ready", flush=True)
        return 0
    except Exception:
        print("orion_dental_workflow_release_failed_closed", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
