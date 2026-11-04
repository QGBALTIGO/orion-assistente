"""Member HTTP + role-separated PostgreSQL, with no external sends/providers."""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import catalog_api, subscription_chatbot as api, subscription_chatbot_broker as broker
from app import subscription_chatbot_store as store, subscription_device_audio
from app.database import tenant_transaction
from app.plan_routing import include_plan_router
from test_subscription_devices_postgres import (
    action, create, device_case, disposable_environment, event, internal,
    new_owner_http, onboarding_db, scope, settings,
)  # noqa: F401 - actual auth/DB/Redis/device fixtures, only transport is fake.

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def chatbot_case(device_case):
    case = device_case
    generic = [route for route in case.app.router.routes if "{resource}" in getattr(route, "path", "")]
    case.app.router.routes[:] = [route for route in case.app.router.routes if route not in generic]
    include_plan_router(case.app, api.router)
    include_plan_router(case.app, broker.router)
    case.app.router.routes.extend(generic)
    case.chat = f"/api/v1/tenants/{case.tenant}/subscription-whatsapp/chatbot"
    case.chat_revision = 1
    await create(case)
    await action(case, "connect")
    await event(case, kind="status", state="connected", name="Dispositivo sintético")
    case.ingress = 100
    try:
        yield case
    finally:
        async with case.ctx.migration.begin() as db:
            for tenant in (case.tenant, case.ctx.a, case.ctx.b):
                await db.execute(text("UPDATE subscription_chatbot_previews SET applied_job_id=NULL WHERE tenant_id=:t"), {"t": tenant})
                await db.execute(text("UPDATE subscription_chatbot_jobs SET preview_id=NULL,safety_backup_id=NULL WHERE tenant_id=:t"), {"t": tenant})
                for table in ("subscription_chatbot_selections", "subscription_chatbot_cooldowns"):
                    await db.execute(text(f"DELETE FROM {table} WHERE tenant_id=:t"), {"t": tenant})
                # Existing Device events/effects are retained until the parent
                # fixture cleans them; remove only the new FK dependants first.
                await db.execute(text("DELETE FROM subscription_device_effects WHERE tenant_id=:t AND type='chatbot_action'"), {"t": tenant})
                for table in ("subscription_chatbot_runs", "subscription_chatbot_previews", "subscription_chatbot_jobs",
                              "subscription_chatbot_backups", "subscription_chatbot_rules", "subscription_chatbot_templates",
                              "subscription_chatbot_assets", "subscription_chatbot_state"):
                    await db.execute(text(f"DELETE FROM {table} WHERE tenant_id=:t"), {"t": tenant})


def definition(keyword="Olá", *, value="Olá, {name}!", enabled=True, device_id=None, response=None, mode="equal"):
    return {"device_id": device_id, "enabled": enabled, "match_mode": mode, "keyword": keyword,
            "response": response or {"kind": "sequence", "actions": [{"id": str(uuid4()), "type": "text", "text": value, "delay_seconds": 1}]}}


async def rule_create(c, value=None, *, key=None):
    response = await c.client.post(c.chat + "/rules", json={"expected_chatbot_revision": c.chat_revision,
        "idempotency_key": str(key or uuid4()), "rule": value or definition()})
    assert response.status_code == 201, response.text
    c.chat_revision = response.json()["chatbot_revision"]
    return response.json()["rule"]


async def edit(c, row, value, *, key=None, expected=None):
    response = await c.client.put(c.chat + "/rules/" + row["id"], json={
        "expected_chatbot_revision": c.chat_revision if expected is None else expected,
        "expected_revision": row["revision"], "idempotency_key": str(key or uuid4()), "rule": value})
    if response.status_code == 200:
        c.chat_revision = response.json()["chatbot_revision"]
    return response


async def input_event(c, value="olá", **extra):
    c.ingress += 1
    return await event(c, text=value, name="Pessoa sintética", chatbot_revision=c.chat_revision,
                       ingress_order=c.ingress, **extra)


