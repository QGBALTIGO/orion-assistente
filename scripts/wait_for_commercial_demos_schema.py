"""Read-only readiness probe for isolated commercial demonstration records."""
import asyncio
import json

from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from scripts.wait_for_dental_schema import CONTRACT_QUERY as PREDECESSOR_CONTRACT

REVISION = "0077_commercial_demos"
DEMO_CONTRACT = """SELECT
  (SELECT COUNT(*)=2 AND bool_and(c.relrowsecurity AND c.relforcerowsecurity
    AND NOT has_table_privilege('app_runtime',c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE')
    AND has_table_privilege('app_support',c.oid,'SELECT,INSERT')
    AND NOT has_table_privilege('app_support',c.oid,'DELETE,TRUNCATE'))
   FROM pg_catalog.pg_class c WHERE c.oid=ANY(ARRAY[
     pg_catalog.to_regclass('public.commercial_demo_sites'),
     pg_catalog.to_regclass('public.commercial_demo_events')]::oid[]))
  AND (SELECT p.prosecdef AND p.proconfig @> ARRAY['search_path=pg_catalog, public']::text[]
    AND has_function_privilege('app_runtime',p.oid,'EXECUTE')
    AND NOT EXISTS(SELECT 1 FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
                   WHERE a.grantee=0 AND a.privilege_type='EXECUTE')
    FROM pg_catalog.pg_proc p
    WHERE p.oid=pg_catalog.to_regprocedure('public.read_commercial_demo(text)'))
  AND NOT has_table_privilege('app_support',
    pg_catalog.to_regclass('public.commercial_demo_events'),'UPDATE')
"""
CONTRACT_QUERY = ("SELECT COALESCE((" + PREDECESSOR_CONTRACT + "),false) AND "
                  "COALESCE((" + DEMO_CONTRACT + "),false) AS ready")


def schema_ready(raw):
    engine = create_engine(raw, poolclass=NullPool, connect_args={
        "options": "-c default_transaction_read_only=on -c statement_timeout=12000"})
    try:
        with engine.connect() as conn:
            return conn.execute(text(CONTRACT_QUERY)).scalar() is True
    finally:
        engine.dispose()


async def probe_runtime():
    import asyncpg
    from scripts.wait_for_single_whatsapp_schema import Settings
    settings = Settings.from_environment()
    connection = await asyncpg.connect(settings.dsn, timeout=10, command_timeout=10,
        server_settings={"default_transaction_read_only": "on", "search_path": "pg_catalog",
                         "statement_timeout": "10000", "application_name": "orion-demo-schema-check"})
    try:
        if await connection.fetchval("SELECT current_user") != "app_runtime":
            return False
        return await connection.fetchval(CONTRACT_QUERY) is True
    finally:
        await connection.close()


def main():
    try:
        ready = asyncio.run(probe_runtime())
        print(json.dumps({"ok": bool(ready), "demo_schema": REVISION}), flush=True)
        return 0 if ready else 3
    except Exception:
        print(json.dumps({"ok": False, "demo_schema": "unavailable"}), flush=True)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
