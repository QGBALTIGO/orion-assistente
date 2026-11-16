"""Reviewed direct sends for subscription businesses; no implicit template."""
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from pydantic import ValidationError
from sqlalchemy import text

from app.catalog_api import Actor, authenticate
from app import subscription_direct_schemas as schemas
from app import subscription_direct_store as store
from app import subscription_url_button_transport as url_button_transport

BASE = "/api/v1/tenants/{tenant_id}/subscription-whatsapp/direct-messages"
router = APIRouter(tags=["Enviar Mensagem de assinaturas"])


def output(response, value):
    response.headers["Cache-Control"] = "no-store"
    return jsonable_encoder(value)


async def payload_after_owner(tenant, actor, request, model):
    async with store.owner_transaction(tenant, actor):
        pass
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > schemas.MAX_BODY:
            raise store.fail("DIRECT_LIMIT", 413)
        raw.extend(chunk)
    try:
        return model.model_validate_json(bytes(raw))
    except (ValidationError, ValueError):
        raise store.fail("DIRECT_INVALID", 422) from None


@router.get(BASE + "/options")
async def options(tenant_id: UUID, response: Response, actor: Actor = Depends(authenticate)):
    async with store.owner_transaction(tenant_id, actor) as db:
        return output(response, {"devices": await store.bulk.campaigns.device_options(db, tenant_id, actor),
            "templates": await store.bulk.campaigns.template_options(db, tenant_id),
            "timezone": await store.timezone_for(db, tenant_id), "generated_at": await store.clock(db),
            "chatbot_revision": await store.shared.revision(db, tenant_id),
            "capabilities": store.library.capabilities(), "limits": {**store.library.limits(),
                "contacts": schemas.MAX_CONTACTS, "caption_utf16": 1000, "review_seconds": schemas.REVIEW_SECONDS}})


@router.get(BASE + "/contacts")
async def contacts(tenant_id: UUID, response: Response, actor: Actor = Depends(authenticate),
                   q: str = Query(default="", max_length=255), page: int = Query(default=1, ge=1, le=10000)):
    if "\x00" in q:
        raise store.fail("DIRECT_INVALID", 422)
    async with store.owner_transaction(tenant_id, actor) as db:
        return output(response, await store.bulk.contacts_page(db, tenant_id, q, page,
            await store.clock(db), await store.timezone_for(db, tenant_id)))


@router.get(BASE + "/requests/{key}")
async def recover_request(tenant_id: UUID, key: UUID, response: Response, actor: Actor = Depends(authenticate)):
    async with store.owner_transaction(tenant_id, actor) as db:
        row = await store.request_row(db, tenant_id, actor, key)
        if row is None:
            raise store.fail("DIRECT_NOT_FOUND", 404)
        return output(response, store.operation_dto(row, await store.clock(db), replayed=True))


@router.post(BASE + "/review", status_code=201)
async def review_direct(tenant_id: UUID, request: Request, response: Response, actor: Actor = Depends(authenticate)):
    payload = await payload_after_owner(tenant_id, actor, request, schemas.ReviewDirectMessage)
    values = payload.model_dump(mode="json")
    proof = None
    if values["message"]["kind"] == "url_button":
        async with store.owner_transaction(tenant_id, actor) as db:
            existing = await store.existing_request(db, tenant_id, actor, payload.idempotency_key, "review", values)
            if existing:
                return output(response, store.operation_dto(existing, await store.clock(db), replayed=True))
            expected = await url_button_transport.snapshot(db, tenant_id, actor, values["message"]["device_id"])
        proof = await url_button_transport.compatible(expected)
    async with store.owner_transaction(tenant_id, actor) as db:
        existing = await store.existing_request(db, tenant_id, actor, payload.idempotency_key, "review", values)
        if existing:
            return output(response, store.operation_dto(existing, await store.clock(db), replayed=True))
        if proof is not None:
            await url_button_transport.revalidate(db, tenant_id, actor, values["message"]["device_id"], proof)
        snapshot = await store.capture_review(db, tenant_id, actor, values["message"], payload.idempotency_key)
        identity, now = uuid4(), await store.clock(db)
        snapshot["review_request_id"] = str(identity)
        preview = store.review_preview(snapshot, identity, now)
        saved = await store.bulk.save_request(db, tenant_id, actor, payload.idempotency_key, "review", values,
            snapshot=snapshot, preview=preview, identity=identity, now=now, origin=store.ORIGIN)
        return output(response, store.operation_dto(saved, now))


async def persist_confirmation(db, tenant, actor, intent, now):
    snapshot, identity = intent["snapshot"], uuid4()
    saved_template = await store.library.create_in_transaction(db, tenant, snapshot["save_template"]) if (
        snapshot["save_template"] is not None) else None
    await db.execute(text("""INSERT INTO subscription_bulk_batches
        (id,tenant_id,owner_account_id,run_version,interval_seconds,snapshot,confirmed_at,next_admission_at,origin)
        VALUES(:id,:t,:a,:run,10,CAST(:snapshot AS jsonb),:now,:now,'direct')"""),
        {"id": identity, "t": tenant, "a": actor.account_id, "run": intent["review_version"], "now": now,
         "snapshot": store.encode(store.bulk.metadata_snapshot(snapshot, actor, intent["id"], now))})
    await store.bulk.insert_recipients(db, tenant, identity, intent["review_version"], snapshot, now)
    result = {"send": await store.send_item(db, tenant, identity, actor, now), "saved_template": saved_template}
    return await store.bulk.complete_request(db, tenant, intent["id"], result, now, bulk_id=identity)


