"""Google Calendar transport. PostgreSQL alone decides appointment state.

Only Orion-owned events are modified. No attendees, invitations, reminders,
Google-to-Orion imports, or event bodies in exceptions/logs.
"""
import asyncio
from html import escape
import os
import unicodedata
from datetime import datetime
from urllib.parse import quote
from uuid import UUID

import httpx

from app.customer_names import useful_name

EVENT_SCOPE = "https://www.googleapis.com/auth/calendar.events"
LIST_SCOPE = "https://www.googleapis.com/auth/calendar.calendarlist.readonly"
GOOGLE_SCOPES = (EVENT_SCOPE, LIST_SCOPE)
API = "https://www.googleapis.com/calendar/v3"
MAX_CALENDARS = 1000
STATE_LABELS = {"confirmed": "Confirmado", "completed": "Finalizado", "no_show": "Não compareceu"}
STATE_COLORS = {"confirmed": "9", "completed": "10", "no_show": "11"}


def display_text(value, limit: int) -> str:
    """Plain text only, bounded and without controls/directional overrides."""
    if not isinstance(value, str):
        return ""
    value = " ".join(value.split())
    return "".join(c for c in value if not unicodedata.category(c).startswith("C"))[:limit]


class CalendarProviderError(Exception):
    """A finite, safe error code, never a provider response or credential."""

    def __init__(self, code: str, *, retryable: bool = False, reauthorize: bool = False):
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.reauthorize = reauthorize


def provider_error(response: httpx.Response, *, token_request: bool = False):
    try:
        payload = response.json()
    except (ValueError, TypeError):
        payload = {}
    error = payload.get("error") if isinstance(payload, dict) else None
    entries = error.get("errors", []) if isinstance(error, dict) else []
    reasons = {item["reason"] for item in entries
               if isinstance(item, dict) and isinstance(item.get("reason"), str)} if isinstance(entries, list) else set()
    if token_request and error == "invalid_grant":
        return CalendarProviderError("google_authorization_expired", reauthorize=True)
    if token_request and isinstance(error, str) and error in {"invalid_client", "unauthorized_client"}:
        return CalendarProviderError("google_configuration_error")
    if response.status_code == 401:
        return CalendarProviderError("google_authorization_expired", reauthorize=True)
    if response.status_code == 429 or reasons.intersection({"rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded"}):
        return CalendarProviderError("google_rate_limited", retryable=True)
    if response.status_code >= 500:
        return CalendarProviderError("google_unavailable", retryable=True)
    if response.status_code == 403:
        return CalendarProviderError("google_permission_denied", reauthorize=True)
    if response.status_code == 404:
        return CalendarProviderError("google_calendar_unavailable", reauthorize=True)
    if response.status_code in {409, 412}:
        return CalendarProviderError("google_event_conflict", retryable=response.status_code == 412)
    return CalendarProviderError("google_request_rejected")


def json_object(response: httpx.Response) -> dict:
    try:
        value = response.json()
    except ValueError:
        raise CalendarProviderError("google_invalid_response") from None
    if not isinstance(value, dict):
        raise CalendarProviderError("google_invalid_response")
    return value


def canonical_calendar(item: dict) -> dict | None:
    """Return only a writable calendar's bounded display metadata."""
    role = item.get("accessRole")
    if not isinstance(role, str) or role not in {"owner", "writer"} or item.get("deleted"):
        return None
    identity = item.get("id")
    if not isinstance(identity, str) or identity == "primary" or not 1 <= len(identity) <= 250 or any(ord(c) < 32 or ord(c) == 127 for c in identity):
        raise CalendarProviderError("google_invalid_response")
    name = item.get("summaryOverride") or item.get("summary") or identity
    if not isinstance(name, str):
        name = identity
    name = " ".join(name.split())[:250]
    timezone = item.get("timeZone")
    return {"id": identity, "name": name, "timezone": timezone if isinstance(timezone, str) else None,
            "primary": item.get("primary") is True, "access_role": item["accessRole"]}


