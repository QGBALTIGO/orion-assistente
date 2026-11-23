"""Report whether clinical ciphertext exists before provisioning the first key."""
import json
import sys

from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from scripts.backup_and_migrate_commissions import connection_values


MIGRATOR_SCOPE = """
SELECT COUNT(*)=:expected AND bool_and(
  has_table_privilege(current_user,c.oid,'SELECT')
  AND EXISTS (
    SELECT 1 FROM pg_catalog.pg_policy p
    WHERE p.polrelid=c.oid AND p.polpermissive AND p.polcmd IN ('*','r')
      AND (current_user::regrole)::oid=ANY(p.polroles)
      AND pg_catalog.pg_get_expr(p.polqual,p.polrelid)='true'
  )
  AND NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_policy p
    WHERE p.polrelid=c.oid AND NOT p.polpermissive
  )
)
FROM pg_catalog.pg_class c
WHERE c.oid IN (pg_catalog.to_regclass('public.dental_records'),
                pg_catalog.to_regclass('public.dental_exam_files'),
                pg_catalog.to_regclass('public.dental_sales_opportunities'))
"""


def ciphertext_exists(raw):
    engine = create_engine(raw, poolclass=NullPool, connect_args={
        "options": "-c default_transaction_read_only=on -c statement_timeout=10000 "
                   "-c application_name=orion-dental-key-check"
    })
    try:
        with engine.connect() as db:
            db.execute(text("SET LOCAL ROLE app_migrator"))
            sales_table = db.scalar(text("SELECT to_regclass('public.dental_sales_opportunities') IS NOT NULL"))
            if sales_table is not True and db.scalar(text("SELECT version_num FROM public.alembic_version")) == "0088_dental_sales_opportunities":
                raise RuntimeError("dental_sales_history_scope_not_verified")
            if db.scalar(text(MIGRATOR_SCOPE), {"expected": 3 if sales_table else 2}) is not True:
                raise RuntimeError("dental_key_history_scope_not_verified")
            sales_check = " OR EXISTS (SELECT 1 FROM public.dental_sales_opportunities)" if sales_table else ""
            return db.scalar(text("""
                SELECT EXISTS (SELECT 1 FROM public.dental_records)
                    OR EXISTS (SELECT 1 FROM public.dental_exam_files)
            """ + sales_check)) is True
    finally:
        engine.dispose()


def main():
    try:
        raw, _ = connection_values()
        exists = ciphertext_exists(raw)
    except Exception:
        print(json.dumps({"verified": False, "first_key_allowed": False}), file=sys.stderr)
        return 3
    print(json.dumps({"verified": True, "first_key_allowed": not exists}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
