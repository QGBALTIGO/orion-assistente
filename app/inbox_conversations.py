"""Human central: bounded history, personal assignment and durable enqueue."""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from pydantic import Field, StrictInt, field_validator
from sqlalchemy import text

from app.catalog_api import Actor, authenticate, audit
from app.catalog_schemas import StrictModel
from app.management_api import management_session
from app import inbox_control as control, inbox_runtime as runtime

router = APIRouter(tags=["Central de atendimento"])
P = "/api/v1/tenants/{tenant_id}/management/inbox"


class Message(StrictModel):
    contact_e164: str = Field(pattern=r"^\+55[1-9][0-9]{9,10}$")
    expected_revision: StrictInt = Field(ge=1)
    operation_id: UUID
    text: str = Field(min_length=1,max_length=1200)

    @field_validator("text")
    @classmethod
    def bounded_text(cls,value):
        if not value.strip() or any(ord(c)<32 and c not in "\n\t" or 0xD800<=ord(c)<=0xDFFF for c in value):
            raise ValueError("Texto inválido")
        if len(value.encode("utf-16-le"))//2>1200:
            raise ValueError("Limite de 1200 caracteres excedido")
        return value


@router.get(P+"/channels")
async def channels(tenant_id: UUID, actor: Actor=Depends(authenticate)):
    control.require_enabled()
    async with management_session(tenant_id,actor) as db:
        rows=(await db.execute(text("""SELECT id,'qr' AS provider,label,enabled,assistant_enabled FROM qr_channels WHERE tenant_id=:t
          UNION ALL SELECT id,'meta',business_number,status IN ('testing','connected'),bot_enabled FROM whatsapp_channels WHERE tenant_id=:t
          ORDER BY provider,label,id"""),{"t":tenant_id})).mappings().all()
        return jsonable_encoder({"items":rows,"send_enabled":runtime.send_enabled(),"account_id":actor.account_id})


@router.get(P+"/conversations")
async def conversations(tenant_id: UUID, channel_id: UUID|None=None, mode: str=Query(default="",pattern=r"^(|ai|human|waiting|paused|closed)$"),
                        q: str=Query(default="",max_length=100), offset: int=Query(default=0,ge=0,le=20000), actor: Actor=Depends(authenticate), unread:bool=False):
    control.require_enabled()
    async with management_session(tenant_id,actor) as db:
        rows=(await db.execute(text("""SELECT c.channel_id,c.contact_e164,c.mode,c.assigned_account_id,c.revision,
          c.pending_operation,c.updated_at,l.provider,p.id AS customer_id,p.name,stats.message_count,stats.unread_count,
          (SELECT body FROM inbox_messages m WHERE m.tenant_id=c.tenant_id AND m.control_id=c.id ORDER BY m.created_at DESC,m.id DESC LIMIT 1) AS preview
          FROM inbox_controls c JOIN inbox_channel_links l ON l.tenant_id=c.tenant_id AND l.channel_id=c.channel_id
          LEFT JOIN customers p ON p.tenant_id=c.tenant_id AND p.whatsapp_e164=c.contact_e164 AND p.active AND p.deleted_at IS NULL
          LEFT JOIN inbox_reads r ON r.tenant_id=c.tenant_id AND r.control_id=c.id AND r.account_id=CAST(:account AS uuid)
          CROSS JOIN LATERAL(SELECT count(*) AS message_count,count(*) FILTER(WHERE direction='in' AND
            (r.message_id IS NULL OR (created_at,id)>(r.read_at,r.message_id))) AS unread_count
            FROM inbox_messages WHERE tenant_id=c.tenant_id AND control_id=c.id) stats
          WHERE c.tenant_id=:t AND (CAST(:channel AS uuid) IS NULL OR c.channel_id=:channel)
          AND (:mode='' OR c.mode=:mode) AND (NOT :unread OR stats.unread_count>0)
          AND (:q='' OR position(lower(:q) in lower(COALESCE(p.name,'')||' '||c.contact_e164))>0 OR EXISTS(
            SELECT 1 FROM (SELECT body FROM inbox_messages WHERE tenant_id=c.tenant_id AND control_id=c.id
              ORDER BY created_at DESC,id DESC LIMIT 100) recent WHERE position(lower(:q) in lower(recent.body))>0))
          ORDER BY c.updated_at DESC,c.channel_id,c.contact_e164 LIMIT 51 OFFSET :offset"""),
          {"t":tenant_id,"channel":channel_id,"mode":mode,"q":q.strip(),"offset":offset,"account":actor.account_id,"unread":unread})).mappings().all()
        return jsonable_encoder({"items":rows[:50],"has_more":len(rows)>50,"limit":50})


