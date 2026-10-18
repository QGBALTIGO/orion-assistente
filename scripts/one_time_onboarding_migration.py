"""Explicit 0019 -> reviewed qualification release, with encrypted backup/rehearsal.

Never called by ordinary API startup. Old data/ACL/RLS are compared before and
after the transaction. No seed, source restore, downgrade, role creation or retry.
"""
from __future__ import annotations
from copy import deepcopy
from dataclasses import dataclass, replace
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from scripts import one_time_business_migration as backup
from scripts import one_time_management_migration as proof
from scripts import one_time_agenda_migration as agenda

ROOT = backup.ROOT
SOURCE = "0019_appointment_lifecycle"
TARGET = "0023_automation_notice_guard"
SOURCE_TABLES = frozenset(agenda.TABLES)
NEW_TABLES = frozenset(("business_onboarding", "business_onboarding_operations",
    "business_onboarding_creations", "qr_channels", "automation_execution",
    "automation_contact_state", "automation_jobs"))
TARGET_TABLES = SOURCE_TABLES | NEW_TABLES
FUNCTIONS = frozenset(("onboarding_update_identity", "onboarding_activate_tenant",
    "qr_channel_transaction_guard", "automation_owner_active", "automation_notice_guard"))
# All files are sealed before engine, backup or migration I/O.
MIGRATIONS = (
    ("0020_business_onboarding", SOURCE, "c34f74c3bf1707ea0b957410d2b421e160b0ab9a9a1829859107f98d1f2bee81"),
    ("0021_qr_channels", "0020_business_onboarding", "918ad20e273b9ea3ee895f4e29fd810eef5fca4961a3800dbe45445617261d66"),
    ("0022_marketing_execution", "0021_qr_channels", "635d2b60356a307c2d6c7e1110acb270b56a3f433bdb1d9ad0d87a436136179f"),
    (TARGET, "0022_marketing_execution", "f4350b4e77acd6a5d643cf8313a6761e9d0e4933c16af441aa337aeea060b8d3"),
)
Stop = backup.Stop
require = backup.require

@dataclass(frozen=True, repr=False)
class Settings:
    url: URL
    bridge_url: str
    token: str
    run_id: str

    @classmethod
    def from_environment(cls):
        require(os.getenv("ORION_RUN_ONBOARDING_MIGRATION") == "1", "explicit_flag_required")
        require(os.getenv("ORION_MIGRATION_SWITCH_ROLE") == "1", "role_switch_required")
        try:
            url = make_url(os.environ["MIGRATION_DATABASE_URL"])
            run_id = os.environ["ORION_ONBOARDING_MIGRATION_RUN_ID"]
            require(str(UUID(run_id)) == run_id and UUID(run_id).version == 4, "canonical_run_id_required")
            bridge = os.getenv("QR_BRIDGE_URL", "").rstrip("/")
            parsed = urlsplit(bridge)
            require(parsed.scheme == "http" and re.fullmatch(r"[a-z0-9][a-z0-9-]*\.railway\.internal",
                parsed.hostname or "") and parsed.port == 3000 and not
                (parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment),
                "private_bridge_required")
        except Stop:
            raise
        except Exception:
            raise Stop("invalid_configuration") from None
        require(url.drivername in {"postgres", "postgresql", "postgresql+psycopg"}, "postgres_required")
        require(bool(url.host and url.database and url.username and url.password), "owner_url_required")
        require(url.host == os.getenv("ORION_EXPECTED_DATABASE_HOST") and
            url.database == os.getenv("ORION_EXPECTED_DATABASE_NAME"), "database_identity_required")
        require(set(url.query) <= {"sslmode", "sslrootcert"} and
            all(isinstance(value, str) for value in url.query.values()), "unsupported_database_option")
        token = os.getenv("QR_BRIDGE_TOKEN", "")
        require(len(token.encode()) >= 32, "backup_key_required")
        return cls(url.set(drivername="postgresql+psycopg"), bridge, token, run_id)

