"""Read-only catalog barrier for isolated group membership work and receipts."""
import asyncio
import json

import asyncpg

from scripts import wait_for_agenda_schema as previous
from scripts import wait_for_subscription_scheduled_messages_schema as catalog
from scripts import wait_for_subscription_bulk_schema as templates
from scripts import wait_for_subscription_direct_schema as direct
from scripts.wait_for_agenda_schema import Settings, Stop, SAFE_ERRORS

SCHEMA_MARKER = "0072_subscription_group_add"
PREVIOUS_CONTRACT = direct.CONTRACT_QUERY
BATCH = "subscription_group_add_batches"
ITEM = "subscription_group_add_items"
SUPPORT_UPDATES = {
    BATCH: ("status", "version", "next_item_at", "started_at", "finished_at", "error_code"),
    ITEM: ("status", "claimed_owner", "claimed_fence", "dispatch_at", "finished_at", "error_code", "last_receipt_at", "provider_settled"),
}
PRIVATE_TABLES = tuple(SUPPORT_UPDATES)
COLUMNS = {
    BATCH: (
        ("id", "uuid", True, -1), ("tenant_id", "uuid", True, -1), ("owner_account_id", "uuid", True, -1),
        ("device_id", "uuid", True, -1), ("generation", "uuid", True, -1), ("settings_revision", "int8", True, -1),
        ("channel_id", "uuid", True, -1), ("device_name", "varchar", True, 104),
        ("destination_group_id", "varchar", True, 51), ("destination_name", "varchar", True, 104),
        ("source", "varchar", True, 12), ("filters", "jsonb", True, -1), ("source_hash", "bpchar", True, 68),
        ("idempotency_key", "uuid", True, -1), ("request_fingerprint", "bpchar", True, 68),
        ("status", "varchar", True, 20), ("version", "int8", True, -1), ("total", "int4", True, -1),
        ("next_item_at", "timestamptz", True, -1), ("created_at", "timestamptz", True, -1),
        ("started_at", "timestamptz", False, -1), ("finished_at", "timestamptz", False, -1), ("error_code", "varchar", False, 84)),
    ITEM: (
        ("id", "uuid", True, -1), ("tenant_id", "uuid", True, -1), ("batch_id", "uuid", True, -1),
        ("device_id", "uuid", True, -1), ("generation", "uuid", True, -1), ("settings_revision", "int8", True, -1),
        ("ordinal", "int4", True, -1), ("phone", "varchar", True, 20), ("name", "varchar", True, 104),
        ("source_context", "jsonb", True, -1), ("permission_version", "int8", True, -1),
        ("status", "varchar", True, 20), ("claim_token", "uuid", True, -1),
        ("claimed_owner", "uuid", False, -1), ("claimed_fence", "int8", False, -1),
        ("dispatch_at", "timestamptz", False, -1), ("finished_at", "timestamptz", False, -1),
        ("error_code", "varchar", False, 84), ("last_receipt_at", "timestamptz", False, -1),
        ("provider_settled", "bool", True, -1)),
}
KEYS = (
    (BATCH, BATCH + "_pkey", "p", ("id",)),
    (BATCH, "uq_subscription_group_add_batch", "u", ("tenant_id", "id")),
    (BATCH, "uq_subscription_group_add_key", "u", ("tenant_id", "idempotency_key")),
    (BATCH, "uq_subscription_group_add_context", "u", ("tenant_id", "id", "device_id", "generation", "settings_revision")),
    (ITEM, ITEM + "_pkey", "p", ("id",)),
    (ITEM, "uq_subscription_group_add_item", "u", ("tenant_id", "id")),
    (ITEM, "uq_subscription_group_add_ordinal", "u", ("tenant_id", "batch_id", "ordinal")),
    (ITEM, "uq_subscription_group_add_phone", "u", ("tenant_id", "batch_id", "phone")),
)
FOREIGN_KEYS = (
    (BATCH, BATCH + "_tenant_id_fkey", ("tenant_id",), "tenants", ("id",)),
    (BATCH, "fk_subscription_group_add_owner", ("tenant_id", "owner_account_id"), "tenant_memberships", ("tenant_id", "account_id")),
    (BATCH, "fk_subscription_group_add_device", ("tenant_id", "device_id", "generation"), "subscription_devices", ("tenant_id", "id", "generation")),
    (BATCH, "fk_subscription_group_add_channel", ("tenant_id", "channel_id"), "qr_channels", ("tenant_id", "id")),
    (ITEM, "fk_subscription_group_add_item_context", ("tenant_id", "batch_id", "device_id", "generation", "settings_revision"),
        BATCH, ("tenant_id", "id", "device_id", "generation", "settings_revision")),
)
CHECKS = (
    (BATCH, BATCH + "_settings_revision_check", ("settings_revision > 0",)),
    (BATCH, BATCH + "_source_check", ("'contacts'", "'group'")),
    (BATCH, BATCH + "_filters_check", ("jsonb_typeof(filters)", "16384")),
    (BATCH, BATCH + "_source_hash_check", ("^[a-f0-9]{64}$",)),
    (BATCH, BATCH + "_request_fingerprint_check", ("^[a-f0-9]{64}$",)),
    (BATCH, BATCH + "_status_check", ("'pending'", "'running'", "'completed'", "'partial'", "'unknown'", "'cancelled'")),
    (BATCH, BATCH + "_version_check", ("version > 0",)),
    (BATCH, BATCH + "_total_check", ("total >= 1", "total <= 5000")),
    (BATCH, "subscription_group_add_destination", ("destination_group_id", "@g[.]us$")),
    (BATCH, "subscription_group_add_batch_terminal", ("'pending'", "'running'", "finished_at is null", "finished_at is not null")),
    (ITEM, ITEM + "_ordinal_check", ("ordinal >= 1", "ordinal <= 5000")),
    (ITEM, ITEM + "_phone_check", ("[1-9][0-9]{7,14}$",)),
    (ITEM, ITEM + "_source_context_check", ("jsonb_typeof(source_context)", "32768")),
    (ITEM, ITEM + "_permission_version_check", ("permission_version >= 0",)),
    (ITEM, ITEM + "_status_check", ("'pending'", "'running'", "'added'", "'already_member'", "'not_added'", "'unknown'", "'cancelled'")),
    (ITEM, ITEM + "_claimed_fence_check", ("claimed_fence > 0",)),
    (ITEM, "subscription_group_add_claim", ("claimed_owner is null", "claimed_fence is null")),
    (ITEM, "subscription_group_add_item_terminal", ("'pending'", "'running'", "finished_at is null", "finished_at is not null")),
    (ITEM, "subscription_group_add_dispatch", ("'pending'", "dispatch_at is null", "'running'", "'unknown'", "dispatch_at is not null", "'cancelled'")),
)
INDEXES = (
    (BATCH, "uq_subscription_group_add_active_device", ("tenant_id", "device_id"), ("'pending'", "'running'")),
    (BATCH, "ix_subscription_group_add_due", ("next_item_at", "tenant_id", "id"), ("'pending'", "'running'")),
    (ITEM, "ix_subscription_group_add_item_next", ("tenant_id", "batch_id", "ordinal"), ("'pending'",)),
    (ITEM, "ix_subscription_group_add_item_unknown", ("tenant_id", "dispatch_at"), ("'running'", "'unknown'")),
)

