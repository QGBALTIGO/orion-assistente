"""Read-only deployment gate: runtime metadata only, no messages or user rows."""
import asyncio
import json
import asyncpg
from scripts import wait_for_onboarding_schema as previous
from scripts.wait_for_agenda_schema import Settings, Stop

SCHEMA_MARKER = "0028_workflow_drafts"
TABLES = previous.TABLES + ("inbox_labels", "inbox_customer_labels", "inbox_quick_replies",
    "inbox_controls", "inbox_control_events", "inbox_channel_links", "inbox_messages",
    "inbox_provider_receipts", "inbox_reads")
FUNCTIONS = previous.FUNCTIONS + (
    "public.inbox_channel_contact_allowed(uuid,uuid,text)",
    "public.inbox_operator_version(uuid,uuid)",
    "public.inbox_meta_guard(uuid,uuid,boolean)")
SCHEMA_QUERY = previous.SCHEMA_QUERY.replace(
    "CASE WHEN p.proname IN", "CASE WHEN p.proname='inbox_operator_version' THEN 'pg_catalog.int4'::pg_catalog.regtype WHEN p.proname IN") + """,
  (SELECT count(*)=2 FROM pg_catalog.pg_attribute a
    JOIN pg_catalog.pg_class c ON c.oid=a.attrelid
    JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='public' AND c.relname='workflow_definitions'
      AND a.attnum>0 AND NOT a.attisdropped AND a.attnotnull
      AND ((a.attname='version' AND a.atttypid='pg_catalog.int4'::pg_catalog.regtype)
        OR (a.attname='updated_at' AND a.atttypid='pg_catalog.timestamptz'::pg_catalog.regtype))) AS inbox_ready
"""

async def probe_schema(settings, timeout):
    connection = None
    try:
        milliseconds = str(max(1, int(timeout * 1000)))
        connection = await asyncpg.connect(settings.dsn, timeout=timeout, command_timeout=timeout,
            server_settings={"default_transaction_read_only": "on", "search_path": "pg_catalog",
                "statement_timeout": milliseconds, "lock_timeout": milliseconds,
                "application_name": "orion-onboarding-schema-wait"})
        row = await connection.fetchrow(SCHEMA_QUERY, list(TABLES), list(FUNCTIONS))
        if (row["runtime_role"] != "app_runtime" or row["authenticated_role"] != "app_runtime"
                or row["restricted_role"] is not True):
            raise Stop("runtime_role_required")
        return all(row[field] is True for field in ("tables_ready", "functions_ready", "consent_ready", "agenda_ready", "inbox_ready"))
    finally:
        if connection is not None:
            connection.terminate()


async def wait_for_schema(settings, **kwargs):
    result = await previous.wait_for_schema(settings, probe=kwargs.pop("probe", probe_schema), **kwargs)
    return {**result, "status": "inbox_schema_ready", "schema_marker": SCHEMA_MARKER}


def main():
    try:
        result = asyncio.run(wait_for_schema(Settings.from_environment()))
    except Exception as error:
        code = str(error) if isinstance(error, Stop) and str(error) in previous.SAFE_ERRORS else "inbox_schema_check_failed"
        print(json.dumps({"ok": False, "error": code})); return 1
    print(json.dumps(result)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