def reviewed_migrations():
    reviewed = []
    for revision, parent, digest in MIGRATIONS:
        path = ROOT / "alembic" / "versions" / (revision + ".py")
        try:
            body = path.read_bytes().replace(b"\r\n", b"\n")
        except OSError:
            raise Stop("reviewed_migration_missing") from None
        require(hashlib.sha256(body).hexdigest() == digest, "unreviewed_onboarding_migration")
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

def verify_legacy_programs(conn, *, target=False):
    value = proof.programs(conn)
    legacy = {"functions": [r for r in value["functions"] if not (target and r[1] in FUNCTIONS)],
              "triggers": value["triggers"]}
    require({(r[1], r[2]) for r in legacy["functions"]} == proof.FUNCTIONS and
        len(legacy["functions"]) == len(proof.FUNCTIONS), "legacy_functions_mismatch")
    require(all(r[0] == "public" and r[3] == "app_migrator" and
        r[4] == (r[1] == "resolve_published_site") and r[5] == ["search_path=pg_catalog, public"]
        for r in legacy["functions"]), "legacy_function_security_mismatch")
    require({(r[1], r[2]) for r in legacy["triggers"]} == proof.TRIGGERS and
        len(legacy["triggers"]) == len(proof.TRIGGERS) and
        all(r[0] == "public" and r[3] == "O" for r in legacy["triggers"]), "legacy_triggers_mismatch")
    return legacy

