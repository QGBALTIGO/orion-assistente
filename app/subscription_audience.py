"""Public contacts and local groups for active subscription-business owners."""
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from starlette.responses import StreamingResponse

from app.catalog_api import Actor, authenticate
from app import subscription_audience_schemas as schemas
from app import subscription_audience_store as store
from app import subscription_audience_import as importer

BASE = "/api/v1/tenants/{tenant_id}/subscription-whatsapp/audience"
router = APIRouter(tags=["Público de serviços por assinatura"])


def output(response, value):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return jsonable_encoder(value)


async def payload_after_owner(tenant, actor, request, model):
    async with store.owner_transaction(tenant, actor):
        pass
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > schemas.MAX_BODY:
            raise store.fail("AUDIENCE_LIMIT", 413)
        raw.extend(chunk)
    try:
        return model.model_validate_json(bytes(raw))
    except (ValidationError, ValueError, UnicodeError):
        # Never echo validation input: it can contain a service password.
        raise store.fail("AUDIENCE_INVALID", 422) from None


def validated_key(value):
    try:
        return schemas.key(value)
    except ValueError:
        raise store.fail("AUDIENCE_NOT_FOUND", 404) from None


def group_filter(value):
    if value in {None, "", "none"}:
        return value or None
    try:
        return UUID(value)
    except ValueError:
        raise store.fail("AUDIENCE_INVALID", 422) from None


async def mutate(tenant, actor, payload, kind, work, *, resource=None):
    try:
        async with store.owner_transaction(tenant, actor) as db:
            return await store.operation(db, tenant, actor, payload, kind, lambda: work(db), resource=resource)
    except IntegrityError:
        # The complete transaction has rolled back; private SQL/values never
        # reach the client or a recovery receipt.
        raise store.fail("AUDIENCE_DUPLICATE") from None


@router.get(BASE + "/options")
async def options(tenant_id: UUID, response: Response, actor: Actor = Depends(authenticate)):
    async with store.owner_transaction(tenant_id, actor) as db:
        return output(response, {"groups": (await store.groups(db, tenant_id, page_size=schemas.MAX_GROUPS))["items"],
            "limits": {"contacts": schemas.MAX_CONTACTS, "groups": schemas.MAX_GROUPS,
                "members_per_group": schemas.MAX_GROUP_MEMBERS, "import_rows": schemas.MAX_IMPORT_ROWS,
                "import_bytes": schemas.MAX_IMPORT_BYTES, "page_size": schemas.MAX_PAGE_SIZE},
            "defaults": {"default_country": "BR", "chatbot_enabled": True, "audio_transcription_enabled": True}})


@router.get(BASE + "/contacts")
async def contacts(tenant_id: UUID, response: Response, actor: Actor = Depends(authenticate),
                   q: str = Query(default="", max_length=255), group_id: str | None = Query(default=None, max_length=36),
                   page: int = Query(default=1, ge=1, le=100000), page_size: int = Query(default=30, ge=1, le=100)):
    if "\x00" in q:
        raise store.fail("AUDIENCE_INVALID", 422)
    async with store.owner_transaction(tenant_id, actor) as db:
        return output(response, await store.contacts_page(db, tenant_id, q, group_filter(group_id), page, page_size))


@router.get(BASE + "/contacts/export")
@router.get(BASE + "/contacts/export.csv")
async def export_contacts(tenant_id: UUID, actor: Actor = Depends(authenticate),
                         q: str = Query(default="", max_length=255), group_id: str | None = Query(default=None, max_length=36)):
    if "\x00" in q:
        raise store.fail("AUDIENCE_INVALID", 422)
    async with store.owner_transaction(tenant_id, actor) as db:
        values = await store.contacts_page(db, tenant_id, q, group_filter(group_id), export=True)
    return StreamingResponse(importer.csv_lines(values["items"]), media_type="text/csv; charset=utf-8",
        headers={"Cache-Control": "no-store", "Pragma": "no-cache",
                 "Content-Disposition": 'attachment; filename="orion-contatos.csv"',
                 "X-Total-Rows": str(values["total"]), "X-Content-Type-Options": "nosniff"})


@router.get(BASE + "/contacts/{contact_key}")
async def contact_detail(tenant_id: UUID, contact_key: str, response: Response, actor: Actor = Depends(authenticate)):
    async with store.owner_transaction(tenant_id, actor) as db:
        return output(response, await store.contact_detail(db, tenant_id, validated_key(contact_key)))


@router.post(BASE + "/contacts", status_code=201)
async def create_contact(tenant_id: UUID, request: Request, response: Response, actor: Actor = Depends(authenticate)):
    payload = await payload_after_owner(tenant_id, actor, request, schemas.CreateContact)
    return output(response, await mutate(tenant_id, actor, payload, "contact_create",
        lambda db: store.save_contact(db, tenant_id, payload.contact)))


