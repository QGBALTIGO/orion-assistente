"""Read previews, explicitly create membership batches, and expose scoped progress."""
import asyncio
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from pydantic import ValidationError
from sqlalchemy import text

from app.catalog_api import Actor, authenticate
from app import subscription_group_add_schemas as schemas
from app import subscription_group_add_store as store

BASE = "/api/v1/tenants/{tenant_id}/subscription-whatsapp/group-add"
INTERNAL = "/internal/qr/channels/{channel_id}/device/group-add"
router = APIRouter(tags=["Adicionar contatos ao grupo"])


def output(response, value):
    response.headers["Cache-Control"] = "no-store"
    return jsonable_encoder(value)


async def body_after_owner(tenant, actor, request, model):
    async with store.owner_transaction(tenant, actor):
        pass
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > schemas.MAX_BODY:
            raise store.fail("GROUP_ADD_LIMIT", 413)
        raw.extend(chunk)
    try:
        return model.model_validate_json(bytes(raw))
    except (ValueError, TypeError, ValidationError):
        raise store.fail("GROUP_ADD_INVALID", 422) from None


async def bridge_read(transport, device, group_id=None):
    payload = store.contacts.transport_scope(device)
    if group_id is not None:
        payload["group_id"] = group_id
    model = schemas.BridgeGroups if group_id is None else schemas.BridgeMembers
    try:
        async with asyncio.timeout(35):
            raw = await store.channels._bridge_control(transport,
                "device/group-add/" + ("groups" if group_id is None else "members"), "POST", payload)
        store.bounded(raw, schemas.BRIDGE_BYTES)
        value = model.model_validate(raw)
    except HTTPException as exc:
        reason = exc.detail.get("group_read_reason") if isinstance(exc.detail, dict) else None
        if reason in {"device_unavailable", "context_changed"}:
            raise store.fail("GROUP_ADD_DEVICE_UNAVAILABLE") from None
        if reason in {"device_busy", "read_busy"}:
            raise store.fail("GROUP_ADD_READ_BUSY") from None
        if reason in {"deadline_exceeded", "provider_timeout"}:
            raise store.fail("GROUP_ADD_BRIDGE_TIMEOUT", 504) from None
        if reason == "invalid_metadata":
            raise store.fail("GROUP_ADD_PROVIDER_DATA_UNAVAILABLE", 503) from None
        raise store.fail("GROUP_ADD_BRIDGE_UNAVAILABLE", 503) from None
    except TimeoutError:
        raise store.fail("GROUP_ADD_BRIDGE_TIMEOUT", 504) from None
    except (ValueError, TypeError, UnicodeError, ValidationError):
        raise store.fail("GROUP_ADD_BRIDGE_UNAVAILABLE", 503) from None
    if (value.device_id != device["id"] or value.generation != device["generation"]
            or value.settings_revision != device["revision"] or group_id is not None and value.group.id != group_id):
        raise store.fail("GROUP_ADD_CONTEXT_CHANGED")
    return value


@router.get(BASE + "/options")
async def options(tenant_id: UUID, response: Response, actor: Actor = Depends(authenticate)):
    async with store.owner_transaction(tenant_id, actor) as db:
        zone = (await db.execute(text("SELECT timezone FROM tenants WHERE id=:t"), {"t": tenant_id})).scalar_one()
        return output(response, {"devices": await store.device_options(db, tenant_id, actor),
            "groups": await store.campaigns.group_summaries(db, tenant_id), "timezone": zone,
            "limits": {"recipients": schemas.MAX_MEMBERS, "preview_seconds": schemas.PREVIEW_SECONDS,
                "interval_seconds": schemas.INTERVAL_SECONDS, "search_utf16": 255,
                "preview_token_characters": schemas.MAX_TOKEN}, "generated_at": await store.clock(db)})


@router.get(BASE + "/groups")
async def groups(tenant_id: UUID, device_id: UUID, response: Response, actor: Actor = Depends(authenticate)):
    async with store.owner_transaction(tenant_id, actor) as db:
        device, transport = await store.scope(db, tenant_id, device_id, actor)
    value = await bridge_read(transport, device)
    async with store.owner_transaction(tenant_id, actor) as db:
        current, _ = await store.scope(db, tenant_id, device_id, actor)
        if store.contacts.device_version(current) != store.contacts.device_version(device):
            raise store.fail("GROUP_ADD_CONTEXT_CHANGED")
    return output(response, {"device": store.contacts.public_device(current),
        "items": [{**group.model_dump(mode="json"), "blocked_reason": None if group.can_add else
                   store.ERRORS["GROUP_ADD_ADMIN_REQUIRED"]} for group in value.groups], "total": len(value.groups),
        **({"unavailable_groups": value.unavailable_groups} if value.unavailable_groups else {})})


