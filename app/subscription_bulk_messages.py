"""Immediate owner-confirmed bulk workbench; reads never create send effects."""
import asyncio
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from pydantic import ValidationError
from sqlalchemy import text

from app.catalog_api import Actor, authenticate
from app import qr_channels as channels
from app import subscription_bulk_schemas as schemas
from app import subscription_bulk_store as store

BASE = "/api/v1/tenants/{tenant_id}/subscription-whatsapp/bulk-messages"
PRIVATE = "/internal/qr/channels/{channel_id}/device/bulk"
router = APIRouter(tags=["Mensagem em Massa de assinaturas"])


def output(response, value):
    response.headers["Cache-Control"] = "no-store"
    return jsonable_encoder(value)


def validate_search(value, status=""):
    if "\x00" in value or status and status not in schemas.RECIPIENT_STATES:
        raise store.fail("BULK_INVALID", 422)


async def payload_after_owner(tenant, actor, request, model):
    # Do not parse or retain a private workbench before resolving its owner.
    async with store.owner_transaction(tenant, actor):
        pass
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > schemas.MAX_BODY:
            raise store.fail("BULK_LIMIT", 413)
        raw.extend(chunk)
    try:
        return model.model_validate_json(bytes(raw))
    except (ValidationError, ValueError):
        raise store.fail("BULK_INVALID", 422) from None


async def owned_batch(db, tenant, actor, identity, *, lock=False):
    row = await store.batch_row(db, tenant, identity, lock=lock)
    if row["owner_account_id"] != actor.account_id:
        raise store.fail("BULK_NOT_FOUND", 404)
    return row


@router.get(BASE)
async def history(tenant_id: UUID, response: Response, actor: Actor = Depends(authenticate),
                  q: str = Query(default="", max_length=255), status: str = "",
                  page: int = Query(default=1, ge=1, le=10000)):
    validate_search(q, status)
    async with store.owner_transaction(tenant_id, actor) as db:
        return output(response, await store.history_page(db, tenant_id, actor, await store.clock(db),
            await store.timezone_for(db, tenant_id), q=q, status=status, page=page))


@router.get(BASE + "/options")
async def options(tenant_id: UUID, response: Response, actor: Actor = Depends(authenticate)):
    async with store.owner_transaction(tenant_id, actor) as db:
        return output(response, {"devices": await store.campaigns.device_options(db, tenant_id, actor),
            "templates": await store.campaigns.template_options(db, tenant_id),
            "groups": await store.campaigns.group_summaries(db, tenant_id),
            "timezone": await store.timezone_for(db, tenant_id), "generated_at": await store.clock(db),
            "defaults": {"interval_seconds": 10, "message_type": "text"},
            "limits": {"recipients": schemas.MAX_RECIPIENTS, "text_utf16": 1000,
                "groups": schemas.MAX_GROUPS, "review_seconds": schemas.REVIEW_SECONDS,
                "interval_seconds": {"min": 10, "max": 60, "step": 1}}})


@router.get(BASE + "/contacts")
async def contacts(tenant_id: UUID, response: Response, actor: Actor = Depends(authenticate),
                   q: str = Query(default="", max_length=255), page: int = Query(default=1, ge=1, le=10000)):
    validate_search(q)
    async with store.owner_transaction(tenant_id, actor) as db:
        return output(response, await store.contacts_page(db, tenant_id, q, page, await store.clock(db),
                                                          await store.timezone_for(db, tenant_id)))


@router.post(BASE + "/audience")
async def audience(tenant_id: UUID, request: Request, response: Response, actor: Actor = Depends(authenticate)):
    payload = await payload_after_owner(tenant_id, actor, request, schemas.AudienceRequest)
    async with store.owner_transaction(tenant_id, actor) as db:
        groups = await store.campaigns.group_summaries(db, tenant_id, payload.group_ids)
        if len(groups) != len(payload.group_ids):
            raise store.fail("BULK_NOT_FOUND", 404)
        rows, duplicates = await store.selected_contacts(db, tenant_id, payload.model_dump(mode="json"))
        blocked = sum(row["blocked"] for row in rows)
        return output(response, {"items": [store.contact_dto(row) for row in rows], "total": len(rows),
            "eligible": len(rows) - blocked, "blocked": blocked, "duplicates": duplicates,
            "timezone": await store.timezone_for(db, tenant_id), "generated_at": await store.clock(db)})


@router.get(BASE + "/requests/{key}")
async def recover_request(tenant_id: UUID, key: UUID, response: Response, actor: Actor = Depends(authenticate)):
    async with store.owner_transaction(tenant_id, actor) as db:
        row = await store.request_row(db, tenant_id, actor, key)
        if row is None:
            raise store.fail("BULK_NOT_FOUND", 404)
        return output(response, store.operation_dto(row, await store.clock(db), replayed=True))


