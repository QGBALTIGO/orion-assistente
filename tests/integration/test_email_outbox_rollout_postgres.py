"""Read-only worker qualification against a preserved pre0038 clone."""
import os

from cryptography.fernet import Fernet
import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app import catalog_api, email_outbox, email_transport

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
@pytest.mark.parametrize("live", ["0", "1"])
async def test_worker_before_api_migration_is_a_read_only_noop(monkeypatch, live):
    raw = os.getenv("EMAIL_PRE_MIGRATION_DATABASE_URL")
    if not raw:
        if os.getenv("REQUIRE_POSTGRES_TESTS") == "1":
            pytest.fail("A preserved isolated0037 clone is required")
        pytest.skip("Requires a preserved isolated0037 clone")
    parsed = make_url(raw)
    assert parsed.host in {"127.0.0.1", "localhost"} and (parsed.database or "").endswith("_e2e")
    for name, value in {
        "PANEL_PUBLIC_URL": "https://orion.example.test", "PANEL_EMAIL_ENABLED": "1",
        "PANEL_EMAIL_LIVE_SEND": live, "PANEL_EMAIL_TRANSPORT": "resend_https",
        "PANEL_SMTP_FROM": "Orion <acesso@orion.example.test>",
        "PANEL_SMTP_PASSWORD": "synthetic-not-a-real-provider-key",
        "PANEL_OTP_SECRET": "synthetic-stable-otp-secret-more-than-thirty-two-characters",
        "PANEL_EMAIL_FERNET_KEY": Fernet.generate_key().decode(), "PANEL_TEST_MODE": "0",
    }.items():
        monkeypatch.setenv(name, value)
    engine = create_async_engine(raw, connect_args={"server_settings": {"default_transaction_read_only": "on"}})
    # app_support deliberately cannot read alembic_version. Verify revision
    # separately with the existing migrator identity, also forced read-only.
    oracle_url = make_url(os.environ["MIGRATION_DATABASE_URL"]).set(database=parsed.database, drivername="postgresql+asyncpg")
    oracle = create_async_engine(oracle_url, connect_args={"server_settings": {"default_transaction_read_only": "on"}})
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(catalog_api, "support_session_factory", lambda: factory)
    async def forbidden(*args, **kwargs):
        raise AssertionError("Email egress is forbidden before migration0038")
    monkeypatch.setattr(email_transport, "send_message", forbidden)
    class Cache:
        async def set(self, *args, **kwargs):pass
        async def enqueue_job(self, *args, **kwargs):
            raise AssertionError("No job may be created before migration0038")
    try:
        async with oracle.connect() as db:
            before = (await db.execute(text("SELECT version_num FROM alembic_version"))).scalar_one()
            assert before == "0037_recurring_goals"
            assert (await db.execute(text("SELECT to_regclass('public.panel_email_outbox')"))).scalar_one() is None
        assert email_transport.configured() is (live == "1")
        assert await email_outbox.scan_email_outbox({"redis": Cache()}) == 0
        async with oracle.connect() as db:
            assert (await db.execute(text("SELECT version_num FROM alembic_version"))).scalar_one() == before
            assert (await db.execute(text("SELECT to_regclass('public.panel_email_outbox')"))).scalar_one() is None
    finally:
        await engine.dispose()
        await oracle.dispose()
