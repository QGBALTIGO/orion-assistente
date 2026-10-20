"""One persistent arbiter per tenant/channel/verified phone.

The send slot never expires automatically: an unknown provider outcome must be
reconciled before another sender or operator can take over. No LLM owns access.
"""
from contextlib import asynccontextmanager
from contextvars import ContextVar
import os
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from pydantic import Field, StrictInt
from sqlalchemy import text

from app.catalog_api import Actor, authenticate, audit
from app.catalog_schemas import StrictModel
from app.database import get_session_factory, tenant_transaction
from app.management_api import management_session

router = APIRouter(tags=["Controle persistente de atendimento"])
PREFIX = "/api/v1/tenants/{tenant_id}/management/inbox/channels/{channel_id}/control"
_assistant_scope = ContextVar("inbox_assistant_scope", default=None)


def bind_assistant(identity, phone, revision):
    return _assistant_scope.set((identity.tenant_id, identity.channel_id, "+"+phone.removeprefix("+"), revision))


def reset_assistant(token):
    _assistant_scope.reset(token)


async def guard_transaction(db, identity):
    scope = _assistant_scope.get()
    if not enabled() or scope is None:
        return
    tenant, channel, phone, revision = scope
    if (identity.tenant_id, identity.channel_id) != (tenant, channel):
        raise HTTPException(409, "O controle não corresponde ao canal autenticado")
    row = (await db.execute(text("""SELECT mode,revision,pending_operation FROM inbox_controls
      WHERE tenant_id=:t AND channel_id=:c AND contact_e164=:phone FOR SHARE"""),
      {"t": tenant, "c": channel, "phone": phone})).mappings().one_or_none()
    if not row or row["mode"] != "ai" or row["revision"] != revision or row["pending_operation"] is not None:
        raise HTTPException(409, "O controle mudou antes da operação de negócio")


def enabled():
    return os.getenv("INBOX_CONTROL_ENABLED", "0") == "1"


def require_enabled():
    if not enabled():
        raise HTTPException(423, "O controle persistente de atendimento ainda não está habilitado")


class Change(StrictModel):
    contact_e164: str = Field(pattern=r"^\+55[1-9][0-9]{9,10}$")
    expected_revision: StrictInt = Field(ge=1)
    mode: Literal["ai", "human", "paused", "waiting", "closed"]


class SendSlot(StrictModel):
    phone: str = Field(pattern=r"^55[1-9][0-9]{9,10}$")
    operation_id: UUID
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    source: Literal["assistant", "automation"]
    control_revision: StrictInt | None = Field(default=None, ge=1)


class FinishSlot(SendSlot):
    accepted: Literal[True]


async def lock_control(db, tenant, channel, phone):
    values = {"t": tenant, "channel": channel, "phone": phone}
    await db.execute(text("""INSERT INTO inbox_controls(tenant_id,channel_id,contact_e164)
      VALUES(:t,:channel,:phone) ON CONFLICT DO NOTHING"""), values)
    row = (await db.execute(text("""SELECT * FROM inbox_controls
      WHERE tenant_id=:t AND channel_id=:channel AND contact_e164=:phone FOR UPDATE"""), values)).mappings().one()
    return dict(row)


async def check_channel(db, tenant, channel, phone):
    found = (await db.execute(text("""SELECT public.inbox_channel_contact_allowed(
      CAST(:t AS uuid),CAST(:id AS uuid),CAST(:phone AS text))"""),
      {"t": tenant, "id": channel, "phone": phone.removeprefix('+')})).scalar_one()
    if found is not True:
        raise HTTPException(404, "Canal ou contato indisponível neste estabelecimento")


async def blocked(tenant, channel, phone):
    if not enabled():
        return False
    async with get_session_factory()() as db:
        async with tenant_transaction(db, tenant):
            row = await lock_control(db, tenant, channel, "+"+phone.removeprefix("+"))
            return row["mode"] != "ai" or row["pending_operation"] is not None


async def assistant_revision(tenant, channel, phone):
    if not enabled():
        return None
    async with get_session_factory()() as db:
        async with tenant_transaction(db, tenant):
            row = await lock_control(db, tenant, channel, "+"+phone.removeprefix("+"))
            if row["mode"] != "ai" or row["pending_operation"] is not None:
                raise HTTPException(409, "Atendimento humano ou suspenso")
            return row["revision"]


