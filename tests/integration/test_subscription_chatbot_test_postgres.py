"""Authenticated, read-only Chatbot simulation against disposable PostgreSQL.

Device setup uses the existing synthetic bridge. After setup the channel has
no lease and every live execution entry point is forbidden. Media decoding is
real; the private object store alone is replaced with an integrity-checked fake.
"""
import asyncio
from contextlib import asynccontextmanager
from io import BytesIO
import json
from pathlib import Path
from threading import Event
from uuid import UUID, uuid4

import httpx
from PIL import Image
import pytest
import pytest_asyncio
from sqlalchemy import text

from app import qr_channels, subscription_chatbot_engine, subscription_chatbot_http
from app import subscription_chatbot_media as media, subscription_chatbot_store as store
from app import subscription_device_audio
from app.plan_routing import include_plan_router
from test_subscription_chatbot_postgres import (
    chatbot_case, definition, device_case, disposable_environment, edit,
    new_owner_http, onboarding_db, private_media, rule_create,
)  # noqa: F401 - pytest requires imported fixtures' actual dependencies too.
from test_subscription_devices_postgres import action, create, settings

pytestmark = pytest.mark.integration
CONTRACT = json.loads((Path(__file__).parents[1] / "fixtures/subscription_chatbot_test_contract.json").read_text())


# Compare full rows, including flags/timestamps, rather than only row counts.
# Simulating must not reserve a real cooldown, selection, quota or operation.
ISOLATED_TABLES = (
    "qr_channels", "subscription_devices", "subscription_device_operations",
    "subscription_device_events", "subscription_device_effects",
    "subscription_device_cooldowns", "subscription_device_audio_usage",
    "subscription_chatbot_state", "subscription_chatbot_rules",
    "subscription_chatbot_templates", "subscription_chatbot_assets",
    "subscription_chatbot_jobs", "subscription_chatbot_runs",
    "subscription_chatbot_cooldowns", "subscription_chatbot_selections",
    "subscription_chatbot_backups", "subscription_chatbot_previews",
)


@pytest_asyncio.fixture
async def simulator_case(chatbot_case):
    from app import subscription_chatbot_test as simulator
    c = chatbot_case
    generic = [route for route in c.app.router.routes if "{resource}" in getattr(route, "path", "")]
    c.app.router.routes[:] = [route for route in c.app.router.routes if route not in generic]
    include_plan_router(c.app, simulator.router)
    c.app.router.routes.extend(generic)
    c.simulator = f"/api/v1/tenants/{c.tenant}/subscription-whatsapp/chatbot-test"
    c.simulation_context = str(uuid4())
    async with c.ctx.migration.begin() as db:
        await db.execute(text("""UPDATE qr_channels SET enabled=false,lease_owner=NULL,
            lease_expires_at=NULL WHERE tenant_id=:t"""), {"t": c.tenant})
        await db.execute(text("UPDATE subscription_devices SET status='disconnected' WHERE tenant_id=:t"),
                         {"t": c.tenant})
    # Keep the synthetic transport consistent with the released SQL lease.
    # A disconnected device must not retain a fake live socket during remove.
    c.bridge.connections.clear()
    yield c


async def persisted_state(c):
    result = {}
    async with c.ctx.migration.connect() as db:
        for table in ISOLATED_TABLES:
            result[table] = (await db.execute(text(f"""SELECT COALESCE(
                jsonb_agg(to_jsonb(entry) ORDER BY to_jsonb(entry)::text), '[]'::jsonb)
                FROM {table} AS entry WHERE tenant_id IN (:tenant,:a,:b)"""),
                {"tenant": c.tenant, "a": c.ctx.a, "b": c.ctx.b})).scalar_one()
    return result


@asynccontextmanager
async def isolated(c, monkeypatch):
    before, calls, forbidden = await persisted_state(c), list(c.bridge.calls), []

    def deny(label):
        async def blocked(*args, **kwargs):
            forbidden.append(label)
            pytest.fail(f"Simulation attempted live execution: {label}")
        return blocked

    with monkeypatch.context() as patch:
        for module, names in (
            (qr_channels, ("_bridge_control",)),
            (subscription_chatbot_engine, ("record_event", "create_test", "drain_contact", "put_effect", "complete_transcript")),
            (subscription_chatbot_http, ("read_url_tags",)),
            (subscription_device_audio, ("transcribe_audio", "_provider_transcribe")),
        ):
            for name in names:
                patch.setattr(module, name, deny(name))
        try:
            yield
        finally:
            after = await persisted_state(c)
            assert not forbidden
            assert c.bridge.calls == calls
            assert after == before, [table for table in ISOLATED_TABLES if after[table] != before[table]]


