"""Actual member auth, PostgreSQL/RLS, broker fencing and durable device effects.

Only the external WhatsApp transport and paid ASR are replaced. The fake bridge
uses the real private HTTP broker, channel credentials, lease and SQL ledgers.
No real channel is connected and no provider request or message is sent.
"""
import asyncio
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
from uuid import UUID, uuid4

from cryptography.fernet import Fernet
from fastapi import HTTPException
import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app import qr_channels as channels, subscription_devices as devices
from app import subscription_device_audio
from app.database import tenant_transaction
from app.plan_routing import include_plan_router
from test_onboarding_completion_plan_postgres import (
    completion, disposable_environment, new_owner_http, onboarding_db, path, ready,
)  # noqa: F401 - real PostgreSQL + Redis/session fixtures

pytestmark = pytest.mark.integration


class Bridge:
    def __init__(self, case, private):
        self.case, self.private = case, private
        self.calls, self.results, self.connections = [], {}, {}
        self.owner = uuid4()
        self.lose_finish = False
        self.timeout_after_result = False

    def headers(self, row, *, live=False):
        result = {"X-Orion-Channel-Key": channels._cipher().decrypt(row["credential_encrypted"].encode()).decode()}
        if live:
            result.update({"X-Orion-Channel-Owner": str(self.owner),
                           "X-Orion-Channel-Fence": str(self.connections[str(row["id"])]["fence"])})
        return result

    async def transport(self, row, action, method, payload=None):
        self.calls.append((str(row["id"]), action, method))
        prefix = f"/internal/qr/channels/{row['id']}"
        if action.startswith("device/operations/"):
            key = action.rsplit("/", 1)[-1]
            return self.results.get(key, {"operation_id": key, "status": "uncertain", "recorded": False})
        if method == "GET":
            connection = self.connections.get(str(row["id"]))
            if not connection:
                raise HTTPException(503, "Synthetic unavailable transport")
            check = await self.private.post(prefix + "/lease/check", headers=self.headers(row),
                json={"owner": str(self.owner), "fence": connection["fence"]})
            assert check.status_code == 200, check.text
            snapshot = check.json()["device"]
            scope = {"device_id": snapshot["id"], "generation": snapshot["generation"],
                     "settings_revision": snapshot["settings_revision"]}
            if action == "device/qr":
                return {**scope, "image": "data:image/png;base64,iVBORw0KGgo=", "expires_in_ms": 30000}
            return {**scope, "status": "qr_ready", "phone": None, "phone_verified": False,
                    "applied_revision": snapshot["settings_revision"]}
        command = action.removeprefix("device/")
        request = {key: payload[key] for key in ("operation_id", "device_id", "generation", "settings_revision")}
        for _ in range(2):  # Actual broker permits may be rechecked before I/O.
            permit = await self.private.post(prefix + "/device/control/authorize", json=request, headers=self.headers(row))
            assert permit.status_code == 200 and permit.json()["authorized"], permit.text
            assert permit.json()["action"] == command and permit.json()["channel_id"] == str(row["id"])
        if command in {"connect", "restart", "relink"}:
            old = self.connections.get(str(row["id"]))
            if old and command != "connect":
                released = await self.private.post(prefix + "/lease/release", headers=self.headers(row),
                    json={"owner": str(self.owner), "fence": old["fence"]})
                assert released.status_code == 200, released.text
            lease = await self.private.post(prefix + "/lease/claim", headers=self.headers(row), json={"owner": str(self.owner)})
            assert lease.status_code == 200, lease.text
            self.connections[str(row["id"])] = lease.json()
            assert lease.json()["allowed_senders"] == [] and lease.json()["assistant_enabled"] is False
            assert lease.json()["device"]["capabilities"]["webhook"] is True
            assert lease.json()["device"]["capabilities"]["proxy"] is True
        if command == "remove":
            connection = self.connections.get(str(row["id"]))
            if connection:
                denied = await self.private.post(prefix + "/lease/check", headers=self.headers(row),
                    json={"owner": str(self.owner), "fence": connection["fence"]})
                assert denied.status_code == 409
            self.connections.pop(str(row["id"]), None)
        result = {**request, "action": command, "status": "succeeded"}
        self.results[request["operation_id"]] = result
        if not self.lose_finish:
            finished = await self.private.post(prefix + "/device/control/finish", headers=self.headers(row),
                                               json={**request, "status": "succeeded"})
            assert finished.status_code == 200, finished.text
        if self.timeout_after_result:
            raise HTTPException(503, "Synthetic lost reply")
        return {**result, **({"status": "pairing", "code": "ABCD-EFGH", "expires_in_ms": 90000} if command == "pair" else {})}


