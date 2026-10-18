"""Reviewed, additive 0017 -> 0018 operation; never imported by API startup.

Only the sealed upgrade below may run, as app_migrator, in one transaction.
Existing rows are never scanned, exported or rewritten. Catalog/ACL/RLS changes
outside the new table abort the transaction. An already-applied target is
validated in a read-only transaction; no seed, repair, downgrade or retry runs.
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
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.pool import NullPool

ROOT = Path(__file__).resolve().parents[1]
SOURCE = "0017_financial_settlements"
TARGET = "0018_sales_requests"
MIGRATION_SHA256 = "e253082452d91fd31490f95d1b3dd0c88cffa155aaca48c1a06b980004cc9a3f"
# Canonical PostgreSQL 16 manifest: columns/defaults, constraints, indexes,
# policies, table/column ACLs, owner and effective application-role privileges.
# This is derived only from the reviewed migration in a disposable database.
TARGET_SCHEMA_SHA256 = "2d9de1cad02f6d38d6cde3cdaa83d8f36ccc9d151d3ca743c7ffb6c8a6fa2859"
VERSION_SCHEMA_SHA256 = "5e9c39355414d66eb9c65a30febb743df9fa210246ac482ca2f1e16b831a26f4"
ADVISORY_LOCK = 681692820118
SOURCE_TABLES = frozenset((
    "alembic_version", "appointment_history", "appointment_items", "appointments",
    "authorized_staff", "billing_events", "business_profile_events", "business_profile_state",
    "business_profile_versions", "calendar_outbox", "campaign_recipients", "campaigns",
    "commercial_complements", "commercial_events", "commercial_inventory", "commercial_orders",
    "commercial_products", "commercial_quote_items", "commercial_quotes", "commercial_variants",
    "commission_advance_allocations", "commission_settlements", "contact_consents", "customers",
    "financial_entries", "google_connections", "human_handoffs", "inbound_events",
    "loyalty_coupons", "loyalty_rules", "loyalty_transactions", "management_ai_runs",
    "management_block_occurrences", "management_block_series", "management_notification_reads",
    "management_preferences", "management_requests", "management_site_preferences",
    "management_team_invitations", "message_outbox", "panel_accounts", "panel_email_tokens",
    "plans", "platform_audit", "professional_goals", "professional_services", "professionals",
    "public_booking_receipts", "public_site_daily_metrics", "schedule_blocks", "service_addons",
    "service_package_items", "service_packages", "services", "subscriptions", "tenant_audit",
    "tenant_membership_events", "tenant_memberships", "tenant_probe", "tenants", "time_off",
    "usage_counters", "whatsapp_channels", "workflow_definitions", "working_breaks", "working_hours",
))
TARGET_TABLES = SOURCE_TABLES | {"sales_requests"}


class Stop(RuntimeError):
    """Only fixed, non-sensitive phase codes are safe to print."""


def require(condition, code):
    if not condition:
        raise Stop(code)


@dataclass(frozen=True, repr=False)
class Settings:
    url: URL
    expected_host: str
    expected_database: str
    switch_role: bool = False

    def validate(self):
        require(self.url.drivername in {"postgres", "postgresql", "postgresql+psycopg"},
                "postgres_required")
        require(bool(self.url.host and self.url.database and self.url.username), "database_url_required")
        require(bool(self.expected_host and self.expected_database) and
                self.url.host == self.expected_host and self.url.database == self.expected_database,
                "database_identity_required")
        require(set(self.url.query) <= {"sslmode", "sslrootcert"} and
                all(isinstance(value, str) for value in self.url.query.values()),
                "unsupported_database_option")
        require(type(self.switch_role) is bool, "invalid_role_switch")

    @classmethod
    def from_environment(cls):
        require(os.getenv("ORION_RUN_SALES_REQUESTS_MIGRATION") == "1", "explicit_flag_required")
        switch = os.getenv("ORION_MIGRATION_SWITCH_ROLE", "0")
        require(switch in {"0", "1"}, "invalid_role_switch")
        try:
            value = cls(make_url(os.environ["MIGRATION_DATABASE_URL"]),
                        os.getenv("ORION_EXPECTED_DATABASE_HOST", ""),
                        os.getenv("ORION_EXPECTED_DATABASE_NAME", ""), switch == "1")
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
    require(hashlib.sha256(body).hexdigest() == MIGRATION_SHA256, "unreviewed_sales_migration")
    spec = importlib.util.spec_from_file_location("orion_reviewed_" + TARGET, path)
    require(spec is not None, "reviewed_migration_unloadable")
    module = importlib.util.module_from_spec(spec)
    # Execute exactly the checked bytes, never a second, unchecked file read.
    exec(compile(body, str(path), "exec"), module.__dict__)
    require(module.revision == TARGET and module.down_revision == SOURCE and
            module.branch_labels is None and module.depends_on is None, "unreviewed_migration_chain")
    return module


def engine_for(url):
    return create_engine(url.set(drivername="postgresql+psycopg"), poolclass=NullPool,
                         connect_args={"connect_timeout": 10}, echo=False, hide_parameters=True)


def prepare(conn, settings):
    settings.validate()
    conn.exec_driver_sql("SET LOCAL statement_timeout='30s'")
    conn.exec_driver_sql("SET LOCAL lock_timeout='5s'")
    conn.exec_driver_sql("SET LOCAL idle_in_transaction_session_timeout='60s'")
    conn.exec_driver_sql("SET LOCAL search_path=pg_catalog,public")
    require(conn.exec_driver_sql("SELECT current_database()").scalar_one() == settings.expected_database,
            "database_identity_required")
    require(conn.exec_driver_sql("SELECT current_setting('server_version_num')::int/10000").scalar_one() == 16,
            "postgres_major_mismatch")
    roles = conn.exec_driver_sql("""SELECT rolname,rolsuper,rolbypassrls FROM pg_roles
        WHERE rolname IN ('app_runtime','app_support','app_migrator')""").all()
    require(len(roles) == 3 and all(not row.rolsuper and not row.rolbypassrls for row in roles),
            "unsafe_application_roles")
    if settings.switch_role:
        conn.exec_driver_sql("SET LOCAL ROLE app_migrator")
    require(conn.exec_driver_sql("SELECT current_user").scalar_one() == "app_migrator", "migrator_role_required")
    require(conn.exec_driver_sql("SELECT count(*) FROM pg_event_trigger").scalar_one() == 0,
            "unreviewed_event_triggers")
    require(conn.exec_driver_sql("""SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relkind NOT IN ('r','i')""").scalar_one() == 0,
            "unreviewed_public_relations")
    version_table = catalog(conn).get("alembic_version")
    require(version_table is not None and not version_table["triggers"] and not version_table["rules"],
            "unreviewed_revision_programs")
    # The sole existing-table write must not invoke a custom CHECK, expression
    # index, FK, rule or trigger that could mutate application data.
    require(fingerprint(version_table) == VERSION_SCHEMA_SHA256, "unexpected_revision_schema")
    require(conn.execute(text("SELECT pg_try_advisory_xact_lock(:key)"),
                         {"key": ADVISORY_LOCK}).scalar_one(), "migration_already_running")


def revision(conn):
    values = conn.exec_driver_sql("SELECT version_num FROM public.alembic_version").scalars().all()
    require(len(values) == 1, "unexpected_revision_rows")
    return values[0]


def table_names(conn):
    return frozenset(conn.exec_driver_sql("SELECT tablename FROM pg_tables WHERE schemaname='public'").scalars())


_CATALOG = text("""
    SELECT c.relname, jsonb_build_object(
      'owner',pg_get_userbyid(c.relowner),'rls',c.relrowsecurity,'force_rls',c.relforcerowsecurity,
      'persistence',c.relpersistence,'options',c.reloptions,
      'acl',(SELECT jsonb_agg(v::text ORDER BY v::text) FROM unnest(c.relacl) v),
      'columns',(SELECT jsonb_agg(jsonb_build_array(a.attname,format_type(a.atttypid,a.atttypmod),
        a.attnotnull,pg_get_expr(d.adbin,d.adrelid),a.attidentity,a.attgenerated,
        (SELECT jsonb_agg(v::text ORDER BY v::text) FROM unnest(a.attacl) v),
        cn.nspname,co.collname) ORDER BY a.attnum)
        FROM pg_attribute a LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
        LEFT JOIN pg_collation co ON co.oid=a.attcollation LEFT JOIN pg_namespace cn ON cn.oid=co.collnamespace
        WHERE a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped),
      'constraints',(SELECT jsonb_agg(jsonb_build_array(x.conname,x.contype,x.convalidated,
        x.condeferrable,x.condeferred,x.connoinherit,pg_get_constraintdef(x.oid)) ORDER BY x.conname)
        FROM pg_constraint x WHERE x.conrelid=c.oid),
      'indexes',(SELECT jsonb_agg(jsonb_build_array(ic.relname,pg_get_indexdef(i.indexrelid),
        i.indisvalid,i.indisready,i.indisunique,i.indisprimary,i.indisexclusion,
        pg_get_userbyid(ic.relowner),ic.reloptions,
        (SELECT jsonb_agg(v::text ORDER BY v::text) FROM unnest(ic.relacl) v)) ORDER BY ic.relname)
        FROM pg_index i JOIN pg_class ic ON ic.oid=i.indexrelid WHERE i.indrelid=c.oid),
      'policies',(SELECT jsonb_agg(jsonb_build_array(p.policyname,p.permissive,p.roles,p.cmd,p.qual,p.with_check)
        ORDER BY p.policyname) FROM pg_policies p WHERE p.schemaname='public' AND p.tablename=c.relname),
      'triggers',(SELECT jsonb_agg(jsonb_build_array(t.tgname,t.tgenabled,pg_get_triggerdef(t.oid)) ORDER BY t.tgname)
        FROM pg_trigger t WHERE t.tgrelid=c.oid AND NOT t.tgisinternal),
      'rules',(SELECT jsonb_agg(jsonb_build_array(r.rulename,pg_get_ruledef(r.oid)) ORDER BY r.rulename)
        FROM pg_rewrite r WHERE r.ev_class=c.oid),
      'runtime_privileges',jsonb_build_array(
        has_table_privilege('app_runtime',c.oid,'SELECT'),has_table_privilege('app_runtime',c.oid,'INSERT'),
        has_table_privilege('app_runtime',c.oid,'UPDATE'),has_table_privilege('app_runtime',c.oid,'DELETE'),
        has_table_privilege('app_runtime',c.oid,'TRUNCATE'),has_table_privilege('app_runtime',c.oid,'REFERENCES'),
        has_table_privilege('app_runtime',c.oid,'TRIGGER')),
      'support_privileges',jsonb_build_array(
        has_table_privilege('app_support',c.oid,'SELECT'),has_table_privilege('app_support',c.oid,'INSERT'),
        has_table_privilege('app_support',c.oid,'UPDATE'),has_table_privilege('app_support',c.oid,'DELETE'),
        has_table_privilege('app_support',c.oid,'TRUNCATE'),has_table_privilege('app_support',c.oid,'REFERENCES'),
        has_table_privilege('app_support',c.oid,'TRIGGER'))
    ) AS definition
    FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='public' AND c.relkind='r' ORDER BY c.relname
