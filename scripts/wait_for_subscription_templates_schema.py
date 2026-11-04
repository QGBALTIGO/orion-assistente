"""Read-only barrier for the private subscription template library extension."""
import asyncio
import json

import asyncpg
from scripts import wait_for_agenda_schema as previous
from scripts.wait_for_agenda_schema import Settings, Stop, SAFE_ERRORS
from scripts.wait_for_subscription_chatbot_schema import CONTRACT_QUERY as PREVIOUS_CONTRACT

SCHEMA_MARKER = "0064_subscription_templates"

CONTRACT_QUERY = """
 SELECT COALESCE((""" + PREVIOUS_CONTRACT + """),false)
   AND (SELECT count(*)=3 FROM pg_catalog.pg_attribute a
     WHERE a.attrelid=pg_catalog.to_regclass('public.subscription_chatbot_templates')
       AND NOT a.attisdropped AND (
         (a.attname='followup_body' AND a.atttypid='pg_catalog.text'::regtype AND a.attnotnull)
         OR (a.attname='location' AND a.atttypid='pg_catalog.jsonb'::regtype)
         OR (a.attname='file_name' AND a.atttypid='pg_catalog.varchar'::regtype AND a.atttypmod=124)))
   AND (SELECT count(*)=4 FROM pg_catalog.pg_constraint c
     WHERE c.conrelid=pg_catalog.to_regclass('public.subscription_chatbot_templates')
       AND c.contype='c' AND c.convalidated AND c.conname IN (
         'subscription_chatbot_templates_asset_kind_check',
         'subscription_chatbot_templates_followup_check',
         'subscription_chatbot_templates_filename_check',
         'subscription_chatbot_templates_location_check'))
   AND EXISTS (SELECT 1 FROM pg_catalog.pg_constraint c
     WHERE c.conrelid=pg_catalog.to_regclass('public.subscription_chatbot_templates')
       AND c.conname='subscription_chatbot_templates_kind_check' AND c.convalidated
       AND pg_catalog.pg_get_constraintdef(c.oid) LIKE '%audio%'
       AND pg_catalog.pg_get_constraintdef(c.oid) LIKE '%location%')
   AND EXISTS (SELECT 1 FROM pg_catalog.pg_constraint c
     WHERE c.conrelid=pg_catalog.to_regclass('public.subscription_chatbot_assets')
       AND c.conname='subscription_chatbot_assets_kind_check' AND c.convalidated
       AND pg_catalog.pg_get_constraintdef(c.oid) LIKE '%audio%')
   AND EXISTS (SELECT 1 FROM pg_catalog.pg_constraint c
     WHERE c.conrelid=pg_catalog.to_regclass('public.subscription_chatbot_jobs')
       AND c.conname='subscription_chatbot_jobs_kind_check' AND c.convalidated
       AND pg_catalog.pg_get_constraintdef(c.oid) LIKE '%template_create%'
       AND pg_catalog.pg_get_constraintdef(c.oid) LIKE '%template_update%'
       AND pg_catalog.pg_get_constraintdef(c.oid) LIKE '%template_delete%'
       AND pg_catalog.pg_get_constraintdef(c.oid) LIKE '%template_asset%')
 AS ready
"""

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
                "application_name": "orion-subscription-templates-schema-wait",
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
    return {**result, "status": "subscription_templates_schema_ready", "schema_marker": SCHEMA_MARKER}


def main():
    try:
        result = asyncio.run(wait_for_schema(Settings.from_environment()))
    except Exception as error:
        code = str(error) if isinstance(error, Stop) and str(error) in SAFE_ERRORS else "subscription_templates_schema_check_failed"
        print(json.dumps({"ok": False, "error": code}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
