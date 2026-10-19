"""Sealed, additive 0019 -> 0020 transactional WhatsApp notice migration.

Runs only as app_migrator with an explicitly verified database identity. The
reviewed operation creates only the scoped notice outbox. It does not change
existing appointments, fabricate notifications, or send any messages. Replays only
validate the target in a read-only transaction. There is no repair or downgrade.
"""
from __future__ import annotations

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
from scripts import one_time_agenda_migration as agenda

ROOT = Path(__file__).resolve().parents[1]
SOURCE = "0019_appointment_lifecycle"
TARGET = "0020_appointment_notices"
MIGRATION_SHA256 = "4aaa4eae11b62052ab5731978fb89372378e0d65c03bda012ecd656f3db43c90"
# Filled only from the isolated PostgreSQL 16 rehearsal, before release.
TARGET_SCHEMA_SHA256 = "871ec60ef0ed7b2bd60d15f153fc4f00609796737c81761f16844b75c55e2629"
TABLES = agenda.TABLES
TARGET_TABLES = TABLES | {"appointment_notices"}
ADVISORY_LOCK = 681692820120

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
        require(os.getenv("ORION_RUN_NOTICE_MIGRATION") == "1", "explicit_flag_required")
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
    require(hashlib.sha256(body).hexdigest() == MIGRATION_SHA256, "unreviewed_notice_migration")
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
    snapshot["tables"] = {key: value for key, value in catalog(conn).items()
                          if key != "appointment_notices"}
    return snapshot


def verify_source(conn):
    require(revision(conn) == SOURCE and table_names(conn) == TABLES, "unexpected_source_schema")
    agenda.verify_target(conn)


def verify_target(conn):
    require(table_names(conn) == TARGET_TABLES, "unexpected_target_tables")
    definition = catalog(conn).get("appointment_notices")
    require(definition is not None and fingerprint(definition) == TARGET_SCHEMA_SHA256,
            "unexpected_notice_schema")
    history = catalog(conn).get("appointment_history")
    require(history is not None and fingerprint(history) == agenda.TARGET_HISTORY_SHA256,
            "unexpected_agenda_schema")


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
        flag = os.getenv("ORION_RUN_NOTICE_MIGRATION", "0")
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
