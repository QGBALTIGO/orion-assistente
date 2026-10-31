"""Real PostgreSQL/RLS, Redis OAuth state and ARQ, with a local Google double."""
import asyncio
import json
import os
import secrets
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

from arq.connections import ArqRedis
from arq.constants import result_key_prefix
from arq.worker import Worker
from cryptography.fernet import Fernet
from fastapi import FastAPI
import httpx
import pytest
import pytest_asyncio
from redis.asyncio import ConnectionPool, from_url
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import calendar_worker as worker, integrations_api as api
from app.calendar_provider import CalendarProviderError, deterministic_event_id, event_payload
from app.calendar_sync import OUTSIDE_INITIAL_SYNC, calendar_lock, enqueue_calendar_sync
from app.catalog_api import Actor, authenticate, router as catalog_router
from app.database import tenant_transaction
from app.google_oauth import OAUTH_COOKIE, candidate_key, digest
from app.management_api import router as management_router
from app.panel_auth import COOKIE, _hash_cookie
from test_management import management_db, appointment, customer

pytestmark = pytest.mark.integration
ORIGIN = "https://panel.example.test"
CALENDAR = "synthetic-calendar@example.invalid"


class ObservedGoogle:
    """Only documented operations; no HTTP client or external connection."""
    def __init__(self):
        self.calls = []
        self.after_exchange = None
        self.after_calendar = None
        self.failure = None
        self.entered = self.release = None

    async def exchange_code(self, code, verifier, redirect):
        self.calls.append(("exchange", code, verifier, redirect))
        assert len(verifier) == 43 and redirect == ORIGIN + "/integrations/google/callback"
        if self.after_exchange:
            await self.after_exchange()
        return "synthetic-refresh-only"

    async def refresh_access_token(self, refresh):
        self.calls.append(("refresh", refresh))
        if self.failure:
            raise self.failure
        return "synthetic-access-only"

    async def list_calendars(self, token):
        self.calls.append(("list",))
        return [{"id": CALENDAR, "name": "Agenda sintética", "timezone": "America/Sao_Paulo", "primary": True, "access_role": "owner"},
                {"id": "other@example.invalid", "name": "Outra agenda", "timezone": "UTC", "primary": False, "access_role": "writer"}]

    async def get_calendar(self, token, identity):
        self.calls.append(("calendar", identity))
        if self.after_calendar:
            await self.after_calendar()
        return {"id": CALENDAR if identity == "primary" else identity, "name": "Agenda sintética",
                "timezone": "America/Sao_Paulo", "primary": identity in {"primary", CALENDAR}, "access_role": "owner"}

    async def apply(self, token, calendar, event, action, row):
        self.calls.append(("apply", calendar, event, action, dict(row)))
        if action != "delete":
            event_payload(event, row)  # Exercise actual consent-limited rendering.
        if self.entered:
            self.entered.set()
            await self.release.wait()
        return event


@pytest_asyncio.fixture
async def google_db(management_db, monkeypatch):
    d = management_db
    redis_url = os.getenv("ORION_GOOGLE_TEST_REDIS_URL") or os.getenv("REDIS_URL")
    if not redis_url:
        if os.getenv("REQUIRE_REDIS_TESTS") == "1":
            pytest.fail("Google OAuth/queue tests require a real disposable Redis")
        pytest.skip("Requires disposable Redis")
    redis = from_url(redis_url, decode_responses=True)
    await redis.ping()
    key = Fernet.generate_key().decode()
    for name, value in {"GOOGLE_CLIENT_ID": "synthetic-client", "GOOGLE_CLIENT_SECRET": "synthetic-secret",
                        "GOOGLE_REDIRECT_URI": ORIGIN + "/integrations/google/callback", "PANEL_PUBLIC_URL": ORIGIN,
                        "INTEGRATION_FERNET_KEY": key, "GOOGLE_LIVE_SYNC": "1", "REDIS_URL": redis_url}.items():
        monkeypatch.setenv(name, value)
    app = FastAPI()
    app.include_router(api.router)
    app.include_router(management_router)
    app.include_router(catalog_router)
    factory = async_sessionmaker(d["runtime"], expire_on_commit=False)
    monkeypatch.setattr(api, "get_session_factory", lambda: factory)
    monkeypatch.setattr(worker, "get_session_factory", lambda: factory)
    provider = ObservedGoogle()
    monkeypatch.setattr(api, "GoogleCalendar", lambda: provider)
    monkeypatch.setattr(worker, "GoogleCalendar", lambda: provider)
    keys = set()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN,
                                headers=d["client"].headers) as client:
        try:
            yield {**d, "app": app, "client": client, "factory": factory, "redis": redis, "keys": keys,
                   "redis_url": redis_url, "key": key, "google": provider,
                   "google_base": f"/api/v1/tenants/{d['a']}/google"}
        finally:
            async with d["migrator"].begin() as db:
                await db.execute(text("DELETE FROM google_connections WHERE tenant_id IN (:a,:b)"), {"a": d["a"], "b": d["b"]})
            for tenant in (d["a"], d["b"]):
                async for found in redis.scan_iter(match=f"google:*{tenant}*"):
                    keys.add(found)
            if keys:
                await redis.delete(*keys)
            await redis.aclose()


