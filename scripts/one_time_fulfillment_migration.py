"""Explicit 0011 -> 0012 operation, never invoked by application startup.

Uses the existing sealed owner configuration and private encrypted backup store.
The full 46-table snapshot must survive an actual PG16 restore before any source
DDL. No downgrade, credential changes, session termination or automatic retry.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import tempfile

import httpx
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from scripts import one_time_business_migration as backup

SOURCE = "0011_commercial_catalog"
TARGET = "0012_commercial_fulfillment"
TABLES = backup.LEGACY_TABLES | backup.NEW_TABLES
COLUMNS = frozenset(("fulfillment_mode", "pickup_appointment_id", "fulfillment_snapshot"))
CONSTRAINTS = frozenset(("fk_quote_pickup_appointment", "ck_quote_fulfillment"))
INDEX = "ix_quote_pickup_appointment"
# Line endings are normalized; every other byte of the reviewed DDL is fixed.
MIGRATION_SHA256 = "c9ebc9ae5fab269df10ec7eaab00afac0b3a19eca58a30b1939a3f258e2ad2b8"
require = backup.require
Stop = backup.Stop


def reviewed_migration():
    path = backup.ROOT / "alembic" / "versions" / (TARGET + ".py")
    require(hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest() == MIGRATION_SHA256,
            "unreviewed_fulfillment_migration")
    spec = importlib.util.spec_from_file_location("orion_reviewed_" + TARGET, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    require(module.revision == TARGET and module.down_revision == SOURCE and
            module.branch_labels is None and module.depends_on is None, "unreviewed_migration_chain")
    return module


def named_constraints(conn):
    return [list(row) for row in conn.execute(text("""
        SELECT c.relname,x.conname,x.contype,x.convalidated,x.condeferrable,x.condeferred,
               x.connoinherit,pg_get_constraintdef(x.oid)
        FROM pg_constraint x JOIN pg_class c ON c.oid=x.conrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' ORDER BY c.relname,x.conname
    """))]


def security_structure(conn, *, projected=False):
    """Preserve ownership, explicit ACLs, role attributes and memberships in place.

    Restore deliberately uses --no-owner/--no-acl in its isolated database; these
    source security properties are compared before and after the source DDL.
    """
    relations = [list(row) for row in conn.exec_driver_sql("""
        SELECT c.relname,c.relkind,pg_get_userbyid(c.relowner),c.relacl::text,c.reloptions,
          c.relrowsecurity,c.relforcerowsecurity
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' ORDER BY c.relname
    """) if not (projected and row[0] == INDEX)]
    columns = [list(row) for row in conn.exec_driver_sql("""
        SELECT c.relname,a.attname,a.attacl::text FROM pg_attribute a
        JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relkind='r' AND a.attnum>0 AND NOT a.attisdropped
        ORDER BY c.relname,a.attnum
    """) if not (projected and row[0] == "commercial_quotes" and row[1] in COLUMNS)]
    queries = {
        "schema": "SELECT nspname,pg_get_userbyid(nspowner),nspacl::text FROM pg_namespace WHERE nspname='public'",
        "database": "SELECT datname,pg_get_userbyid(datdba),datacl::text,datallowconn,datconnlimit FROM pg_database WHERE datname=current_database()",
        "roles": "SELECT rolname,rolsuper,rolinherit,rolcreaterole,rolcreatedb,rolcanlogin,rolreplication,rolbypassrls,rolconfig FROM pg_roles WHERE rolname IN ('app_runtime','app_support','app_migrator') ORDER BY rolname",
        "memberships": "SELECT pg_get_userbyid(roleid),pg_get_userbyid(member),pg_get_userbyid(grantor),admin_option,inherit_option,set_option FROM pg_auth_members ORDER BY roleid,member,grantor",
        "defaults": "SELECT pg_get_userbyid(defaclrole),defaclnamespace::regnamespace::text,defaclobjtype,defaclacl::text FROM pg_default_acl ORDER BY defaclrole,defaclnamespace,defaclobjtype",
    }
    return {"relations": relations, "columns": columns,
            **{key: [list(row) for row in conn.exec_driver_sql(sql)] for key, sql in queries.items()}}


def row_fingerprints(conn, *, projected=False):
    """Only SHA256 digests leave PostgreSQL; no row values enter logs or reports."""
    result = {}
    for table in sorted(TABLES):
        value = "to_jsonb(t)"
        if projected and table == "commercial_quotes":
            value += " - ARRAY['fulfillment_mode','pickup_appointment_id','fulfillment_snapshot']::text[]"
        sql = ('SELECT encode(sha256(convert_to((' + value + ")::text,'UTF8')),'hex') "
               'FROM public."' + table + '" t ORDER BY 1')
        digest = hashlib.sha256()
        # Stream hashes to bound client memory, even for a highly compressible dump.
        with conn.execute(text(sql).execution_options(stream_results=True)) as rows:
            for row in rows:
                digest.update(row[0].encode("ascii") + b"\n")
        result[table] = digest.hexdigest()
    return result


def source_only(conn):
    require(backup.revision(conn) == SOURCE and backup.table_names(conn) == TABLES,
            "source_changed_before_migration")
    quote = next(row for row in backup.catalog_structure(conn, TABLES) if row[0] == "commercial_quotes")
    require(not COLUMNS.intersection(column[0] for column in quote[3]), "partial_fulfillment_schema")
    require(not any(row[0] == "commercial_quotes" and row[1] in CONSTRAINTS for row in named_constraints(conn)),
            "partial_fulfillment_schema")
    require(not conn.execute(text("SELECT EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relname=:name)"),
                             {"name": INDEX}).scalar_one(), "partial_fulfillment_schema")


def projected_catalog(conn):
    """Validate the exact additions before excluding them from preservation checks.

    The CHECK expression is produced only by the byte-pinned reviewed migration;
    its validated/type/deferrability metadata and name must also match. Existing
    constraint definitions and names remain byte-exact and are never excluded.
    """
    rows = deepcopy(backup.catalog_structure(conn, TABLES))
    require({row[0] for row in rows} == TABLES, "target_table_mismatch")
    quote = next(row for row in rows if row[0] == "commercial_quotes")
    added = [column for column in quote[3] if column[0] in COLUMNS]
    require(added == [["fulfillment_mode", "character varying(16)", False, None],
                      ["pickup_appointment_id", "uuid", False, None],
                      ["fulfillment_snapshot", "jsonb", False, None]], "fulfillment_columns_mismatch")
    constraints = named_constraints(conn)
    additions = [row for row in constraints if row[0] == "commercial_quotes" and row[1] in CONSTRAINTS]
    require(len(additions) == 2, "fulfillment_constraints_mismatch")
    for row in additions:
        expected_kind = "f" if row[1] == "fk_quote_pickup_appointment" else "c"
        # PG16 addFkConstraint marks a top-level FK on an ordinary table
        # connoinherit=true. The independently inheritable CHECK remains false.
        require(row[2:7] == [expected_kind, True, False, False, expected_kind == "f"],
                "fulfillment_constraints_mismatch")
        if row[1] == "fk_quote_pickup_appointment":
            require(row[7] == "FOREIGN KEY (tenant_id, pickup_appointment_id) REFERENCES appointments(tenant_id, id) ON DELETE RESTRICT",
                    "fulfillment_fk_mismatch")
        require(quote[4].count(row[7]) == 1, "fulfillment_constraint_identity_mismatch")
        quote[4].remove(row[7])
    expected_index = ("CREATE INDEX ix_quote_pickup_appointment ON public.commercial_quotes USING btree "
                      "(tenant_id, pickup_appointment_id) WHERE (pickup_appointment_id IS NOT NULL)")
    require(quote[5].count(expected_index) == 1, "fulfillment_index_mismatch")
    quote[5].remove(expected_index)
    quote[3] = [column for column in quote[3] if column[0] not in COLUMNS]
    index = conn.execute(text("""SELECT i.indisvalid,i.indisready,pg_get_userbyid(c.relowner),c.relacl,c.reloptions
        FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relname=:name"""), {"name": INDEX}).one()
    require(list(index) == [True, True, "app_migrator", None, None], "fulfillment_index_security_mismatch")
    require(conn.exec_driver_sql("""SELECT count(*) FROM pg_attribute WHERE attrelid='public.commercial_quotes'::regclass
        AND attname IN ('fulfillment_mode','pickup_appointment_id','fulfillment_snapshot') AND attacl IS NOT NULL""").scalar_one() == 0,
        "fulfillment_column_grants_changed")
    require(conn.exec_driver_sql("""SELECT count(*) FROM public.commercial_quotes WHERE fulfillment_mode IS NOT NULL
        OR pickup_appointment_id IS NOT NULL OR fulfillment_snapshot IS NOT NULL""").scalar_one() == 0, "historical_fulfillment_changed")
    return backup.canonical_catalog(rows), [row for row in constraints if not
        (row[0] == "commercial_quotes" and row[1] in CONSTRAINTS)]


def apply_exact_migration(engine, settings, *, expected_catalog, expected_security):
    migration = reviewed_migration()
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as conn:
        with conn.begin():
            backup.set_timeouts(conn)
            require(conn.exec_driver_sql("SELECT pg_try_advisory_xact_lock(681692820104)").scalar_one(), "migration_already_running")
            conn.exec_driver_sql("SELECT version_num FROM public.alembic_version FOR UPDATE")
            backup.owner_preflight(conn, settings, source=SOURCE, tables=TABLES)
            source_only(conn)
            require(backup.catalog_fingerprint(conn, TABLES) == expected_catalog and
                    security_structure(conn) == expected_security, "source_changed_after_backup")
            before_counts = backup.counts(conn, TABLES)
            before_rows = row_fingerprints(conn)
            before_catalog = backup.canonical_catalog(backup.catalog_structure(conn, TABLES))
            before_constraints = named_constraints(conn)
            conn.exec_driver_sql("SET LOCAL ROLE app_migrator")
            require(conn.exec_driver_sql("SELECT current_user").scalar_one() == "app_migrator", "role_switch_failed")
            with Operations.context(MigrationContext.configure(conn)):
                migration.upgrade()
            conn.exec_driver_sql("RESET ROLE")
            projected, constraints = projected_catalog(conn)
            require(projected == before_catalog and constraints == before_constraints,
                    "existing_catalog_changed")
            require(security_structure(conn, projected=True) == expected_security, "existing_security_changed")
            require(backup.counts(conn, TABLES) == before_counts and row_fingerprints(conn, projected=True) == before_rows,
                    "existing_data_changed")
            conn.exec_driver_sql("SET LOCAL ROLE app_migrator")
            result = conn.execute(text("UPDATE public.alembic_version SET version_num=:target WHERE version_num=:source"),
                                  {"target": TARGET, "source": SOURCE})
            require(result.rowcount == 1 and backup.revision(conn) == TARGET, "revision_update_failed")
            conn.exec_driver_sql("RESET ROLE")
            return before_counts


def execute(settings):
    reviewed_migration()  # Refuse an unreviewed file before backup I/O.
    engine = backup.engine_for(settings.url)
    try:
        with engine.begin() as conn:
            identity = backup.owner_preflight(conn, settings, source=SOURCE, tables=TABLES)
            source_only(conn)
            security = security_structure(conn)
        require(backup.client_major("pg_dump") == backup.client_major("pg_restore") == identity["major"] == 16,
                "postgres_major_mismatch")
        with tempfile.TemporaryDirectory(prefix="orion-fulfillment-migration-") as directory:
            os.chmod(directory, 0o700)
            require(shutil.disk_usage(directory).free > backup.MAX_BYTES * 3 + 16 * 1024 * 1024, "temporary_disk_space_low")
            original = Path(directory) / "snapshot.dump"
            backup_counts, fingerprint = backup.capture_dump(engine, settings, original, source=SOURCE, tables=TABLES)
            envelope = backup.encrypt_dump(original.read_bytes(), settings.token, settings.run_id)
            with httpx.Client(timeout=httpx.Timeout(60, connect=10), follow_redirects=False, trust_env=False) as client:
                restored_bytes, digest = backup.durable_backup(settings, envelope, client)
            readback = Path(directory) / "verified.dump"
            backup.private_write(readback, restored_bytes)
            backup.verify_restore(engine, settings, identity, readback, backup_counts, fingerprint, source=SOURCE, tables=TABLES)
            preserved = apply_exact_migration(engine, settings, expected_catalog=fingerprint, expected_security=security)
        return {"status": "complete", "backup_id": settings.run_id, "backup_sha256": digest,
                "backup_bytes": len(envelope), "backup_counts": backup_counts, "preserved_counts": preserved,
                "revision": TARGET, "restore_verified": True}
    finally:
        engine.dispose()


def main():
    try:
        result = execute(backup.Settings.from_environment())
    except Stop as error:
        print(json.dumps({"status": "stopped", "code": str(error)}))
        return 1
    except Exception:
        print(json.dumps({"status": "stopped", "code": "operation_failed_no_details"}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
