"""Read-only catalog barrier for internal appointments and extra services."""
import asyncio
import json

import asyncpg
from scripts import wait_for_agenda_schema as previous
from scripts.wait_for_agenda_schema import Settings, Stop, SAFE_ERRORS
from scripts.wait_for_subscription_sent_messages_schema import CONTRACT_QUERY as PREVIOUS_CONTRACT

SCHEMA_MARKER = "0066_appointment_operations"

CONTRACT_QUERY = """
 SELECT COALESCE((""" + PREVIOUS_CONTRACT + """),false)
   AND (SELECT count(*)=7 FROM (VALUES
     ('appointments','booking_mode','character varying(16)',true),
     ('appointments','created_by_account_id','uuid',false),
     ('appointments','actual_duration_min','integer',false),
     ('appointment_items','item_origin','character varying(8)',true),
     ('appointment_items','added_at','timestamp with time zone',false),
     ('appointment_items','added_by_account_id','uuid',false),
     ('appointment_items','added_duration_min','integer',false)
   ) expected(tbl,col,typ,required)
     JOIN pg_catalog.pg_attribute a ON a.attrelid=pg_catalog.to_regclass('public.'||expected.tbl)
       AND a.attname=expected.col AND a.attnum>0 AND NOT a.attisdropped
       AND a.attnotnull=expected.required
       AND pg_catalog.format_type(a.atttypid,a.atttypmod)=expected.typ
       AND pg_catalog.has_column_privilege('app_runtime',a.attrelid,a.attnum,'SELECT')
       AND pg_catalog.has_column_privilege('app_runtime',a.attrelid,a.attnum,'INSERT')
       AND pg_catalog.has_column_privilege('app_runtime',a.attrelid,a.attnum,'UPDATE'))
   AND (SELECT count(*)=2 FROM (VALUES
     ('appointments','booking_mode','''standard''::character varying'),
     ('appointment_items','item_origin','''booked''::character varying')
   ) expected(tbl,col,default_expr)
     JOIN pg_catalog.pg_attribute a ON a.attrelid=pg_catalog.to_regclass('public.'||expected.tbl)
       AND a.attname=expected.col AND a.attnum>0 AND NOT a.attisdropped
     JOIN pg_catalog.pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
       AND pg_catalog.pg_get_expr(d.adbin,d.adrelid)=expected.default_expr)
   AND (SELECT count(*)=3 FROM pg_catalog.pg_class c
     WHERE c.oid IN (pg_catalog.to_regclass('public.appointments'),
                    pg_catalog.to_regclass('public.appointment_items'),
                    pg_catalog.to_regclass('public.appointment_history'))
       AND c.relkind='r' AND c.relrowsecurity AND c.relforcerowsecurity
       AND pg_catalog.pg_get_userbyid(c.relowner)='app_migrator'
       AND NOT EXISTS (SELECT 1 FROM pg_catalog.aclexplode(COALESCE(c.relacl,
         pg_catalog.acldefault('r',c.relowner))) acl WHERE acl.grantee=0)
       AND EXISTS (SELECT 1 FROM pg_catalog.pg_policy p
         WHERE p.polrelid=c.oid AND p.polcmd='*'
           AND p.polname=c.relname||'_runtime'
           AND (SELECT oid FROM pg_catalog.pg_roles WHERE rolname='app_runtime')=ANY(p.polroles)
           AND pg_catalog.pg_get_expr(p.polqual,p.polrelid) LIKE '%app.tenant_id%'
           AND pg_catalog.pg_get_expr(p.polqual,p.polrelid) LIKE '%tenant_id%'
           AND pg_catalog.pg_get_expr(p.polwithcheck,p.polrelid) LIKE '%app.tenant_id%'
           AND pg_catalog.pg_get_expr(p.polwithcheck,p.polrelid) LIKE '%tenant_id%'))
   AND (SELECT count(*)=5 FROM (VALUES
     ('appointments','ck_appointment_booking_mode'),
     ('appointments','ck_appointment_actual_duration'),
     ('appointment_items','ck_appointment_item_origin'),
     ('appointment_items','ck_appointment_item_added_duration'),
     ('appointment_items','ck_appointment_item_extra_metadata')
   ) expected(tbl,name)
     JOIN pg_catalog.pg_constraint c ON c.conrelid=pg_catalog.to_regclass('public.'||expected.tbl)
       AND c.conname=expected.name AND c.contype='c' AND c.convalidated)
   AND EXISTS (SELECT 1 FROM pg_catalog.pg_constraint c
     WHERE c.conrelid=pg_catalog.to_regclass('public.appointment_history')
       AND c.conname='ck_history_event' AND c.contype='c' AND c.convalidated
       AND pg_catalog.pg_get_constraintdef(c.oid) LIKE '%exceptional_created%'
       AND pg_catalog.pg_get_constraintdef(c.oid) LIKE '%retroactive_created%'
       AND pg_catalog.pg_get_constraintdef(c.oid) LIKE '%services_added%'
       AND pg_catalog.pg_get_constraintdef(c.oid) LIKE '%hold_created%'
       AND pg_catalog.pg_get_constraintdef(c.oid) LIKE '%hold_expired%'
       AND pg_catalog.pg_get_constraintdef(c.oid) LIKE '%confirmed%'
       AND pg_catalog.pg_get_constraintdef(c.oid) LIKE '%cancelled%'
       AND pg_catalog.pg_get_constraintdef(c.oid) LIKE '%rescheduled%'
       AND pg_catalog.pg_get_constraintdef(c.oid) LIKE '%completed%'
       AND pg_catalog.pg_get_constraintdef(c.oid) LIKE '%no_show%')
   AND EXISTS (SELECT 1 FROM pg_catalog.pg_constraint c
     WHERE c.conrelid=pg_catalog.to_regclass('public.appointments')
       AND c.conname='appointments_no_overlap' AND c.contype='x' AND c.convalidated
       AND pg_catalog.pg_get_constraintdef(c.oid) LIKE '%hold%'
       AND pg_catalog.pg_get_constraintdef(c.oid) LIKE '%confirmed%'
       AND pg_catalog.pg_get_constraintdef(c.oid) NOT LIKE '%booking_mode%')
   AND (SELECT count(*)=5 FROM (VALUES
     ('appointments','customers',ARRAY['tenant_id','customer_id']),
     ('appointments','professionals',ARRAY['tenant_id','professional_id']),
     ('appointment_items','appointments',ARRAY['tenant_id','appointment_id']),
     ('appointment_items','services',ARRAY['tenant_id','service_id']),
     ('appointment_history','appointments',ARRAY['tenant_id','appointment_id'])
   ) expected(tbl,referenced,cols)
     JOIN pg_catalog.pg_constraint c ON c.conrelid=pg_catalog.to_regclass('public.'||expected.tbl)
       AND c.confrelid=pg_catalog.to_regclass('public.'||expected.referenced)
       AND c.contype='f' AND c.convalidated
       AND ARRAY(SELECT a.attname::text FROM unnest(c.conkey) WITH ORDINALITY x(num,pos)
         JOIN pg_catalog.pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=x.num
         ORDER BY x.pos)=expected.cols
       AND ARRAY(SELECT a.attname::text FROM unnest(c.confkey) WITH ORDINALITY x(num,pos)
         JOIN pg_catalog.pg_attribute a ON a.attrelid=c.confrelid AND a.attnum=x.num
         ORDER BY x.pos)=ARRAY['tenant_id','id'])
   AND EXISTS (SELECT 1 FROM pg_catalog.pg_proc p
     WHERE p.oid=pg_catalog.to_regprocedure('public.orion_due_auto_completions(integer)')
       AND p.prosecdef AND p.provolatile='s'
       AND pg_catalog.pg_get_userbyid(p.proowner)='app_migrator'
       AND 'search_path=pg_catalog, public'=ANY(p.proconfig)
       AND pg_catalog.has_function_privilege('app_support',p.oid,'EXECUTE')
       AND NOT pg_catalog.has_function_privilege('app_runtime',p.oid,'EXECUTE')
       AND NOT EXISTS (SELECT 1 FROM pg_catalog.aclexplode(COALESCE(p.proacl,
         pg_catalog.acldefault('f',p.proowner))) acl WHERE acl.grantee=0)
       AND pg_catalog.pg_get_functiondef(p.oid) LIKE '%actual_duration_min%'
       AND pg_catalog.pg_get_functiondef(p.oid) LIKE '%added_duration_min%'
       AND pg_catalog.pg_get_functiondef(p.oid) LIKE '%duration_snapshot_min%'
       AND pg_catalog.pg_get_functiondef(p.oid) LIKE '%a.end_at <= now()%'
       AND pg_catalog.pg_get_functiondef(p.oid) LIKE '%timing.completion_due_at <= now()%'
       AND pg_catalog.pg_get_functiondef(p.oid) LIKE '%a.end_at >= t.auto_complete_after%'
       AND pg_catalog.pg_get_functiondef(p.oid) LIKE '%price_mode_snapshot%')
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
                "application_name": "orion-appointment-operations-schema-wait",
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
    return {**result, "status": "appointment_operations_schema_ready", "schema_marker": SCHEMA_MARKER}


def main():
    try:
        result = asyncio.run(wait_for_schema(Settings.from_environment()))
    except Exception as error:
        code = str(error) if isinstance(error, Stop) and str(error) in SAFE_ERRORS else "appointment_operations_schema_check_failed"
        print(json.dumps({"ok": False, "error": code}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