async def start(d):
    response = await d["client"].post(d["google_base"] + "/connect")
    assert response.status_code == 200, response.text
    q = parse_qs(urlsplit(response.json()["authorization_url"]).query)
    state = q["state"][0]
    d["keys"].add("google:oauth:" + state)
    raw = await d["redis"].get("google:oauth:" + state)
    assert "synthetic-secret" not in raw and "synthetic-refresh" not in raw
    assert 0 < await d["redis"].ttl("google:oauth:" + state) <= 600
    assert "SameSite=lax" in response.headers["set-cookie"] and "HttpOnly" in response.headers["set-cookie"]
    return state, json.loads(raw)


async def stage(d):
    state, intent = await start(d)
    response = await d["client"].get("/integrations/google/callback", params={"state": state, "code": "synthetic-code"})
    assert response.status_code == 303, response.text
    assert response.headers["location"] == f"/painel?aba=agenda&google=choose&empresa={d['a']}"
    assert response.headers["cache-control"] == "no-store"
    return intent


async def activate(d):
    await stage(d)
    response = await d["client"].post(d["google_base"] + "/calendar", json={"calendar_id": CALENDAR})
    assert response.status_code == 200, response.text
    return response.json()


async def clear_start_rate(d):
    async for key in d["redis"].scan_iter(match=f"google:start:{d['a']}:*"):
        await d["redis"].delete(key)


async def as_owner_session(d):
    async with d["migrator"].begin() as db:
        row = (await db.execute(text("""
            UPDATE panel_accounts SET verified_at=now() WHERE id=:id RETURNING auth_version,role
        """), {"id": d["account"]})).mappings().one()
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    session_key = _hash_cookie(token)
    await d["redis"].set(session_key, json.dumps({"role": row["role"], "account_id": str(d["account"]),
        "auth_version": row["auth_version"], "csrf": csrf}), ex=600)
    d["keys"].add(session_key)
    d["client"].headers.pop("Authorization", None)
    d["client"].headers["X-Orion-CSRF"] = csrf
    d["client"].headers["Origin"] = ORIGIN
    d["client"].cookies.set(COOKIE, token, domain="panel.example.test", path="/")
    return session_key


async def outbox(d, identity):
    async with d["migrator"].connect() as db:
        return (await db.execute(text("SELECT * FROM calendar_outbox WHERE tenant_id=:tenant AND appointment_id=:id"),
                                {"tenant": d["a"], "id": UUID(str(identity))})).mappings().one_or_none()


async def enqueue(d, identity, event="confirmed"):
    async with d["factory"]() as db:
        async with tenant_transaction(db, d["a"]):
            await enqueue_calendar_sync(db, d["a"], UUID(str(identity)), event)
    return await outbox(d, identity)


async def set_due(d, identity):
    async with d["migrator"].begin() as db:
        await db.execute(text("UPDATE calendar_outbox SET next_attempt_at=now()-interval '1 second' WHERE appointment_id=:id"),
                         {"id": UUID(str(identity))})


