"""Read-only catalog barrier for confirmed scheduled-message work.

This independent contract requires the reviewed columns, tenant keys, guards,
RLS and exact role grants; it never reads messages or recipient rows.
"""
import asyncio
import json

import asyncpg

from scripts import wait_for_agenda_schema as previous
from scripts.wait_for_agenda_schema import Settings, Stop, SAFE_ERRORS
from scripts.wait_for_subscription_campaigns_schema import CONTRACT_QUERY as PREVIOUS_CONTRACT

SCHEMA_MARKER = "0069_subscription_schedules"
SUPPORT_UPDATES = {
    "subscription_schedules": ("owner_account_id", "title", "version", "status", "definition", "snapshot", "run_version",
        "device_id", "template_id", "template_revision", "timezone", "anchor_date", "anchor_time", "interval_seconds",
        "repeat_every_30_days", "confirmed_at", "cancelled_at", "updated_at"),
    "subscription_schedule_runs": ("schedule_version", "version", "run_version", "status", "due_at", "local_date", "timezone",
        "snapshot", "first_admitted_at", "last_admitted_at", "next_admission_at", "manual_released_at", "finished_at", "error_code", "updated_at"),
    "subscription_schedule_recipients": ("status", "admitted_at", "finished_at", "error_code"),
    "subscription_schedule_requests": ("schedule_id", "run_id", "status", "result", "confirmed_at"),
}
PRIVATE_TABLES = tuple(SUPPORT_UPDATES)
SUPPORT_CUSTOMER_COLUMNS = ("id", "tenant_id", "whatsapp_e164", "name", "active", "deleted_at", "version")