def event_payload(event_id: str, appointment: dict) -> dict:
    state = appointment.get("status", "confirmed")
    if state not in STATE_LABELS:
        raise CalendarProviderError("google_appointment_not_exportable")
    start, end = appointment["start_at"], appointment["end_at"]
    if not isinstance(start, datetime) or not isinstance(end, datetime) or not start.tzinfo or not end.tzinfo or end <= start:
        raise CalendarProviderError("google_appointment_invalid_time")
    private = {"appointment_id": str(appointment["id"]), "orion_status": state}
    if appointment.get("tenant_id") is not None:
        private["tenant_id"] = str(appointment["tenant_id"])
    customer = display_text(useful_name(appointment.get("customer_name")), 80)
    services = display_text(appointment.get("service_names"), 120)
    professional = display_text(appointment.get("professional_name"), 100)
    protocol = display_text(appointment.get("protocol"), 30)
    title = " · ".join(item for item in (STATE_LABELS[state], customer, services or protocol) if item)[:240]
    event = {
        "id": event_id, "summary": title,
        "description": (f"Profissional: {escape(professional)}\n" if professional else "") +
                       f"Protocolo: {escape(protocol)}\nAlterações devem ser feitas no Orion.",
        "start": {"dateTime": start.isoformat()}, "end": {"dateTime": end.isoformat()},
        "status": "confirmed", "colorId": STATE_COLORS[state], "visibility": "private",
        "transparency": "transparent" if state == "no_show" else "opaque",
        "extendedProperties": {"private": private}, "reminders": {"useDefault": False},
        "guestsCanInviteOthers": False, "guestsCanModify": False,
    }
    if isinstance(appointment.get("timezone"), str):
        event["start"]["timeZone"] = appointment["timezone"]
        event["end"]["timeZone"] = appointment["timezone"]
    return event


class FakeCalendar:
    def __init__(self):
        self.events: dict[tuple[str, str], dict] = {}
        self.fail = False

    async def apply(self, calendar_id: str, event_id: str, action: str, appointment: dict):
        if self.fail:
            raise RuntimeError("falha simulada da API")
        key = (calendar_id, event_id)
        if action == "delete":
            self.events.pop(key, None)
        else:
            self.events[key] = appointment
        return event_id


