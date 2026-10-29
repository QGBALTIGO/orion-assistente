"""Owner-scoped configuration and bounded diagnostics; never sends on a GET."""
import re
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import Field, StrictBool, field_validator
from sqlalchemy import text

from app import appointment_access as access
from app.catalog_api import Actor, authenticate, audit, support_session_factory
from app.catalog_schemas import StrictModel
from app.management_api import management_session
from app.tenant_access import resolve_tenant_access

router=APIRouter(prefix='/api/v1/tenants/{tenant_id}/appointment-communications',tags=['Comunicação de agendamentos'])
DEFAULTS={'enabled':False,'channel_id':None,'confirmation':True,'professional_notice':True,'cancellation':True,
    'rescheduling':True,'reschedule_acceptance':False,'reminder':True,'review':False,'return_invitation':False,'review_delay_min':15,
    'signature':'Orion Agenda','version':0}


class Settings(StrictModel):
    expected_version:int=Field(ge=0,strict=True)
    enabled:StrictBool
    channel_id:UUID|None=None
    confirmation:StrictBool=True
    professional_notice:StrictBool=True
    cancellation:StrictBool=True
    rescheduling:StrictBool=True
    reschedule_acceptance:StrictBool=False
    reminder:StrictBool=True
    review:StrictBool=False
    return_invitation:StrictBool=False
    review_delay_min:int=Field(default=15,ge=1,le=1440,strict=True)
    signature:str=Field(default='Orion Agenda',min_length=1,max_length=80)
    acknowledged:StrictBool=False

    @field_validator('signature')
    @classmethod
    def signature_text(cls,value):
        if re.search(r'[\r\n<>]|https?://|www\.',value,re.I):raise ValueError('Assinatura inválida')
        return value.strip()


@router.get('')
async def configuration(tenant_id:UUID,actor:Actor=Depends(authenticate)):
    await resolve_tenant_access(actor,tenant_id)
    if not access.enabled():return {'available':False,'settings':DEFAULTS,'notifications':[],'reviews':[]}
    async with management_session(tenant_id,actor) as db:
        settings=(await db.execute(text('SELECT * FROM appointment_communication_settings WHERE tenant_id=:t'),{'t':tenant_id})).mappings().first()
        notifications=(await db.execute(text('''SELECT n.id,n.event,n.recipient_kind,n.status,n.due_at,n.attempted_at,
            n.updated_at,n.last_error,n.attempts,a.protocol FROM appointment_notifications n
            JOIN appointments a ON a.tenant_id=n.tenant_id AND a.id=n.appointment_id
            WHERE n.tenant_id=:t ORDER BY n.created_at DESC LIMIT 100'''),{'t':tenant_id})).mappings().all()
        reviews=(await db.execute(text('''SELECT r.rating,r.comment,r.created_at,a.protocol,p.name AS professional
            FROM appointment_reviews r JOIN appointments a ON a.tenant_id=r.tenant_id AND a.id=r.appointment_id
            JOIN professionals p ON p.tenant_id=r.tenant_id AND p.id=r.professional_id
            WHERE r.tenant_id=:t ORDER BY r.created_at DESC LIMIT 50'''),{'t':tenant_id})).mappings().all()
    async with support_session_factory()() as registry:
        from app.qr_channels import _canonical
        channel,state=await _canonical(registry,tenant_id)
    return jsonable_encoder({'available':True,'settings':dict(settings) if settings else DEFAULTS,
        'canonical_channel_id':channel['id'] if channel is not None and state=='ready' else None,
        'notifications':[dict(x) for x in notifications],'reviews':[dict(x) for x in reviews],
        'transport_limit':'Envio restrito aos contatos autorizados no canal QR; API oficial não ativada por este fluxo.'})


@router.put('')
async def save_configuration(tenant_id:UUID,payload:Settings,actor:Actor=Depends(authenticate)):
    access.require_enabled()
    await resolve_tenant_access(actor,tenant_id,'publish')
    if payload.enabled and not payload.acknowledged:
        raise HTTPException(422,'Confirme a autorização dos contatos e a revisão das mensagens antes de ativar')
    async with support_session_factory()() as registry:
        from app.qr_channels import _canonical
        channel,state=await _canonical(registry,tenant_id)
    if payload.enabled and (not channel or state!='ready' or channel['id']!=payload.channel_id or not channel['enabled']):
        raise HTTPException(409,'Selecione a conexão atual da empresa')
    async with management_session(tenant_id,actor,write=True) as db:
        await db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key,0))'),{'key':'appointment-settings:'+str(tenant_id)})
        current=(await db.execute(text('SELECT version FROM appointment_communication_settings WHERE tenant_id=:t FOR UPDATE'),{'t':tenant_id})).scalar_one_or_none()
        if (current or 0)!=payload.expected_version:raise HTTPException(409,'A configuração mudou. Atualize antes de salvar')
        fields=payload.model_dump(exclude={'expected_version','acknowledged'})
        # Columns are server-defined Pydantic fields, never free SQL identifiers.
        columns=list(fields)
        await db.execute(text('INSERT INTO appointment_communication_settings(tenant_id,'+','.join(columns)+') VALUES(:tenant,'+
            ','.join(':'+x for x in columns)+') ON CONFLICT(tenant_id) DO UPDATE SET '+
            ','.join(x+'=EXCLUDED.'+x for x in columns)+',version=appointment_communication_settings.version+1,updated_at=clock_timestamp()'),
            {'tenant':tenant_id,**fields})
        await db.execute(text("UPDATE appointment_notifications SET status='cancelled',last_error='settings_changed' WHERE tenant_id=:t AND status='pending'"),{'t':tenant_id})
        await audit(db,tenant_id,'appointment_communication_settings','appointment_communication_settings',tenant_id)
    return {'saved':True,'version':(current or 0)+1}
