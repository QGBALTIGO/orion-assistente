"""Owner-scoped configuration and bounded diagnostics; never sends on a GET."""
import re
import os
from uuid import UUID,uuid5,NAMESPACE_URL
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import Field, StrictBool, StrictInt, field_validator
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
    return_intervals:dict[UUID,Annotated[StrictInt,Field(ge=1,le=730)]|None]=Field(default_factory=dict,max_length=200)
    acknowledged:StrictBool=False

    @field_validator('signature')
    @classmethod
    def signature_text(cls,value):
        if re.search(r'[\r\n<>]|https?://|www\.',value,re.I):raise ValueError('Assinatura inválida')
        return value.strip()


@router.get('')
async def configuration(tenant_id:UUID,actor:Actor=Depends(authenticate)):
    await resolve_tenant_access(actor,tenant_id)
    if not access.tenant_enabled(tenant_id):return {'available':False,'settings':DEFAULTS,'notifications':[],'reviews':[]}
    async with management_session(tenant_id,actor) as db:
        zone=(await db.execute(text('SELECT timezone FROM tenants WHERE id=:t'),{'t':tenant_id})).scalar_one()
        settings=(await db.execute(text('SELECT * FROM appointment_communication_settings WHERE tenant_id=:t'),{'t':tenant_id})).mappings().first()
        notifications=(await db.execute(text('''SELECT n.id,n.event,n.recipient_kind,n.status,n.due_at,n.attempted_at,
            n.updated_at,n.last_error,n.attempts,a.protocol,CASE WHEN n.recipient_kind='professional' THEN
            (SELECT p.name FROM professionals p WHERE p.tenant_id=n.tenant_id AND p.id=n.recipient_id) ELSE
            (SELECT c.name FROM customers c WHERE c.tenant_id=n.tenant_id AND c.id=n.recipient_id) END AS recipient_label
            FROM appointment_notifications n
            JOIN appointments a ON a.tenant_id=n.tenant_id AND a.id=n.appointment_id
            WHERE n.tenant_id=:t ORDER BY n.created_at DESC LIMIT 100'''),{'t':tenant_id})).mappings().all()
        services=(await db.execute(text('''SELECT s.id,s.name,r.days FROM services s
            LEFT JOIN appointment_return_intervals r ON r.tenant_id=s.tenant_id AND r.service_id=s.id
            WHERE s.tenant_id=:t AND s.active AND s.catalog_kind<>'variant' ORDER BY s.name,s.id LIMIT 200'''),{'t':tenant_id})).mappings().all()
        reviews=(await db.execute(text('''SELECT r.rating,r.comment,r.created_at,a.protocol,p.name AS professional
            FROM appointment_reviews r JOIN appointments a ON a.tenant_id=r.tenant_id AND a.id=r.appointment_id
            JOIN professionals p ON p.tenant_id=r.tenant_id AND p.id=r.professional_id
            WHERE r.tenant_id=:t ORDER BY r.created_at DESC LIMIT 50'''),{'t':tenant_id})).mappings().all()
    async with support_session_factory()() as registry:
        from app.qr_channels import _canonical
        channel,state=await _canonical(registry,tenant_id)
    return jsonable_encoder({'available':True,'timezone':zone,'settings':dict(settings) if settings else DEFAULTS,
        'pilot_only':access.pilot_only(),
        'pilot_prepared':bool(access.pilot_only() and settings and settings['enabled'] and channel is not None and
            os.getenv('APPOINTMENT_COMMUNICATIONS_PILOT_PHONE','') in channel['allowed_senders']),
        'return_services':[dict(x) for x in services],'canonical_channel_id':channel['id'] if channel is not None and state=='ready' else None,
        'notifications':[dict(x) for x in notifications],'reviews':[dict(x) for x in reviews],
        'transport_limit':'Envio restrito aos contatos autorizados no canal QR; API oficial não ativada por este fluxo.'})


class PilotPreparation(StrictModel):
    acknowledged:StrictBool


class RecoverConfirmation(StrictModel):
    protocol:str=Field(pattern=r'^AG-[A-HJ-NP-Z2-9]{6}$')

    @field_validator('protocol',mode='before')
    @classmethod
    def normalize_protocol(cls,value):
        return value.strip().upper() if isinstance(value,str) else value