async def fast_clock(c):
    # Move only synthetic ledger eligibility back; never sleep or bypass guards.
    async with c.ctx.migration.begin() as db:
        await db.execute(text("""UPDATE subscription_device_effects SET due_at=clock_timestamp()-interval '40 seconds',
            finished_at=CASE WHEN status='accepted' THEN clock_timestamp()-interval '40 seconds' ELSE finished_at END
            WHERE tenant_id=:t"""), {"t": c.tenant})


async def grant(c, effect, *, recheck=False):
    await fast_clock(c)
    response = await internal(c, "/effects/authorize", {**scope(c), "operation_id": effect["operation_id"],
        "chatbot_revision": effect.get("chatbot_revision", c.chat_revision), "recheck": recheck})
    assert response.status_code == 200, response.text
    return response.json()


async def accepted(c, effect, *, provider=None, reconcile=False):
    response = await internal(c, "/effects/finish", {**scope(c), "operation_id": effect["operation_id"],
        "chatbot_revision": effect.get("chatbot_revision", c.chat_revision), "state": "accepted",
        "provider_id": provider or "SYNTHETIC_" + uuid4().hex, "reconcile": reconcile})
    assert response.status_code == 200, response.text
    return response.json()


async def pending(c):
    await fast_clock(c)
    result = await internal(c, "/effects/pending", method="GET")
    assert result.status_code == 200, result.text
    return result.json()


def chat_effects(result):
    return [effect for effect in result["effects"] if effect["type"] == "chatbot_action"]


@pytest.mark.asyncio
async def test_crud_idempotency_newest_creation_order_filters_and_inactive_copy(chatbot_case):
    c = chatbot_case
    first = await rule_create(c, definition(value="Primeira"))
    newer = await rule_create(c, definition(value="Mais recente"))
    old_version = c.chat_revision
    key = uuid4()
    saved = await edit(c, first, definition(value="Editada, mas não recriada"), key=key)
    assert saved.status_code == 200, saved.text
    replay = await edit(c, first, definition(value="Editada, mas não recriada"), key=key, expected=old_version)
    assert replay.status_code == 200 and replay.json() == saved.json()
    assert saved.json()["rule"]["creation_order"] < newer["creation_order"]
    listing = await c.client.get(c.chat, params={"q": "recente", "search_in": "response", "status": "active"})
    assert listing.status_code == 200 and listing.json()["filtered_total"] == 1, listing.text
    assert listing.json()["counts"] == {"total": 2, "active": 2, "inactive": 0}
    assert listing.json()["pending_job"] is None
    events = await input_event(c, "  OLÁ ")
    effect, = chat_effects(events)
    permit = await grant(c, effect)
    assert permit["authorized"] and permit["rule_id"] == newer["id"]
    assert permit["payload"]["action"] == {"type": "text", "text": "Mais recente"}
    repeat = await grant(c, effect, recheck=True)
    assert repeat["authorized"]
    async with c.ctx.migration.connect() as db:
        assert (await db.execute(text("SELECT attempts FROM subscription_device_effects WHERE id=:id"),
                                {"id": UUID(effect["operation_id"])})).scalar_one() == 1
    clone = await c.client.post(c.chat + "/rules/" + newer["id"] + "/duplicate", json={
        "expected_chatbot_revision": c.chat_revision, "expected_revision": newer["revision"], "idempotency_key": str(uuid4())})
    assert clone.status_code == 200, clone.text
    assert clone.json()["rule"]["enabled"] is False
    assert clone.json()["rule"]["creation_order"] > newer["creation_order"]
    revoked = await grant(c, effect, recheck=True)
    assert revoked["authorized"] is False