@pytest_asyncio.fixture
async def device_case(new_owner_http, monkeypatch):
    case = new_owner_http
    monkeypatch.setenv("QR_CHANNELS_ENABLED", "1")
    monkeypatch.setenv("INTEGRATION_FERNET_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("SUBSCRIPTION_DEVICE_ASR_DAILY_SECONDS", "21600")
    monkeypatch.setenv("SUBSCRIPTION_DEVICE_ASR_GLOBAL_DAILY_SECONDS", "86400")
    monkeypatch.setattr(subscription_device_audio, "audio_available", lambda: True)
    # The production app puts explicitly classified modules before the generic
    # catalog routes. Keep the same ordering in this real-auth fixture.
    generic = [route for route in case.app.router.routes if "{resource}" in getattr(route, "path", "")]
    case.app.router.routes[:] = [route for route in case.app.router.routes if route not in generic]
    include_plan_router(case.app, channels.router)
    include_plan_router(case.app, devices.router)
    case.app.router.routes.extend(generic)
    state = await ready(case, offering="both")
    done = await case.client.post(path(state) + "/complete", json=completion(state))
    assert done.status_code == 200, done.text
    case.tenant = UUID(done.json()["tenant_id"])
    case.prefix = f"/api/v1/tenants/{case.tenant}/subscription-whatsapp/devices"
    case.state = done.json()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=case.app), base_url="https://test") as private:
        case.private = private
        case.bridge = Bridge(case, private)
        monkeypatch.setattr(channels, "_bridge_control", case.bridge.transport)
        try:
            yield case
        finally:
            async with case.ctx.migration.begin() as db:
                for tenant in (case.tenant, case.ctx.a, case.ctx.b):
                    for table in ("subscription_device_cooldowns", "subscription_device_effects", "subscription_device_events",
                                  "subscription_device_operations", "subscription_devices", "subscription_device_audio_usage"):
                        await db.execute(text(f"DELETE FROM {table} WHERE tenant_id=:t"), {"t": tenant})
                    await db.execute(text("UPDATE tenants SET whatsapp_channel_id=NULL WHERE id=:t"), {"t": tenant})
                    await db.execute(text("DELETE FROM qr_channels WHERE tenant_id=:t"), {"t": tenant})


async def create(case, *, key=None, name="WhatsApp sintético"):
    response = await case.client.post(case.prefix, json={"name": name, "idempotency_key": str(key or uuid4())})
    assert response.status_code == 201, response.text
    case.device = response.json()["device"]
    async with case.ctx.migration.connect() as db:
        case.row = dict((await db.execute(text("SELECT * FROM qr_channels WHERE tenant_id=:t"), {"t": case.tenant})).mappings().one())
        case.saved = dict((await db.execute(text("SELECT * FROM subscription_devices WHERE tenant_id=:t AND removed_at IS NULL"),
                                         {"t": case.tenant})).mappings().one())
    return case.device


async def settings(case, edit):
    response = await case.client.get(case.prefix + f"/{case.device['id']}/settings")
    assert response.status_code == 200, response.text
    current = response.json()
    value = current["settings"]
    value["proxy"].pop("password_configured")
    edit(value)
    updated = await case.client.put(case.prefix + f"/{case.device['id']}/settings", json={
        "expected_revision": current["revision"], "settings": value})
    assert updated.status_code == 200, updated.text
    case.device["revision"] = updated.json()["revision"]
    return updated.json()


async def action(case, name, *, key=None, expected=None):
    response = await case.client.post(case.prefix + f"/{case.device['id']}/actions", json={
        "action": name, "expected_revision": expected or case.device["revision"], "idempotency_key": str(key or uuid4())})
    assert response.status_code == 202, response.text
    return response.json()


