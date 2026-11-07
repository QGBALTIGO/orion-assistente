"""Read-only catalog barrier for private, explicitly confirmed campaign work.

The specification is independent of the migration: a missing/changed guard or
an expanded grant fails readiness without reading recipients or other rows.
"""
import asyncio
import json

import asyncpg

from scripts import wait_for_agenda_schema as previous
from scripts.wait_for_agenda_schema import Settings, Stop, SAFE_ERRORS
from scripts.wait_for_subscription_diagnostics_schema import CONTRACT_QUERY as PREVIOUS_CONTRACT

SCHEMA_MARKER = "0068_subscription_campaigns"

SUPPORT_UPDATES = {
    "subscription_campaign_groups": ("name", "version", "updated_at", "archived_at"),
    "subscription_campaign_members": ("name", "updated_at", "removed_at"),
    "subscription_campaign_permissions": ("version", "revoked", "source", "actor_account_id",
        "event_id", "evidence_hash", "updated_at"),
    "subscription_campaigns": ("owner_account_id", "title", "version", "status", "definition", "start_at",
        "template_id", "template_revision", "run_version", "snapshot", "confirmed_at", "first_admitted_at",
        "last_admitted_at", "next_admission_at", "archived_at", "updated_at"),
    "subscription_campaign_sources": (),
    "subscription_campaign_recipients": ("status", "admitted_at", "finished_at", "error_code"),
    "subscription_campaign_requests": ("status", "result", "confirmed_at"),
}
PRIVATE_TABLES = tuple(SUPPORT_UPDATES)

# name, PostgreSQL type, NOT NULL, typmod (varchar/char width includes four bytes).
COLUMNS = {
    "subscription_campaign_groups": (
        ("id", "uuid", True, -1), ("tenant_id", "uuid", True, -1),
        ("name", "varchar", True, 104), ("version", "int8", True, -1),
        ("created_at", "timestamptz", True, -1), ("updated_at", "timestamptz", True, -1),
        ("archived_at", "timestamptz", False, -1)),
    "subscription_campaign_members": (
        ("tenant_id", "uuid", True, -1), ("group_id", "uuid", True, -1),
        ("phone", "varchar", True, 19), ("name", "varchar", True, 104),
        ("created_at", "timestamptz", True, -1), ("updated_at", "timestamptz", True, -1),
        ("removed_at", "timestamptz", False, -1)),
    "subscription_campaign_permissions": (
        ("tenant_id", "uuid", True, -1), ("phone", "varchar", True, 19),
        ("version", "int8", True, -1), ("revoked", "bool", True, -1),
        ("source", "varchar", True, 28), ("actor_account_id", "uuid", False, -1),
        ("event_id", "uuid", False, -1), ("evidence_hash", "bpchar", False, 68),
        ("updated_at", "timestamptz", True, -1)),
    "subscription_campaigns": (
        ("id", "uuid", True, -1), ("tenant_id", "uuid", True, -1),
        ("owner_account_id", "uuid", True, -1), ("title", "varchar", True, 124),
        ("version", "int8", True, -1), ("status", "varchar", True, 20),
        ("definition", "jsonb", True, -1), ("start_at", "timestamptz", True, -1),
        ("template_id", "uuid", False, -1), ("template_revision", "int8", False, -1),
        ("run_version", "bpchar", False, 68), ("snapshot", "jsonb", False, -1),
        ("confirmed_at", "timestamptz", False, -1), ("first_admitted_at", "timestamptz", False, -1),
        ("last_admitted_at", "timestamptz", False, -1), ("next_admission_at", "timestamptz", False, -1),
        ("archived_at", "timestamptz", False, -1), ("created_at", "timestamptz", True, -1),
        ("updated_at", "timestamptz", True, -1)),
    "subscription_campaign_sources": (
        ("tenant_id", "uuid", True, -1), ("campaign_id", "uuid", True, -1),
        ("run_version", "bpchar", True, 68), ("group_id", "uuid", True, -1),
        ("group_version", "int8", True, -1), ("name", "varchar", True, 104),
        ("member_count", "int4", True, -1)),
    "subscription_campaign_recipients": (
        ("id", "uuid", True, -1), ("tenant_id", "uuid", True, -1),
        ("campaign_id", "uuid", True, -1), ("run_version", "bpchar", True, 68),
        ("ordinal", "int4", True, -1), ("phone", "varchar", True, 19),
        ("name", "varchar", True, 104), ("source_group_ids", "_uuid", True, -1),
        ("device_id", "uuid", True, -1), ("generation", "uuid", True, -1),
        ("settings_revision", "int8", True, -1), ("permission_version", "int8", True, -1),
        ("plan", "jsonb", True, -1), ("status", "varchar", True, 20),
        ("admitted_at", "timestamptz", False, -1), ("finished_at", "timestamptz", False, -1),
        ("error_code", "varchar", False, 84), ("created_at", "timestamptz", True, -1)),
    "subscription_campaign_requests": (
        ("id", "uuid", True, -1), ("tenant_id", "uuid", True, -1),
        ("actor_account_id", "uuid", True, -1), ("idempotency_key", "uuid", True, -1),
        ("kind", "varchar", True, 28), ("request_fingerprint", "bpchar", True, 68),
        ("campaign_id", "uuid", False, -1), ("group_id", "uuid", False, -1),
        ("status", "varchar", True, 20), ("review_version", "bpchar", False, 68),
        ("snapshot", "jsonb", False, -1), ("preview", "jsonb", False, -1),
        ("result", "jsonb", False, -1), ("created_at", "timestamptz", True, -1),
        ("expires_at", "timestamptz", False, -1), ("confirmed_at", "timestamptz", False, -1)),
    "subscription_device_effects": (
        ("campaign_id", "uuid", False, -1), ("campaign_recipient_id", "uuid", False, -1),
        ("campaign_part_index", "int2", False, -1)),
}

