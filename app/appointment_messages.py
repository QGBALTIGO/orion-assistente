"""Central factual WhatsApp templates. No model-generated fields or arbitrary links."""
import re
from zoneinfo import ZoneInfo
from app.public_origins import canonical_public_origin
from app.qr_presentation import fits_message


def label(value, limit=160):
    value=' '.join(str(value or '').split())[:limit]
    # Neutralize formatting and link injection in user-editable labels.
    value=re.sub(r'https?://\S+|www\.\S+', '[link removido]', value, flags=re.I)
    return value.translate(str.maketrans({'*':'','_':'','~':'','`':'','<':'','>':''}))


def safe_link(value):
    origin=canonical_public_origin()
    if value and (value.startswith(origin+'/a#') or value.startswith(origin+'/s/')
                  or value.startswith(origin+'/panel#')) and not re.search(r'[\s<>]',value):
        return value
    return None


def compose(event, row, *, company, signature='Orion Agenda', customer_link=None,
            booking_link=None, previous=None, include_return=False, address=None):
    previous=previous or {}
    first=label(row.get('customer_name')).split(' ')[0] or 'Olá'
    pro=label(row.get('professional_name'))
    service=' + '.join(label(i['name_snapshot'],100) for i in row['items'])
    if len(service)>320: service=service[:317]+'…'
    when=row['start_at'].astimezone(ZoneInfo(row['timezone']))
    details=f'✂️ Serviço: {service}\n📆 Data: {when:%d/%m/%Y}\n🕒 Horário: {when:%H:%M}\n💈 Profissional: {pro}\n🧾 Protocolo: {label(row["protocol"])}'
    if event=='confirmation':
        parts=[f'Olá, {first}! Tudo bem? 👋','Seu agendamento está confirmado! Confira os detalhes:',details]
        if safe_link(customer_link):parts+=['🔗 GERENCIAR AGENDAMENTO\n'+customer_link,'Pelo link acima, você pode cancelar ou remarcar seu atendimento até 1 hora antes do horário marcado.']
        parts+=['Até breve! 😊']
    elif event.startswith('professional_'):
        title={'professional_new':'📅 NOVO AGENDAMENTO!','professional_cancelled':'❌ AGENDAMENTO CANCELADO',
               'professional_rescheduled':'🔄 AGENDAMENTO REMARCADO','professional_reassigned':'🔄 RESPONSÁVEL ALTERADO'}[event]
        # A phone number is available behind the authenticated panel link.
        parts=[title,f'Olá, {pro}!','👤 Cliente: '+label(row.get('customer_name')),details]
        if event=='professional_new':parts += ['⏱️ Duração: '+str(sum(i['duration_snapshot_min'] for i in row['items']))+' minutos']
    elif event=='customer_cancelled':
        parts=[f'Olá, {first}! 👋','Seu agendamento foi cancelado pela equipe.',details,'Pedimos desculpas pelo inconveniente.']
    elif event=='customer_rescheduled':
        parts=['🔄 SEU AGENDAMENTO FOI REMARCADO',f'Olá, {first}! 👋',details]
    elif event=='reminder':
        parts=['⏰ LEMBRETE DE AGENDAMENTO',f'Olá, {first}! 👋',f'Seu atendimento na {label(company)} será daqui a 1 hora!',details]
        if address:parts+=['📍 Local: '+label(address,200)]
    elif event=='review':
        parts=['✨ COMO FOI SEU ATENDIMENTO?',f'Olá, {first}! 😊',f'Esperamos que você tenha gostado do atendimento com {pro}!',
            'Sua opinião é muito importante para a nossa equipe.']
        if safe_link(customer_link):parts+=['⭐ AVALIAR ATENDIMENTO\n'+customer_link]
        if include_return and safe_link(booking_link):parts+=['✂️ QUE TAL GARANTIR SEU PRÓXIMO HORÁRIO?','🔗 AGENDAR NOVAMENTE\n'+booking_link]
    else:raise ValueError('unsupported_appointment_template')
    if event in {'professional_rescheduled','customer_rescheduled'} and previous.get('start_at'):
        from datetime import datetime
        old=previous['start_at'];old=datetime.fromisoformat(old) if isinstance(old,str) else old
        old=old.astimezone(ZoneInfo(row['timezone']))
        parts+= [f'Horário anterior: {old:%d/%m/%Y às %H:%M}']
    if event.startswith('professional_'):
        parts+=['🔗 GERENCIAR ATENDIMENTO\n'+canonical_public_origin()+'/panel#agenda']
    elif event=='customer_rescheduled' and safe_link(customer_link):parts+=['🔗 VER AGENDAMENTO ATUALIZADO\n'+customer_link]
    elif event=='customer_cancelled' and safe_link(booking_link):parts+=['🔗 AGENDAR NOVAMENTE\n'+booking_link]
    parts+=['🤖 '+label(signature,80)]
    body='\n\n'.join(parts)
    if not fits_message(body):raise ValueError('appointment_message_too_long')
    return body