async def customer_handoff(tenant, channel, phone):
    """A verified inbound customer can pause, never assign a staff identity."""
    if not enabled():
        return
    token = _assistant_scope.set(None)
    try:
        async with get_session_factory()() as db:
          async with tenant_transaction(db, tenant):
            row = await lock_control(db, tenant, channel, "+"+phone.removeprefix("+"))
            # The generation check plus the bridge's final slot gate stops
            # responses prepared before this committed transfer.
            if row["pending_operation"] is not None:
                raise HTTPException(409, "Há um envio em verificação neste atendimento")
            if row["mode"] != "human":
                await db.execute(text("""UPDATE inbox_controls SET mode='human',assigned_account_id=NULL,
                  revision=revision+1,updated_at=clock_timestamp() WHERE tenant_id=:t AND id=:id"""), {"t": tenant, "id": row["id"]})
                await audit(db, tenant, "inbox_customer_handoff", "inbox_controls", row["id"])
    finally:
        _assistant_scope.reset(token)


@router.get(PREFIX)
async def get_control(tenant_id: UUID, channel_id: UUID,
                      phone: str = Query(pattern=r"^\+55[1-9][0-9]{9,10}$"), actor: Actor = Depends(authenticate)):
    require_enabled()
    async with management_session(tenant_id, actor) as db:
        await check_channel(db, tenant_id, channel_id, phone)
        row = (await db.execute(text("""SELECT mode,assigned_account_id,revision,pending_operation,pending_since
          FROM inbox_controls WHERE tenant_id=:t AND channel_id=:channel AND contact_e164=:phone"""),
          {"t": tenant_id, "channel": channel_id, "phone": phone})).mappings().one_or_none()
        return jsonable_encoder(row or {"mode": "ai", "assigned_account_id": None, "revision": 1, "pending_operation": None, "pending_since": None})


@router.put(PREFIX)
async def change_control(tenant_id: UUID, channel_id: UUID, payload: Change, actor: Actor = Depends(authenticate)):
    require_enabled()
    if not isinstance(actor.account_id, UUID):
        raise HTTPException(403, "Entre com sua conta pessoal para assumir um atendimento")
    async with management_session(tenant_id, actor, write=True) as db:
        await check_channel(db, tenant_id, channel_id, payload.contact_e164)
        row = await lock_control(db, tenant_id, channel_id, payload.contact_e164)
        if row["revision"] != payload.expected_revision:
            raise HTTPException(409, "Outro operador alterou o atendimento. Atualize a conversa")
        if row["pending_operation"] is not None:
            raise HTTPException(409, "Um envio ainda está em verificação. Consulte o recibo antes de transferir")
        role = db.info["management_actor_role"]
        if row["assigned_account_id"] not in {None, actor.account_id} and role not in {"owner", "support", "admin"}:
            raise HTTPException(409, "A conversa já foi assumida por outro operador")
        if payload.mode == "waiting" and row["assigned_account_id"] != actor.account_id:
            raise HTTPException(409, "Assuma o atendimento antes de aguardar o cliente")
        assigned = actor.account_id if payload.mode in {"human", "waiting"} else None
        result = (await db.execute(text("""UPDATE inbox_controls SET mode=:mode,assigned_account_id=:account,
          revision=revision+1,updated_at=clock_timestamp() WHERE tenant_id=:t AND id=:id
          RETURNING mode,assigned_account_id,revision,pending_operation,pending_since"""),
          {"t": tenant_id, "id": row["id"], "mode": payload.mode, "account": assigned})).mappings().one()
        await audit(db, tenant_id, "inbox_control_"+payload.mode, "inbox_controls", row["id"])
        return jsonable_encoder(result)


@asynccontextmanager
async def bridge_control(request, payload):
    require_enabled()
    from app.qr_channels import authenticated_channel
    identity = await authenticated_channel(request, phone=payload.phone)
    async with get_session_factory()() as db:
        async with tenant_transaction(db, identity.tenant_id):
            # This server identity has already been authenticated independently
            # from JSON. Check it again around the mutation, including expiry.
            from app.automation_runtime import _notice_guard, _notice_identity
            guard = _notice_identity(identity.tenant_id, payload.phone, identity)
            await _notice_guard(db, guard)
            row = await lock_control(db, identity.tenant_id, identity.channel_id, "+"+payload.phone)
            yield db, identity, row
            await _notice_guard(db, guard)


