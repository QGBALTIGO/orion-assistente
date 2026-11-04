"""Private, effect-scoped media and HTTP receipts for the subscription Chatbot.

No endpoint accepts a destination or message to send. Transport bytes and HTTP
receipts are useful only to the canonical, already-authorized effect.
"""
import asyncio
from contextlib import asynccontextmanager
import math
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.encoders import jsonable_encoder
from sqlalchemy import text

from app import qr_channels as channels
from app import subscription_devices as devices
from app import subscription_chatbot_engine as engine
from app import subscription_chatbot_store as store
from app import subscription_chatbot_rules as rules
from app.subscription_chatbot_schemas import HttpResult


BASE = "/internal/qr/channels/{channel_id}/device/chatbot"
router = APIRouter(tags=["Transporte privado do Chatbot"], dependencies=[Depends(channels.enabled)])
JSON_BYTES = 512 * 1024


def header_scope(request):
    try:
        values = {"device_id": UUID(request.headers["x-orion-device-id"]),
                  "generation": UUID(request.headers["x-orion-device-generation"]),
                  "settings_revision": int(request.headers["x-orion-device-revision"]),
                  "operation_id": UUID(request.headers["x-orion-device-effect"]),
                  "chatbot_revision": int(request.headers["x-orion-chatbot-revision"])}
        if not 1 <= values["settings_revision"] <= 2**63-1 or not 1 <= values["chatbot_revision"] <= 2**63-1:
            raise ValueError()
        return values
    except (KeyError, ValueError, TypeError):
        raise HTTPException(401, "Recurso não autorizado") from None


@asynccontextmanager
async def effect_transaction(request, scope, *, receipt=False):
    async with devices._private_transaction(request, device_id=scope["device_id"]) as (db, device, _, owner, fence):
        effect = await devices._effect(db, device, scope["operation_id"])
        if (effect["type"] != "chatbot_action" or device["generation"] != scope["generation"]
                or effect["generation"] != scope["generation"]
                or effect["settings_revision"] != scope["settings_revision"]
                or effect["chatbot_revision"] != scope["chatbot_revision"]):
            raise store.fail("CHATBOT_EFFECT_REVOKED")
        if receipt:
            if effect["attempts"] < 1 or effect["status"] not in {"running", "uncertain", "accepted", "failed", "not_sent"}:
                raise store.fail("CHATBOT_EFFECT_REVOKED")
        elif (effect["status"] != "running" or effect["claimed_owner"] != owner
                or effect["claimed_fence"] != fence or not devices._current_effect(device, effect)
                or not await engine.policy_allows(db, device, effect)):
            raise store.fail("CHATBOT_EFFECT_REVOKED")
        yield db, device, effect


def _referenced_asset(effect, asset_id):
    action = effect["payload"]["action"]
    if action["type"] in {"image", "sticker", "video", "ptv", "document"}:
        return str((action.get("asset") or {}).get("id")) == str(asset_id)
    if action["type"] == "carousel":
        return str(asset_id) in ((effect.get("result") or {}).get("carousel_assets") or {}).values()
    return False


async def _asset(db, tenant, asset_id):
    row = (await db.execute(text("""SELECT * FROM subscription_chatbot_assets
        WHERE tenant_id=:t AND id=:id AND status='ready'"""), {"t": tenant, "id": asset_id})).mappings().one_or_none()
    if row is None:
        raise store.fail("CHATBOT_NOT_FOUND", 404)
    return dict(row)


@router.get(BASE + "/assets/{asset_id}/content")
async def asset_content(channel_id: UUID, asset_id: UUID, request: Request, variant: str = "original"):
    scope = header_scope(request)
    if variant not in {"original", "thumbnail"}:
        raise store.fail("CHATBOT_NOT_FOUND", 404)
    async with effect_transaction(request, scope) as (db, device, effect):
        if not _referenced_asset(effect, asset_id):
            raise store.fail("CHATBOT_NOT_FOUND", 404)
        asset = await _asset(db, device["tenant_id"], asset_id)
        tenant = device["tenant_id"]
        if variant == "thumbnail" and not asset["thumbnail_size"]:
            raise store.fail("CHATBOT_NOT_FOUND", 404)
    prefix = "thumbnail_" if variant == "thumbnail" else ""
    from app.subscription_chatbot_media import get_media
    data = await asyncio.to_thread(get_media, tenant, asset_id,
        expected_size=asset[prefix + "size"], expected_sha256=asset[prefix + "sha256"], variant=variant)
    async with effect_transaction(request, scope) as (_, _, effect):
        if not _referenced_asset(effect, asset_id):
            raise store.fail("CHATBOT_NOT_FOUND", 404)
    return Response(data, media_type=asset[prefix + "mime"], headers={"Cache-Control": "no-store",
                     "X-Content-Type-Options": "nosniff"})


async def read_bytes(request, maximum):
    length = request.headers.get("content-length")
    if length is not None and (not length.isascii() or not length.isdigit() or len(length) > 9 or int(length) > maximum):
        raise HTTPException(413, "Arquivo acima do limite")
    result = bytearray()
    try:
        async with asyncio.timeout(20):
            async for chunk in request.stream():
                if len(result) + len(chunk) > maximum:
                    raise HTTPException(413, "Arquivo acima do limite")
                result.extend(chunk)
    except TimeoutError:
        raise HTTPException(408, "O envio não foi concluído") from None
    return bytes(result)