@pytest.mark.asyncio
async def test_real_auth_csrf_rls_and_cross_tenant_are_enforced(chatbot_case):
    c = chatbot_case
    row = await rule_create(c)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=c.app), base_url="https://test") as unsigned:
        denied = await unsigned.get(c.chat)
        assert denied.status_code == 401
    missing_csrf = await c.client.post(c.chat + "/rules", headers={"x-orion-csrf": ""}, json={})
    assert missing_csrf.status_code == 403
    foreign = await c.client.get(c.chat.replace(str(c.tenant), str(c.ctx.b)) + "/rules/" + row["id"])
    assert foreign.status_code in {403, 404}
    async with c.ctx.runtime() as db:
        async with tenant_transaction(db, c.tenant):
            visible = (await db.execute(text("SELECT id FROM subscription_chatbot_rules WHERE id=:id"),
                                       {"id": UUID(row["id"])})).scalar_one()
            assert str(visible) == row["id"]
        with pytest.raises(DBAPIError):
            async with tenant_transaction(db, c.tenant):
                await db.execute(text("SELECT response FROM subscription_chatbot_rules"))
        async with tenant_transaction(db, c.ctx.b):
            assert (await db.execute(text("SELECT id FROM subscription_chatbot_rules WHERE id=:id"),
                                    {"id": UUID(row["id"])})).scalar_one_or_none() is None
        with pytest.raises(DBAPIError):
            async with tenant_transaction(db, c.tenant):
                await db.execute(text("UPDATE subscription_chatbot_rules SET enabled=false"))


@pytest.mark.asyncio
async def test_idempotent_event_wildcard_window_and_owner_command_excludes_bot_echo(chatbot_case):
    c = chatbot_case
    wildcard = await rule_create(c, definition(keyword="*", value="Boas-vindas"))
    key = str(uuid4())
    first = await input_event(c, "sem palavra", event_id=key)
    assert len(chat_effects(first)) == 1
    duplicate = await input_event(c, "sem palavra", event_id=key)
    assert duplicate == {"effects": [], "duplicate": True}
    assert chat_effects(await input_event(c, "outra entrada")) == []
    assert len(chat_effects(await input_event(c, "outra pessoa", phone="5567999999902"))) == 1
    specific = await rule_create(c, definition(keyword="pessoal", value="Comando"))
    owner = await input_event(c, "!pessoal", origin="owner_command", message_id="HUMAN_COMMAND")
    effect, = chat_effects(owner)
    assert (await grant(c, effect))["rule_id"] == specific["id"]
    await accepted(c, effect, provider="BOT_OWN_MESSAGE")
    echo = await input_event(c, "!pessoal", origin="owner_command", message_id="BOT_OWN_MESSAGE")
    assert echo["effects"] == []
    async with c.ctx.migration.connect() as db:
        assert (await db.execute(text("SELECT count(*) FROM subscription_chatbot_cooldowns WHERE tenant_id=:t AND rule_id=:r"),
            {"t": c.tenant, "r": UUID(wildcard["id"])})).scalar_one() == 2


@pytest.mark.asyncio
async def test_list_selection_is_bound_to_verified_phone_original_message_and_revision(chatbot_case):
    c = chatbot_case
    menu = {"kind": "list", "title": "Opções", "description": "Escolha", "footer": "", "button_text": "Abrir",
            "sections": [{"id": str(uuid4()), "title": "Seção", "rows": [{"id": str(uuid4()), "title": "Informações",
            "description": "", "action": {"id": str(uuid4()), "type": "text", "text": "Detalhes", "delay_seconds": 1}}]}]}
    row = await rule_create(c, definition(keyword="menu", response=menu))
    effect, = chat_effects(await input_event(c, "menu"))
    sent = await grant(c, effect)
    token = sent["payload"]["action"]["sections"][0]["rows"][0]["id"]
    assert token.startswith("ocb:") and "action" not in sent["payload"]["action"]["sections"][0]["rows"][0]
    await accepted(c, effect, provider="MENU_STANZA")
    wrong = await input_event(c, "", selection_id=token, quoted_message_id="WRONG_STANZA")
    assert chat_effects(wrong) == []
    foreign = await input_event(c, "", selection_id=token, quoted_message_id="MENU_STANZA", phone="5567999999902")
    assert chat_effects(foreign) == []
    selected = await input_event(c, "", selection_id=token, quoted_message_id="MENU_STANZA")
    child, = chat_effects(selected)
    assert (await grant(c, child))["payload"]["action"]["text"] == "Detalhes"
    replay = await input_event(c, "", selection_id=token, quoted_message_id="MENU_STANZA")
    assert chat_effects(replay) == []
    disabled = await edit(c, row, definition(keyword="menu", response=menu, enabled=False))
    assert disabled.status_code == 200
    assert not (await grant(c, child, recheck=True))["authorized"]