@router.patch(BASE + "/contacts/{contact_key}")
async def update_contact(tenant_id: UUID, contact_key: str, request: Request, response: Response, actor: Actor = Depends(authenticate)):
    payload = await payload_after_owner(tenant_id, actor, request, schemas.UpdateContact)
    key = validated_key(contact_key)
    return output(response, await mutate(tenant_id, actor, payload, "contact_update",
        lambda db: store.save_contact(db, tenant_id, payload.contact, old_key=key, expected_version=payload.expected_version), resource=key))


@router.delete(BASE + "/contacts/{contact_key}")
async def archive_contact(tenant_id: UUID, contact_key: str, request: Request, response: Response, actor: Actor = Depends(authenticate)):
    payload = await payload_after_owner(tenant_id, actor, request, schemas.ArchiveContact)
    key = validated_key(contact_key)
    return output(response, await mutate(tenant_id, actor, payload, "contact_archive",
        lambda db: store.archive_contact(db, tenant_id, key, payload.expected_version), resource=key))


@router.get(BASE + "/groups")
async def groups(tenant_id: UUID, response: Response, actor: Actor = Depends(authenticate),
                 q: str = Query(default="", max_length=255), page: int = Query(default=1, ge=1, le=10000),
                 page_size: int = Query(default=30, ge=1, le=100)):
    if "\x00" in q:
        raise store.fail("AUDIENCE_INVALID", 422)
    async with store.owner_transaction(tenant_id, actor) as db:
        return output(response, await store.groups(db, tenant_id, q=q, page=page, page_size=page_size))


@router.get(BASE + "/groups/{group_id}")
async def group_detail(tenant_id: UUID, group_id: UUID, response: Response, actor: Actor = Depends(authenticate)):
    async with store.owner_transaction(tenant_id, actor) as db:
        return output(response, await store.group_detail(db, tenant_id, group_id))


@router.post(BASE + "/groups", status_code=201)
async def create_group(tenant_id: UUID, request: Request, response: Response, actor: Actor = Depends(authenticate)):
    payload = await payload_after_owner(tenant_id, actor, request, schemas.CreateGroup)
    return output(response, await mutate(tenant_id, actor, payload, "group_create",
        lambda db: store.save_group(db, tenant_id, payload.name, payload.contact_keys)))


@router.patch(BASE + "/groups/{group_id}")
async def update_group(tenant_id: UUID, group_id: UUID, request: Request, response: Response, actor: Actor = Depends(authenticate)):
    payload = await payload_after_owner(tenant_id, actor, request, schemas.UpdateGroup)
    return output(response, await mutate(tenant_id, actor, payload, "group_update",
        lambda db: store.save_group(db, tenant_id, payload.name, payload.contact_keys, identity=group_id,
                                   expected_version=payload.expected_version), resource=str(group_id)))


@router.delete(BASE + "/groups/{group_id}")
async def archive_group(tenant_id: UUID, group_id: UUID, request: Request, response: Response, actor: Actor = Depends(authenticate)):
    payload = await payload_after_owner(tenant_id, actor, request, schemas.ArchiveGroup)
    return output(response, await mutate(tenant_id, actor, payload, "group_archive",
        lambda db: store.archive_group(db, tenant_id, group_id, payload.expected_version), resource=str(group_id)))


@router.post(BASE + "/imports/preview")
async def preview_import(tenant_id: UUID, request: Request, response: Response, actor: Actor = Depends(authenticate)):
    payload = await payload_after_owner(tenant_id, actor, request, schemas.ImportPreview)
    async with store.owner_transaction(tenant_id, actor) as db:
        try:
            return output(response, await store.prepare_import(db, tenant_id, actor, payload))
        except ValueError as exc:
            raise store.fail("AUDIENCE_INVALID", 422, str(exc)) from None


@router.post(BASE + "/imports/commit")
async def commit_import(tenant_id: UUID, request: Request, response: Response, actor: Actor = Depends(authenticate)):
    payload = await payload_after_owner(tenant_id, actor, request, schemas.ImportCommit)
    return output(response, await mutate(tenant_id, actor, payload, "import",
        lambda db: store.commit_import(db, tenant_id, actor, payload)))


@router.get(BASE + "/requests/{key}")
async def recover_request(tenant_id: UUID, key: UUID, response: Response, actor: Actor = Depends(authenticate)):
    async with store.owner_transaction(tenant_id, actor) as db:
        row = (await db.execute(text("""SELECT id,actor_account_id,idempotency_key,result
            FROM subscription_audience_operations WHERE tenant_id=:t AND idempotency_key=:key"""),
            {"t": tenant_id, "key": key})).mappings().one_or_none()
        if not row or row["actor_account_id"] != actor.account_id:
            raise store.fail("AUDIENCE_NOT_FOUND", 404)
        return output(response, {"request_id": row["id"], "idempotency_key": row["idempotency_key"],
                                 "replayed": True, "result": row["result"]})
