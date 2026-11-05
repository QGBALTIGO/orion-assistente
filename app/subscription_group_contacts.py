"""Explicit reads of existing WhatsApp groups and owner-confirmed local imports."""
import asyncio
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from pydantic import ValidationError

from app.catalog_api import Actor, authenticate
from app import subscription_group_contacts_schemas as schemas
from app import subscription_group_contacts_store as store

BASE = "/api/v1/tenants/{tenant_id}/subscription-whatsapp/group-contacts"
router = APIRouter(tags=["Contatos de grupos de assinaturas"])


def output(response, value):
    response.headers["Cache-Control"] = "no-store"
    return jsonable_encoder(value)


async def payload_after_owner(tenant, actor, request):
    async with store.owner_transaction(tenant, actor):
        pass
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > schemas.MAX_BODY:
            raise store.fail("GROUP_CONTACTS_LIMIT", 413)
        raw.extend(chunk)
    try:
        return schemas.ImportContacts.model_validate_json(bytes(raw))
    except (ValidationError, ValueError):
        raise store.fail("GROUP_CONTACTS_INVALID", 422) from None


@router.get(BASE + "/options")
async def options(tenant_id: UUID, response: Response, actor: Actor = Depends(authenticate)):
    async with store.owner_transaction(tenant_id, actor) as db:
        return output(response, {"devices": await store.device_options(db, tenant_id, actor),
            "groups": await store.campaigns.group_summaries(db, tenant_id),
            "limits": {"groups": schemas.MAX_GROUPS, "members_per_group": schemas.MAX_MEMBERS,
                "remote_groups": schemas.MAX_REMOTE_GROUPS, "preview_seconds": schemas.PREVIEW_SECONDS,
                "group_name_utf16": 100}, "generated_at": await store.clock(db)})


async def read_groups(tenant, actor, identity, *, selected_group=None):
    async with store.owner_transaction(tenant, actor) as db:
        original, transport = await store.scope(db, tenant, identity, actor)
        if selected_group is not None:
            try:
                selected_group = schemas.group_id(selected_group)
            except ValueError:
                raise store.fail("GROUP_CONTACTS_INVALID", 422) from None
        payload = store.transport_scope(original)
        if selected_group is not None:
            payload["group_id"] = selected_group
    # No tenant lock is held while the private existing session reads WhatsApp.
    action = "members" if selected_group is not None else "groups"
    model = schemas.BridgeMembers if selected_group is not None else schemas.BridgeGroups
    try:
        async with asyncio.timeout(15):
            raw = await store.channels._bridge_control(transport, "device/group-contacts/" + action, "POST", payload)
        if len(store.encode(raw).encode("utf-8")) > schemas.BRIDGE_BYTES:
            raise ValueError()
        result = model.model_validate(raw)
    except (HTTPException, TimeoutError, ValueError, TypeError, UnicodeError, ValidationError):
        raise store.fail("GROUP_CONTACTS_BRIDGE_UNAVAILABLE", 503) from None
    if (str(result.device_id) != payload["device_id"] or str(result.generation) != payload["generation"]
            or result.settings_revision != payload["settings_revision"]
            or selected_group is not None and result.group.id != selected_group):
        raise store.fail("GROUP_CONTACTS_CONTEXT_CHANGED")
    async with store.owner_transaction(tenant, actor) as db:
        current, _ = await store.scope(db, tenant, identity, actor)
        if store.device_version(current) != store.device_version(original):
            raise store.fail("GROUP_CONTACTS_CONTEXT_CHANGED")
        device = store.public_device(current)
        if selected_group is None:
            return {"device": device, "items": [item.model_dump(mode="json") for item in result.groups],
                    "total": len(result.groups)}
        token, expires = store.create_preview(tenant, actor, current, result, await store.clock(db))
        return {"preview_token": token, "expires_at": expires, "device": device,
                **result.model_dump(mode="json", exclude={"device_id", "generation", "settings_revision"})}


@router.get(BASE + "/groups")
async def groups(tenant_id: UUID, response: Response, device_id: UUID, actor: Actor = Depends(authenticate)):
    return output(response, await read_groups(tenant_id, actor, device_id))


@router.get(BASE + "/members")
async def members(tenant_id: UUID, response: Response, device_id: UUID,
                  group_id: str = Query(min_length=1, max_length=47), actor: Actor = Depends(authenticate)):
    return output(response, await read_groups(tenant_id, actor, device_id, selected_group=group_id))


@router.post(BASE + "/import")
async def import_contacts(tenant_id: UUID, request: Request, response: Response, actor: Actor = Depends(authenticate)):
    payload = await payload_after_owner(tenant_id, actor, request)
    async with store.owner_transaction(tenant_id, actor) as db:
        return output(response, await store.import_contacts(db, tenant_id, actor, payload))


@router.get(BASE + "/requests/{key}")
async def recover_request(tenant_id: UUID, key: UUID, response: Response, actor: Actor = Depends(authenticate)):
    async with store.owner_transaction(tenant_id, actor) as db:
        row = await store.request_row(db, tenant_id, actor, key)
        if row is None:
            raise store.fail("GROUP_CONTACTS_NOT_FOUND", 404)
        return output(response, store.operation_dto(row, await store.clock(db), replayed=True))