@router.post(BASE + "/review", status_code=201)
async def review_bulk(tenant_id: UUID, request: Request, response: Response, actor: Actor = Depends(authenticate)):
    payload = await payload_after_owner(tenant_id, actor, request, schemas.ReviewBulk)
    async with store.owner_transaction(tenant_id, actor) as db:
        values = payload.model_dump(mode="json")
        existing = await store.existing_request(db, tenant_id, actor, payload.idempotency_key, "review", values)
        if existing:
            return output(response, store.operation_dto(existing, await store.clock(db), replayed=True))
        snapshot = await store.capture_review(db, tenant_id, actor, values["batch"])
        identity, now = uuid4(), await store.clock(db)
        snapshot["review_request_id"] = str(identity)
        preview = store.review_preview(snapshot, identity, now)
        saved = await store.save_request(db, tenant_id, actor, payload.idempotency_key, "review", values,
            snapshot=snapshot, preview=preview, identity=identity, now=now)
        return output(response, store.operation_dto(saved, now))


async def persist_confirmation(db, tenant, actor, intent, now):
    snapshot = intent["snapshot"]
    identity = uuid4()
    await db.execute(text("""INSERT INTO subscription_bulk_batches
        (id,tenant_id,owner_account_id,run_version,interval_seconds,snapshot,confirmed_at,next_admission_at)
        VALUES(:id,:t,:a,:run,:interval,CAST(:snapshot AS jsonb),:now,:now)"""),
        {"id": identity, "t": tenant, "a": actor.account_id, "run": intent["review_version"],
         "interval": snapshot["definition"]["interval_seconds"], "now": now,
         "snapshot": store.encode(store.metadata_snapshot(snapshot, actor, intent["id"], now))})
    await store.insert_recipients(db, tenant, identity, intent["review_version"], snapshot, now)
    result = {"batch": await store.batch_item(db, tenant, identity, actor, now)}
    return await store.complete_request(db, tenant, intent["id"], result, now, bulk_id=identity)


@router.post(BASE + "/confirm", status_code=202)
async def confirm_bulk(tenant_id: UUID, request: Request, response: Response, actor: Actor = Depends(authenticate)):
    payload = await payload_after_owner(tenant_id, actor, request, schemas.ConfirmBulk)
    async with store.owner_transaction(tenant_id, actor) as db:
        intent = await store.request_row(db, tenant_id, actor, payload.idempotency_key)
        if intent is None:
            raise store.fail("BULK_NOT_FOUND", 404)
        if intent["kind"] != "review" or intent["review_version"] != payload.review_version:
            raise store.fail("BULK_IDEMPOTENCY_CONFLICT")
        now = await store.clock(db)
        if intent["status"] == "succeeded":
            return output(response, store.operation_dto(intent, now, replayed=True))
        if intent["expires_at"] <= now:
            raise store.fail("BULK_REVIEW_EXPIRED", 410)
        current = await store.capture_review(db, tenant_id, actor, intent["snapshot"]["definition"])
        current["review_request_id"] = str(intent["id"])
        if store.fingerprint(current) != intent["review_version"]:
            raise store.fail("BULK_CONFLICT")
        if not intent["snapshot"]["counts"]["eligible"]:
            raise store.fail("BULK_NO_ELIGIBLE")
        saved = await persist_confirmation(db, tenant_id, actor, intent, now)
        return output(response, store.operation_dto(saved, now))


@router.post(BASE + "/{bulk_id}/actions")
async def batch_action(tenant_id: UUID, bulk_id: UUID, request: Request, response: Response,
                       actor: Actor = Depends(authenticate)):
    payload = await payload_after_owner(tenant_id, actor, request, schemas.BulkAction)
    async with store.owner_transaction(tenant_id, actor) as db:
        values = payload.model_dump(mode="json")
        existing = await store.existing_request(db, tenant_id, actor, payload.idempotency_key, "cancel", values, bulk_id=bulk_id)
        if existing:
            return output(response, store.operation_dto(existing, await store.clock(db), replayed=True))
        batch = await owned_batch(db, tenant_id, actor, bulk_id, lock=True)
        if batch["version"] != payload.expected_version:
            extra = {"rejection": {"reason": "stale_version", "idempotency_key": str(payload.idempotency_key),
                "kind": "cancel", "batch_id": str(bulk_id), "expected_version": payload.expected_version}} if payload.expected_version < batch["version"] else {}
            raise store.fail("BULK_CONFLICT", **extra)
        if batch["status"] not in {"pending", "running"} or batch["cancelled_at"] is not None:
            raise store.fail("BULK_CONFLICT")
        from app.subscription_bulk_runtime import cancel_bulk_pending
        await cancel_bulk_pending(db, batch, "BULK_CANCELLED")
        now = await store.clock(db)
        result = {"batch": await store.batch_item(db, tenant_id, bulk_id, actor, now)}
        saved = await store.save_request(db, tenant_id, actor, payload.idempotency_key, "cancel", values,
            bulk_id=bulk_id, result=result, now=now)
        return output(response, store.operation_dto(saved, now))


