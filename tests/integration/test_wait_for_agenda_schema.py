"""Worker barrier against real PG16 migrations in a disposable UUID database."""
import asyncio
import os
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy.engine import make_url

from scripts import wait_for_agenda_schema as gate
from test_business_migration_ops_postgres import migration_source  # noqa: F401

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]


def test_runtime_catalog_probe_waits_for_committed_0019_without_reading_customer_data(migration_source, monkeypatch):
    engine, previous = migration_source
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    command.upgrade(config, "0018_sales_requests")
    runtime_url = make_url(os.environ["APP_DATABASE_URL"]).set(
        host=previous.url.host, port=previous.url.port, database=previous.url.database,
    )
    monkeypatch.setenv("APP_DATABASE_URL", runtime_url.render_as_string(hide_password=False))
    settings = gate.Settings.from_environment()
    with engine.begin() as conn:
        # Catalog visibility must suffice even without permission to read the
        # appointment history or any customer's contact details.
        conn.exec_driver_sql("REVOKE SELECT ON public.appointment_history,public.customers FROM app_runtime")
        assert conn.exec_driver_sql("SELECT version_num FROM public.alembic_version").scalar_one() == "0018_sales_requests"
    assert asyncio.run(gate.probe_schema(settings, 3.0)) is False
    with engine.begin() as conn:
        assert conn.exec_driver_sql("SELECT version_num FROM public.alembic_version").scalar_one() == "0018_sales_requests"
    # Merely having the column is insufficient: nullable JSONB and NOT NULL text
    # must both remain unready. Remove each synthetic column before upgrading.
    for definition in ("jsonb", "text NOT NULL DEFAULT '{}'::text"):
        with engine.begin() as conn:
            conn.exec_driver_sql("ALTER TABLE public.appointment_history ADD COLUMN details " + definition)
        try:
            assert asyncio.run(gate.probe_schema(settings, 3.0)) is False
        finally:
            with engine.begin() as conn:
                conn.exec_driver_sql("ALTER TABLE public.appointment_history DROP COLUMN details")
    command.upgrade(config, "0019_appointment_lifecycle")
    assert asyncio.run(gate.wait_for_schema(settings)) == {"ok": True, "status": "agenda_schema_ready"}
    with engine.begin() as conn:
        assert conn.exec_driver_sql("SELECT version_num FROM public.alembic_version").scalar_one() == "0019_appointment_lifecycle"
