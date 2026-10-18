"""Explicit, byte-reviewed 0012 -> 0017 operation; never application startup.

The source must stay unchanged between its consistent backup and the final
transaction. A private encrypted readback is restored with owners and ACLs into
this run's isolated database, where the five upgrades are rehearsed first.
Only that UUID database can be dropped. No role bootstrap, downgrade, source
restore, session termination, automatic retry, or already-at-head shortcut.
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
from scripts import one_time_fulfillment_migration as fulfillment

ROOT = backup.ROOT
SOURCE = "0012_commercial_fulfillment"
TARGET = "0017_financial_settlements"
SOURCE_TABLES = backup.LEGACY_TABLES | backup.NEW_TABLES
NEW_TABLES = frozenset((
    "commission_advance_allocations", "commission_settlements", "financial_entries",
    "loyalty_coupons", "loyalty_rules", "loyalty_transactions", "management_ai_runs",
    "management_block_occurrences", "management_block_series", "management_notification_reads",
    "management_preferences", "management_requests", "management_site_preferences",
    "management_team_invitations", "professional_goals", "public_booking_receipts",
    "public_site_daily_metrics", "service_addons", "service_package_items", "service_packages",
))
TARGET_TABLES = SOURCE_TABLES | NEW_TABLES
# Normalize CRLF only. Verify all five files before executing any of their Python.
MIGRATIONS = (
    ("0013_management", SOURCE,
     "161187e1bf34a9a29bd182ad9ba7cee9583864b050e0cb73da609b9a04a17447"),
    ("0014_public_catalog", "0013_management",
     "c98b783b7c5bf855ab94dc017071d439cd17f66475267998efdafb7850d95ba8"),
    ("0015_loyalty_rewards", "0014_public_catalog",
     "4b1f03393d50f828fda71485b3314f497f3c847b954bf657c355b85d460208aa"),
    ("0016_management_extras", "0015_loyalty_rewards",
     "c6b1e196c5af6beeacc0415383da514cff07297ece4b3a79563d5bd65f719b99"),
    (TARGET, "0016_management_extras",
     "74a02d73b2dae114be898943453d39de2d7de7b18aeae4f6dd10121bf5863f87"),
)
require = backup.require
Stop = backup.Stop

# Exact additions to pre-existing tables, in PostgreSQL attribute order.
# New tables are separately required to be empty after the reviewed upgrades.
ADDED_COLUMNS = {
    "customers": [
        ["instagram", "character varying(120)", False, None],
        ["neighborhood", "character varying(120)", False, None],
        ["city", "character varying(120)", False, None],
        ["deleted_at", "timestamp with time zone", False, None],
        ["email", "character varying(320)", False, None],
        ["birth_date", "date", False, None], ["notes", "text", False, None],
        ["active", "boolean", True, "true"], ["version", "integer", True, "1"],
        ["updated_at", "timestamp with time zone", True, "now()"],
    ],
    "services": [
        ["category", "character varying(80)", False, None],
        ["color", "character varying(7)", True, "'#8b5cf6'::character varying"],
        ["image_url", "character varying(2048)", False, None],
        ["sort_order", "integer", True, "0"],
        ["catalog_kind", "character varying(12)", True, "'base'::character varying"],
        ["addons_version", "integer", True, "0"],
    ],
    "professionals": [
        ["specialty", "character varying(120)", False, None],
        ["color", "character varying(7)", True, "'#31ad52'::character varying"],
        ["commission_percent", "numeric(5,2)", True, "'0'::numeric"],
        ["accepts_appointments", "boolean", True, "true"],
        ["image_url", "character varying(2048)", False, None],
    ],
    "campaigns": [
        ["message", "text", False, None],
        ["audience", "character varying(24)", True, "'all_active'::character varying"],
        ["scheduled_at", "timestamp with time zone", False, None],
        ["version", "integer", True, "1"],
        ["updated_at", "timestamp with time zone", True, "now()"],
    ],
    "appointments": [
        ["commission_percent_snapshot", "numeric(5,2)", False, None],
        ["commission_cents", "bigint", False, None],
        ["original_total_cents", "integer", False, None],
        ["discount_cents", "integer", True, "0"],
    ],
    "appointment_items": [["catalog_snapshot", "jsonb", False, None]],
}
ADDED_INDEXES = {
    "ix_customers_management_name": ("customers", "tenant_id, active, name, id"),
    "ix_appointments_management_period": ("appointments", "tenant_id, start_at, status"),
    "ix_appointments_management_customer": ("appointments", "tenant_id, customer_id, status, start_at"),
    "ix_services_management_order": ("services", "tenant_id, sort_order, name"),
    "ix_campaigns_management": ("campaigns", "tenant_id, status, created_at"),
}
_HISTORY_PREFIX = ("CHECK (((event)::text = ANY ((ARRAY['hold_created'::character varying, "
    "'hold_expired'::character varying, 'confirmed'::character varying, "
    "'cancelled'::character varying, 'rescheduled'::character varying")
HISTORY_SOURCE = _HISTORY_PREFIX + "])::text[])))"
HISTORY_TARGET = _HISTORY_PREFIX + ", 'completed'::character varying, 'no_show'::character varying])::text[])))"
FULFILLMENT_CHECK = (
    "CHECK ((((fulfillment_mode IS NULL) AND (pickup_appointment_id IS NULL) AND (fulfillment_snapshot IS NULL)) "
    "OR ((fulfillment_mode IS NOT NULL) AND ((fulfillment_mode)::text = ANY ((ARRAY['pickup'::character varying, "
    "'delivery'::character varying])::text[])) AND (fulfillment_snapshot IS NOT NULL) "
    "AND (jsonb_typeof(fulfillment_snapshot) = 'object'::text) "
    "AND COALESCE(((fulfillment_snapshot ->> 'schema_version'::text) = '1'::text), false) "
    "AND COALESCE(((fulfillment_snapshot ->> 'mode'::text) = (fulfillment_mode)::text), false) "
    "AND ((pickup_appointment_id IS NULL) OR ((fulfillment_mode)::text = 'pickup'::text)))))"
)
FULFILLMENT_FK = (
    "FOREIGN KEY (tenant_id, pickup_appointment_id) REFERENCES appointments(tenant_id, id) ON DELETE RESTRICT"
)
FULFILLMENT_INDEX = (
    "CREATE INDEX ix_quote_pickup_appointment ON public.commercial_quotes USING btree "
    "(tenant_id, pickup_appointment_id) WHERE (pickup_appointment_id IS NOT NULL)"
)
ADDED_CHECKS = {
    ("customers", "ck_customer_management_version"): "CHECK ((version >= 1))",
    ("customers", "ck_customer_management_notes"): "CHECK (((notes IS NULL) OR (length(notes) <= 5000)))",
    ("customers", "ck_customer_management_birth"): "CHECK (((birth_date IS NULL) OR (birth_date >= '1900-01-01'::date)))",
    ("appointments", "ck_appointment_commission"): (
        "CHECK ((((commission_percent_snapshot IS NULL) AND (commission_cents IS NULL)) OR "
        "((commission_percent_snapshot IS NOT NULL) AND (commission_cents IS NOT NULL) AND "
        "((commission_percent_snapshot >= (0)::numeric) AND (commission_percent_snapshot <= (100)::numeric)) "
        "AND ((commission_cents >= 0) AND (commission_cents <= total_cents)))))"),
    ("appointments", "ck_appointment_discount"): (
        "CHECK (((discount_cents >= 0) AND (((original_total_cents IS NULL) AND (discount_cents = 0)) OR "
        "((original_total_cents IS NOT NULL) AND (original_total_cents = (total_cents + discount_cents))))))"),
    ("services", "ck_service_color"): "CHECK (((color)::text ~ '^#[0-9a-fA-F]{6}$'::text))",
    ("services", "ck_service_sort_order"): "CHECK (((sort_order >= 0) AND (sort_order <= 1000000)))",
    ("services", "ck_service_catalog_kind"): (
        "CHECK ((((catalog_kind)::text = ANY ((ARRAY['base'::character varying, 'combo'::character varying, "
        "'variant'::character varying])::text[])) AND (addons_version >= 0)))"),
    ("professionals", "ck_professional_color"): "CHECK (((color)::text ~ '^#[0-9a-fA-F]{6}$'::text))",
    ("professionals", "ck_professional_commission"): "CHECK (((commission_percent >= (0)::numeric) AND (commission_percent <= (100)::numeric)))",
    ("campaigns", "ck_campaign_management_values"): "CHECK (((version >= 1) AND ((message IS NULL) OR ((length(message) >= 1) AND (length(message) <= 5000)))))",
    ("campaigns", "ck_campaign_management_audience"): (
        "CHECK (((audience)::text = ANY ((ARRAY['all_active'::character varying, "
        "'inactive_customers'::character varying, 'birthday_month'::character varying, "
        "'new_customers'::character varying])::text[])))"),
}
FUNCTIONS = frozenset((
    ("package_changed", ""), ("package_source_changed", ""),
    ("refresh_service_package", "uuid, uuid"), ("resolve_published_site", "text"),
    ("snapshot_service_package_item", ""), ("validate_package_item", ""),
))
TRIGGERS = frozenset((
    ("appointment_items", "package_item_snapshot"), ("services", "package_service_changed"),
    ("professional_services", "package_professional_changed"), ("service_addons", "package_addons_changed"),
    ("service_packages", "package_changed"), ("service_package_items", "package_items_changed"),
    ("service_package_items", "validate_package_item"),
))
_READ_INSERT = frozenset((
    "management_requests", "loyalty_transactions", "management_block_series", "loyalty_coupons",
    "commission_settlements", "commission_advance_allocations",
))
_READ_WRITE_DELETE = frozenset((
    "service_packages", "service_package_items", "service_addons", "public_site_daily_metrics",
    "public_booking_receipts",
))


@dataclass(frozen=True, repr=False)
class Settings:
    url: URL
    bridge_url: str
    token: str
    run_id: str

    @classmethod
    def from_environment(cls):
        require(os.getenv("ORION_RUN_MANAGEMENT_MIGRATION") == "1", "explicit_flag_required")
        require(os.getenv("ORION_MIGRATION_SWITCH_ROLE") == "1", "role_switch_required")
        try:
            url = make_url(os.environ["MIGRATION_DATABASE_URL"])
            run_id = os.environ["ORION_MANAGEMENT_MIGRATION_RUN_ID"]
            require(str(UUID(run_id)) == run_id and UUID(run_id).version == 4, "canonical_run_id_required")
            bridge = os.getenv("QR_BRIDGE_URL", "").rstrip("/")
            parsed = urlsplit(bridge)
            private_bridge = (parsed.scheme == "http" and parsed.hostname is not None and
                re.fullmatch(r"[a-z0-9][a-z0-9-]*\.railway\.internal", parsed.hostname) is not None and
                parsed.port == 3000 and not (parsed.username or parsed.password or parsed.path or
                                            parsed.query or parsed.fragment))
        except Stop:
            raise
        except Exception:
            raise Stop("invalid_configuration") from None
        require(url.drivername in {"postgres", "postgresql", "postgresql+psycopg"}, "postgres_required")
        require(bool(url.host and url.database and url.username and url.password), "owner_url_required")
        require(url.host == os.getenv("ORION_EXPECTED_DATABASE_HOST") and
                url.database == os.getenv("ORION_EXPECTED_DATABASE_NAME"), "database_identity_required")
        require(set(url.query) <= {"sslmode", "sslrootcert"}, "unsupported_database_option")
        require(all(isinstance(value, str) for value in url.query.values()), "unsupported_database_option")
        require(private_bridge, "private_bridge_required")
        token = os.getenv("QR_BRIDGE_TOKEN", "")
        require(len(token.encode("utf-8")) >= 32, "backup_key_required")
        return cls(url.set(drivername="postgresql+psycopg"), bridge, token, run_id)


def reviewed_migrations():
    reviewed = []
    for revision, previous, expected_hash in MIGRATIONS:
        path = ROOT / "alembic" / "versions" / (revision + ".py")
        try:
            body = path.read_bytes().replace(b"\r\n", b"\n")
        except OSError:
            raise Stop("reviewed_migration_missing") from None
        require(hashlib.sha256(body).hexdigest() == expected_hash, "unreviewed_management_migration")
        reviewed.append((revision, previous, path, body))
    modules = []
    for revision, previous, path, body in reviewed:
        spec = importlib.util.spec_from_file_location("orion_reviewed_" + revision, path)
        require(spec is not None, "reviewed_migration_unloadable")
        module = importlib.util.module_from_spec(spec)
        # Execute the already checked bytes, avoiding a second, unchecked file read.
        exec(compile(body, str(path), "exec"), module.__dict__)
        require(module.revision == revision and module.down_revision == previous and
                module.branch_labels is None and module.depends_on is None, "unreviewed_migration_chain")
        modules.append(module)
    return modules


def set_timeouts(conn):
    backup.set_timeouts(conn)
    # JSON row hashes must not depend on per-database display settings.
    conn.exec_driver_sql("SET LOCAL timezone='UTC'")
    conn.exec_driver_sql("SET LOCAL datestyle='ISO, YMD'")
    conn.exec_driver_sql("SET LOCAL bytea_output='hex'")


def counts(conn, tables=SOURCE_TABLES):
    require(set(tables) <= TARGET_TABLES, "unapproved_table")
    return {name: conn.exec_driver_sql('SELECT count(*) FROM public."' + name + '"').scalar_one()
            for name in sorted(tables)}


def canonical_constraint(table, expression):
    # Keep the legacy normalizer unchanged; apply its exact, reviewed aliases.
    return backup.canonical_catalog([[table, False, False, [], [expression], [], []]])[0][4][0]


def named_constraints(conn, tables=SOURCE_TABLES):
    result = [row for row in fulfillment.named_constraints(conn) if row[0] in tables]
    for row in result:
        row[7] = canonical_constraint(row[0], row[7])
    return result


def programs(conn):
    functions = [list(row) for row in conn.exec_driver_sql("""
        SELECT n.nspname,p.proname,oidvectortypes(p.proargtypes),pg_get_userbyid(p.proowner),
          p.prosecdef,p.proconfig,p.proacl::text,pg_get_functiondef(p.oid)
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND NOT EXISTS
          (SELECT 1 FROM pg_depend d WHERE d.classid='pg_proc'::regclass AND d.objid=p.oid AND d.deptype='e')
        ORDER BY n.nspname,p.proname,oidvectortypes(p.proargtypes)
    """)]
    triggers = [list(row) for row in conn.exec_driver_sql("""
        SELECT n.nspname,c.relname,t.tgname,t.tgenabled,pg_get_triggerdef(t.oid)
        FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace WHERE NOT t.tgisinternal
        ORDER BY n.nspname,c.relname,t.tgname
    """)]
    return {"functions": functions, "triggers": triggers}


def security_structure(conn):
    value = fulfillment.security_structure(conn)
    value["indexes"] = [list(row) for row in conn.exec_driver_sql("""
        SELECT c.relname,t.relname,i.indisvalid,i.indisready,i.indisunique,i.indisprimary,i.indisexclusion
        FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid JOIN pg_class t ON t.oid=i.indrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' ORDER BY c.relname
    """)]
    return value


def without_database_identity(security):
    value = deepcopy(security)
    # Only the isolated database's name/CONNECT ACL differ intentionally. Schema,
    # relation and column owners/ACLs, default grants, roles and memberships stay.
    value.pop("database")
    return value


def source_only(conn):
    require(backup.revision(conn) == SOURCE and backup.table_names(conn) == SOURCE_TABLES,
            "unexpected_source_schema")
    catalog = {row[0]: row for row in backup.catalog_structure(conn, SOURCE_TABLES)}
    for table, additions in ADDED_COLUMNS.items():
        require(not {column[0] for column in additions}.intersection(column[0] for column in catalog[table][3]),
                "partial_management_schema")
    require(next(column for column in catalog["customers"][3] if column[0] == "whatsapp_e164") ==
            ["whatsapp_e164", "character varying(16)", True, None], "unexpected_customer_source")
    constraints = named_constraints(conn)
    history = [row for row in constraints if row[:2] == ["appointment_history", "ck_history_event"]]
    require(history == [["appointment_history", "ck_history_event", "c", True, False, False, False,
                         canonical_constraint("appointment_history", HISTORY_SOURCE)]], "unexpected_history_source")
    require(not any(tuple(row[:2]) in ADDED_CHECKS for row in constraints), "partial_management_schema")
    quote_columns = [column for column in catalog["commercial_quotes"][3] if column[0] in fulfillment.COLUMNS]
    require(quote_columns == [["fulfillment_mode", "character varying(16)", False, None],
                             ["pickup_appointment_id", "uuid", False, None],
                             ["fulfillment_snapshot", "jsonb", False, None]], "unexpected_fulfillment_source")
    found = {tuple(row[:2]): row for row in constraints}
    for name, kind, no_inherit, definition in (
        ("ck_quote_fulfillment", "c", False, FULFILLMENT_CHECK),
        ("fk_quote_pickup_appointment", "f", True, FULFILLMENT_FK),
    ):
        require(found.get(("commercial_quotes", name)) == ["commercial_quotes", name, kind, True, False,
                False, no_inherit, canonical_constraint("commercial_quotes", definition)],
                "unexpected_fulfillment_source")
    require(catalog["commercial_quotes"][5].count(FULFILLMENT_INDEX) == 1, "unexpected_fulfillment_source")
    index = conn.exec_driver_sql("""SELECT i.indisvalid,i.indisready,pg_get_userbyid(c.relowner),c.relacl,c.reloptions
        FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relname='ix_quote_pickup_appointment'""").one()
    require(list(index) == [True, True, "app_migrator", None, None], "unexpected_fulfillment_source")
    require(not conn.execute(text("""SELECT EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n
        ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relname=ANY(:names))"""),
        {"names": sorted(ADDED_INDEXES)}).scalar_one(), "partial_management_schema")


def owner_preflight(conn, settings):
    set_timeouts(conn)
    row = conn.execute(text("""SELECT current_database() AS name,d.oid,d.datdba,r.oid AS role_oid,
        r.rolsuper,r.rolbypassrls,r.rolcreatedb,current_setting('server_version_num')::int/10000 AS major
        FROM pg_database d JOIN pg_roles r ON r.rolname=current_user WHERE d.datname=current_database()""")).mappings().one()
    require(row["name"] == settings.url.database and row["datdba"] == row["role_oid"], "database_owner_required")
    require((row["rolsuper"] or row["rolbypassrls"]) and (row["rolsuper"] or row["rolcreatedb"]),
            "owner_backup_privileges_required")
    require(row["major"] == 16, "postgres_major_mismatch")
    roles = conn.exec_driver_sql("SELECT rolname,rolsuper,rolbypassrls FROM pg_roles WHERE rolname IN ('app_runtime','app_support','app_migrator')").all()
    require(len(roles) == 3 and all(not item.rolsuper and not item.rolbypassrls for item in roles),
            "unsafe_application_roles")
    require(set(conn.exec_driver_sql("SELECT extname FROM pg_extension").scalars()) == {"plpgsql", "btree_gist"},
            "unexpected_extensions")
    require(conn.exec_driver_sql("SELECT count(*) FROM pg_largeobject_metadata").scalar_one() == 0,
            "unreviewed_large_objects")
    require(conn.exec_driver_sql("SELECT count(*) FROM pg_event_trigger").scalar_one() == 0,
            "unreviewed_event_triggers")
    require(conn.execute(text("""SELECT count(*) FROM pg_namespace
        WHERE nspname NOT IN ('public','pg_catalog','information_schema')
        AND nspname NOT LIKE :toast AND nspname NOT LIKE :temporary"""),
        {"toast": "pg_toast%", "temporary": "pg_temp_%"}).scalar_one() == 0, "unreviewed_database_schema")
    require(conn.exec_driver_sql("""SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relkind NOT IN ('r','i')""").scalar_one() == 0, "unreviewed_database_relations")
    require(programs(conn) == {"functions": [], "triggers": []}, "unreviewed_database_programs")
    source_only(conn)
    conn.exec_driver_sql("SET LOCAL ROLE app_migrator")
    require(conn.exec_driver_sql("SELECT current_user").scalar_one() == "app_migrator", "role_switch_failed")
    conn.exec_driver_sql("RESET ROLE")
    return dict(row)


def row_fingerprints(conn, *, projected=False):
    """Stream only SHA256 values; neither rows nor personal data leave PostgreSQL."""
    result = {}
    for table in sorted(SOURCE_TABLES):
        value = "to_jsonb(t)"
        if projected and table in ADDED_COLUMNS:
            # These identifiers are constants above, never database/user input.
            value += " - ARRAY[" + ",".join("'" + column[0] + "'" for column in ADDED_COLUMNS[table]) + "]::text[]"
        query = ('SELECT encode(sha256(convert_to((' + value + ")::text,'UTF8')),'hex') "
                 'FROM public."' + table + '" t ORDER BY 1')
        digest = hashlib.sha256()
        with conn.execute(text(query).execution_options(stream_results=True)) as rows:
            for row in rows:
                digest.update(row[0].encode("ascii") + b"\n")
        result[table] = digest.hexdigest()
    return result


def source_snapshot(conn):
    return {"revision": backup.revision(conn), "counts": counts(conn), "rows": row_fingerprints(conn),
            "catalog": backup.canonical_catalog(backup.catalog_structure(conn, SOURCE_TABLES)),
            "constraints": named_constraints(conn), "security": security_structure(conn), "programs": programs(conn)}


def capture_dump(engine, settings, path):
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as conn:
        with conn.begin():
            conn.exec_driver_sql("SET TRANSACTION READ ONLY")
            owner_preflight(conn, settings)
            conn.exec_driver_sql("LOCK TABLE " + ",".join('public."' + name + '"' for name in sorted(SOURCE_TABLES)) + " IN ACCESS SHARE MODE")
            snapshot_id = conn.exec_driver_sql("SELECT pg_export_snapshot()").scalar_one()
            expected = source_snapshot(conn)
            backup.private_write(path, b"")
            backup.run_client("pg_dump", ["--format=custom", "--no-password", "--snapshot=" + snapshot_id,
                "--lock-wait-timeout=10000", "--file=" + str(path)], settings.url, max_file=path)
            require(5 <= path.stat().st_size <= backup.MAX_BYTES - 36, "dump_size_invalid")
            return expected


def projected_catalog(conn):
    """Exclude only validated, byte-reviewed changes from old-schema comparison."""
    rows = backup.canonical_catalog(backup.catalog_structure(conn, SOURCE_TABLES))
    by_table = {row[0]: row for row in rows}
    for table, expected in ADDED_COLUMNS.items():
        names = {column[0] for column in expected}
        require([column for column in by_table[table][3] if column[0] in names] == expected,
                "management_columns_mismatch")
        by_table[table][3] = [column for column in by_table[table][3] if column[0] not in names]
    phone = next(column for column in by_table["customers"][3] if column[0] == "whatsapp_e164")
    require(phone == ["whatsapp_e164", "character varying(16)", False, None], "management_phone_mismatch")
    phone[2] = True
    constraints = named_constraints(conn)
    found = {tuple(row[:2]): row for row in constraints}
    for key, definition in ADDED_CHECKS.items():
        expected = [*key, "c", True, False, False, False, canonical_constraint(key[0], definition)]
        require(found.get(key) == expected, "management_constraints_mismatch")
        definitions = by_table[key[0]][4]
        require(definitions.count(expected[7]) == 1, "management_constraint_identity_mismatch")
        definitions.remove(expected[7])
    history = found.get(("appointment_history", "ck_history_event"))
    target_history = canonical_constraint("appointment_history", HISTORY_TARGET)
    require(history == ["appointment_history", "ck_history_event", "c", True, False, False, False, target_history],
            "management_history_mismatch")
    definitions = by_table["appointment_history"][4]
    require(definitions.count(target_history) == 1, "management_constraint_identity_mismatch")
    definitions[definitions.index(target_history)] = canonical_constraint("appointment_history", HISTORY_SOURCE)
    history[7] = canonical_constraint("appointment_history", HISTORY_SOURCE)
    constraints = [row for row in constraints if tuple(row[:2]) not in ADDED_CHECKS]
    for name, (table, columns) in ADDED_INDEXES.items():
        definition = "CREATE INDEX " + name + " ON public." + table + " USING btree (" + columns + ")"
        require(by_table[table][5].count(definition) == 1, "management_index_mismatch")
        by_table[table][5].remove(definition)
        index = conn.execute(text("""SELECT i.indisvalid,i.indisready,pg_get_userbyid(c.relowner),c.relacl,c.reloptions
            FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND c.relname=:name"""), {"name": name}).one()
        require(list(index) == [True, True, "app_migrator", None, None], "management_index_security_mismatch")
    return rows, constraints


def projected_security(after, before):
    value = deepcopy(after)
    relations = {row[0] for row in before["relations"]}
    columns = {(row[0], row[1]) for row in before["columns"]}
    indexes = {row[0] for row in before["indexes"]}
    value["relations"] = [row for row in value["relations"] if row[0] in relations]
    value["columns"] = [row for row in value["columns"] if (row[0], row[1]) in columns]
    value["indexes"] = [row for row in value["indexes"] if row[0] in indexes]
    return value


def verify_added_defaults(conn):
    # No UPDATE/backfill of old values is part of these five upgrades. New
    # columns must have precisely their reviewed NULL/default on every old row.
    for table, columns in ADDED_COLUMNS.items():
        conditions = [
            '"' + column[0] + '" IS NOT NULL' if column[3] is None else
            '"' + column[0] + '" IS DISTINCT FROM (' + column[3] + ')'
            for column in columns
        ]
        require(not conn.exec_driver_sql('SELECT EXISTS(SELECT 1 FROM public."' + table + '" WHERE ' +
                                        " OR ".join(conditions) + ")").scalar_one(), "unexpected_management_backfill")
        require(conn.execute(text("""SELECT count(*) FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relname=:table
            AND a.attname=ANY(:columns) AND a.attacl IS NOT NULL"""),
            {"table": table, "columns": [column[0] for column in columns]}).scalar_one() == 0,
            "management_column_grants_changed")


def verify_new_schema(conn):
    require(backup.table_names(conn) == TARGET_TABLES, "target_table_mismatch")
    require(not any(counts(conn, NEW_TABLES).values()), "unexpected_management_seed_rows")
    for name in sorted(NEW_TABLES):
        row = conn.execute(text("""SELECT c.relrowsecurity,c.relforcerowsecurity,pg_get_userbyid(c.relowner),
            has_table_privilege('app_runtime',c.oid,'SELECT'),has_table_privilege('app_runtime',c.oid,'INSERT'),
            has_table_privilege('app_runtime',c.oid,'UPDATE'),has_table_privilege('app_runtime',c.oid,'DELETE'),
            has_table_privilege('app_support',c.oid,'SELECT'),has_table_privilege('app_support',c.oid,'INSERT'),
            has_table_privilege('app_support',c.oid,'UPDATE'),has_table_privilege('app_support',c.oid,'DELETE'),
            EXISTS(SELECT 1 FROM aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) a WHERE a.grantee=0)
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND c.relname=:name"""), {"name": name}).one()
        runtime = [True, True, name not in _READ_INSERT, name in _READ_WRITE_DELETE]
        support = [False] * 4
        if name == "management_team_invitations":
            runtime, support = [False] * 4, [True, True, True, False]
        require(list(row) == [True, True, "app_migrator", *runtime, *support, False],
                "management_table_security_mismatch")
    value = programs(conn)
    require({(row[1], row[2]) for row in value["functions"]} == FUNCTIONS and
            len(value["functions"]) == len(FUNCTIONS), "management_functions_mismatch")
    for row in value["functions"]:
        require(row[0] == "public" and row[3] == "app_migrator" and
                row[4] == (row[1] == "resolve_published_site") and
                row[5] == ["search_path=pg_catalog, public"], "management_function_security_mismatch")
    require({(row[1], row[2]) for row in value["triggers"]} == TRIGGERS and
            len(value["triggers"]) == len(TRIGGERS) and
            all(row[0] == "public" and row[3] == "O" for row in value["triggers"]), "management_triggers_mismatch")
    permissions = conn.exec_driver_sql("""SELECT p.proname,
        has_function_privilege('app_runtime',p.oid,'EXECUTE'),has_function_privilege('app_support',p.oid,'EXECUTE'),
        EXISTS(SELECT 1 FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a WHERE a.grantee=0)
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public'
        AND NOT EXISTS(SELECT 1 FROM pg_depend d WHERE d.classid='pg_proc'::regclass AND d.objid=p.oid AND d.deptype='e')""").all()
    require(all(list(row[1:]) == [row[0] in {"resolve_published_site", "refresh_service_package"}, False, False]
                for row in permissions), "management_function_grants_mismatch")
    require(conn.exec_driver_sql("SELECT count(*) FROM pg_event_trigger").scalar_one() == 0,
            "unreviewed_event_triggers")


def target_manifest(conn):
    return {"revision": backup.revision(conn),
            "catalog": backup.canonical_catalog(backup.catalog_structure(conn, TARGET_TABLES)),
            "constraints": named_constraints(conn, TARGET_TABLES),
            "security": without_database_identity(security_structure(conn)), "programs": programs(conn)}


def apply_exact_migrations(engine, settings, expected_snapshot, expected_target=None):
    modules = reviewed_migrations()
    require(expected_target is not None or settings.url.database == backup.restore_name(settings.run_id),
            "rehearsal_proof_required")
    # READ COMMITTED plus table locks makes the comparison see every write that
    # committed before the locks, rather than an earlier repeatable-read snapshot.
    with engine.connect().execution_options(isolation_level="READ COMMITTED") as conn:
        with conn.begin():
            owner_preflight(conn, settings)
            require(conn.exec_driver_sql("SELECT pg_try_advisory_xact_lock(681692820104)").scalar_one(),
                    "migration_already_running")
            conn.exec_driver_sql("LOCK TABLE " + ",".join('public."' + name + '"' for name in sorted(SOURCE_TABLES)) + " IN SHARE MODE")
            conn.exec_driver_sql("SELECT version_num FROM public.alembic_version FOR UPDATE")
            source_only(conn)
            before = source_snapshot(conn)
            require(before == expected_snapshot, "source_changed_after_backup")
            conn.exec_driver_sql("SET LOCAL ROLE app_migrator")
            require(conn.exec_driver_sql("SELECT current_user").scalar_one() == "app_migrator", "role_switch_failed")
            with Operations.context(MigrationContext.configure(conn)):
                for module in modules:
                    module.upgrade()
            conn.exec_driver_sql("RESET ROLE")
            projected, constraints = projected_catalog(conn)
            require(projected == before["catalog"] and constraints == before["constraints"], "existing_catalog_changed")
            require(projected_security(security_structure(conn), before["security"]) == before["security"],
                    "existing_security_changed")
            require(counts(conn) == before["counts"] and row_fingerprints(conn, projected=True) == before["rows"],
                    "existing_data_changed")
            verify_added_defaults(conn)
            verify_new_schema(conn)
            conn.exec_driver_sql("SET LOCAL ROLE app_migrator")
            result = conn.execute(text("UPDATE public.alembic_version SET version_num=:target WHERE version_num=:source"),
                                  {"target": TARGET, "source": SOURCE})
            require(result.rowcount == 1 and backup.revision(conn) == TARGET, "revision_update_failed")
            conn.exec_driver_sql("RESET ROLE")
            manifest = target_manifest(conn)
            if expected_target is not None:
                require(manifest == expected_target, "rehearsed_target_mismatch")
            return manifest


def verify_restore(engine, settings, source_identity, dump_path, expected_snapshot):
    name = backup.restore_name(settings.run_id)
    require(name != settings.url.database, "restore_source_collision")
    created_oid = None
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as admin:
        try:
            require(not admin.execute(text("SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname=:name)"),
                                      {"name": name}).scalar_one(), "restore_database_already_exists")
            admin.exec_driver_sql('CREATE DATABASE "' + name + '" TEMPLATE template0 ALLOW_CONNECTIONS false')
            created_oid = admin.execute(text("SELECT oid FROM pg_database WHERE datname=:name"), {"name": name}).scalar_one()
            admin.exec_driver_sql('REVOKE CONNECT ON DATABASE "' + name + '" FROM PUBLIC,app_runtime,app_support,app_migrator')
            admin.exec_driver_sql('ALTER DATABASE "' + name + '" ALLOW_CONNECTIONS true')
            temp_settings = replace(settings, url=settings.url.set(database=name))
            # Keep object owners and ACLs. No --create: the archive may not change
            # the source database or overwrite the isolated database CONNECT gate.
            backup.run_client("pg_restore", ["--no-password", "--exit-on-error", "--single-transaction",
                "--dbname=" + name, str(dump_path)], temp_settings.url)
            restored = backup.engine_for(temp_settings.url)
            try:
                with restored.begin() as check:
                    identity = owner_preflight(check, temp_settings)
                    require(identity["oid"] == created_oid and identity["role_oid"] == source_identity["role_oid"],
                            "restore_database_identity_changed")
                    require(not check.execute(text("""SELECT EXISTS(SELECT 1 FROM pg_roles
                        WHERE rolname IN ('app_runtime','app_support','app_migrator')
                        AND has_database_privilege(rolname,:name,'CONNECT'))"""),
                        {"name": name}).scalar_one(), "restore_connect_gate_changed")
                    actual = source_snapshot(check)
                    require(actual["counts"] == expected_snapshot["counts"], "restored_count_mismatch")
                    for section in ("revision", "rows", "catalog", "constraints", "programs"):
                        require(actual[section] == expected_snapshot[section], "restored_" + section + "_mismatch")
                    require(without_database_identity(actual["security"]) ==
                            without_database_identity(expected_snapshot["security"]), "restored_security_mismatch")
                return apply_exact_migrations(restored, temp_settings, actual)
            finally:
                restored.dispose()
        finally:
            if created_oid is not None:
                backup.safe_drop_created(admin, name, created_oid, source_identity["role_oid"], source_identity["oid"])


def execute(settings):
    reviewed_migrations()  # Refuse every unreviewed byte before engine/backup I/O.
    engine = backup.engine_for(settings.url)
    try:
        with engine.begin() as conn:
            identity = owner_preflight(conn, settings)
        require(backup.client_major("pg_dump") == backup.client_major("pg_restore") == identity["major"] == 16,
                "postgres_major_mismatch")
        with tempfile.TemporaryDirectory(prefix="orion-management-migration-") as directory:
            os.chmod(directory, 0o700)
            require(shutil.disk_usage(directory).free > backup.MAX_BYTES * 3 + 16 * 1024 * 1024,
                    "temporary_disk_space_low")
            original = Path(directory) / "snapshot.dump"
            expected = capture_dump(engine, settings, original)
            envelope = backup.encrypt_dump(original.read_bytes(), settings.token, settings.run_id)
            with httpx.Client(timeout=httpx.Timeout(60, connect=10), follow_redirects=False, trust_env=False) as client:
                restored_bytes, digest = backup.durable_backup(settings, envelope, client)
            readback = Path(directory) / "verified.dump"
            backup.private_write(readback, restored_bytes)
            rehearsed = verify_restore(engine, settings, identity, readback, expected)
            apply_exact_migrations(engine, settings, expected, rehearsed)
        return {"status": "complete", "revision": TARGET, "backup_id": settings.run_id,
                "backup_sha256": digest, "backup_bytes": len(envelope), "backup_counts": expected["counts"],
                "preserved_counts": expected["counts"], "restore_verified": True, "rehearsal_verified": True}
    finally:
        engine.dispose()


def main():
    try:
        result = execute(Settings.from_environment())
    except Stop as error:
        code = str(error)
        if re.fullmatch(r"[a-z][a-z0-9_]{0,79}", code) is None:
            code = "operation_failed_no_details"
        print(json.dumps({"status": "stopped", "code": code}))
        return 1
    except Exception:
        print(json.dumps({"status": "stopped", "code": "operation_failed_no_details"}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
