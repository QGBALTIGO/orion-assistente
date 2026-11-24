"""Real tenant contact preferences at ingress and immediately before effects.

The WhatsApp session and speech provider are synthetic. These tests never send
messages; they exercise the authenticated broker's actual PostgreSQL decisions.
"""
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text

from app import subscription_device_audio
from test_subscription_chatbot_postgres import (
    chatbot_case, device_case, disposable_environment, new_owner_http, onboarding_db,
    rule_create, definition, input_event, chat_effects, grant,
)
from test_subscription_devices_postgres import event, settings, permit, internal, scope

pytestmark = pytest.mark.integration
PHONE = "5567999999901"


@pytest_asyncio.fixture
async def preferences_case(chatbot_case):
    c = chatbot_case
    yield c
    async with c.ctx.migration.begin() as db:
        await db.execute(text("DELETE FROM subscription_audience_contacts WHERE tenant_id IN (:t,:a,:b)"),
                         {"t": c.tenant, "a": c.ctx.a, "b": c.ctx.b})


async def contact(c, *, phone=PHONE, chatbot=True, audio=True, tenant=None, removed=False):
    async with c.ctx.migration.begin() as db:
        await db.execute(text("""INSERT INTO subscription_audience_contacts
            (tenant_id,phone,name,chatbot_enabled,audio_transcription_enabled,plan_name,
             service_username,service_password_encrypted,removed_at)
            VALUES(:t,:phone,'Contato privado',:chatbot,:audio,'Plano privado','usuario-privado',
                   'encrypted-private-placeholder',CASE WHEN :removed THEN clock_timestamp() ELSE NULL END)
            ON CONFLICT(tenant_id,phone) DO UPDATE SET chatbot_enabled=EXCLUDED.chatbot_enabled,
                audio_transcription_enabled=EXCLUDED.audio_transcription_enabled,removed_at=EXCLUDED.removed_at"""),
            {"t": tenant or c.tenant, "phone": phone, "chatbot": chatbot, "audio": audio, "removed": removed})


async def audio_event(c):
    result = await input_event(c, "", kind="audio", message_id="AUDIO_" + uuid4().hex,
                              audio={"seconds": 1, "size": 10, "media_type": "audio/ogg"})
    return next(effect for effect in result["effects"] if effect["type"] == "transcribe")


async def audio_request(c, effect):
    headers = {**c.bridge.headers(c.row, live=True), "Content-Type": "audio/ogg",
        "X-Orion-Device-Id": c.device["id"], "X-Orion-Device-Generation": str(c.saved["generation"]),
        "X-Orion-Device-Revision": str(c.device["revision"]), "X-Orion-Device-Effect": effect["operation_id"]}
    return await c.private.post(f"/internal/qr/channels/{c.row['id']}/device/audio", headers=headers,
                                content=b"synthetic-audio-no-external-provider")


async def effect_count(c, purpose):
    async with c.ctx.migration.connect() as db:
        return (await db.execute(text("SELECT count(*) FROM subscription_device_effects WHERE tenant_id=:t AND purpose=:p"),
                                 {"t": c.tenant, "p": purpose})).scalar_one()


async def test_disabled_contact_suppresses_automation_but_preserves_call_rejection_and_webhook(preferences_case):
    c = preferences_case
    def configure(value):
        value["business_hours"].update(enabled=True, outside_message="Fora do horário")
        for day in value["business_hours"]["days"]:
            day["enabled"] = False
        value["calls"].update(reject_enabled=True, reply_enabled=True, reply_message="Não atendemos ligações")
        value["transcription"].update(enabled=True, reply_enabled=True)
        value["webhook"].update(enabled=True, url="https://webhook.example.test/receive")
    await settings(c, configure)
    await rule_create(c)
    await contact(c, chatbot=False, audio=False)
    message = await input_event(c)
    assert [effect["type"] for effect in message["effects"]] == ["webhook"]
    call = await event(c, kind="call", call_id="CALL_AUDIENCE", call_from=PHONE + "@s.whatsapp.net")
    assert {effect["type"] for effect in call["effects"]} == {"reject_call", "webhook"}
    audio = await input_event(c, "", kind="audio", message_id="AUDIO_DISABLED",
                              audio={"seconds": 1, "size": 10, "media_type": "audio/ogg"})
    assert [effect["type"] for effect in audio["effects"]] == ["webhook"]
    response = await permit(c, message["effects"][0])
    assert response["authorized"]
    body = response["payload"]["body"]["data"]
    assert body["text"] == "olá" and body["name"] == "Pessoa sintética"
    assert not {"plan_name", "service_username", "service_password_encrypted", "chatbot_enabled"} & body.keys()
    assert "privado" not in str(body)
    assert await effect_count(c, "absence") == await effect_count(c, "call_reply") == 0


async def test_contact_switches_are_independent_and_foreign_tenant_cannot_disable_unknown_contact(preferences_case):
    c = preferences_case
    await settings(c, lambda value: value["transcription"].update(enabled=True, reply_enabled=True))
    await rule_create(c)
    await contact(c, chatbot=False, audio=True)
    assert chat_effects(await input_event(c)) == []
    assert (await audio_event(c))["type"] == "transcribe"
    await contact(c, chatbot=True, audio=False)
    assert len(chat_effects(await input_event(c))) == 1
    audio = await input_event(c, "", kind="audio", message_id="AUDIO_NO_TRANSCRIPTION",
                              audio={"seconds": 1, "size": 10, "media_type": "audio/ogg"})
    assert audio["effects"] == []
    other = "5567999999902"
    await contact(c, phone=other, chatbot=False, audio=False, tenant=c.ctx.b)
    assert len(chat_effects(await input_event(c, phone=other))) == 1
    await contact(c, removed=True)
    assert chat_effects(await input_event(c)) == []