@pytest.mark.asyncio
async def test_unconfigured_is_honest_and_roles_cannot_connect_or_mutate(google_db, monkeypatch):
    d = google_db
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET")
    result = await d["client"].get(d["google_base"] + "/status")
    assert result.status_code == 200 and result.json()["configured"] is False
    assert result.json()["sync_status"] == "not_configured"
    assert not any(result.json()["capabilities"].values())
    assert (await d["client"].post(d["google_base"] + "/connect")).status_code == 503
    assert d["google"].calls == []
    await as_owner_session(d)
    async with d["migrator"].begin() as db:
        await db.execute(text("UPDATE tenant_memberships SET role='editor' WHERE tenant_id=:tenant"), {"tenant": d["a"]})
    status = (await d["client"].get(d["google_base"] + "/status")).json()
    assert not any(status["capabilities"].values())
    for path, body in (("connect", {}), ("revoke", {}), ("retry", {}), ("calendar", {"calendar_id": CALENDAR})):
        assert (await d["client"].post(d["google_base"] + "/" + path, json=body)).status_code == 403
    assert (await d["client"].get(f"/api/v1/tenants/{d['b']}/google/status")).status_code == 403


@pytest.mark.asyncio
async def test_owner_oauth_enforces_csrf_nonce_and_single_use_before_selection(google_db):
    d = google_db
    await as_owner_session(d)
    csrf = d["client"].headers.pop("X-Orion-CSRF")
    assert (await d["client"].post(d["google_base"] + "/connect")).status_code == 403
    d["client"].headers["X-Orion-CSRF"] = csrf
    d["client"].headers["Origin"] = "https://foreign.example.test"
    assert (await d["client"].post(d["google_base"] + "/connect")).status_code == 403
    d["client"].headers["Origin"] = ORIGIN
    state, intent = await start(d)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=d["app"]), base_url=ORIGIN) as stranger:
        denied = await stranger.get("/integrations/google/callback", params={"state": state, "code": "private-code"})
        assert denied.status_code == 403 and "private-code" not in denied.text
    assert d["google"].calls == [] and await d["redis"].exists("google:oauth:" + state)
    completed = await d["client"].get("/integrations/google/callback", params={"state": state, "code": "synthetic-code"})
    assert completed.status_code == 303
    assert (await d["client"].get("/integrations/google/callback", params={"state": state, "code": "synthetic-code"})).status_code == 403
    candidate = json.loads(await d["redis"].get(candidate_key(d["a"], intent["initiator"])))
    assert "refresh_token" not in candidate and "synthetic-refresh" not in json.dumps(candidate)
    assert Fernet(d["key"].encode()).decrypt(candidate["refresh_token_enc"].encode()) == b"synthetic-refresh-only"
    assert await api.connection_snapshot(d["a"]) is None
    status = (await d["client"].get(d["google_base"] + "/status")).json()
    assert status["selection_pending"] and not status["connected"] and status["sync_status"] == "choose_calendar"
    assert [call[0] for call in d["google"].calls] == ["exchange"]


@pytest.mark.asyncio
async def test_unverified_owner_gets_preflight_before_google_state_or_token_exchange(google_db):
    d = google_db
    await as_owner_session(d)
    async with d["migrator"].begin() as db:
        await db.execute(text("UPDATE panel_accounts SET verified_at=NULL WHERE id=:id"), {"id": d["account"]})
    status = (await d["client"].get(d["google_base"] + "/status")).json()
    assert status["email_confirmation_required"] is True
    assert status["sync_status"] == "email_confirmation_required"
    assert status["capabilities"]["can_connect"] is False
    before = set([key async for key in d["redis"].scan_iter(match="google:oauth:*")])
    refused = await d["client"].post(d["google_base"] + "/connect")
    assert refused.status_code == 403 and "Confirme seu e-mail" in refused.text
    assert set([key async for key in d["redis"].scan_iter(match="google:oauth:*")]) == before
    assert d["google"].calls == []


