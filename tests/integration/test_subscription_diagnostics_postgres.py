"""Stage-06 actual cookies, CSRF, PostgreSQL grants and diagnostic admission.

Provider health/status and transport are synthetic. Selection, refresh, recovery
and preparation never send; only an explicit confirmation admits one effect.
"""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import HTTPException
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app import qr_channels, subscription_devices as devices
from app import subscription_diagnostics as api
from app import subscription_chatbot_engine, subscription_chatbot_http
from app import subscription_chatbot_rules, subscription_device_audio
from app.catalog_api import support_session_factory
from app.database import tenant_transaction
from app.plan_routing import include_plan_router
from test_subscription_devices_postgres import (
    action, create, device_case, disposable_environment, event, finish, internal,
    new_owner_http, onboarding_db, permit, scope, settings,
)  # noqa: F401 - retain the actual auth/session/role fixture dependency chain.
from test_subscription_chatbot_test_postgres import ISOLATED_TABLES, revoke
from test_subscription_sent_messages_postgres import foreign_message

pytestmark = pytest.mark.integration
CONTRACT = json.loads((Path(__file__).parents[1] / "fixtures/subscription_diagnostics_contract.json").read_text())
SELF_PHONE = CONTRACT["detail_response"]["test_message"]["default_recipient_e164"].lstrip("+")
OTHER_PHONE = "5567999999903"
TABLES = (*ISOLATED_TABLES, "subscription_sent_message_resends", "subscription_diagnostic_requests")


@pytest_asyncio.fixture
async def diagnostic_case(device_case, monkeypatch):
    c = device_case
    generic = [route for route in c.app.router.routes if "{resource}" in getattr(route, "path", "")]
    c.app.router.routes[:] = [route for route in c.app.router.routes if route not in generic]
    include_plan_router(c.app, api.router)
    c.app.router.routes.extend(generic)
    c.diagnostics = f"/api/v1/tenants/{c.tenant}/subscription-whatsapp/diagnostics"
    c.diag_self, c.diag_status, c.diag_reads = SELF_PHONE, "connected", []
    c.diag_server = "available"
    c.diag_proxy_dispatches, c.diag_receipts = [], {}
    c.diag_proxy_reply_lost = False
    original_transport = c.bridge.transport

    async def status(transport, row):
        c.diag_reads.append(("status", str(row["id"])))
        if c.diag_status is None:
            return None
        return {"status": c.diag_status, "phone": c.diag_self,
            "phone_verified": c.diag_self is not None, "applied_revision": row["revision"],
            "profile": {"name": "Aparelho sintético", "avatar_url": None},
            "observed_at": datetime.now(timezone.utc), "private_token": "SYNTHETIC_PRIVATE_PROVIDER_TOKEN"}

    async def server():
        c.diag_reads.append(("server", None))
        return {"state": c.diag_server, "latency_ms": 17 if c.diag_server == "available" else None,
            "observed_at": datetime.now(timezone.utc) if c.diag_server != "unknown" else None,
            "measurement": "api_to_bridge_health"}

    async def transport(row, name, method, payload=None):
        if name == "device/diagnostics/proxy":
            assert method == "POST"
            assert set(payload) == {"request_id", "device_id", "generation", "settings_revision"}
            c.bridge.calls.append((str(row["id"]), name, method))
            c.diag_proxy_dispatches.append(dict(payload))
            if c.diag_proxy_reply_lost:
                raise HTTPException(503, "Synthetic proxy response lost after recording")
            return {**payload, "status": "running", "recorded": True}
        if name.startswith("device/diagnostics/operations/"):
            assert method == "GET"
            c.bridge.calls.append((str(row["id"]), name, method))
            identity = name.rsplit("/", 1)[-1]
            return c.diag_receipts.get(identity, {"request_id": identity, "recorded": False, "status": "uncertain"})
        return await original_transport(row, name, method, payload)

    c.diag_status_reader = status
    monkeypatch.setattr(api, "read_status", status)
    monkeypatch.setattr(api, "read_server", server)
    monkeypatch.setattr(qr_channels, "_bridge_control", transport)
    try:
        yield c
    finally:
        # The intent FK points to immutable effects; remove dependants first.
        async with c.ctx.migration.begin() as db:
            await db.execute(text("DELETE FROM subscription_diagnostic_requests WHERE tenant_id IN (:t,:a,:b)"),
                {"t": c.tenant, "a": c.ctx.a, "b": c.ctx.b})


async def persisted(c, *, include_requests=True):
    result = {}
    async with c.ctx.migration.connect() as db:
        for table in TABLES:
            if not include_requests and table == "subscription_diagnostic_requests":
                continue
            result[table] = (await db.execute(text(f"""SELECT COALESCE(
              jsonb_agg(to_jsonb(entry) ORDER BY to_jsonb(entry)::text),'[]'::jsonb)
              FROM {table} entry WHERE tenant_id IN (:t,:a,:b)"""),
              {"t": c.tenant, "a": c.ctx.a, "b": c.ctx.b})).scalar_one()
    return result


@asynccontextmanager
async def no_work(c, monkeypatch, *, unchanged=False, include_requests=True, receipts=False, proxy_dispatch=False):
    before = await persisted(c, include_requests=include_requests) if unchanged else None
    calls = len(c.bridge.calls)
    original_transport = qr_channels._bridge_control
    attempted = []

    async def denied(*args, **kwargs):
        attempted.append("external execution")
        pytest.fail("A diagnostic read/preparation attempted external work")

    def compile_denied(*args, **kwargs):
        attempted.append("compilation")
        pytest.fail("A diagnostic attempted to compile chatbot rules")

    async def transport(row, name, method, payload=None):
        if receipts and method == "GET" and name.startswith("device/diagnostics/operations/"):
            return await original_transport(row, name, method, payload)
        if proxy_dispatch and method == "POST" and name == "device/diagnostics/proxy":
            return await original_transport(row, name, method, payload)
        return await denied(row, name, method, payload)

    with monkeypatch.context() as patch:
        patch.setattr(qr_channels, "_bridge_control", transport)
        patch.setattr(devices, "_refresh", denied)
        patch.setattr(subscription_chatbot_http, "read_url_tags", denied)
        patch.setattr(subscription_device_audio, "transcribe_audio", denied)
        patch.setattr(subscription_device_audio, "_provider_transcribe", denied)
        patch.setattr(subscription_chatbot_rules, "compile_plan", compile_denied)
        patch.setattr(subscription_chatbot_engine, "record_event", denied)
        try:
            yield
        finally:
            assert not attempted
            assert all((receipts and method == "GET" and name.startswith("device/diagnostics/operations/"))
                       or (proxy_dispatch and method == "POST" and name == "device/diagnostics/proxy")
                       for _, name, method in c.bridge.calls[calls:])
            if unchanged:
                after = await persisted(c, include_requests=include_requests)
                assert after == before, [table for table in after if after[table] != before[table]]


