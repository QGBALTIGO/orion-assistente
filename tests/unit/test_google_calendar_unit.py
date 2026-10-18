"""Google protocol contracts through MockTransport; never live credentials."""
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from cryptography.fernet import Fernet
from fastapi import HTTPException
import httpx
import pytest

from app.calendar_provider import (
    API, GOOGLE_SCOPES, CalendarProviderError, GoogleCalendar, canonical_calendar,
    deterministic_event_id, event_payload, provider_error,
)
from app.google_oauth import google_configured, make_authorization_url, oauth_config


def booking(**changes):
    start = datetime(2026, 10, 20, 10, tzinfo=timezone(timedelta(hours=-3)))
    return {"id": uuid4(), "tenant_id": uuid4(), "protocol": "AG-ABC123", "status": "confirmed",
            "start_at": start, "end_at": start + timedelta(minutes=40), "timezone": "America/Sao_Paulo",
            "customer_name": "Ana sintética", "service_names": "Corte sintético",
            "professional_name": "Profissional <sintético>", "customer_phone": "+5511999999999",
            "notes": "PRIVATE_INTERNAL_NOTE", "cancellation_reason": "PRIVATE_REASON", **changes}


def owned(row, **changes):
    return {"id": deterministic_event_id(row["id"]), "etag": '"version-one"',
            "extendedProperties": {"private": {"appointment_id": str(row["id"]),
                                                "tenant_id": str(row["tenant_id"]) }}, **changes}


@pytest.fixture
def configured(monkeypatch):
    for name, value in {"GOOGLE_CLIENT_ID": "synthetic-client", "GOOGLE_CLIENT_SECRET": "synthetic-secret",
                        "GOOGLE_REDIRECT_URI": "https://panel.example.test/integrations/google/callback",
                        "PANEL_PUBLIC_URL": "https://panel.example.test", "INTEGRATION_FERNET_KEY": Fernet.generate_key().decode()}.items():
        monkeypatch.setenv(name, value)


@pytest.mark.parametrize("status,label,color,transparency", [
    ("confirmed", "Confirmado", "9", "opaque"), ("completed", "Finalizado", "10", "opaque"),
    ("no_show", "Não compareceu", "11", "transparent"),
])
def test_event_contains_only_consented_metadata_and_real_state(status, label, color, transparency):
    row = booking(status=status)
    payload = event_payload(deterministic_event_id(row["id"]), row)
    assert payload["summary"] == f"{label} · Ana sintética · Corte sintético"
    assert payload["status"] == "confirmed" and payload["visibility"] == "private"
    assert payload["colorId"] == color and payload["transparency"] == transparency
    assert payload["extendedProperties"]["private"]["orion_status"] == status
    assert payload["start"]["dateTime"].endswith("-03:00")
    assert payload["start"]["timeZone"] == "America/Sao_Paulo"
    assert "&lt;sintético&gt;" in payload["description"]
    assert payload["reminders"] == {"useDefault": False}
    serialized = json.dumps(payload)
    for forbidden in ("attendees", "PRIVATE_INTERNAL_NOTE", "PRIVATE_REASON", "+5511999999999"):
        assert forbidden not in serialized


@pytest.mark.parametrize("name", [None, "Cliente WhatsApp", "contato do whatsapp", "+5511999999999", "123456@lid"])
def test_placeholder_is_not_exported_as_customer_identity(name):
    row = booking(customer_name=name)
    assert event_payload("orion123", row)["summary"] == "Confirmado · Corte sintético"


def test_labels_bounded_and_no_directional_controls_or_internal_data():
    row = booking(customer_name="A" * 80, service_names="S" * 300, professional_name="P\u202e<script>\n" * 40)
    data = event_payload("orion123", row)
    assert len(data["summary"]) <= 240 and len(data["description"]) < 250
    assert "\u202e" not in data["description"] and "<script>" not in data["description"]
    with pytest.raises(CalendarProviderError, match="invalid_time"):
        event_payload("orion123", booking(start_at=datetime(2026, 1, 1)))


@pytest.mark.parametrize("reason", [{"private": "do-not-echo"}, ["bad"], None, 1])
def test_malformed_google_error_reason_cannot_throw_or_leak(reason):
    error = provider_error(httpx.Response(403, json={"error": {"errors": [{"reason": reason}]}}))
    assert error.code == "google_permission_denied"
    assert str(error) == "google_permission_denied"