@pytest.mark.parametrize("change", ["logout", "membership", "account_version", "revoke"])
@pytest.mark.asyncio
async def test_callback_rechecks_initiator_and_revocation_after_token_exchange(google_db, change):
    d = google_db
    session_key = await as_owner_session(d)
    state, intent = await start(d)
    async def invalidate():
        if change == "logout":
            await d["redis"].delete(session_key)
        elif change == "revoke":
            assert (await d["client"].post(d["google_base"] + "/revoke")).status_code == 200
        else:
            async with d["migrator"].begin() as db:
                query = "UPDATE tenant_memberships SET role='viewer' WHERE account_id=:id" if change == "membership" else \
                        "UPDATE panel_accounts SET auth_version=auth_version+1 WHERE id=:id"
                await db.execute(text(query), {"id": d["account"]})
    d["google"].after_exchange = invalidate
    response = await d["client"].get("/integrations/google/callback", params={"state": state, "code": "synthetic-code"})
    assert response.status_code in {401, 403, 409}, response.text
    assert not await d["redis"].exists(candidate_key(d["a"], intent["initiator"]))
    row = await api.connection_snapshot(d["a"])
    assert row is None or row["refresh_token_enc"] is None
    assert "synthetic-refresh" not in response.text


@pytest.mark.asyncio
async def test_selection_activates_only_explicit_calendar_locks_binding_and_revoke_keeps_it(google_db):
    d = google_db
    await stage(d)
    listing = await d["client"].get(d["google_base"] + "/calendars")
    assert listing.status_code == 200 and all(item["selectable"] for item in listing.json()["items"])
    selected = await d["client"].post(d["google_base"] + "/calendar", json={"calendar_id": CALENDAR})
    assert selected.status_code == 200, selected.text
    status = selected.json()
    assert status["connected"] and status["calendar_change_locked"] and status["calendar_name"] == "Agenda sintética"
    row = await api.connection_snapshot(d["a"])
    assert row["calendar_id"] == CALENDAR and Fernet(d["key"].encode()).decrypt(row["refresh_token_enc"]) == b"synthetic-refresh-only"
    assert all(call[0] != "apply" for call in d["google"].calls)
    assert (await d["client"].post(d["google_base"] + "/revoke")).status_code == 200
    row = await api.connection_snapshot(d["a"])
    assert row["calendar_id"] == CALENDAR and row["refresh_token_enc"] is None and row["status"] == "revoked"
    await clear_start_rate(d)
    await stage(d)
    listing = (await d["client"].get(d["google_base"] + "/calendars")).json()
    assert [item["selectable"] for item in listing["items"]] == [True, False]
    denied = await d["client"].post(d["google_base"] + "/calendar", json={"calendar_id": "other@example.invalid"})
    assert denied.status_code == 409
    selected = await d["client"].post(d["google_base"] + "/calendar", json={"calendar_id": CALENDAR})
    assert selected.status_code == 200 and selected.json()["connected"]


@pytest.mark.asyncio
async def test_revoke_during_selection_prevents_stale_credential_persist(google_db):
    d = google_db
    await stage(d)
    async def revoke():
        result = await d["client"].post(d["google_base"] + "/revoke")
        assert result.status_code == 200
    d["google"].after_calendar = revoke
    result = await d["client"].post(d["google_base"] + "/calendar", json={"calendar_id": CALENDAR})
    assert result.status_code == 409
    row = await api.connection_snapshot(d["a"])
    assert row["refresh_token_enc"] is None and row["status"] == "revoked"


@pytest.mark.parametrize("old_grant", [True, False])
@pytest.mark.asyncio
async def test_legacy_primary_is_resolved_only_through_its_previous_grant(google_db, old_grant):
    d = google_db
    encrypted = Fernet(d["key"].encode()).encrypt(b"synthetic-old-grant") if old_grant else None
    async with d["migrator"].begin() as db:
        await db.execute(text("""
            INSERT INTO google_connections (tenant_id,calendar_id,refresh_token_enc,status)
            VALUES (:tenant,'primary',:token,'active')
        """), {"tenant": d["a"], "token": encrypted})
    status = (await d["client"].get(d["google_base"] + "/status")).json()
    assert not status["connected"] and status["last_error"] == "google_calendar_selection_required"
    await stage(d)
    listed = (await d["client"].get(d["google_base"] + "/calendars")).json()
    assert [row["selectable"] for row in listed["items"]] == [True, False]
    response = await d["client"].post(d["google_base"] + "/calendar", json={"calendar_id": CALENDAR})
    assert response.status_code == (200 if old_grant else 409), response.text
    row = await api.connection_snapshot(d["a"])
    assert row["calendar_id"] == (CALENDAR if old_grant else "primary")
    if old_grant:
        assert ("refresh", "synthetic-old-grant") in d["google"].calls
        assert ("calendar", "primary") in d["google"].calls
    assert all(call[0] != "apply" for call in d["google"].calls)


