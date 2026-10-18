"""Read-only worker barrier for the additive onboarding/channel/automation schema.

Run with ``python -m scripts.wait_for_onboarding_schema`` before the new worker.
Only APP_DATABASE_URL/app_runtime is used. No migrations, function invocation,
customer reads, credential reads, grants or environment changes occur here.
The 0023 function is a transactional schema marker; app_runtime deliberately
cannot SELECT alembic_version, so this does not claim to read its revision row.
"""
import asyncio
import json
import time

import asyncpg

from scripts.wait_for_agenda_schema import Settings, Stop

WAIT_SECONDS = 300.0
POLL_SECONDS = 1.0
ATTEMPT_SECONDS = 3.0
SCHEMA_MARKER = "0023_automation_notice_guard"
TABLES = (
    "business_onboarding", "business_onboarding_operations", "business_onboarding_creations",
    "qr_channels", "automation_execution", "automation_contact_state", "automation_jobs",
)
FUNCTIONS = (
    "public.onboarding_update_identity(uuid,text,text)",
    "public.onboarding_activate_tenant(uuid)",
    "public.qr_channel_transaction_guard(uuid,uuid,uuid,uuid,bigint)",
    "public.automation_owner_active(uuid,uuid,integer)",
    "public.automation_notice_guard(uuid,uuid,uuid,uuid,bigint,text)",
)
SAFE_ERRORS = frozenset({
    "invalid_runtime_configuration", "runtime_role_required", "runtime_authentication_failed",
    "runtime_catalog_unavailable", "onboarding_schema_timeout", "onboarding_schema_check_failed",
})

# All inspected relations are pg_catalog. The named public objects are resolved
# as metadata OIDs; no public function is executed or public row selected.
SCHEMA_QUERY = """
WITH required_tables AS (SELECT unnest($1::text[]) AS name),
required_functions AS (SELECT unnest($2::text[]) AS signature),
tables_ready AS (
  SELECT bool_and(coalesce(c.relkind='r' AND c.relrowsecurity AND c.relforcerowsecurity
    AND pg_catalog.pg_get_userbyid(c.relowner)='app_migrator',false)) AS ready
  FROM required_tables wanted
  LEFT JOIN pg_catalog.pg_namespace n ON n.nspname='public'
  LEFT JOIN pg_catalog.pg_class c ON c.relnamespace=n.oid AND c.relname=wanted.name
), functions_ready AS (
  SELECT bool_and(coalesce(p.prokind='f' AND p.prosecdef
    AND p.prorettype=CASE WHEN p.proname IN ('onboarding_update_identity','onboarding_activate_tenant')
      THEN 'pg_catalog.void'::pg_catalog.regtype ELSE 'pg_catalog.bool'::pg_catalog.regtype END
    AND p.proconfig=ARRAY['search_path=pg_catalog, public']::text[]
    AND pg_catalog.pg_get_userbyid(p.proowner)='app_migrator'
    AND pg_catalog.has_function_privilege(current_user,p.oid,'EXECUTE')
    AND NOT EXISTS (SELECT 1 FROM pg_catalog.aclexplode(coalesce(p.proacl,
      pg_catalog.acldefault('f',p.proowner))) acl
      WHERE acl.grantee=0 AND acl.privilege_type='EXECUTE'),false)) AS ready
  FROM required_functions wanted
  LEFT JOIN pg_catalog.pg_proc p ON p.oid=pg_catalog.to_regprocedure(wanted.signature)
)
SELECT current_user AS runtime_role, session_user AS authenticated_role,
  coalesce((SELECT NOT (rolsuper OR rolbypassrls OR rolcreatedb OR rolcreaterole OR rolreplication)
    FROM pg_catalog.pg_roles WHERE rolname=current_user),false) AS restricted_role,
  (SELECT ready FROM tables_ready) AS tables_ready,
  (SELECT ready FROM functions_ready) AS functions_ready,
  EXISTS (
    SELECT 1 FROM pg_catalog.pg_attribute a
    JOIN pg_catalog.pg_class c ON c.oid=a.attrelid
    JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
    JOIN pg_catalog.pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum
    WHERE n.nspname='public' AND c.relname='contact_consents' AND c.relkind='r'
      AND a.attname='operational_opt_in' AND a.attnum>0 AND NOT a.attisdropped
      AND a.attnotnull AND a.atttypid='pg_catalog.bool'::pg_catalog.regtype
      AND pg_catalog.pg_get_expr(d.adbin,d.adrelid)='false'
  ) AS consent_ready,
  EXISTS (
    SELECT 1 FROM pg_catalog.pg_attribute a
    JOIN pg_catalog.pg_class c ON c.oid=a.attrelid
    JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='public' AND c.relname='appointment_history' AND c.relkind='r'
      AND a.attname='details' AND a.attnum>0 AND NOT a.attisdropped AND a.attnotnull
      AND a.atttypid='pg_catalog.jsonb'::pg_catalog.regtype
  ) AS agenda_ready
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
        return all(row[field] is True for field in ("tables_ready", "functions_ready", "consent_ready", "agenda_ready"))
    finally:
        if connection is not None:
            connection.terminate()


async def wait_for_schema(settings, *, clock=None, sleep=None, probe=None):
    clock, sleep, probe = clock or time.monotonic, sleep or asyncio.sleep, probe or probe_schema
    deadline = clock() + WAIT_SECONDS
    while (remaining := deadline - clock()) > 0:
        budget = min(ATTEMPT_SECONDS, remaining)
        try:
            async with asyncio.timeout(budget):
                if await probe(settings, budget):
                    return {"ok": True, "status": "onboarding_schema_ready", "schema_marker": SCHEMA_MARKER}
        except (asyncpg.InvalidAuthorizationSpecificationError, asyncpg.InvalidPasswordError,
                asyncpg.InvalidCatalogNameError):
            raise Stop("runtime_authentication_failed") from None
        except asyncpg.InsufficientPrivilegeError:
            raise Stop("runtime_catalog_unavailable") from None
        except (OSError, TimeoutError, asyncpg.PostgresError):
            pass
        if (remaining := deadline - clock()) > 0:
            await sleep(min(POLL_SECONDS, remaining))
    raise Stop("onboarding_schema_timeout")


def main():
    try:
        result = asyncio.run(wait_for_schema(Settings.from_environment()))
    except Exception as error:
        code = str(error) if isinstance(error, Stop) and str(error) in SAFE_ERRORS else "onboarding_schema_check_failed"
        print(json.dumps({"ok": False, "error": code}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