KEYS = (
    ("subscription_campaign_groups", "subscription_campaign_groups_pkey", "p", ("id",)),
    ("subscription_campaign_groups", "uq_subscription_campaign_group_scope", "u", ("tenant_id", "id")),
    ("subscription_campaign_members", "subscription_campaign_members_pkey", "p", ("tenant_id", "group_id", "phone")),
    ("subscription_campaign_permissions", "subscription_campaign_permissions_pkey", "p", ("tenant_id", "phone")),
    ("subscription_campaigns", "subscription_campaigns_pkey", "p", ("id",)),
    ("subscription_campaigns", "uq_subscription_campaign_scope", "u", ("tenant_id", "id")),
    ("subscription_campaign_sources", "subscription_campaign_sources_pkey", "p", ("tenant_id", "campaign_id", "run_version", "group_id")),
    ("subscription_campaign_recipients", "subscription_campaign_recipients_pkey", "p", ("id",)),
    ("subscription_campaign_recipients", "uq_subscription_campaign_recipient_scope", "u", ("tenant_id", "campaign_id", "id")),
    ("subscription_campaign_recipients", "uq_subscription_campaign_recipient_id", "u", ("tenant_id", "id")),
    ("subscription_campaign_recipients", "uq_subscription_campaign_recipient_phone", "u", ("tenant_id", "campaign_id", "run_version", "phone")),
    ("subscription_campaign_recipients", "uq_subscription_campaign_recipient_order", "u", ("tenant_id", "campaign_id", "run_version", "ordinal")),
    ("subscription_campaign_requests", "subscription_campaign_requests_pkey", "p", ("id",)),
    ("subscription_campaign_requests", "uq_subscription_campaign_request_scope", "u", ("tenant_id", "id")),
    ("subscription_campaign_requests", "uq_subscription_campaign_request_key", "u", ("tenant_id", "idempotency_key")),
    ("subscription_device_effects", "uq_subscription_device_effect_campaign_part", "u", ("tenant_id", "campaign_recipient_id", "campaign_part_index")),
)

