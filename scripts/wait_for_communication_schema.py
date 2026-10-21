"""Read-only deployment gate: runtime metadata only, no messages or user rows."""
import asyncio
import json
import asyncpg
from scripts import wait_for_inbox_schema as previous
from scripts.wait_for_agenda_schema import Settings, Stop

SCHEMA_MARKER = "0029_communication_contract"
TABLES = previous.TABLES + ('communication_pending',)
FUNCTIONS = previous.FUNCTIONS
SCHEMA_QUERY = previous.SCHEMA_QUERY + """,
 (SELECT count(*)=14 FROM (VALUES
  ('inbound_events','envelope','jsonb'),('inbound_events','ingress_order','int8'),
  ('inbox_messages','envelope','jsonb'),('automation_jobs','presentation','jsonb'),
  ('customers','birth_month','int2'),('customers','birth_day','int2'),
  ('contact_consents','birthday_opt_in','bool'),('contact_consents','birthday_declined_at','timestamptz'),
  ('contact_consents','birthday_purpose_version','int4'),
  ('contact_consents','operational_updated_at','timestamptz'),('contact_consents','marketing_updated_at','timestamptz'),
  ('contact_consents','birthday_updated_at','timestamptz'),
  ('communication_pending','version','int4'),('communication_pending','status','text')) AS required(rel,col,typ)
  JOIN pg_catalog.pg_namespace n ON n.nspname='public'
  JOIN pg_catalog.pg_class c ON c.relnamespace=n.oid AND c.relname=required.rel
  JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid AND a.attname=required.col AND NOT a.attisdropped
  JOIN pg_catalog.pg_type t ON t.oid=a.atttypid AND t.typname=required.typ) AS communication_ready
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
        return all(row[field] is True for field in ("tables_ready", "functions_ready", "consent_ready", "agenda_ready", "inbox_ready", "communication_ready"))
    finally:
        if connection is not None:
            connection.terminate()


async def wait_for_schema(settings, **kwargs):
    result = await previous.wait_for_schema(settings, probe=kwargs.pop("probe", probe_schema), **kwargs)
    return {**result, "status": "communication_schema_ready", "schema_marker": SCHEMA_MARKER}


def main():
    try:
        result = asyncio.run(wait_for_schema(Settings.from_environment()))
    except Exception as error:
        code = str(error) if isinstance(error, Stop) and str(error) in previous.SAFE_ERRORS else "communication_schema_check_failed"
        print(json.dumps({"ok": False, "error": code})); return 1
    print(json.dumps(result)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
