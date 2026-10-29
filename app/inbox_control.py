"""One persistent arbiter per tenant/channel/verified phone.

The send slot never expires automatically: an unknown provider outcome must be
reconciled before another sender or operator can take over. No LLM owns access.
"""
from contextlib import asynccontextmanager
from contextvars import ContextVar
import json
import os
from typing import Literal
from uuid import UUID
from types import SimpleNamespace

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
    return _assistant_scope.set((identity.tenant_id, identity.channel_id, "+"+phone.removeprefix("+"), revision,getattr(identity,'provider','meta')))


def reset_assistant(token):
    _assistant_scope.reset(token)


async def guard_transaction(db, identity=None):
    scope = _assistant_scope.get()
    if not enabled() or scope is None:
        return
    tenant, channel, phone, revision, provider = scope
    from app.plan_permissions import require_feature
    await require_feature(db, tenant, "reception.ai")
    if identity is not None and (identity.tenant_id, identity.channel_id) != (tenant, channel):
        raise HTTPException(409, "O controle não corresponde ao canal autenticado")
    if identity is None:
        from app.qr_channel_scope import current
        if current() is None and provider=='meta':
            valid = (await db.execute(text("SELECT public.inbox_meta_guard(:t,:c,true)"), {"t":tenant,"c":channel})).scalar_one()
            if valid is not True:
                raise HTTPException(409, "A autorização da assistente mudou")
    row = (await db.execute(text("""SELECT mode,revision,pending_operation FROM inbox_controls
      WHERE tenant_id=:t AND channel_id=:c AND contact_e164=:phone FOR SHARE"""),
      {"t": tenant, "c": channel, "phone": phone})).mappings().one_or_none()
    if not row or row["mode"] != "ai" or row["revision"] != revision or row["pending_operation"] is not None:
        raise HTTPException(409, "O controle mudou antes da operação de negócio")


def enabled():
    from app.qr_channel_scope import current
    identity = current()
    return os.getenv("INBOX_CONTROL_ENABLED", "0") == "1" or bool(identity and getattr(identity, 'central_adopted', False))


def require_enabled():
    if not enabled():
        raise HTTPException(423, "O controle persistente de atendimento ainda não está habilitado")


async def clear_transient_handoff(redis, state_key):
    """Called under the existing conversation lock, after persistent AI authority.

    A manually resumed managed channel must not hand itself back to a human due
    to the old Redis pause. Preserve every other conversational/domain state.
    """
    scope=_assistant_scope.get()
    if enabled() and scope is not None:
        tenant,channel,phone,revision,_provider=scope
        async with get_session_factory()() as db:
            async with tenant_transaction(db,tenant):
                resumed=(await db.execute(text('''SELECT e.revision FROM inbox_control_events e
                  JOIN inbox_controls c ON c.tenant_id=e.tenant_id AND c.id=e.control_id
                  WHERE c.tenant_id=:t AND c.channel_id=:channel AND c.contact_e164=:phone
                    AND c.mode='ai' AND c.revision=:revision AND e.source='operator'
                    AND e.mode='ai' AND e.previous_mode<>'ai'
                  ORDER BY e.revision DESC LIMIT 1'''),
                  {'t':tenant,'channel':channel,'phone':phone,'revision':revision})).scalar_one_or_none()
        if resumed is not None:
            marker=state_key+':ai-resume-generation'
            seen=await redis.get(marker)
            if isinstance(seen,bytes):seen=seen.decode('ascii',errors='replace')
            if str(seen)!=str(resumed):
                # Only provisional conversation state. Domain rows, journal,
                # verified operation receipts and WhatsApp credentials remain.
                for key in (state_key,state_key+':commerce',state_key+':birthday-invite'):await redis.delete(key)
                await redis.set(marker,str(resumed),ex=7*86400)
                return
    try:
        state = json.loads(await redis.get(state_key) or "null")
    except (ValueError, TypeError):
        return
    if isinstance(state, dict) and state.get("step") == "human":
        await redis.delete(state_key)
        await redis.delete(state_key+":commerce")
        await redis.delete(state_key+':birthday-invite')


class Change(StrictModel):
    contact_e164: str = Field(pattern=r"^\+55[1-9][0-9]{9,10}$")
    expected_revision: StrictInt = Field(ge=1)
    mode: Literal["ai", "human", "paused", "waiting", "closed"]


class SendSlot(StrictModel):
    phone: str = Field(pattern=r"^55[1-9][0-9]{9,10}$")
    operation_id: UUID
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    source: Literal["assistant", "automation", "manual"]
    control_revision: StrictInt | None = Field(default=None, ge=1)
    text: str | None = Field(default=None, min_length=1, max_length=1200)


class FinishSlot(SendSlot):
    accepted: Literal[True]
    provider_id: str | None = Field(default=None, min_length=1, max_length=200)


