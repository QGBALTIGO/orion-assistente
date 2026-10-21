"""Explicit 0029 -> 0030 legacy administrator schema release with encrypted backup and isolated rehearsal.

No messages, channel changes, seeds, source restore or destructive downgrade.
Run only as a reviewed pre-deploy operation with the existing owner credential.
"""
from __future__ import annotations
from dataclasses import dataclass, replace
import ast
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
from scripts import one_time_communication_migration as previous
from scripts.one_time_onboarding_migration import Settings as BaseSettings

backup, proof = previous.backup, previous.proof
ROOT, Stop, require = previous.ROOT, previous.Stop, previous.require
SOURCE, TARGET = previous.TARGET, "0030_legacy_admin_identity"
SOURCE_TABLES = previous.TARGET_TABLES
NEW_TABLES = frozenset()
TARGET_TABLES = SOURCE_TABLES | NEW_TABLES
FUNCTIONS = previous.FUNCTIONS
MIGRATIONS = ((TARGET,SOURCE,"09c070122ef84c0af272de1b9ec853585ba209d6f7ebaff782fe770c8eaa1a96"),)
ADDED = {'panel_accounts':('auth_provider','legacy_username','legacy_credential_digest')}
ADMIN_CHECK = 'ck_panel_account_auth_identity'
ADMIN_INDEX = 'uq_panel_account_legacy_admin_identity'
# Exact pg_get_constraintdef observed on isolated PG16 after sealed migration
# 09c070... (admin-identity-pg16-seal.json); never learned from a deployment.
ADMIN_CHECK_SHA256 = 'ed5f016a031d4b744ea124c3eb468df9ce8281669a45c45f9e5f487bc64e60ea'

class Settings(BaseSettings):
    @classmethod
    def from_environment(cls):
        return super().from_environment(flag="ORION_RUN_ADMIN_IDENTITY_MIGRATION",
            run_id_name="ORION_ADMIN_IDENTITY_MIGRATION_RUN_ID")

def reviewed_migrations():
    reviewed = []
    for revision, parent, digest in MIGRATIONS:
        path = ROOT / "alembic" / "versions" / (revision + ".py")
        try:
            body = path.read_bytes().replace(b"\r\n", b"\n")
        except OSError:
            raise Stop("reviewed_migration_missing") from None
        require(hashlib.sha256(body).hexdigest() == digest, "unreviewed_admin_identity_migration")
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


