"""Explicit 0023 -> 0028 inbox release with encrypted backup and isolated rehearsal.

No messages, channel changes, seeds, source restore or destructive downgrade.
Run only as a reviewed pre-deploy operation with the existing owner credential.
"""
from __future__ import annotations
from dataclasses import dataclass, replace
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

import httpx
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from scripts import one_time_onboarding_migration as previous

backup, proof = previous.backup, previous.proof
ROOT, Stop, require = previous.ROOT, previous.Stop, previous.require
SOURCE, TARGET = previous.TARGET, "0028_workflow_drafts"
SOURCE_TABLES = previous.TARGET_TABLES
NEW_TABLES = frozenset(("inbox_labels", "inbox_customer_labels", "inbox_quick_replies",
    "inbox_controls", "inbox_control_events", "inbox_channel_links", "inbox_messages",
    "inbox_provider_receipts", "inbox_reads"))
TARGET_TABLES = SOURCE_TABLES | NEW_TABLES
FUNCTIONS = previous.FUNCTIONS | {"inbox_channel_contact_allowed", "inbox_operator_version", "inbox_meta_guard"}
MIGRATIONS = (
    ("0024_inbox_tools", SOURCE, "ad0b9d142a29df39003a397b2d51c80a52caa74f16adae7cec3704d0e9f8baf1"),
    ("0025_inbox_control", "0024_inbox_tools", "e998a1fe05277cb703bcc7fd9ff0c54ea5661954ad4f03522e41b0e8333b47a3"),
    ("0026_cancelled_notification", "0025_inbox_control", "e262ce177105854705acad5a02639c53952248af26dec88fbcb9f20564638511"),
    ("0027_unified_inbox", "0026_cancelled_notification", "763ad3666f0d0f30f67d52d44fadadb85c6e45b6ada78428239ce8c89b205999"),
    (TARGET, "0027_unified_inbox", "85cf43050a9b4a4209e8bb0a390adaf1b58300c67c061eff5e21ca2b0b2e249c"),
)

class Settings(previous.Settings):
    @classmethod
    def from_environment(cls):
        return super().from_environment(flag="ORION_RUN_INBOX_MIGRATION",
            run_id_name="ORION_INBOX_MIGRATION_RUN_ID")

def reviewed_migrations():
    reviewed = []
    for revision, parent, digest in MIGRATIONS:
        path = ROOT / "alembic" / "versions" / (revision + ".py")
        try:
            body = path.read_bytes().replace(b"\r\n", b"\n")
        except OSError:
            raise Stop("reviewed_migration_missing") from None
        require(hashlib.sha256(body).hexdigest() == digest, "unreviewed_inbox_migration")
        reviewed.append((revision, parent, path, body))
    modules = []
    for revision, parent, path, body in reviewed:
        spec = importlib.util.spec_from_file_location("orion_reviewed_" + revision, path)
        require(spec is not None, "reviewed_migration_unloadable")
        module = importlib.util.module_from_spec(spec)
        exec(compile(body, str(path), "exec"), module.__dict__)
        require(module.revision == revision and module.down_revision == parent and
            module.branch_labels is None and module.depends_on is None, "unreviewed_migration_chain")
        modules.append(module)
    return modules

def owner_preflight(conn, settings, *, source=True):
    identity = previous.owner_preflight(conn, settings, source=False)
    if source: source_only(conn)
    return identity


def source_only(conn, *, restored=False):
    require(backup.revision(conn) == SOURCE and backup.table_names(conn) == SOURCE_TABLES,
        "unexpected_source_schema")
    previous.verify_new_schema(conn)


def counts(conn, tables=SOURCE_TABLES):
    require(set(tables) <= TARGET_TABLES, "unapproved_table")
    return {name: conn.exec_driver_sql('SELECT count(*) FROM public."' + name + '"').scalar_one()
        for name in sorted(tables)}


def rows(conn, *, projected=False):
    result = {}
    for table in sorted(SOURCE_TABLES - {"alembic_version"}):
        expression = "to_jsonb(t)"
        if projected and table == "workflow_definitions": expression += " - 'version' - 'updated_at'"
        query = ('SELECT encode(sha256(convert_to((' + expression + ")::text,'UTF8')),'hex') "
            'FROM public."' + table + '" t ORDER BY 1')
        digest = hashlib.sha256()
        with conn.execute(text(query).execution_options(stream_results=True)) as values:
            for value in values: digest.update(value[0].encode("ascii") + b"\n")
        result[table] = digest.hexdigest()
    return result


