"""One-time migration must be explicit, bounded and role-restricted."""
import pytest

from scripts import one_time_account_migration as migration


def test_migration_requires_opt_in_and_role_switch(monkeypatch):
    monkeypatch.delenv("ORION_RUN_ACCOUNT_MIGRATION",raising=False)
    monkeypatch.setenv("MIGRATION_DATABASE_URL","postgresql://x:y@localhost:5432/test")
    monkeypatch.setenv("ORION_MIGRATION_SWITCH_ROLE","1")
    with pytest.raises(RuntimeError,match="not explicitly enabled"):
        migration.migration_dsn()
    monkeypatch.setenv("ORION_RUN_ACCOUNT_MIGRATION","1")
    monkeypatch.setenv("ORION_MIGRATION_SWITCH_ROLE","0")
    with pytest.raises(RuntimeError,match="role switch"):
        migration.migration_dsn()
    monkeypatch.setenv("ORION_MIGRATION_SWITCH_ROLE","1")
    assert migration.migration_dsn().drivername=="postgresql+psycopg"


@pytest.mark.integration
def test_database_account_schema_matches_restricted_migration(monkeypatch):
    # CI applies Alembic upgrade head under app_migrator before running tests.
    monkeypatch.setenv("ORION_RUN_ACCOUNT_MIGRATION","1")
    monkeypatch.setenv("ORION_MIGRATION_SWITCH_ROLE","1")
    engine = migration.create_engine(migration.migration_dsn())
    try:
        with engine.connect() as connection:
            current = connection.execute(migration.text("SELECT version_num FROM alembic_version")).scalar_one()
        if current == migration.TARGET:
            migration.main()
        else:
            # Future additive schema work must not silently reuse this older operation.
            with pytest.raises(RuntimeError, match="Unexpected Alembic revision"):
                migration.main()
    finally:
        engine.dispose()
