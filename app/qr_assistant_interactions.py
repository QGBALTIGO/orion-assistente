"""Backend-issued WhatsApp choices; a displayed label never authorizes a write.

Every choice is bound to the authenticated conversation, current state, accepted
outbound message, channel ownership and deadline. The existing booking engine
still validates ownership, the complete summary, availability and idempotency.
"""
import hashlib
import json
import math
import os
import re
import secrets
import time
from contextvars import ContextVar
from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field
from sqlalchemy import text

from app.catalog_schemas import StrictModel
from app import qr_channel_scope
from app.qr_assistant_catalog import clip, focus_service
from app.qr_presentation import money, duration, message_units

router = APIRouter(tags=["Escolhas privadas da recepcionista"])
ID_PATTERN = r"^[A-Za-z0-9:_-]{1,160}$"
STALE = "Essa opção mudou ou expirou. Pode me dizer o que você quer fazer agora? Vou conferir os dados atuais antes de alterar qualquer agendamento."
_choice = ContextVar("qr_verified_interactive_choice", default=None)


def begin_choice():
    return _choice.set(None)


def reset_choice(token):
    _choice.reset(token)


def current_choice():
    return _choice.get()


def consume_choice():
    result = _choice.get()
    _choice.set(None)
    return result


class Capabilities(StrictModel):
    assistant_interactions: int = Field(strict=True, ge=1, le=1)


class Selection(StrictModel):
    id: str = Field(min_length=1, max_length=160, pattern=ID_PATTERN)
    quoted_message_id: str = Field(min_length=1, max_length=160, pattern=ID_PATTERN)


class PresentationAck(StrictModel):
    phone: str = Field(pattern=r"^55[1-9][0-9](?:9[0-9]{8}|[2-5][0-9]{7})$")
    presentation_id: str = Field(min_length=32, max_length=64, pattern=ID_PATTERN)
    provider_id: str = Field(min_length=1, max_length=160, pattern=ID_PATTERN)


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":"))


def _read(value):
    try:
        result = json.loads(value or "null")
        return result if isinstance(result, dict) else None
    except (ValueError, TypeError):
        return None


def _digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def authority(tenant, phone, namespace, identity=None):
    identity = identity if identity is not None else qr_channel_scope.current()
    channel = {field: str(getattr(identity, field)) for field in
        ("channel_id", "owner_account_id", "fence", "lease_owner", "preserved_session_fingerprint")} if identity else None
    return _digest(_json({"tenant": str(tenant), "phone": phone, "namespace": namespace, "channel": channel}))


async def state_digest(redis, key):
    state = _read(await redis.get(key)) or {}
    revision = await redis.get(key+":catalog-context")
    if isinstance(revision, bytes):
        revision = revision.decode()
    return _digest(_json({"state": state, "revision": revision}))


async def control_revision(tenant, phone, identity=None):
    identity = identity if identity is not None else qr_channel_scope.current()
    if identity is None or not (os.getenv("INBOX_CONTROL_ENABLED") == "1" or identity.central_adopted):
        return None
    from app import inbox_control
    if await inbox_control.blocked(tenant, identity.channel_id, phone):
        raise HTTPException(423, "A equipe assumiu este atendimento")
    return await inbox_control.assistant_revision(tenant, identity.channel_id, phone)


ACK_SCRIPT = """
local raw=redis.call('GET',KEYS[1])
if not raw then return 0 end
local item=cjson.decode(raw)
if item.id~=ARGV[1] or item.authority~=ARGV[3] or item.expires_at<=tonumber(ARGV[4]) then return 0 end
if item.provider_id and item.provider_id~=cjson.null and item.provider_id~=ARGV[2] then return -1 end
local ttl=redis.call('PTTL',KEYS[1])
if ttl<=0 then return 0 end
item.provider_id=ARGV[2]
redis.call('SET',KEYS[1],cjson.encode(item),'PX',ttl)
return 1
"""


async def acknowledge(redis, tenant, phone, namespace, payload, *, identity=None):
    key = f"orion:qr:ai:{tenant}:{namespace}:presentation"
    current = _read(await redis.get(key))
    if current is None or current.get("control_revision") != await control_revision(tenant, phone, identity):
        raise HTTPException(409, "O controle do atendimento mudou")
    result = await redis.eval(ACK_SCRIPT, 1, key, payload.presentation_id, payload.provider_id,
                              authority(tenant, phone, namespace, identity), time.time())
    if result != 1:
        raise HTTPException(409, "A apresentação não está mais vigente")
    return {"acknowledged": True}


