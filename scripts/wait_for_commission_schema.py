"""Wait for the verified 0033 schema before starting API/worker.

Read-only barrier for coordinated Railway deployments. Never upgrades the DB.
"""
import os
import sys
import time
from sqlalchemy.engine import make_url
from scripts.apply_professional_commission_migration import TARGET, migrated_version


def main():
    raw = os.getenv("MIGRATION_DATABASE_URL", "")
    if raw.startswith("postgres://"):
        raw = raw.replace("postgres://", "postgresql+psycopg://", 1)
    elif raw.startswith("postgresql://"):
        raw = raw.replace("postgresql://", "postgresql+psycopg://", 1)
    try:
        url = make_url(raw)
    except Exception:
        print("commission_schema_barrier_no_database", file=sys.stderr)
        return 2
    if (url.drivername != "postgresql+psycopg"
            or url.host != os.getenv("ORION_EXPECTED_DATABASE_HOST")
            or url.database != os.getenv("ORION_EXPECTED_DATABASE_NAME")
            or not url.username or not url.password):
        print("commission_schema_barrier_wrong_scope", file=sys.stderr)
        return 2
    for index in range(120):
        try:
            if migrated_version(raw) == TARGET:
                print("commission_schema_0033_verified", flush=True)
                return 0
        except Exception:
            pass
        if index < 119:
            time.sleep(4)
    print("commission_schema_barrier_timeout", file=sys.stderr)
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
