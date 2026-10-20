"""Explicit 0028 -> 0029 communication release with encrypted backup and isolated rehearsal.

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
from scripts import one_time_inbox_migration as previous
from scripts.one_time_onboarding_migration import Settings as BaseSettings

backup, proof = previous.backup, previous.proof
ROOT, Stop, require = previous.ROOT, previous.Stop, previous.require
SOURCE, TARGET = previous.TARGET, "0029_communication_contract"
SOURCE_TABLES = previous.TARGET_TABLES
NEW_TABLES = frozenset(("communication_pending",))
TARGET_TABLES = SOURCE_TABLES | NEW_TABLES
FUNCTIONS = previous.FUNCTIONS
MIGRATIONS = ((TARGET,SOURCE,"13164b0677526c7b439d299347d542fe6576303804e85efe699f2de5ad83662c"),)
ADDED = {
    'inbound_events':('envelope','ingress_order'),
    'inbox_messages':('envelope',),
    'customers':('birth_month','birth_day'),
    'contact_consents':('birthday_opt_in','birthday_declined_at','birthday_purpose_version','operational_updated_at','marketing_updated_at','birthday_updated_at'),
    'automation_jobs':('presentation',),
}

class Settings(BaseSettings):
    @classmethod
    def from_environment(cls):
        return super().from_environment(flag="ORION_RUN_COMMUNICATION_MIGRATION",
            run_id_name="ORION_COMMUNICATION_MIGRATION_RUN_ID")

def reviewed_migrations():
    reviewed = []
    for revision, parent, digest in MIGRATIONS:
        path = ROOT / "alembic" / "versions" / (revision + ".py")
        try:
            body = path.read_bytes().replace(b"\r\n", b"\n")
        except OSError:
            raise Stop("reviewed_migration_missing") from None
        require(hashlib.sha256(body).hexdigest() == digest, "unreviewed_communication_migration")
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
    proof.set_timeouts(conn)
    identity = conn.execute(text("""SELECT current_database() AS name,d.oid,d.datdba,
        r.oid AS role_oid,r.rolsuper,r.rolbypassrls,r.rolcreatedb,
        current_setting('server_version_num')::int/10000 AS major
        FROM pg_database d JOIN pg_roles r ON r.rolname=current_user
        WHERE d.datname=current_database()""")).mappings().one()
    require(identity["name"] == settings.url.database and identity["datdba"] == identity["role_oid"],
            "database_owner_required")
    require((identity["rolsuper"] or identity["rolbypassrls"]) and
        (identity["rolsuper"] or identity["rolcreatedb"]), "owner_backup_privileges_required")
    require(identity["major"] == 16, "postgres_major_mismatch")
    roles = conn.exec_driver_sql("""SELECT rolname,rolsuper,rolbypassrls FROM pg_roles
        WHERE rolname IN ('app_runtime','app_support','app_migrator')""").all()
    require(len(roles) == 3 and all(not r.rolsuper and not r.rolbypassrls for r in roles),
            "unsafe_application_roles")
    require(set(conn.exec_driver_sql("SELECT extname FROM pg_extension").scalars()) ==
        {"plpgsql", "btree_gist"}, "unexpected_extensions")
    require(conn.exec_driver_sql("SELECT count(*) FROM pg_largeobject_metadata").scalar_one() == 0,
        "unreviewed_large_objects")
    require(conn.exec_driver_sql("SELECT count(*) FROM pg_event_trigger").scalar_one() == 0,
        "unreviewed_event_triggers")
    require(conn.exec_driver_sql("""SELECT count(*) FROM pg_class c JOIN pg_namespace n
        ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind NOT IN ('r','i')
          AND NOT(c.relkind='S' AND c.relname='inbound_events_ingress_order_seq'
            AND (SELECT version_num FROM public.alembic_version)='0029_communication_contract')""").scalar_one() == 0,
        "unreviewed_database_relations")
    if source: source_only(conn)
    conn.exec_driver_sql("SET LOCAL ROLE app_migrator")
    require(conn.exec_driver_sql("SELECT current_user").scalar_one() == "app_migrator", "role_switch_failed")
    conn.exec_driver_sql("RESET ROLE")
    return dict(identity)


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
        if projected and table in ADDED: expression += ''.join(" - '"+name+"'" for name in ADDED[table])
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
    catalog=canonical_catalog(backup.catalog_structure(conn,SOURCE_TABLES))
    if projected:
        for row in catalog:
            if row[0] in ADDED:
                require({c[0] for c in row[3]} >= set(ADDED[row[0]]),'added_columns_missing')
                row[3]=[c for c in row[3] if c[0] not in ADDED[row[0]]]
            if row[0]=='inbound_events':
                index='CREATE INDEX ix_inbound_conversation_order ON public.inbound_events USING btree (tenant_id, channel_id, sender_e164, ingress_order DESC)'
                require(row[5].count(index)==1,'ingress_index_mismatch')
                row[5].remove(index)
            if row[0]=='customers':
                added=next(r for r in named_constraints(conn) if r[:2]==['customers','ck_customer_birthday_partial'])
                require(row[4].count(added[7])==1,'birthday_check_missing')
                row[4].remove(added[7])
    return catalog


def preserved_constraints(conn, *, projected=False):
    return [r for r in named_constraints(conn) if not (projected and r[:2]==['customers','ck_customer_birthday_partial'])]


def snapshot(conn):
    return {'revision':backup.revision(conn),'counts':counts(conn),'rows':rows(conn),
        'catalog':old_catalog(conn),'security':proof.security_structure(conn),
        'constraints':preserved_constraints(conn),'programs':proof.programs(conn)}


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
    require(conn.exec_driver_sql("SELECT pg_get_serial_sequence('public.inbound_events','ingress_order')").scalar_one()=='public.inbound_events_ingress_order_seq','ingress_sequence_mismatch')
    trigger=conn.exec_driver_sql("SELECT count(*) FROM pg_trigger WHERE tgname='communication_consent_versions' AND NOT tgisinternal").scalar_one()
    require(trigger==1,'consent_trigger_missing')
    require(not conn.exec_driver_sql("SELECT has_function_privilege('app_support','public.communication_consent_versions()','EXECUTE')").scalar_one(),'consent_function_grant_mismatch')
    if pristine:
        require(not any(counts(conn,NEW_TABLES).values()),'unexpected_seed_rows')
        require(not conn.exec_driver_sql("SELECT EXISTS(SELECT 1 FROM customers WHERE birth_day IS NOT NULL OR birth_month IS NOT NULL)").scalar_one(),'birthday_backfill_forbidden')
        require(not conn.exec_driver_sql("SELECT EXISTS(SELECT 1 FROM contact_consents WHERE birthday_opt_in OR birthday_declined_at IS NOT NULL OR birthday_purpose_version<>0 OR operational_updated_at<>updated_at OR marketing_updated_at<>updated_at OR birthday_updated_at<>updated_at)").scalar_one(),'consent_backfill_mismatch')
        for table,column in [('inbound_events','envelope'),('inbox_messages','envelope'),('automation_jobs','presentation')]:
            require(not conn.exec_driver_sql('SELECT EXISTS(SELECT 1 FROM '+table+' WHERE '+column+"<>'{}'::jsonb)").scalar_one(),'metadata_backfill_forbidden')


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
            require(conn.exec_driver_sql("SELECT pg_try_advisory_xact_lock(681692820129)").scalar_one(),
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
            programs["functions"] = [row for row in programs["functions"] if row[1]!="communication_consent_versions"]
            programs["triggers"] = [row for row in programs["triggers"] if row[2]!="communication_consent_versions"]
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
        with tempfile.TemporaryDirectory(prefix="orion-communication-migration-") as directory:
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
        if os.getenv("ORION_RUN_COMMUNICATION_MIGRATION", "0") == "0":
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
