"""Hermetic mail contracts. Every HTTP call is replaced; no provider traffic."""
import json
import traceback
from types import SimpleNamespace
from uuid import uuid4

from cryptography.fernet import Fernet
import httpx
import pytest
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from app import email_transport as transport, email_outbox as outbox


@pytest.fixture(autouse=True)
def mail_settings(monkeypatch):
    for name, value in {
        "PANEL_PUBLIC_URL": "https://orion.example.test",
        "PANEL_EMAIL_ENABLED": "1", "PANEL_EMAIL_LIVE_SEND": "1",
        "PANEL_EMAIL_TRANSPORT": "resend_https", "PANEL_SMTP_PASSWORD": "unit-only-provider-key",
        "PANEL_SMTP_FROM": "Orion <acesso@orion.example.test>",
        "PANEL_OTP_SECRET": "unit-only-otp-secret-at-least-thirty-two-characters",
        "PANEL_EMAIL_FERNET_KEY": Fernet.generate_key().decode(), "PANEL_TEST_MODE": "0",
    }.items():
        monkeypatch.setenv(name, value)
    class NoNetwork:
        def __init__(self, *args, **kwargs):
            raise AssertionError("Unexpected real HTTP request")
    monkeypatch.setattr(transport.httpx, "AsyncClient", NoNetwork)


@pytest.mark.parametrize("kind", sorted(transport.KINDS))
def test_all_templates_have_text_html_canonical_url_and_escape_business(kind):
    payload = transport.render_message("Person@EXAMPLE.TEST", kind, code="012345",
        invitation_token="A" * 43, business_name='<img src=x onerror="bad()"> & Cia')
    assert set(payload) == {"from", "to", "subject", "text", "html"}
    assert payload["to"] == ["person@example.test"]
    assert "https://orion.example.test/" in payload["html"] and "https://orion.example.test/" in payload["text"]
    assert "<img src=x" not in payload["html"]
    assert "senha" not in payload["subject"].lower() or kind in {"reset", "password_changed"}
    if kind in {"verify", "reset"}:
        assert "012345" in payload["text"] and "012345" in payload["html"]
    else:
        assert "012345" not in payload["html"]
    if kind == "team_invite":
        assert "/convite#token=" + "A" * 43 in payload["text"]
        assert "&lt;img" in payload["html"]


@pytest.mark.parametrize("url", ["", "http://example.test", "https://example.test/path", "https://u:p@example.test", "https://example.test?q=x", "https://example.test/#x"])
def test_readiness_and_rendering_both_reject_invalid_origins(monkeypatch, url):
    monkeypatch.setenv("PANEL_PUBLIC_URL", url)
    assert not transport.configured()
    with pytest.raises(ValueError):
        transport.render_message("person@example.test", "reset", code="123456")


@pytest.mark.parametrize("name,value", [("PANEL_EMAIL_ENABLED", "0"), ("PANEL_EMAIL_LIVE_SEND", "0"),
    ("PANEL_EMAIL_TRANSPORT", "smtp"), ("PANEL_EMAIL_FERNET_KEY", "bad"), ("PANEL_SMTP_PASSWORD", ""),
    ("PANEL_OTP_SECRET", "short"), ("PANEL_SMTP_FROM", "Orion\r\nBcc: x@example.test")])