canonical_catalog = previous.canonical_catalog


def named_constraints(conn, tables=SOURCE_TABLES):
    return previous.named_constraints(conn, tables)


def old_catalog(conn, *, projected=False):
    catalog = canonical_catalog(backup.catalog_structure(conn, SOURCE_TABLES))
    if projected:
        workflow = next(row for row in catalog if row[0] == "workflow_definitions")
        added = [col for col in workflow[3] if col[0] in ("version", "updated_at")]
        require(added == [["version", "integer", True, "1"],
            ["updated_at", "timestamp with time zone", True, "now()"]], "workflow_columns_mismatch")
        workflow[3] = [col for col in workflow[3] if col[0] not in ("version", "updated_at")]
        require(workflow[4].count("CHECK ((version > 0))") == 1, "workflow_check_mismatch")
        workflow[4].remove("CHECK ((version > 0))")
    # Only the named, byte-reviewed 0026 event constraint is allowed to change.
    constraint = next(row for row in named_constraints(conn) if row[:2] ==
        ["automation_jobs", "automation_jobs_event_check"])
    events = next(row for row in catalog if row[0] == "automation_jobs")[4]
    require(events.count(constraint[7]) == 1, "event_constraint_identity_mismatch")
    events.remove(constraint[7])
    return catalog


def preserved_constraints(conn, *, projected=False):
    return [row for row in named_constraints(conn) if row[:2] !=
        ["automation_jobs", "automation_jobs_event_check"] and not
        (projected and row[:2] == ["workflow_definitions", "workflow_definitions_version_check"])]


def snapshot(conn):
    return {"revision": backup.revision(conn), "counts": counts(conn), "rows": rows(conn),
        "catalog": old_catalog(conn), "security": proof.security_structure(conn),
        "constraints": preserved_constraints(conn), "programs": proof.programs(conn),
        "event_constraint": next(row for row in named_constraints(conn) if row[:2] ==
            ["automation_jobs", "automation_jobs_event_check"])}


@dataclass(frozen=True, repr=False)
class _RestoredSourceProof:
    run_id: str
    database: str
    snapshot: dict


def verify_new_schema(conn, *, pristine=False):
    require(backup.table_names(conn) == TARGET_TABLES, "target_table_mismatch")
    secured = conn.execute(text("""SELECT c.relname,c.relrowsecurity,c.relforcerowsecurity,
        pg_get_userbyid(c.relowner) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relname=ANY(:names)"""), {"names": sorted(NEW_TABLES)}).all()
    require({r[0] for r in secured} == NEW_TABLES and
        all(list(r[1:]) == [True, True, "app_migrator"] for r in secured), "target_security_mismatch")
    functions = conn.exec_driver_sql("""SELECT p.proname,p.prosecdef,p.proconfig,pg_get_userbyid(p.proowner),
        EXISTS(SELECT 1 FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a WHERE a.grantee=0)
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.prosecdef AND p.proname <> 'resolve_published_site'""").all()
    require({r[0] for r in functions} == FUNCTIONS and len(functions) == len(FUNCTIONS) and
        all(r[1] and r[2] == ["search_path=pg_catalog, public"] and r[3] == "app_migrator" and not r[4]
            for r in functions), "target_function_security_mismatch")
    old_catalog(conn, projected=True)
    if pristine:
        # Only existing active Meta handoffs are imported. Never infer a resume.
        require(not conn.exec_driver_sql("""SELECT EXISTS(
            (SELECT tenant_id,channel_id,contact_e164,'human' FROM public.human_handoffs WHERE status='active'
             EXCEPT SELECT tenant_id,channel_id,contact_e164,mode FROM public.inbox_controls)
            UNION ALL
            (SELECT tenant_id,channel_id,contact_e164,mode FROM public.inbox_controls
             EXCEPT SELECT tenant_id,channel_id,contact_e164,'human' FROM public.human_handoffs WHERE status='active'))""").scalar_one(),
            "handoff_import_mismatch")
        require(not any(counts(conn, NEW_TABLES - {"inbox_controls", "inbox_channel_links"}).values()),
            "unexpected_seed_rows")
        require(not conn.exec_driver_sql("SELECT EXISTS(SELECT 1 FROM public.workflow_definitions WHERE version<>1)").scalar_one(),
            "unexpected_workflow_backfill")