@router.post('/recover-confirmation')
async def recover_confirmation(tenant_id:UUID,payload:RecoverConfirmation,actor:Actor=Depends(authenticate)):
    """Admin recovery of a real, recent inbound; never accepts a phone or URL."""
    access.require_tenant(tenant_id)
    if actor.role!='admin' or not isinstance(actor.account_id,UUID):
        raise HTTPException(403,'A confirmação manual exige o administrador autenticado')
    from app.qr_legacy import configured_tenant,channel_id
    from app.qr_channels import _owned_channel
    from app.appointment_confirmation import confirmation_reply,detected_protocol
    from app.appointment_messages import is_generic_response
    from app.inbox_conversations import enqueue_preserved_manual
    if configured_tenant()!=tenant_id:raise HTTPException(403,'Use a conexão do admin')
    channel=channel_id(tenant_id)
    await _owned_channel(tenant_id,channel,actor)
    async with management_session(tenant_id,actor) as db:
        incoming=(await db.execute(text("""SELECT m.id,m.body,c.contact_e164 FROM inbox_messages m
            JOIN inbox_controls c ON c.tenant_id=m.tenant_id AND c.id=m.control_id
            JOIN appointments a ON a.tenant_id=c.tenant_id AND a.protocol=:protocol
            JOIN customers k ON k.tenant_id=a.tenant_id AND k.id=a.customer_id AND k.whatsapp_e164=c.contact_e164
            WHERE m.tenant_id=:t AND c.channel_id=:channel AND m.direction='in' AND m.source='customer'
              AND m.kind='text' AND m.external_id IS NOT NULL AND k.active AND k.deleted_at IS NULL
              AND m.created_at>clock_timestamp()-interval '24 hours'
              AND position(:protocol IN upper(m.body))>0 ORDER BY m.created_at DESC,m.id DESC LIMIT 1"""),
            {'t':tenant_id,'channel':channel,'protocol':payload.protocol})).mappings().first()
    if not incoming or detected_protocol(incoming['body'])!=payload.protocol:
        raise HTTPException(404,'Não há uma solicitação recente desse protocolo pelo contato cadastrado')
    if not access.recipient_allowed(incoming['contact_e164']):
        raise HTTPException(403,'O contato ainda não está autorizado para o piloto')
    operation=uuid5(NAMESPACE_URL,'orion:appointment-confirmation-recovery:'+str(tenant_id)+':'+str(incoming['id']))
    answer=await confirmation_reply(tenant_id,incoming['contact_e164'],incoming['body'],'qr:'+str(channel),'recovery:'+str(incoming['id']))
    if not answer or is_generic_response(answer):raise HTTPException(409,'Atualize as permissões e os dados do agendamento antes de confirmar')
    return await enqueue_preserved_manual(tenant_id,channel,incoming['contact_e164'],answer,operation,actor)