async def lock_control(db, tenant, channel, phone):
    values = {"t": tenant, "channel": channel, "phone": phone}
    await register_channel(db,tenant,channel)
    await db.execute(text("""INSERT INTO inbox_controls(tenant_id,channel_id,contact_e164)
      VALUES(:t,:channel,:phone) ON CONFLICT DO NOTHING"""), values)
    row = (await db.execute(text("""SELECT * FROM inbox_controls
      WHERE tenant_id=:t AND channel_id=:channel AND contact_e164=:phone FOR UPDATE"""), values)).mappings().one()
    return dict(row)


async def register_channel(db,tenant,channel):
    await db.execute(text("""INSERT INTO inbox_channel_links(tenant_id,channel_id,provider,qr_channel_id,meta_channel_id)
      SELECT tenant_id,id,'qr',id,NULL FROM qr_channels WHERE tenant_id=:t AND id=:channel
      UNION ALL SELECT tenant_id,id,'meta',NULL,id FROM whatsapp_channels WHERE tenant_id=:t AND id=:channel
      ON CONFLICT DO NOTHING"""), {"t":tenant,"channel":channel})


@asynccontextmanager
async def operator_session(tenant,actor,*,write=False):
    # Personal authenticated operator requests never inherit an AI generation.
    # This also makes concurrent in-process callers behave like HTTP requests.
    token=_assistant_scope.set(None)
    try:
        async with management_session(tenant,actor,write=write) as db:
            yield db
    finally:
        _assistant_scope.reset(token)


@asynccontextmanager
async def assistant_turn(tenant, channel, phone, redis=None, state_key=None):
    """Meta and domain tools share the same committed authority as managed QR."""
    if not enabled() or _assistant_scope.get() is not None:
        yield None
        return
    if await blocked(tenant, channel, phone):
        yield False
        return
    revision = await assistant_revision(tenant, channel, phone)
    token = bind_assistant(SimpleNamespace(tenant_id=tenant, channel_id=channel), phone, revision)
    try:
        if redis is not None and state_key is not None:
            await clear_transient_handoff(redis, state_key)
        yield revision
    finally:
        reset_assistant(token)


async def check_channel(db, tenant, channel, phone):
    found = (await db.execute(text("""SELECT public.inbox_channel_contact_allowed(
      CAST(:t AS uuid),CAST(:id AS uuid),CAST(:phone AS text))"""),
      {"t": tenant, "id": channel, "phone": phone.removeprefix('+')})).scalar_one()
    if found is not True:
        raise HTTPException(404, "Canal ou contato indisponível neste estabelecimento")


async def control_event(db, tenant, row, mode, source, actor=None):
    await db.execute(text("""INSERT INTO inbox_control_events(tenant_id,control_id,actor_account_id,source,previous_mode,mode,revision)
      VALUES(:t,:id,:actor,:source,:previous,:mode,:revision)"""),
      {"t": tenant, "id": row["id"], "actor": actor, "source": source, "previous": row["mode"], "mode": mode, "revision": row["revision"]+1})


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
            values = {"t": tenant, "channel": channel, "phone": "+"+phone.removeprefix("+")}
            if _assistant_scope.get() is None:
                await register_channel(db,tenant,channel)
                await db.execute(text("""INSERT INTO inbox_controls(tenant_id,channel_id,contact_e164)
                  VALUES(:t,:channel,:phone) ON CONFLICT DO NOTHING"""), values)
            # A bound revalidation already holds SHARE in guard_transaction.
            # Never upgrade it to UPDATE or issue an upsert: concurrent readers
            # must remain readers while the operator waits to take over.
            row = (await db.execute(text("""SELECT mode,revision,pending_operation FROM inbox_controls
              WHERE tenant_id=:t AND channel_id=:channel AND contact_e164=:phone"""), values)).mappings().one_or_none()
            if not row or row["mode"] != "ai" or row["pending_operation"] is not None:
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
                # A customer may suspend future AI while a provider result is
                # pending. This never clears the slot or assigns an operator.
                if row["mode"] != "human":
                    await db.execute(text("""UPDATE inbox_controls SET mode='human',
                      revision=revision+1,updated_at=clock_timestamp() WHERE tenant_id=:t AND id=:id"""), {"t": tenant, "id": row["id"]})
                    await control_event(db, tenant, row, "human", "customer")
                    await audit(db, tenant, "inbox_customer_handoff", "inbox_controls", row["id"])
    finally:
        _assistant_scope.reset(token)


