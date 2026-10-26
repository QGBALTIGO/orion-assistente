"""Guarded, one-time public amenities release for Railway pre-deploy.

Uses the same scoped migrator, reviewed backup bucket, pg_dump verification,
and checksum-tested S3 upload as the existing 0034 deployment procedure.
Fail closed before exposing new site preferences if schema is not ready.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from scripts.apply_variable_combo_migration import migrated_version
from scripts.backup_and_migrate_commissions import (
    connection_values,
    storage_values,
    perform_backup,
)

PREVIOUS = "0034_variable_combo_pricing"
TARGET = "0035_public_amenities"


def main() -> int:
    try:
        raw, url = connection_values()
        current = migrated_version(raw)
        if current == TARGET:
            print("orion_public_amenities_schema_ready", flush=True)
            return 0
        if current != PREVIOUS:
            print("orion_public_amenities_unreviewed_schema_revision", file=sys.stderr)
            return 2

        # Back up the entire database before any DDL. Upload and checksum
        # verification happen in perform_backup; failure aborts this deployment.
        storage = storage_values()
        with tempfile.TemporaryDirectory(prefix="orion-public-amenities-") as folder:
            perform_backup(
                url, storage, Path(folder) / "backup.dump",
                backup_context="public-amenities-0035",
                schema_revision=PREVIOUS,
            )

        environment = {**os.environ, "ORION_MIGRATION_SWITCH_ROLE": "1"}
        completed = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", TARGET],
            env=environment, check=False,
        )
        if completed.returncode != 0 or migrated_version(raw) != TARGET:
            print("orion_public_amenities_schema_upgrade_failed", file=sys.stderr)
            return 3
        print("orion_public_amenities_schema_0035_verified_after_backup", flush=True)
        return 0
    except Exception:
        # Do not leak connection strings, tenant data or S3 credentials to logs.
        print("orion_public_amenities_predeploy_failed_closed", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