def request_body(c, value="Olá", *, context=None, revision=None, device=None, request_id=None):
    return {"request_id": str(request_id or uuid4()),
            "context_id": context or c.simulation_context,
            "device_id": device or c.device["id"],
            "expected_chatbot_revision": c.chat_revision if revision is None else revision,
            "input": {"type": "text", "text": value} if isinstance(value, str) else value}


async def simulate(c, value="Olá", **kwargs):
    body = request_body(c, value, **kwargs)
    response = await c.client.post(c.simulator + "/simulate", json=body)
    assert response.status_code == 200, response.text
    assert response.headers.get("cache-control") == "no-store"
    data = response.json()
    assert data["request_id"] == body["request_id"] and data["context_id"] == body["context_id"]
    assert data["chatbot_revision"] == body["expected_chatbot_revision"]
    assert data["device"]["id"] == body["device_id"]
    return data


async def revoke(c, kind):
    if kind == "replaced_device":
        # A generation is immutable and referenced by real ledger FKs. Exercise
        # the supported remove/create lifecycle instead of changing that key.
        await action(c, "remove")
        await create(c, name="Novo dispositivo sintético")
        return
    async with c.ctx.migration.begin() as db:
        if kind == "owner":
            await db.execute(text("UPDATE tenant_memberships SET active=false WHERE tenant_id=:t AND account_id=:a"),
                             {"t": c.tenant, "a": c.ctx.actor.account_id})
        elif kind == "viewer":
            await db.execute(text("UPDATE tenant_memberships SET role='viewer' WHERE tenant_id=:t AND account_id=:a"),
                             {"t": c.tenant, "a": c.ctx.actor.account_id})
        elif kind == "niche":
            await db.execute(text("""UPDATE business_onboarding SET configuration=jsonb_set(
                configuration,'{niche}','"barbearia"'::jsonb) WHERE tenant_id=:t"""), {"t": c.tenant})
        elif kind == "revision":
            await db.execute(text("UPDATE subscription_chatbot_state SET revision=revision+1 WHERE tenant_id=:t"),
                             {"t": c.tenant})
        elif kind == "device_revision":
            await db.execute(text("UPDATE subscription_devices SET revision=revision+1 WHERE tenant_id=:t"),
                             {"t": c.tenant})
        else:
            raise AssertionError(kind)


async def image_rule(c):
    buffer = BytesIO()
    Image.new("RGB", (32, 24), (23, 75, 132)).save(buffer, format="PNG")
    prepared = media.prepare_media(buffer.getvalue(), "image")

    @asynccontextmanager
    async def authorized():
        async with store.owner_transaction(c.tenant, c.ctx.actor) as db:
            yield db

    asset = await store.persist_prepared(c.tenant, "image", prepared, authorized)
    identity = uuid4()
    async with authorized() as db:
        await db.execute(text("""INSERT INTO subscription_chatbot_templates
            (id,tenant_id,origin_id,name,kind,body,asset_id)
            VALUES(:id,:t,:origin,'Imagem sintética','image','Legenda do teste',:asset)"""),
            {"id": identity, "t": c.tenant, "origin": uuid4(), "asset": asset["id"]})
        c.chat_revision = await store.bump_revision(db, c.tenant)
    row = await rule_create(c, definition(keyword="imagem", response={"kind": "sequence", "actions": [
        {"id": str(uuid4()), "type": "template", "template_id": str(identity), "delay_seconds": 1}]}))
    return row, prepared


def menu_definition():
    return definition(keyword="menu", response={"kind": "list", "title": "Atendimento",
        "description": "Escolha uma opção", "footer": "Orion", "button_text": "Ver opções",
        "sections": [{"id": str(uuid4()), "title": "Opções", "rows": [{"id": str(uuid4()),
            "title": "Informações", "description": "Conheça o serviço", "action": {
                "id": str(uuid4()), "type": "text", "text": "Detalhes escolhidos", "delay_seconds": 1}}]}]})