@pytest.mark.asyncio
async def test_initial_window_excludes_unsent_history_but_preserves_known_and_uncertain_work(google_db):
    d = google_db
    person = await customer(d)
    old = await appointment(d, person["id"], start_at=d["now"] - timedelta(days=5))
    future = await appointment(d, person["id"], start_at=d["now"] + timedelta(days=1))
    linked = await appointment(d, person["id"], status="cancelled", start_at=d["now"] - timedelta(days=7))
    uncertain = await appointment(d, person["id"], status="completed", start_at=d["now"] - timedelta(days=9))
    for identity, event in ((old, "confirmed"), (future, "confirmed"), (linked, "cancelled"), (uncertain, "completed")):
        await enqueue(d, identity, event)
    async with d["migrator"].begin() as db:
        await db.execute(text("UPDATE calendar_outbox SET external_event_id=:event WHERE appointment_id=:id"),
                         {"id": UUID(linked), "event": deterministic_event_id(UUID(linked))})
        await db.execute(text("UPDATE calendar_outbox SET attempts=1 WHERE appointment_id=:id"), {"id": UUID(uncertain)})
    status = await activate(d)
    assert status["pending"] == 3 and status["failed"] == 0 and status["sent"] == 0
    skipped = await outbox(d, old)
    assert skipped["status"] == "dead" and skipped["last_error"] == OUTSIDE_INITIAL_SYNC and skipped["external_event_id"] is None
    public = (await d["client"].get(d["google_base"] + f"/appointments/{old}/status")).json()
    assert public["status"] == "outside_initial_sync" and public["last_error"] is None and public["last_synced_at"] is None
    assert (await d["client"].post(d["google_base"] + "/retry")).status_code == 200
    assert (await outbox(d, old))["status"] == "dead"
    await enqueue(d, old, "no_show")
    assert (await outbox(d, old))["last_error"] == OUTSIDE_INITIAL_SYNC
    await clear_start_rate(d)
    await stage(d)
    result = await d["client"].post(d["google_base"] + "/calendar", json={"calendar_id": CALENDAR})
    assert result.status_code == 200
    assert (await outbox(d, uncertain))["status"] == "pending"  # Reset attempts do not erase prior uncertainty on reconnect.
    assert (await outbox(d, old))["last_error"] == OUTSIDE_INITIAL_SYNC
    assert all(call[0] != "apply" for call in d["google"].calls)


@pytest.mark.asyncio
async def test_worker_coalesces_latest_states_without_exporting_private_details_and_tenant_leak(google_db):
    d = google_db
    person = await customer(d, name="Ana sintética", phone="11912345678", notes="PRIVATE_ONLY")
    identity = await appointment(d, person["id"], start_at=d["now"] + timedelta(days=1))
    await activate(d)
    item = await outbox(d, identity)
    assert await worker.process_calendar_outbox({}, str(d["a"]), str(item["id"])) == "sent"
    for state in ("completed", "no_show", "cancelled"):
        async with d["migrator"].begin() as db:
            await db.execute(text("UPDATE appointments SET status=:state WHERE id=:id"), {"state": state, "id": UUID(identity)})
        await enqueue(d, identity, state)
        assert await worker.process_calendar_outbox({}, str(d["a"]), str(item["id"])) == "sent"
    applied = [call for call in d["google"].calls if call[0] == "apply"]
    assert [call[3] for call in applied] == ["create", "update", "update", "delete"]
    assert [call[4]["status"] for call in applied] == ["confirmed", "completed", "no_show", "cancelled"]
    assert len({call[2] for call in applied}) == 1
    assert applied[0][4]["service_names"] == "Corte sintético"
    assert applied[0][4]["customer_name"] == "Ana sintética"
    assert all("notes" not in call[4] and "customer_phone" not in call[4] and "details" not in call[4] for call in applied)
    async with d["factory"]() as db:
        async with tenant_transaction(db, d["b"]):
            assert (await db.execute(text("SELECT id FROM google_connections"))).all() == []
            assert (await db.execute(text("SELECT id FROM calendar_outbox"))).all() == []
    denied = await d["client"].get(f"/api/v1/tenants/{d['b']}/google/appointments/{identity}/status")
    assert denied.status_code == 404
    state = (await d["client"].get(d["google_base"] + f"/appointments/{identity}/status")).json()
    assert state["status"] == "synced" and state["last_synced_at"] and state["configured"]