FOREIGN_KEYS = (
    ("subscription_campaign_groups", "subscription_campaign_groups_tenant_id_fkey", ("tenant_id",), "tenants", ("id",)),
    ("subscription_campaign_members", "fk_subscription_campaign_member_group", ("tenant_id", "group_id"), "subscription_campaign_groups", ("tenant_id", "id")),
    ("subscription_campaign_permissions", "subscription_campaign_permissions_tenant_id_fkey", ("tenant_id",), "tenants", ("id",)),
    ("subscription_campaign_permissions", "fk_subscription_campaign_permission_actor", ("tenant_id", "actor_account_id"), "tenant_memberships", ("tenant_id", "account_id")),
    ("subscription_campaign_permissions", "fk_subscription_campaign_permission_event", ("tenant_id", "event_id"), "subscription_device_events", ("tenant_id", "id")),
    ("subscription_campaigns", "subscription_campaigns_tenant_id_fkey", ("tenant_id",), "tenants", ("id",)),
    ("subscription_campaigns", "fk_subscription_campaign_owner", ("tenant_id", "owner_account_id"), "tenant_memberships", ("tenant_id", "account_id")),
    ("subscription_campaigns", "fk_subscription_campaign_template", ("tenant_id", "template_id"), "subscription_chatbot_templates", ("tenant_id", "id")),
    ("subscription_campaign_sources", "fk_subscription_campaign_source_campaign", ("tenant_id", "campaign_id"), "subscription_campaigns", ("tenant_id", "id")),
    ("subscription_campaign_sources", "fk_subscription_campaign_source_group", ("tenant_id", "group_id"), "subscription_campaign_groups", ("tenant_id", "id")),
    ("subscription_campaign_recipients", "fk_subscription_campaign_recipient_campaign", ("tenant_id", "campaign_id"), "subscription_campaigns", ("tenant_id", "id")),
    ("subscription_campaign_recipients", "fk_subscription_campaign_recipient_device", ("tenant_id", "device_id", "generation"), "subscription_devices", ("tenant_id", "id", "generation")),
    ("subscription_campaign_requests", "subscription_campaign_requests_tenant_id_fkey", ("tenant_id",), "tenants", ("id",)),
    ("subscription_campaign_requests", "fk_subscription_campaign_request_actor", ("tenant_id", "actor_account_id"), "tenant_memberships", ("tenant_id", "account_id")),
    ("subscription_campaign_requests", "fk_subscription_campaign_request_campaign", ("tenant_id", "campaign_id"), "subscription_campaigns", ("tenant_id", "id")),
    ("subscription_campaign_requests", "fk_subscription_campaign_request_group", ("tenant_id", "group_id"), "subscription_campaign_groups", ("tenant_id", "id")),
    ("subscription_device_effects", "fk_subscription_device_effect_campaign", ("tenant_id", "campaign_id"), "subscription_campaigns", ("tenant_id", "id")),
    ("subscription_device_effects", "fk_subscription_device_effect_campaign_recipient", ("tenant_id", "campaign_id", "campaign_recipient_id"), "subscription_campaign_recipients", ("tenant_id", "campaign_id", "id")),
)