async def selected_action(redis, tenant, phone, namespace, selection):
    key = f"orion:qr:ai:{tenant}:{namespace}"
    record = _read(await redis.get(key+":presentation"))
    try:
        valid = (record is not None and record.get("authority") == authority(tenant, phone, namespace)
            and record.get("control_revision") == await control_revision(tenant, phone)
            and isinstance(record.get("expires_at"), (int, float)) and math.isfinite(record["expires_at"])
            and record["expires_at"] > time.time()
            and isinstance(record.get("provider_id"), str)
            and secrets.compare_digest(record["provider_id"], selection.quoted_message_id)
            and secrets.compare_digest(record.get("state", ""), await state_digest(redis, key)))
        action = record.get("choices", {}).get(_digest(selection.id)) if valid else None
    except (TypeError, ValueError, KeyError):
        action = None
    if not isinstance(action, dict):
        return None
    return action


async def resolve(redis, tenant, phone, namespace, selection):
    action = await selected_action(redis, tenant, phone, namespace, selection)
    if action is None:
        return None
    key = f"orion:qr:ai:{tenant}:{namespace}"
    # Consumption happens under run_locked's conversation lock. A failed write
    # must not make the old button a new authorization on a later delivery.
    await redis.delete(key+":presentation")
    if action.get("kind") == "service":
        return await focus_service(redis, key, UUID(action["service_id"]))
    if action.get("kind") == "professional":
        _choice.set({"kind": "professional", "professional_id": str(UUID(action["professional_id"]))})
        return "[Escolha de profissional recebida]"
    if action.get("kind") == "text" and isinstance(action.get("body"), str):
        return action["body"]
    return None


async def _appointment_url(tenant, phone, protocol):
    from app import appointment_access
    from app.database import get_session_factory, tenant_transaction
    if not appointment_access.enabled() or not isinstance(protocol, str):
        return None
    async with get_session_factory()() as db:
        async with tenant_transaction(db, tenant):
            row = (await db.execute(text("""SELECT a.id FROM appointments a
                JOIN customers c ON c.tenant_id=a.tenant_id AND c.id=a.customer_id
                WHERE a.tenant_id=:tenant AND a.protocol=:protocol AND c.whatsapp_e164=:phone
                AND c.active AND c.deleted_at IS NULL AND a.status='confirmed'
                AND NOT a.customer_access_blocked"""),
                {"tenant": tenant, "protocol": protocol, "phone": "+"+phone})).mappings().first()
            if row is None:
                return None
            return await appointment_access.shared_link(db, tenant, row["id"], audience="customer")


async def _timezone(tenant):
    from app.database import get_session_factory, tenant_transaction
    async with get_session_factory()() as db:
        async with tenant_transaction(db, tenant):
            value = (await db.execute(text("SELECT timezone FROM tenants WHERE id=:tenant AND status='active'"),
                {"tenant": tenant})).scalar_one_or_none()
    return ZoneInfo(value) if value else None