async def setup_device(c, *, connected=True, proxy=False):
    await create(c)
    await settings(c, lambda value: (
        value.update(timezone="America/Cuiaba", test_phone_e164="+5567999999907"),
        value["proxy"].update(enabled=True, url="https://proxy.example.com:8080",
            username="SYNTHETIC_PRIVATE_PROXY_USER", password="SYNTHETIC_PRIVATE_PROXY_PASSWORD") if proxy else None))
    c.diag_status = "connected" if connected else "stopped"
    if connected:
        await action(c, "connect")
        await event(c, kind="status", state="connected", phone=SELF_PHONE, name="Aparelho sintético")
    return c.device


async def device_version(c):
    response = await c.client.get(c.diagnostics)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return next(item["version"] for item in response.json()["devices"] if item["id"] == c.device["id"])


async def report(c):
    response = await c.client.get(c.diagnostics + "/" + c.device["id"])
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()


async def prepare(c, *, key=None, phone=None, version=None):
    return await c.client.post(c.diagnostics + "/" + c.device["id"] + "/test-messages/prepare",
        json={"idempotency_key": str(key or uuid4()), "expected_version": version or await device_version(c), "phone": phone})


async def confirm(c, prepared, **changes):
    body = {"idempotency_key": prepared["idempotency_key"],
        "confirmation_version": prepared["preview"]["confirmation_version"], "confirmation": "ENVIAR", **changes}
    return await c.client.post(c.diagnostics + "/" + prepared["device_id"] + "/test-messages/" + prepared["request_id"] + "/confirm", json=body)


async def recover(c, operation):
    response = await c.client.get(c.diagnostics + "/requests/" + operation["idempotency_key"])
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()


async def request_record(c, operation):
    async with c.ctx.migration.connect() as db:
        return dict((await db.execute(text("SELECT * FROM subscription_diagnostic_requests WHERE tenant_id=:t AND id=:id"),
            {"t": c.tenant, "id": UUID(operation["request_id"])})).mappings().one())


async def effect_record(c, identity):
    async with c.ctx.migration.connect() as db:
        return dict((await db.execute(text("SELECT * FROM subscription_device_effects WHERE tenant_id=:t AND id=:id"),
            {"t": c.tenant, "id": UUID(str(identity))})).mappings().one())


async def seed_message(c, *, state="accepted", phone=OTHER_PHONE, seconds=0, message="Mensagem sintética", kind="send_text"):
    source, identity = uuid4(), uuid4()
    async with c.ctx.migration.begin() as db:
        await db.execute(text("""INSERT INTO subscription_device_events
          (id,tenant_id,device_id,generation,provider_event_id,kind,occurred_at,payload)
          VALUES(:id,:t,:d,:g,:provider,'text',clock_timestamp(),CAST(:payload AS jsonb))"""),
          {"id": source, "t": c.tenant, "d": UUID(c.device["id"]), "g": c.saved["generation"],
           "provider": "DIAGNOSTIC_SYNTHETIC_" + source.hex,
           "payload": json.dumps({"phone": phone, "name": "Contato <script>literal</script>", "text": "Entrada sintética",
                                 "private_token": "SYNTHETIC_PRIVATE_INPUT_TOKEN"})})
        await db.execute(text("""INSERT INTO subscription_device_effects
          (id,tenant_id,device_id,generation,settings_revision,event_id,effect_key,type,purpose,payload,
           status,error_code,created_at,finished_at)
          VALUES(:id,:t,:d,:g,:r,:event,:key,:kind,:purpose,CAST(:payload AS jsonb),CAST(:state AS varchar(20)),:error,
            statement_timestamp()-(:seconds * interval '1 second'),
            CASE WHEN CAST(:state AS varchar(20)) IN ('pending','running') THEN NULL ELSE statement_timestamp() END)"""),
          {"id": identity, "t": c.tenant, "d": UUID(c.device["id"]), "g": c.saved["generation"], "r": c.device["revision"],
           "event": source, "key": identity.hex, "kind": kind, "purpose": "absence" if kind == "send_text" else "webhook",
           "payload": json.dumps({"phone": phone, "text": message, "private_token": "SYNTHETIC_PRIVATE_OUTPUT_TOKEN"}),
           "state": state, "error": "TRANSPORT_UNAVAILABLE_BEFORE_SEND" if state == "not_sent" else None, "seconds": seconds})
    return identity


async def test_initial_list_is_sql_only_and_never_selects_or_connects_automatically(diagnostic_case, monkeypatch):
    c = diagnostic_case
    async with no_work(c, monkeypatch, unchanged=True):
        empty = await c.client.get(c.diagnostics)
        assert empty.status_code == 200 and empty.json()["devices"] == []
        assert empty.json()["selected_device_id"] is None
    await setup_device(c, connected=False)
    async with no_work(c, monkeypatch, unchanged=True):
        data = (await c.client.get(c.diagnostics)).json()
        assert data["selected_device_id"] is None and len(data["devices"]) == 1
        assert data["devices"][0]["id"] == c.device["id"]
        assert len(data["devices"][0]["version"]) == 64
    assert c.diag_reads == []
    assert c.bridge.calls == []


@pytest.mark.parametrize("live_state,health", [("connected", "available"), ("disconnected", "available"), (None, "unavailable")])
async def test_refresh_separates_server_connection_and_stale_state_without_writes(diagnostic_case, monkeypatch, live_state, health):
    c = diagnostic_case
    await setup_device(c)
    c.diag_status, c.diag_server = live_state, health
    async with no_work(c, monkeypatch, unchanged=True):
        first, second = await report(c), await report(c)
    assert first["server"]["state"] == health
    assert first["server"]["latency_ms"] == (17 if health == "available" else None)
    assert first["server"]["measurement"] == "api_to_bridge_health"
    if live_state is None:
        assert first["connection"]["source"] == "stored" and first["connection"]["freshness"] == "stale"
        assert first["test_message"]["can_prepare"] is False
    else:
        assert first["connection"]["state"] == live_state and first["connection"]["freshness"] == "current"
        assert first["test_message"]["can_prepare"] is (live_state == "connected")
    assert second["device"] == first["device"] and first["recent_messages"] == {"limit": 3, "items": []}
    assert "SYNTHETIC_PRIVATE" not in json.dumps(first)