@pytest.mark.asyncio
async def test_retry_backoff_exhaustion_and_reauthorization_are_distinct_from_connected(google_db):
    d = google_db
    person = await customer(d)
    identity = await appointment(d, person["id"], start_at=d["now"] + timedelta(days=1))
    await activate(d)
    item = await outbox(d, identity)
    d["google"].failure = CalendarProviderError("google_rate_limited", retryable=True)
    for attempt in range(1, 9):
        await set_due(d, identity)
        assert await worker.process_calendar_outbox({}, str(d["a"]), str(item["id"])) == "retry"
        row = await outbox(d, identity)
        assert row["attempts"] == attempt and row["last_error"] == "google_rate_limited"
        assert row["next_attempt_at"] > row["updated_at"]
    assert row["status"] == "pending"  # Transient Google limits must recover automatically.
    status = (await d["client"].get(d["google_base"] + "/status")).json()
    assert status["connected"] and status["sync_status"] == "attention" and status["pending"] == 1
    assert (await d["client"].post(d["google_base"] + "/retry")).status_code == 200
    d["google"].failure = CalendarProviderError("google_authorization_expired", reauthorize=True)
    assert await worker.process_calendar_outbox({}, str(d["a"]), str(item["id"])) == "failed"
    status = (await d["client"].get(d["google_base"] + "/status")).json()
    assert not status["connected"] and status["sync_status"] == "reauthorization_required"
    assert status["capabilities"]["can_connect"] and not status["capabilities"]["can_retry"]
    assert status["last_error"] == "google_authorization_expired"


@pytest.mark.asyncio
async def test_worker_disabled_disconnected_and_hold_events_do_not_send(google_db, monkeypatch):
    d = google_db
    person = await customer(d)
    identity = await appointment(d, person["id"], start_at=d["now"] + timedelta(days=1))
    assert await enqueue(d, identity, "hold_created") is None
    item = await enqueue(d, identity)
    monkeypatch.setenv("GOOGLE_LIVE_SYNC", "0")
    assert await worker.process_calendar_outbox({}, str(d["a"]), str(item["id"])) == "disabled_by_default"
    assert (await outbox(d, identity))["attempts"] == 0
    monkeypatch.setenv("GOOGLE_LIVE_SYNC", "1")
    assert await worker.process_calendar_outbox({}, str(d["a"]), str(item["id"])) == "not_connected"
    after = await outbox(d, identity)
    assert after["attempts"] == 0 and after["next_attempt_at"] > d["now"] + timedelta(minutes=14)
    assert d["google"].calls == []


@pytest.mark.asyncio
async def test_disconnect_cannot_succeed_while_old_credential_is_sending(google_db):
    d = google_db
    person = await customer(d)
    identity = await appointment(d, person["id"], start_at=d["now"] + timedelta(days=1))
    await activate(d)
    item = await outbox(d, identity)
    d["google"].entered, d["google"].release = asyncio.Event(), asyncio.Event()
    send = asyncio.create_task(worker.process_calendar_outbox({}, str(d["a"]), str(item["id"])))
    try:
        await asyncio.wait_for(d["google"].entered.wait(), 5)
        denied = await d["client"].post(d["google_base"] + "/revoke")
        assert denied.status_code == 409
        d["google"].release.set()
        assert await asyncio.wait_for(send, 5) == "sent"
        revoked = await d["client"].post(d["google_base"] + "/revoke")
        assert revoked.status_code == 200 and not revoked.json()["connected"]
        applied_count = len([call for call in d["google"].calls if call[0] == "apply"])
        await enqueue(d, identity, "rescheduled")
        assert await worker.process_calendar_outbox({}, str(d["a"]), str(item["id"])) == "not_connected"
        assert len([call for call in d["google"].calls if call[0] == "apply"]) == applied_count
    finally:
        d["google"].release.set()
        if not send.done():
            send.cancel()
            await asyncio.gather(send, return_exceptions=True)