# Required semantic fragments, beyond a constraint merely retaining its name.
CHECKS = (
    ("subscription_campaign_groups", "subscription_campaign_group_values", ("version > 0", "length", "name")),
    ("subscription_campaign_members", "subscription_campaign_member_values", ("^[1-9][0-9]{7,14}$", "length", "name")),
    ("subscription_campaign_permissions", "subscription_campaign_permission_values", (
        "^[1-9][0-9]{7,14}$", "version > 0", "'optout'", "revoked", "event_id is not null",
        "actor_account_id is null", "evidence_hash is null", "'owner_permission'", "not revoked",
        "actor_account_id is not null", "event_id is null", "evidence_hash is not null", "^[a-f0-9]{64}$")),
    ("subscription_campaigns", "subscription_campaign_values", ("version > 0", "length", "title",
        "'draft'", "'scheduled'", "'running'", "'paused'", "'completed'", "'cancelled'",
        "jsonb_typeof(definition)", "'object'", "32768", "template_revision > 0")),
    ("subscription_campaigns", "subscription_campaign_snapshot", ("run_version is null", "snapshot is null",
        "confirmed_at is null", "run_version is not null", "^[a-f0-9]{64}$", "snapshot is not null",
        "jsonb_typeof(snapshot)", "1048576", "confirmed_at is not null")),
    ("subscription_campaigns", "subscription_campaign_execution", ("'scheduled'", "'running'", "'paused'",
        "'completed'", "run_version is not null", "first_admitted_at is null", "last_admitted_at is not null",
        "archived_at is null", "'cancelled'")),
    ("subscription_campaign_sources", "subscription_campaign_source_values", ("^[a-f0-9]{64}$",
        "group_version > 0", "member_count >= 0", "member_count <= 5000", "length", "name")),
    ("subscription_campaign_recipients", "subscription_campaign_recipient_values", (
        "^[a-f0-9]{64}$", "ordinal >= 1", "ordinal <= 10000", "^[1-9][0-9]{7,14}$", "length", "name",
        "cardinality(source_group_ids)", "array_position(source_group_ids, null", "settings_revision > 0",
        "permission_version >= 0", "jsonb_typeof(plan)", "'array'", "jsonb_array_length(plan)", "131072",
        "'pending'", "'running'", "'sent'", "'failed'", "'uncertain'", "'cancelled'")),
    ("subscription_campaign_recipients", "subscription_campaign_recipient_terminal", (
        "'pending'", "'running'", "finished_at is null", "'sent'", "'failed'", "'uncertain'",
        "'cancelled'", "finished_at is not null")),
    ("subscription_campaign_recipients", "subscription_campaign_recipient_admission", (
        "'running'", "'sent'", "'uncertain'", "admitted_at is not null")),
    ("subscription_campaign_requests", "subscription_campaign_request_values", (
        "'create'", "'update'", "'review'", "'action'", "'group_create'", "'group_update'",
        "'group_archive'", "'group_permissions'", "'prepared'", "'succeeded'", "^[a-f0-9]{64}$",
        "campaign_id is not null", "group_id is null", "group_id is not null", "campaign_id is null",
        "jsonb_typeof(result)", "4194304")),
    ("subscription_campaign_requests", "subscription_campaign_request_review", (
        "'review'", "review_version is not null", "^[a-f0-9]{64}$", "jsonb_typeof(snapshot)", "67108864",
        "jsonb_typeof(preview)", "1048576", "expires_at is not null", "expires_at > created_at",
        "expires_at <=", "00:02:00", "'prepared'", "result is null", "confirmed_at is null",
        "'succeeded'", "result is not null", "confirmed_at is not null", "snapshot is null",
        "preview is null", "review_version is null", "expires_at is null")),
    ("subscription_device_events", "subscription_device_events_kind_check", ("'diagnostic'", "'campaign'")),
    ("subscription_device_effects", "subscription_device_effects_type_check", ("'chatbot_action'", "'campaign_action'")),
    ("subscription_device_effects", "subscription_device_effect_campaign_scope", (
        "'campaign_action'", "purpose", "campaign_id is not null", "campaign_recipient_id is not null",
        "campaign_part_index is not null", "campaign_part_index >= 0", "campaign_part_index <= 2",
        "campaign_id is null", "campaign_recipient_id is null", "campaign_part_index is null")),
)