async def test_three_recent_attempts_are_scoped_ordered_bounded_and_sanitized(diagnostic_case, monkeypatch):
    c = diagnostic_case
    await setup_device(c)
    older = await seed_message(c, seconds=40)
    expected = [await seed_message(c, state=state, seconds=seconds, message=("á🙂" * 1100 if state == "uncertain" else "<img src=x>"))
                for state, seconds in (("accepted", 30), ("not_sent", 20), ("uncertain", 10))]
    await seed_message(c, kind="webhook", seconds=0)
    await foreign_message(c)
    async with no_work(c, monkeypatch, unchanged=True):
        data = await report(c)
    rows = data["recent_messages"]["items"]
    assert [row["id"] for row in rows] == [str(value) for value in reversed(expected)]
    assert str(older) not in json.dumps(rows)
    assert [row["status"] for row in rows] == ["uncertain", "failed", "sent"]
    assert rows[0]["truncated"] and rows[0]["sent"]["truncated"]
    assert data["recent_messages"]["limit"] == 3
    assert not any(secret in json.dumps(data) for secret in
        ("SYNTHETIC_PRIVATE", "FOREIGN_PRIVATE", "credential_encrypted", "claimed_owner", "claimed_fence", "token_hash"))
    assert all("actions" not in row and "resend" not in row for row in rows)


async def test_real_auth_csrf_scope_and_private_ledger_grants(diagnostic_case, monkeypatch):
    c = diagnostic_case
    await setup_device(c)
    prepared = (await prepare(c)).json()
    foreign_effect = await foreign_message(c)
    async with c.ctx.migration.connect() as db:
        foreign = (await db.execute(text("SELECT device_id FROM subscription_device_effects WHERE id=:id"),
            {"id": foreign_effect})).scalar_one()
    assert (await c.private.get(c.diagnostics)).status_code == 401
    endpoint = c.diagnostics + "/" + c.device["id"] + "/test-messages/prepare"
    assert (await c.client.post(endpoint, json={}, headers={"x-orion-csrf": ""})).status_code == 403
    async with no_work(c, monkeypatch, unchanged=True):
        assert (await c.client.get(c.diagnostics.replace(str(c.tenant), str(c.ctx.b)))).status_code == 403
        assert (await c.client.get(c.diagnostics + "/" + str(foreign))).status_code == 404
        assert (await c.client.get(c.diagnostics + "/requests/" + str(uuid4()))).status_code == 404
        assert (await recover(c, prepared))["request_id"] == prepared["request_id"]
    async with c.ctx.runtime() as db:
        for field in ("id", "snapshot"):
            with pytest.raises(DBAPIError, match="permission denied for table subscription_diagnostic_requests") as denied:
                async with tenant_transaction(db, c.tenant):
                    await db.execute(text(f"SELECT {field} FROM subscription_diagnostic_requests"))
            assert denied.value.orig.sqlstate == "42501"
    async with support_session_factory()() as db:
        with pytest.raises(DBAPIError) as denied:
            async with db.begin():
                await db.execute(text("UPDATE subscription_diagnostic_requests SET snapshot='{}'::jsonb WHERE id=:id"),
                    {"id": UUID(prepared["request_id"])})
        assert denied.value.orig.sqlstate == "42501"


@pytest.mark.parametrize("change", ["owner", "viewer", "niche"])
async def test_each_route_rechecks_authority_before_decoding_input(diagnostic_case, monkeypatch, change):
    c = diagnostic_case
    await setup_device(c)
    prepared = (await prepare(c)).json()
    await revoke(c, change)
    root = c.diagnostics + "/" + c.device["id"]
    async with no_work(c, monkeypatch, unchanged=True):
        for path in (c.diagnostics, root, c.diagnostics + "/requests/" + prepared["idempotency_key"]):
            assert (await c.client.get(path)).status_code == 403
        for path in (root + "/test-messages/prepare", root + "/test-messages/" + prepared["request_id"] + "/confirm",
                     root + "/proxy-tests"):
            response = await c.client.post(path, content=b"not-json", headers={"content-type": "application/json"})
            assert response.status_code == 403, response.text


@pytest.mark.parametrize("phone,mode", [(None, "self"), ("+" + SELF_PHONE, "self"), ("+" + OTHER_PHONE, "explicit")])
async def test_durable_prepare_is_idempotent_and_creates_no_effect(diagnostic_case, monkeypatch, phone, mode):
    c = diagnostic_case
    await setup_device(c)
    key, version = uuid4(), await device_version(c)
    async with no_work(c, monkeypatch, unchanged=True, include_requests=False):
        responses = await asyncio.gather(*(prepare(c, key=key, phone=phone, version=version) for _ in range(2)))
    assert [response.status_code for response in responses] == [201, 201]
    first, replay = (response.json() for response in responses)
    assert first["request_id"] == replay["request_id"] and first["request_id"] != str(key)
    assert sorted([first["replayed"], replay["replayed"]]) == [False, True]
    assert first["status"] == "prepared" and first["message"] is None and first["proxy"] is None
    assert first["preview"]["recipient"] == {"mode": mode, "phone_e164": phone or "+" + SELF_PHONE}
    assert first["preview"]["text"] == CONTRACT["detail_response"]["test_message"]["text"] == api.TEST_TEXT
    created = datetime.fromisoformat(first["created_at"])
    expires = datetime.fromisoformat(first["preview"]["expires_at"])
    assert (expires - created).total_seconds() == 120
    saved = await request_record(c, first)
    assert saved["effect_id"] is None and saved["confirmed_at"] is None
    async with no_work(c, monkeypatch, unchanged=True):
        recovered = await recover(c, first)
        conflict = await prepare(c, key=key, phone="+" + OTHER_PHONE if phone is None else None, version=version)
    assert recovered["preview"] == first["preview"] and recovered["status"] == "prepared"
    assert conflict.status_code == 409 and conflict.json()["detail"]["code"] == "DIAGNOSTIC_KEY_CONFLICT"


