"""Read-only readiness check for isolated customer history imports."""
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from scripts.wait_for_commercial_demos_schema import CONTRACT_QUERY as PREVIOUS_CONTRACT

TARGET = "0078_customer_legacy_history"
CONTRACT_QUERY = """SELECT COALESCE((""" + PREVIOUS_CONTRACT + """),false) AND
    EXISTS (SELECT 1 FROM pg_class c WHERE c.oid=to_regclass('public.customer_legacy_history')
        AND c.relrowsecurity AND c.relforcerowsecurity
        AND has_table_privilege('app_runtime',c.oid,'SELECT')
        AND NOT has_table_privilege('app_runtime',c.oid,'INSERT,UPDATE,DELETE,TRUNCATE')
        AND NOT has_table_privilege('app_support',c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE'))
    AND EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='public'
        AND tablename='customer_legacy_history' AND policyname='customer_legacy_runtime_read')
    AS ready"""


def schema_ready(raw):
    engine = create_engine(raw, poolclass=NullPool, connect_args={
        "options": "-c default_transaction_read_only=on -c statement_timeout=12000"})
    try:
        with engine.connect() as db:
            return db.execute(text(CONTRACT_QUERY)).scalar() is True
    finally:
        engine.dispose()