@pytest.mark.asyncio
async def test_native_sequence_waits_for_accepted_predecessor_and_can_reconcile_without_resend(chatbot_case):
    c = chatbot_case
    actions = [{"id": str(uuid4()), "type": "text", "text": value, "delay_seconds": 1} for value in ("Um", "Dois")]
    await rule_create(c, definition(response={"kind": "sequence", "actions": actions}))
    one, = chat_effects(await input_event(c))
    assert (await grant(c, one))["authorized"]
    async with c.ctx.migration.begin() as db:
        await db.execute(text("UPDATE subscription_device_effects SET authorized_at=clock_timestamp()-interval '181 seconds' WHERE id=:id"),
                         {"id": UUID(one["operation_id"])})
    state = await pending(c)
    assert chat_effects(state) == []
    assert state["reconcile"][0]["chatbot_revision"] == c.chat_revision
    assert not (await grant(c, one))["authorized"]
    finished = await accepted(c, one, provider="ACK_AFTER_TIMEOUT", reconcile=True)
    two, = chat_effects(finished)
    assert (await grant(c, two))["payload"]["action"]["text"] == "Dois"
    await accepted(c, two)


@pytest.mark.asyncio
async def test_http_receipt_derives_reply_once_and_never_trusts_free_text_destination(chatbot_case):
    c = chatbot_case
    http = {"id": str(uuid4()), "type": "url", "url": "https://example.com/synthetic",
            "method": "POST", "custom_template": "Olá, {{person}}: {{value}}", "as_file": False, "delay_seconds": 1}
    await rule_create(c, definition(response={"kind": "sequence", "actions": [http,
        {"id": str(uuid4()), "type": "text", "text": "Fim", "delay_seconds": 1}]}))
    effect, = chat_effects(await input_event(c))
    permit = await grant(c, effect)
    assert permit["payload"]["action"]["type"] == "http"
    assert permit["payload"]["action"]["data"]["senderPhone"] == "5567999999901"
    assert permit["payload"]["action"]["data"]["deviceName"] == "WhatsApp sintético"
    bad = await internal(c, "/effects/finish", {**scope(c), "operation_id": effect["operation_id"],
        "chatbot_revision": c.chat_revision, "state": "accepted"})
    assert bad.status_code == 409
    body = {**scope(c), "operation_id": effect["operation_id"], "chatbot_revision": c.chat_revision,
            "response_json": {"person": "Pessoa", "value": 7, "to": "5511000000000"}}
    result = await internal(c, "/chatbot/http-result", body)
    assert result.status_code == 200 and result.json()["recorded"] is True, result.text
    child, = chat_effects(result.json())
    derived = await grant(c, child)
    assert derived["payload"]["phone"] == "5567999999901"
    assert derived["payload"]["action"]["text"] == "Olá, Pessoa: 7"
    replay = await internal(c, "/chatbot/http-result", {**body, "reconcile": True})
    assert replay.status_code == 200 and replay.json()["recorded"] is True, replay.text
    changed = await internal(c, "/chatbot/http-result", {**body, "reconcile": True, "response_json": {"reply": "changed"}})
    assert changed.status_code == 409
    continuation = await accepted(c, child)
    last, = chat_effects(continuation)
    assert (await grant(c, last))["payload"]["action"]["text"] == "Fim"
    async with c.ctx.migration.connect() as db:
        assert (await db.execute(text("SELECT count(*) FROM subscription_device_effects WHERE tenant_id=:t AND type='chatbot_action'"),
                                {"t": c.tenant})).scalar_one() == 3


