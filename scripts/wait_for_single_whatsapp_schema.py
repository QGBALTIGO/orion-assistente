"""Read-only runtime barrier for the single WhatsApp binding release.

Uses only app_runtime and PostgreSQL catalogs. Never migrates, reads customer
rows, starts a connection or invokes the provider.
"""
import asyncio
import json

import asyncpg
from scripts import wait_for_agenda_schema as previous
from scripts.wait_for_agenda_schema import Settings, Stop, SAFE_ERRORS

SCHEMA_MARKER = "0044_single_whatsapp_connection"

# Shared with the privileged release verifier; the runtime role can inspect
# these catalogs without gaining access to credentials or account data.
GUARDS_QUERY = """
 SELECT p.prosecdef=false AND p.pronargs=0 AND p.prorettype='pg_catalog.trigger'::regtype
   AND pg_catalog.pg_get_userbyid(p.proowner)='app_migrator'
   AND EXISTS(SELECT 1 FROM unnest(p.proconfig) c
       WHERE replace(c,' ','')='search_path=pg_catalog,public')
   AND (SELECT count(*)=3 FROM (VALUES
      ('qr_single_connection_insert',5,ARRAY[]::text[]),
      ('qr_single_connection_activate',19,ARRAY['enabled','assistant_enabled']::text[]),
      ('qr_single_connection_claim',19,ARRAY['lease_owner','fence']::text[])) e(name,kind,cols)
      JOIN pg_catalog.pg_trigger t ON t.tgname=e.name
       AND t.tgrelid='public.qr_channels'::regclass AND NOT t.tgisinternal
       AND t.tgfoid=p.oid AND t.tgenabled='O' AND t.tgtype=e.kind
      WHERE ARRAY(SELECT a.attname::text FROM unnest(t.tgattr::smallint[])
         WITH ORDINALITY v(num,pos) JOIN pg_catalog.pg_attribute a
         ON a.attrelid=t.tgrelid AND a.attnum=v.num ORDER BY v.pos)=e.cols)
 AS ready FROM pg_catalog.pg_proc p
 WHERE p.oid=pg_catalog.to_regprocedure('public.qr_single_connection_guard()')
"""

SCHEMA_QUERY = """
 SELECT current_user AS runtime_role, session_user AS authenticated_role,
   EXISTS(SELECT 1 FROM pg_catalog.pg_roles WHERE rolname=current_user
      AND NOT rolsuper AND NOT rolcreaterole AND NOT rolbypassrls) AS restricted_role,
   EXISTS(SELECT 1 FROM pg_catalog.pg_attribute a
      WHERE a.attrelid=pg_catalog.to_regclass('public.tenants')
       AND a.attname='whatsapp_channel_id' AND a.attnum>0 AND NOT a.attisdropped
       AND NOT a.attnotnull AND a.atttypid='pg_catalog.uuid'::regtype) AS pointer_ready,
   EXISTS(SELECT 1 FROM pg_catalog.pg_constraint c
      WHERE c.conrelid=pg_catalog.to_regclass('public.tenants')
       AND c.confrelid=pg_catalog.to_regclass('public.qr_channels')
       AND c.conname='fk_tenant_whatsapp_channel' AND c.contype='f'
       AND c.convalidated AND NOT c.condeferrable AND c.confdeltype='r') AS binding_ready,
   EXISTS(SELECT 1 FROM pg_catalog.pg_index i JOIN pg_catalog.pg_class c ON c.oid=i.indexrelid
      WHERE c.relname='ix_tenant_whatsapp_channel'
       AND i.indrelid=pg_catalog.to_regclass('public.tenants')
       AND i.indisvalid AND i.indisready AND i.indisunique AND i.indnkeyatts=1
       AND i.indpred IS NOT NULL) AS index_ready,
   (SELECT count(*)=2 FROM pg_catalog.pg_constraint c
      WHERE c.conrelid=pg_catalog.to_regclass('public.qr_channels') AND c.contype='c'
       AND c.convalidated AND c.conname IN
       ('ck_qr_channel_senders','ck_qr_channel_private_policy')) AS policy_ready,
   COALESCE((""" + GUARDS_QUERY + """),false) AS guards_ready,
   pg_catalog.has_column_privilege(current_user,'public.tenants','whatsapp_channel_id','SELECT')
    AND NOT pg_catalog.has_column_privilege(current_user,'public.tenants','whatsapp_channel_id','UPDATE') AS grants_ready
"""


async def probe_schema(settings, timeout):
    connection = None
    try:
        milliseconds = str(max(1, int(timeout * 1000)))
        connection = await asyncpg.connect(settings.dsn, timeout=timeout, command_timeout=timeout,
            server_settings={"default_transaction_read_only": "on", "search_path": "pg_catalog",
                "statement_timeout": milliseconds, "lock_timeout": milliseconds,
                "application_name": "orion-single-whatsapp-schema-wait"})
        row = await connection.fetchrow(SCHEMA_QUERY)
        if (row["runtime_role"] != "app_runtime" or row["authenticated_role"] != "app_runtime"
                or row["restricted_role"] is not True):
            raise Stop("runtime_role_required")
        return all(row[field] is True for field in (
            "pointer_ready", "binding_ready", "index_ready", "policy_ready", "guards_ready", "grants_ready"))
    finally:
        if connection is not None:
            connection.terminate()


async def wait_for_schema(settings, **kwargs):
    result = await previous.wait_for_schema(settings, probe=kwargs.pop("probe", probe_schema), **kwargs)
    return {**result, "status": "single_whatsapp_schema_ready", "schema_marker": SCHEMA_MARKER}


def main():
    try:
        result = asyncio.run(wait_for_schema(Settings.from_environment()))
    except Exception as error:
        code = str(error) if isinstance(error, Stop) and str(error) in SAFE_ERRORS else "single_whatsapp_schema_check_failed"
        print(json.dumps({"ok": False, "error": code}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