INDEXES = (
    ("subscription_campaign_groups", "ix_subscription_campaign_groups_active", ("tenant_id", "created_at", "id"), ("archived_at is null",)),
    ("subscription_campaign_members", "ix_subscription_campaign_member_phone", ("tenant_id", "phone", "group_id"), ("removed_at is null",)),
    ("subscription_campaigns", "ix_subscription_campaign_list", ("tenant_id", "created_at", "id"), ()),
    ("subscription_campaigns", "ix_subscription_campaign_due", ("tenant_id", "next_admission_at", "id"), ("'scheduled'", "'running'", "archived_at is null")),
    ("subscription_campaign_recipients", "ix_subscription_campaign_recipient_pending", ("tenant_id", "campaign_id", "run_version", "device_id", "ordinal"), ("'pending'",)),
    ("subscription_campaign_recipients", "ix_subscription_campaign_recipient_rate", ("tenant_id", "admitted_at"), ("admitted_at is not null",)),
    ("subscription_campaign_recipients", "ix_subscription_campaign_recipient_contact", ("tenant_id", "phone", "status"), ()),
    ("subscription_campaign_requests", "ix_subscription_campaign_request_actor", ("tenant_id", "actor_account_id", "created_at", "id"), ()),
)


def _literal(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, (tuple, list)):
        return "ARRAY[" + ",".join(_literal(v) for v in value) + "]::text[]"
    return "'" + value.replace("'", "''") + "'"


def _values(rows):
    return ",\n".join("(" + ",".join(_literal(value) for value in row) + ")" for row in rows)


TABLE_CONTRACT = """
 SELECT NOT EXISTS (
   SELECT 1 FROM (VALUES """ + _values(SUPPORT_UPDATES.items()) + """) expected(name,updates)
   WHERE NOT EXISTS (
     SELECT 1 FROM pg_catalog.pg_class c
     WHERE c.oid=pg_catalog.to_regclass('public.'||expected.name)
       AND c.relkind='r' AND c.relrowsecurity AND c.relforcerowsecurity
       AND pg_catalog.pg_get_userbyid(c.relowner)='app_migrator'
       AND pg_catalog.has_table_privilege('app_support',c.oid,'SELECT')
       AND pg_catalog.has_table_privilege('app_support',c.oid,'INSERT')
       AND NOT pg_catalog.has_table_privilege('app_support',c.oid,'UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
       AND NOT pg_catalog.has_table_privilege('app_support',c.oid,'SELECT WITH GRANT OPTION,INSERT WITH GRANT OPTION')
       AND NOT pg_catalog.has_any_column_privilege('app_support',c.oid,'REFERENCES')
       AND NOT pg_catalog.has_any_column_privilege('app_runtime',c.oid,'SELECT,INSERT,UPDATE,REFERENCES')
       AND NOT pg_catalog.has_table_privilege('app_runtime',c.oid,'DELETE,TRUNCATE,TRIGGER')
       AND NOT EXISTS (SELECT 1 FROM pg_catalog.aclexplode(COALESCE(c.relacl,
         pg_catalog.acldefault('r',c.relowner))) acl WHERE acl.grantee=0)
       AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_attribute a
         WHERE a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped AND (
           pg_catalog.has_column_privilege('app_support',c.oid,a.attnum,'UPDATE') <>
             (a.attname::text=ANY(expected.updates))
           OR pg_catalog.has_column_privilege('app_support',c.oid,a.attnum,'UPDATE WITH GRANT OPTION')
           OR EXISTS (SELECT 1 FROM pg_catalog.aclexplode(a.attacl) acl WHERE acl.grantee=0)))
       AND (SELECT count(*)=3 FROM pg_catalog.pg_policy p WHERE p.polrelid=c.oid)
       AND EXISTS (SELECT 1 FROM pg_catalog.pg_policy p WHERE p.polrelid=c.oid
         AND p.polname=expected.name||'_runtime' AND p.polcmd='r' AND p.polpermissive
         AND p.polroles=ARRAY[(SELECT oid FROM pg_catalog.pg_roles WHERE rolname='app_runtime')]
         AND p.polwithcheck IS NULL
         AND pg_catalog.pg_get_expr(p.polqual,p.polrelid) LIKE '%tenant_id = %'
         AND pg_catalog.pg_get_expr(p.polqual,p.polrelid) LIKE '%app.tenant_id%'
         AND pg_catalog.pg_get_expr(p.polqual,p.polrelid) LIKE '%NULLIF%'
         AND pg_catalog.pg_get_expr(p.polqual,p.polrelid) NOT LIKE '% OR %')
       AND NOT EXISTS (SELECT 1 FROM (VALUES ('app_support','_support'),('app_migrator','_migrator')) role(name,suffix)
         WHERE NOT EXISTS (SELECT 1 FROM pg_catalog.pg_policy p WHERE p.polrelid=c.oid
           AND p.polname=expected.name||role.suffix AND p.polcmd='*' AND p.polpermissive
           AND p.polroles=ARRAY[(SELECT oid FROM pg_catalog.pg_roles WHERE rolname=role.name)]
           AND pg_catalog.pg_get_expr(p.polqual,p.polrelid)='true'
           AND pg_catalog.pg_get_expr(p.polwithcheck,p.polrelid)='true'))))
"""