replace = templates._catalog_contract
TABLE_CONTRACT = replace(catalog.TABLE_CONTRACT, catalog.SUPPORT_UPDATES.items(), SUPPORT_UPDATES.items())
COLUMN_CONTRACT = replace(catalog.COLUMN_CONTRACT,
    ((table, *column) for table, columns in catalog.COLUMNS.items() for column in columns),
    ((table, *column) for table, columns in COLUMNS.items() for column in columns))
KEY_CONTRACT = replace(catalog.KEY_CONTRACT, catalog.KEYS, KEYS)
FOREIGN_KEY_CONTRACT = replace(catalog.FOREIGN_KEY_CONTRACT, catalog.FOREIGN_KEYS, FOREIGN_KEYS)
CHECK_CONTRACT = replace(catalog.CHECK_CONTRACT, catalog.CHECKS, CHECKS)
INDEX_CONTRACT = replace(catalog.INDEX_CONTRACT, catalog.INDEXES, INDEXES)
SETTLEMENT_CONTRACT = """SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_attrdef d
  ON d.adrelid=a.attrelid AND d.adnum=a.attnum
  WHERE a.attrelid=pg_catalog.to_regclass('public.subscription_group_add_items')
    AND a.attname='provider_settled' AND pg_catalog.pg_get_expr(d.adbin,d.adrelid)='false')
  AND EXISTS(SELECT 1 FROM pg_catalog.pg_index i WHERE i.indexrelid=
    pg_catalog.to_regclass('public.uq_subscription_group_add_active_device') AND i.indisunique)"""
