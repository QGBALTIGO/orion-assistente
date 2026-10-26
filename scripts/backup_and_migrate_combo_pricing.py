"""Guarded one-time production migration for variable-priced combos.

Never used by ordinary API/worker startup. The full database is dumped to
a private S3 object, read back and SHA-256 verified before SQL changes.
"""
from pathlib import Path
import sys
import tempfile

from scripts.backup_and_migrate_commissions import (
    connection_values, storage_values, perform_backup,
)
from scripts.apply_variable_combo_migration import (
    PREVIOUS, TARGET, main as migrate, migrated_version,
)


def main():
    try:
        raw, url = connection_values()
        current = migrated_version(raw)
        if current == TARGET:
            print("combo_variable_schema_already_ready", flush=True)
            return 0
        if current != PREVIOUS:
            print("combo_variable_migration_wrong_origin", file=sys.stderr)
            return 2
        storage = storage_values()
        with tempfile.TemporaryDirectory(prefix="orion-combo-release-") as folder:
            perform_backup(url, storage, Path(folder) / "backup.dump",
                           backup_context="variable-combo-0034",
                           schema_revision=PREVIOUS)
        outcome = migrate()
        if outcome:
            return outcome
        if migrated_version(raw) != TARGET:
            print("combo_variable_schema_not_verified", file=sys.stderr)
            return 3
        print("combo_variable_schema_0034_published_after_backup", flush=True)
        return 0
    except Exception:
        # Never log driver exceptions that may include hostnames or connection data.
        print("combo_variable_release_preflight_or_backup_failed", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