@pytest.mark.parametrize("phone,mode", [(None, "self"), ("+" + OTHER_PHONE, "explicit")])
async def test_confirmation_admits_exactly_one_frozen_effect_and_reads_never_dispatch(diagnostic_case, monkeypatch, phone, mode):
    c = diagnostic_case
    await setup_device(c)
    prepared = (await prepare(c, phone=phone)).json()
    before = await persisted(c)
    async with no_work(c, monkeypatch):
        responses = await asyncio.gather(*(confirm(c, prepared) for _ in range(2)))
    assert [response.status_code for response in responses] == [202, 202]
    values = [response.json() for response in responses]
    assert sorted(value["replayed"] for value in values) == [False, True]
    assert values[0]["message"]["effect_id"] == values[1]["message"]["effect_id"]
    operation = values[0]
    assert operation["status"] == "pending" and operation["preview"] == prepared["preview"]
    effect = await effect_record(c, operation["message"]["effect_id"])
    assert effect["type"] == "send_text" and effect["purpose"] == "diagnostic_test"
    assert effect["payload"] == {"phone": (phone or SELF_PHONE).lstrip("+"), "text": api.TEST_TEXT}
    assert effect["attempts"] == 0 and effect["status"] == "pending" and effect["authorized_at"] is None
    assert effect["provider_id"] is None and effect["chatbot_run_id"] is None
    after = await persisted(c)
    assert len(after["subscription_device_effects"]) == len(before["subscription_device_effects"]) + 1
    assert len(after["subscription_device_events"]) == len(before["subscription_device_events"]) + 1
    for table in TABLES:
        if table not in {"subscription_device_effects", "subscription_device_events", "subscription_diagnostic_requests"}:
            assert after[table] == before[table], table
    async with no_work(c, monkeypatch, unchanged=True):
        for _ in range(2):
            recovered = await recover(c, prepared)
            assert recovered["message"]["effect_id"] == str(effect["id"]) and recovered["message"]["outcome"] == "pending"
        assert (await report(c))["recent_messages"]["items"][0]["id"] == str(effect["id"])


def rejected(response, status, code=None):
    assert response.status_code == status, response.text
    if code:
        assert response.json()["detail"]["code"] == code, response.text


def effect_descriptor(operation):
    return {"operation_id": operation["message"]["effect_id"], "type": "send_text"}


async def renew_lease(c):
    connection = c.bridge.connections[str(c.row["id"])]
    response = await c.private.post(f"/internal/qr/channels/{c.row['id']}/lease/renew",
        headers=c.bridge.headers(c.row), json={"owner": str(c.bridge.owner), "fence": connection["fence"]})
    assert response.status_code == 200, response.text


@pytest.mark.parametrize("changes,status,code", [
    ({"confirmation_version": "0" * 64}, 409, "DIAGNOSTIC_CONFIRMATION_INVALID"),
    ({"idempotency_key": "99000000-0000-4000-8000-000000000001"}, 409, "DIAGNOSTIC_KEY_CONFLICT"),
    ({"confirmation": "SIM"}, 422, None),
    ({"phone": "+" + OTHER_PHONE}, 422, None),
])
async def test_confirmation_requires_exact_intention_and_literal_with_no_admission(diagnostic_case, monkeypatch, changes, status, code):
    c = diagnostic_case
    await setup_device(c)
    prepared = (await prepare(c)).json()
    async with no_work(c, monkeypatch, unchanged=True):
        rejected(await confirm(c, prepared, **changes), status, code)
        assert (await recover(c, prepared))["status"] == "prepared"


async def test_expired_preview_is_derived_on_read_and_cannot_be_confirmed(diagnostic_case, monkeypatch):
    c = diagnostic_case
    await setup_device(c)
    prepared = (await prepare(c)).json()
    async with c.ctx.migration.begin() as db:
        await db.execute(text("""UPDATE subscription_diagnostic_requests
          SET created_at=created_at-interval '130 seconds',expires_at=expires_at-interval '130 seconds'
          WHERE id=:id"""), {"id": UUID(prepared["request_id"])})
    async with no_work(c, monkeypatch, unchanged=True):
        for _ in range(2):
            recovered = await recover(c, prepared)
            assert recovered["status"] == "expired" and recovered["message"] is None
        rejected(await confirm(c, prepared), 410, "DIAGNOSTIC_PREVIEW_EXPIRED")
    assert (await request_record(c, prepared))["status"] == "prepared"


@pytest.mark.parametrize("change,code", [
    ("revision", "DIAGNOSTIC_CONTEXT_CHANGED"),
    ("disconnected", "DIAGNOSTIC_DEVICE_UNAVAILABLE"),
    ("unverified", "DIAGNOSTIC_SELF_IDENTITY_UNVERIFIED"),
    ("self_phone", "DIAGNOSTIC_SELF_IDENTITY_CHANGED"),
])
async def test_confirmation_rechecks_frozen_device_and_verified_self(diagnostic_case, monkeypatch, change, code):
    c = diagnostic_case
    await setup_device(c)
    prepared = (await prepare(c, phone="+" + OTHER_PHONE)).json()
    if change == "revision":
        await settings(c, lambda value: value.update(name="Nova revisão sintética"))
    elif change == "disconnected":
        c.diag_status = "disconnected"
    elif change == "unverified":
        c.diag_self = None
    else:
        c.diag_self = "5567999999904"
    async with no_work(c, monkeypatch, unchanged=True):
        rejected(await confirm(c, prepared), 409, code)
        assert (await recover(c, prepared))["message"] is None


@pytest.mark.parametrize("phase", ["prepare", "confirm"])
async def test_owner_revoked_during_status_read_is_rechecked_before_intent_or_effect(diagnostic_case, monkeypatch, phase):
    c = diagnostic_case
    await setup_device(c)
    prepared = (await prepare(c)).json() if phase == "confirm" else None
    version = await device_version(c)

    async def revoke_after_read(transport, row):
        value = await c.diag_status_reader(transport, row)
        await revoke(c, "owner")
        return value

    monkeypatch.setattr(api, "read_status", revoke_after_read)
    async with no_work(c, monkeypatch, unchanged=True):
        response = await confirm(c, prepared) if prepared else await prepare(c, version=version)
        rejected(response, 403)


async def test_distinct_concurrent_confirmations_admit_one_active_attempt(diagnostic_case, monkeypatch):
    c = diagnostic_case
    await setup_device(c)
    first, second = [(await prepare(c, phone=phone)).json() for phone in (None, "+" + OTHER_PHONE)]
    before = await persisted(c)
    async with no_work(c, monkeypatch):
        responses = await asyncio.gather(confirm(c, first), confirm(c, second))
    assert sorted(response.status_code for response in responses) == [202, 409]
    rejected(next(response for response in responses if response.status_code == 409), 409, "DIAGNOSTIC_BUSY")
    after = await persisted(c)
    assert len(after["subscription_device_effects"]) == len(before["subscription_device_effects"]) + 1
    assert len(after["subscription_device_events"]) == len(before["subscription_device_events"]) + 1
    assert sorted(row["status"] for row in after["subscription_diagnostic_requests"]) == ["pending", "prepared"]