def reviewed_operator_body():
    reviewed_migrations()
    path=ROOT/'alembic'/'versions'/(TARGET+'.py')
    tree=ast.parse(path.read_bytes().replace(b'\r\n',b'\n'))
    upgrade=next(node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=='upgrade')
    require(len(upgrade.body)==1 and isinstance(upgrade.body[0],ast.Expr),'reviewed_sql_shape_mismatch')
    call=upgrade.body[0].value
    require(isinstance(call,ast.Call) and len(call.args)==1,'reviewed_sql_shape_mismatch')
    statement=ast.literal_eval(call.args[0])
    bodies=re.findall(r'AS \$fn\$(.*?)\$fn\$',statement,re.DOTALL)
    require(len(bodies)==1,'reviewed_sql_shape_mismatch')
    return bodies[0]

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
            AND (SELECT version_num FROM public.alembic_version) IN ('0029_communication_contract','0030_legacy_admin_identity'))""").scalar_one() == 0,
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
            if row[0]=='panel_accounts':
                for column in row[3]:
                    if column[0] in {'email','password_hash'}:
                        require(column[2] is False,'legacy_nullable_columns_mismatch')
                        column[2]=True
                added=next((r for r in named_constraints(conn) if r[:2]==['panel_accounts',ADMIN_CHECK]),None)
                require(added is not None and row[4].count(added[7])==1,'admin_identity_check_missing')
                row[4].remove(added[7])
                definition=conn.execute(text('SELECT pg_get_indexdef(to_regclass(:name))'),{'name':'public.'+ADMIN_INDEX}).scalar_one()
                require(definition is not None and row[5].count(definition)==1,'admin_identity_index_missing')
                row[5].remove(definition)
    return catalog


def preserved_constraints(conn, *, projected=False):
    return [r for r in named_constraints(conn) if not (projected and r[:2]==['panel_accounts',ADMIN_CHECK])]


def identity_sequences(conn):
    state=conn.exec_driver_sql('SELECT last_value,is_called FROM public.inbound_events_ingress_order_seq').one()
    definition=conn.exec_driver_sql("""SELECT pg_get_userbyid(c.relowner),c.relacl::text,
        s.seqtypid::regtype::text,s.seqstart,s.seqincrement,s.seqmax,s.seqmin,s.seqcache,s.seqcycle
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_sequence s ON s.seqrelid=c.oid
        WHERE n.nspname='public' AND c.relname='inbound_events_ingress_order_seq'""").one()
    return [{'name':'inbound_events_ingress_order_seq','definition':list(definition),'state':list(state)}]


def preserved_programs(conn):
    value=proof.programs(conn)
    changed=[r for r in value['functions'] if r[:3]==['public','inbox_operator_version','uuid, uuid']]
    require(len(changed)==1,'operator_function_mismatch')
    # Only this reviewed body changes. Ownership, ACL, signature, security and
    # search_path remain in the comparison; the full target is rehearsed too.
    changed[0][-1]='<reviewed inbox_operator_version body>'
    return value


def snapshot(conn):
    return {'revision':backup.revision(conn),'counts':counts(conn),'rows':rows(conn),
        'catalog':old_catalog(conn),'security':proof.security_structure(conn),
        'constraints':preserved_constraints(conn),'programs':proof.programs(conn),
        'identity_sequences':identity_sequences(conn)}


@dataclass(frozen=True, repr=False)
class _RestoredSourceProof:
    run_id: str
    database: str
    snapshot: dict


def verify_new_schema(conn, *, pristine=False):
    previous.verify_new_schema(conn)
    catalog={r[0]:r for r in canonical_catalog(backup.catalog_structure(conn,TARGET_TABLES))}
    columns={c[0]:c[1:] for c in catalog['panel_accounts'][3]}
    require(columns['auth_provider']==['character varying(20)',True,"'password'::character varying"] and
        columns['legacy_username']==['character varying(254)',False,None] and
        columns['legacy_credential_digest']==['character(64)',False,None] and
        columns['email'][1] is False and columns['password_hash'][1] is False,'admin_identity_columns_mismatch')
    check=conn.execute(text("""SELECT convalidated,contype,pg_get_constraintdef(oid) FROM pg_constraint
        WHERE conrelid='public.panel_accounts'::regclass AND conname=:name"""),{'name':ADMIN_CHECK}).one_or_none()
    require(check is not None and tuple(check[:2])==(True,'c') and
        hashlib.sha256(check[2].encode()).hexdigest()==ADMIN_CHECK_SHA256,'admin_identity_check_mismatch')
    index=conn.execute(text("""SELECT i.indisvalid,i.indisready,i.indisunique,i.indnkeyatts,
        pg_get_userbyid(c.relowner),c.relacl::text,pg_get_expr(i.indpred,i.indrelid),a.attname
        FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid
        JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=i.indkey[0]
        WHERE c.oid=to_regclass(:name) AND i.indrelid='public.panel_accounts'::regclass"""),{'name':'public.'+ADMIN_INDEX}).one_or_none()
    require(index is not None and list(index)==[True,True,True,1,'app_migrator',None,
        "((auth_provider)::text = 'legacy_admin'::text)",'auth_provider'],'admin_identity_index_mismatch')
    body=conn.exec_driver_sql("SELECT prosrc FROM pg_proc WHERE oid='public.inbox_operator_version(uuid,uuid)'::regprocedure").scalar_one()
    require(body.replace('\r\n','\n')==reviewed_operator_body(),'operator_binding_guard_mismatch')
    old_catalog(conn, projected=True)
    if pristine:
        require(not conn.exec_driver_sql("""SELECT EXISTS(SELECT 1 FROM panel_accounts WHERE
            auth_provider<>'password' OR legacy_username IS NOT NULL OR legacy_credential_digest IS NOT NULL)""").scalar_one(),
            'identity_backfill_forbidden')


def manifest(conn):
    return {"revision": backup.revision(conn), "catalog": canonical_catalog(
        backup.catalog_structure(conn, TARGET_TABLES)), "security": proof.without_database_identity(
        proof.security_structure(conn)), "constraints": named_constraints(conn, TARGET_TABLES),
        "programs": proof.programs(conn),"identity_sequences":identity_sequences(conn)}


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
            old_programs=json.loads(json.dumps(expected['programs']))
            changed=[r for r in old_programs['functions'] if r[:3]==['public','inbox_operator_version','uuid, uuid']]
            require(len(changed)==1,'operator_function_mismatch')
            changed[0][-1]='<reviewed inbox_operator_version body>'
            require(preserved_programs(conn)==old_programs,'existing_programs_changed')
            require(identity_sequences(conn)==expected['identity_sequences'],'identity_sequence_changed')
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
        with tempfile.TemporaryDirectory(prefix="orion-admin-identity-migration-") as directory:
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
        if os.getenv("ORION_RUN_ADMIN_IDENTITY_MIGRATION", "0") == "0":
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