@pytest.mark.asyncio
async def test_bootstrap_and_text_work_without_a_live_channel_and_persist_nothing(simulator_case, monkeypatch):
    c = simulator_case
    # All seven default days are closed. The simulator explicitly ignores the
    # live hours policy while leaving the saved device configuration intact.
    await settings(c, lambda value: value["business_hours"].update(enabled=True, block_replies_outside_hours=True))
    row = await rule_create(c, definition(keyword="Olá", value="Resposta local"))
    async with isolated(c, monkeypatch):
        bootstrap = await c.client.get(c.simulator)
        assert bootstrap.status_code == 200 and bootstrap.headers.get("cache-control") == "no-store", bootstrap.text
        data = bootstrap.json()
        assert data["chatbot_revision"] == c.chat_revision
        assert data["limits"] == CONTRACT["bootstrap_response"]["limits"]
        assert data["capabilities"]["simulate"] is True and data["capabilities"]["include_inactive"] is True
        assert [(device["id"], device["status"]) for device in data["devices"]] == [(c.device["id"], "disconnected")]
        result = await simulate(c, "  OLÁ  ")
        assert result["outcome"] == "matched" and result["matched_rule"]["id"] == row["id"]
        assert result["input"]["text"] == "OLÁ"
        assert result["messages"][0]["type"] == "text" and result["messages"][0]["text"] == "Resposta local"
        assert result["device"]["status"] == "disconnected"
        no_match = await simulate(c, "mensagem sem correspondência")
        assert no_match["outcome"] == "no_match" and no_match["matched_rule"] is None
        assert no_match["messages"][0]["text"] == CONTRACT["no_match_response"]["messages"][0]["text"]
        assert any(item["rule_id"] == row["id"] for item in no_match["keywords"])
        encoded = json.dumps(data | result)
        assert all(key not in encoded for key in ("credential_encrypted", "proxy_secret", "lease_owner", "idempotency_key"))


@pytest.mark.asyncio
async def test_inactive_newest_rule_and_disabled_dependency_are_simulated_only_in_copy(simulator_case, monkeypatch):
    c = simulator_case
    older = await rule_create(c, definition(keyword="mesma", value="Antiga ativa"))
    inactive = await rule_create(c, definition(keyword="mesma", value="Nova inativa", enabled=False))
    leaf = await rule_create(c, definition(keyword="folha", value="Dependência inativa preservada"))
    parent = await rule_create(c, definition(keyword="pai", response={"kind": "sequence", "actions": [
        {"id": str(uuid4()), "type": "nested_reply", "rule_id": leaf["id"], "delay_seconds": 1}]}))
    disabled = await edit(c, leaf, definition(keyword="folha", value="Dependência inativa preservada", enabled=False))
    assert disabled.status_code == 200, disabled.text
    async with isolated(c, monkeypatch):
        matched = await simulate(c, "mesma")
        assert matched["matched_rule"]["id"] == inactive["id"] and matched["matched_rule"]["enabled"] is False
        assert matched["messages"][0]["text"] == "Nova inativa"
        forced = await simulate(c, {"type": "rule", "rule_id": older["id"]})
        assert forced["matched_rule"]["id"] == older["id"] and forced["matched_rule"]["reason"] == "forced_rule"
        expanded = await simulate(c, {"type": "rule", "rule_id": parent["id"]})
        assert expanded["messages"][0]["text"] == "Dependência inativa preservada"


@pytest.mark.asyncio
async def test_repeated_wildcard_requests_never_consume_a_real_cooldown_or_job(simulator_case, monkeypatch):
    c = simulator_case
    row = await rule_create(c, definition(keyword="*", value="Boas-vindas local"))
    key = uuid4()
    async with isolated(c, monkeypatch):
        values = [await simulate(c, value, request_id=key) for value in ("Primeira", "Segunda", "Primeira")]
        assert all(value["matched_rule"]["id"] == row["id"] and value["messages"][0]["text"] == "Boas-vindas local" for value in values)


