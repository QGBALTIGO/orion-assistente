"""ONE-TIME Railway account-table migration with security checks.

The DB owner credential is provided via a temporary Railway reference.
Alembic SET ROLEs into app_migrator before applying DDL. Do not use as
an application startup hook or persist the owner DB URL in the API.
"""
import os
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

ALLOWED_REVISIONS={"0007_advanced","0008_panel_accounts","0009_email_otp"}
TARGET="0009_email_otp"


def migration_dsn():
    if os.getenv("ORION_RUN_ACCOUNT_MIGRATION")!="1":
        raise RuntimeError("Account migration not explicitly enabled")
    if os.getenv("ORION_MIGRATION_SWITCH_ROLE")!="1":
        raise RuntimeError("Required migration role switch is disabled")
    value=os.getenv("MIGRATION_DATABASE_URL","")
    if not value:
        raise RuntimeError("Migration database URL is not configured")
    url=make_url(value)
    if url.drivername not in {"postgres","postgresql","postgresql+psycopg"}:
        raise RuntimeError("Expected a PostgreSQL migration URL")
    return url.set(drivername="postgresql+psycopg")


def state(conn):
    version=conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    if version not in ALLOWED_REVISIONS:
        raise RuntimeError("Unexpected Alembic revision: stop before changing database")
    migrator=conn.execute(text(
        "SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='app_migrator')"
    )).scalar_one()
    if not migrator:
        raise RuntimeError("Required app_migrator role does not exist")
    return version


def verify(conn):
    version=state(conn)
    if version!=TARGET:
        raise RuntimeError("Migration revision did not reach target")
    for table in ("panel_accounts","panel_email_tokens"):
        row=conn.execute(text("""
            SELECT c.relrowsecurity, c.relforcerowsecurity,
              has_table_privilege('app_runtime',c.oid,'SELECT') AS runtime_select,
              has_table_privilege('app_support',c.oid,'SELECT,INSERT,UPDATE') AS support_access
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND c.relname=:table
        """),{"table":table}).mappings().one()
        if not (row["relrowsecurity"] and row["relforcerowsecurity"] and
                not row["runtime_select"] and row["support_access"]):
            raise RuntimeError("Account table is missing the expected RLS/grants")
    attempts=conn.execute(text("""
        SELECT EXISTS(SELECT 1 FROM pg_attribute WHERE
          attrelid='public.panel_email_tokens'::regclass
          AND attname='attempts' AND NOT attisdropped)
    """)).scalar_one()
    if not attempts:
        raise RuntimeError("Email code attempt counter is missing")
    print("ORION_ACCOUNT_MIGRATION_READY_RLS_RESTRICTED")


def main():
    url=migration_dsn()
    engine=create_engine(url,pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            current=state(conn)
            print("ORION_MIGRATION_STARTING_REVISION="+current)
            conn.rollback()
        if current != TARGET:
            command.upgrade(Config("alembic.ini"),TARGET)
        with engine.connect() as conn:
            verify(conn)
            conn.rollback()
    finally:
        engine.dispose()


if __name__=="__main__":
    main()
