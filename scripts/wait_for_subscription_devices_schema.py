"""Read-only readiness contract for tenant-isolated subscription devices.

Only the API pre-deploy may perform the backup and migration. API workers and
the ordinary job worker inspect the catalogs using the restricted runtime role.
No device, secret, contact or message record is read by this barrier.
"""
import asyncio
import json

import asyncpg
from scripts import wait_for_agenda_schema as previous
from scripts.wait_for_agenda_schema import Settings, Stop, SAFE_ERRORS

SCHEMA_MARKER = "0062_subscription_devices"

CONTRACT_QUERY = """
 WITH expected(name) AS (VALUES
   ('subscription_devices'),('subscription_device_operations'),
   ('subscription_device_events'),('subscription_device_effects'),
   ('subscription_device_cooldowns'),('subscription_device_audio_usage')
 ), relations AS (
   SELECT c.* FROM expected e JOIN pg_catalog.pg_class c
     ON c.oid=pg_catalog.to_regclass('public.'||e.name)
 )
 SELECT (SELECT count(*)=6 FROM relations)
   AND COALESCE((SELECT bool_and(
     c.relkind='r' AND c.relrowsecurity AND c.relforcerowsecurity
     AND pg_catalog.pg_get_userbyid(c.relowner)='app_migrator'
     AND pg_catalog.has_table_privilege('app_support',c.oid,'SELECT')
     AND pg_catalog.has_table_privilege('app_support',c.oid,'INSERT')
     AND pg_catalog.has_table_privilege('app_support',c.oid,'UPDATE')
     AND NOT pg_catalog.has_table_privilege('app_support',c.oid,'DELETE')
     AND NOT pg_catalog.has_any_column_privilege('app_runtime',c.oid,'INSERT')
     AND NOT pg_catalog.has_any_column_privilege('app_runtime',c.oid,'UPDATE')
     AND NOT pg_catalog.has_table_privilege('app_runtime',c.oid,'DELETE')
     AND NOT EXISTS (SELECT 1 FROM pg_catalog.aclexplode(COALESCE(c.relacl,
       pg_catalog.acldefault('r',c.relowner))) acl WHERE acl.grantee=0)
     AND EXISTS (SELECT 1 FROM pg_catalog.pg_policy p WHERE p.polrelid=c.oid
       AND p.polcmd='r'
       AND (SELECT oid FROM pg_catalog.pg_roles WHERE rolname='app_runtime')=ANY(p.polroles)
       AND pg_catalog.pg_get_expr(p.polqual,p.polrelid) LIKE '%app.tenant_id%'
       AND pg_catalog.pg_get_expr(p.polqual,p.polrelid) LIKE '%tenant_id%')
     AND EXISTS (SELECT 1 FROM pg_catalog.pg_attribute a WHERE a.attrelid=c.oid
       AND a.attname='tenant_id' AND a.attnotnull AND NOT a.attisdropped
       AND a.atttypid='pg_catalog.uuid'::regtype)
   ) FROM relations c),false)
   AND NOT pg_catalog.has_column_privilege('app_runtime',
     pg_catalog.to_regclass('public.subscription_devices'),'settings','SELECT')
   AND NOT pg_catalog.has_column_privilege('app_runtime',
     pg_catalog.to_regclass('public.subscription_devices'),'proxy_secret_encrypted','SELECT')
   AND NOT pg_catalog.has_column_privilege('app_runtime',
     pg_catalog.to_regclass('public.subscription_devices'),'runtime','SELECT')
   AND NOT pg_catalog.has_column_privilege('app_runtime',
     pg_catalog.to_regclass('public.subscription_device_operations'),'request_payload','SELECT')
   AND pg_catalog.has_column_privilege('app_runtime',
     pg_catalog.to_regclass('public.subscription_devices'),'name','SELECT')
   AND pg_catalog.has_column_privilege('app_runtime',
     pg_catalog.to_regclass('public.subscription_device_operations'),'status','SELECT')
   AND pg_catalog.has_table_privilege('app_runtime',
     pg_catalog.to_regclass('public.subscription_device_audio_usage'),'SELECT')
   AND NOT EXISTS (SELECT 1 FROM relations c
     WHERE c.relname IN ('subscription_device_events','subscription_device_effects','subscription_device_cooldowns')
       AND pg_catalog.has_any_column_privilege('app_runtime',c.oid,'SELECT'))
   AND (SELECT count(*)=4 FROM pg_catalog.pg_index i
     JOIN pg_catalog.pg_class c ON c.oid=i.indexrelid
     WHERE c.relnamespace='public'::regnamespace
       AND c.relname IN ('uq_subscription_device_active_tenant',
         'uq_subscription_device_active_channel','uq_subscription_device_pending_control',
         'uq_subscription_device_outbound_message')
       AND i.indisunique AND i.indisvalid AND i.indpred IS NOT NULL)
   AND (SELECT count(*)=3 FROM pg_catalog.pg_constraint k
     WHERE k.conrelid IN (pg_catalog.to_regclass('public.subscription_device_operations'),
       pg_catalog.to_regclass('public.subscription_device_events'),
       pg_catalog.to_regclass('public.subscription_device_effects'))
       AND k.confrelid=pg_catalog.to_regclass('public.subscription_devices')
       AND k.contype='f' AND k.convalidated AND cardinality(k.conkey)=3)
   AND EXISTS (SELECT 1 FROM pg_catalog.pg_constraint k
     WHERE k.conrelid=pg_catalog.to_regclass('public.subscription_devices')
       AND k.confrelid=pg_catalog.to_regclass('public.qr_channels')
       AND k.contype='f' AND k.convalidated AND cardinality(k.conkey)=2)
   AND EXISTS (SELECT 1 FROM pg_catalog.pg_constraint k
     WHERE k.conrelid=pg_catalog.to_regclass('public.subscription_devices')
       AND k.confrelid=pg_catalog.to_regclass('public.tenant_memberships')
       AND k.contype='f' AND k.convalidated AND cardinality(k.conkey)=2)
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
                "application_name": "orion-subscription-devices-schema-wait",
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
    return {**result, "status": "subscription_devices_schema_ready", "schema_marker": SCHEMA_MARKER}


def main():
    try:
        result = asyncio.run(wait_for_schema(Settings.from_environment()))
    except Exception as error:
        code = str(error) if isinstance(error, Stop) and str(error) in SAFE_ERRORS else "subscription_devices_schema_check_failed"
        print(json.dumps({"ok": False, "error": code}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