@pytest.mark.asyncio
async def test_old_http_receipt_is_persisted_but_cannot_send_after_rule_edit(chatbot_case):
    c = chatbot_case
    http = {"id": str(uuid4()), "type": "url", "url": "https://example.com/synthetic",
            "method": "POST", "custom_template": "", "as_file": False, "delay_seconds": 1}
    row = await rule_create(c, definition(response={"kind": "sequence", "actions": [http]}))
    effect, = chat_effects(await input_event(c))
    assert (await grant(c, effect))["authorized"]
    old = c.chat_revision
    assert (await edit(c, row, definition(value="Nova"))).status_code == 200
    result = await internal(c, "/chatbot/http-result", {**scope(c), "operation_id": effect["operation_id"],
        "chatbot_revision": old, "response_json": {"reply": "Antiga"}, "reconcile": True})
    assert result.status_code == 200 and result.json() == {"recorded": True, "operation_id": effect["operation_id"], "effects": []}, result.text
    async with c.ctx.migration.connect() as db:
        row = (await db.execute(text("SELECT status,result FROM subscription_device_effects WHERE id=:id"),
                               {"id": UUID(effect["operation_id"])})).mappings().one()
        assert row["status"] == "accepted" and row["result"]["delivery_suppressed"] is True
        assert row["result"]["response_json"] == {"reply": "Antiga"}


@pytest.mark.asyncio
async def test_next_contact_input_depends_on_actual_http_sequence_tail_not_new_child_timestamp(chatbot_case):
    c = chatbot_case
    http = {"id": str(uuid4()), "type": "url", "url": "https://example.com/synthetic",
            "method": "GET", "custom_template": "", "as_file": False, "delay_seconds": 1}
    await rule_create(c, definition(response={"kind": "sequence", "actions": [http,
        {"id": str(uuid4()), "type": "text", "text": "Cauda original", "delay_seconds": 1}]}))
    first, = chat_effects(await input_event(c))
    assert (await grant(c, first))["authorized"]
    complete = await internal(c, "/chatbot/http-result", {**scope(c), "operation_id": first["operation_id"],
        "chatbot_revision": c.chat_revision, "response_json": {"reply": "Filho inserido depois da cauda"}})
    assert complete.status_code == 200, complete.text
    child, = chat_effects(complete.json())
    newer = await input_event(c)
    assert chat_effects(newer) == []
    async with c.ctx.migration.connect() as db:
        effects = (await db.execute(text("""SELECT e.id,e.previous_effect,e.payload,r.ingress_order
            FROM subscription_device_effects e JOIN subscription_chatbot_runs r ON r.id=e.chatbot_run_id
            WHERE e.tenant_id=:t ORDER BY r.ingress_order,e.created_at"""), {"t": c.tenant})).mappings().all()
    first_order = min(row["ingress_order"] for row in effects)
    old_tail, = [row for row in effects if row["ingress_order"] == first_order
                 and row["payload"]["action"].get("text") == "Cauda original"]
    next_head, = [row for row in effects if row["ingress_order"] > first_order and row["payload"]["action"]["type"] == "http"]
    assert next_head["previous_effect"] == old_tail["id"]
    assert next_head["previous_effect"] != UUID(child["operation_id"])
    assert (await grant(c, child))["authorized"]
    tail, = chat_effects(await accepted(c, child))
    assert (await grant(c, tail))["payload"]["action"]["text"] == "Cauda original"
    following, = chat_effects(await accepted(c, tail))
    assert following["operation_id"] == str(next_head["id"])