def manifest(conn):
    return {"revision": backup.revision(conn), "catalog": canonical_catalog(
        backup.catalog_structure(conn, TARGET_TABLES)), "security": proof.without_database_identity(
        proof.security_structure(conn)), "constraints": named_constraints(conn, TARGET_TABLES),
        "programs": proof.programs(conn)}


def capture_dump(engine, settings, path):
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as conn:
        with conn.begin():
            conn.exec_driver_sql("SET TRANSACTION READ ONLY")
            owner_preflight(conn, settings)
            conn.exec_driver_sql("LOCK TABLE " + ",".join('public."' + n + '"' for n in sorted(SOURCE_TABLES)) +
                                " IN ACCESS SHARE MODE")
            snapshot_id = conn.exec_driver_sql("SELECT pg_export_snapshot()").scalar_one()
            before = snapshot(conn)
            backup.private_write(path, b"")
            backup.run_client("pg_dump", ["--format=custom", "--no-password", "--snapshot=" + snapshot_id,
                "--lock-wait-timeout=10000", "--file=" + str(path)], settings.url, max_file=path)
            require(5 <= path.stat().st_size <= backup.MAX_BYTES - 36, "dump_size_invalid")
            return before

def apply_exact_migrations(engine, settings, expected, rehearsed=None, *, restored_proof=None):
    modules = reviewed_migrations()
    restored = isinstance(restored_proof, _RestoredSourceProof)
    require(rehearsed is not None or restored,
        "rehearsal_proof_required")
    if restored:
        require(settings.url.database == backup.restore_name(settings.run_id) == restored_proof.database
            and settings.run_id == restored_proof.run_id and expected == restored_proof.snapshot,
            "restored_source_proof_mismatch")
    with engine.connect().execution_options(isolation_level="READ COMMITTED") as conn:
        with conn.begin():
            owner_preflight(conn, settings, source=not restored)
            if restored: source_only(conn, restored=True)
            require(conn.exec_driver_sql("SELECT pg_try_advisory_xact_lock(681692820128)").scalar_one(),
                "migration_already_running")
            conn.exec_driver_sql("LOCK TABLE " + ",".join('public."' + n + '"' for n in sorted(SOURCE_TABLES)) +
                                " IN SHARE MODE")
            conn.exec_driver_sql("SELECT version_num FROM public.alembic_version FOR UPDATE")
            require(snapshot(conn) == expected, "source_changed_after_backup")
            conn.exec_driver_sql("SET LOCAL ROLE app_migrator")
            with Operations.context(MigrationContext.configure(conn)):
                for module in modules: module.upgrade()
            conn.exec_driver_sql("RESET ROLE")
            require(old_catalog(conn, projected=True) == expected["catalog"], "existing_catalog_changed")
            require(preserved_constraints(conn, projected=True) == expected["constraints"], "existing_constraints_changed")
            require(proof.projected_security(proof.security_structure(conn), expected["security"]) ==
                expected["security"], "existing_security_changed")
            require(counts(conn) == expected["counts"] and rows(conn, projected=True) == expected["rows"],
                "existing_data_changed")
            programs = proof.programs(conn)
            programs["functions"] = [row for row in programs["functions"] if not row[1].startswith("inbox_")]
            require(programs == expected["programs"], "existing_programs_changed")
            verify_new_schema(conn, pristine=True)
            conn.exec_driver_sql("SET LOCAL ROLE app_migrator")
            result = conn.execute(text("UPDATE public.alembic_version SET version_num=:target WHERE version_num=:source"),
                {"target": TARGET, "source": SOURCE})
            require(result.rowcount == 1 and backup.revision(conn) == TARGET, "revision_update_failed")
            conn.exec_driver_sql("RESET ROLE")
            actual = manifest(conn)
            if rehearsed is not None: require(actual == rehearsed, "rehearsed_target_mismatch")
            return actual

