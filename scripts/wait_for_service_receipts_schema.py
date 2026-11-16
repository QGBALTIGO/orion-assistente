"""Read-only startup/release contract for configurable service receipts."""
import asyncio
import asyncpg
from sqlalchemy import create_engine,text
from sqlalchemy.pool import NullPool
from scripts.wait_for_service_archive_schema import CONTRACT_QUERY as PREVIOUS_CONTRACT
from scripts.wait_for_agenda_schema import Settings

TARGET='0080_service_receipts'
CONTRACT_QUERY="""SELECT COALESCE(("""+PREVIOUS_CONTRACT+"""),false)
 AND EXISTS(SELECT 1 FROM pg_attribute WHERE attrelid=to_regclass('public.financial_entries')
   AND attname='received_at' AND NOT attisdropped AND format_type(atttypid,atttypmod)='timestamp with time zone')
 AND (SELECT count(*)=2 FROM pg_class WHERE oid IN (to_regclass('public.tenant_receipt_settings'),
   to_regclass('public.service_receipt_history')) AND relrowsecurity AND relforcerowsecurity)
 AND to_regprocedure('public.orion_due_service_receipts(integer)') IS NOT NULL
 AND NOT has_table_privilege('app_runtime','public.service_receipt_history','DELETE')
 AND NOT has_table_privilege('app_runtime','public.service_receipt_history','UPDATE')
 AND NOT has_table_privilege('app_support','public.service_receipt_history','SELECT') AS ready"""


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
            raise RuntimeError('service_receipts_schema_not_ready')
    finally:await connection.close()


if __name__=='__main__':
    asyncio.run(wait_for_schema(Settings.from_environment()))