async def present(redis, tenant, phone, namespace, result, capabilities, *, verified_operation=False):
    page = result.pop("_catalog_page", None)
    key = f"orion:qr:ai:{tenant}:{namespace}"
    await redis.delete(key+":presentation")
    if capabilities is None or not isinstance(result.get("answer"), str) or not result["answer"]:
        return result
    answer, step = result["answer"], result.get("step")
    state = _read(await redis.get(key)) or {}
    now, expires = time.time(), time.time()+300
    nonce, choices = secrets.token_urlsafe(24), {}

    def option(title, spec, description=""):
        token = f"oa1_{nonce}_{len(choices)}"
        choices[_digest(token)] = spec
        return {"id": token, "title": clip(title, 24), "description": clip(description, 72)}

    action = None
    if page:
        rows = []
        for row in page["rows"]:
            price = ("a partir de " if row.get("price_mode") == "from" else "")+money(row["price_cents"])
            description = price+" · "+duration(row["duration_min"])+" · "+row["name"]
            rows.append(option(row["name"], {"kind": "service", "service_id": str(row["id"])}, description))
        if page["page"] > 0:
            rows.append(option("Página anterior", {"kind": "text", "body": f"ver serviços página {page['page']}"}))
        if page["page"]+1 < page["pages"]:
            rows.append(option("Mais serviços", {"kind": "text", "body": f"ver serviços página {page['page']+2}"}))
        action = {"type": "list", "title": "Serviços", "description": page["intro"],
                  "button_text": "Ver serviços", "footer": f"Página {page['page']+1}/{page['pages']}",
                  "sections": [{"title": "Serviços disponíveis", "rows": rows}]}
    elif step in {"confirm", "reschedule_confirm", "cancel_confirm"} and state.get("step") == step:
        summary = state.get("confirmation_summary")
        try:
            expires = min(expires, float(state.get("confirmation_expires_at", 0)))
        except (ValueError, TypeError):
            expires = 0
        # A FAQ or greeting during a draft is not a freshly presented summary.
        if (not state.get("confirmation_requires_review") and isinstance(summary, str) and summary in answer
                and math.isfinite(expires) and expires > now and message_units(answer) <= 1024):
            positive = "Cancelar horário" if step == "cancel_confirm" else "Confirmar"
            buttons = [option(positive, {"kind": "text", "body": "sim"}),
                       option("Manter horário" if step == "cancel_confirm" else "Alterar horário",
                              {"kind": "text", "body": "não" if step == "cancel_confirm" else "quero alterar o horário"})]
            if step != "cancel_confirm":
                buttons.append(option("Voltar", {"kind": "text", "body": "não"}))
            action = {"type": "reply_buttons", "text": answer, "footer": "Confira os dados antes de escolher.",
                      "buttons": [{"id": x["id"], "title": x["title"]} for x in buttons]}
    elif step in {"time", "reschedule_time", "professional"} and state.get("step") == step:
        rows = []
        if step == "professional":
            people = state.get("professionals", [])
            for index, person in enumerate(people[:10], 1):
                if person["name"] in answer:
                    duplicate = sum(row["name"] == person["name"] for row in people) > 1
                    rows.append(option(person["name"], {"kind": "professional", "professional_id": str(person["id"])},
                                       f"Opção {index}" if duplicate else ""))
        else:
            zone = await _timezone(tenant)
            for value in state.get("slots", [])[:10]:
                clock = datetime.fromisoformat(value)
                if zone is None or clock.tzinfo is None:
                    continue
                clock = clock.astimezone(zone)
                if clock.strftime("%H:%M") in answer:
                    rows.append(option(clock.strftime("%H:%M"), {"kind": "text", "body": clock.strftime("%H:%M")}))
        if rows:
            action = {"type": "list", "title": "Escolha uma opção", "description": answer,
                      "button_text": "Ver opções", "footer": "Disponibilidade conferida novamente ao reservar.",
                      "sections": [{"title": "Opções consultadas", "rows": rows}]}
    elif step in {"confirmed", "rescheduled"} and verified_operation and message_units(answer) <= 1024:
        # Link failure never changes the successful booking or invents a URL.
        try:
            url = await _appointment_url(tenant, phone, result.get("protocol"))
        except (HTTPException, ValueError, RuntimeError):
            url = None
        if url:
            action = {"type": "url_button", "purpose": "appointment", "text": answer,
                      "button_text": "Ver agendamento", "footer": "Remarque ou cancele pela página segura.", "url": url}
    limit = 1000 if action and action.get("type") == "list" else 1024
    if action is None or message_units(action.get("text", action.get("description", ""))) > limit:
        return result
    record = {"id": nonce, "authority": authority(tenant, phone, namespace), "state": await state_digest(redis, key),
              "control_revision": await control_revision(tenant, phone),
              "expires_at": expires, "provider_id": None, "choices": choices}
    await redis.set(key+":presentation", _json(record), ex=max(1, int(expires-now)))
    return {**result, "presentation": {"version": 1, "id": nonce, "action": action}}


async def _ack_request(payload, request, channel_id=None):
    from redis.asyncio import from_url
    import os
    identity = None
    if channel_id is None:
        from app.qr_intake_revision import authorized_tenant
        from app.qr_legacy import require_managed_path
        from app.inbox_control import enabled
        tenant = authorized_tenant(request, payload.phone)
        if enabled() or await require_managed_path():
            raise HTTPException(403, "Use o canal gerenciado atual")
        namespace = payload.phone
    else:
        from app.qr_channels import authenticated_channel
        from app.qr_ai import _managed_assistant_ready
        identity = await authenticated_channel(request, phone=payload.phone)
        if identity.channel_id != channel_id or not identity.assistant_enabled or not await _managed_assistant_ready(identity):
            raise HTTPException(403, "Canal de atendimento indisponível")
        tenant, namespace = identity.tenant_id, identity.conversation_namespace(payload.phone)
    redis = from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"), socket_connect_timeout=3, socket_timeout=3)
    try:
        return await acknowledge(redis, tenant, payload.phone, namespace, payload, identity=identity)
    finally:
        await redis.aclose()


@router.post("/internal/qr/ai/presentation-ack", include_in_schema=False)
async def legacy_ack(payload: PresentationAck, request: Request):
    return await _ack_request(payload, request)


@router.post("/internal/qr/channels/{channel_id}/ai/presentation-ack", include_in_schema=False)
async def channel_ack(channel_id: UUID, payload: PresentationAck, request: Request):
    return await _ack_request(payload, request, channel_id)