async def test_existing_contact_attempt_blocks_test_without_mutating_original(diagnostic_case, monkeypatch):
    c = diagnostic_case
    await setup_device(c)
    original = await seed_message(c, state="pending", phone=OTHER_PHONE)
    prepared = (await prepare(c, phone="+" + OTHER_PHONE)).json()
    async with no_work(c, monkeypatch, unchanged=True):
        rejected(await confirm(c, prepared), 409, "DIAGNOSTIC_CONTACT_BUSY")
    assert (await effect_record(c, original))["status"] == "pending"


async def test_confirmed_intent_replays_after_revision_change_without_a_new_effect(diagnostic_case, monkeypatch):
    c = diagnostic_case
    await setup_device(c)
    prepared = (await prepare(c)).json()
    accepted = await confirm(c, prepared)
    assert accepted.status_code == 202, accepted.text
    operation = accepted.json()
    previous_scope = scope(c)
    await settings(c, lambda value: value.update(name="Configuração posterior"))
    async with no_work(c, monkeypatch, unchanged=True):
        replay = await confirm(c, prepared)
        assert replay.status_code == 202 and replay.json()["replayed"] is True
        assert replay.json()["message"]["effect_id"] == operation["message"]["effect_id"]
        rejected(await prepare(c, version=CONTRACT["list_response"]["devices"][0]["version"]),
            409, "DIAGNOSTIC_CONTEXT_CHANGED")
    await renew_lease(c)
    rejected(await internal(c, "/effects/authorize", {**previous_scope,
        "operation_id": operation["message"]["effect_id"]}), 409, "DEVICE_CONFLICT")
    assert (await effect_record(c, operation["message"]["effect_id"]))["attempts"] == 0


@pytest.mark.parametrize("phone,outcome", [(None, "accepted"), ("+" + OTHER_PHONE, "uncertain")])
async def test_only_confirmed_broker_effect_gets_self_exception_and_never_repeats(diagnostic_case, monkeypatch, phone, outcome):
    c = diagnostic_case
    await setup_device(c)
    prepared = (await prepare(c, phone=phone)).json()
    response = await confirm(c, prepared)
    assert response.status_code == 202, response.text
    operation = response.json()
    effect = effect_descriptor(operation)
    await renew_lease(c)
    async with no_work(c, monkeypatch):
        grant = await permit(c, effect)
        assert grant["authorized"] and grant["purpose"] == "diagnostic_test" and grant["confirmed"] is True
        assert grant["diagnostic_request_id"] == prepared["request_id"]
        assert grant["recipient_mode"] == ("explicit" if phone else "self") and grant["self_phone"] == SELF_PHONE
        assert grant["payload"] == {"phone": (phone or SELF_PHONE).lstrip("+"), "text": api.TEST_TEXT}
        recheck = await internal(c, "/effects/authorize", {**scope(c), "operation_id": effect["operation_id"], "recheck": True})
        assert recheck.status_code == 200 and recheck.json() == grant, recheck.text
        assert (await permit(c, effect))["authorized"] is False
        await finish(c, effect, outcome, provider_id="SYNTHETIC_ACCEPTED_ID" if outcome == "accepted" else None,
            error_code=None if outcome == "accepted" else "SYNTHETIC_PRIVATE_PROVIDER_ERROR")
    async with no_work(c, monkeypatch, unchanged=True):
        recovered = await recover(c, prepared)
        assert recovered["status"] == ("succeeded" if outcome == "accepted" else "uncertain")
        assert recovered["message"]["outcome"] == outcome
        assert "SYNTHETIC_PRIVATE_PROVIDER_ERROR" not in json.dumps(recovered)
        replay = await confirm(c, prepared)
        assert replay.status_code == 202 and replay.json()["message"]["effect_id"] == effect["operation_id"]
        assert (await permit(c, effect))["authorized"] is False
    final = await effect_record(c, effect["operation_id"])
    assert final["attempts"] == 1 and final["status"] == outcome


@pytest.mark.parametrize("stage", ["before_grant", "running_recheck"])
async def test_self_identity_change_revokes_broker_authority(stage, diagnostic_case, monkeypatch):
    c = diagnostic_case
    await setup_device(c)
    prepared = (await prepare(c)).json()
    operation = (await confirm(c, prepared)).json()
    effect = effect_descriptor(operation)
    await renew_lease(c)
    if stage == "running_recheck":
        assert (await permit(c, effect))["authorized"]
    await event(c, kind="status", state="connected", phone="5567999999904")
    async with no_work(c, monkeypatch):
        response = await internal(c, "/effects/authorize", {**scope(c), "operation_id": effect["operation_id"],
            "recheck": stage == "running_recheck"})
        assert response.status_code == 200 and response.json()["authorized"] is False, response.text
    row = await effect_record(c, effect["operation_id"])
    assert row["status"] == ("not_sent" if stage == "before_grant" else "running")
    assert row["attempts"] == (0 if stage == "before_grant" else 1)


async def proxy_test(c, *, key=None, version=None, **extra):
    return await c.client.post(c.diagnostics + "/" + c.device["id"] + "/proxy-tests",
        json={"idempotency_key": str(key or uuid4()), "expected_version": version or await device_version(c), **extra})


async def proxy_callback(c, name, body, *, headers=None):
    # Independent diagnostic claims use the channel credential, not a live
    # WhatsApp lease or the lifecycle controller's operation endpoint.
    return await internal(c, "/diagnostics/proxy/" + name, body, headers=headers or c.bridge.headers(c.row))


async def claim_proxy(c, operation):
    claim = {**scope(c), "request_id": operation["request_id"], "owner": str(uuid4()), "claim_token": str(uuid4())}
    response = await proxy_callback(c, "authorize", {**claim, "recheck": False})
    assert response.status_code == 200 and response.json()["authorized"], response.text
    return claim, response.json()


def proxy_result(grant, **changes):
    return {"scope": "configured_proxy", "configuration_version": grant["configuration_version"],
        "applied_to_session": False, "state": "reachable", "latency_ms": 17,
        "target": "tls_reference", "observed_at": datetime.now(timezone.utc).isoformat(), "error_code": None, **changes}