async def prepare_preview(tenant, actor, payload, *, issue_token=True):
    async with store.owner_transaction(tenant, actor) as db:
        device, transport = await store.scope(db, tenant, payload.device_id, actor)
        initial = await store.local_contacts(db, tenant, payload) if payload.source == "contacts" else None
    destination = await bridge_read(transport, device, payload.destination_group_id) if payload.destination_group_id else None
    source = None
    if payload.source == "group":
        source = destination if payload.source_group_id == payload.destination_group_id else await bridge_read(
            transport, device, payload.source_group_id)
    async with store.owner_transaction(tenant, actor) as db:
        current, _ = await store.scope(db, tenant, payload.device_id, actor)
        if store.contacts.device_version(current) != store.contacts.device_version(device):
            raise store.fail("GROUP_ADD_CONTEXT_CHANGED")
        if payload.source == "contacts":
            rows, duplicates = await store.local_contacts(db, tenant, payload)
            if store.fingerprint(rows) != store.fingerprint(initial[0]):
                raise store.fail("GROUP_ADD_SOURCE_CHANGED")
            unavailable = 0
        else:
            rows = sorted(await store.remote_contacts(db, tenant, payload, source), key=lambda row: row["phone"])
            duplicates, unavailable = source.duplicate_count, source.unavailable_count
        now = await store.clock(db)
        unresolved = await store.unresolved_phones(db, tenant, payload.destination_group_id)
    existing = {member.phone for member in destination.members} if destination else set()
    members = [{"phone": row["phone"], "name": row["name"],
        "status": "blocked" if row["blocked"] else "already_member" if row["phone"] in existing else
            "blocked" if row["phone"] in unresolved else "ready",
        "blocked_reason": store.ERRORS["GROUP_ADD_OPTED_OUT"] if row["blocked"] else
            "Já está no grupo." if row["phone"] in existing else store.ERRORS["GROUP_ADD_OUTCOME_UNKNOWN"]
            if row["phone"] in unresolved else None} for row in rows]
    ready = [member["phone"] for member in members if member["status"] == "ready"]
    token, expiry = None, None
    if issue_token and destination and destination.is_admin and ready:
        token, expiry = store.create_preview(tenant, actor, current, payload, rows, ready, now)
    public = {"device": store.contacts.public_device(current),
        "destination": {**destination.group.model_dump(mode="json"), "is_admin": destination.is_admin,
            "blocked_reason": None if destination.is_admin else store.ERRORS["GROUP_ADD_ADMIN_REQUIRED"]} if destination else None,
        "source": payload.source, "filters": store.filters(payload),
        "source_group": source.group.model_dump(mode="json") if source else None,
        "members": members, "counts": {"total": len(members), "ready": len(ready),
            "already_member": sum(member["status"] == "already_member" for member in members),
            "blocked": sum(member["status"] == "blocked" for member in members),
            "unavailable": unavailable, "duplicates": duplicates},
        "preview_token": token, "expires_at": expiry, "generated_at": now}
    store.bounded(public, schemas.MAX_BODY)
    return public, rows, current, destination


@router.post(BASE + "/preview")
async def preview(tenant_id: UUID, request: Request, response: Response, actor: Actor = Depends(authenticate)):
    payload = await body_after_owner(tenant_id, actor, request, schemas.PreviewRequest)
    public, _, _, _ = await prepare_preview(tenant_id, actor, payload)
    return output(response, public)


@router.post(BASE + "/batches")
async def create_batch(tenant_id: UUID, request: Request, response: Response, actor: Actor = Depends(authenticate)):
    payload = await body_after_owner(tenant_id, actor, request, schemas.CreateBatch)
    fingerprint = store.fingerprint(payload.model_dump(mode="json"))
    async with store.owner_transaction(tenant_id, actor) as db:
        prior = await store.existing(db, tenant_id, actor, payload.idempotency_key, fingerprint)
        if prior:
            return output(response, await store.response(db, prior, replayed=True))
        seal = store.decode_preview(payload.preview_token, tenant_id, actor, await store.clock(db))
        device, _ = await store.scope(db, tenant_id, seal.request.device_id, actor)
        store.validate_device(seal, device)
        if not set(payload.phones) <= set(seal.phones):
            raise store.fail("GROUP_ADD_INVALID", 422)
    _, rows, _, destination = await prepare_preview(tenant_id, actor, seal.request, issue_token=False)
    if not destination or not destination.is_admin:
        raise store.fail("GROUP_ADD_ADMIN_REQUIRED")
    async with store.owner_transaction(tenant_id, actor) as db:
        prior = await store.existing(db, tenant_id, actor, payload.idempotency_key, fingerprint)
        if prior:
            return output(response, await store.response(db, prior, replayed=True))
        device, _ = await store.scope(db, tenant_id, seal.request.device_id, actor)
        # The source may change while the private destination read is finishing.
        if seal.request.source == "contacts":
            rows, _ = await store.local_contacts(db, tenant_id, seal.request)
        else:
            current_permissions = await store.permissions(db, tenant_id, [row["phone"] for row in rows])
            for row in rows:
                permission = current_permissions.get(row["phone"], {})
                row.update(permission_version=permission.get("version", 0), blocked=permission.get("revoked", False))
        return output(response, await store.persist(db, tenant_id, actor, payload, seal, device,
                                                   destination.group, rows, await store.clock(db)))


