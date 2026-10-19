"""Read-only runtime gate. No migrations, customer reads, or transport calls."""
import asyncio
import json
from scripts import wait_for_agenda_schema as gate

QUERY = """
SELECT current_user AS runtime_role, EXISTS (
 SELECT 1 FROM pg_catalog.pg_class c
 JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
 JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid
 JOIN pg_catalog.pg_type t ON t.oid=a.atttypid
 JOIN pg_catalog.pg_namespace tn ON tn.oid=t.typnamespace
 WHERE n.nspname='public' AND c.relname='appointment_notices' AND c.relkind='r'
   AND c.relrowsecurity AND c.relforcerowsecurity
   AND a.attname='snapshot' AND a.attnum>0 AND NOT a.attisdropped AND a.attnotnull
   AND tn.nspname='pg_catalog' AND t.typname='jsonb'
) AS ready
"""

async def probe(settings, timeout):
    connection = None
    try:
        ms = str(max(1, int(timeout * 1000)))
        connection = await gate.asyncpg.connect(settings.dsn, timeout=timeout, command_timeout=timeout,
            server_settings={'default_transaction_read_only':'on','search_path':'pg_catalog',
                             'statement_timeout':ms,'lock_timeout':ms,'application_name':'orion-notice-schema-wait'})
        row = await connection.fetchrow(QUERY)
        if row['runtime_role'] != 'app_runtime':
            raise gate.Stop('runtime_role_required')
        return bool(row['ready'])
    finally:
        if connection is not None:
            connection.terminate()


def main():
    try:
        asyncio.run(gate.wait_for_schema(gate.Settings.from_environment(), probe=probe))
    except Exception as error:
        code = str(error) if isinstance(error,gate.Stop) and str(error) in gate.SAFE_ERRORS else 'notice_schema_check_failed'
        print(json.dumps({'ok':False,'error':code}))
        return 1
    print(json.dumps({'ok':True,'status':'notice_schema_ready'}))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
