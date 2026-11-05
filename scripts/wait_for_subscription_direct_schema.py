"""Read-only catalog barrier for immutable direct-message origins.

The complete published bulk contract remains mandatory. Only catalog metadata
is inspected, including defaults, exact origin domains and the compound FK.
"""
import asyncio
import json

import asyncpg

from scripts import wait_for_agenda_schema as previous
from scripts import wait_for_subscription_bulk_schema as bulk
from scripts import wait_for_subscription_scheduled_messages_schema as catalog
from scripts.wait_for_agenda_schema import Settings, Stop, SAFE_ERRORS

SCHEMA_MARKER = "0071_subscription_direct"
PREVIOUS_CONTRACT = bulk.CONTRACT_QUERY
PRIVATE_TABLES = ("subscription_bulk_batches", "subscription_bulk_requests")
COLUMNS = {table: (("origin", "varchar", True, 12),) for table in PRIVATE_TABLES}
KEYS = (("subscription_bulk_batches", "uq_subscription_bulk_batch_origin", "u",
         ("tenant_id", "id", "origin")),)
FOREIGN_KEYS = (("subscription_bulk_requests", "fk_subscription_bulk_request_origin",
                 ("tenant_id", "bulk_id", "origin"), "subscription_bulk_batches",
                 ("tenant_id", "id", "origin")),)
CHECKS = (
    ("subscription_bulk_batches", "subscription_bulk_batch_origin"),
    ("subscription_bulk_requests", "subscription_bulk_request_origin"),
)


def _catalog_contract(template, old_rows, new_rows):
    old = catalog._values(old_rows)
    if template.count(old) != 1:
        raise RuntimeError("direct_catalog_predicate_template_changed")
    return template.replace(old, catalog._values(new_rows), 1)


COLUMN_CONTRACT = _catalog_contract(catalog.COLUMN_CONTRACT,
    ((table, *column) for table, columns in catalog.COLUMNS.items() for column in columns),
    ((table, *column) for table, columns in COLUMNS.items() for column in columns))
KEY_CONTRACT = _catalog_contract(catalog.KEY_CONTRACT, catalog.KEYS, KEYS)
FOREIGN_KEY_CONTRACT = _catalog_contract(catalog.FOREIGN_KEY_CONTRACT, catalog.FOREIGN_KEYS, FOREIGN_KEYS)
DEFAULT_CONTRACT = """
 SELECT NOT EXISTS (SELECT 1 FROM (VALUES """ + catalog._values((table,) for table in PRIVATE_TABLES) + """) expected(name)
   WHERE NOT EXISTS (SELECT 1 FROM pg_catalog.pg_attribute a
     JOIN pg_catalog.pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
     WHERE a.attrelid=pg_catalog.to_regclass('public.'||expected.name)
       AND a.attname='origin' AND a.attnum>0 AND NOT a.attisdropped
       AND pg_catalog.pg_get_expr(d.adbin,d.adrelid)='''bulk''::character varying'))
"""
# PostgreSQL 16 deparses this VARCHAR IN guard to one equality/ANY expression.
# Compare the entire normalized guard: adding a third origin or an OR bypass
# must not pass simply because the two expected literals are still present.
CHECK_CONTRACT = """
 SELECT NOT EXISTS (SELECT 1 FROM (VALUES """ + catalog._values(CHECKS) + """) expected(table_name,name)
   WHERE NOT EXISTS (SELECT 1 FROM pg_catalog.pg_constraint c
     WHERE c.conrelid=pg_catalog.to_regclass('public.'||expected.table_name)
       AND c.conname=expected.name AND c.contype='c' AND c.convalidated
       AND regexp_replace(pg_catalog.pg_get_constraintdef(c.oid),'[[:space:]]','','g')=
         'CHECK(((origin)::text=ANY((ARRAY[''bulk''::charactervarying,''direct''::charactervarying])::text[])))'))
"""
CONTRACT_QUERY = "SELECT " + " AND ".join(
    "COALESCE((" + query + "),false)" for query in (
        PREVIOUS_CONTRACT, COLUMN_CONTRACT, KEY_CONTRACT,
        FOREIGN_KEY_CONTRACT, DEFAULT_CONTRACT, CHECK_CONTRACT)
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
                "application_name": "orion-subscription-direct-schema-wait",
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
    return {**result, "status": "subscription_direct_schema_ready", "schema_marker": SCHEMA_MARKER}


def main():
    try:
        result = asyncio.run(wait_for_schema(Settings.from_environment()))
    except Exception as error:
        code = str(error) if isinstance(error, Stop) and str(error) in SAFE_ERRORS else "subscription_direct_schema_check_failed"
        print(json.dumps({"ok": False, "error": code}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