@router.get(PREFIX)
async def get_control(tenant_id: UUID, channel_id: UUID,
                      phone: str = Query(pattern=r"^\+55[1-9][0-9]{9,10}$"), actor: Actor = Depends(authenticate)):
    require_enabled()
    async with operator_session(tenant_id, actor) as db:
        await check_channel(db, tenant_id, channel_id, phone)
        row = (await db.execute(text("""SELECT mode,assigned_account_id,revision,pending_operation,pending_since
          FROM inbox_controls WHERE tenant_id=:t AND channel_id=:channel AND contact_e164=:phone"""),
          {"t": tenant_id, "channel": channel_id, "phone": phone})).mappings().one_or_none()
        return jsonable_encoder(row or {"mode": "ai", "assigned_account_id": None, "revision": 1, "pending_operation": None, "pending_since": None})


@router.put(PREFIX)
async def change_control(tenant_id: UUID, channel_id: UUID, payload: Change, actor: Actor = Depends(authenticate)):
    if payload.mode == "ai":
        from app.plan_permissions import require_tenant_feature
        await require_tenant_feature(tenant_id, "reception.ai")
    require_enabled()
    if not isinstance(actor.account_id, UUID):
        raise HTTPException(403, "Entre novamente com uma conta habilitada na central para assumir um atendimento")
    async with operator_session(tenant_id, actor, write=True) as db:
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
        await control_event(db, tenant_id, row, payload.mode, "operator", actor.account_id)
        # Old Meta handoff endpoints and Redis state no longer own AI access.
        if payload.mode == "ai":
            await db.execute(text("""UPDATE human_handoffs SET status='resolved',expires_at=now()
              WHERE tenant_id=:t AND channel_id=:c AND contact_e164=:phone AND status='active'"""),
              {"t":tenant_id,"c":channel_id,"phone":payload.contact_e164})
            await db.execute(text("""UPDATE automation_contact_state SET human_paused=false,revision=revision+1
              WHERE tenant_id=:t AND contact_e164=:phone AND human_paused AND NOT EXISTS(
                SELECT 1 FROM inbox_controls WHERE tenant_id=:t AND contact_e164=:phone AND mode<>'ai')"""),
              {'t':tenant_id,'phone':payload.contact_e164})
        return jsonable_encoder(result)


@router.get(PREFIX+"/history")
async def control_history(tenant_id: UUID, channel_id: UUID,
                          phone: str = Query(pattern=r"^\+55[1-9][0-9]{9,10}$"), actor: Actor = Depends(authenticate)):
    require_enabled()
    async with operator_session(tenant_id, actor) as db:
        await check_channel(db, tenant_id, channel_id, phone)
        rows = (await db.execute(text("""SELECT e.id,e.actor_account_id,e.source,e.previous_mode,e.mode,e.revision,e.occurred_at
          FROM inbox_control_events e JOIN inbox_controls c ON c.tenant_id=e.tenant_id AND c.id=e.control_id
          WHERE c.tenant_id=:t AND c.channel_id=:channel AND c.contact_e164=:phone
          ORDER BY e.revision DESC LIMIT 100"""), {"t": tenant_id, "channel": channel_id, "phone": phone})).mappings().all()
        return jsonable_encoder({"items": rows, "limit": 100})