@pytest.mark.asyncio
async def test_canonical_audio_matches_before_later_text_and_never_uses_legacy_ai(chatbot_case, monkeypatch):
    c = chatbot_case
    await settings(c, lambda value: value["transcription"].update(enabled=True, reply_enabled=True))
    await rule_create(c, definition(keyword="audio", value="Resposta ao áudio"))
    await rule_create(c, definition(keyword="depois", value="Resposta posterior"))
    audio = await input_event(c, None, kind="audio", message_id="SYNTHETIC_AUDIO", audio={"seconds": 12, "size": 3000, "media_type": "audio/ogg"})
    transcribe, = [item for item in audio["effects"] if item["type"] == "transcribe"]
    assert chat_effects(audio) == []
    later = await input_event(c, "depois")
    assert chat_effects(later) == []
    claim = await internal(c, "/effects/authorize", {**scope(c), "operation_id": transcribe["operation_id"]})
    assert claim.status_code == 200 and claim.json()["authorized"]
    async def transcribe_fake(request, *, max_minutes, authorize):
        await authorize(12, phase="before_provider")
        await authorize(12, phase="after_provider")
        return {"text": "audio", "duration_seconds": 12}
    monkeypatch.setattr(subscription_device_audio, "transcribe_audio", transcribe_fake)
    headers = c.bridge.headers(c.row, live=True) | {"X-Orion-Device-Id": c.device["id"],
        "X-Orion-Device-Generation": str(c.saved["generation"]), "X-Orion-Device-Revision": str(c.device["revision"]),
        "X-Orion-Device-Effect": transcribe["operation_id"], "Content-Type": "audio/ogg"}
    result = await c.private.post(f"/internal/qr/channels/{c.row['id']}/device/audio", headers=headers, content=b"synthetic-placeholder")
    assert result.status_code == 200, result.text
    transcription, = [item for item in result.json()["effects"] if item["type"] == "send_text"]
    claim = await internal(c, "/effects/authorize", {**scope(c), "operation_id": transcription["operation_id"]})
    assert claim.status_code == 200 and claim.json()["authorized"]
    finished = await internal(c, "/effects/finish", {**scope(c), "operation_id": transcription["operation_id"], "state": "accepted", "provider_id": "TRANSCRIPT"})
    assert finished.status_code == 200, finished.text
    audio_reply, = chat_effects(finished.json())
    assert (await grant(c, audio_reply))["payload"]["action"]["text"] == "Resposta ao áudio"
    later_reply, = chat_effects(await accepted(c, audio_reply))
    assert (await grant(c, later_reply))["payload"]["action"]["text"] == "Resposta posterior"
    async with c.ctx.migration.connect() as db:
        channel = (await db.execute(text("SELECT assistant_enabled,allowed_senders FROM qr_channels WHERE id=:id"), {"id": c.row["id"]})).mappings().one()
        assert channel["assistant_enabled"] is False and channel["allowed_senders"] == []


@pytest.mark.asyncio
async def test_hours_and_device_revision_revoke_only_chatbot_decisions(chatbot_case):
    c = chatbot_case
    row = await rule_create(c)
    effect, = chat_effects(await input_event(c))
    assert (await grant(c, effect))["authorized"]
    before = c.device["revision"]
    await edit(c, row, definition(value="Nova versão"))
    current = await c.client.get(c.prefix + f"/{c.device['id']}/settings")
    assert current.json()["revision"] == before
    assert not (await grant(c, effect, recheck=True))["authorized"]
    def outside(value):
        value["business_hours"].update(enabled=True, block_replies_outside_hours=True, outside_message="Fora do expediente")
        for day in value["business_hours"]["days"]:
            day.update(enabled=False, start=None, end=None, break_start=None, break_end=None)
    await settings(c, outside)
    result = await input_event(c)
    assert chat_effects(result) == []
    assert any(item["type"] == "send_text" for item in result["effects"])


@pytest.mark.asyncio
async def test_explicit_test_response_job_uses_connected_device_and_terminal_ledger(chatbot_case):
    c = chatbot_case
    row = await rule_create(c, definition(enabled=False))
    key = str(uuid4())
    payload = {"expected_chatbot_revision": c.chat_revision, "expected_revision": row["revision"],
               "idempotency_key": key, "device_id": c.device["id"], "phone": "+5567999999902"}
    result = await c.client.post(c.chat + f"/rules/{row['id']}/test", json=payload)
    assert result.status_code == 202, result.text
    job = result.json()["job"]
    replay = await c.client.post(c.chat + f"/rules/{row['id']}/test", json=payload)
    assert replay.status_code == 202 and replay.json()["job"]["id"] == job["id"]
    effect, = chat_effects(await pending(c))
    assert (await grant(c, effect))["payload"]["phone"] == "5567999999902"
    await accepted(c, effect)
    done = await c.client.get(c.chat + "/jobs/" + job["id"])
    assert done.status_code == 200 and done.json()["job"]["status"] == "succeeded", done.text
    assert done.json()["job"]["result"] == {"accepted_messages": 1}