def test_readiness_fails_closed(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    assert not transport.configured()


@pytest.mark.parametrize("recipient", ["a@example.test\r\nBcc: victim@example.test", "a@example.test,b@example.test", "<a@example.test>", "missing", "x" * 255])
def test_invalid_recipients_never_reach_templates(recipient):
    with pytest.raises(ValueError):
        transport.render_message(recipient, "reset", code="123456")


@pytest.mark.parametrize("kind,kwargs", [("anything", {}), ("verify", {"code": "123456<script>"}), ("team_invite", {"invitation_token": "bad"})])
def test_unknown_template_or_invalid_secret_is_rejected(kind, kwargs):
    with pytest.raises(ValueError):
        transport.render_message("person@example.test", kind, **kwargs)


def fake_client(monkeypatch, handler):
    class Client:
        def __init__(self, **kwargs):
            assert kwargs["timeout"] <= 15 and kwargs["follow_redirects"] is False
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def post(self, url, **kwargs):
            return await handler(url, **kwargs)
    monkeypatch.setattr(transport.httpx, "AsyncClient", Client)


@pytest.mark.asyncio
async def test_provider_retries_use_exact_same_payload_and_idempotency_key(monkeypatch):
    calls = []
    async def handler(url, **kwargs):
        calls.append((url, kwargs))
        return httpx.Response(200, json={"id": "provider-uuid"})
    fake_client(monkeypatch, handler)
    payload = transport.render_message("person@example.test", "reset", code="123456")
    item = uuid4()
    assert await transport.send_message(payload, item) == "provider-uuid"
    monkeypatch.setenv("PANEL_SMTP_FROM", "Other <other@orion.example.test>")
    assert await transport.send_message(payload, item) == "provider-uuid"
    assert calls[0] == calls[1]
    assert calls[0][1]["headers"]["Idempotency-Key"] == "orion-email/" + str(item)
    assert calls[0][0] == "https://api.resend.com/emails"


@pytest.mark.asyncio
@pytest.mark.parametrize("status,body,retryable,ambiguous", [(429, {"secret": "provider-secret"}, True, False),
    (503, {}, True, True), (302, {"secret": "provider-secret"}, False, False), (403, {"secret": "provider-secret"}, False, False),
    (409, {"name": "invalid_idempotent_request"}, False, False),
    (409, {"name": "concurrent_idempotent_requests"}, True, False),
    (200, [], True, True), (200, {}, True, True)])
async def test_provider_failures_are_finite_sanitized_and_classified(monkeypatch, status, body, retryable, ambiguous):
    async def handler(url, **kwargs):
        return httpx.Response(status, json=body)
    fake_client(monkeypatch, handler)
    payload = transport.render_message("person@example.test", "reset", code="123456")
    with pytest.raises(transport.EmailProviderError) as error:
        await transport.send_message(payload, uuid4())
    assert error.value.retryable is retryable and error.value.ambiguous is ambiguous
    assert "provider-secret" not in str(error.value) and "123456" not in str(error.value)


@pytest.mark.asyncio
async def test_timeout_is_ambiguous_and_never_echoes_exception_text(monkeypatch):
    async def handler(url, **kwargs):
        raise httpx.ReadTimeout("private recipient, OTP and provider key")
    fake_client(monkeypatch, handler)
    with pytest.raises(transport.EmailProviderError) as error:
        await transport.send_message(transport.render_message("person@example.test", "reset", code="123456"), uuid4())
    assert error.value.ambiguous and error.value.retryable
    assert str(error.value) == "email_transport_ambiguous"
    rendered = "".join(traceback.format_exception(error.value))
    assert "private recipient, OTP and provider key" not in rendered


@pytest.mark.asyncio
async def test_direct_transport_enforces_live_and_test_recipient_guards(monkeypatch):
    payload = transport.render_message("person@example.test", "reset", code="123456")
    monkeypatch.setenv("PANEL_EMAIL_LIVE_SEND", "0")
    with pytest.raises(transport.EmailProviderError, match="email_disabled"):
        await transport.send_message(payload, uuid4())
    monkeypatch.setenv("PANEL_EMAIL_LIVE_SEND", "1")
    monkeypatch.setenv("PANEL_TEST_MODE", "1")
    monkeypatch.setenv("PANEL_TEST_EMAIL", "pilot@example.test")
    with pytest.raises(transport.EmailProviderError, match="email_recipient_suppressed"):
        await transport.send_message(payload, uuid4())


class CapturingDB:
    def __init__(self):
        self.calls = []
    async def execute(self, sql, params):
        self.calls.append((str(sql), params))
        return SimpleNamespace(mappings=lambda: SimpleNamespace(one=lambda: {"status": "pending"}))


@pytest.mark.asyncio
async def test_queue_contains_only_ciphertext_not_mailbox_code_or_link():
    db = CapturingDB()
    result = await outbox.enqueue(db, kind="reset", recipient="person@example.test", code="123456", dedupe_key="unit-token")
    assert result["status"] == "queued"
    params = db.calls[0][1]
    serialized = json.dumps(params, default=str)
    assert "person@example.test" not in serialized and "123456" not in serialized and "https://" not in serialized
    decrypted = json.loads(transport.cipher().decrypt(params["payload"].encode()))
    assert decrypted["to"] == ["person@example.test"] and "123456" in decrypted["text"]


@pytest.mark.asyncio
async def test_disabled_events_are_not_accumulated_and_worker_never_opens_database(monkeypatch):
    monkeypatch.setenv("PANEL_EMAIL_LIVE_SEND", "0")
    db = CapturingDB()
    assert (await outbox.enqueue(db, kind="reset", recipient="person@example.test", code="123456", dedupe_key="disabled"))["status"] == "disabled"
    assert not db.calls
    assert await outbox.process_email_outbox({}, str(uuid4())) == "disabled"


@pytest.mark.asyncio
async def test_request_quotas_are_recipient_and_global_not_ip_or_account_dependent():
    calls = []
    class Cache:
        async def eval(self, script, count, *args):
            calls.append((script, count, args))
            return 0
    assert not await outbox.request_quota(Cache(), "person@example.test")
    _, count, args = calls[0]
    assert count == 2 and args[1] == "orion:email:request:global"
    assert "person@example.test" not in str(args) and args[0].endswith(outbox.recipient_digest("person@example.test"))


@pytest.mark.asyncio
async def test_redis_quota_failure_is_closed_and_sanitized():
    from fastapi import HTTPException
    class Cache:
        async def eval(self, *args):
            raise RedisError("private connection value")
    with pytest.raises(HTTPException) as error:
        await outbox.request_quota(Cache(), "person@example.test")
    assert error.value.status_code == 503 and "private" not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("live", ["0", "1"])
async def test_pre_migration_scanner_only_checks_table_presence(monkeypatch, live):
    from app import catalog_api
    monkeypatch.setenv("PANEL_EMAIL_LIVE_SEND", live)
    statements = []
    class DB:
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        def begin(self):return self
        async def execute(self, sql):
            statements.append(str(sql))
            return SimpleNamespace(scalar_one=lambda: None)
    class Cache:
        async def set(self,*args,**kwargs):pass
    monkeypatch.setattr(catalog_api,"support_session_factory",lambda: DB)
    assert await outbox.scan_email_outbox({"redis":Cache()}) == 0
    assert statements == ["SELECT to_regclass('public.panel_email_outbox')"]


@pytest.mark.asyncio
async def test_pre_migration_scanner_does_not_swallow_other_database_errors(monkeypatch):
    from app import catalog_api
    class DB:
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        def begin(self):return self
        async def execute(self,sql):raise SQLAlchemyError("synthetic_sql_failure")
    class Cache:
        async def set(self,*args,**kwargs):pass
    monkeypatch.setattr(catalog_api,"support_session_factory",lambda: DB)
    with pytest.raises(SQLAlchemyError,match="synthetic_sql_failure"):
        await outbox.scan_email_outbox({"redis":Cache()})
