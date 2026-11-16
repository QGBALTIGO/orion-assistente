"""Read-only startup/release contract for internal shared appointments."""
import asyncio
import asyncpg
from sqlalchemy import create_engine,text
from sqlalchemy.pool import NullPool
from scripts.wait_for_service_receipts_schema import CONTRACT_QUERY as PREVIOUS_CONTRACT
from scripts.wait_for_agenda_schema import Settings

TARGET='0081_exceptional_overlap'
CONTRACT_QUERY="""SELECT COALESCE(("""+PREVIOUS_CONTRACT+"""),false)
 AND EXISTS(SELECT 1 FROM pg_attribute WHERE attrelid=to_regclass('public.appointments')
   AND attname='allow_overlap' AND NOT attisdropped AND attnotnull
   AND format_type(atttypid,atttypmod)='boolean')
 AND EXISTS(SELECT 1 FROM pg_trigger WHERE tgrelid=to_regclass('public.appointments')
   AND tgname='appointments_exceptional_overlap_guard' AND tgenabled='O')
 AND EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid=to_regclass('public.appointments')
   AND conname='ck_appointment_shared_exception' AND convalidated)
 AND EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid=to_regclass('public.appointments')
   AND conname='appointments_no_overlap' AND pg_get_constraintdef(oid) LIKE '%NOT allow_overlap%')
 AND NOT has_function_privilege('app_runtime','public.orion_guard_exceptional_overlap()','EXECUTE')
 AS ready"""


def schema_ready(raw):
    engine=create_engine(raw,poolclass=NullPool,connect_args={
        'options':'-c default_transaction_read_only=on -c statement_timeout=12000'})
    try:
        with engine.connect() as db:return db.execute(text(CONTRACT_QUERY)).scalar() is True
    finally:engine.dispose()


async def wait_for_schema(settings):
    connection=await asyncpg.connect(settings.dsn,timeout=15,
        server_settings={'default_transaction_read_only':'on','statement_timeout':'12000'})
    try:
        if await connection.fetchval(CONTRACT_QUERY) is not True:
            raise RuntimeError('exceptional_overlap_schema_not_ready')
    finally:await connection.close()


if __name__=='__main__':
    asyncio.run(wait_for_schema(Settings.from_environment()))