@pytest.mark.asyncio
async def test_two_outboxes_reenqueue_after_connection_busy_despite_arq_retained_result(google_db, monkeypatch):
    # ARQ uses SIGUSR1 as an internal close token even with OS handlers off.
    # Windows has no SIGUSR1; keep the real Redis/worker assertions unchanged.
    import signal
    if not hasattr(signal,'SIGUSR1'):monkeypatch.setattr(signal,'SIGUSR1',signal.SIGTERM,raising=False)
    d = google_db
    person = await customer(d)
    identities = [await appointment(d, person["id"], start_at=d["now"] + timedelta(days=day)) for day in (1, 2)]
    await activate(d)
    queue_name = "orion-google-test:" + str(uuid4())
    pool = ConnectionPool.from_url(d["redis_url"], decode_responses=False)
    arq = ArqRedis(pool, default_queue_name=queue_name)
    d["google"].entered, d["google"].release = asyncio.Event(), asyncio.Event()
    # Only the clock delay is shortened; actual SQL locks, queue IDs and ARQ
    # retained results are exercised with the production worker coroutine.
    monkeypatch.setattr(worker, "CONNECTION_BUSY_DELAY_SECONDS", .05)
    runner = Worker([worker.process_calendar_outbox], queue_name=queue_name, redis_pool=arq, burst=True,
                    max_jobs=2, max_burst_jobs=2, poll_delay=.01, handle_signals=False, keep_result=3600, log_results=False)
    run = None
    second = None
    keys = set()
    try:
        assert await worker.scan_calendar_outbox({"redis": arq}) == 2
        job_ids = [key.decode() for key in await arq.zrange(queue_name, 0, -1)]
        assert len(job_ids) == 2
        keys.update(result_key_prefix + job_id for job_id in job_ids)
        run = asyncio.create_task(runner.async_run())
        await asyncio.wait_for(d["google"].entered.wait(), 5)
        async with asyncio.timeout(5):
            while not any([await arq.exists(result_key_prefix + job_id) for job_id in job_ids]):
                await asyncio.sleep(.02)
        d["google"].release.set()
        await asyncio.wait_for(run, 5)
        assert runner.jobs_complete == 2
        rows = [await outbox(d, identity) for identity in identities]
        waiting = next(row for row in rows if row["status"] == "pending")
        assert waiting["attempts"] == 0
        original = next(job for job in job_ids if str(waiting["id"]) in job)
        assert await arq.exists(result_key_prefix + original)
        assert await arq.enqueue_job("process_calendar_outbox", str(d["a"]), str(waiting["id"]), _job_id=original) is None
        await asyncio.sleep(.08)
        assert await worker.scan_calendar_outbox({"redis": arq}) == 1
        new_ids = [key.decode() for key in await arq.zrange(queue_name, 0, -1)]
        assert len(new_ids) == 1 and new_ids[0] != original
        keys.add(result_key_prefix + new_ids[0])
        second = Worker([worker.process_calendar_outbox], queue_name=queue_name, redis_pool=arq, burst=True,
                        max_burst_jobs=1, poll_delay=.01, handle_signals=False, keep_result=3600, log_results=False)
        await asyncio.wait_for(second.async_run(), 5)
        assert all(row["status"] == "sent" for row in [await outbox(d, identity) for identity in identities])
        assert len([call for call in d["google"].calls if call[0] == "apply"]) == 2
    finally:
        d["google"].release.set()
        if run and not run.done():
            run.cancel()
            await asyncio.gather(run, return_exceptions=True)
        await runner.close()
        if second:
            await second.close()
        if keys:
            await arq.delete(*keys)
        await arq.delete(queue_name, queue_name + ":health-check")
        await arq.aclose()


