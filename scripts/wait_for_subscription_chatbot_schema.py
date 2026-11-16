"""Read-only catalog barrier for the additive subscription chatbot schema."""
import asyncio
import json

import asyncpg
from scripts import wait_for_agenda_schema as previous
from scripts.wait_for_agenda_schema import Settings, Stop, SAFE_ERRORS

SCHEMA_MARKER = "0063_subscription_chatbot"

CONTRACT_QUERY = """
 WITH expected(name) AS (VALUES
   ('subscription_chatbot_state'),('subscription_chatbot_assets'),
   ('subscription_chatbot_templates'),('subscription_chatbot_rules'),
   ('subscription_chatbot_jobs'),('subscription_chatbot_runs'),
   ('subscription_chatbot_cooldowns'),('subscription_chatbot_selections'),
   ('subscription_chatbot_backups'),('subscription_chatbot_previews')
 ), relations AS (
   SELECT c.* FROM expected e JOIN pg_catalog.pg_class c
     ON c.oid=pg_catalog.to_regclass('public.'||e.name)
 ), private_columns(table_name,column_name) AS (VALUES
   ('subscription_chatbot_rules','keyword'),('subscription_chatbot_rules','normalized_keyword'),
   ('subscription_chatbot_rules','response'),('subscription_chatbot_jobs','payload'),
   ('subscription_chatbot_jobs','result'),('subscription_chatbot_backups','snapshot'),
   ('subscription_device_effects','result'),('subscription_device_effects','chatbot_run_id')
 ), expected_keys(table_name,target_name,columns) AS (VALUES
   ('subscription_chatbot_templates','subscription_chatbot_assets',2),
   ('subscription_chatbot_rules','subscription_devices',2),
   ('subscription_chatbot_rules','tenant_memberships',2),
   ('subscription_chatbot_jobs','tenant_memberships',2),
   ('subscription_chatbot_jobs','subscription_chatbot_previews',2),
   ('subscription_chatbot_jobs','subscription_chatbot_backups',2),
   ('subscription_chatbot_runs','subscription_devices',3),
   ('subscription_chatbot_runs','subscription_device_events',2),
   ('subscription_chatbot_runs','subscription_chatbot_rules',2),
   ('subscription_chatbot_runs','subscription_chatbot_jobs',2),
   ('subscription_chatbot_runs','tenant_memberships',2),
   ('subscription_chatbot_cooldowns','subscription_devices',2),
   ('subscription_chatbot_cooldowns','subscription_chatbot_rules',2),
   ('subscription_chatbot_cooldowns','subscription_chatbot_runs',2),
   ('subscription_chatbot_selections','subscription_devices',3),
   ('subscription_chatbot_selections','subscription_chatbot_runs',2),
   ('subscription_chatbot_selections','subscription_device_effects',2),
   ('subscription_chatbot_selections','subscription_device_events',2),
   ('subscription_chatbot_previews','tenant_memberships',2),
   ('subscription_chatbot_previews','subscription_devices',2),
   ('subscription_chatbot_previews','subscription_chatbot_backups',2),
   ('subscription_chatbot_previews','subscription_chatbot_jobs',2),
   ('subscription_device_effects','subscription_chatbot_runs',2)
 )
 SELECT (SELECT count(*)=10 FROM relations)
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
   AND NOT EXISTS (SELECT 1 FROM private_columns p
     WHERE pg_catalog.has_column_privilege('app_runtime',
       pg_catalog.to_regclass('public.'||p.table_name),p.column_name,'SELECT'))
   AND NOT EXISTS (SELECT 1 FROM relations c
     WHERE c.relname IN ('subscription_chatbot_assets','subscription_chatbot_templates',
       'subscription_chatbot_runs','subscription_chatbot_cooldowns',
       'subscription_chatbot_selections','subscription_chatbot_previews')
       AND pg_catalog.has_any_column_privilege('app_runtime',c.oid,'SELECT'))
   AND pg_catalog.has_column_privilege('app_runtime',
     pg_catalog.to_regclass('public.subscription_chatbot_state'),'revision','SELECT')
   AND pg_catalog.has_column_privilege('app_runtime',
     pg_catalog.to_regclass('public.subscription_chatbot_rules'),'enabled','SELECT')
   AND pg_catalog.has_column_privilege('app_runtime',
     pg_catalog.to_regclass('public.subscription_chatbot_jobs'),'status','SELECT')
   AND pg_catalog.has_column_privilege('app_runtime',
     pg_catalog.to_regclass('public.subscription_chatbot_backups'),'rules_count','SELECT')
   AND NOT EXISTS (SELECT 1 FROM expected_keys e WHERE NOT EXISTS (
     SELECT 1 FROM pg_catalog.pg_constraint k
       WHERE k.conrelid=pg_catalog.to_regclass('public.'||e.table_name)
         AND k.confrelid=pg_catalog.to_regclass('public.'||e.target_name)
         AND k.contype='f' AND k.convalidated
         AND cardinality(k.conkey)=e.columns
         AND (SELECT a.attname FROM pg_catalog.pg_attribute a
           WHERE a.attrelid=k.conrelid AND a.attnum=k.conkey[1])='tenant_id'
         AND (SELECT a.attname FROM pg_catalog.pg_attribute a
           WHERE a.attrelid=k.confrelid AND a.attnum=k.confkey[1])='tenant_id'
   ))
   AND (SELECT count(*)=3 FROM pg_catalog.pg_index i
     JOIN pg_catalog.pg_class c ON c.oid=i.indexrelid
     WHERE c.relnamespace='public'::regnamespace
       AND c.relname IN ('uq_subscription_chatbot_template_origin',
         'uq_subscription_chatbot_rule_origin','uq_subscription_chatbot_daily_backup')
       AND i.indisunique AND i.indisvalid AND i.indisready AND i.indpred IS NOT NULL)
   AND EXISTS (SELECT 1 FROM pg_catalog.pg_constraint k
     WHERE k.conrelid=pg_catalog.to_regclass('public.subscription_device_effects')
       AND k.conname='subscription_device_effect_chatbot_scope'
       AND k.contype='c' AND k.convalidated
       AND pg_catalog.pg_get_constraintdef(k.oid) LIKE '%chatbot_action%'
       AND pg_catalog.pg_get_constraintdef(k.oid) LIKE '%chatbot_run_id IS NOT NULL%'
       AND pg_catalog.pg_get_constraintdef(k.oid) LIKE '%chatbot_revision IS NOT NULL%')
   AND EXISTS (SELECT 1 FROM pg_catalog.pg_constraint k
     WHERE k.conrelid=pg_catalog.to_regclass('public.subscription_device_effects')
       AND k.conname='subscription_device_effects_type_check'
       AND k.contype='c' AND k.convalidated
       AND pg_catalog.pg_get_constraintdef(k.oid) LIKE '%chatbot_action%')
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
                "application_name": "orion-subscription-chatbot-schema-wait",
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
    return {**result, "status": "subscription_chatbot_schema_ready", "schema_marker": SCHEMA_MARKER}


def main():
    try:
        result = asyncio.run(wait_for_schema(Settings.from_environment()))
    except Exception as error:
        code = str(error) if isinstance(error, Stop) and str(error) in SAFE_ERRORS else "subscription_chatbot_schema_check_failed"
        print(json.dumps({"ok": False, "error": code}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