@router.get(BASE + "/{bulk_id}/recipients")
async def recipients(tenant_id: UUID, bulk_id: UUID, response: Response, actor: Actor = Depends(authenticate),
                     q: str = Query(default="", max_length=255), status: str = "",
                     page: int = Query(default=1, ge=1, le=10000)):
    validate_search(q, status)
    async with store.owner_transaction(tenant_id, actor) as db:
        batch = await owned_batch(db, tenant_id, actor, bulk_id)
        return output(response, await store.recipient_page(db, batch, await store.clock(db), q=q, status=status, page=page))


@router.get(BASE + "/{bulk_id}")
async def batch_detail(tenant_id: UUID, bulk_id: UUID, response: Response, actor: Actor = Depends(authenticate)):
    async with store.owner_transaction(tenant_id, actor) as db:
        batch = await owned_batch(db, tenant_id, actor, bulk_id)
        now = await store.clock(db)
        return output(response, {"batch": await store.batch_item(db, tenant_id, bulk_id, actor, now),
            "recipients": await store.recipient_page(db, batch, now)})


def asset_header_scope(request, device_id, generation, settings_revision, operation_id):
    try:
        actual = (UUID(request.headers["x-orion-device-id"]), UUID(request.headers["x-orion-device-generation"]),
            int(request.headers["x-orion-device-revision"]), UUID(request.headers["x-orion-device-effect"]))
        if actual != (device_id, generation, settings_revision, operation_id):
            raise ValueError()
    except (KeyError, ValueError, TypeError):
        raise store.fail("BULK_REVOKED", 401) from None


async def authorized_asset(request, asset_id, scope, variant):
    from app import subscription_devices as devices
    from app.subscription_bulk_runtime import bulk_effect_authority
    async with devices._private_transaction(request, device_id=scope[0]) as (db, device, _, owner, fence):
        effect = await devices._effect(db, device, scope[3])
        if (effect["type"] != "bulk_action" or effect["status"] != "running" or effect["attempts"] != 1
                or effect["claimed_owner"] != owner or effect["claimed_fence"] != fence
                or effect["generation"] != scope[1] or effect["settings_revision"] != scope[2]):
            raise store.fail("BULK_REVOKED")
        reason, _ = await bulk_effect_authority(db, device, effect, await store.clock(db))
        if reason:
            raise store.fail(reason)
        action = effect["payload"]["action"]
        if action["type"] not in {"image", "video", "audio", "document", "sticker", "ptv"}:
            raise store.fail("BULK_NOT_FOUND", 404)
        descriptor = action.get("asset")
        if not isinstance(descriptor, dict) or descriptor.get("id") != str(asset_id):
            raise store.fail("BULK_NOT_FOUND", 404)
        row = (await db.execute(text("""SELECT * FROM subscription_chatbot_assets
            WHERE tenant_id=:t AND id=:id AND status='ready'"""), {"t": device["tenant_id"], "id": asset_id})).mappings().one_or_none()
        if row is None or jsonable_encoder(store.shared.asset_descriptor(row)) != descriptor:
            raise store.fail("BULK_TEMPLATE_CHANGED")
        if variant == "thumbnail" and not row["thumbnail_size"]:
            raise store.fail("BULK_NOT_FOUND", 404)
        return device["tenant_id"], dict(row)


@router.get(PRIVATE + "/assets/{asset_id}", dependencies=[Depends(channels.enabled)])
async def bulk_asset(channel_id: UUID, asset_id: UUID, request: Request, device_id: UUID, generation: UUID,
                     operation_id: UUID, settings_revision: int = Query(ge=1, le=2**63-1), variant: str = "original"):
    if variant not in {"original", "thumbnail"}:
        raise store.fail("BULK_NOT_FOUND", 404)
    asset_header_scope(request, device_id, generation, settings_revision, operation_id)
    scope = (device_id, generation, settings_revision, operation_id)
    tenant, asset = await authorized_asset(request, asset_id, scope, variant)
    prefix = "thumbnail_" if variant == "thumbnail" else ""
    from app.subscription_chatbot_media import get_media
    data = await asyncio.to_thread(get_media, tenant, asset_id, expected_size=asset[prefix + "size"],
        expected_sha256=asset[prefix + "sha256"], variant=variant)
    _, current = await authorized_asset(request, asset_id, scope, variant)
    if current[prefix + "sha256"] != asset[prefix + "sha256"] or current[prefix + "size"] != asset[prefix + "size"]:
        raise store.fail("BULK_TEMPLATE_CHANGED")
    return Response(data, media_type=asset[prefix + "mime"],
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