@pytest.mark.asyncio
async def test_native_list_selection_is_local_and_bound_to_context_rule_and_row(simulator_case, monkeypatch):
    c = simulator_case
    await rule_create(c, menu_definition())
    async with isolated(c, monkeypatch):
        opened = await simulate(c, "menu")
        menu, = opened["messages"]
        assert menu["type"] == "list"
        row = menu["sections"][0]["rows"][0]
        assert "action" not in row
        selection = {"type": "selection", "selection_token": menu["selection_token"], "row_id": row["id"]}
        selected = await simulate(c, selection)
        assert selected["outcome"] == "matched" and selected["messages"][0]["text"] == "Detalhes escolhidos"
        # Replaying a local selection is harmless: no WhatsApp selection token
        # or persistent event is created or consumed by the simulator.
        replay = await simulate(c, selection)
        assert replay["messages"][0]["text"] == "Detalhes escolhidos"
        for value, context in ((selection, str(uuid4())),
            (selection | {"row_id": str(uuid4())}, None),
            (selection | {"selection_token": menu["selection_token"] + "x"}, None)):
            invalid = await c.client.post(c.simulator + "/simulate", json=request_body(c, value, context=context))
            assert invalid.status_code == 409, invalid.text
            assert invalid.json()["detail"]["code"] == CONTRACT["errors"]["invalid_context"]


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["revision", "replaced_device", "device_revision"])
async def test_selection_cannot_survive_configuration_epoch_changes(simulator_case, monkeypatch, change):
    c = simulator_case
    await rule_create(c, menu_definition())
    opened = await simulate(c, "menu")
    menu, = opened["messages"]
    selection = {"type": "selection", "selection_token": menu["selection_token"], "row_id": menu["sections"][0]["rows"][0]["id"]}
    await revoke(c, change)
    if change == "revision":
        c.chat_revision += 1
    async with isolated(c, monkeypatch):
        denied = await c.client.post(c.simulator + "/simulate", json=request_body(c, selection))
        assert denied.status_code == 409, denied.text
        assert denied.json()["detail"]["code"] == CONTRACT["errors"]["invalid_context"]


@pytest.mark.asyncio
async def test_external_url_and_carousel_remain_declarative_without_http_or_asr(simulator_case, monkeypatch):
    c = simulator_case
    actions = [{"id": str(uuid4()), "type": "url", "url": "https://example.com/synthetic-no-fetch",
        "method": "POST", "custom_template": "{{reply}}", "as_file": True, "delay_seconds": 1},
        {"id": str(uuid4()), "type": "carousel", "body": "Catálogo", "delay_seconds": 1,
         "cards": [{"id": str(uuid4()), "title": f"Opção {index}", "body": "Descrição",
             "image_url": f"https://example.com/never-fetched-{index}.png", "button_text": "Informações"} for index in range(3)]}]
    await rule_create(c, definition(keyword="externo", response={"kind": "sequence", "actions": actions}))
    async with isolated(c, monkeypatch):
        data = await simulate(c, "externo")
        external, carousel = data["messages"]
        assert external["type"] == "external_request" and external["executed"] is False and external["as_file"] is True
        assert carousel["type"] == "carousel" and len(carousel["cards"]) == 3
        assert all(card["image_status"] == "not_requested" and "image_url" not in card for card in carousel["cards"])


@pytest.mark.asyncio
async def test_real_auth_csrf_cross_tenant_and_unknown_ids_are_rejected(simulator_case, monkeypatch):
    c = simulator_case
    await rule_create(c)
    async with isolated(c, monkeypatch):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=c.app), base_url="https://test") as anonymous:
            assert (await anonymous.get(c.simulator)).status_code == 401
            assert (await anonymous.post(c.simulator + "/simulate", json=request_body(c))).status_code == 401
        no_csrf = await c.client.post(c.simulator + "/simulate", json=request_body(c), headers={"x-orion-csrf": ""})
        assert no_csrf.status_code == 403
        foreign = c.simulator.replace(str(c.tenant), str(c.ctx.b))
        assert (await c.client.get(foreign)).status_code in {403, 404}
        assert (await c.client.post(foreign + "/simulate", json=request_body(c))).status_code in {403, 404}
        for body in (request_body(c, device=str(uuid4())), request_body(c, {"type": "rule", "rule_id": str(uuid4())})):
            denied = await c.client.post(c.simulator + "/simulate", json=body)
            assert denied.status_code == 404, denied.text


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["owner", "viewer", "niche"])
async def test_current_owner_and_subscription_niche_are_required_for_every_request(simulator_case, monkeypatch, change):
    c = simulator_case
    await rule_create(c)
    assert (await c.client.get(c.simulator)).status_code == 200
    await revoke(c, change)
    async with isolated(c, monkeypatch):
        denied = await c.client.get(c.simulator)
        assert denied.status_code == 403, denied.text
        denied = await c.client.post(c.simulator + "/simulate", json=request_body(c))
        assert denied.status_code == 403, denied.text


