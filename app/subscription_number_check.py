"""Owned, read-only WhatsApp number checks on the existing Baileys session."""
import asyncio
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.encoders import jsonable_encoder
from pydantic import ValidationError
from redis.exceptions import RedisError

from app.panel_auth import _request_redis

from app.catalog_api import Actor, authenticate
from app import qr_channels as channels
from app import subscription_group_contacts_store as devices
from app import subscription_number_check_schemas as schemas
from app import subscription_chatbot_store as shared

BASE = "/api/v1/tenants/{tenant_id}/subscription-whatsapp/number-check"
router = APIRouter(tags=["Verificador de números do WhatsApp de assinaturas"])
FEATURE = "number_check.manage"


def fail(code, status=409):
    messages = {
        "NUMBER_CHECK_INVALID": "Confira o dispositivo, os números e sua autorização.",
        "NUMBER_CHECK_DEVICE_UNAVAILABLE": "Conecte seu WhatsApp para verificar os números.",
        "NUMBER_CHECK_UNAVAILABLE": "Não foi possível verificar estes números agora. Nenhuma mensagem foi enviada.",
        "NUMBER_CHECK_CONTEXT_CHANGED": "A conexão foi alterada. Atualize antes de verificar novamente.",
        "NUMBER_CHECK_LIMIT": "Limite de verificações desta hora atingido. Não foi realizada uma nova consulta.",
    }
    return HTTPException(status, {"code": code, "message": messages[code]},
                         headers={"Cache-Control": "no-store"})


def output(response, value):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return jsonable_encoder(value)


def owner_transaction(tenant, actor):
    return shared.owner_transaction(tenant, actor, feature=FEATURE)


# Redis guarantees a single per-tenant/device quota over worker, transport and
# session restarts. No requested phone, response or contact is stored in Redis.
# Each reservation charges *before* making an external query; failures are not refunded.
QUOTA_SCRIPT = """
local count = redis.call('INCRBY', KEYS[1], tonumber(ARGV[1]))
if count == tonumber(ARGV[1]) then
  redis.call('PEXPIRE', KEYS[1], tonumber(ARGV[3]))
end
if count > tonumber(ARGV[2]) then return 0 end
return 1
"""


async def reserve_quota(request: Request, tenant: UUID, device: UUID, amount: int):
    redis, owned = _request_redis(request)
    try:
        permitted = await redis.eval(QUOTA_SCRIPT, 1,
            f"orion:subscription:number-check:v1:{tenant}:{device}", amount,
            schemas.MAX_PER_HOUR, 3_600_000)
    except (RedisError, OSError):
        raise fail("NUMBER_CHECK_UNAVAILABLE", 503) from None
    finally:
        if owned:
            await redis.aclose()
    if permitted != 1:
        raise fail("NUMBER_CHECK_LIMIT", 429)


@router.get(BASE + "/options")
async def options(tenant_id: UUID, response: Response, actor: Actor = Depends(authenticate)):
    async with owner_transaction(tenant_id, actor) as db:
        return output(response, {
            "devices": await devices.device_options(db, tenant_id, actor),
            "limits": {"per_request": schemas.MAX_PER_REQUEST, "per_hour": schemas.MAX_PER_HOUR},
        })


@router.post(BASE + "/check")
async def check(tenant_id: UUID, request: Request, response: Response, actor: Actor = Depends(authenticate)):
    # Authenticate and scope before consuming request bytes, then do not hold a
    # tenant transaction open across network I/O to the existing socket.
    async with owner_transaction(tenant_id, actor):
        pass
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > schemas.MAX_BODY:
            raise fail("NUMBER_CHECK_INVALID", 413)
        raw.extend(chunk)
    try:
        payload = schemas.CheckRequest.model_validate_json(bytes(raw))
    except (ValidationError, ValueError, UnicodeError):
        raise fail("NUMBER_CHECK_INVALID", 422) from None

    async with owner_transaction(tenant_id, actor) as db:
        try:
            device, transport = await devices.scope(db, tenant_id, payload.device_id, actor, require_connected=True)
        except HTTPException as exc:
            if exc.status_code in {404, 409}:
                raise fail("NUMBER_CHECK_DEVICE_UNAVAILABLE") from None
            raise
        expected_version = devices.device_version(device)
        scope = devices.transport_scope(device)
    await reserve_quota(request, tenant_id, payload.device_id, len(payload.phones))
    try:
        async with asyncio.timeout(17):
            remote = await channels._bridge_control(
                transport, "device/number-check/check", "POST", {**scope, "phones": payload.phones}
            )
        if len(shared.encode(remote).encode("utf-8")) > schemas.BRIDGE_BYTES:
            raise ValueError("oversize")
        checked = schemas.BridgeResult.model_validate(remote)
    except (HTTPException, TimeoutError, ValueError, TypeError, UnicodeError, ValidationError):
        raise fail("NUMBER_CHECK_UNAVAILABLE", 503) from None

    if (str(checked.device_id) != scope["device_id"] or
        str(checked.generation) != scope["generation"] or
        checked.settings_revision != scope["settings_revision"] or
        [item.phone for item in checked.items] != payload.phones):
        raise fail("NUMBER_CHECK_CONTEXT_CHANGED")

    async with owner_transaction(tenant_id, actor) as db:
        fresh, _ = await devices.scope(db, tenant_id, payload.device_id, actor, require_connected=True)
        if devices.device_version(fresh) != expected_version:
            raise fail("NUMBER_CHECK_CONTEXT_CHANGED")

    return output(response, {
        "device_id": str(checked.device_id),
        "items": [item.model_dump(mode="json") for item in checked.items],
        "total": len(checked.items),
        "notice": "Esta consulta não envia mensagens e não confirma que o destinatário aceita novos contatos.",
    })