@router.post("/internal/qr/channels/{channel_id}/send-authorize", include_in_schema=False)
async def authorize_send(channel_id: UUID, payload: SendSlot, request: Request):
    async with bridge_control(request, payload) as (db, identity, row):
        if payload.source == "assistant" and not identity.assistant_enabled:
            raise HTTPException(409, "Assistente desativada")
        if payload.source == "assistant" and payload.control_revision != row["revision"]:
            raise HTTPException(409, "O controle mudou durante a preparação da resposta")
        if row["mode"] != "ai" or row["pending_operation"] is not None:
            raise HTTPException(409, "Atendimento ocupado ou suspenso")
        # The legacy company-wide marketing pause remains a conservative veto.
        paused = (await db.execute(text("""SELECT human_paused FROM automation_contact_state
          WHERE tenant_id=:t AND contact_e164=:phone"""), {"t": identity.tenant_id, "phone": "+"+payload.phone})).scalar_one_or_none()
        if paused:
            raise HTTPException(409, "Atendimento suspenso")
        await db.execute(text("""UPDATE inbox_controls SET pending_operation=:operation,pending_fingerprint=:fingerprint,
          pending_since=clock_timestamp() WHERE tenant_id=:t AND id=:id"""),
          {"t": identity.tenant_id, "id": row["id"], "operation": payload.operation_id, "fingerprint": payload.fingerprint})
        return {"authorized": True, "operation_id": payload.operation_id}


@router.post("/internal/qr/channels/{channel_id}/send-finish", include_in_schema=False)
async def finish_send(channel_id: UUID, payload: FinishSlot, request: Request):
    async with bridge_control(request, payload) as (db, identity, row):
        if row["pending_operation"] != payload.operation_id or row["pending_fingerprint"] != payload.fingerprint:
            raise HTTPException(409, "Recibo não corresponde ao envio pendente")
        await db.execute(text("""UPDATE inbox_controls SET pending_operation=NULL,pending_fingerprint=NULL,pending_since=NULL
          WHERE tenant_id=:t AND id=:id"""), {"t": identity.tenant_id, "id": row["id"]})
        return {"released": True, "operation_id": payload.operation_id}


class Reconcile(StrictModel):
    contact_e164: str = Field(pattern=r"^\+55[1-9][0-9]{9,10}$")
    operation_id: UUID
    expected_revision: StrictInt = Field(ge=1)


@router.post(PREFIX+"/reconcile")
async def reconcile_control(tenant_id: UUID, channel_id: UUID, payload: Reconcile, actor: Actor = Depends(authenticate)):
    require_enabled()
    # A receipt is read from the authenticated bridge, never asserted by a UI.
    from app.automation_transport import inspect_automation
    from app.tenant_access import resolve_tenant_access
    await resolve_tenant_access(actor, tenant_id, "draft")
    record = await inspect_automation(tenant_id, channel_id, str(payload.operation_id))
    if record.get("operation_id") != str(payload.operation_id) or not (
        record.get("state") == "accepted" and record.get("accepted") is True
        or record.get("state") == "refused" and record.get("delivery") == "not_sent"
    ):
        raise HTTPException(409, "O resultado continua incerto. A suspensão foi preservada")
    async with management_session(tenant_id, actor, write=True) as db:
        await check_channel(db, tenant_id, channel_id, payload.contact_e164)
        row = await lock_control(db, tenant_id, channel_id, payload.contact_e164)
        if row["revision"] != payload.expected_revision or row["pending_operation"] != payload.operation_id:
            raise HTTPException(409, "O atendimento mudou. Atualize antes de verificar")
        await db.execute(text("""UPDATE inbox_controls SET pending_operation=NULL,pending_fingerprint=NULL,pending_since=NULL
          WHERE tenant_id=:t AND id=:id"""), {"t": tenant_id, "id": row["id"]})
        await audit(db, tenant_id, "inbox_send_reconciled", "inbox_controls", row["id"])
        return {"released": True, "delivery": record.get("delivery", "unknown")}