def scope(case, revision=None):
    return {"device_id": case.device["id"], "generation": str(case.saved["generation"]),
            "settings_revision": revision or case.device["revision"]}


async def internal(case, suffix, payload=None, *, method="POST", headers=None):
    return await case.private.request(method, f"/internal/qr/channels/{case.row['id']}/device" + suffix,
        headers=headers or case.bridge.headers(case.row, live=True), json=payload)


async def event(case, *, kind="text", event_id=None, phone="5567999999901", revision=None, **extra):
    payload = {**scope(case, revision), "event_id": event_id or str(uuid4()), "kind": kind,
               "occurred_at": datetime.now(timezone.utc).isoformat(), **({"phone": phone} if phone else {}), **extra}
    response = await internal(case, "/event", payload)
    assert response.status_code == 200, response.text
    return response.json()


async def permit(case, effect, *, revision=None):
    response = await internal(case, "/effects/authorize", {**scope(case, revision), "operation_id": effect["operation_id"]})
    assert response.status_code == 200, response.text
    return response.json()


async def finish(case, effect, state, *, revision=None, **extra):
    response = await internal(case, "/effects/finish", {**scope(case, revision), "operation_id": effect["operation_id"],
                                                        "state": state, **extra})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
async def test_real_owner_limit_and_idempotency_create_no_socket_plan_or_ai(device_case):
    c = device_case
    key = uuid4()
    requests = await asyncio.gather(*(c.client.post(c.prefix, json={"name": "Único", "idempotency_key": str(key)}) for _ in range(2)))
    assert sorted(r.status_code for r in requests) == [200, 201], [r.text for r in requests]
    assert requests[0].json()["device"]["id"] == requests[1].json()["device"]["id"]
    assert c.bridge.calls == []
    listed = await c.client.get(c.prefix)
    assert listed.status_code == 200 and listed.json()["usage"] == {"used": 1, "limit": 1}
    assert listed.json()["capabilities"]["manage"] and not listed.json()["can_create"]
    assert listed.json()["items"][0]["status"] == "disabled"
    assert not listed.json()["items"][0]["phone_verified"]
    denied = await c.client.post(c.prefix, json={"name": "Segundo", "idempotency_key": str(uuid4())})
    assert denied.status_code == 409 and denied.json()["detail"]["code"] == "DEVICE_LIMIT_REACHED"
    assert "credential" not in json.dumps(listed.json())
    async with c.ctx.migration.connect() as db:
        channel = (await db.execute(text("SELECT enabled,assistant_enabled,allowed_senders FROM qr_channels WHERE tenant_id=:t"), {"t": c.tenant})).mappings().one()
        assert channel == {"enabled": False, "assistant_enabled": False, "allowed_senders": []}
        assert (await db.execute(text("SELECT count(*) FROM tenant_plan_controls WHERE tenant_id=:t"), {"t": c.tenant})).scalar_one() == 0