@pytest.fixture
def private_media(monkeypatch):
    from app import subscription_chatbot_media as media
    import hashlib
    objects = {}
    def put(tenant, asset, prepared):
        objects[(str(tenant), str(asset), "original")] = prepared.data
        if prepared.thumbnail:
            objects[(str(tenant), str(asset), "thumbnail")] = prepared.thumbnail
    def get(tenant, asset, *, expected_size, expected_sha256, variant="original"):
        data = objects[(str(tenant), str(asset), variant)]
        assert len(data) == expected_size and hashlib.sha256(data).hexdigest() == expected_sha256
        return data
    monkeypatch.setattr(media, "put_media", put)
    monkeypatch.setattr(media, "get_media", get)
    return objects


def media_headers(c, effect):
    return c.bridge.headers(c.row, live=True) | {
        "X-Orion-Device-Id": c.device["id"], "X-Orion-Device-Generation": str(c.saved["generation"]),
        "X-Orion-Device-Revision": str(c.device["revision"]), "X-Orion-Device-Effect": effect["operation_id"],
        "X-Orion-Chatbot-Revision": str(effect["chatbot_revision"])}


@pytest.mark.asyncio
async def test_http_txt_and_carousel_assets_are_private_normalized_and_effect_scoped(chatbot_case, private_media):
    from io import BytesIO
    from PIL import Image
    c = chatbot_case
    request_action = {"id": str(uuid4()), "type": "url", "url": "https://example.com/synthetic",
        "method": "POST", "custom_template": "", "as_file": True, "delay_seconds": 1}
    await rule_create(c, definition(keyword="arquivo", response={"kind": "sequence", "actions": [request_action]}))
    effect, = chat_effects(await input_event(c, "arquivo"))
    assert (await grant(c, effect))["authorized"]
    complete = await internal(c, "/chatbot/http-result", {**scope(c), "operation_id": effect["operation_id"],
        "chatbot_revision": c.chat_revision, "response_json": {"reply": "Conteúdo completo e privado"}})
    assert complete.status_code == 200, complete.text
    document, = chat_effects(complete.json())
    permit = await grant(c, document)
    asset = permit["payload"]["action"]["asset"]
    assert asset["kind"] == "document" and asset["mime"] == "text/plain; charset=utf-8"
    content = await c.private.get(f"/internal/qr/channels/{c.row['id']}/device/chatbot/assets/{asset['id']}/content",
                                  headers=media_headers(c, document))
    assert content.status_code == 200 and content.content == "Conteúdo completo e privado".encode(), content.text
    denied = await c.private.get(f"/internal/qr/channels/{c.row['id']}/device/chatbot/assets/{asset['id']}/content")
    assert denied.status_code == 401
    await accepted(c, document)
    cards = [{"id": str(uuid4()), "title": f"Cartão {n}", "body": "Descrição", "image_url": "https://example.com/image.jpg",
              "button_text": "Selecionar"} for n in range(3)]
    row = await rule_create(c, definition(keyword="cards", response={"kind": "sequence", "actions": [{"id": str(uuid4()),
        "type": "carousel", "body": "Catálogo", "cards": cards, "delay_seconds": 1}]}))
    carousel, = chat_effects(await input_event(c, "cards"))
    assert (await grant(c, carousel))["authorized"]
    buffer = BytesIO()
    Image.new("RGB", (32, 24), "red").save(buffer, format="PNG")
    headers = media_headers(c, carousel) | {"X-Orion-Chatbot-Card": "0", "Content-Type": "application/octet-stream"}
    base = f"/internal/qr/channels/{c.row['id']}/device/chatbot"
    prepared = await c.private.post(base + "/carousel-image", headers=headers, content=buffer.getvalue())
    assert prepared.status_code == 200, prepared.text
    image = prepared.json()["asset"]
    assert image["kind"] == "image" and image["mime"] == "image/jpeg"
    cached = await c.private.post(base + "/carousel-image", headers=headers, content=buffer.getvalue())
    assert cached.status_code == 200 and cached.json() == prepared.json()
    normalized = await c.private.get(base + f"/assets/{image['id']}/content", headers=media_headers(c, carousel))
    assert normalized.status_code == 200 and normalized.content.startswith(b"\xff\xd8")
    unrelated = await c.private.get(base + f"/assets/{asset['id']}/content", headers=media_headers(c, carousel))
    assert unrelated.status_code == 404
    assert (await edit(c, row, definition(keyword="cards", value="Mudou"))).status_code == 200
    revoked = await c.private.get(base + f"/assets/{image['id']}/content", headers=media_headers(c, carousel))
    assert revoked.status_code == 409


