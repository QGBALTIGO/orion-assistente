"""Configuration diagnosis never substitutes for an actual Google authorization."""
import json

import pytest
from cryptography.fernet import Fernet

from app.google_oauth import configuration_checklist
from scripts.check_google_setup import main


@pytest.fixture
def configured(monkeypatch):
    for key, value in {"GOOGLE_CLIENT_ID": "synthetic-client.apps.googleusercontent.com",
                       "GOOGLE_CLIENT_SECRET": "synthetic-private-client-secret",
                       "INTEGRATION_FERNET_KEY": Fernet.generate_key().decode(),
                       "GOOGLE_REDIRECT_URI": "https://orion.example.test/integrations/google/callback",
                       "PANEL_PUBLIC_URL": "https://orion.example.test", "GOOGLE_LIVE_SYNC": "0"}.items():
        monkeypatch.setenv(key, value)


def test_ready_configuration_does_not_claim_authorization_or_sync(configured):
    result = configuration_checklist()
    assert result["configured"] and not result["external_authorization_verified"]
    assert result["next_step"] == "authorize_account" and not result["sync_enabled"]


@pytest.mark.parametrize("key", ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "INTEGRATION_FERNET_KEY", "GOOGLE_REDIRECT_URI"])
def test_each_missing_requirement_is_actionable_without_secret_values(configured, monkeypatch, key):
    monkeypatch.delenv(key)
    result = configuration_checklist()
    assert not result["configured"] and result["next_step"] == "prepare_server"
    assert next(item for item in result["checks"] if item["id"] == key)["ready"] is False
    assert "synthetic-private-client-secret" not in json.dumps(result)


@pytest.mark.parametrize("url", ["http://orion.example.test/integrations/google/callback",
    "https://other.example.test/integrations/google/callback", "https://orion.example.test/panel",
    "https://orion.example.test/integrations/google/callback?code=private", "https://user:password@orion.example.test/integrations/google/callback",
    "https://[broken/integrations/google/callback"])
def test_incorrect_callback_stays_unconfigured(configured, monkeypatch, url):
    monkeypatch.setenv("GOOGLE_REDIRECT_URI", url)
    assert not configuration_checklist()["configured"]


def test_invalid_encryption_key_is_reported_without_echo(configured, monkeypatch):
    monkeypatch.setenv("INTEGRATION_FERNET_KEY", "private-invalid-value")
    result = configuration_checklist()
    assert not result["configured"] and "private-invalid-value" not in json.dumps(result)


def test_worker_does_not_require_browser_callback(configured, monkeypatch):
    monkeypatch.delenv("GOOGLE_REDIRECT_URI")
    assert configuration_checklist(worker=True)["configured"]
    assert not configuration_checklist()["configured"]


def test_checker_is_read_only_and_returns_failure_for_missing_configuration(monkeypatch, capsys):
    for key in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "INTEGRATION_FERNET_KEY", "GOOGLE_REDIRECT_URI"):
        monkeypatch.delenv(key, raising=False)
    assert main([]) == 2
    result = json.loads(capsys.readouterr().out)
    assert not result["configured"] and not result["external_authorization_verified"]