@asynccontextmanager
async def bridge_control(request, payload):
    from app.qr_channels import authenticated_channel
    identity = await authenticated_channel(request, phone=payload.phone)
    if not identity.central_adopted:
        require_enabled()
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
        from app.plan_permissions import require_feature
        if payload.source == "automation":
            # The persisted job, rather than the bridge/browser label, selects
            # the capability. Operational messages remain part of Essencial.
            job_event = (await db.execute(text("""SELECT event FROM automation_jobs
                WHERE tenant_id=:t AND id=:id AND channel_id=:channel AND status='sending'"""),
                {"t": identity.tenant_id, "id": payload.operation_id, "channel": channel_id})).scalar_one_or_none()
            if job_event is not None:
                from app.automation_policy import plan_feature
                feature = plan_feature(job_event)
            else:
                notification = (await db.execute(text("""SELECT 1 FROM appointment_notifications
                    WHERE tenant_id=:t AND id=:id AND channel_id=:channel AND status='sending'"""),
                    {"t": identity.tenant_id, "id": payload.operation_id, "channel": channel_id})).scalar_one_or_none()
                feature = "notifications.basic" if notification else None
            if feature is None:
                raise HTTPException(403, "Operação automática sem autorização atual")
        else:
            feature = "reception.ai" if payload.source == "assistant" else "conversations.manual"
        await require_feature(db, identity.tenant_id, feature)
        if payload.source == "assistant" and not identity.assistant_enabled:
            raise HTTPException(409, "Assistente desativada")
        if payload.source == "assistant":
            # The credential lookup precedes this transaction. Check bot/AI
            # flags again under the domain's existing SHARE locks, so a toggle
            # between lookup and claim cannot authorize a stale AI response.
            live = (await db.execute(text("""SELECT public.qr_channel_transaction_guard(
              CAST(:t AS uuid),CAST(:c AS uuid),CAST(:a AS uuid),CAST(:o AS uuid),CAST(:f AS bigint))"""),
              {"t":identity.tenant_id,"c":identity.channel_id,"a":identity.owner_account_id,
               "o":identity.lease_owner,"f":identity.fence})).scalar_one()
            if live is not True:
                raise HTTPException(409, "A autorização da assistente mudou")
        if payload.source in {"assistant", "manual"} and payload.control_revision != row["revision"]:
            raise HTTPException(409, "O controle mudou durante a preparação da resposta")
        if payload.source == "manual":
            from app.inbox_runtime import manual_slot
            await manual_slot(db, identity.tenant_id, row, payload)
            return {"authorized": True, "operation_id": payload.operation_id}
        if row["mode"] != "ai" or row["pending_operation"] is not None:
            raise HTTPException(409, "Atendimento ocupado ou suspenso")
        # The legacy company-wide marketing pause remains a conservative veto.
        paused = (await db.execute(text("""SELECT human_paused FROM automation_contact_state
          WHERE tenant_id=:t AND contact_e164=:phone"""), {"t": identity.tenant_id, "phone": "+"+payload.phone})).scalar_one_or_none()
        if paused and payload.source == "automation":
            raise HTTPException(409, "Atendimento suspenso")
        if payload.text is not None:
            from app.inbox_runtime import record_outbound, fingerprint
            if fingerprint(payload.phone, payload.text) != payload.fingerprint:
                raise HTTPException(409, "O texto não corresponde à operação")
            await record_outbound(db, identity.tenant_id, row, payload.operation_id, payload.source,
                                  payload.text, payload.fingerprint, status="sending")
        await db.execute(text("""UPDATE inbox_controls SET pending_operation=:operation,pending_fingerprint=:fingerprint,
          pending_since=clock_timestamp() WHERE tenant_id=:t AND id=:id"""),
          {"t": identity.tenant_id, "id": row["id"], "operation": payload.operation_id, "fingerprint": payload.fingerprint})
        return {"authorized": True, "operation_id": payload.operation_id}


@router.post("/internal/qr/channels/{channel_id}/send-finish", include_in_schema=False)
async def finish_send(channel_id: UUID, payload: FinishSlot, request: Request):
    async with bridge_control(request, payload) as (db, identity, row):
        if row["pending_operation"] != payload.operation_id or row["pending_fingerprint"] != payload.fingerprint:
            raise HTTPException(409, "Recibo não corresponde ao envio pendente")
        from app.inbox_runtime import accepted
        await accepted(db, identity.tenant_id, payload.operation_id, payload.provider_id)
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
    try:
        record = await inspect_automation(tenant_id, channel_id, str(payload.operation_id))
    except (ValueError,__import__('httpx').HTTPError):
        raise HTTPException(503,"O recibo está indisponível. A suspensão foi preservada") from None
    if record.get("operation_id") != str(payload.operation_id) or not (
        record.get("state") == "accepted" and record.get("accepted") is True
        or record.get("state") == "refused" and record.get("delivery") == "not_sent"
    ):
        raise HTTPException(409, "O resultado continua incerto. A suspensão foi preservada")
    async with operator_session(tenant_id, actor, write=True) as db:
        exists=(await db.execute(text('SELECT id FROM inbox_controls WHERE tenant_id=:t AND channel_id=:c AND contact_e164=:phone'),
            {'t':tenant_id,'c':channel_id,'phone':payload.contact_e164})).scalar_one_or_none()
        if exists is None:
            raise HTTPException(404,'Conversa não encontrada')
        row = await lock_control(db, tenant_id, channel_id, payload.contact_e164)
        if row["revision"] != payload.expected_revision or row["pending_operation"] != payload.operation_id:
            raise HTTPException(409, "O atendimento mudou. Atualize antes de verificar")
        await db.execute(text("""UPDATE inbox_controls SET pending_operation=NULL,pending_fingerprint=NULL,pending_since=NULL
          WHERE tenant_id=:t AND id=:id"""), {"t": tenant_id, "id": row["id"]})
        if record.get('accepted') is True:
            from app.inbox_runtime import accepted
            await accepted(db,tenant_id,payload.operation_id,None)
        else:
            await db.execute(text("""UPDATE inbox_messages SET status='failed',updated_at=clock_timestamp()
              WHERE tenant_id=:t AND id=:id AND control_id=:c AND status IN ('sending','uncertain')"""),
              {'t':tenant_id,'id':payload.operation_id,'c':row['id']})
        await audit(db, tenant_id, "inbox_send_reconciled", "inbox_controls", row["id"])
        return {"released": True, "delivery": record.get("delivery", "unknown")}