@router.get(P+"/channels/{channel_id}/messages")
async def messages(tenant_id: UUID, channel_id: UUID, phone: str=Query(pattern=r"^\+55[1-9][0-9]{9,10}$"),
                   before: UUID|None=None, actor: Actor=Depends(authenticate)):
    control.require_enabled()
    async with management_session(tenant_id,actor) as db:
        row=(await db.execute(text("SELECT * FROM inbox_controls WHERE tenant_id=:t AND channel_id=:c AND contact_e164=:phone"),
                              {"t":tenant_id,"c":channel_id,"phone":phone})).mappings().one_or_none()
        if not row:
            raise HTTPException(404,"Conversa não encontrada")
        items=(await db.execute(text("""SELECT id,direction,source,kind,body,status,created_at,actor_account_id,external_id
          FROM inbox_messages WHERE tenant_id=:t AND control_id=:c AND (CAST(:before AS uuid) IS NULL OR (created_at,id)<
            (SELECT created_at,id FROM inbox_messages WHERE tenant_id=:t AND control_id=:c AND id=:before))
          ORDER BY created_at DESC,id DESC LIMIT 101"""), {"t":tenant_id,"c":row["id"],"before":before})).mappings().all()
        customer=(await db.execute(text("SELECT id FROM customers WHERE tenant_id=:t AND whatsapp_e164=:phone AND active AND deleted_at IS NULL"),
                                   {"t":tenant_id,"phone":phone})).scalar_one_or_none()
        return jsonable_encoder({"control":{k:row[k] for k in ("mode","assigned_account_id","revision","pending_operation","pending_since")},
                                 "customer_id":customer,"items":list(reversed(items[:100])),"has_more":len(items)>100,
                                 "next_before":items[99]["id"] if len(items)>100 else None})


class ReadThrough(StrictModel):
    contact_e164: str=Field(pattern=r"^\+55[1-9][0-9]{9,10}$")
    message_id: UUID


@router.post(P+'/channels/{channel_id}/read')
async def mark_read(tenant_id:UUID,channel_id:UUID,payload:ReadThrough,actor:Actor=Depends(authenticate)):
    control.require_enabled()
    if not isinstance(actor.account_id,UUID):
        raise HTTPException(403,'Entre com sua conta pessoal para marcar leitura')
    async with management_session(tenant_id,actor) as db:
        saved=(await db.execute(text('''INSERT INTO inbox_reads(tenant_id,control_id,account_id,message_id,read_at)
          SELECT m.tenant_id,m.control_id,:account,m.id,m.created_at FROM inbox_messages m JOIN inbox_controls c
          ON c.tenant_id=m.tenant_id AND c.id=m.control_id WHERE m.tenant_id=:t AND m.id=:id
            AND c.channel_id=:channel AND c.contact_e164=:phone
          ON CONFLICT(tenant_id,control_id,account_id) DO UPDATE SET message_id=EXCLUDED.message_id,read_at=EXCLUDED.read_at
          WHERE (inbox_reads.read_at,inbox_reads.message_id)<(EXCLUDED.read_at,EXCLUDED.message_id)
          RETURNING control_id'''),{'t':tenant_id,'channel':channel_id,'phone':payload.contact_e164,'id':payload.message_id,'account':actor.account_id})).scalar_one_or_none()
        if saved is None:
            valid=(await db.execute(text('''SELECT 1 FROM inbox_messages m JOIN inbox_controls c ON c.tenant_id=m.tenant_id AND c.id=m.control_id
              WHERE m.tenant_id=:t AND m.id=:id AND c.channel_id=:channel AND c.contact_e164=:phone'''),
              {'t':tenant_id,'channel':channel_id,'phone':payload.contact_e164,'id':payload.message_id})).first()
            if not valid:raise HTTPException(404,'Mensagem não encontrada nesta conversa')
    return {'read':True,'provider_read_receipt':False}


