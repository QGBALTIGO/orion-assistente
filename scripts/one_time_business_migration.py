"""Explicit, one-time 0009 -> 0011 operation; never import from app startup.

Requires a temporary database-owner credential, a private encrypted backup store,
and same-major PostgreSQL clients. All failures expose phase codes, not DB errors.
The only database this module can DROP is its own UUID restore-test database.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from alembic.migration import MigrationContext
from alembic.operations import Operations
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.pool import NullPool

SOURCE = "0009_email_otp"
TARGET = "0011_commercial_catalog"
MIGRATIONS = (("0010_business_profiles", SOURCE), (TARGET, "0010_business_profiles"))
MAGIC = b"ORIONDB1"
MAX_BYTES = 32 * 1024 * 1024
TIMEOUT = 120
ROOT = Path(__file__).resolve().parents[1]
LEGACY_TABLES = frozenset((
    "alembic_version", "tenants", "tenant_probe", "platform_audit", "authorized_staff",
    "professionals", "services", "professional_services", "working_hours", "working_breaks",
    "time_off", "schedule_blocks", "tenant_audit", "customers", "appointments",
    "appointment_items", "appointment_history", "whatsapp_channels", "inbound_events",
    "message_outbox", "google_connections", "calendar_outbox", "plans", "subscriptions",
    "billing_events", "usage_counters", "human_handoffs", "contact_consents", "campaigns",
    "campaign_recipients", "workflow_definitions", "panel_accounts", "panel_email_tokens",
))
MEMBERSHIP_TABLES = frozenset(("tenant_memberships", "tenant_membership_events"))
NEW_TABLES = MEMBERSHIP_TABLES | frozenset((
    "business_profile_versions", "business_profile_state", "business_profile_events",
    "commercial_products", "commercial_variants", "commercial_inventory",
    "commercial_complements", "commercial_quotes", "commercial_quote_items",
    "commercial_orders", "commercial_events",
))
IMMUTABLE_TABLES = frozenset(("tenant_membership_events", "business_profile_versions",
    "business_profile_events", "commercial_quotes", "commercial_quote_items", "commercial_events"))


class Stop(RuntimeError):
    """Safe operational error: codes must never contain external error text."""


def require(condition, code):
    if not condition:
        raise Stop(code)


@dataclass(frozen=True, repr=False)
class Settings:
    url: URL
    bridge_url: str
    token: str
    run_id: str

    @classmethod
    def from_environment(cls):
        require(os.getenv("ORION_RUN_BUSINESS_MIGRATION") == "1", "explicit_flag_required")
        require(os.getenv("ORION_MIGRATION_SWITCH_ROLE") == "1", "role_switch_required")
        try:
            url = make_url(os.environ["MIGRATION_DATABASE_URL"])
            run_id = os.environ["ORION_BUSINESS_MIGRATION_RUN_ID"]
            require(str(UUID(run_id)) == run_id and UUID(run_id).version == 4, "canonical_run_id_required")
        except Stop:
            raise
        except Exception:
            raise Stop("invalid_configuration") from None
        require(url.drivername in {"postgres", "postgresql", "postgresql+psycopg"}, "postgres_required")
        require(bool(url.host and url.database and url.username and url.password), "owner_url_required")
        require(url.host == os.getenv("ORION_EXPECTED_DATABASE_HOST") and
                url.database == os.getenv("ORION_EXPECTED_DATABASE_NAME"), "database_identity_required")
        # Accept only transport options we also pass unchanged to the PG clients.
        require(set(url.query) <= {"sslmode", "sslrootcert"}, "unsupported_database_option")
        bridge = os.getenv("QR_BRIDGE_URL", "").rstrip("/")
        parsed = urlsplit(bridge)
        require(parsed.scheme == "http" and parsed.hostname is not None and
                re.fullmatch(r"[a-z0-9][a-z0-9-]*\.railway\.internal", parsed.hostname) is not None and
                parsed.port == 3000 and not (parsed.username or parsed.password or parsed.path or
                                             parsed.query or parsed.fragment), "private_bridge_required")
        token = os.getenv("QR_BRIDGE_TOKEN", "")
        require(len(token.encode("utf-8")) >= 32, "backup_key_required")
        return cls(url.set(drivername="postgresql+psycopg"), bridge, token, run_id)


def backup_key(token):
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=b"",
                info=b"orion:database-backup:v1").derive(token.encode("utf-8"))


def encrypt_dump(dump, token, run_id):
    require(dump.startswith(b"PGDMP"), "invalid_custom_dump")
    require(len(dump) + len(MAGIC) + 28 <= MAX_BYTES, "backup_too_large")
    nonce = os.urandom(12)
    return MAGIC + nonce + AESGCM(backup_key(token)).encrypt(nonce, dump, MAGIC + run_id.encode("ascii"))


def decrypt_dump(envelope, token, run_id):
    require(len(MAGIC) + 28 < len(envelope) <= MAX_BYTES and envelope.startswith(MAGIC), "invalid_envelope")
    try:
        plaintext = AESGCM(backup_key(token)).decrypt(envelope[8:20], envelope[20:], MAGIC + run_id.encode("ascii"))
    except Exception:
        raise Stop("backup_authentication_failed") from None
    require(plaintext.startswith(b"PGDMP"), "invalid_custom_dump")
    return plaintext


def durable_backup(settings, envelope, client):
    """POST exactly once. An uncertain POST is resolved by one bounded GET."""
    digest = hashlib.sha256(envelope).hexdigest()
    path = settings.bridge_url + "/internal/ops/database-backups/" + settings.run_id
    headers = {"X-Orion-Bridge-Key": settings.token, "X-Orion-SHA256": digest,
               "Content-Type": "application/octet-stream"}
    try:
        response = client.post(path, content=envelope, headers=headers)
    except httpx.TransportError:
        response = None
    if response is not None:
        require(response.status_code == 201, "backup_write_not_acknowledged")
        try:
            ack = response.json()
        except Exception:
            raise Stop("invalid_backup_ack") from None
        require(ack == {"id": settings.run_id, "sha256": digest, "size": len(envelope)}, "invalid_backup_ack")
    received = bytearray()
    try:
        with client.stream("GET", path, headers=headers) as result:
            require(result.status_code == 200, "backup_readback_failed")
            require(result.headers.get("content-type", "").split(";")[0] == "application/octet-stream", "invalid_backup_content_type")
            for chunk in result.iter_bytes(65536):
                require(len(received) + len(chunk) <= MAX_BYTES, "backup_readback_too_large")
                received.extend(chunk)
    except Stop:
        raise
    except Exception:
        raise Stop("backup_readback_failed") from None
    require(bytes(received) == envelope, "backup_readback_mismatch")
    return decrypt_dump(bytes(received), settings.token, settings.run_id), digest


def engine_for(url):
    return create_engine(url, poolclass=NullPool, connect_args={"connect_timeout": 10}, echo=False)


def pg_environment(url):
    # Never place a password or connection URL in a command line. Discard inherited
    # PGOPTIONS/PGSERVICE so neither can redirect the command or alter its role.
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("PG") and
           not any(word in key.upper() for word in ("TOKEN", "SECRET", "PASSWORD", "DATABASE_URL"))}
    env.update(PGHOST=url.host, PGPORT=str(url.port or 5432), PGDATABASE=url.database,
               PGUSER=url.username, PGPASSWORD=url.password, PGCONNECT_TIMEOUT="10",
               PGAPPNAME="orion_explicit_migration", PGOPTIONS="-c statement_timeout=120000 -c lock_timeout=10000")
    for key, value in url.query.items():
        env[{"sslmode": "PGSSLMODE", "sslrootcert": "PGSSLROOTCERT"}[key]] = value
    return env


def run_client(program, args, url=None, max_file=None):
    executable = shutil.which(program)
    require(executable is not None, "postgres_client_missing")
    if max_file is not None:
        # pg_dump writes a private file. Poll its size while it runs, not only
        # afterwards, so a larger-than-reviewed database cannot fill the disk.
        process = subprocess.Popen([executable, *args], env=pg_environment(url),
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        started = time.monotonic()
        try:
            while process.poll() is None:
                require(time.monotonic() - started < TIMEOUT, "postgres_client_timeout")
                require(max_file.stat().st_size <= MAX_BYTES - 36, "dump_size_invalid")
                try:
                    process.wait(timeout=0.1)
                except subprocess.TimeoutExpired:
                    pass
            require(process.returncode == 0, "postgres_client_failed")
            return b""
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
    try:
        result = subprocess.run([executable, *args], env=pg_environment(url) if url else {},
                                capture_output=True, timeout=TIMEOUT, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise Stop("postgres_client_failed") from None
    require(result.returncode == 0, "postgres_client_failed")
    return result.stdout


def client_major(program):
    match = re.search(rb"\(PostgreSQL\) (\d+)\.", run_client(program, ["--version"]))
    require(match is not None, "postgres_client_version_unknown")
    return int(match[1])


def set_timeouts(conn):
    conn.exec_driver_sql("SET LOCAL statement_timeout='120s'")
    conn.exec_driver_sql("SET LOCAL lock_timeout='10s'")
    conn.exec_driver_sql("SET LOCAL idle_in_transaction_session_timeout='180s'")
    conn.exec_driver_sql("SET LOCAL search_path=public,pg_catalog")


def table_names(conn):
    return frozenset(conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public'")).scalars())


def revision(conn):
    values = conn.execute(text("SELECT version_num FROM public.alembic_version")).scalars().all()
    require(len(values) == 1, "unexpected_revision_rows")
    return values[0]


def counts(conn, tables=LEGACY_TABLES):
    require(set(tables) <= LEGACY_TABLES | NEW_TABLES, "unapproved_table")
    return {name: conn.exec_driver_sql('SELECT count(*) FROM public."' + name + '"').scalar_one()
            for name in sorted(tables)}


def catalog_fingerprint(conn, tables=LEGACY_TABLES):
    """No row values, names of customers, owners or ACLs are included."""
    query = text("""
        SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity,
          (SELECT jsonb_agg(jsonb_build_array(a.attname,format_type(a.atttypid,a.atttypmod),a.attnotnull,
             pg_get_expr(d.adbin,d.adrelid)) ORDER BY a.attnum)
           FROM pg_attribute a LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
           WHERE a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped) AS columns,
          (SELECT jsonb_agg(pg_get_constraintdef(x.oid) ORDER BY x.conname)
           FROM pg_constraint x WHERE x.conrelid=c.oid) AS constraints,
          (SELECT jsonb_agg(pg_get_indexdef(i.indexrelid) ORDER BY ic.relname)
           FROM pg_index i JOIN pg_class ic ON ic.oid=i.indexrelid WHERE i.indrelid=c.oid) AS indexes,
          (SELECT jsonb_agg(jsonb_build_array(p.policyname,p.permissive,p.roles,p.cmd,p.qual,p.with_check)
             ORDER BY p.policyname) FROM pg_policies p WHERE p.schemaname='public' AND p.tablename=c.relname) AS policies
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relkind='r' ORDER BY c.relname
    """)
    rows = [list(row) for row in conn.execute(query) if row[0] in tables]
    return hashlib.sha256(json.dumps(rows, sort_keys=True, default=str).encode()).hexdigest()


def owner_preflight(conn, settings):
    set_timeouts(conn)
    row = conn.execute(text("""SELECT current_database() AS name, d.oid, d.datdba,
        r.oid AS role_oid, r.rolsuper, r.rolbypassrls, r.rolcreatedb,
        current_setting('server_version_num')::int/10000 AS major
        FROM pg_database d JOIN pg_roles r ON r.rolname=current_user WHERE d.datname=current_database()""")).mappings().one()
    require(row["name"] == settings.url.database and row["datdba"] == row["role_oid"], "database_owner_required")
    require((row["rolsuper"] or row["rolbypassrls"]) and (row["rolsuper"] or row["rolcreatedb"]), "owner_backup_privileges_required")
    require(revision(conn) == SOURCE and table_names(conn) == LEGACY_TABLES, "unexpected_source_schema")
    roles = conn.execute(text("SELECT rolname,rolsuper,rolbypassrls FROM pg_roles WHERE rolname IN ('app_runtime','app_support','app_migrator')")).all()
    require(len(roles) == 3 and all(not row.rolsuper and not row.rolbypassrls for row in roles), "unsafe_application_roles")
    extensions = set(conn.execute(text("SELECT extname FROM pg_extension")).scalars())
    require(extensions == {"plpgsql", "btree_gist"}, "unexpected_extensions")
    require(conn.exec_driver_sql("SELECT count(*) FROM pg_largeobject_metadata").scalar_one() == 0, "unreviewed_large_objects")
    require(conn.exec_driver_sql("""SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND NOT EXISTS
        (SELECT 1 FROM pg_depend d WHERE d.classid='pg_proc'::regclass AND d.objid=p.oid AND d.deptype='e')""").scalar_one() == 0, "unreviewed_database_functions")
    require(conn.exec_driver_sql("""SELECT count(*) FROM pg_namespace WHERE nspname NOT IN ('public','pg_catalog','information_schema')
        AND nspname NOT LIKE 'pg_toast%' AND nspname NOT LIKE 'pg_temp_%'""").scalar_one() == 0, "unreviewed_database_schema")
    require(conn.exec_driver_sql("""SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relkind NOT IN ('r','i')""").scalar_one() == 0, "unreviewed_database_relations")
    conn.exec_driver_sql("SET LOCAL ROLE app_migrator")
    require(conn.exec_driver_sql("SELECT current_user").scalar_one() == "app_migrator", "role_switch_failed")
    conn.exec_driver_sql("RESET ROLE")
    return dict(row)


def private_write(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def capture_dump(engine, settings, path):
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as conn:
        with conn.begin():
            set_timeouts(conn)
            conn.exec_driver_sql("SET TRANSACTION READ ONLY")
            require(revision(conn) == SOURCE and table_names(conn) == LEGACY_TABLES, "source_changed_before_backup")
            snapshot = conn.exec_driver_sql("SELECT pg_export_snapshot()").scalar_one()
            before = counts(conn)
            fingerprint = catalog_fingerprint(conn)
            # Reserve the file with private permissions before pg_dump opens it.
            private_write(path, b"")
            run_client("pg_dump", ["--format=custom", "--no-password", "--snapshot=" + snapshot,
                "--lock-wait-timeout=10000", "--file=" + str(path)], settings.url, max_file=path)
            require(5 <= path.stat().st_size <= MAX_BYTES - 36, "dump_size_invalid")
            return before, fingerprint


def restore_name(run_id):
    require(str(UUID(run_id)) == run_id and UUID(run_id).version == 4, "canonical_run_id_required")
    return "orion_restore_" + UUID(run_id).hex


def safe_drop_created(conn, name, created_oid, owner_oid, source_oid):
    require(re.fullmatch(r"orion_restore_[0-9a-f]{32}", name) is not None and created_oid != source_oid,
            "restore_cleanup_identity_failed")
    row = conn.execute(text("SELECT oid,datdba FROM pg_database WHERE datname=:name"), {"name": name}).first()
    require(row is not None and row.oid == created_oid and row.datdba == owner_oid, "restore_cleanup_identity_failed")
    # No FORCE and no pg_terminate_backend: unexpected sessions stop this operation.
    conn.exec_driver_sql('DROP DATABASE "' + name + '"')


def verify_restore(engine, settings, source_identity, dump_path, expected_counts, fingerprint):
    name = restore_name(settings.run_id)
    require(name != settings.url.database, "restore_source_collision")
    created_oid = None
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as admin:
        try:
            require(not admin.execute(text("SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname=:name)"), {"name": name}).scalar_one(), "restore_database_already_exists")
            admin.exec_driver_sql('CREATE DATABASE "' + name + '" TEMPLATE template0 ALLOW_CONNECTIONS false')
            created_oid = admin.execute(text("SELECT oid FROM pg_database WHERE datname=:name"), {"name": name}).scalar_one()
            admin.exec_driver_sql('REVOKE CONNECT ON DATABASE "' + name + '" FROM PUBLIC,app_runtime,app_support,app_migrator')
            admin.exec_driver_sql('ALTER DATABASE "' + name + '" ALLOW_CONNECTIONS true')
            temp_url = settings.url.set(database=name)
            run_client("pg_restore", ["--no-password", "--no-owner", "--no-acl", "--exit-on-error",
                "--single-transaction", "--dbname=" + name, str(dump_path)], temp_url)
            restored = engine_for(temp_url)
            try:
                with restored.begin() as check:
                    set_timeouts(check)
                    require(revision(check) == SOURCE and table_names(check) == LEGACY_TABLES, "restored_revision_mismatch")
                    require(counts(check) == expected_counts, "restored_count_mismatch")
                    require(catalog_fingerprint(check) == fingerprint, "restored_catalog_mismatch")
            finally:
                restored.dispose()
        finally:
            if created_oid is not None:
                safe_drop_created(admin, name, created_oid, source_identity["role_oid"], source_identity["oid"])


def verify_new_schema(conn):
    require(table_names(conn) == LEGACY_TABLES | NEW_TABLES, "target_table_mismatch")
    for name in sorted(NEW_TABLES):
        row = conn.execute(text("""SELECT c.relrowsecurity,c.relforcerowsecurity,
            pg_get_userbyid(c.relowner) AS owner,
            has_table_privilege('app_runtime',c.oid,'SELECT') AS runtime_read,
            has_table_privilege('app_runtime',c.oid,'INSERT') AS runtime_insert,
            has_table_privilege('app_runtime',c.oid,'UPDATE') AS runtime_update,
            has_table_privilege('app_runtime',c.oid,'DELETE') AS runtime_delete,
            has_table_privilege('app_support',c.oid,'SELECT') AS support_read,
            has_table_privilege('app_support',c.oid,'INSERT') AS support_insert,
            has_table_privilege('app_support',c.oid,'UPDATE') AS support_update,
            has_table_privilege('app_support',c.oid,'DELETE') AS support_delete
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND c.relname=:name"""), {"name": name}).mappings().one()
        membership = name in MEMBERSHIP_TABLES
        require(row["relrowsecurity"] and row["relforcerowsecurity"] and row["owner"] == "app_migrator", "target_rls_owner_failed")
        expected_update = name not in IMMUTABLE_TABLES
        require(row["runtime_read"] == (not membership) and row["runtime_insert"] == (not membership) and
                row["runtime_update"] == (not membership and expected_update) and not row["runtime_delete"] and
                row["support_read"] == membership and row["support_insert"] == membership and
                row["support_update"] == (membership and expected_update) and not row["support_delete"], "target_grants_failed")
    require(all(value == 0 for value in counts(conn, NEW_TABLES).values()), "unexpected_seeded_data")


def apply_exact_migrations(engine):
    """Only these two checked-in upgrade functions; single transactional commit."""
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as conn:
        with conn.begin():
            set_timeouts(conn)
            require(conn.exec_driver_sql("SELECT pg_try_advisory_xact_lock(681692820104)").scalar_one(), "migration_already_running")
            conn.exec_driver_sql("SELECT version_num FROM public.alembic_version FOR UPDATE")
            require(revision(conn) == SOURCE and table_names(conn) == LEGACY_TABLES, "source_changed_before_migration")
            before = counts(conn)
            fingerprint = catalog_fingerprint(conn)
            conn.exec_driver_sql("SET LOCAL ROLE app_migrator")
            require(conn.exec_driver_sql("SELECT current_user").scalar_one() == "app_migrator", "role_switch_failed")
            context = MigrationContext.configure(conn)
            with Operations.context(context):
                for name, previous in MIGRATIONS:
                    spec = importlib.util.spec_from_file_location("orion_reviewed_" + name, ROOT / "alembic" / "versions" / (name + ".py"))
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    require(module.revision == name and module.down_revision == previous, "unreviewed_migration_chain")
                    module.upgrade()
            conn.exec_driver_sql("RESET ROLE")
            require(counts(conn) == before and catalog_fingerprint(conn) == fingerprint, "legacy_changed_by_migration")
            verify_new_schema(conn)
            conn.exec_driver_sql("SET LOCAL ROLE app_migrator")
            require(conn.exec_driver_sql("SELECT current_user").scalar_one() == "app_migrator", "role_switch_failed")
            result = conn.execute(text("UPDATE public.alembic_version SET version_num=:target WHERE version_num=:source"), {"target": TARGET, "source": SOURCE})
            require(result.rowcount == 1 and revision(conn) == TARGET, "revision_update_failed")
            conn.exec_driver_sql("RESET ROLE")
            return before


def execute(settings):
    engine = engine_for(settings.url)
    try:
        with engine.begin() as conn:
            identity = owner_preflight(conn, settings)
        require(client_major("pg_dump") == client_major("pg_restore") == identity["major"], "postgres_major_mismatch")
        # These files contain plaintext only inside this private ephemeral directory.
        # The retained copy on the bridge is authenticated ciphertext exclusively.
        with tempfile.TemporaryDirectory(prefix="orion-migration-") as directory:
            os.chmod(directory, 0o700)
            require(shutil.disk_usage(directory).free > MAX_BYTES * 3 + 16 * 1024 * 1024, "temporary_disk_space_low")
            original = Path(directory) / "snapshot.dump"
            backup_counts, fingerprint = capture_dump(engine, settings, original)
            envelope = encrypt_dump(original.read_bytes(), settings.token, settings.run_id)
            with httpx.Client(timeout=httpx.Timeout(60, connect=10), follow_redirects=False, trust_env=False) as client:
                restored_bytes, digest = durable_backup(settings, envelope, client)
            readback = Path(directory) / "verified.dump"
            private_write(readback, restored_bytes)
            verify_restore(engine, settings, identity, readback, backup_counts, fingerprint)
            migrated_counts = apply_exact_migrations(engine)
        return {"status": "complete", "backup_id": settings.run_id, "backup_sha256": digest,
                "backup_bytes": len(envelope), "backup_counts": backup_counts,
                "preserved_counts": migrated_counts, "revision": TARGET, "restore_verified": True}
    finally:
        engine.dispose()


def main():
    try:
        result = execute(Settings.from_environment())
    except Stop as error:
        print(json.dumps({"status": "stopped", "code": str(error)}))
        return 1
    except Exception:
        # SQLAlchemy/psycopg/subprocess errors may include host, role, query or data.
        print(json.dumps({"status": "stopped", "code": "operation_failed_no_details"}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