@pytest.mark.parametrize("role", ["reader", "freeBusyReader", "writerWithoutPrivateAccess", {}, [], None])
def test_unusable_or_malformed_roles_are_not_offered(role):
    assert canonical_calendar({"id": "calendar@example.invalid", "accessRole": role}) is None


@pytest.mark.asyncio
async def test_calendar_list_pages_and_resolves_primary_to_canonical_identity():
    calls = []
    def handler(request):
        calls.append(request)
        if request.url.path.endswith("/primary"):
            return httpx.Response(200, json={"id": "owner@example.invalid", "accessRole": "owner", "primary": True})
        assert request.url.params["minAccessRole"] == "writer" and request.url.params["maxResults"] == "250"
        if "pageToken" not in request.url.params:
            return httpx.Response(200, json={"items": [{"id": "reader", "accessRole": "reader"},
                {"id": "owner@example.invalid", "accessRole": "owner", "summary": "Agenda sintética", "primary": True}], "nextPageToken": "second"})
        assert request.url.params["pageToken"] == "second"
        return httpx.Response(200, json={"items": [{"id": "shared@example.invalid", "accessRole": "writer"}]})
    provider = GoogleCalendar(transport=httpx.MockTransport(handler))
    rows = await provider.list_calendars("synthetic-access")
    assert [row["id"] for row in rows] == ["owner@example.invalid", "shared@example.invalid"]
    assert (await provider.get_calendar("synthetic-access", "primary"))["id"] == "owner@example.invalid"
    assert all(request.method == "GET" for request in calls)


@pytest.mark.asyncio
async def test_calendar_list_has_a_bounded_page_limit():
    provider = GoogleCalendar(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"items": [], "nextPageToken": "again"})))
    with pytest.raises(CalendarProviderError, match="google_calendar_list_too_large"):
        await provider.list_calendars("synthetic-access")


@pytest.mark.asyncio
async def test_code_exchange_passes_pkce_and_requires_both_scopes(configured):
    calls = []
    def handler(request):
        calls.append(parse_qs(request.content.decode()))
        return httpx.Response(200, json={"scope": " ".join(GOOGLE_SCOPES), "refresh_token": "synthetic-refresh"})
    provider = GoogleCalendar(transport=httpx.MockTransport(handler))
    assert await provider.exchange_code("synthetic-code", "v" * 43, "https://panel.example.test/integrations/google/callback") == "synthetic-refresh"
    assert calls[0]["code_verifier"] == ["v" * 43] and calls[0]["grant_type"] == ["authorization_code"]
    limited = GoogleCalendar(transport=httpx.MockTransport(lambda request: httpx.Response(200,
        json={"scope": GOOGLE_SCOPES[0], "refresh_token": "not-exported"})))
    with pytest.raises(CalendarProviderError, match="google_permissions_incomplete"):
        await limited.exchange_code("code", "v" * 43, "https://panel.example.test/integrations/google/callback")


@pytest.mark.parametrize("status,body,code,retry,reauthorize", [
    (400, {"error": "invalid_grant", "secret": "never-return"}, "google_authorization_expired", False, True),
    (401, {}, "google_authorization_expired", False, True),
    (429, {}, "google_rate_limited", True, False),
    (503, {"error": "private-upstream-message"}, "google_unavailable", True, False),
])
@pytest.mark.asyncio
async def test_token_failures_are_sanitized(configured, status, body, code, retry, reauthorize):
    provider = GoogleCalendar(transport=httpx.MockTransport(lambda request: httpx.Response(status, json=body)))
    with pytest.raises(CalendarProviderError) as caught:
        await provider.refresh_access_token("synthetic-refresh")
    assert (caught.value.code, caught.value.retryable, caught.value.reauthorize) == (code, retry, reauthorize)
    assert "secret" not in str(caught.value) and "private" not in str(caught.value)


@pytest.mark.asyncio
async def test_create_and_conflict_recovery_apply_the_latest_event_not_blind_success():
    row = booking(status="completed")
    calls = []
    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(404)
        if request.method == "POST":
            assert request.url.params["sendUpdates"] == "none"
            return httpx.Response(409, json={"error": {"errors": [{"reason": "duplicate"}]}})
        if request.method == "GET":
            return httpx.Response(200, json=owned(row))
        assert request.method == "PATCH" and request.headers["If-Match"] == '"version-one"'
        data = json.loads(request.content)
        assert data["summary"].startswith("Finalizado") and data["colorId"] == "10"
        assert "id" not in data and "attendees" not in data
        assert request.url.params["sendUpdates"] == "none"
        return httpx.Response(200, json={"id": deterministic_event_id(row["id"])})
    provider = GoogleCalendar(transport=httpx.MockTransport(handler))
    await provider.apply("synthetic-access", "calendar@example.invalid", deterministic_event_id(row["id"]), "create", row)
    assert [request.method for request in calls] == ["GET", "POST", "GET", "PATCH"]