""")


def catalog(conn):
    return dict(conn.execute(_CATALOG).all())


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def existing_catalog(conn):
    """Metadata only. The new FK's internal RI triggers are intentionally excluded.

    No ordinary table is read here, including when it contains millions of rows.
    Existing user triggers, function bodies and permissions are compared exactly.
    """
    tables = catalog(conn)
    tables.pop("sales_requests", None)
    return {
        "tables": tables,
        "schemas": [list(row) for row in conn.exec_driver_sql("""SELECT nspname,pg_get_userbyid(nspowner),nspacl::text
            FROM pg_namespace WHERE nspname='public'""")],
        "default_acl": [list(row) for row in conn.exec_driver_sql("""SELECT pg_get_userbyid(d.defaclrole),n.nspname,
            d.defaclobjtype,d.defaclacl::text FROM pg_default_acl d LEFT JOIN pg_namespace n ON n.oid=d.defaclnamespace
            ORDER BY pg_get_userbyid(d.defaclrole),n.nspname,d.defaclobjtype""")],
        "functions": [list(row) for row in conn.exec_driver_sql("""SELECT p.proname,oidvectortypes(p.proargtypes),
            pg_get_userbyid(p.proowner),p.prosecdef,p.proconfig,p.proacl::text,pg_get_functiondef(p.oid)
            FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname='public' AND p.prokind IN ('f','p')
            ORDER BY p.proname,oidvectortypes(p.proargtypes)""")],
        "memberships": [list(row) for row in conn.exec_driver_sql("""SELECT pg_get_userbyid(roleid),
            pg_get_userbyid(member),admin_option,inherit_option,set_option FROM pg_auth_members
            ORDER BY pg_get_userbyid(roleid),pg_get_userbyid(member)""")],
    }


def verify_target(conn):
    require(table_names(conn) == TARGET_TABLES, "unexpected_target_tables")
    actual = catalog(conn).get("sales_requests")
    require(actual is not None and fingerprint(actual) == TARGET_SCHEMA_SHA256, "unexpected_sales_schema")


def apply_exact_migration(engine, settings):
    module = reviewed_migration()
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as conn:
        with conn.begin():
            prepare(conn, settings)
            # A concurrent migration must not race the revision change, even if
            # that other operation does not use this runner's advisory lock.
            conn.exec_driver_sql("SELECT version_num FROM public.alembic_version FOR UPDATE")
            require(revision(conn) == SOURCE and table_names(conn) == SOURCE_TABLES,
                    "unexpected_source_schema")
            before = existing_catalog(conn)
            require(all(value["owner"] == "app_migrator" for value in before["tables"].values()),
                    "unexpected_source_owner")
            with Operations.context(MigrationContext.configure(conn)):
                module.upgrade()
            verify_target(conn)
            require(not conn.exec_driver_sql("SELECT EXISTS(SELECT 1 FROM public.sales_requests)").scalar_one(),
                    "unexpected_seeded_requests")
            result = conn.execute(text("UPDATE public.alembic_version SET version_num=:target WHERE version_num=:source"),
                                  {"target": TARGET, "source": SOURCE})
            require(result.rowcount == 1 and revision(conn) == TARGET, "revision_update_failed")
            require(existing_catalog(conn) == before, "existing_catalog_changed")
    return {"ok": True, "status": "applied", "source": SOURCE, "revision": TARGET}


def execute(settings):
    settings.validate()
    reviewed_migration()  # Refuse altered Python before making a connection.
    engine = engine_for(settings.url)
    try:
        with engine.connect().execution_options(isolation_level="REPEATABLE READ") as conn:
            with conn.begin():
                conn.exec_driver_sql("SET TRANSACTION READ ONLY")
                prepare(conn, settings)
                current = revision(conn)
                if current == TARGET:
                    verify_target(conn)
                    return {"ok": True, "status": "already_applied", "source": SOURCE, "revision": TARGET}
                require(current == SOURCE and table_names(conn) == SOURCE_TABLES, "unexpected_source_schema")
        # Recheck everything in the write transaction. A change between this
        # read-only preflight and DDL aborts; no automatic retry or repair runs.
        return apply_exact_migration(engine, settings)
    finally:
        engine.dispose()


def main():
    try:
        flag = os.getenv("ORION_RUN_SALES_REQUESTS_MIGRATION", "0")
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