@pytest.mark.asyncio
async def test_real_auth_csrf_membership_niche_and_rls_are_not_bypassed(device_case):
    c = device_case
    await create(c)
    no_csrf = await c.client.post(c.prefix, json={"name": "Blocked", "idempotency_key": str(uuid4())}, headers={"x-orion-csrf": ""})
    assert no_csrf.status_code == 403
    anonymous = await c.private.get(c.prefix)
    assert anonymous.status_code == 401
    foreign = await c.client.get(c.prefix.replace(str(c.tenant), str(c.ctx.a)))
    assert foreign.status_code == 403
    legacy = await c.client.get(f"/api/v1/tenants/{c.tenant}/qr-channels")
    assert legacy.status_code == 403
    async with c.ctx.runtime() as db:
        async with tenant_transaction(db, c.tenant):
            assert (await db.execute(text("SELECT id FROM subscription_devices"))).scalars().all() == [UUID(c.device["id"])]
    async with c.ctx.runtime() as db:
        async with tenant_transaction(db, c.ctx.a):
            assert (await db.execute(text("SELECT id FROM subscription_devices"))).scalars().all() == []
    async with c.ctx.runtime() as db:
        with pytest.raises(DBAPIError):
            async with tenant_transaction(db, c.tenant):
                await db.execute(text("SELECT proxy_secret_encrypted,settings FROM subscription_devices"))
    async with c.ctx.migration.begin() as db:
        await db.execute(text("UPDATE tenant_memberships SET active=false WHERE tenant_id=:t AND account_id=:a"),
                         {"t": c.tenant, "a": c.ctx.actor.account_id})
    revoked = await c.client.get(c.prefix)
    assert revoked.status_code == 403
    async with c.ctx.migration.begin() as db:
        await db.execute(text("UPDATE tenant_memberships SET active=true WHERE tenant_id=:t AND account_id=:a"),
                         {"t": c.tenant, "a": c.ctx.actor.account_id})
        await db.execute(text("UPDATE business_onboarding SET configuration=jsonb_set(configuration,'{niche}','\"barbearia\"'::jsonb) WHERE tenant_id=:t"), {"t": c.tenant})
    standard = await c.client.get(c.prefix)
    assert standard.status_code == 403


@pytest.mark.asyncio
async def test_settings_revision_proxy_secret_preservation_and_stale_permit(device_case):
    c = device_case
    await create(c)
    updated = await settings(c, lambda value: value["proxy"].update(enabled=True, url="http://proxy.example.test:8080",
        username="fixture", password="never-public-fixture"))
    assert updated["settings"]["proxy"]["password_configured"] and "never-public-fixture" not in json.dumps(updated)
    async with c.ctx.migration.connect() as db:
        encrypted = (await db.execute(text("SELECT proxy_secret_encrypted FROM subscription_devices WHERE id=:d"), {"d": UUID(c.device["id"])})).scalar_one()
        assert encrypted != "never-public-fixture"
    await settings(c, lambda value: value.update(name="Nome atualizado"))
    async with c.ctx.migration.connect() as db:
        assert (await db.execute(text("SELECT proxy_secret_encrypted FROM subscription_devices WHERE id=:d"), {"d": UUID(c.device["id"])})).scalar_one() == encrypted
    stale = await c.client.put(c.prefix + f"/{c.device['id']}/settings", json={"expected_revision": 1,
        "settings": devices.DeviceSettings().model_dump(mode="json")})
    assert stale.status_code == 409
    await settings(c, lambda value: value["proxy"].update(clear_password=True, enabled=False))
    assert not (await c.client.get(c.prefix + f"/{c.device['id']}/settings")).json()["settings"]["proxy"]["password_configured"]
    await action(c, "connect")
    old_revision = c.device["revision"]
    await settings(c, lambda value: value["calls"].update(reject_enabled=True))
    old = await internal(c, "/event", {**scope(c, old_revision), "event_id": "stale", "kind": "text",
        "occurred_at": datetime.now(timezone.utc).isoformat(), "phone": "5567999999901"})
    assert old.status_code == 409


