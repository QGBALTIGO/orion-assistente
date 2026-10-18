"""Sealed, additive 0018 -> 0019 agenda history migration.

Runs only as app_migrator with an explicitly verified database identity. The
reviewed operation adds history metadata, its check and one lookup index. It
does not relabel old appointments or change their business data. Replays only
validate the target in a read-only transaction. There is no repair or downgrade.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.engine import make_url

from scripts import one_time_sales_requests_migration as previous

ROOT = Path(__file__).resolve().parents[1]
SOURCE = "0018_sales_requests"
TARGET = "0019_appointment_lifecycle"
MIGRATION_SHA256 = "50c942f69b661769a6cb9d9d7c2916178c0510f448eae87209f9d56dbef98c57"
SOURCE_HISTORY_SHA256 = "0466f1ca161c4335510b94109f023f31709f0bc0361984d8f1f8d0ef77288aaf"
TARGET_HISTORY_SHA256 = "442893de1fbeb098b9f938926831ae4225e1314d900fa2ab0ee49f467e420ee8"
TABLES = previous.TARGET_TABLES
ADVISORY_LOCK = 681692820119
DETAILS_CONSTRAINT = "ck_appointment_history_details"
HISTORY_INDEX = "ix_appointment_history_timeline"

Stop = previous.Stop
require = previous.require
engine_for = previous.engine_for
catalog = previous.catalog
fingerprint = previous.fingerprint
revision = previous.revision
table_names = previous.table_names


@dataclass(frozen=True, repr=False)
class Settings(previous.Settings):
    @classmethod
    def from_environment(cls):
        require(os.getenv("ORION_RUN_AGENDA_MIGRATION") == "1", "explicit_flag_required")
        # The operational URL may authenticate as a different owner. Always
        # require the explicit switch so deployment cannot silently omit it.
        require(os.getenv("ORION_MIGRATION_SWITCH_ROLE") == "1", "role_switch_required")
        try:
            value = cls(make_url(os.environ["MIGRATION_DATABASE_URL"]),
                        os.getenv("ORION_EXPECTED_DATABASE_HOST", ""),
                        os.getenv("ORION_EXPECTED_DATABASE_NAME", ""), True)
            value.validate()
            return value
        except Stop:
            raise
        except Exception:
            raise Stop("invalid_configuration") from None


def reviewed_migration():
    path = ROOT / "alembic" / "versions" / (TARGET + ".py")
    try:
        body = path.read_bytes().replace(b"\r\n", b"\n")
    except OSError:
        raise Stop("reviewed_migration_missing") from None
    require(hashlib.sha256(body).hexdigest() == MIGRATION_SHA256, "unreviewed_agenda_migration")
    spec = importlib.util.spec_from_file_location("orion_reviewed_" + TARGET, path)
    require(spec is not None, "reviewed_migration_unloadable")
    module = importlib.util.module_from_spec(spec)
    exec(compile(body, str(path), "exec"), module.__dict__)
    require(module.revision == TARGET and module.down_revision == SOURCE and
            module.branch_labels is None and module.depends_on is None, "unreviewed_migration_chain")
    return module


def prepare(conn, settings):
    # Reuse the previously reviewed connection/role/RLS/revision-table checks.
    # Its advisory lock also prevents the preceding one-time operation racing us.
    previous.prepare(conn, settings)
    require(conn.execute(text("SELECT pg_try_advisory_xact_lock(:key)"),
                         {"key": ADVISORY_LOCK}).scalar_one(), "migration_already_running")


def existing_catalog(conn, *, projected=False):
    snapshot = previous.existing_catalog(conn)
    # The preceding helper intentionally excludes sales_requests. Here it is an
    # existing table and must be protected together with every other table.
    snapshot["tables"] = catalog(conn)
    if projected:
        definition = deepcopy(snapshot["tables"].get("appointment_history"))
        require(definition is not None, "missing_appointment_history")
        definition["columns"] = [value for value in definition["columns"] if value[0] != "details"]
        definition["constraints"] = [value for value in definition["constraints"]
                                     if value[0] != DETAILS_CONSTRAINT] or None
        definition["indexes"] = [value for value in definition["indexes"] if value[0] != HISTORY_INDEX] or None
        snapshot["tables"]["appointment_history"] = definition
    return snapshot


def verify_source(conn):
    require(revision(conn) == SOURCE and table_names(conn) == TABLES, "unexpected_source_schema")
    definition = catalog(conn).get("appointment_history")
    require(definition is not None and fingerprint(definition) == SOURCE_HISTORY_SHA256,
            "unexpected_source_history")
    previous.verify_target(conn)


def verify_target(conn):
    require(table_names(conn) == TABLES, "unexpected_target_tables")
    definition = catalog(conn).get("appointment_history")
    require(definition is not None and fingerprint(definition) == TARGET_HISTORY_SHA256,
            "unexpected_agenda_schema")
    previous.verify_target(conn)


def apply_exact_migration(engine, settings):
    module = reviewed_migration()
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as conn:
        with conn.begin():
            prepare(conn, settings)
            conn.exec_driver_sql("SELECT version_num FROM public.alembic_version FOR UPDATE")
            verify_source(conn)
            before = existing_catalog(conn)
            require(all(value["owner"] == "app_migrator" for value in before["tables"].values()),
                    "unexpected_source_owner")
            with Operations.context(MigrationContext.configure(conn)):
                module.upgrade()
            verify_target(conn)
            require(existing_catalog(conn, projected=True) == before, "existing_catalog_changed")
            result = conn.execute(text("UPDATE public.alembic_version SET version_num=:target WHERE version_num=:source"),
                                  {"target": TARGET, "source": SOURCE})
            require(result.rowcount == 1 and revision(conn) == TARGET, "revision_update_failed")
            require(existing_catalog(conn, projected=True) == before, "existing_catalog_changed")
    return {"ok": True, "status": "applied", "source": SOURCE, "revision": TARGET}


def execute(settings):
    settings.validate()
    reviewed_migration()
    engine = engine_for(settings.url)
    try:
        with engine.connect().execution_options(isolation_level="REPEATABLE READ") as conn:
            with conn.begin():
                conn.exec_driver_sql("SET TRANSACTION READ ONLY")
                prepare(conn, settings)
                if revision(conn) == TARGET:
                    verify_target(conn)
                    return {"ok": True, "status": "already_applied", "source": SOURCE, "revision": TARGET}
                verify_source(conn)
        return apply_exact_migration(engine, settings)
    finally:
        engine.dispose()


def main():
    try:
        flag = os.getenv("ORION_RUN_AGENDA_MIGRATION", "0")
        if flag == "0":
            print(json.dumps({"ok": True, "status": "disabled"}))
            return 0
        result = execute(Settings.from_environment())
    except Stop as error:
        code = str(error)
        print(json.dumps({"ok": False, "error": code if re.fullmatch(r"[a-z0-9_]{1,80}", code) else "operation_failed"}))
        return 1
    except Exception:
        print(json.dumps({"ok": False, "error": "operation_failed"}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
