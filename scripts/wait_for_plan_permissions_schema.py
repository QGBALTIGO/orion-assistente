"""Read-only worker gate; no jobs run before atomic initial rights are ready."""
import asyncio
import json
import asyncpg
from scripts.wait_for_agenda_schema import Settings, Stop
from scripts.wait_for_onboarding_schema import wait_for_schema as wait_previous, SAFE_ERRORS

SCHEMA_MARKER = "0050_plan_permissions"


async def probe_schema(settings, timeout):
    connection = None
    try:
        connection = await asyncpg.connect(settings.dsn, timeout=timeout, command_timeout=timeout,
            server_settings={"default_transaction_read_only": "on", "search_path": "pg_catalog",
                "application_name": "orion-plan-permissions-schema-wait"})
        role = await connection.fetchrow("""SELECT current_user,session_user,
            EXISTS(SELECT 1 FROM pg_roles WHERE rolname=current_user AND NOT rolsuper
                AND NOT rolbypassrls AND NOT rolcreaterole AND NOT rolcreatedb) AS restricted""")
        if role["current_user"] != "app_runtime" or role["session_user"] != "app_runtime" or role["restricted"] is not True:
            raise Stop("runtime_role_required")
        exists = await connection.fetchval("SELECT to_regprocedure('public.orion_plan_release_ready()') IS NOT NULL")
        return exists and await connection.fetchval("SELECT public.orion_plan_release_ready()") is True
    finally:
        if connection is not None:
            connection.terminate()


async def wait_for_schema(settings, **kwargs):
    result = await wait_previous(settings, probe=kwargs.pop("probe", probe_schema), **kwargs)
    return {**result, "status": "plan_permissions_schema_ready", "schema_marker": SCHEMA_MARKER}


def main():
    try:
        result = asyncio.run(wait_for_schema(Settings.from_environment()))
    except Exception as error:
        code = str(error) if isinstance(error, Stop) and str(error) in SAFE_ERRORS else "plan_permissions_schema_check_failed"
        print(json.dumps({"ok": False, "error": code})); return 1
    print(json.dumps(result)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