COLUMN_CONTRACT = """
 SELECT NOT EXISTS (
   SELECT 1 FROM (VALUES """ + _values(
       (table, *column) for table, columns in COLUMNS.items() for column in columns
   ) + """) expected(table_name,name,type_name,required,typmod)
   WHERE NOT EXISTS (SELECT 1 FROM pg_catalog.pg_attribute a
     WHERE a.attrelid=pg_catalog.to_regclass('public.'||expected.table_name)
       AND a.attnum>0 AND NOT a.attisdropped AND a.attname=expected.name
       AND a.atttypid=pg_catalog.to_regtype('pg_catalog.'||expected.type_name)
       AND a.attnotnull=expected.required AND a.atttypmod=expected.typmod))
"""

KEY_CONTRACT = """
 SELECT NOT EXISTS (
   SELECT 1 FROM (VALUES """ + _values(KEYS) + """) expected(table_name,name,kind,columns)
   WHERE NOT EXISTS (SELECT 1 FROM pg_catalog.pg_constraint c
     JOIN pg_catalog.pg_index i ON i.indexrelid=c.conindid
     WHERE c.conrelid=pg_catalog.to_regclass('public.'||expected.table_name)
       AND c.conname=expected.name AND c.contype::text=expected.kind
       AND c.convalidated AND NOT c.condeferrable AND NOT c.condeferred
       AND i.indisvalid AND i.indisready AND i.indisunique
       AND ARRAY(SELECT a.attname::text FROM unnest(c.conkey) WITH ORDINALITY k(num,pos)
         JOIN pg_catalog.pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=k.num
         ORDER BY k.pos)=expected.columns))
"""

FOREIGN_KEY_CONTRACT = """
 SELECT NOT EXISTS (
   SELECT 1 FROM (VALUES """ + _values(FOREIGN_KEYS) + """) expected(table_name,name,columns,parent,parent_columns)
   WHERE NOT EXISTS (SELECT 1 FROM pg_catalog.pg_constraint c
     WHERE c.conrelid=pg_catalog.to_regclass('public.'||expected.table_name)
       AND c.confrelid=pg_catalog.to_regclass('public.'||expected.parent)
       AND c.conname=expected.name AND c.contype='f' AND c.convalidated
       AND NOT c.condeferrable AND NOT c.condeferred
       AND c.confdeltype='a' AND c.confupdtype='a' AND c.confmatchtype='s'
       AND ARRAY(SELECT a.attname::text FROM unnest(c.conkey) WITH ORDINALITY k(num,pos)
         JOIN pg_catalog.pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=k.num
         ORDER BY k.pos)=expected.columns
       AND ARRAY(SELECT a.attname::text FROM unnest(c.confkey) WITH ORDINALITY k(num,pos)
         JOIN pg_catalog.pg_attribute a ON a.attrelid=c.confrelid AND a.attnum=k.num
         ORDER BY k.pos)=expected.parent_columns))
"""

