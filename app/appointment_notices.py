"""Appointment changes and WhatsApp notices commit together. No network on writes."""
import json
import os
import re
import unicodedata
from datetime import datetime
from zoneinfo import ZoneInfo
from sqlalchemy import text
from fastapi.encoders import jsonable_encoder

EVENTS = frozenset({'confirmed','rescheduled','cancelled','completed','no_show'})
LABELS = {'pending':'Aguardando envio','blocked':'Aguardando conexão','sending':'Verificando envio',
          'sent':'Enviado ao WhatsApp','delivered':'Entregue','read':'Lido',
          'uncertain':'Envio sem confirmação','superseded':'Substituído por uma atualização',
          'expired':'Prazo de envio encerrado','failed':'Envio não realizado'}
ERRORS = {'channel_not_configured':'O WhatsApp desta empresa ainda não está configurado para avisos.',
          'not_connected':'O WhatsApp está desconectado. O aviso permanece na fila.',
          'bridge_unavailable':'O serviço de WhatsApp está indisponível. A tentativa será repetida.',
          'account_changed':'A conta conectada mudou. Confira o vínculo antes de continuar.',
          'recipient_changed':'O contato do cliente foi alterado. Revise os dados do agendamento.',
          'send_uncertain':'Não houve confirmação de envio. Não será enviada outra cópia automaticamente.',
          'delivery_unconfirmed':'O WhatsApp não confirmou a entrega ao aparelho.',
          'not_sent':'O envio foi recusado antes de chegar ao transporte.',
          'storage_unavailable':'O registro seguro do WhatsApp está indisponível.',
          'recipient_invalid':'O cliente está sem um número de WhatsApp válido.',
          'expired':'O prazo de envio deste aviso terminou.'}


def enabled():
    return os.getenv('ORION_APPOINTMENT_NOTICES_ENABLED') == '1'


def clean(value, limit=100):
    value = ''.join(c for c in str(value or '') if not unicodedata.category(c).startswith('C'))
    return re.sub(r'\s+', ' ', value).strip().replace('*','').replace('_','').replace('`','')[:limit]


def phone(value):
    value=str(value or '').lstrip('+')
    return value if re.fullmatch(r'[1-9][0-9]{7,14}',value) else None


def moment(value, zone):
    instant = datetime.fromisoformat(value) if isinstance(value,str) else value
    if not isinstance(instant,datetime) or instant.tzinfo is None:
        raise ValueError('notice_timestamp_invalid')
    return instant.astimezone(ZoneInfo(zone)).strftime('%d/%m/%Y às %H:%M')


def notice_text(event, snapshot):
    if event not in EVENTS:
        raise ValueError('notice_event_invalid')
    business=clean(snapshot.get('business_name')) or 'Equipe de atendimento'
    date=moment(snapshot['start_at'],snapshot['timezone'])
    title={'confirmed':'Agendamento confirmado','rescheduled':'Seu agendamento foi remarcado',
           'cancelled':'Agendamento cancelado','completed':'Atendimento finalizado',
           'no_show':'Atualização do seu atendimento'}[event]
    lines=[f'*{business}*', '', f'*{title}*', '']
    if event=='rescheduled' and snapshot.get('previous_start_at'):
        lines.append('Horário anterior: '+moment(snapshot['previous_start_at'],snapshot['timezone']))
    lines.append(('Novo horário: ' if event=='rescheduled' else 'Horário: ')+date)
    professional=clean(snapshot.get('professional_name'))
    if professional: lines.append('Profissional: '+professional)
    lines.append('Protocolo: '+clean(snapshot.get('protocol'),40))
    if event=='cancelled':lines.extend(['','O horário foi liberado. Responda por aqui para combinar um novo atendimento.'])
    elif event=='completed':lines.extend(['','Obrigado pela visita. Fale conosco por aqui quando precisar.'])
    elif event=='no_show':lines.extend(['','O atendimento foi registrado como não realizado. Caso precise corrigir essa informação ou remarcar, responda por aqui.'])
    else:lines.extend(['','Confira os dados. Caso precise cancelar ou remarcar, responda por aqui.'])
    # Internal cancellation reasons, clinical details and account metadata are never sent.
    result='\n'.join(lines)
    if len(result)>1200:raise ValueError('notice_too_long')
    return result


async def enqueue_notice(db, tenant, appointment, event, details):
    if not enabled() or event not in EVENTS:
        return
    row=(await db.execute(text('''
        SELECT a.id,a.updated_at,a.start_at,a.end_at,a.status,a.protocol,a.professional_id,
               c.whatsapp_e164,c.deleted_at,t.name AS business_name,t.timezone,p.name AS professional_name
        FROM appointments a JOIN tenants t ON t.id=a.tenant_id
        JOIN customers c ON c.tenant_id=a.tenant_id AND c.id=a.customer_id
        JOIN professionals p ON p.tenant_id=a.tenant_id AND p.id=a.professional_id
        WHERE a.tenant_id=:tenant AND a.id=:id
    '''),{'tenant':tenant,'id':appointment})).mappings().one()
    await db.execute(text('''
        UPDATE appointment_notices SET status='superseded',updated_at=clock_timestamp()
        WHERE tenant_id=:tenant AND appointment_id=:id AND status IN ('pending','blocked')
          AND appointment_version<>:version
    '''),{'tenant':tenant,'id':appointment,'version':row['updated_at']})
    # Channel adapters already reply to the requesting customer. Do not duplicate that reply.
    if details.get('source') not in {'panel','public_site'}:
        return
    previous=details.get('previous') or {}
    snapshot=jsonable_encoder({k:row[k] for k in ('start_at','end_at','status','protocol','professional_id',
                                'business_name','timezone','professional_name')})
    snapshot.update(phone=phone(row['whatsapp_e164']),previous_start_at=previous.get('start_at'))
    initial='pending' if snapshot['phone'] and not row['deleted_at'] else 'failed'
    await db.execute(text('''
        INSERT INTO appointment_notices(tenant_id,appointment_id,appointment_version,event,snapshot,status,last_error,expires_at)
        VALUES(:tenant,:id,:version,:event,CAST(:snapshot AS jsonb),:status,:error,
               CASE WHEN :event IN ('confirmed','rescheduled') THEN LEAST(now()+interval '24 hours',:start) ELSE now()+interval '24 hours' END)
        ON CONFLICT(tenant_id,appointment_id,event,appointment_version) DO NOTHING
    '''),{'tenant':tenant,'id':appointment,'version':row['updated_at'],'event':event,
          'snapshot':json.dumps(snapshot,ensure_ascii=False),'status':initial,
          'error':'recipient_invalid' if initial=='failed' else None,'start':row['start_at']})


async def read_notices(db,tenant,appointment):
    if not enabled():
        return {'enabled':False,'items':[]}
    rows=(await db.execute(text('''
        SELECT id,event,status,attempts,last_error,created_at,updated_at,expires_at>now() AS fresh
        FROM appointment_notices WHERE tenant_id=:tenant AND appointment_id=:id
        ORDER BY created_at DESC,id DESC LIMIT 20
    '''),{'tenant':tenant,'id':appointment})).mappings().all()
    return {'enabled':True,'items':[dict(r,status_label=LABELS[r['status']],
        error_message=ERRORS.get(r['last_error']),can_retry=r['fresh'] and r['status']=='blocked') for r in rows]}
