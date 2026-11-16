"""Read-only startup barrier for the 0074 fixed-client appointment series.

Extends the published 0073 catalog/role guarantees without querying any
customer records, accepting session credentials or running DDL.
"""
import asyncio
import json

import asyncpg

from scripts.wait_for_agenda_schema import Settings, Stop, SAFE_ERRORS
from scripts.wait_for_agenda_schema import wait_for_schema as original_wait
from scripts import wait_for_cakto_recovery_schema as predecessor

SCHEMA_MARKER = "0074_fixed_client_series"
COLUMN_CONTRACT = """WITH required(table_name,column_name,type_name) AS (VALUES
 ('appointment_series','id','uuid'),
 ('appointment_series','tenant_id','uuid'),
 ('appointment_series','customer_id','uuid'),
 ('appointment_series','professional_id','uuid'),
 ('appointment_series','service_ids','uuid[]'),
 ('appointment_series','first_date','date'),
 ('appointment_series','weekday','smallint'),
 ('appointment_series','local_time','time without time zone'),
 ('appointment_series','interval_weeks','smallint'),
 ('appointment_series','occurrence_count','smallint'),
 ('appointment_series','idempotency_key','character varying(120)'),
 ('appointment_series','request_fingerprint','character varying(64)'),
 ('appointments','series_id','uuid'),
 ('appointments','series_index','smallint'))
 SELECT NOT EXISTS(SELECT 1 FROM required e WHERE NOT EXISTS(
 SELECT 1 FROM pg_catalog.pg_attribute a
 WHERE a.attrelid=pg_catalog.to_regclass('public.'||e.table_name)
  AND a.attname=e.column_name AND NOT a.attisdropped AND a.attnum>0
  AND pg_catalog.format_type(a.atttypid,a.atttypmod)=e.type_name))"""
ROLE_CONTRACT = """SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_class c
 WHERE c.oid=pg_catalog.to_regclass('public.appointment_series')
  AND c.relkind='r' AND c.relrowsecurity AND c.relforcerowsecurity
  AND pg_catalog.pg_get_userbyid(c.relowner)='app_migrator'
  AND pg_catalog.has_table_privilege('app_runtime',c.oid,'SELECT')
  AND pg_catalog.has_table_privilege('app_runtime',c.oid,'INSERT')
  AND pg_catalog.has_table_privilege('app_runtime',c.oid,'UPDATE')
  AND NOT pg_catalog.has_table_privilege('app_runtime',c.oid,'DELETE')
  AND NOT pg_catalog.has_table_privilege('app_support',c.oid,'SELECT'))
 AND EXISTS(SELECT 1 FROM pg_catalog.pg_policies
 WHERE schemaname='public' AND tablename='appointment_series'
  AND policyname='appointment_series_runtime')"""
CONSTRAINT_CONTRACT = """WITH expected(table_name,constraint_name,kind) AS (VALUES
 ('appointment_series','uq_appointment_series_scope','u'),
 ('appointment_series','uq_appointment_series_request','u'),
 ('appointments','uq_appointment_series_index','u'),
 ('appointments','fk_appointment_series','f'),
 ('appointments','ck_appointment_series_pair','c'),
 ('appointment_series','ck_appointment_series_weekday','c'),
 ('appointment_series','ck_appointment_series_interval','c'),
 ('appointment_series','ck_appointment_series_count','c'))
 SELECT NOT EXISTS(SELECT 1 FROM expected e WHERE NOT EXISTS(
 SELECT 1 FROM pg_catalog.pg_constraint k
 WHERE k.conrelid=pg_catalog.to_regclass('public.'||e.table_name)
  AND k.conname=e.constraint_name AND k.contype=e.kind::"char"
  AND k.convalidated))
 AND EXISTS(SELECT 1 FROM pg_catalog.pg_index i
 WHERE i.indexrelid=pg_catalog.to_regclass('public.ix_appointment_series_instances')
  AND i.indisvalid AND i.indisready)"""
# app_runtime has no permission to SELECT alembic_version: this check is
# performed only with the reviewed migrator role in the pre-deploy release.
REVISION_CONTRACT = """SELECT (SELECT count(*) FROM public.alembic_version)=1 AND
 EXISTS(SELECT 1 FROM public.alembic_version
 WHERE version_num='0074_fixed_client_series')"""
CONTRACT_QUERY = "SELECT " + " AND ".join("COALESCE((" + part + "),false)" for part in (
    predecessor.CONTRACT_QUERY, COLUMN_CONTRACT, ROLE_CONTRACT,
    CONSTRAINT_CONTRACT)) + " AS ready"
SCHEMA_QUERY = """SELECT current_user AS runtime_role, session_user AS authenticated_role,
 EXISTS(SELECT 1 FROM pg_catalog.pg_roles WHERE rolname=current_user
 AND NOT rolsuper AND NOT rolcreaterole AND NOT rolbypassrls) AS restricted_role,
 COALESCE((""" + CONTRACT_QUERY + """),false) AS schema_ready"""


async def probe_schema(settings, timeout):
    connection = None
    try:
        millis = str(max(1, int(timeout * 1000)))
        connection = await asyncpg.connect(settings.dsn, timeout=timeout,
            command_timeout=timeout, server_settings={
                "default_transaction_read_only": "on", "search_path": "pg_catalog",
                "statement_timeout": millis, "lock_timeout": millis,
                "application_name": "orion-fixed-client-schema-wait"})
        result = await connection.fetchrow(SCHEMA_QUERY)
        if (result["runtime_role"] != "app_runtime"
                or result["authenticated_role"] != "app_runtime"
                or result["restricted_role"] is not True):
            raise Stop("runtime_role_required")
        return result["schema_ready"] is True
    finally:
        if connection is not None:
            connection.terminate()


async def wait_for_schema(settings, **kwargs):
    result = await original_wait(settings, probe=kwargs.pop("probe", probe_schema), **kwargs)
    return {**result, "status": "fixed_client_schema_ready", "schema_marker": SCHEMA_MARKER}


def main():
    try:
        result = asyncio.run(wait_for_schema(Settings.from_environment()))
    except Exception as exc:
        marker = str(exc) if isinstance(exc, Stop) and str(exc) in SAFE_ERRORS else "fixed_client_schema_check_failed"
        print(json.dumps({"ok": False, "error": marker}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