class GoogleCalendar:
    def __init__(self, *, transport=None):
        self.transport = transport

    def client(self):
        return httpx.AsyncClient(timeout=10, follow_redirects=False, transport=self.transport)

    async def exchange_code(self, code: str, verifier: str, redirect_uri: str) -> str:
        try:
            async with asyncio.timeout(12), self.client() as client:
                response = await client.post("https://oauth2.googleapis.com/token", data={
                    "client_id": os.environ["GOOGLE_CLIENT_ID"],
                    "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
                    "code": code, "code_verifier": verifier,
                    "grant_type": "authorization_code", "redirect_uri": redirect_uri,
                })
        except (httpx.RequestError, TimeoutError):
            raise CalendarProviderError("google_unavailable", retryable=True) from None
        if response.status_code != 200:
            raise provider_error(response, token_request=True)
        value = json_object(response)
        scope = value.get("scope", "")
        refresh = value.get("refresh_token")
        if not isinstance(scope, str) or not set(GOOGLE_SCOPES).issubset(scope.split()):
            raise CalendarProviderError("google_permissions_incomplete", reauthorize=True)
        if not isinstance(refresh, str) or not 1 <= len(refresh) <= 8192:
            raise CalendarProviderError("google_refresh_token_missing", reauthorize=True)
        return refresh

    async def refresh_access_token(self, refresh_token: str) -> str:
        client_id, secret = os.getenv("GOOGLE_CLIENT_ID"), os.getenv("GOOGLE_CLIENT_SECRET")
        if not client_id or not secret:
            raise CalendarProviderError("google_configuration_error")
        try:
            async with asyncio.timeout(12), self.client() as client:
                response = await client.post("https://oauth2.googleapis.com/token", data={
                    "client_id": client_id, "client_secret": secret, "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                })
        except (httpx.RequestError, TimeoutError):
            raise CalendarProviderError("google_unavailable", retryable=True) from None
        if response.status_code != 200:
            raise provider_error(response, token_request=True)
        value = json_object(response).get("access_token")
        if not isinstance(value, str) or not 1 <= len(value) <= 8192:
            raise CalendarProviderError("google_invalid_response")
        return value

    async def list_calendars(self, access_token: str) -> list[dict]:
        result, page = [], None
        try:
            async with asyncio.timeout(20), self.client() as client:
                for _ in range(4):
                    params = {"maxResults": 250, "minAccessRole": "writer", "showHidden": "true"}
                    if page:
                        params["pageToken"] = page
                    response = await client.get(API + "/users/me/calendarList", params=params,
                                                headers={"Authorization": "Bearer " + access_token})
                    if response.status_code != 200:
                        raise provider_error(response)
                    data = json_object(response)
                    items = data.get("items", [])
                    if not isinstance(items, list) or len(items) > 250:
                        raise CalendarProviderError("google_invalid_response")
                    for item in items:
                        if not isinstance(item, dict):
                            raise CalendarProviderError("google_invalid_response")
                        safe = canonical_calendar(item)
                        if safe is not None:
                            result.append(safe)
                    page = data.get("nextPageToken")
                    if not page:
                        return result
                    if not isinstance(page, str) or len(page) > 4096:
                        raise CalendarProviderError("google_invalid_response")
                raise CalendarProviderError("google_calendar_list_too_large")
        except (httpx.RequestError, TimeoutError):
            raise CalendarProviderError("google_unavailable", retryable=True) from None

    async def get_calendar(self, access_token: str, calendar_id: str) -> dict:
        try:
            async with asyncio.timeout(12), self.client() as client:
                response = await client.get(API + "/users/me/calendarList/" + quote(calendar_id, safe=""),
                                            headers={"Authorization": "Bearer " + access_token})
        except (httpx.RequestError, TimeoutError):
            raise CalendarProviderError("google_unavailable", retryable=True) from None
        if response.status_code != 200:
            raise provider_error(response)
        item = canonical_calendar(json_object(response))
        if item is None:
            raise CalendarProviderError("google_calendar_not_writable")
        return item

    @staticmethod
    def verify_owned_event(data: dict, appointment: dict):
        properties = data.get("extendedProperties", {})
        private = properties.get("private", {}) if isinstance(properties, dict) else {}
        if not isinstance(private, dict) or private.get("appointment_id") != str(appointment["id"]):
            raise CalendarProviderError("google_event_not_owned")
        if private.get("tenant_id") is not None and private["tenant_id"] != str(appointment.get("tenant_id")):
            raise CalendarProviderError("google_event_not_owned")
        if data.get("attendees"):
            # Do not modify guest-bearing events or trigger guest notifications.
            raise CalendarProviderError("google_event_has_attendees")

    async def apply(self, access_token: str, calendar_id: str, event_id: str,
                    action: str, appointment: dict):
        if action not in {"create", "update", "delete"} or calendar_id == "primary":
            raise CalendarProviderError("google_calendar_selection_required")
        base = API + "/calendars/" + quote(calendar_id, safe="") + "/events"
        event_url = base + "/" + quote(event_id, safe="")
        headers = {"Authorization": "Bearer " + access_token}
        params = {"sendUpdates": "none"}
        payload = None if action == "delete" else event_payload(event_id, appointment)
        try:
            async with asyncio.timeout(20), self.client() as client:
                # Read only this deterministic event to prove ownership before
                # updating/deleting it. No calendar contents are imported.
                current = await client.get(event_url, headers=headers)
                if current.status_code in {404, 410}:
                    if action == "delete":
                        if current.status_code == 404:
                            # A 404 can also hide lost access to the calendar.
                            # Prove access before acknowledging an absent event.
                            await self.get_calendar(access_token, calendar_id)
                        return event_id
                    response = await client.post(base, headers=headers, params=params, json=payload)
                    if response.status_code != 409:
                        if response.status_code not in {200, 201}:
                            raise provider_error(response)
                        return event_id
                    # A previous uncertain request may have committed. A 409
                    # requires applying the latest desired state, not success.
                    current = await client.get(event_url, headers=headers)
                if current.status_code != 200:
                    raise provider_error(current)
                data = json_object(current)
                if action == "delete" and data.get("status") == "cancelled":
                    return event_id
                self.verify_owned_event(data, appointment)
                safe_headers = dict(headers)
                if isinstance(data.get("etag"), str):
                    safe_headers["If-Match"] = data["etag"]
                if action == "delete":
                    response = await client.delete(event_url, headers=safe_headers, params=params)
                    if response.status_code in {404, 410}:
                        if response.status_code == 404:
                            await self.get_calendar(access_token, calendar_id)
                        return event_id
                else:
                    # PATCH leaves unrelated non-sensitive Google fields alone.
                    response = await client.patch(event_url, headers=safe_headers, params=params,
                                                  json={k: v for k, v in payload.items() if k != "id"})
                if response.status_code not in {200, 201, 204}:
                    raise provider_error(response)
        except (httpx.RequestError, TimeoutError):
            raise CalendarProviderError("google_unavailable", retryable=True) from None
        return event_id


def deterministic_event_id(appointment_id: UUID) -> str:
    # Google accepts base32hex characters; this UUID-based ID is stable.
    return "orion" + appointment_id.hex