CUSTOMER_DATE_CONTRACT = """SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_attribute a
 WHERE a.attrelid=pg_catalog.to_regclass('public.customers') AND a.attname='created_at'
   AND a.attnum>0 AND NOT a.attisdropped AND a.attnotnull
   AND a.atttypid=pg_catalog.to_regtype('pg_catalog.timestamptz')
   AND pg_catalog.has_column_privilege('app_support',a.attrelid,a.attnum,'SELECT')
   AND NOT pg_catalog.has_column_privilege('app_support',a.attrelid,a.attnum,
     'SELECT WITH GRANT OPTION,INSERT,UPDATE,REFERENCES'))"""
# Exact state domains and the active-device predicate are admission boundaries.
# Strip only parser-added text casts, whitespace and grouping parentheses;
# extra values/operators remain visible and cannot pass this comparison.
EXACT_DOMAINS_CONTRACT = r"""WITH expected(table_name,constraint_name,expression) AS (VALUES
 ('subscription_group_add_batches','subscription_group_add_batches_source_check',
   'source=anyarray[''contacts'',''group'']'),
 ('subscription_group_add_batches','subscription_group_add_batches_status_check',
   'status=anyarray[''pending'',''running'',''completed'',''partial'',''unknown'',''cancelled'']'),
 ('subscription_group_add_items','subscription_group_add_items_status_check',
   'status=anyarray[''pending'',''running'',''added'',''already_member'',''not_added'',''unknown'',''cancelled'']'))
 SELECT NOT EXISTS(SELECT 1 FROM expected e WHERE NOT EXISTS(
   SELECT 1 FROM pg_catalog.pg_constraint c WHERE c.conrelid=pg_catalog.to_regclass('public.'||e.table_name)
   AND c.conname=e.constraint_name AND c.contype='c' AND c.convalidated
   AND pg_catalog.regexp_replace(pg_catalog.regexp_replace(
      lower(pg_catalog.pg_get_expr(c.conbin,c.conrelid)),
      '::(character varying|text)(\[\])?','','g'),'[[:space:]()]','','g')=e.expression))
 AND EXISTS(SELECT 1 FROM pg_catalog.pg_index i WHERE i.indexrelid=
   pg_catalog.to_regclass('public.uq_subscription_group_add_active_device') AND i.indisunique
   AND pg_catalog.regexp_replace(pg_catalog.regexp_replace(
      lower(pg_catalog.pg_get_expr(i.indpred,i.indrelid)),
      '::(character varying|text)(\[\])?','','g'),'[[:space:]()]','','g')=
      'status=anyarray[''pending'',''running'']')"""
CONTRACT_QUERY = "SELECT " + " AND ".join("COALESCE((" + query + "),false)" for query in (
    PREVIOUS_CONTRACT, TABLE_CONTRACT, COLUMN_CONTRACT, KEY_CONTRACT, FOREIGN_KEY_CONTRACT,
    CHECK_CONTRACT, INDEX_CONTRACT, SETTLEMENT_CONTRACT, CUSTOMER_DATE_CONTRACT, EXACT_DOMAINS_CONTRACT)) + " AS ready"
SCHEMA_QUERY = """SELECT current_user AS runtime_role,session_user AS authenticated_role,
  EXISTS(SELECT 1 FROM pg_catalog.pg_roles WHERE rolname=current_user
    AND NOT rolsuper AND NOT rolcreaterole AND NOT rolbypassrls) AS restricted_role,
  COALESCE((""" + CONTRACT_QUERY + """),false) AS schema_ready"""


async def probe_schema(settings, timeout):
    connection = None
    try:
        milliseconds = str(max(1, int(timeout * 1000)))
        connection = await asyncpg.connect(settings.dsn, timeout=timeout, command_timeout=timeout,
            server_settings={"default_transaction_read_only": "on", "search_path": "pg_catalog",
                "statement_timeout": milliseconds, "lock_timeout": milliseconds,
                "application_name": "orion-subscription-group-add-schema-wait"})
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
    return {**result, "status": "subscription_group_add_schema_ready", "schema_marker": SCHEMA_MARKER}


def main():
    try:
        result = asyncio.run(wait_for_schema(Settings.from_environment()))
    except Exception as error:
        code = str(error) if isinstance(error, Stop) and str(error) in SAFE_ERRORS else "subscription_group_add_schema_check_failed"
        print(json.dumps({"ok": False, "error": code}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
