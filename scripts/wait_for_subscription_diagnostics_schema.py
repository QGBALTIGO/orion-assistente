"""Read-only catalog barrier for immutable previews and fenced diagnostic work."""
import asyncio
import json

import asyncpg
from scripts import wait_for_agenda_schema as previous
from scripts.wait_for_agenda_schema import Settings, Stop, SAFE_ERRORS
from scripts.wait_for_appointment_operations_schema import CONTRACT_QUERY as PREVIOUS_CONTRACT

SCHEMA_MARKER = "0067_subscription_diagnostics"

CONTRACT_QUERY = """
 SELECT COALESCE((""" + PREVIOUS_CONTRACT + """),false)
   AND EXISTS (SELECT 1 FROM pg_catalog.pg_class c
     WHERE c.oid=pg_catalog.to_regclass('public.subscription_diagnostic_requests')
       AND c.relkind='r' AND c.relrowsecurity AND c.relforcerowsecurity
       AND pg_catalog.pg_get_userbyid(c.relowner)='app_migrator'
       AND pg_catalog.has_table_privilege('app_support',c.oid,'SELECT')
       AND pg_catalog.has_table_privilege('app_support',c.oid,'INSERT')
       AND NOT pg_catalog.has_table_privilege('app_support',c.oid,'UPDATE')
       AND NOT pg_catalog.has_table_privilege('app_support',c.oid,'DELETE')
       AND NOT pg_catalog.has_table_privilege('app_support',c.oid,'TRUNCATE')
       AND NOT pg_catalog.has_any_column_privilege('app_runtime',c.oid,'SELECT')
       AND NOT pg_catalog.has_any_column_privilege('app_runtime',c.oid,'INSERT')
       AND NOT pg_catalog.has_any_column_privilege('app_runtime',c.oid,'UPDATE')
       AND NOT pg_catalog.has_table_privilege('app_runtime',c.oid,'DELETE')
       AND NOT pg_catalog.has_table_privilege('app_runtime',c.oid,'TRUNCATE')
       AND NOT EXISTS (SELECT 1 FROM pg_catalog.aclexplode(COALESCE(c.relacl,
         pg_catalog.acldefault('r',c.relowner))) acl WHERE acl.grantee=0)
       AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_attribute a
         WHERE a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped
           AND pg_catalog.has_column_privilege('app_support',c.oid,a.attnum,'UPDATE') <>
             (a.attname IN ('status','effect_id','confirmed_at','execution_expires_at',
               'started_at','finished_at','claimed_owner','claim_token','claim_fence','claim_expires_at','result')))
       AND EXISTS (SELECT 1 FROM pg_catalog.pg_policy p WHERE p.polrelid=c.oid
         AND p.polcmd='r'
         AND (SELECT oid FROM pg_catalog.pg_roles WHERE rolname='app_runtime')=ANY(p.polroles)
         AND pg_catalog.pg_get_expr(p.polqual,p.polrelid) LIKE '%app.tenant_id%'
         AND pg_catalog.pg_get_expr(p.polqual,p.polrelid) LIKE '%tenant_id%')
       AND EXISTS (SELECT 1 FROM pg_catalog.pg_policy p WHERE p.polrelid=c.oid
         AND p.polcmd='*' AND pg_catalog.pg_get_expr(p.polqual,p.polrelid)='true'
         AND pg_catalog.pg_get_expr(p.polwithcheck,p.polrelid)='true'
         AND (SELECT oid FROM pg_catalog.pg_roles WHERE rolname='app_support')=ANY(p.polroles)))
   AND (SELECT count(*)=6 FROM pg_catalog.pg_attribute a
     WHERE a.attrelid=pg_catalog.to_regclass('public.subscription_diagnostic_requests')
       AND a.attname IN ('id','tenant_id','device_id','generation','actor_account_id','idempotency_key')
       AND a.attnotnull AND NOT a.attisdropped AND a.atttypid='pg_catalog.uuid'::regtype)
   AND (SELECT count(*)=4 FROM pg_catalog.pg_attribute a
     WHERE a.attrelid=pg_catalog.to_regclass('public.subscription_diagnostic_requests')
       AND NOT a.attisdropped AND a.attnotnull AND (
         (a.attname IN ('settings_revision','claim_fence') AND a.atttypid='pg_catalog.int8'::regtype)
         OR (a.attname IN ('created_at','expires_at') AND a.atttypid='pg_catalog.timestamptz'::regtype)))
   AND EXISTS (SELECT 1 FROM pg_catalog.pg_attribute a
     WHERE a.attrelid=pg_catalog.to_regclass('public.subscription_diagnostic_requests')
       AND a.attname='snapshot' AND a.attnotnull AND NOT a.attisdropped AND a.atttypid='pg_catalog.jsonb'::regtype)
   AND (SELECT count(*)=2 FROM pg_catalog.pg_constraint c
     WHERE c.conrelid=pg_catalog.to_regclass('public.subscription_diagnostic_requests')
       AND c.contype='u' AND c.convalidated AND cardinality(c.conkey)=2
       AND (SELECT a.attname FROM pg_catalog.pg_attribute a
         WHERE a.attrelid=c.conrelid AND a.attnum=c.conkey[1])='tenant_id'
       AND ((c.conname='uq_subscription_diagnostic_key' AND
          (SELECT a.attname FROM pg_catalog.pg_attribute a WHERE a.attrelid=c.conrelid AND a.attnum=c.conkey[2])='idempotency_key')
         OR (c.conname='uq_subscription_diagnostic_effect' AND
          (SELECT a.attname FROM pg_catalog.pg_attribute a WHERE a.attrelid=c.conrelid AND a.attnum=c.conkey[2])='effect_id')))
   AND (SELECT count(*)=3 FROM pg_catalog.pg_constraint c
     WHERE c.conrelid=pg_catalog.to_regclass('public.subscription_diagnostic_requests')
       AND c.contype='f' AND c.convalidated
       AND (SELECT a.attname FROM pg_catalog.pg_attribute a
         WHERE a.attrelid=c.conrelid AND a.attnum=c.conkey[1])='tenant_id'
       AND (SELECT a.attname FROM pg_catalog.pg_attribute a
         WHERE a.attrelid=c.confrelid AND a.attnum=c.confkey[1])='tenant_id'
       AND ((c.conname='fk_subscription_diagnostic_device'
           AND c.confrelid=pg_catalog.to_regclass('public.subscription_devices') AND cardinality(c.conkey)=3
           AND (SELECT a.attname FROM pg_catalog.pg_attribute a WHERE a.attrelid=c.conrelid AND a.attnum=c.conkey[2])='device_id'
           AND (SELECT a.attname FROM pg_catalog.pg_attribute a WHERE a.attrelid=c.conrelid AND a.attnum=c.conkey[3])='generation')
         OR (c.conname='fk_subscription_diagnostic_actor'
           AND c.confrelid=pg_catalog.to_regclass('public.tenant_memberships') AND cardinality(c.conkey)=2
           AND (SELECT a.attname FROM pg_catalog.pg_attribute a WHERE a.attrelid=c.conrelid AND a.attnum=c.conkey[2])='actor_account_id')
         OR (c.conname='fk_subscription_diagnostic_effect'
           AND c.confrelid=pg_catalog.to_regclass('public.subscription_device_effects') AND cardinality(c.conkey)=2
           AND (SELECT a.attname FROM pg_catalog.pg_attribute a WHERE a.attrelid=c.conrelid AND a.attnum=c.conkey[2])='effect_id')))
   AND (SELECT count(*)=4 FROM pg_catalog.pg_constraint c
     WHERE c.conrelid=pg_catalog.to_regclass('public.subscription_diagnostic_requests')
       AND c.contype='c' AND c.convalidated AND c.conname IN (
         'subscription_diagnostic_expiry_check','subscription_diagnostic_kind_check',
         'subscription_diagnostic_claim_check','subscription_diagnostic_result_check'))
   AND EXISTS (SELECT 1 FROM pg_catalog.pg_constraint c
     WHERE c.conrelid=pg_catalog.to_regclass('public.subscription_diagnostic_requests')
       AND c.contype='c' AND c.convalidated AND c.conname='subscription_diagnostic_kind_check'
       AND pg_catalog.pg_get_constraintdef(c.oid) LIKE '%confirmation_version IS NOT NULL%'
       AND pg_catalog.pg_get_constraintdef(c.oid) LIKE '%confirmed_at IS NOT NULL%'
       AND pg_catalog.pg_get_constraintdef(c.oid) LIKE '%effect_id IS NOT NULL%'
       AND pg_catalog.pg_get_constraintdef(c.oid) LIKE '%message_test%'
       AND pg_catalog.pg_get_constraintdef(c.oid) LIKE '%proxy_test%')
   AND EXISTS (SELECT 1 FROM pg_catalog.pg_constraint c
     WHERE c.conrelid=pg_catalog.to_regclass('public.subscription_diagnostic_requests')
       AND c.contype='c' AND c.convalidated AND c.conname='subscription_diagnostic_claim_check'
       AND pg_catalog.pg_get_constraintdef(c.oid) LIKE '%claim_token IS NOT NULL%'
       AND pg_catalog.pg_get_constraintdef(c.oid) LIKE '%claim_expires_at <= execution_expires_at%')
   AND EXISTS (SELECT 1 FROM pg_catalog.pg_constraint c
     WHERE c.conrelid=pg_catalog.to_regclass('public.subscription_device_events')
       AND c.contype='c' AND c.convalidated AND c.conname='subscription_device_events_kind_check'
       AND pg_catalog.pg_get_constraintdef(c.oid) LIKE '%diagnostic%')
   AND (SELECT count(*)=2 FROM pg_catalog.pg_index i
     JOIN pg_catalog.pg_class c ON c.oid=i.indexrelid
     WHERE c.relnamespace='public'::regnamespace AND i.indisvalid AND i.indisready
       AND i.indrelid=pg_catalog.to_regclass('public.subscription_diagnostic_requests')
       AND (c.relname='ix_subscription_diagnostic_device'
         OR c.relname='ix_subscription_diagnostic_active' AND i.indpred IS NOT NULL))
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
                "application_name": "orion-subscription-diagnostics-schema-wait",
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
    return {**result, "status": "subscription_diagnostics_schema_ready", "schema_marker": SCHEMA_MARKER}


def main():
    try:
        result = asyncio.run(wait_for_schema(Settings.from_environment()))
    except Exception as error:
        code = str(error) if isinstance(error, Stop) and str(error) in SAFE_ERRORS else "subscription_diagnostics_schema_check_failed"
        print(json.dumps({"ok": False, "error": code}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
