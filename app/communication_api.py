"""Factual previews and team-only pending notices. These routes never send."""
from datetime import datetime, timezone
import os
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from pydantic import Field
from sqlalchemy import text

from app.catalog_api import Actor,authenticate,audit
from app.catalog_schemas import StrictModel
from app.management_api import management_session
from app.management_preferences import preferences,AutomationRule
from app.automation_templates import compose,EVENTS,catalog
from app.automation_policy import schedule_explanation
from app.automation_runtime import base_labels,appointment_context,settings_for
from app.communication_style import style_for,presentation_metadata

router=APIRouter(prefix='/api/v1/tenants/{tenant_id}/management',tags=['Comunicação revisada'])


@router.get('/communication/customers/{customer_id}/appointments')
async def customer_appointments(tenant_id:UUID,customer_id:UUID,actor:Actor=Depends(authenticate)):
    async with management_session(tenant_id,actor) as db:
        rows=(await db.execute(text('''SELECT a.id,a.protocol,a.start_at,a.status,p.name AS professional
          FROM appointments a JOIN customers c ON c.tenant_id=a.tenant_id AND c.id=a.customer_id
          JOIN professionals p ON p.tenant_id=a.tenant_id AND p.id=a.professional_id
          WHERE a.tenant_id=:t AND a.customer_id=:c AND c.active AND c.deleted_at IS NULL
          ORDER BY a.start_at DESC,a.id DESC LIMIT 40'''),{'t':tenant_id,'c':customer_id})).mappings().all()
        zone=(await db.execute(text('SELECT timezone FROM tenants WHERE id=:t'),{'t':tenant_id})).scalar_one()
        return jsonable_encoder({'items':[dict(x) for x in rows],'timezone':zone})


class Preview(StrictModel):
    event: str=Field(pattern='^('+'|'.join(EVENTS)+')$')
    customer_id: UUID
    appointment_id: UUID | None = None
    expected_preferences_version: int=Field(ge=0,strict=True)
    draft: AutomationRule | None = None


async def notification_context(db,tenant,settings,pref,customer,event,appointment_id=None):
    labels=base_labels(settings,customer,pref)
    slug=(await db.execute(text('SELECT public_slug FROM management_site_preferences WHERE tenant_id=:t AND public_enabled'),{'t':tenant})).scalar_one_or_none()
    origin=os.getenv('PANEL_PUBLIC_URL','').rstrip('/')
    if slug and origin:labels['link_agendar']=origin+'/s/'+slug
    facts={}
    if event in {'booking_confirmed','rescheduled','cancelled','reminder_24h','reminder_2h','reminder_1h','after_service','cancelled_7d'}:
        if appointment_id is None:raise HTTPException(422,'Escolha um agendamento real deste cliente')
        record=(await db.execute(text('''SELECT a.*,p.name AS professional,
          (SELECT string_agg(i.name_snapshot,', ' ORDER BY i.item_order) FROM appointment_items i
            WHERE i.tenant_id=a.tenant_id AND i.appointment_id=a.id) AS services,
          (SELECT max(h.occurred_at) FROM appointment_history h WHERE h.tenant_id=a.tenant_id AND h.appointment_id=a.id
            AND h.event=CASE WHEN :event='rescheduled' THEN 'rescheduled' ELSE a.status END) AS occurred_at
          FROM appointments a JOIN professionals p ON p.tenant_id=a.tenant_id AND p.id=a.professional_id
          WHERE a.tenant_id=:t AND a.customer_id=:customer AND a.id=:id'''),
          {'t':tenant,'customer':customer['id'],'id':appointment_id,'event':event})).mappings().one_or_none()
        if not record:raise HTTPException(404,'Agendamento não encontrado para este cliente neste negócio')
        facts={**dict(record),'appointment_id':record['id']}
        labels.update(appointment_context(settings,customer,pref,record))
    else:
        completed=(await db.execute(text("SELECT max(h.occurred_at) FROM appointment_history h JOIN appointments a ON a.tenant_id=h.tenant_id AND a.id=h.appointment_id WHERE a.tenant_id=:t AND a.customer_id=:c AND a.status='completed' AND h.event='completed'"),{'t':tenant,'c':customer['id']})).scalar_one_or_none()
        facts={'occurred_at':completed,'birth_date':customer.get('birth_date'),'birth_month':customer.get('birth_month'),'birth_day':customer.get('birth_day')}
        if event=='loyalty_reward':
            coupon=(await db.execute(text("SELECT code,issued_at FROM loyalty_coupons WHERE tenant_id=:t AND customer_id=:c AND status='available' AND expires_at>now() ORDER BY issued_at DESC LIMIT 1"),{'t':tenant,'c':customer['id']})).mappings().one_or_none()
            if coupon:facts['occurred_at']=coupon['issued_at'];labels['cupom']=coupon['code']
    facts['has_future_booking']=bool((await db.execute(text("SELECT 1 FROM appointments WHERE tenant_id=:t AND customer_id=:c AND status='confirmed' AND start_at>now() LIMIT 1"),{'t':tenant,'c':customer['id']})).first())
    return facts,labels