@pytest.mark.asyncio
async def test_control_replay_pair_and_lost_finish_remove_reconcile_keep_history(device_case):
    c = device_case
    await create(c)
    original = (c.row["id"], c.saved["generation"], c.device["id"])
    key = uuid4()
    first = await action(c, "connect", key=key)
    assert first["operation"]["status"] == "succeeded"
    count = len(c.bridge.calls)
    again = await action(c, "connect", key=key)
    assert again["operation"] == first["operation"] and len(c.bridge.calls) == count
    qr = await c.client.get(c.prefix + f"/{c.device['id']}/qr")
    assert qr.status_code == 200 and qr.json()["image"].startswith("data:image/png;base64,")
    paired = await c.client.post(c.prefix + f"/{c.device['id']}/pair", json={"expected_revision": c.device["revision"], "phone": "5567999999901"})
    assert paired.status_code == 200 and paired.json()["code"] == "ABCD-EFGH", paired.text
    pair_record = next(value for value in c.bridge.results.values() if value["action"] == "pair")
    pair_read = await c.client.get(c.prefix + f"/{c.device['id']}/operations/{pair_record['operation_id']}")
    assert pair_read.status_code == 200 and "ABCD-EFGH" not in pair_read.text and "code" not in pair_read.json()["operation"]
    await action(c, "relink")
    async with c.ctx.migration.connect() as db:
        assert (await db.execute(text("SELECT generation FROM subscription_devices WHERE id=:d"), {"d": UUID(c.device["id"])})).scalar_one() == original[1]
    c.bridge.lose_finish = c.bridge.timeout_after_result = True
    removed = await action(c, "remove")
    assert removed["operation"]["status"] == "uncertain" and removed["device"] is not None
    assert removed["device"]["status"] == "removing" and not removed["device"]["capabilities"]["remove"]
    result = await c.client.get(c.prefix + f"/{c.device['id']}/operations/{removed['operation']['id']}")
    assert result.status_code == 200 and result.json()["operation"]["status"] == "succeeded" and result.json()["device"] is None
    assert (await c.client.get(c.prefix)).json()["usage"] == {"used": 0, "limit": 1}
    c.bridge.lose_finish = c.bridge.timeout_after_result = False
    await create(c, name="Nova identidade")
    assert c.row["id"] == original[0] and c.saved["generation"] != original[1] and c.device["id"] != original[2]
    assert c.device["status"] == "disabled" and not c.device["phone_verified"]


@pytest.mark.asyncio
async def test_daily_notice_reservation_is_atomic_and_test_phone_bypasses_only_cooldown(device_case):
    c = device_case
    await create(c)
    await settings(c, lambda value: value["business_hours"].update(enabled=True, outside_message="Fora do horário"))
    await action(c, "connect")
    first = await event(c, event_id="message-1", message_id="M1")
    assert len(first["effects"]) == 1
    duplicate = await event(c, event_id="message-1", message_id="M1")
    assert duplicate == {"effects": [], "duplicate": True}
    second = await event(c, message_id="M2")
    answers = await asyncio.gather(permit(c, first["effects"][0]), permit(c, second["effects"][0]))
    assert sum(answer["authorized"] for answer in answers) == 1
    await settings(c, lambda value: value.update(test_phone_e164="+5567999999901"))
    for message in ("M3", "M4"):
        current = await event(c, message_id=message)
        assert (await permit(c, current["effects"][0]))["authorized"]
    wrong = await internal(c, "/effects/authorize", {**scope(c), "operation_id": str(uuid4())})
    assert wrong.status_code == 404


@pytest.mark.asyncio
async def test_hidden_calls_and_unique_message_metrics_never_count_receipts_twice(device_case):
    c = device_case
    await create(c)
    await settings(c, lambda value: value["calls"].update(reject_enabled=True, reply_enabled=True))
    await action(c, "connect")
    result = await event(c, kind="call", phone=None, call_id="CALL-1", call_from="123456789012345@lid")
    assert [item["type"] for item in result["effects"]] == ["reject_call"]
    allowed = await permit(c, result["effects"][0])
    assert allowed["authorized"] and allowed["payload"]["phone"] is None
    known = await event(c, kind="call", call_id="CALL-2", call_from="5567999999901@s.whatsapp.net")
    reject, reply = known["effects"]
    assert not (await permit(c, reply))["authorized"]
    assert (await permit(c, reject))["authorized"]
    await finish(c, reject, "accepted")
    assert (await permit(c, reply))["authorized"]
    for kind, message, state in (("text", "IN1", None), ("outbound", "OUT1", "delivered"), ("outbound", "OUT1", "read")):
        await event(c, kind=kind, message_id=message, **({"state": state} if state else {}))
    listed = await c.client.get(c.prefix)
    assert listed.status_code == 200
    stats = listed.json()["items"][0]["metrics"]
    assert stats["total"] == stats["today"] == 2 and stats["scope"] == "orion_events"