@router.post('/prepare-admin-pilot')
async def prepare_admin_pilot(tenant_id:UUID,payload:PilotPreparation,actor:Actor=Depends(authenticate)):
    """Authorize only the verified connected self number; never pair or reset it."""
    access.require_tenant(tenant_id)
    if not access.pilot_only() or actor.role!='admin' or not isinstance(actor.account_id,UUID) or not payload.acknowledged:
        raise HTTPException(403,'Preparação exclusiva do administrador no piloto')
    from app.qr_legacy import channel_id,configured_tenant
    from app.qr_channels import _owned_channel,_bridge_control,_control_result,_owner,_require_canonical
    if tenant_id!=configured_tenant():raise HTTPException(403,'O piloto exige a conexão preservada do admin')
    channel=channel_id(tenant_id)
    row=await _owned_channel(tenant_id,channel,actor)
    observed=_control_result(row,await _bridge_control(row,'status','GET'),'status')
    phone=observed.get('phone')
    if not observed.get('phone_verified') or not phone or not access.recipient_allowed(phone):
        raise HTTPException(409,'Confira o número conectado e o número autorizado do piloto')
    async with support_session_factory()() as registry:
        async with registry.begin():
            await _owner(registry,tenant_id,actor)
            current=await _require_canonical(registry,tenant_id,channel,lock=True)
            if current['owner_account_id']!=actor.account_id or current['revision']!=row['revision'] or not current['enabled']:
                raise HTTPException(409,'A conexão mudou. Atualize antes de preparar o piloto')
            allowed=list(current['allowed_senders'])
            if phone not in allowed:
                if len(allowed)>=10:raise HTTPException(409,'O canal já possui dez contatos autorizados; revise a política')
                import json
                allowed.append(phone)
                await registry.execute(text('UPDATE qr_channels SET allowed_senders=CAST(:phones AS jsonb),revision=revision+1,updated_at=clock_timestamp() WHERE tenant_id=:t AND id=:c'),
                    {'phones':json.dumps(allowed),'t':tenant_id,'c':channel})
    async with management_session(tenant_id,actor,write=True) as db:
        await db.execute(text("""INSERT INTO contact_consents(tenant_id,contact_e164,operational_opt_in,marketing_opt_in,source)
            VALUES(:t,:phone,true,true,'owner_attestation') ON CONFLICT(tenant_id,contact_e164) DO UPDATE
            SET operational_opt_in=true,marketing_opt_in=true,source='owner_attestation',updated_at=clock_timestamp()"""),
            {'t':tenant_id,'phone':'+'+phone})
        current_settings=(await db.execute(text('SELECT * FROM appointment_communication_settings WHERE tenant_id=:t'),{'t':tenant_id})).mappings().first()
        await audit(db,tenant_id,'appointment_admin_pilot_prepared','qr_channels',channel)
    if not current_settings or not current_settings['enabled'] or current_settings['channel_id']!=channel:
        await save_configuration(tenant_id,Settings(expected_version=current_settings['version'] if current_settings else 0,enabled=True,channel_id=channel,
            review=True,return_invitation=False,acknowledged=True),actor)
    return {'prepared':True,'recipient_suffix':phone[-4:],'session_preserved':True,'messages_sent':0}


@router.put('')
async def save_configuration(tenant_id:UUID,payload:Settings,actor:Actor=Depends(authenticate)):
    access.require_tenant(tenant_id)
    if access.pilot_only() and actor.role!='admin':raise HTTPException(403,'O piloto exige a conta de admin')
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
        fields=payload.model_dump(exclude={'expected_version','acknowledged','return_intervals'})
        ids=list(payload.return_intervals)
        if ids:
            count=(await db.execute(text('SELECT count(*) FROM services WHERE tenant_id=:t AND id=ANY(CAST(:ids AS uuid[])) AND active'),{'t':tenant_id,'ids':ids})).scalar_one()
            if count!=len(ids):raise HTTPException(422,'Revise os serviços da sua empresa antes de salvar')
            for service,days in payload.return_intervals.items():
                await db.execute(text('''INSERT INTO appointment_return_intervals(tenant_id,service_id,days)
                    VALUES(:t,:id,:days) ON CONFLICT(tenant_id,service_id) DO UPDATE SET days=EXCLUDED.days,updated_at=clock_timestamp()'''),
                    {'t':tenant_id,'id':service,'days':days})
        # Columns are server-defined Pydantic fields, never free SQL identifiers.
        columns=list(fields)
        await db.execute(text('INSERT INTO appointment_communication_settings(tenant_id,'+','.join(columns)+') VALUES(:tenant,'+
            ','.join(':'+x for x in columns)+') ON CONFLICT(tenant_id) DO UPDATE SET '+
            ','.join(x+'=EXCLUDED.'+x for x in columns)+',version=appointment_communication_settings.version+1,updated_at=clock_timestamp()'),
            {'tenant':tenant_id,**fields})
        await db.execute(text("UPDATE appointment_notifications SET status='cancelled',last_error='settings_changed' WHERE tenant_id=:t AND status='pending'"),{'t':tenant_id})
        await audit(db,tenant_id,'appointment_communication_settings','appointment_communication_settings',tenant_id)
    return {'saved':True,'version':(current or 0)+1}
