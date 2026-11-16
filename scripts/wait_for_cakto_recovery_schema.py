"""Read-only 0073 barrier; never query financial data or expose credentials."""
import asyncio
import json

import asyncpg

from scripts import wait_for_agenda_schema as previous
from scripts import wait_for_subscription_group_add_schema as predecessor
from scripts.wait_for_agenda_schema import Settings, Stop, SAFE_ERRORS

SCHEMA_MARKER = "0073_cakto_recovery"
COLUMN_CONTRACT = """WITH expected(table_name,column_name,type_name,not_null,type_mod) AS (VALUES
 ('cakto_webhook_events','receipt_origin','varchar',true,28),
 ('orion_prepaid_pix_orders','provider_checked_at','timestamptz',false,-1),
 ('orion_prepaid_pix_orders','pix_code_enc','text',false,-1),
 ('orion_prepaid_pix_orders','pix_expires_at','timestamptz',false,-1),
 ('orion_direct_payment_attempts','provider_checked_at','timestamptz',false,-1),
 ('orion_cakto_bindings','provider_checked_at','timestamptz',false,-1))
 SELECT NOT EXISTS(SELECT 1 FROM expected e WHERE NOT EXISTS(
  SELECT 1 FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_type t ON t.oid=a.atttypid
  WHERE a.attrelid=pg_catalog.to_regclass('public.'||e.table_name)
   AND a.attname=e.column_name AND NOT a.attisdropped AND a.attnum>0
   AND t.typname=e.type_name AND a.attnotnull=e.not_null AND a.atttypmod=e.type_mod))"""
PROVENANCE_CONTRACT = """SELECT EXISTS(
 SELECT 1 FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_attrdef d
  ON d.adrelid=a.attrelid AND d.adnum=a.attnum
 WHERE a.attrelid=pg_catalog.to_regclass('public.cakto_webhook_events') AND a.attname='receipt_origin'
  AND pg_catalog.pg_get_expr(d.adbin,d.adrelid)='''webhook_hmac''::character varying')
 AND EXISTS(SELECT 1 FROM pg_catalog.pg_constraint c
  WHERE c.conrelid=pg_catalog.to_regclass('public.cakto_webhook_events')
   AND c.conname='ck_cakto_receipt_origin' AND c.contype='c' AND c.convalidated
   AND pg_catalog.regexp_replace(pg_catalog.regexp_replace(lower(pg_catalog.pg_get_expr(c.conbin,c.conrelid)),
    '::(character varying|text)(\\[\\])?','','g'),'[[:space:]()]','','g')=
    'receipt_origin=anyarray[''webhook_hmac'',''provider_read'']')
 AND EXISTS(SELECT 1 FROM pg_catalog.pg_constraint c
  WHERE c.conrelid=pg_catalog.to_regclass('public.orion_prepaid_pix_orders')
   AND c.conname='ck_prepaid_pix_resume_cipher' AND c.contype='c' AND c.convalidated
   AND pg_catalog.regexp_replace(lower(pg_catalog.pg_get_expr(c.conbin,c.conrelid)),
      '[[:space:]()]','','g')=
    'pix_code_encisnullandpix_expires_atisnullorpix_code_encisnotnullandpix_expires_atisnotnullandchar_lengthpix_code_enc>=100andchar_lengthpix_code_enc<=32768')"""
ROLE_CONTRACT = """WITH expected(table_name) AS (VALUES ('cakto_webhook_events'),
 ('orion_prepaid_pix_orders'),('orion_direct_payment_attempts'),('orion_cakto_bindings'))
 SELECT NOT EXISTS(SELECT 1 FROM expected e WHERE NOT EXISTS(
  SELECT 1 FROM pg_catalog.pg_class c WHERE c.oid=pg_catalog.to_regclass('public.'||e.table_name)
   AND c.relrowsecurity AND c.relforcerowsecurity
   AND has_table_privilege('app_support',c.oid,'SELECT')
   AND has_table_privilege('app_support',c.oid,'INSERT')
   AND has_table_privilege('app_support',c.oid,'UPDATE')
   AND NOT has_table_privilege('app_runtime',c.oid,'SELECT,INSERT,UPDATE,DELETE')))"""
INDEX_CONTRACT = """WITH expected(table_name,index_name,columns) AS (VALUES
 ('orion_prepaid_pix_orders','ix_prepaid_pix_provider_check',ARRAY['state','provider_checked_at','id']),
 ('orion_direct_payment_attempts','ix_direct_provider_check',ARRAY['state','provider_checked_at','id']),
 ('orion_cakto_bindings','ix_cakto_binding_provider_check',ARRAY['provider_checked_at','tenant_id']))
 SELECT NOT EXISTS(SELECT 1 FROM expected e WHERE NOT EXISTS(
  SELECT 1 FROM pg_catalog.pg_index i
  WHERE i.indrelid=pg_catalog.to_regclass('public.'||e.table_name)
   AND i.indexrelid=pg_catalog.to_regclass('public.'||e.index_name)
   AND i.indisvalid AND i.indisready AND NOT i.indisunique AND i.indpred IS NULL
   AND (SELECT array_agg(a.attname::text ORDER BY k.ordinality)
    FROM unnest(i.indkey) WITH ORDINALITY AS k(attnum,ordinality)
    JOIN pg_catalog.pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=k.attnum)=e.columns))"""
CONTRACT_QUERY = "SELECT " + " AND ".join("COALESCE((" + query + "),false)" for query in (
    predecessor.CONTRACT_QUERY, COLUMN_CONTRACT, PROVENANCE_CONTRACT, ROLE_CONTRACT, INDEX_CONTRACT)) + " AS ready"
SCHEMA_QUERY = """SELECT current_user AS runtime_role,session_user AS authenticated_role,
 EXISTS(SELECT 1 FROM pg_catalog.pg_roles WHERE rolname=current_user
  AND NOT rolsuper AND NOT rolcreaterole AND NOT rolbypassrls) AS restricted_role,
 COALESCE((""" + CONTRACT_QUERY + """),false) AS schema_ready"""


async def probe_schema(settings, timeout):
    connection = None
    try:
        milliseconds = str(max(1, int(timeout * 1000)))
        connection = await asyncpg.connect(settings.dsn, timeout=timeout, command_timeout=timeout,
            server_settings={"default_transaction_read_only":"on", "search_path":"pg_catalog",
                             "statement_timeout":milliseconds, "lock_timeout":milliseconds,
                             "application_name":"orion-cakto-recovery-schema-wait"})
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
    return {**result,"status":"cakto_recovery_schema_ready","schema_marker":SCHEMA_MARKER}


def main():
    try:
        result = asyncio.run(wait_for_schema(Settings.from_environment()))
    except Exception as error:
        code = str(error) if isinstance(error, Stop) and str(error) in SAFE_ERRORS else "cakto_recovery_schema_check_failed"
        print(json.dumps({"ok":False,"error":code}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