@pytest.mark.asyncio
async def test_webhook_retry_is_durable_and_disables_only_after_third_failed_attempt(device_case):
    c = device_case
    await create(c)
    await settings(c, lambda value: value["webhook"].update(enabled=True, url="https://webhook.example.test/receive", disable_on_failure=True))
    await action(c, "connect")
    result = await event(c, message_id="WEB1", text="Evento sintético")
    effect = result["effects"][0]
    assert effect["type"] == "webhook"
    for attempt in range(1, 4):
        permission = await permit(c, effect)
        assert permission["authorized"] and permission["payload"]["body"]["data"]["text"] == "Evento sintético"
        await finish(c, effect, "failed", error_code="DEVICE_WEBHOOK_FAILED")
        async with c.ctx.migration.begin() as db:
            saved = (await db.execute(text("SELECT settings,revision FROM subscription_devices WHERE id=:d"), {"d": UUID(c.device["id"])})).mappings().one()
            assert saved["settings"]["webhook"]["enabled"] is (attempt < 3)
            if attempt < 3:
                await db.execute(text("UPDATE subscription_device_effects SET due_at=clock_timestamp()-interval '1 second' WHERE id=:id"), {"id": UUID(effect["operation_id"])})
    current = await c.client.get(c.prefix + f"/{c.device['id']}/settings")
    assert current.json()["revision"] == c.device["revision"] + 1
    assert not current.json()["settings"]["webhook"]["enabled"]


@pytest.mark.asyncio
async def test_stale_leases_and_niche_changes_block_real_broker_before_effects(device_case):
    c = device_case
    await create(c)
    await settings(c, lambda value: value["calls"].update(reject_enabled=True))
    await action(c, "connect")
    result = await event(c, kind="call", call_id="C1", call_from="5567999999901@s.whatsapp.net")
    old = c.bridge.headers(c.row, live=True)
    async with c.ctx.migration.begin() as db:
        await db.execute(text("UPDATE qr_channels SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=:c"), {"c": c.row["id"]})
    denied = await internal(c, "/effects/authorize", {**scope(c), "operation_id": result["effects"][0]["operation_id"]}, headers=old)
    assert denied.status_code == 409
    async with c.ctx.migration.begin() as db:
        await db.execute(text("UPDATE business_onboarding SET configuration=jsonb_set(configuration,'{niche}','\"outro\"'::jsonb) WHERE tenant_id=:t"), {"t": c.tenant})
    claim = await c.private.post(f"/internal/qr/channels/{c.row['id']}/lease/claim", headers=c.bridge.headers(c.row), json={"owner": str(uuid4())})
    assert claim.status_code == 403
    async with c.ctx.migration.connect() as db:
        assert (await db.execute(text("SELECT attempts FROM subscription_device_effects WHERE id=:o"), {"o": UUID(result["effects"][0]["operation_id"])})).scalar_one() == 0


