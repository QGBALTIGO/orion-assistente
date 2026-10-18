"""QR identity contracts without a database, session or outbound network."""
from dataclasses import asdict
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from pydantic import ValidationError
from cryptography.fernet import Fernet

from app import qr_channels as channels


def payload(**extra):
    return {"label": "Canal sintético", "allowed_senders": ["+5567999999901"],
            "idempotency_key": str(uuid4()), **extra}


@pytest.mark.parametrize("extra", [
    {"tenant_id": str(uuid4())}, {"owner_account_id": str(uuid4())}, {"enabled": True}, {"assistant_enabled": True},
    {"credential": "injected"}, {"lease_owner": str(uuid4())}, {"provider": "invented"},
    {"allowed_senders": []}, {"allowed_senders": ["120363000@g.us"]},
    {"allowed_senders": ["123456789@lid"]}, {"allowed_senders": ["+5567999999901", "5567999999901"]},
    {"allowed_senders": ["+5567999999901"] * 11}, {"label": "   "}, {"label": "Name\nsecret"},
])
def test_registration_rejects_authority_and_invalid_policy(extra):
    with pytest.raises(ValidationError):
        channels.CreateChannel.model_validate(payload(**extra))


def test_registration_keeps_only_explicit_normalized_allowlist():
    value = channels.CreateChannel.model_validate(payload())
    assert value.allowed_senders == ["5567999999901"]
    assert "enabled" not in value.model_dump()


def test_connection_and_assistant_activation_are_explicit_separate_changes():
    connection = channels.ChannelState(expected_revision=1, enabled=True)
    assert connection.assistant_enabled is None
    assistant = channels.ChannelState(expected_revision=2, assistant_enabled=True)
    assert assistant.enabled is None
    with pytest.raises(ValidationError):
        channels.ChannelState(expected_revision=1)


@pytest.mark.parametrize("value", [None, "", "short", "x"*257])
def test_credentials_fail_closed(value):
    with pytest.raises(HTTPException) as exc:
        channels.credential_hash(value)
    assert exc.value.status_code == 401


def test_encryption_requires_explicit_config_and_is_not_plaintext(monkeypatch):
    monkeypatch.delenv("INTEGRATION_FERNET_KEY", raising=False)
    with pytest.raises(HTTPException) as exc:
        channels._cipher()
    assert exc.value.status_code == 503
    monkeypatch.setenv("INTEGRATION_FERNET_KEY", Fernet.generate_key().decode())
    credential = "synthetic-private-credential-" + "x"*40
    ciphertext = channels._cipher().encrypt(credential.encode())
    assert credential.encode() not in ciphertext
    assert channels._cipher().decrypt(ciphertext).decode() == credential


def test_context_namespace_separates_channels_with_same_tenant_and_contact():
    tenant, account, owner = uuid4(), uuid4(), uuid4()
    a = channels.ChannelIdentity(uuid4(), tenant, account, "baileys", 1, owner, True)
    b = channels.ChannelIdentity(uuid4(), tenant, account, "baileys", 2, owner, True)
    assert a.context_namespace != b.context_namespace
    assert "credential" not in asdict(a)


@pytest.mark.asyncio
async def test_disabled_routes_never_request_database_or_external_services(monkeypatch):
    monkeypatch.delenv("QR_CHANNELS_ENABLED", raising=False)
    def unexpected():
        raise AssertionError("database must not be used")
    monkeypatch.setattr(channels, "support_session_factory", unexpected)
    api = FastAPI(); api.include_router(channels.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="https://test") as client:
        for method, url, body in [
            ("GET", f"/api/v1/tenants/{uuid4()}/qr-channels", None),
            ("POST", f"/internal/qr/channels/{uuid4()}/lease/claim", {"owner": str(uuid4())}),
            ("GET", f"/internal/qr/channels/{uuid4()}/identity", None),
        ]:
            result = await client.request(method, url, json=body)
            assert result.status_code == 423


@pytest.mark.parametrize("field,value", [("fence", True), ("fence", 0), ("fence", "1"), ("tenant_id", str(uuid4()))])
def test_lease_contract_cannot_smuggle_tenant_or_coerce_fence(field, value):
    with pytest.raises(ValidationError):
        channels.LeaseRequest.model_validate({"owner": str(uuid4()), field: value})


@pytest.mark.asyncio
async def test_non_account_support_identity_cannot_create_channel():
    class Database:
        async def execute(self, *args, **kwargs):
            raise AssertionError("not authorized to query memberships")
    with pytest.raises(HTTPException) as exc:
        await channels._owner(Database(), uuid4(), SimpleNamespace(role="support", account_id=None))
    assert exc.value.status_code == 403