@router.post('/marketing/automations/preview')
async def preview(tenant_id:UUID,payload:Preview,actor:Actor=Depends(authenticate)):
    async with management_session(tenant_id,actor) as db:
        pref=await preferences(db,tenant_id)
        if pref['version']!=payload.expected_preferences_version:raise HTTPException(409,'As rotinas mudaram. Atualize a revisão antes da prévia')
        customer=(await db.execute(text('SELECT * FROM customers WHERE tenant_id=:t AND id=:id AND active AND deleted_at IS NULL'),{'t':tenant_id,'id':payload.customer_id})).mappings().one_or_none()
        if not customer:raise HTTPException(404,'Cliente não encontrado neste negócio')
        settings=await settings_for(db,tenant_id)
        if not settings:
            settings=dict((await db.execute(text('SELECT name AS business_name,timezone FROM tenants WHERE id=:t'),{'t':tenant_id})).mappings().one())
        settings['style']=await style_for(db,tenant_id)
        prefs={'preferences':{'business':pref['business'],'automations':pref['automations']}}
        facts,labels=await notification_context(db,tenant_id,settings,prefs,dict(customer),payload.event,payload.appointment_id)
        expected_status={'booking_confirmed':'confirmed','rescheduled':'confirmed','cancelled':'cancelled',
            'reminder_24h':'confirmed','reminder_2h':'confirmed','reminder_1h':'confirmed','after_service':'completed','cancelled_7d':'cancelled'}.get(payload.event)
        if expected_status and facts.get('status')!=expected_status:
            raise HTTPException(422,'O estado real deste agendamento não comprova o evento escolhido')
        if payload.event in {'rescheduled','cancelled','after_service','cancelled_7d','loyalty_reward','inactive_60d'} and not facts.get('occurred_at'):
            raise HTTPException(422,'O histórico real não comprova o evento escolhido')
        if payload.event.startswith('birthday_') and not (customer.get('birth_date') or customer.get('birth_day') and customer.get('birth_month')):
            raise HTTPException(422,'O cadastro não possui dia e mês de aniversário válidos')
        saved=next((r for r in pref['automations']['rules'] if r['event']==payload.event),None)
        rule=payload.draft.model_dump() if payload.draft else saved or next(r for r in catalog() if r['event']==payload.event)
        if rule['event']!=payload.event:raise HTTPException(422,'O evento do modelo não corresponde à prévia')
        try:prepared=compose(payload.event,rule,labels,business=pref['business'],style=settings['style'],public_origin=os.getenv('PANEL_PUBLIC_URL'))
        except (ValueError,KeyError):raise HTTPException(422,'Falta um dado obrigatório ou um link aprovado para compor a mensagem inteira') from None
        scheduled,reason=schedule_explanation(payload.event,facts,datetime.now(timezone.utc),settings['timezone'],
            policy=pref['automations']['delivery_policy'],recovery_days=pref['automations']['recovery_days'],approved_at=settings.get('approved_at'))
        return jsonable_encoder({'body':prepared.body,'presentation':presentation_metadata(prepared,settings),'preferences_version':pref['version'],
            'customer_version':customer['version'],'appointment_version':facts.get('updated_at'),'event':payload.event,
            'appointment':{key:facts.get(key) for key in ('id','protocol','start_at','status','professional','updated_at')} if facts.get('appointment_id') else None,
            'timezone':settings['timezone'],
            'unsaved_draft':rule!=saved,'scheduled':scheduled,'schedule_reason':reason,
            'execution_authorized':False,'sent':False,'mode':'preview_only',
            'notice':'Prévia sem envio. A execução exige estado, consentimento, canal, revisão e autorização atuais.'})


@router.get('/communication/pending')
async def pending(tenant_id:UUID,phone:str|None=Query(default=None,pattern=r'^\+55[1-9][0-9]{9,10}$'),actor:Actor=Depends(authenticate)):
    async with management_session(tenant_id,actor) as db:
        rows=(await db.execute(text('''SELECT n.id,n.contact_e164,n.event,n.reason,n.appointment_id,n.appointment_version,n.version,n.created_at,
          CASE WHEN n.appointment_id IS NOT NULL AND a.updated_at IS DISTINCT FROM n.appointment_version
            THEN 'obsolete' ELSE n.status END AS status
          FROM communication_pending n LEFT JOIN appointments a ON a.tenant_id=n.tenant_id AND a.id=n.appointment_id
          WHERE n.tenant_id=:t AND n.status='pending' AND (CAST(:phone AS text) IS NULL OR n.contact_e164=:phone)
          ORDER BY n.created_at DESC,n.id DESC LIMIT 100'''),{'t':tenant_id,'phone':phone})).mappings().all()
        return jsonable_encoder({'items':[dict(r) for r in rows],'audience':'team','sends_messages':False})


class Acknowledge(StrictModel):
    expected_version:int=Field(ge=1,strict=True)


@router.post('/communication/pending/{notice_id}/acknowledge')
async def acknowledge(tenant_id:UUID,notice_id:UUID,payload:Acknowledge,actor:Actor=Depends(authenticate)):
    if actor.account_id is None:raise HTTPException(403,'Entre com sua conta pessoal para registrar a leitura')
    async with management_session(tenant_id,actor,write=True) as db:
        row=(await db.execute(text("UPDATE communication_pending SET status='acknowledged',acknowledged_by=:account,version=version+1,updated_at=clock_timestamp() WHERE tenant_id=:t AND id=:id AND version=:v AND status='pending' RETURNING id,version,status"),
            {'t':tenant_id,'id':notice_id,'v':payload.expected_version,'account':actor.account_id})).mappings().one_or_none()
        if not row:raise HTTPException(409,'A pendência mudou. Atualize antes de marcar como vista')
        await audit(db,tenant_id,'communication_pending_acknowledged','communication_pending',notice_id)
        return {**dict(row),'sent':False}