CHECK_CONTRACT = """
 SELECT NOT EXISTS (
   SELECT 1 FROM (VALUES """ + _values(CHECKS) + """) expected(table_name,name,fragments)
   WHERE NOT EXISTS (SELECT 1 FROM pg_catalog.pg_constraint c
     WHERE c.conrelid=pg_catalog.to_regclass('public.'||expected.table_name)
       AND c.conname=expected.name AND c.contype='c' AND c.convalidated
       AND NOT EXISTS (SELECT 1 FROM unnest(expected.fragments) required(fragment)
         WHERE strpos(lower(pg_catalog.pg_get_constraintdef(c.oid)),required.fragment)=0)))
"""

INDEX_CONTRACT = """
 SELECT NOT EXISTS (
   SELECT 1 FROM (VALUES """ + _values(INDEXES) + """) expected(table_name,name,columns,predicate_fragments)
   WHERE NOT EXISTS (SELECT 1 FROM pg_catalog.pg_index i
     JOIN pg_catalog.pg_class c ON c.oid=i.indexrelid
     WHERE i.indrelid=pg_catalog.to_regclass('public.'||expected.table_name)
       AND c.relnamespace='public'::regnamespace AND c.relname=expected.name
       AND i.indisvalid AND i.indisready AND i.indexprs IS NULL
       AND i.indnkeyatts=cardinality(expected.columns) AND i.indnatts=i.indnkeyatts
       AND ARRAY(SELECT a.attname::text FROM unnest(i.indkey::smallint[]) WITH ORDINALITY k(num,pos)
         JOIN pg_catalog.pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=k.num
         ORDER BY k.pos)=expected.columns
       AND (i.indpred IS NOT NULL)=(cardinality(expected.predicate_fragments)>0)
       AND NOT EXISTS (SELECT 1 FROM unnest(expected.predicate_fragments) required(fragment)
         WHERE strpos(lower(pg_catalog.pg_get_expr(i.indpred,i.indrelid)),required.fragment)=0)))
"""

CONTRACT_QUERY = "SELECT " + " AND ".join(
    "COALESCE((" + query + "),false)" for query in (
        PREVIOUS_CONTRACT, TABLE_CONTRACT, COLUMN_CONTRACT, KEY_CONTRACT,
        FOREIGN_KEY_CONTRACT, CHECK_CONTRACT, INDEX_CONTRACT)
) + " AS ready"

SCHEMA_QUERY = """
 SELECT current_user AS runtime_role, session_user AS authenticated_role,
   EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname=current_user
     AND NOT rolsuper AND NOT rolcreaterole AND NOT rolbypassrls) AS restricted_role,
   COALESCE((""" + CONTRACT_QUERY + """),false) AS schema_ready
"""


async def probe_schema(settings, timeout):
    connection = None
    try:
        milliseconds = str(max(1, int(timeout * 1000)))
        connection = await asyncpg.connect(settings.dsn, timeout=timeout,
            command_timeout=timeout, server_settings={
                "default_transaction_read_only": "on", "search_path": "pg_catalog",
                "statement_timeout": milliseconds, "lock_timeout": milliseconds,
                "application_name": "orion-subscription-campaigns-schema-wait",
            })
        row = await connection.fetchrow(SCHEMA_QUERY)
        if (row["runtime_role"] != "app_runtime" or row["authenticated_role"] != "app_runtime"
                or row["restricted_role"] is not True):
            raise Stop("runtime_role_required")
        return row["schema_ready"] is True
    finally:
        if connection is not None:
            connection.terminate()


async def wait_for_schema(settings, **kwargs):
    result = await previous.wait_for_schema(settings, probe=kwargs.pop("probe", probe_schema), **kwargs)
    return {**result, "status": "subscription_campaigns_schema_ready", "schema_marker": SCHEMA_MARKER}


def main():
    try:
        result = asyncio.run(wait_for_schema(Settings.from_environment()))
    except Exception as error:
        code = str(error) if isinstance(error, Stop) and str(error) in SAFE_ERRORS else "subscription_campaigns_schema_check_failed"
        print(json.dumps({"ok": False, "error": code}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
