"""Reviewed publication release and compatible predeploy dispatch for monthly goals.

The historical main() still performs only 0035 -> 0036. The deployed entrypoint
then dispatches the separately reviewed 0036 -> 0037 release. Both migrations
require their own verified private backup before DDL. This does not
publish sites, alter campaigns, send messages or touch WhatsApp sessions. An old
API can continue public reads after migration; its site writes fail closed until
the current API starts. Recovery uses a forward fix, not a blind code downgrade.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile

from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from scripts.apply_variable_combo_migration import migrated_version
from scripts.backup_and_migrate_commissions import connection_values, storage_values, perform_backup

PREVIOUS = "0035_public_amenities"
TARGET = "0036_site_publication"


def schema_contract_ready(raw):
    """Read-only guard against a partial/tampered schema with a current revision."""
    engine = create_engine(raw, poolclass=NullPool, connect_args={"options":
        "-c default_transaction_read_only=on -c statement_timeout=10000 -c lock_timeout=3000"})
    try:
        with engine.connect() as db:
            tables = db.execute(text("""SELECT relname,relrowsecurity,relforcerowsecurity,pg_get_userbyid(relowner)
                FROM pg_class WHERE relnamespace='public'::regnamespace
                AND relname IN ('public_site_slugs','public_site_publications')""")).all()
            if len(tables) != 2 or not all(row[1] and row[2] and row[3] == "app_migrator" for row in tables):
                return False
            functions = db.execute(text("""SELECT p.proname,pg_get_userbyid(p.proowner),p.prosecdef,p.proconfig,
                has_function_privilege('app_runtime',p.oid,'EXECUTE'),
                has_function_privilege('app_support',p.oid,'EXECUTE')
                FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public'
                AND p.proname IN ('resolve_published_site','public_site_slug_available',
                                  'guard_site_publication_writer','site_history_immutable')""")).all()
            finite = {"resolve_published_site", "public_site_slug_available"}
            if len(functions) != 4 or not all(
                row[1] == "app_migrator" and row[2] == (row[0] in finite)
                and row[3] == ["search_path=pg_catalog, public"]
                and row[4] == (row[0] in finite) and not row[5]
                for row in functions
            ):
                return False
            triggers = db.execute(text("""SELECT c.relname,t.tgname,t.tgtype
                FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
                WHERE NOT t.tgisinternal AND t.tgenabled='O'
                  AND c.relnamespace='public'::regnamespace
                  AND t.tgname IN ('guard_site_publication_writer','site_history_immutable')""")).all()
            if set(triggers) != {
                ("management_site_preferences", "guard_site_publication_writer", 31),
                ("public_site_publications", "site_history_immutable", 27),
            }:
                return False
            return bool(db.execute(text("""SELECT
                to_regclass('public.public_site_slugs_pkey') IS NOT NULL
                AND to_regclass('public.uq_public_site_canonical_tenant') IS NOT NULL
                AND to_regclass('public.uq_site_publication_version') IS NOT NULL
                AND EXISTS(SELECT 1 FROM information_schema.columns
                    WHERE table_schema='public' AND table_name='management_site_preferences' AND column_name='public_version')
                AND EXISTS(SELECT 1 FROM information_schema.columns
                    WHERE table_schema='public' AND table_name='management_site_preferences' AND column_name='public_disabled_at')""")).scalar_one())
    finally:
        engine.dispose()


def main():
    try:
        raw, url = connection_values()
        current = migrated_version(raw)
        if current == TARGET:
            if not schema_contract_ready(raw):
                print("orion_site_publication_schema_contract_failed", file=sys.stderr)
                return 3
            print("orion_site_publication_schema_ready", flush=True)
            return 0
        if current != PREVIOUS:
            print("orion_site_publication_unreviewed_schema_revision", file=sys.stderr)
            return 2
        storage = storage_values()
        with tempfile.TemporaryDirectory(prefix="orion-site-publication-") as folder:
            perform_backup(url, storage, Path(folder) / "backup.dump",
                           backup_context="site-publication-0036", schema_revision=PREVIOUS,
                           preserve_ownership=True)
        environment = {**os.environ, "ORION_MIGRATION_SWITCH_ROLE": "1"}
        result = subprocess.run([sys.executable, "-m", "alembic", "upgrade", TARGET],
                                env=environment, check=False, timeout=600,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if result.returncode != 0 or migrated_version(raw) != TARGET or not schema_contract_ready(raw):
            print("orion_site_publication_schema_upgrade_failed", file=sys.stderr)
            return 3
        print("orion_site_publication_schema_0036_verified_after_backup", flush=True)
        return 0
    except Exception:
        # Connection strings, database rows, dump content and S3 credentials do
        # not belong in deployment logs, including failures from dependencies.
        print("orion_site_publication_predeploy_failed_closed", file=sys.stderr)
        return 3


def predeploy_main():
    """Keep the configured Railway command while accepting only the reviewed chain."""
    from scripts import backup_and_migrate_recurring_goals as goals
    try:
        raw, _url = connection_values()
        current = migrated_version(raw)
        if current == PREVIOUS:
            result = main()
            if result:
                return result
        elif current not in {TARGET, goals.TARGET}:
            print("orion_site_publication_unreviewed_schema_revision", file=sys.stderr)
            return 2
        return goals.main()
    except Exception:
        print("orion_site_publication_predeploy_failed_closed", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(predeploy_main())
