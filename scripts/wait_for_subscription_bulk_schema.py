"""Read-only catalog barrier for immutable one-shot bulk messages.

The predecessor remains mandatory. Generic catalog predicates are reused with
an explicit, independently enumerated bulk schema; no private row is read.
"""
import asyncio
import json

import asyncpg

from scripts import wait_for_agenda_schema as previous
from scripts import wait_for_subscription_scheduled_messages_schema as scheduled
from scripts.wait_for_agenda_schema import Settings, Stop, SAFE_ERRORS

SCHEMA_MARKER = "0070_subscription_bulk"
PREVIOUS_CONTRACT = scheduled.CONTRACT_QUERY
SUPPORT_UPDATES = {
    "subscription_bulk_batches": ("version", "status", "first_admitted_at", "last_admitted_at",
        "next_admission_at", "finished_at", "cancelled_at", "error_code", "updated_at"),
    "subscription_bulk_recipients": ("status", "admitted_at", "finished_at", "error_code"),
    "subscription_bulk_requests": ("bulk_id", "status", "result", "confirmed_at"),
}
PRIVATE_TABLES = tuple(SUPPORT_UPDATES)
COLUMNS = {
    "subscription_bulk_batches": (
        ("id", "uuid", True, -1), ("tenant_id", "uuid", True, -1), ("owner_account_id", "uuid", True, -1),
        ("version", "int8", True, -1), ("run_version", "bpchar", True, 68), ("status", "varchar", True, 20),
        ("interval_seconds", "int2", True, -1), ("snapshot", "jsonb", True, -1),
        ("confirmed_at", "timestamptz", True, -1), ("first_admitted_at", "timestamptz", False, -1),
        ("last_admitted_at", "timestamptz", False, -1), ("next_admission_at", "timestamptz", False, -1),
        ("finished_at", "timestamptz", False, -1), ("cancelled_at", "timestamptz", False, -1),
        ("error_code", "varchar", False, 84), ("created_at", "timestamptz", True, -1),
        ("updated_at", "timestamptz", True, -1)),
    "subscription_bulk_recipients": (
        ("id", "uuid", True, -1), ("tenant_id", "uuid", True, -1), ("bulk_id", "uuid", True, -1),
        ("row_key", "uuid", True, -1), ("ordinal", "int4", True, -1), ("run_version", "bpchar", True, 68),
        ("device_id", "uuid", True, -1), ("generation", "uuid", True, -1), ("settings_revision", "int8", True, -1),
        ("phone", "varchar", True, 19), ("name", "varchar", True, 124), ("source", "varchar", True, 12),
        ("origins", "jsonb", True, -1), ("manual_consent", "bool", True, -1),
        ("permission_version", "int8", True, -1), ("template_id", "uuid", False, -1),
        ("template_revision", "int8", False, -1), ("context", "jsonb", True, -1), ("plan", "jsonb", True, -1),
        ("plan_hash", "bpchar", True, 68), ("status", "varchar", True, 20),
        ("admitted_at", "timestamptz", False, -1), ("finished_at", "timestamptz", False, -1),
        ("error_code", "varchar", False, 84), ("created_at", "timestamptz", True, -1)),
    "subscription_bulk_requests": (
        ("id", "uuid", True, -1), ("tenant_id", "uuid", True, -1), ("actor_account_id", "uuid", True, -1),
        ("idempotency_key", "uuid", True, -1), ("kind", "varchar", True, 20),
        ("request_fingerprint", "bpchar", True, 68), ("bulk_id", "uuid", False, -1),
        ("status", "varchar", True, 20), ("review_version", "bpchar", False, 68),
        ("snapshot", "jsonb", False, -1), ("preview", "jsonb", False, -1), ("result", "jsonb", False, -1),
        ("created_at", "timestamptz", True, -1), ("expires_at", "timestamptz", False, -1),
        ("confirmed_at", "timestamptz", False, -1)),
    "subscription_device_effects": (
        ("bulk_id", "uuid", False, -1), ("bulk_recipient_id", "uuid", False, -1),
        ("bulk_part_index", "int2", False, -1)),
}
KEYS = (
    ("subscription_bulk_batches", "subscription_bulk_batches_pkey", "p", ("id",)),
    ("subscription_bulk_batches", "uq_subscription_bulk_scope", "u", ("tenant_id", "id")),
    ("subscription_bulk_batches", "uq_subscription_bulk_version", "u", ("tenant_id", "id", "run_version")),
    ("subscription_bulk_recipients", "subscription_bulk_recipients_pkey", "p", ("id",)),
    ("subscription_bulk_recipients", "uq_subscription_bulk_recipient_id", "u", ("tenant_id", "id")),
    ("subscription_bulk_recipients", "uq_subscription_bulk_recipient_scope", "u", ("tenant_id", "bulk_id", "id")),
    ("subscription_bulk_recipients", "uq_subscription_bulk_recipient_key", "u", ("tenant_id", "bulk_id", "row_key")),
    ("subscription_bulk_recipients", "uq_subscription_bulk_recipient_order", "u", ("tenant_id", "bulk_id", "ordinal")),
    ("subscription_bulk_requests", "subscription_bulk_requests_pkey", "p", ("id",)),
    ("subscription_bulk_requests", "uq_subscription_bulk_request_id", "u", ("tenant_id", "id")),
    ("subscription_bulk_requests", "uq_subscription_bulk_request_key", "u", ("tenant_id", "idempotency_key")),
    ("subscription_device_effects", "uq_subscription_device_effect_bulk_part", "u", ("tenant_id", "bulk_recipient_id", "bulk_part_index")),
)
FOREIGN_KEYS = (
    ("subscription_bulk_batches", "subscription_bulk_batches_tenant_id_fkey", ("tenant_id",), "tenants", ("id",)),
    ("subscription_bulk_batches", "fk_subscription_bulk_owner", ("tenant_id", "owner_account_id"), "tenant_memberships", ("tenant_id", "account_id")),
    ("subscription_bulk_recipients", "fk_subscription_bulk_recipient_batch", ("tenant_id", "bulk_id"), "subscription_bulk_batches", ("tenant_id", "id")),
    ("subscription_bulk_recipients", "fk_subscription_bulk_recipient_version", ("tenant_id", "bulk_id", "run_version"), "subscription_bulk_batches", ("tenant_id", "id", "run_version")),
    ("subscription_bulk_recipients", "fk_subscription_bulk_recipient_device", ("tenant_id", "device_id", "generation"), "subscription_devices", ("tenant_id", "id", "generation")),
    ("subscription_bulk_recipients", "fk_subscription_bulk_recipient_template", ("tenant_id", "template_id"), "subscription_chatbot_templates", ("tenant_id", "id")),
    ("subscription_bulk_requests", "subscription_bulk_requests_tenant_id_fkey", ("tenant_id",), "tenants", ("id",)),
    ("subscription_bulk_requests", "fk_subscription_bulk_request_actor", ("tenant_id", "actor_account_id"), "tenant_memberships", ("tenant_id", "account_id")),
    ("subscription_bulk_requests", "fk_subscription_bulk_request_batch", ("tenant_id", "bulk_id"), "subscription_bulk_batches", ("tenant_id", "id")),
    ("subscription_device_effects", "fk_subscription_device_effect_bulk", ("tenant_id", "bulk_id"), "subscription_bulk_batches", ("tenant_id", "id")),
    ("subscription_device_effects", "fk_subscription_device_effect_bulk_recipient", ("tenant_id", "bulk_id", "bulk_recipient_id"), "subscription_bulk_recipients", ("tenant_id", "bulk_id", "id")),
)
CHECKS = (
    ("subscription_bulk_batches", "subscription_bulk_values", ("version > 0", "^[a-f0-9]{64}$", "'pending'", "'running'", "'completed'", "'failed'", "'uncertain'", "'cancelled'", "interval_seconds >= 10", "interval_seconds <= 60", "jsonb_typeof(snapshot)", "1048576")),
    ("subscription_bulk_batches", "subscription_bulk_terminal", ("'pending'", "'running'", "finished_at is null", "'completed'", "'failed'", "'uncertain'", "'cancelled'", "finished_at is not null")),
    ("subscription_bulk_batches", "subscription_bulk_admission", ("'running'", "first_admitted_at is not null", "first_admitted_at is null", "last_admitted_at is null", "last_admitted_at is not null", "last_admitted_at >= first_admitted_at")),
    ("subscription_bulk_batches", "subscription_bulk_cancellation", ("'cancelled'", "cancelled_at is not null", "cancelled_at is null", "cancelled_at >= confirmed_at")),
    ("subscription_bulk_recipients", "subscription_bulk_recipient_values", ("^[a-f0-9]{64}$", "plan_hash", "ordinal >= 1", "ordinal <= 500", "^[1-9][0-9]{7,14}$", "length", "name", "settings_revision > 0", "permission_version >= 0", "jsonb_typeof(context)", "jsonb_typeof(origins)", "jsonb_typeof(plan)", "jsonb_array_length(plan) >= 1", "jsonb_array_length(plan) <= 3", "131072", "'pending'", "'running'", "'sent'", "'failed'", "'uncertain'", "'cancelled'")),
    ("subscription_bulk_recipients", "subscription_bulk_recipient_source", ("'manual'", "'catalog'", "origins = '[]'::jsonb", "manual_consent", "not manual_consent", "jsonb_array_length(origins) >= 1", "jsonb_array_length(origins) <= 101")),
    ("subscription_bulk_recipients", "subscription_bulk_recipient_template", ("template_id is null", "template_revision is null", "template_id is not null", "template_revision is not null", "template_revision > 0")),
    ("subscription_bulk_recipients", "subscription_bulk_recipient_terminal", ("'pending'", "'running'", "finished_at is null", "'sent'", "'failed'", "'uncertain'", "'cancelled'", "finished_at is not null")),
    ("subscription_bulk_recipients", "subscription_bulk_recipient_admission", ("'running'", "'sent'", "'uncertain'", "admitted_at is not null")),
    ("subscription_bulk_requests", "subscription_bulk_request_values", ("'review'", "'cancel'", "'prepared'", "'succeeded'", "^[a-f0-9]{64}$", "jsonb_typeof(result)", "4194304")),
    ("subscription_bulk_requests", "subscription_bulk_request_scope", ("'review'", "'cancel'", "'prepared'", "'succeeded'", "bulk_id is null", "bulk_id is not null")),
    ("subscription_bulk_requests", "subscription_bulk_request_review", ("'review'", "'cancel'", "review_version is not null", "^[a-f0-9]{64}$", "jsonb_typeof(snapshot)", "67108864", "jsonb_typeof(preview)", "1048576", "expires_at is not null", "expires_at > created_at", "expires_at <=", "00:02:00", "'prepared'", "result is null", "confirmed_at is null", "'succeeded'", "result is not null", "confirmed_at is not null", "snapshot is null", "preview is null", "review_version is null", "expires_at is null")),
    ("subscription_device_events", "subscription_device_events_kind_check", ("'campaign'", "'scheduled'", "'bulk'")),
    ("subscription_device_effects", "subscription_device_effects_type_check", ("'chatbot_action'", "'campaign_action'", "'scheduled_action'", "'bulk_action'")),
    ("subscription_device_effects", "subscription_device_effect_bulk_scope", ("'bulk_action'", "purpose", "bulk_id is not null", "bulk_recipient_id is not null", "bulk_part_index is not null", "bulk_part_index >= 0", "bulk_part_index <= 2", "bulk_id is null", "bulk_recipient_id is null", "bulk_part_index is null")),
)
INDEXES = (
    ("subscription_bulk_batches", "ix_subscription_bulk_list", ("tenant_id", "created_at", "id"), ()),
    ("subscription_bulk_batches", "ix_subscription_bulk_pending", ("tenant_id", "next_admission_at", "confirmed_at", "id"), ("'pending'", "'running'")),
    ("subscription_bulk_recipients", "ix_subscription_bulk_recipient_pending", ("tenant_id", "bulk_id", "ordinal", "device_id"), ("'pending'", "admitted_at is null")),
    ("subscription_bulk_recipients", "ix_subscription_bulk_recipient_rate", ("tenant_id", "admitted_at"), ("admitted_at is not null",)),
    ("subscription_bulk_recipients", "ix_subscription_bulk_recipient_contact", ("tenant_id", "phone", "status"), ()),
    ("subscription_bulk_recipients", "ix_subscription_bulk_recipient_history", ("tenant_id", "finished_at", "id"), ("'sent'",)),
    ("subscription_bulk_requests", "ix_subscription_bulk_request_actor", ("tenant_id", "actor_account_id", "created_at", "id"), ()),
)


