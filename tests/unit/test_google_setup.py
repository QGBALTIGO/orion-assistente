"""Configuration diagnosis never substitutes for an actual Google authorization."""
import json

import pytest
from cryptography.fernet import Fernet

from app.google_oauth import configuration_checklist, google_configured, oauth_config
from fastapi import HTTPException
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


@pytest.mark.parametrize("origin", ["production", "orion.example.test", "https:orion.example.test", "//orion.example.test",
    "http://orion.example.test", "https://other.example.test", "https://[broken"])
def test_nonempty_invalid_public_origin_never_reports_ready(configured, monkeypatch, capsys, origin):
    monkeypatch.setenv("PANEL_PUBLIC_URL", origin)
    report = configuration_checklist()
    assert report["configured"] is False and report["next_step"] == "prepare_server"
    assert next(item for item in report["checks"] if item["id"] == "GOOGLE_REDIRECT_URI")["ready"] is False
    assert google_configured() is False
    with pytest.raises(HTTPException) as caught: oauth_config()
    assert caught.value.status_code == 503
    assert main([]) == 2 and json.loads(capsys.readouterr().out)["configured"] is False


@pytest.mark.parametrize("origin", [None, "", "https://orion.example.test"])
def test_valid_or_optional_public_origin_matches_runtime_without_claiming_external_access(configured, monkeypatch, capsys, origin):
    if origin is None: monkeypatch.delenv("PANEL_PUBLIC_URL")
    else: monkeypatch.setenv("PANEL_PUBLIC_URL", origin)
    report = configuration_checklist()
    assert report["configured"] is True and google_configured() is True
    assert oauth_config()[2] == "https://orion.example.test/integrations/google/callback"
    assert not report["sync_enabled"] and not report["external_authorization_verified"]
    assert main([]) == 0 and json.loads(capsys.readouterr().out)["configured"] is True


@pytest.mark.parametrize("name,value", [
    ("GOOGLE_REDIRECT_URI", "http://orion.example.test/integrations/google/callback"),
    ("GOOGLE_REDIRECT_URI", "https://orion.example.test/wrong"),
    ("GOOGLE_REDIRECT_URI", "https://orion.example.test/integrations/google/callback#private"),
    ("GOOGLE_REDIRECT_URI", "https://orion.example.test/integrations/google/callback?code=private"),
    ("GOOGLE_REDIRECT_URI", "https://user:private@orion.example.test/integrations/google/callback"),
    ("INTEGRATION_FERNET_KEY", "private-invalid-value"),
])
def test_readiness_and_runtime_both_reject_callback_or_key_without_echo(configured, monkeypatch, capsys, name, value):
    monkeypatch.setenv(name, value)
    assert not configuration_checklist()["configured"] and not google_configured()
    with pytest.raises(HTTPException) as caught: oauth_config()
    assert caught.value.status_code == 503
    assert main([]) == 2
    output = capsys.readouterr().out
    assert not json.loads(output)["configured"] and "private" not in output and "synthetic-private-client-secret" not in output
