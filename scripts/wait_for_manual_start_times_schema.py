"""Read-only worker barrier for the optional manual start-time policy.

The existing API pre-deploy owns the backup and migration. A new worker must
wait for that transaction before it can consume booking jobs.
"""
import asyncio
import json

import asyncpg
from scripts import wait_for_agenda_schema as previous
from scripts.wait_for_agenda_schema import Settings, Stop, SAFE_ERRORS

SCHEMA_MARKER = "0060_manual_start_times"

# This catalog-only contract is also used by the privileged release verifier.
# It does not read a tenant, a customer, a booking or a configured start time.
CONTRACT_QUERY = """
 SELECT c.relrowsecurity AND c.relforcerowsecurity
   AND pg_catalog.pg_get_userbyid(c.relowner)='app_migrator'
   AND pg_catalog.has_table_privilege('app_runtime',c.oid,'SELECT')
   AND pg_catalog.has_table_privilege('app_runtime',c.oid,'INSERT')
   AND pg_catalog.has_table_privilege('app_runtime',c.oid,'UPDATE')
   AND pg_catalog.has_table_privilege('app_runtime',c.oid,'DELETE')
   AND NOT EXISTS (
     SELECT 1 FROM pg_catalog.aclexplode(COALESCE(c.relacl,
       pg_catalog.acldefault('r',c.relowner))) acl WHERE acl.grantee=0)
   AND (SELECT count(*)=8 FROM (VALUES
     ('id','uuid'),('tenant_id','uuid'),('professional_id','uuid'),
     ('weekday','int2'),('mode','varchar'),('start_times','_time'),
     ('version','int4'),('updated_at','timestamptz')) expected(col,typ)
     JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid
       AND a.attname=expected.col AND a.attnum>0 AND NOT a.attisdropped
       AND a.attnotnull
     JOIN pg_catalog.pg_type t ON t.oid=a.atttypid AND t.typname=expected.typ)
   AND EXISTS (SELECT 1 FROM pg_catalog.pg_policy p
     WHERE p.polrelid=c.oid AND p.polcmd='*'
       AND (SELECT oid FROM pg_catalog.pg_roles WHERE rolname='app_runtime')=ANY(p.polroles)
       AND p.polqual IS NOT NULL AND p.polwithcheck IS NOT NULL)
   AND EXISTS (SELECT 1 FROM pg_catalog.pg_constraint k
     WHERE k.conrelid=c.oid AND k.contype='u' AND k.convalidated
       AND ARRAY(SELECT a.attname::text FROM unnest(k.conkey) WITH ORDINALITY x(num,pos)
         JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid AND a.attnum=x.num
         ORDER BY x.pos)=ARRAY['tenant_id','professional_id','weekday'])
   AND EXISTS (SELECT 1 FROM pg_catalog.pg_constraint k
     WHERE k.conrelid=c.oid AND k.contype='f' AND k.convalidated
       AND k.confrelid=pg_catalog.to_regclass('public.professionals')
       AND cardinality(k.conkey)=2)
   AND EXISTS (SELECT 1 FROM pg_catalog.pg_proc p
     WHERE p.oid=pg_catalog.to_regprocedure('public.orion_valid_manual_start_times(time without time zone[])')
       AND NOT p.prosecdef AND p.provolatile='i'
       AND p.prorettype='pg_catalog.bool'::regtype)
 AS ready FROM pg_catalog.pg_class c
 WHERE c.oid=pg_catalog.to_regclass('public.professional_start_policies')
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
        connection = await asyncpg.connect(
            settings.dsn, timeout=timeout, command_timeout=timeout,
            server_settings={"default_transaction_read_only": "on", "search_path": "pg_catalog",
                             "statement_timeout": milliseconds, "lock_timeout": milliseconds,
                             "application_name": "orion-manual-start-schema-wait"},
        )
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
    return {**result, "status": "manual_start_times_schema_ready", "schema_marker": SCHEMA_MARKER}


def main():
    try:
        result = asyncio.run(wait_for_schema(Settings.from_environment()))
    except Exception as error:
        code = str(error) if isinstance(error, Stop) and str(error) in SAFE_ERRORS else "manual_start_times_schema_check_failed"
        print(json.dumps({"ok": False, "error": code}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
