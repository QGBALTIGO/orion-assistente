"""Private ingress revision fence, before business writes; never an undo protocol.

Notices bypass the long conversation lock so a newer trusted message can revoke
an inference still in progress. Only an explicit zero-write response permits the
bridge to carry prepared text into the next complete batch. Unknown outcomes
and attempts that started a write are never replayed by this protocol.
"""
from contextvars import ContextVar
from dataclasses import dataclass
import os
import re
import secrets
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field

from app.catalog_schemas import StrictModel
from app import qr_channel_scope

router = APIRouter()
TTL = 604800
MAX_SEEN = 2048
ID_PATTERN = r"^[a-zA-Z0-9._:-]{1,160}$"
PHONE_PATTERN = r"^55[1-9][0-9]{9,10}$"

NOTICE_SCRIPT = """
local existing=redis.call('HGET',KEYS[2],ARGV[1])
if existing then return tonumber(existing) end
local revision=redis.call('INCR',KEYS[1])
redis.call('HSET',KEYS[2],ARGV[1],revision)
redis.call('ZADD',KEYS[3],revision,ARGV[1])
local excess=redis.call('ZCARD',KEYS[3])-tonumber(ARGV[3])
if excess>0 then
  local old=redis.call('ZRANGE',KEYS[3],0,excess-1)
  for _,id in ipairs(old) do redis.call('HDEL',KEYS[2],id);redis.call('ZREM',KEYS[3],id) end
end
for _,key in ipairs(KEYS) do redis.call('EXPIRE',key,ARGV[2]) end
return revision
"""

VALIDATE_SCRIPT = """
local latest=redis.call('GET',KEYS[1])
if not latest or tonumber(latest)~=tonumber(ARGV[1]) then return 0 end
local maximum=0
for i=2,#ARGV do
 local value=redis.call('HGET',KEYS[2],ARGV[i])
 if not value then return 0 end
 maximum=math.max(maximum,tonumber(value))
end
if maximum~=tonumber(ARGV[1]) then return 0 end
return 1
"""


def enabled():
    return qr_channel_scope.current() is not None or os.getenv("QR_AI_INTAKE_REVISION_ENABLED", "0") == "1"


def keys(tenant: UUID, phone: str):
    if not isinstance(tenant, UUID) or not re.fullmatch(PHONE_PATTERN, phone):
        raise ValueError("Authenticated conversation required")
    return _storage_keys(qr_channel_scope.tenant_scope(tenant), phone)


def _storage_keys(namespace, phone):
    base = f"orion:qr:intake:{namespace}:{phone}"
    return base + ":revision", base + ":seen", base + ":order"


def channel_keys(identity, phone):
    """Server-authenticated notice scope, including when the assistant is off.

    This selects storage only. It never binds assistant/domain permission.
    """
    from app.qr_channels import ChannelIdentity
    if (not isinstance(identity, ChannelIdentity) or not identity.allowed_sender or identity.provider != "baileys"
            or not isinstance(identity.tenant_id, UUID) or not isinstance(identity.channel_id, UUID)
            or not isinstance(phone, str) or not re.fullmatch(PHONE_PATTERN, phone)):
        raise ValueError("Authenticated channel conversation required")
    namespace = str(identity.tenant_id) if identity.legacy_transport else f"{identity.tenant_id}:{identity.channel_id}"
    return _storage_keys(namespace, phone)


class Notice(StrictModel):
    phone: str = Field(pattern=PHONE_PATTERN)
    message_id: str = Field(pattern=ID_PATTERN)


def authorized_tenant(request: Request, phone: str):
    configured = os.getenv("QR_BRIDGE_TOKEN", "")
    if len(configured) < 32 or not secrets.compare_digest(configured, request.headers.get("x-orion-bridge-key", "")):
        raise HTTPException(401, "Acesso negado")
    if not enabled() or os.getenv("QR_AI_ENABLED", "0") != "1":
        raise HTTPException(423, "Registro de ingresso desativado")
    allowed = {x.strip() for x in os.getenv("QR_AI_ALLOWED_SENDERS", "").split(",") if x.strip()}
    if not allowed or phone not in allowed:
        raise HTTPException(403, "Contato não autorizado")
    try:
        return UUID(os.environ["QR_AI_TENANT_ID"])
    except (ValueError, KeyError):
        raise HTTPException(503, "Estabelecimento indisponível") from None


async def register(redis, tenant, phone, message_id):
    return await _register(redis, keys(tenant, phone), message_id)


async def register_channel(redis, identity, phone, message_id):
    return await _register(redis, channel_keys(identity, phone), message_id)


async def _register(redis, storage_keys, message_id):
    if not re.fullmatch(ID_PATTERN, message_id):
        raise ValueError("Validated message identity required")
    value = await redis.eval(NOTICE_SCRIPT, 3, *storage_keys, message_id, TTL, MAX_SEEN)
    if type(value) is not int or not 1 <= value <= 9007199254740991:
        raise RuntimeError("Invalid ingress revision")
    return value


@router.post("/internal/qr/ai/notice", include_in_schema=False)
async def notice(payload: Notice, request: Request):
    tenant = authorized_tenant(request, payload.phone)
    from redis.asyncio import from_url
    redis = from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"), socket_connect_timeout=3, socket_timeout=3)
    try:
        return {"revision": await register(redis, tenant, payload.phone, payload.message_id)}
    except Exception:
        raise HTTPException(503, "Não consegui validar esta mensagem agora") from None
    finally:
        await redis.aclose()


@dataclass
class TurnRevision:
    tenant: UUID
    phone: str
    message_ids: tuple[str, ...]
    expected: int
    redis: object
    write_started: bool = False


_turn: ContextVar[TurnRevision | None] = ContextVar("orion_intake_turn_revision", default=None)


class Superseded(Exception):
    def __init__(self, not_committed=True):
        self.not_committed = bool(not_committed)
        super().__init__("Ingress revision changed; never retry an unknown operation")

    def result(self):
        return {"answer": None, "step": "superseded" if self.not_committed else "superseded_after_write",
                "not_committed": self.not_committed}


def bind(tenant, phone, message_ids, expected_revision, redis):
    keys(tenant, phone)
    if (type(expected_revision) is not int or not 1 <= expected_revision <= 9007199254740991 or
            not isinstance(message_ids, (list, tuple)) or not 1 <= len(message_ids) <= 6 or
            len(set(message_ids)) != len(message_ids) or
            any(not isinstance(x, str) or not re.fullmatch(ID_PATTERN, x) for x in message_ids)):
        raise HTTPException(422, "Revisão de ingresso inválida")
    return _turn.set(TurnRevision(tenant, phone, tuple(message_ids), expected_revision, redis))


def reset(token):
    _turn.reset(token)


def write_started():
    context = _turn.get()
    return context is not None and context.write_started


async def assert_current():
    # Managed authority is mandatory even outside a revision-bound turn and
    # regardless of the legacy pilot's optional revision flag.
    await qr_channel_scope.assert_current()
    context = _turn.get()
    if context is None or not enabled():
        return
    try:
        current = await context.redis.eval(VALIDATE_SCRIPT, 2, *keys(context.tenant, context.phone)[:2],
                                           context.expected, *context.message_ids)
    except Exception:
        # No clear non-commit acknowledgment on infrastructure failure. The
        # caller propagates an uncertain result and the bridge never replays it.
        raise HTTPException(503, "Não consegui validar o pedido antes da operação") from None
    if current != 1:
        raise Superseded(not_committed=not context.write_started)


async def check_before_write():
    await assert_current()
    context = _turn.get()
    if context is not None and enabled():
        context.write_started = True