@router.post(BASE + "/carousel-image")
async def carousel_image(channel_id: UUID, request: Request):
    scope = header_scope(request)
    try:
        card = int(request.headers["x-orion-chatbot-card"])
        if not 0 <= card <= 2:
            raise ValueError()
    except (KeyError, ValueError, TypeError):
        raise store.fail("CHATBOT_NOT_FOUND", 404) from None
    async with effect_transaction(request, scope) as (db, device, effect):
        action = effect["payload"]["action"]
        if action["type"] != "carousel" or card >= len(action["cards"]):
            raise store.fail("CHATBOT_NOT_FOUND", 404)
        tenant = device["tenant_id"]
        cached = ((effect["result"] or {}).get("carousel_assets") or {}).get(str(card))
        if cached:
            return jsonable_encoder({"asset": store.asset_descriptor(await _asset(db, tenant, UUID(cached)))})
    raw = await read_bytes(request, 8_000_000)
    from app.subscription_chatbot_media import prepare_media
    prepared = await asyncio.to_thread(prepare_media, raw, "image")
    @asynccontextmanager
    async def authorized():
        async with effect_transaction(request, scope) as (db, _, _):
            yield db
    asset = await store.persist_prepared(tenant, "image", prepared, authorized)
    async with effect_transaction(request, scope) as (db, _, effect):
        result = dict(effect["result"] or {})
        assets = dict(result.get("carousel_assets") or {})
        # The first normalized image for this operation/card is immutable.
        if str(card) in assets and assets[str(card)] != str(asset["id"]):
            raise store.fail("CHATBOT_CONFLICT")
        assets[str(card)] = str(asset["id"])
        result["carousel_assets"] = assets
        await db.execute(text("UPDATE subscription_device_effects SET result=CAST(:result AS jsonb) WHERE id=:id"),
                         {"id": effect["id"], "result": store.encode(result)})
    return jsonable_encoder({"asset": store.asset_descriptor(asset)})


def checked_result(value):
    """Bound JSON shape as well as bytes; never evaluate expressions or URLs."""
    stack, nodes = [(value, 0)], 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > 8192 or depth > 16:
            raise store.fail("CHATBOT_HTTP_RESULT_INVALID", 422)
        if isinstance(item, dict):
            if any(not isinstance(key, str) or len(key) > 2048 for key in item):
                raise store.fail("CHATBOT_HTTP_RESULT_INVALID", 422)
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)
        elif item is not None and not isinstance(item, (str, bool, int, float)):
            raise store.fail("CHATBOT_HTTP_RESULT_INVALID", 422)
        elif isinstance(item, float) and not math.isfinite(item):
            raise store.fail("CHATBOT_HTTP_RESULT_INVALID", 422)
    try:
        if len(store.encode(value).encode("utf-8")) > JSON_BYTES:
            raise ValueError()
    except (ValueError, UnicodeError):
        raise store.fail("CHATBOT_HTTP_RESULT_INVALID", 422) from None
    return value


async def _record_suppressed(db, device, effect, result):
    result.update(resolved=True, delivery_suppressed=True)
    await db.execute(text("""UPDATE subscription_device_effects SET status='accepted',
        result=CAST(:result AS jsonb),finished_at=clock_timestamp() WHERE id=:id"""),
        {"id": effect["id"], "result": store.encode(result)})
    # Revoke the unexecuted tail without using stale configuration to compose
    # any new output. HTTP evidence remains, independently of its replies.
    await db.execute(text("""UPDATE subscription_device_effects SET status='not_sent',
        finished_at=clock_timestamp(),error_code='CHATBOT_EFFECT_REVOKED'
        WHERE tenant_id=:t AND chatbot_run_id=:run AND status='pending'"""),
        {"t": device["tenant_id"], "run": effect["chatbot_run_id"]})
    await engine.after_effect_finish(db, device, effect, "accepted")
    return {"recorded": True, "operation_id": str(effect["id"]), "effects": []}