async def test_proxy_claim_is_independent_of_whatsapp_and_idempotent_after_response_loss(diagnostic_case, monkeypatch):
    c = diagnostic_case
    await setup_device(c, connected=False, proxy=True)
    assert not c.bridge.connections
    c.diag_proxy_reply_lost = True
    key, version = uuid4(), await device_version(c)
    async with no_work(c, monkeypatch, unchanged=True, include_requests=False, proxy_dispatch=True):
        first, replay = await asyncio.gather(*(proxy_test(c, key=key, version=version) for _ in range(2)))
    assert first.status_code == replay.status_code == 202
    operation = first.json()
    assert operation["status"] == "pending" and operation["message"] is operation["preview"] is None
    assert operation["request_id"] == replay.json()["request_id"] != str(key)
    assert sorted([operation["replayed"], replay.json()["replayed"]]) == [False, True]
    assert len(c.diag_proxy_dispatches) == 1 and not c.bridge.connections
    assert c.diag_reads == []
    async with no_work(c, monkeypatch, unchanged=True, receipts=True):
        recovered = await recover(c, operation)
        assert recovered["status"] == "pending"
        rejected(await proxy_test(c), 409, "DIAGNOSTIC_BUSY")
    async with no_work(c, monkeypatch, unchanged=True, include_requests=False):
        claim, grant = await claim_proxy(c, operation)
        assert grant["claim_fence"] == 1 and 0 < grant["expires_in_ms"] <= 30000
        assert grant["owner"] != str(c.bridge.owner) and grant["kind"] == "proxy_test"
        assert grant["proxy"]["url"] == "https://proxy.example.com:8080"
        assert grant["proxy_secret"] == {"password": "SYNTHETIC_PRIVATE_PROXY_PASSWORD"}
        duplicate = await proxy_callback(c, "authorize", {**claim, "recheck": False})
        assert duplicate.status_code == 200 and duplicate.json()["authorized"] is False
        again = await proxy_callback(c, "authorize", {**claim, "recheck": True, "claim_fence": grant["claim_fence"]})
        assert again.status_code == 200 and again.json()["authorized"] is True, again.text
        for changed in ({"claim_token": str(uuid4())}, {"claim_fence": 2}):
            denied = await proxy_callback(c, "authorize", {**claim, "recheck": True, "claim_fence": 1, **changed})
            assert denied.status_code == 200 and denied.json()["authorized"] is False, denied.text
        result = proxy_result(grant)
        completion = {**claim, "claim_fence": 1, "status": "succeeded", "result": result}
        ended = await proxy_callback(c, "finish", completion)
        assert ended.status_code == 200 and ended.json()["status"] == "succeeded", ended.text
    async with no_work(c, monkeypatch, unchanged=True):
        duplicate_finish = await proxy_callback(c, "finish", completion)
        assert duplicate_finish.status_code == 200
        receipt = await proxy_callback(c, "receipt", {"request_id": operation["request_id"]})
        assert receipt.status_code == 200 and receipt.json()["read_only"] is True
        recovered = await recover(c, operation)
        assert recovered["status"] == "succeeded" and recovered["proxy"]["status"] == "reachable"
        assert recovered["proxy"]["scope"] == "configured" and recovered["proxy"]["applied_to_session"] == "no"
        assert recovered["proxy"]["latency_ms"] == 17
        data = await report(c)
        # Creation leaves the canonical device disabled; a synthetic stopped
        # transport must not imply that the proxy test connected WhatsApp.
        assert data["connection"]["state"] == "disabled"
        assert data["proxy"]["last_test"]["freshness"] == "current"
        assert data["test_message"]["can_prepare"] is False
        assert not any(value in json.dumps([data, recovered]) for value in
            ("SYNTHETIC_PRIVATE", "claim_token", "claimed_owner", "proxy_secret", "credential_encrypted"))
    assert len(c.diag_proxy_dispatches) == 1 and not c.bridge.connections


async def test_proxy_recovery_can_project_a_receipt_without_persisting_or_reprobing(diagnostic_case, monkeypatch):
    c = diagnostic_case
    await setup_device(c, connected=False, proxy=True)
    operation = (await proxy_test(c)).json()
    claim, grant = await claim_proxy(c, operation)
    c.diag_receipts[operation["request_id"]] = {**scope(c), "request_id": operation["request_id"],
        "recorded": True, "status": "succeeded", "result": proxy_result(grant)}
    async with no_work(c, monkeypatch, unchanged=True, receipts=True):
        for _ in range(2):
            recovered = await recover(c, operation)
            assert recovered["status"] == "succeeded" and recovered["proxy"]["status"] == "reachable"
        receipt = await proxy_callback(c, "receipt", {"request_id": operation["request_id"]})
        assert receipt.status_code == 200 and receipt.json()["read_only"] is True
    saved = await request_record(c, operation)
    assert saved["status"] == "running" and saved["result"] is None and saved["finished_at"] is None
    assert saved["claim_token"] == UUID(claim["claim_token"]) and len(c.diag_proxy_dispatches) == 1
    c.diag_receipts[operation["request_id"]]["result"]["configuration_version"] = "0" * 64
    async with no_work(c, monkeypatch, unchanged=True, receipts=True):
        assert (await recover(c, operation))["status"] == "running"


@pytest.mark.parametrize("claimed", [False, True])
async def test_expired_proxy_authorization_never_restarts_or_reclaims(diagnostic_case, monkeypatch, claimed):
    c = diagnostic_case
    await setup_device(c, connected=False, proxy=True)
    operation = (await proxy_test(c)).json()
    claim, grant = await claim_proxy(c, operation) if claimed else (None, None)
    async with c.ctx.migration.begin() as db:
        if claimed:
            await db.execute(text("""UPDATE subscription_diagnostic_requests
              SET created_at=statement_timestamp()-interval '55 seconds',expires_at=statement_timestamp()+interval '5 seconds',
                execution_expires_at=statement_timestamp()+interval '5 seconds',
                started_at=statement_timestamp()-interval '40 seconds',claim_expires_at=statement_timestamp()-interval '10 seconds'
              WHERE id=:id"""), {"id": UUID(operation["request_id"])})
        else:
            await db.execute(text("""UPDATE subscription_diagnostic_requests
              SET created_at=created_at-interval '70 seconds',expires_at=expires_at-interval '70 seconds',
                execution_expires_at=execution_expires_at-interval '70 seconds' WHERE id=:id"""),
                {"id": UUID(operation["request_id"])})
    async with no_work(c, monkeypatch, unchanged=True, receipts=True):
        recovered = await recover(c, operation)
        assert recovered["status"] == ("uncertain" if claimed else "failed")
        assert recovered["proxy"]["error_code"] == ("PROXY_OUTCOME_UNCERTAIN" if claimed else "PROXY_EXPIRED_BEFORE_START")
        if claimed:
            again = await proxy_callback(c, "authorize", {**claim, "recheck": True, "claim_fence": grant["claim_fence"]})
            assert again.status_code == 200 and again.json()["authorized"] is False
            initial = await proxy_callback(c, "authorize", {**claim, "recheck": False})
            assert initial.status_code == 200 and initial.json()["authorized"] is False
        else:
            rejected(await proxy_callback(c, "authorize", {**scope(c), "request_id": operation["request_id"],
                "owner": str(uuid4()), "claim_token": str(uuid4()), "recheck": False}), 409, "DIAGNOSTIC_CONTEXT_CHANGED")
    assert len(c.diag_proxy_dispatches) == 1


