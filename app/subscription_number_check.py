"""Read-only checks through the owner's existing WhatsApp session."""
import asyncio
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.encoders import jsonable_encoder
from pydantic import ValidationError
from sqlalchemy import text

from app.catalog_api import Actor, authenticate
from app import qr_channels as channels
from app import subscription_chatbot_store as shared
from app import subscription_campaign_store as campaigns
from app import subscription_group_contacts_store as devices
from app import subscription_number_check_schemas as schemas

BASE = "/api/v1/tenants/{tenant_id}/subscription-whatsapp/number-check"
FEATURE = "number_check.manage"
router = APIRouter(tags=["Verificador de números WhatsApp"])


def fail(code, status=409, **extra):
    messages = {
        "NUMBER_CHECK_INVALID": "Confira o dispositivo e informe números ou um grupo de contatos.",
        "NUMBER_CHECK_LIMIT": "Verifique até 1.000 números por consulta. Divida a lista para continuar.",
        "NUMBER_CHECK_EMPTY": "O grupo selecionado não contém números para verificar.",
        "NUMBER_CHECK_NOT_FOUND": "O grupo não foi encontrado nesta empresa.",
        "NUMBER_CHECK_DEVICE_UNAVAILABLE": "Conecte o dispositivo do WhatsApp para verificar os números.",
        "NUMBER_CHECK_CONTEXT_CHANGED": "A conexão mudou durante a consulta. Atualize e tente novamente.",
        "NUMBER_CHECK_UNAVAILABLE": "O WhatsApp não concluiu a consulta. Nenhum número foi marcado como inexistente.",
        "NUMBER_CHECK_BUSY": "Há uma verificação em andamento neste dispositivo. Aguarde a conclusão.",
    }
    return HTTPException(status, {"code": code, "message": messages[code], **extra},
                         headers={"Cache-Control": "no-store"})


def owner_transaction(tenant, actor):
    return shared.owner_transaction(tenant, actor, feature=FEATURE)


def output(response, value):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return jsonable_encoder(value)


@router.get(BASE + "/options")
async def options(tenant_id: UUID, response: Response, actor: Actor = Depends(authenticate)):
    async with owner_transaction(tenant_id, actor) as db:
        return output(response, {"devices": await devices.device_options(db, tenant_id, actor),
            "groups": await campaigns.group_summaries(db, tenant_id),
            "limits": {"numbers_per_check": schemas.MAX_NUMBERS}})


async def read_payload(tenant, actor, request):
    async with owner_transaction(tenant, actor):
        pass
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > schemas.MAX_BODY:
            raise fail("NUMBER_CHECK_LIMIT", 413)
        raw.extend(chunk)
    try:
        return schemas.CheckRequest.model_validate_json(bytes(raw))
    except (ValidationError, ValueError, UnicodeError):
        raise fail("NUMBER_CHECK_INVALID", 422) from None


async def group_numbers(db, tenant, identity):
    row = await campaigns.group_row(db, tenant, identity)
    if row["archived_at"] is not None:
        raise fail("NUMBER_CHECK_NOT_FOUND", 404)
    values = (await db.execute(text("""SELECT phone FROM subscription_campaign_members
        WHERE tenant_id=:tenant AND group_id=:group AND removed_at IS NULL
        ORDER BY phone LIMIT :limit"""),
        {"tenant": tenant, "group": identity, "limit": schemas.MAX_NUMBERS + 1})).scalars().all()
    if len(values) > schemas.MAX_NUMBERS:
        raise fail("NUMBER_CHECK_LIMIT", 422, limit=schemas.MAX_NUMBERS)
    if not values:
        raise fail("NUMBER_CHECK_EMPTY", 422)
    return ["+" + value.removeprefix("+") for value in values]


async def check_device(db, tenant, identity, actor):
    try:
        return await devices.scope(db, tenant, identity, actor, require_connected=True)
    except HTTPException as exc:
        if exc.status_code in {404, 409}:
            raise fail("NUMBER_CHECK_DEVICE_UNAVAILABLE") from None
        raise


@router.post(BASE + "/check")
async def check(tenant_id: UUID, request: Request, response: Response, actor: Actor = Depends(authenticate)):
    payload = await read_payload(tenant_id, actor, request)
    async with owner_transaction(tenant_id, actor) as db:
        device, transport = await check_device(db, tenant_id, payload.device_id, actor)
        before = devices.device_version(device)
        scope = devices.transport_scope(device)
        values = await group_numbers(db, tenant_id, payload.group_id) if payload.group_id else payload.numbers
        numbers, rows, duplicates = schemas.prepare_numbers(values)
    # Never hold the tenant transaction while WhatsApp is responding.
    if numbers:
        try:
            async with asyncio.timeout(35):
                raw = await channels._bridge_control(transport, "device/number-check/check", "POST",
                                                     {**scope, "numbers": numbers})
            if len(shared.encode(raw).encode("utf-8")) > schemas.BRIDGE_BYTES:
                raise ValueError("oversized_provider_response")
            result = schemas.BridgeResult.model_validate(raw)
        except HTTPException as exc:
            reason = exc.detail.get("group_read_reason") if isinstance(exc.detail, dict) else None
            if reason == "context_changed":
                raise fail("NUMBER_CHECK_CONTEXT_CHANGED") from None
            if reason in {"read_busy", "device_busy"}:
                raise fail("NUMBER_CHECK_BUSY", 409) from None
            raise fail("NUMBER_CHECK_UNAVAILABLE", 503) from None
        except (TimeoutError, ValueError, TypeError, UnicodeError, ValidationError):
            raise fail("NUMBER_CHECK_UNAVAILABLE", 503) from None
        if (str(result.device_id) != scope["device_id"] or str(result.generation) != scope["generation"]
                or result.settings_revision != scope["settings_revision"]
                or [item.number for item in result.results] != numbers):
            raise fail("NUMBER_CHECK_CONTEXT_CHANGED")
        results = {item.number: item.model_dump(mode="json") for item in result.results}
        rows = [row if "status" in row else results[row["number"]] for row in rows]
    async with owner_transaction(tenant_id, actor) as db:
        current, _ = await check_device(db, tenant_id, payload.device_id, actor)
        if devices.device_version(current) != before:
            raise fail("NUMBER_CHECK_CONTEXT_CHANGED")
    counts = {status: sum(row["status"] == status for row in rows) for status in schemas.STATUSES}
    return output(response, {"device_id": payload.device_id, "group_id": payload.group_id,
        "items": rows, "total": len(rows), "input_total": len(values),
        "duplicates": duplicates, "counts": counts})
