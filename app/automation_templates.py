"""Versioned defaults and one factual composition shared by preview and jobs.

Existing/custom templates retain their source text. Only explicitly library-based
rules select variants. Optional missing facts omit whole default blocks; a missing
required fact never fabricates a success or a URL.
"""
from dataclasses import dataclass
import hashlib
import json

from app.qr_presentation import escape_label, message_units
from app.communication_links import delivery_link,validate_body_links

REVISION=1
EVENTS=('booking_confirmed','reminder_24h','reminder_2h','reminder_1h','rescheduled','cancelled',
    'inactive_60d','cancelled_7d','birthday_7d','birthday_2d','loyalty_reward','after_service')
HEADINGS={'booking_confirmed':'Agendamento confirmado!','reminder_24h':'Lembrete do seu atendimento',
    'reminder_2h':'Seu atendimento está chegando','reminder_1h':'Lembrete do agendamento',
    'rescheduled':'Seu agendamento foi remarcado','cancelled':'Agendamento cancelado',
    'inactive_60d':'Quando precisar, estamos por aqui','cancelled_7d':'Podemos consultar uma nova data',
    'birthday_7d':'Seu aniversário está chegando','birthday_2d':'Seu aniversário está perto',
    'loyalty_reward':'Seu benefício está disponível','after_service':'Obrigado pela visita!'}
ICONS={'booking_confirmed':'✅','rescheduled':'🔄','cancelled':'📋','loyalty_reward':'🎁','after_service':'💬',
    'birthday_7d':'🎉','birthday_2d':'🎉'}
AGENDA=frozenset({'booking_confirmed','rescheduled','cancelled','reminder_24h','reminder_2h','reminder_1h'})


def library_template(event, *, name=True, link=True, emoji=False):
    if event not in EVENTS:raise ValueError('unknown_event')
    greeting='Olá, *{cliente}*!' if name else 'Olá!'
    title=('*'+HEADINGS[event]+'*')
    if emoji:title=ICONS.get(event,'🔔' if event.startswith('reminder_') else '💬')+' '+title
    parts=['*{empresa}*',greeting,title]
    if event in AGENDA:
        icon=lambda value:value+' ' if emoji else ''
        parts += [icon('📋')+'Serviço: *{servico}*\n'+icon('👤')+'Profissional: *{profissional}*',
            icon('📅')+'Data: *{data}*\n'+icon('🕒')+'Horário: *{horario}*']
        if event in {'booking_confirmed','rescheduled'}:parts += [icon('💰')+'Total: *{valor}*']
        if event in {'booking_confirmed','rescheduled','cancelled'}:parts += [icon('🔖')+'Protocolo: *{protocolo}*']
    elif event=='loyalty_reward':parts += ['Cupom: *{cupom}*','_Confira a validade e as condições com a equipe antes de utilizar._']
    elif event=='after_service':parts += ['Esperamos que o atendimento tenha sido bom para você.']
    elif event.startswith('birthday_'):parts += ['A equipe deseja um ótimo novo ciclo. Quando precisar, estamos por aqui.']
    else:parts += ['Se ainda precisar, você pode falar com a equipe para consultar uma nova data.']
    if event=='rescheduled':parts += ['_O horário anterior foi substituído._']
    if link:
        parts += ['Para avaliar sua experiência:\n{link_review}' if event=='after_service' else 'Para acessar a agenda:\n{link_agendar}']
    if event not in AGENDA and event!='after_service':parts += ['_Se preferir não receber essas mensagens, escreva PARAR._']
    return '\n\n'.join(parts)


def catalog():
    return [{'event':e,'template_source':'library','template_revision':REVISION,
        'template':library_template(e),'variants':['com_nome__com_link','com_nome__sem_link','sem_nome__com_link','sem_nome__sem_link']}
        for e in EVENTS]


@dataclass(frozen=True)
class Composition:
    body: str
    source: str
    revision: int
    variant: str
    digest: str

    def metadata(self):
        return {'schema_version':1,'source':self.source,'template_revision':self.revision,
            'variant':self.variant,'body_sha256':self.digest,'utf16_units':message_units(self.body)}


def compose(event, rule, facts, *, business=None, style=None, public_origin=None):
    from app.automation_policy import render_template
    values=dict(facts);business=business or {};style=style or {}
    for field,purpose in [('link_agendar','booking'),('link_review','review')]:
        if values.get(field):
            try:values[field]=delivery_link(values[field],purpose=purpose,
                approved_hosts=business.get('approved_link_hosts',()),public_origin=public_origin)
            except ValueError:values.pop(field,None)
    # Unknown/generic labels are not a person's chosen name.
    from app.customer_names import useful_name
    values['cliente']=useful_name(values.get('cliente'))
    source=rule.get('template_source','custom')
    variant=('com_nome' if values.get('cliente') else 'sem_nome')+'__'+('com_link' if values.get('link_review' if event=='after_service' else 'link_agendar') else 'sem_link')
    if source=='library':
        if rule.get('template_revision',REVISION)!=REVISION:raise ValueError('template_version_unavailable')
        template=library_template(event,name=bool(values.get('cliente')),link=variant.endswith('com_link'),emoji=style.get('emoji')=='light')
    elif source=='custom':template=rule['template'];variant='custom'
    else:raise ValueError('invalid_template_source')
    body=render_template(template,values)
    validate_body_links(body,approved_hosts=business.get('approved_link_hosts',()),public_origin=public_origin)
    return Composition(body,source,REVISION if source=='library' else rule.get('template_revision',1),variant,
        hashlib.sha256(body.encode()).hexdigest())


def verified_presentation(job):
    """Check the complete persisted payload; never cut or rewrite a queued body."""
    body=job.get('body')
    if not isinstance(body,str) or not body.strip() or message_units(body)>1200:return False
    meta=job.get('presentation') or {}
    if not meta:return True  # Legacy custom jobs retain their reviewed payload.
    return bool(meta.get('schema_version')==1 and meta.get('source') in {'library','custom'}
        and type(meta.get('template_revision')) is int and meta['template_revision']>=1
        and meta.get('utf16_units')==message_units(body)
        and meta.get('body_sha256')==hashlib.sha256(body.encode()).hexdigest())