async def test_proxy_configuration_change_revokes_probe_and_labels_old_receipt(diagnostic_case, monkeypatch):
    c = diagnostic_case
    await setup_device(c, connected=False, proxy=True)
    operation = (await proxy_test(c)).json()
    claim, grant = await claim_proxy(c, operation)
    await settings(c, lambda value: value["proxy"].update(url="https://proxy2.example.com:8080"))
    async with no_work(c, monkeypatch, unchanged=True):
        rejected(await proxy_callback(c, "authorize", {**claim, "claim_fence": 1, "recheck": True}),
            409, "DIAGNOSTIC_CONTEXT_CHANGED")
    completed = await proxy_callback(c, "finish", {**claim, "claim_fence": 1, "status": "succeeded", "result": proxy_result(grant)})
    assert completed.status_code == 200 and completed.json()["status"] == "failed", completed.text
    async with no_work(c, monkeypatch, unchanged=True):
        recovered = await recover(c, operation)
        assert recovered["proxy"]["error_code"] == "PROXY_CONFIGURATION_CHANGED"
        assert recovered["proxy"]["applied_to_session"] == "no"
        data = await report(c)
        assert data["proxy"]["host"] == "proxy2.example.com" and data["proxy"]["last_test"]["freshness"] == "stale"


@pytest.mark.parametrize("change,status", [("owner", 401), ("niche", 403)])
async def test_private_proxy_callbacks_recheck_owner_and_niche(change, status, diagnostic_case, monkeypatch):
    c = diagnostic_case
    await setup_device(c, connected=False, proxy=True)
    operation = (await proxy_test(c)).json()
    claim, grant = await claim_proxy(c, operation)
    await revoke(c, change)
    async with no_work(c, monkeypatch, unchanged=True):
        rejected(await proxy_callback(c, "authorize", {**claim, "claim_fence": 1, "recheck": True}), status)
        rejected(await proxy_callback(c, "finish", {**claim, "claim_fence": 1, "status": "succeeded", "result": proxy_result(grant)}), status)
        rejected(await proxy_callback(c, "receipt", {"request_id": operation["request_id"]}), status)


@pytest.mark.parametrize("field,value", [("url", "http://127.0.0.1/private"), ("host", "other.example.com"),
    ("password", "SYNTHETIC_PRIVATE_OVERRIDE")])
async def test_proxy_request_never_accepts_an_arbitrary_target_or_credentials(diagnostic_case, monkeypatch, field, value):
    c = diagnostic_case
    await setup_device(c, connected=False, proxy=True)
    async with no_work(c, monkeypatch, unchanged=True):
        rejected(await proxy_test(c, **{field: value}), 422)
    assert not c.diag_proxy_dispatches


async def test_direct_connection_has_no_proxy_test_and_key_cannot_change_kind(diagnostic_case, monkeypatch):
    c = diagnostic_case
    await setup_device(c)
    prepared = (await prepare(c)).json()
    async with no_work(c, monkeypatch, unchanged=True):
        data = await report(c)
        assert data["proxy"]["mode"] == "direct" and data["proxy"]["can_test"] is False
        assert data["proxy"]["host"] is None and data["proxy"]["port"] is None
        rejected(await proxy_test(c), 409, "PROXY_NOT_CONFIGURED")
        rejected(await proxy_test(c, key=prepared["idempotency_key"]), 409, "DIAGNOSTIC_KEY_CONFLICT")
    assert not c.diag_proxy_dispatches


async def test_historical_proxy_receipt_remains_scoped_after_device_replacement(diagnostic_case, monkeypatch):
    c = diagnostic_case
    await setup_device(c, connected=False, proxy=True)
    operation = (await proxy_test(c)).json()
    claim, grant = await claim_proxy(c, operation)
    response = await proxy_callback(c, "finish", {**claim, "claim_fence": 1, "status": "succeeded", "result": proxy_result(grant)})
    assert response.status_code == 200, response.text
    old_channel, old_device = c.row["id"], c.device["id"]
    await action(c, "remove")
    await create(c, name="Substituto sintético")
    assert c.row["id"] == old_channel and c.device["id"] != old_device
    async with no_work(c, monkeypatch, unchanged=True):
        receipt = await proxy_callback(c, "receipt", {"request_id": operation["request_id"]})
        assert receipt.status_code == 200 and receipt.json()["read_only"] is True, receipt.text
        assert receipt.json()["device_id"] == old_device
        recovered = await recover(c, operation)
        assert recovered["device_id"] == old_device and recovered["status"] == "succeeded"
        new_report = await report(c)
        assert new_report["recent_messages"]["items"] == [] and new_report["proxy"]["last_test"] is None


async def test_paused_tenant_cannot_grant_proxy_network_but_can_preserve_existing_receipt(diagnostic_case, monkeypatch):
    c = diagnostic_case
    await setup_device(c, connected=False, proxy=True)
    operation = (await proxy_test(c)).json()
    claim = {**scope(c), "request_id": operation["request_id"], "owner": str(uuid4()), "claim_token": str(uuid4())}
    async with c.ctx.migration.begin() as db:
        await db.execute(text("UPDATE tenants SET status='paused' WHERE id=:t"), {"t": c.tenant})
    async with no_work(c, monkeypatch, unchanged=True):
        # Execution still goes through the normal plan guard: a paused tenant
        # loses devices.manage before diagnostic-specific authority is reached.
        rejected(await proxy_callback(c, "authorize", {**claim, "recheck": False}), 403, "WORKSPACE_FEATURE_UNAVAILABLE")
    async with c.ctx.migration.begin() as db:
        await db.execute(text("UPDATE tenants SET status='active' WHERE id=:t"), {"t": c.tenant})
    claim, grant = await claim_proxy(c, operation)
    async with c.ctx.migration.begin() as db:
        await db.execute(text("UPDATE tenants SET status='paused' WHERE id=:t"), {"t": c.tenant})
    async with no_work(c, monkeypatch, unchanged=True):
        rejected(await proxy_callback(c, "authorize", {**claim, "claim_fence": 1, "recheck": True}), 403, "WORKSPACE_FEATURE_UNAVAILABLE")
    async with no_work(c, monkeypatch, unchanged=True, include_requests=False):
        response = await proxy_callback(c, "finish", {**claim, "claim_fence": 1, "status": "succeeded", "result": proxy_result(grant)})
        assert response.status_code == 200 and response.json()["status"] == "succeeded", response.text
    async with no_work(c, monkeypatch, unchanged=True):
        receipt = await proxy_callback(c, "receipt", {"request_id": operation["request_id"]})
        assert receipt.status_code == 200 and receipt.json()["read_only"] is True, receipt.text
    assert len(c.diag_proxy_dispatches) == 1


