"""SQL admission contract used by the encrypted bridge ingress journal.

The HTTP broker, tenant authorization, lease and SQL transaction are real.
No WhatsApp connection, model call, media download or ASR provider is used.
"""
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from test_onboarding_completion_plan_postgres import (
    disposable_environment, new_owner_http, onboarding_db,
)  # noqa: F401 - loopback database/Redis and real owner authentication
from test_subscription_devices_postgres import (
    action, create, device_case, event, finish, internal, permit, settings,
)  # noqa: F401 - same production HTTP broker fixture

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_lost_admission_response_can_replay_one_text_id_without_duplicate_effects(device_case):
    c = device_case
    await create(c)
    await settings(c, lambda value: value["business_hours"].update(enabled=True, outside_message="Aviso sintético"))
    await action(c, "connect")
    # The first response is deliberately not used by an imaginary bridge.
    first = await event(c, event_id="msg:lost-text-admission", message_id="SOURCE-TEXT", text="Mensagem original")
    assert first["duplicate"] is False and len(first["effects"]) == 1
    recovered = await event(c, event_id="msg:lost-text-admission", message_id="SOURCE-TEXT", text="Mensagem original")
    assert recovered == {"effects": [], "duplicate": True}
    pending = await internal(c, "/effects/pending", method="GET")
    assert pending.status_code == 200, pending.text
    assert [item["operation_id"] for item in pending.json()["effects"]] == [first["effects"][0]["operation_id"]]
    async with c.ctx.migration.connect() as db:
        events = (await db.execute(text("SELECT count(*) FROM subscription_device_events WHERE device_id=:d AND provider_event_id=:id"),
            {"d": UUID(c.device["id"]), "id": "msg:lost-text-admission"})).scalar_one()
        effects = (await db.execute(text("SELECT count(*) FROM subscription_device_effects WHERE device_id=:d"),
            {"d": UUID(c.device["id"])})).scalar_one()
    assert events == effects == 1


@pytest.mark.asyncio
async def test_audio_duplicate_does_not_return_source_dependent_asr_so_bridge_must_retain_source(device_case):
    c = device_case
    await create(c)
    await settings(c, lambda value: value["transcription"].update(enabled=True, max_minutes=30, reply_enabled=False, template="{transcript}"))
    await action(c, "connect")
    source = dict(kind="audio", event_id="msg:lost-audio-admission", message_id="SOURCE-AUDIO",
                  audio={"seconds": 1, "size": 10, "media_type": "audio/ogg"})
    first = await event(c, **source)
    assert first["duplicate"] is False
    assert [effect["type"] for effect in first["effects"]] == ["transcribe"]
    assert await event(c, **source) == {"effects": [], "duplicate": True}
    pending = await internal(c, "/effects/pending", method="GET")
    assert pending.status_code == 200, pending.text
    assert not any(item["type"] == "transcribe" for item in pending.json()["effects"])
    async with c.ctx.migration.connect() as db:
        records = (await db.execute(text("SELECT type,status,attempts FROM subscription_device_effects WHERE device_id=:d"),
            {"d": UUID(c.device["id"])})).mappings().all()
    assert [dict(record) for record in records] == [{"type": "transcribe", "status": "pending", "attempts": 0}]


@pytest.mark.asyncio
async def test_explicit_relink_invalidates_old_pending_authority_once_without_losing_history(device_case):
    c = device_case
    await create(c)
    await settings(c, lambda value: value["business_hours"].update(enabled=True, outside_message="Aviso da identidade anterior"))
    await action(c, "connect")
    await event(c, kind="status", state="connected", phone="5567999999902")
    first = await event(c, event_id="old-account-event", message_id="OLD-ACCOUNT-MESSAGE")
    effect = first["effects"][0]
    generation, previous_revision = c.saved["generation"], c.device["revision"]
    key = uuid4()
    linked = await action(c, "relink", key=key)
    assert linked["device"]["revision"] == previous_revision + 1
    assert linked["device"]["phone"] is None and linked["device"]["phone_verified"] is False
    calls = len(c.bridge.calls)
    repeated = await action(c, "relink", key=key, expected=previous_revision)
    assert repeated["operation"] == linked["operation"]
    assert repeated["device"]["revision"] == previous_revision + 1 and len(c.bridge.calls) == calls
    await event(c, kind="status", state="connected", phone="5567999999904")
    pending = await internal(c, "/effects/pending", method="GET")
    assert pending.status_code == 200, pending.text
    assert effect["operation_id"] not in {item["operation_id"] for item in pending.json()["effects"]}
    assert (await permit(c, effect))["authorized"] is False
    async with c.ctx.migration.connect() as db:
        row = (await db.execute(text("SELECT generation,revision FROM subscription_devices WHERE id=:d"),
            {"d": UUID(c.device["id"])})).mappings().one()
        saved = (await db.execute(text("SELECT status,error_code,attempts FROM subscription_device_effects WHERE id=:e"),
            {"e": UUID(effect["operation_id"])})).mappings().one()
        original = (await db.execute(text("SELECT count(*) FROM subscription_device_events WHERE device_id=:d AND provider_event_id='old-account-event'"),
            {"d": UUID(c.device["id"])})).scalar_one()
    assert row["generation"] == generation and row["revision"] == previous_revision + 1
    assert dict(saved) == {"status": "not_sent", "error_code": "device_relinked", "attempts": 0}
    assert original == 1


@pytest.mark.asyncio
async def test_normal_restart_keeps_the_account_revision_and_valid_pending_effect(device_case):
    c = device_case
    await create(c)
    await settings(c, lambda value: value["business_hours"].update(enabled=True, outside_message="Aviso ainda válido"))
    await action(c, "connect")
    first = await event(c, event_id="same-account-event", message_id="SAME-ACCOUNT-MESSAGE")
    previous_revision = c.device["revision"]
    await action(c, "restart")
    assert c.device["revision"] == previous_revision
    assert (await permit(c, first["effects"][0]))["authorized"] is True


@pytest.mark.asyncio
async def test_relink_keeps_evidence_of_an_already_attempted_failed_webhook(device_case):
    c = device_case
    await create(c)
    await settings(c, lambda value: value["webhook"].update(enabled=True, url="https://webhook.example.test/receive"))
    await action(c, "connect")
    result = await event(c, event_id="attempted-webhook-event", message_id="ATTEMPTED-WEBHOOK", text="Evento sintético")
    effect = result["effects"][0]
    assert effect["type"] == "webhook" and (await permit(c, effect))["authorized"] is True
    await finish(c, effect, "failed", error_code="DEVICE_WEBHOOK_FAILED")
    await action(c, "relink")
    async with c.ctx.migration.connect() as db:
        saved = (await db.execute(text("SELECT status,error_code,attempts FROM subscription_device_effects WHERE id=:e"),
            {"e": UUID(effect["operation_id"])})).mappings().one()
    assert dict(saved) == {"status": "failed", "error_code": "DEVICE_WEBHOOK_FAILED", "attempts": 1}
    pending = await internal(c, "/effects/pending", method="GET")
    assert pending.status_code == 200, pending.text
    assert effect["operation_id"] not in {item["operation_id"] for item in pending.json()["effects"]}
    assert (await permit(c, effect))["authorized"] is False