def source_only(conn):
    require(backup.revision(conn) == SOURCE and backup.table_names(conn) == SOURCE_TABLES,
            "unexpected_source_schema")
    agenda.verify_target(conn)
    verify_legacy_programs(conn)
    require(all(row[0] != "operational_opt_in" for row in conn.exec_driver_sql(
        "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name='contact_consents'")),
        "partial_onboarding_schema")

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
        ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind NOT IN ('r','i')""").scalar_one() == 0,
        "unreviewed_database_relations")
    if source: source_only(conn)
    conn.exec_driver_sql("SET LOCAL ROLE app_migrator")
    require(conn.exec_driver_sql("SELECT current_user").scalar_one() == "app_migrator", "role_switch_failed")
    conn.exec_driver_sql("RESET ROLE")
    return dict(identity)

def counts(conn, tables=SOURCE_TABLES):
    require(set(tables) <= TARGET_TABLES, "unapproved_table")
    return {name: conn.exec_driver_sql('SELECT count(*) FROM public."' + name + '"').scalar_one()
            for name in sorted(tables)}

def rows(conn, *, projected=False):
    result = {}
    for table in sorted(SOURCE_TABLES - {"alembic_version"}):
        expression = "to_jsonb(t)"
        if projected and table == "contact_consents": expression += " - 'operational_opt_in'"
        query = ('SELECT encode(sha256(convert_to((' + expression + ")::text,'UTF8')),'hex') "
                 'FROM public."' + table + '" t ORDER BY 1')
        digest = hashlib.sha256()
        with conn.execute(text(query).execution_options(stream_results=True)) as values:
            for value in values: digest.update(value[0].encode("ascii") + b"\n")
        result[table] = digest.hexdigest()
    return result

def canonical_catalog(catalog):
    """Only the two reviewed PostgreSQL 16 spellings of this legacy AND check.

    pg_dump text roundtrip may flatten associative AND. Never strip arbitrary
    parentheses or rewrite OR/NOT/operands, owners, RLS or any other constraint.
    """
    value = backup.canonical_catalog(catalog)
    nested = "CHECK ((((amount_cents >= 1) AND (amount_cents <= 1000000000)) AND ((currency)::text = 'BRL'::text)))"
    flat = "CHECK (((amount_cents >= 1) AND (amount_cents <= 1000000000) AND ((currency)::text = 'BRL'::text)))"
    for row in value:
        if row[0] == "financial_entries":
            row[4] = [flat if expression == nested else expression for expression in row[4] or []] or None
    return value


def old_catalog(conn, *, projected=False):
    catalog = canonical_catalog(backup.catalog_structure(conn, SOURCE_TABLES))
    if projected:
        consent = next(row for row in catalog if row[0] == "contact_consents")
        added = [col for col in consent[3] if col[0] == "operational_opt_in"]
        require(added == [["operational_opt_in", "boolean", True, "false"]], "consent_column_mismatch")
        consent[3] = [col for col in consent[3] if col[0] != "operational_opt_in"]
    return catalog

def snapshot(conn):
    return {"revision": backup.revision(conn), "counts": counts(conn), "rows": rows(conn),
        "catalog": old_catalog(conn), "security": proof.security_structure(conn),
        "programs": proof.programs(conn)}

def verify_new_schema(conn, *, pristine=False):
    require(backup.table_names(conn) == TARGET_TABLES, "target_table_mismatch")
    require(all(row[1:4] == [True, True, "app_migrator"] for row in
        [list(r) for r in conn.exec_driver_sql("""SELECT c.relname,c.relrowsecurity,c.relforcerowsecurity,
        pg_get_userbyid(c.relowner) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relname IN (
        'business_onboarding','business_onboarding_operations','business_onboarding_creations',
        'qr_channels','automation_execution','automation_contact_state','automation_jobs')""")]),
        "target_security_mismatch")
    functions = conn.exec_driver_sql("""SELECT p.proname,p.prosecdef,p.proconfig,
        pg_get_userbyid(p.proowner),
        EXISTS(SELECT 1 FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a WHERE a.grantee=0)
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.prosecdef AND p.proname <> 'resolve_published_site'""").all()
    require({r[0] for r in functions} == FUNCTIONS and len(functions) == len(FUNCTIONS) and
        all(r[1] and r[2] == ["search_path=pg_catalog, public"] and r[3] == "app_migrator" and not r[4]
            for r in functions), "target_function_security_mismatch")
    verify_legacy_programs(conn, target=True)
    old_catalog(conn, projected=True)
    if pristine:
        require(not any(counts(conn, NEW_TABLES).values()), "unexpected_seed_rows")
        require(not conn.exec_driver_sql("""SELECT EXISTS(SELECT 1 FROM public.contact_consents
            WHERE operational_opt_in IS DISTINCT FROM false)""").scalar_one(), "unexpected_consent_backfill")

def manifest(conn):
    return {"revision": backup.revision(conn), "catalog": canonical_catalog(
        backup.catalog_structure(conn, TARGET_TABLES)), "security": proof.without_database_identity(
        proof.security_structure(conn)), "programs": proof.programs(conn)}

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

def apply_exact_migrations(engine, settings, expected, rehearsed=None):
    modules = reviewed_migrations()
    require(rehearsed is not None or settings.url.database == backup.restore_name(settings.run_id),
        "rehearsal_proof_required")
    with engine.connect().execution_options(isolation_level="READ COMMITTED") as conn:
        with conn.begin():
            owner_preflight(conn, settings)
            require(conn.exec_driver_sql("SELECT pg_try_advisory_xact_lock(681692820123)").scalar_one(),
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
            require(proof.projected_security(proof.security_structure(conn), expected["security"]) ==
                expected["security"], "existing_security_changed")
            require(counts(conn) == expected["counts"] and rows(conn, projected=True) == expected["rows"],
                "existing_data_changed")
            require(verify_legacy_programs(conn, target=True) == expected["programs"], "existing_programs_changed")
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
                    found = owner_preflight(conn, temp)
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
                return apply_exact_migrations(restored, temp, actual)
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
        with tempfile.TemporaryDirectory(prefix="orion-onboarding-migration-") as directory:
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
        if os.getenv("ORION_RUN_ONBOARDING_MIGRATION", "0") == "0":
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
