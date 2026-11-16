"""Read-only readiness check for service archiving without history deletion."""
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from scripts.wait_for_customer_legacy_history_schema import CONTRACT_QUERY as PREVIOUS_CONTRACT

TARGET = "0079_service_archive"
CONTRACT_QUERY = """SELECT COALESCE((""" + PREVIOUS_CONTRACT + """),false) AND
    EXISTS (SELECT 1 FROM pg_attribute a
        WHERE a.attrelid=to_regclass('public.services') AND a.attname='deleted_at'
          AND NOT a.attisdropped AND format_type(a.atttypid,a.atttypmod)='timestamp with time zone')
    AND EXISTS (SELECT 1 FROM pg_constraint c
        WHERE c.conrelid=to_regclass('public.services')
          AND c.conname='ck_service_archive_inactive' AND c.convalidated)
    AS ready"""


def schema_ready(raw):
    engine = create_engine(raw, poolclass=NullPool, connect_args={
        "options": "-c default_transaction_read_only=on -c statement_timeout=12000"})
    try:
        with engine.connect() as db:
            return db.execute(text(CONTRACT_QUERY)).scalar() is True
    finally:
        engine.dispose()
