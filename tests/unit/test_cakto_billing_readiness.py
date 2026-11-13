"""Every payment-readiness condition must be present before a checkout is offered."""
import pytest

from app.cakto_readiness import REQUIRED_FLAGS, direct_environment_ready, webhook_secret_valid
from app import cakto_api, orion_direct_checkout
from app.catalog_api import Actor

CREDENTIALS = ("CAKTO_CLIENT_ID", "CAKTO_CLIENT_SECRET", "CAKTO_WEBHOOK_SECRET")


def clear(monkeypatch):
    for name in (*REQUIRED_FLAGS, *CREDENTIALS):
        monkeypatch.delenv(name, raising=False)


def ready(monkeypatch):
    for name in REQUIRED_FLAGS:
        monkeypatch.setenv(name, "1")
    monkeypatch.setenv("CAKTO_CLIENT_ID", "synthetic-id")
    monkeypatch.setenv("CAKTO_CLIENT_SECRET", "synthetic-secret")
    monkeypatch.setenv("CAKTO_WEBHOOK_SECRET", "synthetic-hmac-secret-with-over-32-characters")


def test_single_live_flag_does_not_advertise_checkout(monkeypatch):
    clear(monkeypatch)
    monkeypatch.setenv("CAKTO_BILLING_LIVE_ENABLED", "1")
    assert not direct_environment_ready()
    assert not orion_direct_checkout.checkout_enabled()


@pytest.mark.parametrize("missing", [*REQUIRED_FLAGS, *CREDENTIALS])
def test_any_missing_payment_guard_keeps_checkout_off(monkeypatch, missing):
    clear(monkeypatch)
    ready(monkeypatch)
    assert direct_environment_ready()
    monkeypatch.delenv(missing)
    assert not direct_environment_ready(), missing
    assert not orion_direct_checkout.checkout_enabled(), missing


def test_short_webhook_secret_is_not_configured(monkeypatch):
    clear(monkeypatch)
    ready(monkeypatch)
    monkeypatch.setenv("CAKTO_WEBHOOK_SECRET", "too-short")
    assert not webhook_secret_valid()
    assert not direct_environment_ready()


@pytest.mark.asyncio
async def test_admin_connection_does_not_call_provider_when_oauth_is_missing(monkeypatch):
    class Unconfigured:
        configured = False
    monkeypatch.setattr(cakto_api, "cached_cakto_client", lambda: Unconfigured())
    monkeypatch.setenv("CAKTO_WEBHOOK_SECRET", "short")
    result = await cakto_api.admin_cakto_connection(Actor("support"))
    assert result["api_connected"] is False
    assert result["webhook_secret_configured"] is False
    assert result["webhook_offer_coverage"] == 0
    assert result["billing_ready"] is False
    assert "secret" not in str(result).lower().replace("webhook_secret_configured", "")


def test_offer_matcher_rejects_malformed_provider_ids():
    rows=[{"name":"Orion Pro","price":"89.90","type":"subscription",
           "recurrence_period":30,"status":"active"}]
    assert not cakto_api.match_plans(rows)[1]["found"]