@pytest.mark.parametrize("change,code", [
    ({"extendedProperties": {}}, "google_event_not_owned"),
    ({"extendedProperties": {"private": {"appointment_id": "someone-else"}}}, "google_event_not_owned"),
    ({"attendees": [{"email": "synthetic@example.invalid"}]}, "google_event_has_attendees"),
])
@pytest.mark.asyncio
async def test_existing_foreign_or_guest_event_is_never_mutated(change, code):
    row, calls = booking(), []
    def handler(request):
        calls.append(request.method)
        return httpx.Response(200, json=owned(row, **change))
    provider = GoogleCalendar(transport=httpx.MockTransport(handler))
    with pytest.raises(CalendarProviderError, match=code):
        await provider.apply("synthetic", "calendar", "orion123", "update", row)
    assert calls == ["GET"]


@pytest.mark.asyncio
async def test_delete_absent_event_requires_calendar_access_and_never_exports_past_names():
    row, calls = booking(status="cancelled"), []
    def handler(request):
        calls.append(request)
        return httpx.Response(403 if "calendarList" in request.url.path else 404)
    provider = GoogleCalendar(transport=httpx.MockTransport(handler))
    with pytest.raises(CalendarProviderError, match="google_permission_denied"):
        await provider.apply("synthetic", "calendar", "orion123", "delete", row)
    assert len(calls) == 2 and all(call.method == "GET" for call in calls)


@pytest.mark.asyncio
async def test_delete_idempotent_and_etag_guarded_without_invites():
    row, calls = booking(status="cancelled"), []
    def handler(request):
        calls.append(request)
        if request.method == "GET":
            return httpx.Response(200, json=owned(row))
        assert request.method == "DELETE" and request.url.params["sendUpdates"] == "none"
        assert request.headers["If-Match"] == '"version-one"'
        return httpx.Response(410)
    provider = GoogleCalendar(transport=httpx.MockTransport(handler))
    assert await provider.apply("synthetic", "calendar", "orion123", "delete", row) == "orion123"
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_network_uncertainty_is_retryable_and_no_raw_request_is_returned():
    def handler(request):
        raise httpx.ReadTimeout("contains synthetic-secret and private name", request=request)
    provider = GoogleCalendar(transport=httpx.MockTransport(handler))
    with pytest.raises(CalendarProviderError) as caught:
        await provider.apply("synthetic-secret", "calendar", "orion123", "create", booking())
    assert caught.value.code == "google_unavailable" and caught.value.retryable
    assert str(caught.value) == "google_unavailable"


def test_authorization_url_is_offline_pkce_and_server_redirect(configured):
    verifier = "v" * 43
    uri = make_authorization_url("synthetic-client", oauth_config()[2], "s" * 43, verifier)
    parsed = urlsplit(uri)
    q = parse_qs(parsed.query)
    assert parsed.netloc == "accounts.google.com"
    assert q["code_challenge"] == [base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")]
    assert q["code_challenge_method"] == ["S256"] and q["access_type"] == ["offline"]
    assert set(q["scope"][0].split()) == set(GOOGLE_SCOPES)


@pytest.mark.parametrize("name,value", [
    ("GOOGLE_CLIENT_SECRET", ""), ("INTEGRATION_FERNET_KEY", "not-a-key"),
    ("GOOGLE_REDIRECT_URI", "http://panel.example.test/integrations/google/callback"),
    ("GOOGLE_REDIRECT_URI", "https://other.example.test/integrations/google/callback"),
    ("GOOGLE_REDIRECT_URI", "https://[broken"),
    ("GOOGLE_REDIRECT_URI", "https://panel.example.test/integrations/google/callback?token=private"),
    ("PANEL_PUBLIC_URL", "not-a-public-origin"),
])
def test_missing_or_invalid_server_configuration_is_explicit(configured, monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    assert google_configured() is False
    with pytest.raises(HTTPException) as caught:
        oauth_config()
    assert caught.value.status_code == 503 and "private" not in caught.value.detail