@router.post(BASE + "/http-result")
async def http_result(channel_id: UUID, request: Request):
    # Authenticate the bridge before consuming an untrusted JSON body.
    async with devices._private_transaction(request):
        pass
    from app.subscription_chatbot import bounded_json
    payload = await bounded_json(request, HttpResult, limit=JSON_BYTES + 8192)
    response_json = checked_result(payload.response_json)
    scope = payload.model_dump(exclude={"response_json", "reconcile"})
    digest = store.fingerprint(response_json)
    transaction = lambda: effect_transaction(request, scope, receipt=payload.reconcile)
    async with transaction() as (db, device, effect):
        if effect["payload"]["action"]["type"] != "http":
            raise store.fail("CHATBOT_EFFECT_REVOKED")
        result = dict(effect["result"] or {})
        if result.get("http_fingerprint") not in {None, digest}:
            raise store.fail("CHATBOT_CONFLICT")
        if result.get("resolved"):
            return jsonable_encoder({"recorded": True, "operation_id": effect["id"],
                "effects": await engine.available_effects(db, device["id"], effect["event_id"])
                    if devices._current_effect(device, effect) and await engine.policy_allows(db, device, effect) else []})
        result.update(http_fingerprint=digest, response_json=response_json)
        await db.execute(text("UPDATE subscription_device_effects SET result=CAST(:result AS jsonb) WHERE id=:id"),
                         {"id": effect["id"], "result": store.encode(result)})
        if not devices._current_effect(device, effect) or not await engine.policy_allows(db, device, effect):
            return await _record_suppressed(db, device, effect, result)
        action = effect["payload"]["action"]
        tenant = device["tenant_id"]
        try:
            reply = rules.render_http_result(action, response_json)
            parts = rules.split_text(reply) if not action.get("as_file") else None
        except ValueError:
            result.update(resolved=True, error_code="CHATBOT_HTTP_RESULT_INVALID")
            await db.execute(text("""UPDATE subscription_device_effects SET status='failed',
                result=CAST(:result AS jsonb),error_code='CHATBOT_HTTP_RESULT_INVALID',finished_at=clock_timestamp()
                WHERE id=:id"""), {"id": effect["id"], "result": store.encode(result)})
            await engine.after_effect_finish(db, device, effect, "failed")
            return {"recorded": True, "operation_id": str(effect["id"]), "effects": []}
    asset = None
    if action.get("as_file"):
        from app.subscription_chatbot_media import prepare_media
        prepared = await asyncio.to_thread(prepare_media, reply.encode("utf-8"), "document")
        @asynccontextmanager
        async def authorized():
            async with transaction() as (db, device, effect):
                if not devices._current_effect(device, effect) or not await engine.policy_allows(db, device, effect):
                    raise store.fail("CHATBOT_EFFECT_REVOKED")
                yield db
        asset = await store.persist_prepared(tenant, "document", prepared, authorized)
    async with transaction() as (db, device, effect):
        result = dict(effect["result"] or {})
        if result.get("resolved"):
            return jsonable_encoder({"recorded": True, "operation_id": effect["id"],
                "effects": await engine.available_effects(db, device["id"], effect["event_id"])
                    if devices._current_effect(device, effect) and await engine.policy_allows(db, device, effect) else []})
        if not devices._current_effect(device, effect) or not await engine.policy_allows(db, device, effect):
            return await _record_suppressed(db, device, effect, result)
        run = dict((await db.execute(text("SELECT * FROM subscription_chatbot_runs WHERE tenant_id=:t AND id=:id FOR UPDATE"),
            {"t": tenant, "id": effect["chatbot_run_id"]})).mappings().one())
        successors = (await db.execute(text("""SELECT id FROM subscription_device_effects
            WHERE tenant_id=:t AND previous_effect=:id AND status='pending' FOR UPDATE"""),
            {"t": tenant, "id": effect["id"]})).scalars().all()
        actions = ([{"type": "document", "asset": store.asset_descriptor(asset), "file_name": "resposta.txt"}]
                   if asset else [{"type": "text", "text": part} for part in parts])
        # Reserve at least one response for every remaining HTTP action. A
        # bounded native sequence must not turn into hundreds of URL replies.
        transports = (await db.execute(text("""SELECT count(*) FROM subscription_device_effects
            WHERE tenant_id=:t AND chatbot_run_id=:run AND id<>:id
              AND (payload->'action'->>'type'<>'http' OR status<>'accepted')"""),
            {"t": tenant, "run": run["id"], "id": effect["id"]})).scalar_one()
        if transports + len(actions) > 30:
            result.update(resolved=True, error_code="CHATBOT_HTTP_RESULT_INVALID")
            await db.execute(text("""UPDATE subscription_device_effects SET status='failed',
                result=CAST(:result AS jsonb),error_code='CHATBOT_HTTP_RESULT_INVALID',finished_at=clock_timestamp()
                WHERE id=:id"""), {"id": effect["id"], "result": store.encode(result)})
            await engine.after_effect_finish(db, device, effect, "failed")
            return {"recorded": True, "operation_id": str(effect["id"]), "effects": []}
        previous = effect["id"]
        for index, child_action in enumerate(actions):
            child = await engine.put_effect(db, device, run, {"action": child_action, "delay_seconds": 0},
                f"chatbot-http:{effect['id']}:{index}", previous=previous)
            previous = child["id"]
        for successor in successors:
            await db.execute(text("UPDATE subscription_device_effects SET previous_effect=:previous WHERE tenant_id=:t AND id=:id"),
                             {"t": tenant, "id": successor, "previous": previous})
        result.update(resolved=True, reply_effects=len(actions))
        await db.execute(text("""UPDATE subscription_device_effects SET status='accepted',
            result=CAST(:result AS jsonb),finished_at=clock_timestamp() WHERE id=:id"""),
            {"id": effect["id"], "result": store.encode(result)})
        effects = await engine.after_effect_finish(db, device, effect, "accepted")
        return jsonable_encoder({"recorded": True, "operation_id": effect["id"], "effects": effects})