@router.post(P+"/channels/{channel_id}/messages",status_code=202)
async def enqueue(tenant_id: UUID, channel_id: UUID, payload: Message, actor: Actor=Depends(authenticate)):
    control.require_enabled()
    if not runtime.send_enabled():
        raise HTTPException(423,"O envio pela central ainda não foi habilitado nesta instalação")
    if not isinstance(actor.account_id,UUID):
        raise HTTPException(403,"Entre com sua conta pessoal para enviar")
    digest=runtime.fingerprint(payload.contact_e164,payload.text)
    async with management_session(tenant_id,actor,write=True) as db:
        await control.check_channel(db,tenant_id,channel_id,payload.contact_e164)
        row=await control.lock_control(db,tenant_id,channel_id,payload.contact_e164)
        existing=(await db.execute(text("SELECT * FROM inbox_messages WHERE tenant_id=:t AND id=:id"),{"t":tenant_id,"id":payload.operation_id})).mappings().one_or_none()
        if existing:
            if existing["control_id"]!=row["id"] or existing["source"]!="manual" or existing["actor_account_id"]!=actor.account_id or existing["fingerprint"]!=digest:
                raise HTTPException(409,"Identificação de envio já usada por outra mensagem")
            return {"operation_id":payload.operation_id,"status":existing["status"],"sent":existing["status"] in {"accepted","delivered","read"}}
        if row["revision"]!=payload.expected_revision or row["mode"] not in {"human","waiting"} or row["assigned_account_id"]!=actor.account_id:
            raise HTTPException(409,"Assuma a conversa e atualize o controle antes de enviar")
        if row["pending_operation"] is not None:
            raise HTTPException(409,"Há um envio em verificação. Aguarde o recibo")
        version=await runtime.operator_version(db,tenant_id,actor.account_id)
        if version is None:
            raise HTTPException(403,"Permissão de atendimento revogada")
        await runtime.record_outbound(db,tenant_id,row,payload.operation_id,"manual",payload.text,digest,actor=actor.account_id,auth_version=version)
        await audit(db,tenant_id,"inbox_message_queued","inbox_messages",payload.operation_id)
        return {"operation_id":payload.operation_id,"status":"queued","sent":False}


class Journal(StrictModel):
    phone: str=Field(pattern=r"^55[1-9][0-9]{9,10}$")
    message_id: str=Field(pattern=r"^[a-zA-Z0-9._:-]{1,160}$")
    text: str=Field(default="",max_length=1500)
    kind: str=Field(default="text",pattern=r"^(text|audio|unsupported)$")


@router.post("/internal/qr/channels/{channel_id}/messages",include_in_schema=False)
async def journal(channel_id: UUID,payload: Journal,request: Request):
    from app.qr_channels import authenticated_channel
    from app.database import get_session_factory,tenant_transaction
    from app.automation_runtime import _notice_guard,_notice_identity
    identity=await authenticated_channel(request,phone=payload.phone)
    if not identity.central_adopted:
        control.require_enabled()
    async with get_session_factory()() as db:
        async with tenant_transaction(db,identity.tenant_id):
            guard=_notice_identity(identity.tenant_id,payload.phone,identity)
            await _notice_guard(db,guard)
            await runtime.record_inbound(db,identity.tenant_id,channel_id,payload.phone,payload.message_id,payload.text,payload.kind)
            await _notice_guard(db,guard)
    return {"recorded":True}


class Receipt(StrictModel):
    phone: str=Field(pattern=r"^55[1-9][0-9]{9,10}$")
    operation_id: UUID
    provider_id: str=Field(min_length=1,max_length=200)
    delivery: str=Field(pattern=r"^(delivered|read)$")


@router.post("/internal/qr/channels/{channel_id}/message-receipt",include_in_schema=False)
async def receipt(channel_id: UUID,payload: Receipt,request: Request):
    async with control.bridge_control(request,payload) as (db,identity,row):
        item=(await db.execute(text("SELECT external_id FROM inbox_messages WHERE tenant_id=:t AND id=:id AND control_id=:c AND direction='out'"),
                              {"t":identity.tenant_id,"id":payload.operation_id,"c":row["id"]})).scalar_one_or_none()
        if item!=payload.provider_id:
            raise HTTPException(409,"Recibo não corresponde à mensagem")
        await runtime.provider_receipt(db,identity.tenant_id,channel_id,"+"+payload.phone,payload.provider_id,payload.delivery)
    return {"recorded":True}
