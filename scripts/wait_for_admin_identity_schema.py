"""Read-only runtime metadata barrier for the separate 0030 admin identity release."""
import asyncio
import json

import asyncpg
from scripts import wait_for_communication_schema as previous
from scripts.wait_for_agenda_schema import Settings, Stop, SAFE_ERRORS

SCHEMA_MARKER='0030_legacy_admin_identity'
TABLES,FUNCTIONS=previous.TABLES,previous.FUNCTIONS
SCHEMA_QUERY=previous.SCHEMA_QUERY+""",
 (SELECT count(*)=5 FROM (VALUES
  ('auth_provider','varchar',true),('legacy_username','varchar',false),
  ('legacy_credential_digest','bpchar',false),('email','varchar',false),('password_hash','varchar',false))
  AS expected(col,typ,required)
  JOIN pg_catalog.pg_namespace n ON n.nspname='public'
  JOIN pg_catalog.pg_class c ON c.relnamespace=n.oid AND c.relname='panel_accounts'
  JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid AND a.attname=expected.col
   AND NOT a.attisdropped AND a.attnotnull=expected.required
  JOIN pg_catalog.pg_type t ON t.oid=a.atttypid AND t.typname=expected.typ)
 AND EXISTS(SELECT 1 FROM pg_catalog.pg_constraint
  WHERE conrelid=pg_catalog.to_regclass('public.panel_accounts')
   AND conname='ck_panel_account_auth_identity' AND contype='c' AND convalidated)
 AND EXISTS(SELECT 1 FROM pg_catalog.pg_index i
  JOIN pg_catalog.pg_class c ON c.oid=i.indexrelid
  WHERE c.relname='uq_panel_account_legacy_admin_identity'
   AND i.indrelid=pg_catalog.to_regclass('public.panel_accounts')
   AND i.indisvalid AND i.indisready AND i.indisunique AND i.indnkeyatts=1)
 AND EXISTS(SELECT 1 FROM pg_catalog.pg_proc p
  WHERE p.oid=pg_catalog.to_regprocedure('public.inbox_operator_version(uuid,uuid)')
   AND pg_catalog.strpos(pg_catalog.pg_get_functiondef(p.oid),'app.legacy_admin_digest')>0)
 AS admin_identity_ready
"""


async def probe_schema(settings,timeout):
    connection=None
    try:
        milliseconds=str(max(1,int(timeout*1000)))
        connection=await asyncpg.connect(settings.dsn,timeout=timeout,command_timeout=timeout,
            server_settings={'default_transaction_read_only':'on','search_path':'pg_catalog',
                'statement_timeout':milliseconds,'lock_timeout':milliseconds,
                'application_name':'orion-admin-identity-schema-wait'})
        row=await connection.fetchrow(SCHEMA_QUERY,list(TABLES),list(FUNCTIONS))
        if row['runtime_role']!='app_runtime' or row['authenticated_role']!='app_runtime' or row['restricted_role'] is not True:
            raise Stop('runtime_role_required')
        return all(row[field] is True for field in ('tables_ready','functions_ready','consent_ready',
            'agenda_ready','inbox_ready','communication_ready','admin_identity_ready'))
    finally:
        if connection is not None:connection.terminate()


async def wait_for_schema(settings,**kwargs):
    result=await previous.wait_for_schema(settings,probe=kwargs.pop('probe',probe_schema),**kwargs)
    return {**result,'status':'admin_identity_schema_ready','schema_marker':SCHEMA_MARKER}


def main():
    try:result=asyncio.run(wait_for_schema(Settings.from_environment()))
    except Exception as error:
        code=str(error) if isinstance(error,Stop) and str(error) in SAFE_ERRORS else 'admin_identity_schema_check_failed'
        print(json.dumps({'ok':False,'error':code}));return 1
    print(json.dumps(result));return 0


if __name__=='__main__':raise SystemExit(main())