def stale_rejection(response, c, key, version, kind):
    rejected(response, 409, "DIAGNOSTIC_CONTEXT_CHANGED")
    assert response.json()["detail"]["rejection"] == {"reason": "stale_device_version",
        "idempotency_key": str(key), "device_id": c.device["id"], "kind": kind, "expected_version": version}


@pytest.mark.parametrize("kind", ["message_test", "proxy_test"])
async def test_stale_admission_proves_exact_key_absent_without_creating_work(diagnostic_case, monkeypatch, kind):
    c = diagnostic_case
    await setup_device(c, connected=kind == "message_test", proxy=kind == "proxy_test")
    key, version = uuid4(), await device_version(c)
    await settings(c, lambda value: value.update(name="Revisão posterior à intenção do navegador"))
    assert await device_version(c) != version
    endpoint = prepare if kind == "message_test" else proxy_test
    reads = list(c.diag_reads)
    async with no_work(c, monkeypatch, unchanged=True):
        responses = await asyncio.gather(*(endpoint(c, key=key, version=version) for _ in range(2)))
        for response in responses:
            stale_rejection(response, c, key, version, kind)
        missing = await c.client.get(c.diagnostics + "/requests/" + str(key))
        rejected(missing, 404, "DIAGNOSTIC_NOT_FOUND")
        assert "rejection" not in missing.json()["detail"]
    assert c.diag_reads == reads and not c.diag_proxy_dispatches


async def test_inflight_prepare_with_rejected_stale_key_cannot_commit_after_version_change(diagnostic_case, monkeypatch):
    c = diagnostic_case
    await setup_device(c)
    key, version = uuid4(), await device_version(c)
    entered, resume = asyncio.Event(), asyncio.Event()
    reads = []

    async def held_status(transport, row):
        reads.append(row["revision"])
        entered.set()
        await asyncio.wait_for(resume.wait(), timeout=20)
        return await c.diag_status_reader(transport, row)

    monkeypatch.setattr(api, "read_status", held_status)
    pending = asyncio.create_task(prepare(c, key=key, version=version))
    try:
        await asyncio.wait_for(entered.wait(), timeout=20)
        # This real settings request acquires the same tenant/device locks and
        # increases revision while the earlier prepare is outside its SQL lock.
        await settings(c, lambda value: value.update(name="Mudança durante a leitura de status"))
        assert await device_version(c) != version
        async with no_work(c, monkeypatch, unchanged=True):
            stale_rejection(await prepare(c, key=key, version=version), c, key, version, "message_test")
            resume.set()
            stale_rejection(await asyncio.wait_for(pending, timeout=20), c, key, version, "message_test")
            missing = await c.client.get(c.diagnostics + "/requests/" + str(key))
            rejected(missing, 404, "DIAGNOSTIC_NOT_FOUND")
        assert len(reads) == 1 and not c.diag_proxy_dispatches
    finally:
        resume.set()
        if not pending.done():
            pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.parametrize("kind", ["message_test", "proxy_test"])
async def test_existing_intention_with_old_version_replays_without_rejection_proof(diagnostic_case, monkeypatch, kind):
    c = diagnostic_case
    await setup_device(c, connected=kind == "message_test", proxy=kind == "proxy_test")
    key, version = uuid4(), await device_version(c)
    endpoint = prepare if kind == "message_test" else proxy_test
    first = await endpoint(c, key=key, version=version)
    assert first.status_code == (201 if kind == "message_test" else 202), first.text
    original = first.json()
    await settings(c, lambda value: value.update(name="Configuração após admissão da intenção"))
    assert await device_version(c) != version
    reads, dispatches = list(c.diag_reads), list(c.diag_proxy_dispatches)
    async with no_work(c, monkeypatch, unchanged=True):
        response = await endpoint(c, key=key, version=version)
        assert response.status_code == first.status_code, response.text
        replay = response.json()
        assert replay["request_id"] == original["request_id"] and replay["replayed"] is True
        assert "rejection" not in replay
    assert c.diag_reads == reads and c.diag_proxy_dispatches == dispatches


async def test_proxy_scope_error_after_admission_never_releases_recovery_key(diagnostic_case, monkeypatch):
    c = diagnostic_case
    await setup_device(c, connected=False, proxy=True)
    key, version = uuid4(), await device_version(c)
    original_transport = qr_channels._bridge_control

    async def change_policy_after_dispatch(transport, name, method, payload=None):
        result = await original_transport(transport, name, method, payload)
        if name == "device/diagnostics/proxy":
            async with c.ctx.migration.begin() as db:
                await db.execute(text("UPDATE qr_channels SET allowed_senders=CAST(:senders AS jsonb) WHERE tenant_id=:t AND id=:c"),
                    {"senders": json.dumps([OTHER_PHONE]), "t": c.tenant, "c": c.row["id"]})
        return result

    monkeypatch.setattr(qr_channels, "_bridge_control", change_policy_after_dispatch)
    try:
        async with no_work(c, monkeypatch, proxy_dispatch=True):
            response = await proxy_test(c, key=key, version=version)
        rejected(response, 409, "DIAGNOSTIC_CONTEXT_CHANGED")
        assert "rejection" not in response.json()["detail"] and len(c.diag_proxy_dispatches) == 1
    finally:
        async with c.ctx.migration.begin() as db:
            await db.execute(text("UPDATE qr_channels SET allowed_senders=CAST(:senders AS jsonb) WHERE tenant_id=:t AND id=:c"),
                {"senders": json.dumps(c.row["allowed_senders"]), "t": c.tenant, "c": c.row["id"]})
    identity = c.diag_proxy_dispatches[0]["request_id"]
    saved = await request_record(c, {"request_id": identity})
    assert saved["status"] == "pending" and saved["effect_id"] is None
    async with no_work(c, monkeypatch, unchanged=True, receipts=True):
        recovered = await recover(c, {"idempotency_key": str(key)})
        replay = await proxy_test(c, key=key, version=version)
        assert recovered["request_id"] == identity and recovered["status"] == "pending"
        assert replay.status_code == 202 and replay.json()["request_id"] == identity
        assert replay.json()["replayed"] is True and "rejection" not in replay.json()
    assert len(c.diag_proxy_dispatches) == 1