@pytest.mark.asyncio
async def test_media_lock_does_not_consume_pool1_and_survives_cancel_until_writer_finishes(chatbot_case, private_media, monkeypatch):
    from threading import Event
    from fastapi import HTTPException
    from app import subscription_chatbot_media as media
    c = chatbot_case
    parent_factory = store.support_session_factory()
    limited_engine = create_async_engine(parent_factory.kw["bind"].url, pool_size=1, max_overflow=0, pool_timeout=0.5)
    limited = async_sessionmaker(limited_engine, expire_on_commit=False)
    monkeypatch.setattr(store, "support_session_factory", lambda: limited)
    @asynccontextmanager
    async def authorized():
        async with store.owner_transaction(c.tenant, c.ctx.actor) as db:
            yield db
    prepared = media.prepare_media(b"Synthetic recovery of immutable bytes", "document")
    entered, release = Event(), Event()
    real_put = media.put_media
    def blocked_put(*args):
        entered.set()
        assert release.wait(5), "synthetic writer was not released"
        real_put(*args)
    monkeypatch.setattr(media, "put_media", blocked_put)
    writing = asyncio.create_task(store.persist_prepared(c.tenant, "document", prepared, authorized))
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        # The one transactional connection remains available while the writer
        # holds its advisory lease on a separate, bounded NullPool connection.
        async with limited() as db:
            assert (await db.execute(text("SELECT 1"))).scalar_one() == 1
        with pytest.raises(HTTPException) as concurrent:
            await store.persist_prepared(c.tenant, "document", prepared, authorized)
        assert concurrent.value.status_code == 409
        writing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await writing
        async with limited() as db:
            reserved = (await db.execute(text("SELECT id,status FROM subscription_chatbot_assets WHERE tenant_id=:t AND sha256=:sha"),
                {"t": c.tenant, "sha": prepared.sha256})).mappings().one()
            assert reserved["status"] == "pending"
        with pytest.raises(HTTPException) as still_owned:
            await store.persist_prepared(c.tenant, "document", prepared, authorized)
        assert still_owned.value.status_code == 409
        release.set()
        await asyncio.wait_for(asyncio.gather(*list(store._media_cleanup), return_exceptions=True), timeout=5)
        async with limited() as db:
            assert (await db.execute(text("SELECT status FROM subscription_chatbot_assets WHERE id=:id"),
                                    {"id": reserved["id"]})).scalar_one() == "failed"
        resumed = await asyncio.wait_for(store.persist_prepared(c.tenant, "document", prepared, authorized), timeout=5)
        assert resumed["id"] == reserved["id"] and resumed["status"] == "ready"
        assert resumed["sha256"] == prepared.sha256
    finally:
        release.set()
        if not writing.done():
            writing.cancel()
        await asyncio.gather(writing, return_exceptions=True)
        await asyncio.wait_for(asyncio.gather(*list(store._media_cleanup), return_exceptions=True), timeout=5)
        await limited_engine.dispose()