def _catalog_contract(template, old_rows, new_rows):
    """Reuse a fixed catalog predicate without weakening or silently replacing it."""
    old = scheduled._values(old_rows)
    if template.count(old) != 1:
        raise RuntimeError("bulk_catalog_predicate_template_changed")
    return template.replace(old, scheduled._values(new_rows), 1)


TABLE_CONTRACT = _catalog_contract(scheduled.TABLE_CONTRACT, scheduled.SUPPORT_UPDATES.items(), SUPPORT_UPDATES.items())
COLUMN_CONTRACT = _catalog_contract(scheduled.COLUMN_CONTRACT,
    ((table, *column) for table, columns in scheduled.COLUMNS.items() for column in columns),
    ((table, *column) for table, columns in COLUMNS.items() for column in columns))
KEY_CONTRACT = _catalog_contract(scheduled.KEY_CONTRACT, scheduled.KEYS, KEYS)
FOREIGN_KEY_CONTRACT = _catalog_contract(scheduled.FOREIGN_KEY_CONTRACT, scheduled.FOREIGN_KEYS, FOREIGN_KEYS)
CHECK_CONTRACT = _catalog_contract(scheduled.CHECK_CONTRACT, scheduled.CHECKS, CHECKS)
INDEX_CONTRACT = _catalog_contract(scheduled.INDEX_CONTRACT, scheduled.INDEXES, INDEXES)
# Every supported unique recipient index is already required by KEY_CONTRACT.
# An extra unique(phone), including an expression index, would reject an
# intentional duplicate row; the catalog must reject that drift as well.
RECIPIENT_UNIQUENESS_CONTRACT = """SELECT count(*)=5 FROM pg_catalog.pg_index
  WHERE indrelid=pg_catalog.to_regclass('public.subscription_bulk_recipients') AND indisunique"""
CONTRACT_QUERY = "SELECT " + " AND ".join(
    "COALESCE((" + query + "),false)" for query in (
        PREVIOUS_CONTRACT, TABLE_CONTRACT, COLUMN_CONTRACT, KEY_CONTRACT,
        FOREIGN_KEY_CONTRACT, CHECK_CONTRACT, INDEX_CONTRACT, RECIPIENT_UNIQUENESS_CONTRACT)
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
                "application_name": "orion-subscription-bulk-schema-wait",
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
    return {**result, "status": "subscription_bulk_schema_ready", "schema_marker": SCHEMA_MARKER}


def main():
    try:
        result = asyncio.run(wait_for_schema(Settings.from_environment()))
    except Exception as error:
        code = str(error) if isinstance(error, Stop) and str(error) in SAFE_ERRORS else "subscription_bulk_schema_check_failed"
        print(json.dumps({"ok": False, "error": code}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
