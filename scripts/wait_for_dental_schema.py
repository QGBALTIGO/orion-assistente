"""Read-only schema contract for the isolated 0075 clinical release.

Never opens a migration connection from the worker/API. No patient rows read.
"""
import asyncio
import json

from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

TABLES = (
    "dental_clinicians", "dental_patients", "dental_records", "dental_exam_files",
    "dental_rooms", "dental_room_reservations", "dental_supplies",
    "dental_supply_movements", "dental_access_audit"
)

ROLE_CONTRACT = """SELECT COUNT(*)=9 AND bool_and(
 c.relrowsecurity AND c.relforcerowsecurity
 AND NOT has_table_privilege('app_support',c.oid,'SELECT')
 AND has_table_privilege('app_runtime',c.oid,'SELECT')
 AND NOT has_table_privilege('app_runtime',c.oid,'TRUNCATE')
) FROM pg_catalog.pg_class c
WHERE c.oid = ANY(ARRAY[""" + ",".join(
    "pg_catalog.to_regclass('public."+table+"')" for table in TABLES
) + """]::oid[])"""

APPEND_ONLY = """SELECT
  NOT has_table_privilege('app_runtime',to_regclass('public.dental_records'),'UPDATE,DELETE')
  AND NOT has_table_privilege('app_runtime',to_regclass('public.dental_exam_files'),'UPDATE,DELETE')
  AND NOT has_table_privilege('app_runtime',to_regclass('public.dental_access_audit'),'UPDATE,DELETE')
  AND NOT has_table_privilege('app_runtime',to_regclass('public.dental_supply_movements'),'UPDATE,DELETE')
  AND EXISTS(SELECT 1 FROM pg_catalog.pg_constraint
    WHERE conrelid=to_regclass('public.dental_room_reservations')
      AND contype='x' AND convalidated)
  AND EXISTS(SELECT 1 FROM pg_catalog.pg_trigger
    WHERE tgrelid=to_regclass('public.appointments')
      AND tgname='dental_follow_appointment' AND tgenabled='O' AND NOT tgisinternal)
"""

CONTRACT_QUERY = "SELECT COALESCE(("+ROLE_CONTRACT+"),false) AND COALESCE(("+APPEND_ONLY+"),false) AS ready"

REVISION = "0075_dental_clinic"


def schema_ready(raw):
    engine = create_engine(raw, poolclass=NullPool,
        connect_args={"options":"-c default_transaction_read_only=on -c statement_timeout=12000"})
    try:
        with engine.connect() as conn:
            return conn.execute(text(CONTRACT_QUERY)).scalar() is True
    finally:
        engine.dispose()


async def probe_runtime():
    import asyncpg
    from scripts.wait_for_single_whatsapp_schema import Settings
    settings=Settings.from_environment()
    connection=await asyncpg.connect(settings.dsn,timeout=10,
        command_timeout=10,server_settings={
            "default_transaction_read_only":"on","search_path":"pg_catalog",
            "statement_timeout":"10000","application_name":"orion-dental-schema-check"})
    try:
        if await connection.fetchval("SELECT current_user") != "app_runtime":
            return False
        return await connection.fetchval(CONTRACT_QUERY) is True
    finally:
        await connection.close()


def main():
    try:
        ready=asyncio.run(probe_runtime())
        print(json.dumps({"ok":bool(ready),"clinical_schema":REVISION}),flush=True)
        return 0 if ready else 3
    except Exception:
        print(json.dumps({"ok":False,"clinical_schema":"unavailable"}),flush=True)
        return 3


if __name__=="__main__":
    raise SystemExit(main())