@pytest.mark.parametrize("recheck", [False, True])
async def test_contact_change_revokes_chatbot_before_initial_or_final_permit(preferences_case, recheck):
    c = preferences_case
    await rule_create(c)
    effect, = chat_effects(await input_event(c))
    if recheck:
        assert (await grant(c, effect))["authorized"]
    await contact(c, chatbot=False)
    assert (await grant(c, effect, recheck=recheck))["authorized"] is False
    async with c.ctx.migration.connect() as db:
        row = (await db.execute(text("SELECT status,attempts FROM subscription_device_effects WHERE id=:id"),
                               {"id": UUID(effect["operation_id"])})).mappings().one()
    assert row["status"] == ("running" if recheck else "not_sent")
    assert row["attempts"] == (1 if recheck else 0)


async def test_disabled_explicit_test_returns_conflict_without_persisting_job_or_event(preferences_case):
    c = preferences_case
    row = await rule_create(c, definition(enabled=False))
    await contact(c, chatbot=False)
    async def counts():
        async with c.ctx.migration.connect() as db:
            return [(await db.execute(text(f"SELECT count(*) FROM {table} WHERE tenant_id=:t"),
                                      {"t": c.tenant})).scalar_one()
                    for table in ("subscription_device_events", "subscription_device_effects", "subscription_chatbot_runs", "subscription_chatbot_jobs")]
    before = await counts()
    response = await c.client.post(c.chat + f"/rules/{row['id']}/test", json={
        "expected_chatbot_revision": c.chat_revision, "expected_revision": row["revision"],
        "idempotency_key": str(uuid4()), "device_id": c.device["id"], "phone": "+" + PHONE})
    assert response.status_code == 409 and response.json()["detail"]["code"] == "CHATBOT_CONTACT_DISABLED", response.text
    assert await counts() == before


async def test_notice_is_revoked_by_contact_preference_before_final_send(preferences_case):
    c = preferences_case
    def configure(value):
        value["business_hours"].update(enabled=True, outside_message="Fora do horário")
        for day in value["business_hours"]["days"]:
            day["enabled"] = False
    await settings(c, configure)
    effect, = (await event(c, text="Olá"))["effects"]
    assert (await permit(c, effect))["authorized"]
    await contact(c, chatbot=False)
    response = await internal(c, "/effects/authorize", {**scope(c), "operation_id": effect["operation_id"], "recheck": True})
    assert response.status_code == 200 and response.json()["authorized"] is False, response.text


@pytest.mark.parametrize("withdraw_at", ["before_upload", "before_provider", "after_provider", "after_return"])
async def test_audio_preference_is_rechecked_at_every_provider_boundary(preferences_case, monkeypatch, withdraw_at):
    c = preferences_case
    await settings(c, lambda value: value["transcription"].update(enabled=True, reply_enabled=True))
    await rule_create(c)
    effect = await audio_event(c)
    assert (await permit(c, effect))["authorized"]
    entries, provider_calls = [], []
    async def transcribe(request, *, max_minutes, authorize):
        entries.append("entered")
        if withdraw_at == "before_provider":
            await contact(c, audio=False)
        await authorize(1, phase="before_provider")
        provider_calls.append("synthetic")
        if withdraw_at == "after_provider":
            await contact(c, audio=False)
        await authorize(1, phase="after_provider")
        if withdraw_at == "after_return":
            await contact(c, audio=False)
        return {"text": "olá", "duration_seconds": 1}
    monkeypatch.setattr(subscription_device_audio, "transcribe_audio", transcribe)
    if withdraw_at == "before_upload":
        await contact(c, audio=False)
    response = await audio_request(c, effect)
    assert response.status_code == 409, response.text
    assert entries == ([] if withdraw_at == "before_upload" else ["entered"])
    assert provider_calls == (["synthetic"] if withdraw_at in {"after_provider", "after_return"} else [])
    assert await effect_count(c, "transcription_reply") == await effect_count(c, "chatbot_reply") == 0
    assert "olá" not in response.text


async def test_completed_transcription_reply_rechecks_audio_preference(preferences_case, monkeypatch):
    c = preferences_case
    await settings(c, lambda value: value["transcription"].update(enabled=True, reply_enabled=True))
    await rule_create(c)
    effect = await audio_event(c)
    assert (await permit(c, effect))["authorized"]
    async def transcribe(request, *, max_minutes, authorize):
        await authorize(1, phase="before_provider")
        await authorize(1, phase="after_provider")
        return {"text": "olá", "duration_seconds": 1}
    monkeypatch.setattr(subscription_device_audio, "transcribe_audio", transcribe)
    response = await audio_request(c, effect)
    assert response.status_code == 200, response.text
    reply = next(row for row in response.json()["effects"] if row["type"] == "send_text")
    assert (await permit(c, reply))["authorized"]
    await contact(c, audio=False)
    final = await internal(c, "/effects/authorize", {**scope(c), "operation_id": reply["operation_id"], "recheck": True})
    assert final.status_code == 200 and final.json()["authorized"] is False, final.text