@pytest.mark.asyncio
async def test_audio_quota_single_attempt_and_complete_ordered_parts_reconcile_without_resend(device_case, monkeypatch):
    c = device_case
    await create(c)
    await settings(c, lambda value: value["transcription"].update(enabled=True, max_minutes=30, reply_enabled=False, template="{transcript}"))
    await action(c, "connect")
    result = await event(c, kind="audio", message_id="AUDIO1", audio={"seconds": 1, "size": 10, "media_type": "audio/ogg"})
    effect = result["effects"][0]
    assert (await permit(c, effect))["authorized"]
    # An ASR request may spend 30s downloading plus 120s decoding/provider and
    # transport overhead. The outbox must not abandon its live attempt at150s.
    async with c.ctx.migration.begin() as db:
        await db.execute(text("UPDATE subscription_device_effects SET authorized_at=clock_timestamp()-interval '160 seconds' WHERE id=:o"), {"o": UUID(effect["operation_id"])})
    assert (await internal(c, "/effects/pending", method="GET")).status_code == 200
    async with c.ctx.migration.connect() as db:
        assert (await db.execute(text("SELECT status FROM subscription_device_effects WHERE id=:o"), {"o": UUID(effect["operation_id"])})).scalar_one() == "running"
    source = ("áudio completo " * 7000).strip()
    provider_calls = []
    async def synthetic_audio(request, *, max_minutes, authorize):
        assert max_minutes == 30
        await authorize(1799.1, phase="before_provider")
        provider_calls.append("single-attempt")
        await authorize(1799.1, phase="after_provider")
        return {"text": source, "duration_seconds": 1799.1}
    monkeypatch.setattr(subscription_device_audio, "transcribe_audio", synthetic_audio)
    headers = {**c.bridge.headers(c.row, live=True), "Content-Type": "audio/ogg",
        "X-Orion-Device-Id": c.device["id"], "X-Orion-Device-Generation": str(c.saved["generation"]),
        "X-Orion-Device-Revision": str(c.device["revision"]), "X-Orion-Device-Effect": effect["operation_id"]}
    endpoint = f"/internal/qr/channels/{c.row['id']}/device/audio"
    transcript = await c.private.post(endpoint, headers=headers, content=b"synthetic-decoder-isolated")
    assert transcript.status_code == 200 and transcript.json()["text"] == source and transcript.json()["truncated"] is False, transcript.text
    assert provider_calls == ["single-attempt"]
    duplicate = await c.private.post(endpoint, headers=headers, content=b"must-not-charge-again")
    assert duplicate.status_code == 409 and provider_calls == ["single-attempt"]
    async with c.ctx.migration.connect() as db:
        usage = (await db.execute(text("SELECT reserved_seconds,attempts FROM subscription_device_audio_usage WHERE tenant_id=:t"), {"t": c.tenant})).one()
        assert tuple(usage) == (1800, 1)
        rows = (await db.execute(text("SELECT id,previous_effect,payload FROM subscription_device_effects WHERE device_id=:d AND purpose='transcription_reply' ORDER BY effect_key"),
                                 {"d": UUID(c.device["id"])})).mappings().all()
    assert "".join(row["payload"]["text"] for row in rows) == source and len(rows) == 4
    first, second = ({"operation_id": str(row["id"]), "type": "send_text"} for row in rows[:2])
    assert not (await permit(c, second))["authorized"]
    assert (await permit(c, first))["authorized"]
    async with c.ctx.migration.begin() as db:
        await db.execute(text("UPDATE subscription_device_effects SET authorized_at=clock_timestamp()-interval '181 seconds' WHERE id=:o"), {"o": UUID(first["operation_id"])})
    pending = await internal(c, "/effects/pending", method="GET")
    assert pending.status_code == 200 and any(row["operation_id"] == first["operation_id"] for row in pending.json()["reconcile"])
    await finish(c, first, "accepted", reconcile=True, provider_id="SENT-ONCE")
    assert not (await permit(c, first))["authorized"]
    assert (await permit(c, second))["authorized"]


@pytest.mark.asyncio
async def test_audio_budget_exhaustion_never_calls_provider_or_releases_ambiguous_reservation(device_case, monkeypatch):
    c = device_case
    await create(c)
    await settings(c, lambda value: value["transcription"].update(enabled=True, max_minutes=30))
    await action(c, "connect")
    monkeypatch.setenv("SUBSCRIPTION_DEVICE_ASR_DAILY_SECONDS", "10")
    effect = (await event(c, kind="audio", message_id="A2", audio={"seconds": 1, "size": 8, "media_type": "audio/ogg"}))["effects"][0]
    assert (await permit(c, effect))["authorized"]
    called = []
    async def too_long(request, *, max_minutes, authorize):
        await authorize(10.1, phase="before_provider")
        called.append(True)
        pytest.fail("Quota must reject before paid provider")
    monkeypatch.setattr(subscription_device_audio, "transcribe_audio", too_long)
    headers = {**c.bridge.headers(c.row, live=True), "Content-Type": "audio/ogg", "X-Orion-Device-Id": c.device["id"],
        "X-Orion-Device-Generation": str(c.saved["generation"]), "X-Orion-Device-Revision": str(c.device["revision"]),
        "X-Orion-Device-Effect": effect["operation_id"]}
    response = await c.private.post(f"/internal/qr/channels/{c.row['id']}/device/audio", headers=headers, content=b"synthetic")
    assert response.status_code == 429 and called == []
    async with c.ctx.migration.connect() as db:
        assert (await db.execute(text("SELECT count(*) FROM subscription_device_audio_usage WHERE tenant_id=:t"), {"t": c.tenant})).scalar_one() == 0