@router.post(BASE + "/confirm", status_code=202)
async def confirm_direct(tenant_id: UUID, request: Request, response: Response, actor: Actor = Depends(authenticate)):
    payload = await payload_after_owner(tenant_id, actor, request, schemas.ConfirmDirectMessage)
    async with store.owner_transaction(tenant_id, actor) as db:
        intent, now = await confirmation_intent(db, tenant_id, actor, payload)
        if intent["status"] == "succeeded":
            return output(response, store.operation_dto(intent, now, replayed=True))
        message = intent["snapshot"]["message"]
        if message["kind"] != "url_button":
            return output(response, await complete_confirmation(db, tenant_id, actor, payload, intent, now))
        expected = await url_button_transport.snapshot(db, tenant_id, actor, message["device_id"])
    proof = await url_button_transport.compatible(expected)
    async with store.owner_transaction(tenant_id, actor) as db:
        intent, now = await confirmation_intent(db, tenant_id, actor, payload)
        if intent["status"] == "succeeded":
            return output(response, store.operation_dto(intent, now, replayed=True))
        message = intent["snapshot"]["message"]
        if message["kind"] != "url_button":
            raise store.fail("DIRECT_CONFLICT")
        await url_button_transport.revalidate(db, tenant_id, actor, message["device_id"], proof)
        return output(response, await complete_confirmation(db, tenant_id, actor, payload, intent, now))


async def confirmation_intent(db, tenant, actor, payload):
    intent = await store.request_row(db, tenant, actor, payload.idempotency_key)
    if intent is None:
        raise store.fail("DIRECT_NOT_FOUND", 404)
    if intent["kind"] != "review" or intent["review_version"] != payload.review_version:
        raise store.fail("DIRECT_IDEMPOTENCY_CONFLICT")
    now = await store.clock(db)
    if intent["status"] != "succeeded" and intent["expires_at"] <= now:
        raise store.fail("DIRECT_REVIEW_EXPIRED", 410)
    return intent, now


async def complete_confirmation(db, tenant, actor, payload, intent, now):
    current = await store.capture_review(db, tenant, actor, intent["snapshot"]["message"], payload.idempotency_key)
    current["review_request_id"] = str(intent["id"])
    if store.fingerprint(current) != intent["review_version"]:
        raise store.fail("DIRECT_CONFLICT")
    if not intent["snapshot"]["counts"]["eligible"]:
        raise store.fail("DIRECT_NO_ELIGIBLE")
    saved = await persist_confirmation(db, tenant, actor, intent, now)
    return store.operation_dto(saved, now)


@router.post(BASE + "/{send_id}/actions")
async def send_action(tenant_id: UUID, send_id: UUID, request: Request, response: Response,
                      actor: Actor = Depends(authenticate)):
    payload = await payload_after_owner(tenant_id, actor, request, schemas.DirectAction)
    async with store.owner_transaction(tenant_id, actor) as db:
        values = payload.model_dump(mode="json")
        existing = await store.existing_request(db, tenant_id, actor, payload.idempotency_key, "cancel", values, send_id=send_id)
        if existing:
            return output(response, store.operation_dto(existing, await store.clock(db), replayed=True))
        send = await store.owned_send(db, tenant_id, actor, send_id, lock=True)
        if send["version"] != payload.expected_version:
            extra = {"rejection": {"reason": "stale_version", "idempotency_key": str(payload.idempotency_key),
                "kind": "cancel", "send_id": str(send_id), "expected_version": payload.expected_version}} if (
                    payload.expected_version < send["version"]) else {}
            raise store.fail("DIRECT_CONFLICT", **extra)
        if send["status"] not in {"pending", "running"} or send["cancelled_at"] is not None:
            raise store.fail("DIRECT_CONFLICT")
        from app.subscription_bulk_runtime import cancel_bulk_pending
        await cancel_bulk_pending(db, send, "BULK_CANCELLED")
        now = await store.clock(db)
        result = {"send": await store.send_item(db, tenant_id, send_id, actor, now), "saved_template": None}
        saved = await store.bulk.save_request(db, tenant_id, actor, payload.idempotency_key, "cancel", values,
            bulk_id=send_id, result=result, now=now, origin=store.ORIGIN)
        return output(response, store.operation_dto(saved, now))


@router.get(BASE + "/{send_id}")
async def send_detail(tenant_id: UUID, send_id: UUID, response: Response, actor: Actor = Depends(authenticate)):
    async with store.owner_transaction(tenant_id, actor) as db:
        send = await store.owned_send(db, tenant_id, actor, send_id)
        now = await store.clock(db)
        return output(response, {"send": await store.send_item(db, tenant_id, send_id, actor, now),
            "recipients": await store.recipients_page(db, send, now)})