@pytest.mark.asyncio
async def test_google_oauth_connects_before_new_company_staff_setup(google_db, monkeypatch):
    """Regression: a new company must not get HTTP 409 just for missing staff."""
    d=google_db
    monkeypatch.setenv("ONBOARDING_ENABLED","1")
    await as_owner_session(d)
    async with d["migrator"].begin() as db:
        await db.execute(text("""
            INSERT INTO business_onboarding(tenant_id,created_by,is_new,configuration)
            VALUES (:tenant,:actor,true,CAST(:config AS jsonb))
        """), {"tenant":d["a"],"actor":d["account"],
               "config":json.dumps({"niche":"barbearia","activity":"services",
                                    "modality":"online"})})
    try:
        # The fixture has existing staff, but this *new onboarding* is still
        # incomplete. The operational readiness gate must reject bookings,
        # while OAuth setup must not be blocked by that same requirement.
        from app.onboarding_service import require_operational_ready
        from fastapi import HTTPException
        async with d["factory"]() as db:
            with pytest.raises(HTTPException) as blocked:
                async with tenant_transaction(db,d["a"]):
                    await require_operational_ready(db,d["a"],"appointments")
            assert blocked.value.status_code==409
        state, intent=await start(d)
        assert intent["tenant_id"]==str(d["a"])
        result=await d["client"].get("/integrations/google/callback",
                                      params={"state":state,"code":"synthetic-code"})
        assert result.status_code==303, result.text
        assert "google=choose" in result.headers["location"]
        chosen=await d["client"].post(d["google_base"]+"/calendar",
                                        json={"calendar_id":CALENDAR})
        assert chosen.status_code==200, chosen.text
        assert chosen.json()["connected"] is True
    finally:
        async with d["migrator"].begin() as db:
            await db.execute(text(
                "DELETE FROM business_onboarding WHERE tenant_id=:t"
            ),{"t":d["a"]})


@pytest.mark.asyncio
async def test_disconnected_intents_drain_without_false_connection_busy(google_db):
    """Unconnected tenant jobs should never fight over a nonexistent grant."""
    d=google_db
    person=await customer(d)
    identities=[await appointment(d,person["id"],start_at=d["now"]+timedelta(days=i))
                for i in (1,2)]
    jobs=[await enqueue(d,item) for item in identities]
    results=await asyncio.gather(*(
        worker.process_calendar_outbox({},str(d["a"]),str(job["id"])) for job in jobs
    ))
    assert results==["not_connected","not_connected"]
    for identity in identities:
        row=await outbox(d,identity)
        assert row["attempts"]==0
        assert row["status"]=="pending"
        assert row["last_error"]=="calendar_not_connected"
        assert row["next_attempt_at"]>d["now"]+timedelta(minutes=14)
    assert d["google"].calls==[]


@pytest.mark.asyncio
async def test_live_google_check_never_creates_a_test_event_or_exposes_tokens(google_db):
    d=google_db
    # Checking without explicit owner authorization must not contact Google.
    unavailable=await d["client"].post(d["google_base"]+"/check")
    assert unavailable.status_code==409
    assert d["google"].calls==[]
    await activate(d)
    d["google"].calls.clear()
    verified=await d["client"].post(d["google_base"]+"/check")
    assert verified.status_code==200,verified.text
    assert verified.json()=={"verified":True,"calendar_name":"Agenda sintética",
        "calendar_id":CALENDAR,"writable":True,"events_created":0,"direction":"orion_to_google"}
    assert [call[0] for call in d["google"].calls]==["refresh","calendar"]
    assert "synthetic-refresh-only" not in verified.text
    assert "synthetic-access-only" not in verified.text
    d["google"].failure=CalendarProviderError("google_authorization_expired",reauthorize=True)
    expired=await d["client"].post(d["google_base"]+"/check")
    assert expired.status_code==409
    assert "expirou" in expired.text.lower() or "reconecte" in expired.text.lower()
    assert not any(call[0]=="apply" for call in d["google"].calls)
    saved=await api.connection_snapshot(d["a"])
    assert saved["status"]=="error"  # Definitive invalid_grant unlocks Reconectar.
    assert saved["last_error"]=="google_authorization_expired"
    assert saved["refresh_token_enc"]  # Preserve the encrypted token for review.
    status=(await d["client"].get(d["google_base"]+"/status")).json()
    assert status["sync_status"]=="reauthorization_required"
    assert status["capabilities"]["can_connect"] is True