# PostgreSQL widths include four typmod bytes. A column listed as nullable
# must stay nullable as well, notably the explicit invalid-DST placeholder.
COLUMNS = {
    "subscription_schedules": (
        ("id", "uuid", True, -1), ("tenant_id", "uuid", True, -1), ("owner_account_id", "uuid", True, -1),
        ("title", "varchar", True, 124), ("version", "int8", True, -1), ("status", "varchar", True, 20),
        ("definition", "jsonb", True, -1), ("snapshot", "jsonb", True, -1), ("run_version", "bpchar", True, 68),
        ("device_id", "uuid", True, -1), ("template_id", "uuid", False, -1), ("template_revision", "int8", False, -1),
        ("timezone", "varchar", True, 68), ("anchor_date", "date", True, -1), ("anchor_time", "bpchar", True, 9),
        ("interval_seconds", "int2", True, -1), ("repeat_every_30_days", "bool", True, -1),
        ("confirmed_at", "timestamptz", True, -1), ("cancelled_at", "timestamptz", False, -1),
        ("created_at", "timestamptz", True, -1), ("updated_at", "timestamptz", True, -1)),
    "subscription_schedule_runs": (
        ("id", "uuid", True, -1), ("tenant_id", "uuid", True, -1), ("schedule_id", "uuid", True, -1),
        ("occurrence_index", "int4", True, -1), ("schedule_version", "int8", True, -1), ("version", "int8", True, -1),
        ("run_version", "bpchar", True, 68), ("status", "varchar", True, 20), ("due_at", "timestamptz", False, -1),
        ("local_date", "date", True, -1), ("timezone", "varchar", True, 68), ("snapshot", "jsonb", True, -1),
        ("first_admitted_at", "timestamptz", False, -1), ("last_admitted_at", "timestamptz", False, -1),
        ("next_admission_at", "timestamptz", False, -1), ("manual_released_at", "timestamptz", False, -1),
        ("finished_at", "timestamptz", False, -1), ("error_code", "varchar", False, 84), ("source_run_id", "uuid", False, -1),
        ("created_at", "timestamptz", True, -1), ("updated_at", "timestamptz", True, -1)),
    "subscription_schedule_recipients": (
        ("id", "uuid", True, -1), ("tenant_id", "uuid", True, -1), ("schedule_id", "uuid", True, -1),
        ("run_id", "uuid", True, -1), ("run_version", "bpchar", True, 68), ("ordinal", "int4", True, -1),
        ("phone", "varchar", True, 19), ("name", "varchar", True, 124), ("origins", "jsonb", True, -1),
        ("permission_version", "int8", True, -1), ("device_id", "uuid", True, -1), ("generation", "uuid", True, -1),
        ("settings_revision", "int8", True, -1), ("plan", "jsonb", True, -1), ("plan_hash", "bpchar", True, 68),
        ("status", "varchar", True, 20), ("admitted_at", "timestamptz", False, -1), ("finished_at", "timestamptz", False, -1),
        ("error_code", "varchar", False, 84), ("created_at", "timestamptz", True, -1)),
    "subscription_schedule_requests": (
        ("id", "uuid", True, -1), ("tenant_id", "uuid", True, -1), ("actor_account_id", "uuid", True, -1),
        ("idempotency_key", "uuid", True, -1), ("kind", "varchar", True, 28), ("request_fingerprint", "bpchar", True, 68),
        ("schedule_id", "uuid", False, -1), ("run_id", "uuid", False, -1), ("status", "varchar", True, 20),
        ("review_version", "bpchar", False, 68), ("snapshot", "jsonb", False, -1), ("preview", "jsonb", False, -1),
        ("result", "jsonb", False, -1), ("created_at", "timestamptz", True, -1), ("expires_at", "timestamptz", False, -1),
        ("confirmed_at", "timestamptz", False, -1)),
    "subscription_device_effects": (
        ("schedule_id", "uuid", False, -1), ("schedule_run_id", "uuid", False, -1),
        ("schedule_recipient_id", "uuid", False, -1), ("schedule_part_index", "int2", False, -1)),
}
KEYS = (
    ("subscription_schedules", "subscription_schedules_pkey", "p", ("id",)),
    ("subscription_schedules", "uq_subscription_schedule_scope", "u", ("tenant_id", "id")),
    ("subscription_schedule_runs", "subscription_schedule_runs_pkey", "p", ("id",)),
    ("subscription_schedule_runs", "uq_subscription_schedule_run_id", "u", ("tenant_id", "id")),
    ("subscription_schedule_runs", "uq_subscription_schedule_run_scope", "u", ("tenant_id", "schedule_id", "id")),
    ("subscription_schedule_runs", "uq_subscription_schedule_run_occurrence", "u", ("tenant_id", "schedule_id", "occurrence_index")),
    ("subscription_schedule_recipients", "subscription_schedule_recipients_pkey", "p", ("id",)),
    ("subscription_schedule_recipients", "uq_subscription_schedule_recipient_id", "u", ("tenant_id", "id")),
    ("subscription_schedule_recipients", "uq_subscription_schedule_recipient_scope", "u", ("tenant_id", "schedule_id", "run_id", "id")),
    ("subscription_schedule_recipients", "uq_subscription_schedule_recipient_phone", "u", ("tenant_id", "run_id", "run_version", "phone")),
    ("subscription_schedule_recipients", "uq_subscription_schedule_recipient_order", "u", ("tenant_id", "run_id", "run_version", "ordinal")),
    ("subscription_schedule_requests", "subscription_schedule_requests_pkey", "p", ("id",)),
    ("subscription_schedule_requests", "uq_subscription_schedule_request_id", "u", ("tenant_id", "id")),
    ("subscription_schedule_requests", "uq_subscription_schedule_request_key", "u", ("tenant_id", "idempotency_key")),
    ("subscription_device_effects", "uq_subscription_device_effect_schedule_part", "u", ("tenant_id", "schedule_recipient_id", "schedule_part_index")),
)
FOREIGN_KEYS = (
    ("subscription_schedules", "subscription_schedules_tenant_id_fkey", ("tenant_id",), "tenants", ("id",)),
    ("subscription_schedules", "fk_subscription_schedule_owner", ("tenant_id", "owner_account_id"), "tenant_memberships", ("tenant_id", "account_id")),
    ("subscription_schedules", "fk_subscription_schedule_device", ("tenant_id", "device_id"), "subscription_devices", ("tenant_id", "id")),
    ("subscription_schedules", "fk_subscription_schedule_template", ("tenant_id", "template_id"), "subscription_chatbot_templates", ("tenant_id", "id")),
    ("subscription_schedule_runs", "fk_subscription_schedule_run_schedule", ("tenant_id", "schedule_id"), "subscription_schedules", ("tenant_id", "id")),
    ("subscription_schedule_runs", "fk_subscription_schedule_run_source", ("tenant_id", "schedule_id", "source_run_id"), "subscription_schedule_runs", ("tenant_id", "schedule_id", "id")),
    ("subscription_schedule_recipients", "fk_subscription_schedule_recipient_run", ("tenant_id", "schedule_id", "run_id"), "subscription_schedule_runs", ("tenant_id", "schedule_id", "id")),
    ("subscription_schedule_recipients", "fk_subscription_schedule_recipient_device", ("tenant_id", "device_id", "generation"), "subscription_devices", ("tenant_id", "id", "generation")),
    ("subscription_schedule_requests", "subscription_schedule_requests_tenant_id_fkey", ("tenant_id",), "tenants", ("id",)),
    ("subscription_schedule_requests", "fk_subscription_schedule_request_actor", ("tenant_id", "actor_account_id"), "tenant_memberships", ("tenant_id", "account_id")),
    ("subscription_schedule_requests", "fk_subscription_schedule_request_schedule", ("tenant_id", "schedule_id"), "subscription_schedules", ("tenant_id", "id")),
    ("subscription_schedule_requests", "fk_subscription_schedule_request_run", ("tenant_id", "schedule_id", "run_id"), "subscription_schedule_runs", ("tenant_id", "schedule_id", "id")),
    ("subscription_device_effects", "fk_subscription_device_effect_schedule", ("tenant_id", "schedule_id"), "subscription_schedules", ("tenant_id", "id")),
    ("subscription_device_effects", "fk_subscription_device_effect_schedule_run", ("tenant_id", "schedule_id", "schedule_run_id"), "subscription_schedule_runs", ("tenant_id", "schedule_id", "id")),
    ("subscription_device_effects", "fk_subscription_device_effect_schedule_recipient", ("tenant_id", "schedule_id", "schedule_run_id", "schedule_recipient_id"), "subscription_schedule_recipients", ("tenant_id", "schedule_id", "run_id", "id")),
)
CHECKS = (
    ("subscription_schedules", "subscription_schedule_values", ("version > 0", "length", "title", "'active'", "'cancelled'", "'completed'",
        "jsonb_typeof(definition)", "jsonb_typeof(snapshot)", "1048576", "^[a-f0-9]{64}$", "timezone", "anchor_time", "interval_seconds")),
    ("subscription_schedules", "subscription_schedule_template", ("template_id is null", "template_revision is null", "template_id is not null", "template_revision is not null", "template_revision > 0")),
    ("subscription_schedules", "subscription_schedule_cancellation", ("'cancelled'", "cancelled_at is null", "cancelled_at is not null")),
    ("subscription_schedule_runs", "subscription_schedule_run_values", ("occurrence_index >= 0", "schedule_version > 0", "version > 0", "^[a-f0-9]{64}$", "jsonb_typeof(snapshot)", "1048576", "'pending'", "'running'", "'completed'", "'failed'", "'uncertain'", "'cancelled'")),
    ("subscription_schedule_runs", "subscription_schedule_run_terminal", ("'pending'", "'running'", "finished_at is null", "'completed'", "'failed'", "'uncertain'", "'cancelled'", "finished_at is not null")),
    ("subscription_schedule_runs", "subscription_schedule_run_admission", ("'running'", "first_admitted_at is not null", "first_admitted_at is null", "last_admitted_at is null", "last_admitted_at is not null", "last_admitted_at >= first_admitted_at")),
    ("subscription_schedule_runs", "subscription_schedule_run_due", ("due_at is not null", "due_at is null", "error_code", "is distinct from", "'schedule_recurrence_invalid'", "'pending'", "'cancelled'", "first_admitted_at is null", "manual_released_at is null", "next_admission_at is null")),
    ("subscription_schedule_runs", "subscription_schedule_run_origin", ("occurrence_index = 0", "source_run_id is null", "occurrence_index > 0", "source_run_id is not null", "source_run_id <> id")),
    ("subscription_schedule_recipients", "subscription_schedule_recipient_values", ("^[a-f0-9]{64}$", "plan_hash", "ordinal >= 1", "ordinal <= 10000", "^[1-9][0-9]{7,14}$", "length", "name", "jsonb_typeof(origins)", "jsonb_array_length(origins) >= 1", "jsonb_array_length(origins) <= 101", "octet_length((origins)::text) <= 131072", "permission_version >= 0", "settings_revision > 0", "jsonb_typeof(plan)", "jsonb_array_length(plan)", "131072", "'pending'", "'running'", "'sent'", "'failed'", "'uncertain'", "'cancelled'")),
    ("subscription_schedule_recipients", "subscription_schedule_recipient_terminal", ("'pending'", "'running'", "finished_at is null", "'sent'", "'failed'", "'uncertain'", "'cancelled'", "finished_at is not null")),
    ("subscription_schedule_recipients", "subscription_schedule_recipient_admission", ("'running'", "'sent'", "'uncertain'", "admitted_at is not null")),
    ("subscription_schedule_requests", "subscription_schedule_request_values", ("'review_create'", "'review_edit'", "'pending_review'", "'cancel_run'", "'cancel_series'", "'prepared'", "'succeeded'", "^[a-f0-9]{64}$", "jsonb_typeof(result)", "4194304")),
    ("subscription_schedule_requests", "subscription_schedule_request_scope", ("'review_create'", "'review_edit'", "'pending_review'", "'cancel_run'", "'cancel_series'", "schedule_id is null", "schedule_id is not null", "run_id is null", "run_id is not null")),
    ("subscription_schedule_requests", "subscription_schedule_request_review", ("'review_create'", "'review_edit'", "'pending_review'", "review_version is not null", "^[a-f0-9]{64}$", "jsonb_typeof(snapshot)", "67108864", "jsonb_typeof(preview)", "1048576", "expires_at is not null", "expires_at > created_at", "expires_at <=", "00:02:00", "'prepared'", "result is null", "confirmed_at is null", "'succeeded'", "result is not null", "confirmed_at is not null", "snapshot is null", "preview is null", "review_version is null", "expires_at is null")),
    ("subscription_device_events", "subscription_device_events_kind_check", ("'campaign'", "'scheduled'")),
    ("subscription_device_effects", "subscription_device_effects_type_check", ("'chatbot_action'", "'campaign_action'", "'scheduled_action'")),
    ("subscription_device_effects", "subscription_device_effect_schedule_scope", ("'scheduled_action'", "purpose", "schedule_id is not null", "schedule_run_id is not null", "schedule_recipient_id is not null", "schedule_part_index is not null", "schedule_part_index >= 0", "schedule_part_index <= 2", "schedule_id is null", "schedule_run_id is null", "schedule_recipient_id is null", "schedule_part_index is null")),
)
INDEXES = (
    ("subscription_schedules", "ix_subscription_schedule_list", ("tenant_id", "created_at", "id"), ()),
    ("subscription_schedules", "ix_subscription_schedule_active", ("tenant_id", "device_id", "id"), ("'active'",)),
    ("subscription_schedule_runs", "ix_subscription_schedule_run_list", ("tenant_id", "created_at", "id"), ()),
    ("subscription_schedule_runs", "ix_subscription_schedule_run_due", ("tenant_id", "next_admission_at", "due_at", "id"), ("'pending'", "'running'")),
    ("subscription_schedule_recipients", "ix_subscription_schedule_recipient_pending", ("tenant_id", "run_id", "run_version", "device_id", "ordinal"), ("'pending'",)),
    ("subscription_schedule_recipients", "ix_subscription_schedule_recipient_rate", ("tenant_id", "admitted_at"), ("admitted_at is not null",)),
    ("subscription_schedule_recipients", "ix_subscription_schedule_recipient_contact", ("tenant_id", "phone", "status"), ()),
    ("subscription_schedule_requests", "ix_subscription_schedule_request_actor", ("tenant_id", "actor_account_id", "created_at", "id"), ()),
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

# 0072 adds only the CRM creation timestamp for its observed date filters.
# Old 0069-0071 schemas keep their exact original column set. Both reviewed,
# RLS-protected 0072 relations must exist before this extension is accepted.
GROUP_ADD_CUSTOMER_DATE_SCHEMA = """SELECT NOT EXISTS(
 SELECT 1 FROM (VALUES ('subscription_group_add_batches'),('subscription_group_add_items')) expected(name)
 WHERE NOT EXISTS(SELECT 1 FROM pg_catalog.pg_class c
   WHERE c.oid=pg_catalog.to_regclass('public.'||expected.name) AND c.relkind='r'
     AND c.relrowsecurity AND c.relforcerowsecurity
     AND pg_catalog.pg_get_userbyid(c.relowner)='app_migrator'))"""

CUSTOMER_CATALOG_CONTRACT = """
 SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_class c
   WHERE c.oid=pg_catalog.to_regclass('public.customers')
     AND c.relkind='r' AND c.relrowsecurity AND c.relforcerowsecurity
     AND NOT pg_catalog.has_table_privilege('app_support',c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
     AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_attribute a
       WHERE a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped AND (
         pg_catalog.has_column_privilege('app_support',c.oid,a.attnum,'SELECT') <>
           (a.attname::text=ANY(""" + _literal(SUPPORT_CUSTOMER_COLUMNS) + """)
             OR a.attname='created_at' AND (""" + GROUP_ADD_CUSTOMER_DATE_SCHEMA + """))
         OR pg_catalog.has_column_privilege('app_support',c.oid,a.attnum,'SELECT WITH GRANT OPTION,INSERT,UPDATE,REFERENCES')))
     AND EXISTS(SELECT 1 FROM pg_catalog.pg_policy p WHERE p.polrelid=c.oid
       AND p.polname='customers_subscription_schedule_support' AND p.polcmd='r' AND p.polpermissive
       AND p.polroles=ARRAY[(SELECT oid FROM pg_catalog.pg_roles WHERE rolname='app_support')]
       AND p.polwithcheck IS NULL
       AND pg_catalog.pg_get_expr(p.polqual,p.polrelid) LIKE '%EXISTS%'
       AND pg_catalog.pg_get_expr(p.polqual,p.polrelid) LIKE '%business_onboarding%'
       AND pg_catalog.pg_get_expr(p.polqual,p.polrelid) LIKE '%customers.tenant_id%'
       AND pg_catalog.pg_get_expr(p.polqual,p.polrelid) LIKE '%configuration%'
       AND pg_catalog.pg_get_expr(p.polqual,p.polrelid) LIKE '%''niche''%'
       AND pg_catalog.pg_get_expr(p.polqual,p.polrelid) LIKE '%''servico_assinaturas''%'
       AND pg_catalog.pg_get_expr(p.polqual,p.polrelid) NOT LIKE '% OR %')
     AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_policy p WHERE p.polrelid=c.oid
       AND p.polname<>'customers_subscription_schedule_support'
       AND (0=ANY(p.polroles) OR (SELECT oid FROM pg_catalog.pg_roles WHERE rolname='app_support')=ANY(p.polroles))))
"""

CONTRACT_QUERY = "SELECT " + " AND ".join(
    "COALESCE((" + query + "),false)" for query in (
        PREVIOUS_CONTRACT, TABLE_CONTRACT, COLUMN_CONTRACT, KEY_CONTRACT,
        FOREIGN_KEY_CONTRACT, CHECK_CONTRACT, INDEX_CONTRACT, CUSTOMER_CATALOG_CONTRACT)
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
                "application_name": "orion-subscription-scheduled-messages-schema-wait",
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
    return {**result, "status": "subscription_scheduled_messages_schema_ready", "schema_marker": SCHEMA_MARKER}


def main():
    try:
        result = asyncio.run(wait_for_schema(Settings.from_environment()))
    except Exception as error:
        code = str(error) if isinstance(error, Stop) and str(error) in SAFE_ERRORS else "subscription_scheduled_messages_schema_check_failed"
        print(json.dumps({"ok": False, "error": code}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