def verify_restore(engine, settings, identity, path, expected):
    name = backup.restore_name(settings.run_id)
    require(name != settings.url.database, "restore_source_collision")
    oid = None
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as admin:
        try:
            require(not admin.execute(text("SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname=:n)"),
                {"n": name}).scalar_one(), "restore_database_already_exists")
            admin.exec_driver_sql('CREATE DATABASE "' + name + '" TEMPLATE template0 ALLOW_CONNECTIONS false')
            oid = admin.execute(text("SELECT oid FROM pg_database WHERE datname=:n"), {"n": name}).scalar_one()
            admin.exec_driver_sql('REVOKE CONNECT ON DATABASE "' + name + '" FROM PUBLIC,app_runtime,app_support,app_migrator')
            admin.exec_driver_sql('ALTER DATABASE "' + name + '" ALLOW_CONNECTIONS true')
            temp = replace(settings, url=settings.url.set(database=name))
            backup.run_client("pg_restore", ["--no-password", "--exit-on-error", "--single-transaction",
                "--dbname=" + name, str(path)], temp.url)
            restored = backup.engine_for(temp.url)
            try:
                with restored.begin() as conn:
                    found = owner_preflight(conn, temp, source=False)
                    source_only(conn, restored=True)
                    require(found["oid"] == oid and found["role_oid"] == identity["role_oid"],
                        "restore_database_identity_changed")
                    require(not conn.execute(text("""SELECT EXISTS(SELECT 1 FROM pg_roles WHERE
                        rolname IN ('app_runtime','app_support','app_migrator') AND
                        has_database_privilege(rolname,:n,'CONNECT'))"""), {"n": name}).scalar_one(),
                        "restore_connect_gate_changed")
                    actual = snapshot(conn)
                    require({k: v for k, v in actual.items() if k != "security"} ==
                        {k: v for k, v in expected.items() if k != "security"}, "restored_snapshot_mismatch")
                    require(proof.without_database_identity(actual["security"]) ==
                        proof.without_database_identity(expected["security"]), "restored_security_mismatch")
                receipt = _RestoredSourceProof(settings.run_id, name, actual)
                return apply_exact_migrations(restored, temp, actual, restored_proof=receipt)
            finally: restored.dispose()
        finally:
            if oid is not None: backup.safe_drop_created(admin, name, oid, identity["role_oid"], identity["oid"])

def execute(settings):
    reviewed_migrations()
    engine = backup.engine_for(settings.url)
    try:
        with engine.begin() as conn:
            if backup.revision(conn) == TARGET:
                conn.exec_driver_sql("SET TRANSACTION READ ONLY")
                owner_preflight(conn, settings, source=False)
                verify_new_schema(conn)
                return {"status": "already_applied", "revision": TARGET}
            identity = owner_preflight(conn, settings)
        require(backup.client_major("pg_dump") == backup.client_major("pg_restore") == 16,
            "postgres_major_mismatch")
        with tempfile.TemporaryDirectory(prefix="orion-inbox-migration-") as directory:
            os.chmod(directory, 0o700)
            require(shutil.disk_usage(directory).free > backup.MAX_BYTES * 3 + 16 * 1024 * 1024,
                "temporary_disk_space_low")
            original = Path(directory) / "snapshot.dump"
            expected = capture_dump(engine, settings, original)
            envelope = backup.encrypt_dump(original.read_bytes(), settings.token, settings.run_id)
            with httpx.Client(timeout=httpx.Timeout(60, connect=10), follow_redirects=False, trust_env=False) as client:
                readback, digest = backup.durable_backup(settings, envelope, client)
            verified = Path(directory) / "verified.dump"
            backup.private_write(verified, readback)
            target = verify_restore(engine, settings, identity, verified, expected)
            apply_exact_migrations(engine, settings, expected, target)
        return {"status": "complete", "revision": TARGET, "backup_id": settings.run_id,
            "backup_sha256": digest, "backup_bytes": len(envelope),
            "preserved_counts": expected["counts"], "restore_verified": True, "rehearsal_verified": True}
    finally: engine.dispose()

def main():
    try:
        if os.getenv("ORION_RUN_INBOX_MIGRATION", "0") == "0":
            print(json.dumps({"status": "disabled"})); return 0
        result = execute(Settings.from_environment())
    except Stop as error:
        code = str(error)
        print(json.dumps({"status": "stopped", "code": code if re.fullmatch(r"[a-z][a-z0-9_]{0,79}", code)
            else "operation_failed_no_details"})); return 1
    except Exception:
        print(json.dumps({"status": "stopped", "code": "operation_failed_no_details"})); return 1
    print(json.dumps(result, sort_keys=True)); return 0

if __name__ == "__main__":
    raise SystemExit(main())