@router.get(BASE + "/requests/{key}")
async def recover(tenant_id: UUID, key: UUID, response: Response, actor: Actor = Depends(authenticate)):
    async with store.owner_transaction(tenant_id, actor) as db:
        row = await store.existing(db, tenant_id, actor, key)
        if row is None:
            raise store.fail("GROUP_ADD_NOT_FOUND", 404)
        return output(response, await store.response(db, row, replayed=True))


@router.get(BASE + "/batches/{batch_id}")
async def detail(tenant_id: UUID, batch_id: UUID, response: Response, actor: Actor = Depends(authenticate)):
    async with store.owner_transaction(tenant_id, actor) as db:
        row = await store.batch_row(db, tenant_id, actor, batch_id)
        return output(response, await store.response(db, row))


async def private_item(db, device, payload):
    item = (await db.execute(text("""SELECT * FROM subscription_group_add_items
        WHERE tenant_id=:t AND id=:id FOR UPDATE"""),
        {"t": device["tenant_id"], "id": payload.operation_id})).mappings().one_or_none()
    if not item:
        raise store.fail("GROUP_ADD_NOT_FOUND", 404)
    batch = (await db.execute(text("SELECT * FROM subscription_group_add_batches WHERE tenant_id=:t AND id=:id"),
        {"t": device["tenant_id"], "id": item["batch_id"]})).mappings().one()
    if (item["device_id"] != payload.device_id or device["id"] != payload.device_id
            or item["generation"] != payload.generation or device["generation"] != payload.generation
            or item["settings_revision"] != payload.settings_revision or item["claim_token"] != payload.claim_token
            or item["phone"] != payload.phone or batch["destination_group_id"] != payload.group_id
            or batch["channel_id"] != device["channel_id"] or batch["owner_account_id"] != device["owner_account_id"]):
        raise store.fail("GROUP_ADD_CONTEXT_CHANGED")
    return dict(batch), dict(item)


@router.post(INTERNAL + "/authorize", include_in_schema=False)
async def authorize(channel_id: UUID, payload: schemas.PrivateAuthorize, request: Request):
    async with store.devices._private_transaction(request, device_id=payload.device_id) as (db, device, transport, owner, fence):
        batch, item = await private_item(db, device, payload)
        now = await store.clock(db)
        denied = {"authorized": False, "operation_id": str(payload.operation_id)}
        if (item["status"] != "running" or batch["status"] != "running" or item["dispatch_at"] is None
                or (now - item["dispatch_at"]).total_seconds() >= 30
                or item["claimed_owner"] is not None and (item["claimed_owner"] != owner or item["claimed_fence"] != fence)):
            return denied
        try:
            await store.owner_authority(db, batch)
            await store.check_item_source(db, batch, item)
        except HTTPException:
            return denied
        await db.execute(text("""UPDATE subscription_group_add_items SET claimed_owner=:owner,claimed_fence=:fence
            WHERE tenant_id=:t AND id=:id AND status='running'"""),
            {"owner": owner, "fence": fence, "t": batch["tenant_id"], "id": item["id"]})
        expires = max(0, min(30000, int((transport["lease_expires_at"] - now).total_seconds() * 1000),
            int((30 - (now - item["dispatch_at"]).total_seconds()) * 1000)))
        if expires <= 0:
            return denied
        return jsonable_encoder({"authorized": True, "channel_id": channel_id, "tenant_id": batch["tenant_id"],
            "owner": owner, "fence": fence, **payload.model_dump(exclude={"recheck"}), "expires_in_ms": expires})


@router.post(INTERNAL + "/receipt", include_in_schema=False)
async def receipt(channel_id: UUID, payload: schemas.PrivateOperation, request: Request):
    async with store.devices._private_transaction(request, live=False, device_id=payload.device_id,
            allow_removed=True) as (db, device, _, _, _):
        batch, item = await private_item(db, device, payload)
        if item["dispatch_at"] is None or item["status"] == "pending":
            return {"authorized": False, "operation_id": str(payload.operation_id)}
        return jsonable_encoder({"authorized": True, "read_only": True,
            "channel_id": channel_id, "tenant_id": batch["tenant_id"],
            **payload.model_dump(), "expires_in_ms": 30000})