@pytest.mark.asyncio
async def test_stale_revision_and_utf16_body_boundaries_do_not_create_any_operations(simulator_case, monkeypatch):
    c = simulator_case
    await rule_create(c, definition(keyword="*", value="Texto local"))
    async with isolated(c, monkeypatch):
        for text_value in ("x" * 400, "🙂" * 200):
            assert (await simulate(c, text_value))["outcome"] == "matched"
        for text_value in ("x" * 401, "🙂" * 201, " " * 400 + "x", "\x00", "linha\nseguinte", "   "):
            invalid = await c.client.post(c.simulator + "/simulate", json=request_body(c, text_value))
            assert invalid.status_code == 422, invalid.text
        stale = await c.client.post(c.simulator + "/simulate", json=request_body(c, revision=c.chat_revision - 1))
        assert stale.status_code == 409 and stale.json()["detail"]["code"] == CONTRACT["errors"]["stale_revision"], stale.text
        too_large = await c.client.post(c.simulator + "/simulate", content=b" " * 8193,
                                       headers={"Content-Type": "application/json"})
        assert too_large.status_code == 413, too_large.text
        unknown = await c.client.post(c.simulator + "/simulate", json=request_body(c) | {"phone": "+5567999999901"})
        assert unknown.status_code == 422


@pytest.mark.asyncio
async def test_private_image_bytes_are_scoped_to_owner_tenant_asset_and_revision(simulator_case, private_media, monkeypatch):
    c = simulator_case
    _, prepared = await image_rule(c)
    async with isolated(c, monkeypatch):
        result = await simulate(c, "imagem")
        output, = result["messages"]
        assert output["type"] == "media" and output["kind"] == "image" and output["caption"] == "Legenda do teste"
        asset = output["asset"]
        assert asset["mime"] == "image/jpeg" and asset["size"] == prepared.size
        original = await c.client.get(asset["content_url"])
        thumb = await c.client.get(asset["thumbnail_url"])
        assert original.status_code == thumb.status_code == 200
        assert original.content == prepared.data and thumb.content == prepared.thumbnail
        assert original.headers.get("cache-control") == "no-store" and original.headers.get("x-content-type-options") == "nosniff"
        assert original.headers.get("content-type").startswith("image/jpeg")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=c.app), base_url="https://test") as anonymous:
            assert (await anonymous.get(asset["content_url"])).status_code == 401
        foreign = await c.client.get(asset["content_url"].replace(str(c.tenant), str(c.ctx.b)))
        assert foreign.status_code in {403, 404, 409}, foreign.text
        wrong_asset = await c.client.get(asset["content_url"].replace(asset["id"], str(uuid4())))
        assert wrong_asset.status_code in {404, 409}, wrong_asset.text
        unsigned = await c.client.get(asset["content_url"].split("?", 1)[0])
        assert unsigned.status_code in {409, 422}, unsigned.text


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["owner", "niche", "revision"])
async def test_private_media_reauthorizes_after_storage_read_before_returning_bytes(simulator_case, private_media, monkeypatch, change):
    c = simulator_case
    _, prepared = await image_rule(c)
    data = await simulate(c, "imagem")
    url = data["messages"][0]["asset"]["content_url"]
    original_get = media.get_media
    entered, release = Event(), Event()

    def blocked_get(*args, **kwargs):
        value = original_get(*args, **kwargs)
        entered.set()
        assert release.wait(5), "The synthetic media reader was not released"
        return value

    monkeypatch.setattr(media, "get_media", blocked_get)
    downloading = asyncio.create_task(c.client.get(url))
    try:
        assert await asyncio.to_thread(entered.wait, 3), "Media read was not reached"
        await asyncio.wait_for(revoke(c, change), timeout=3)
        after_revocation, calls = await persisted_state(c), list(c.bridge.calls)
        release.set()
        response = await asyncio.wait_for(downloading, timeout=5)
        assert response.status_code in {403, 409}, response.text
        assert response.content != prepared.data and not response.content.startswith(b"\xff\xd8")
        assert await persisted_state(c) == after_revocation
        assert c.bridge.calls == calls
    finally:
        release.set()
        if not downloading.done():
            downloading.cancel()
        await asyncio.gather(downloading, return_exceptions=True)
