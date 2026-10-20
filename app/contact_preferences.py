"""Explicit customer preferences. Never changes bookings or grants a staff role."""
from datetime import date
import json
import os
import re

from sqlalchemy import text

from app.qr_behaviors import norm
from app.database import get_session_factory,tenant_transaction

PURPOSE_VERSION=1
BIRTHDAY_INVITATION='Seu agendamento já está garantido. Se quiser receber duas mensagens de aniversário, sete e dois dias antes, informe apenas seu dia e mês (DD/MM). É opcional; pode recusar e cancelar essa permissão a qualquer momento.'
MONTHS={'janeiro':1,'fevereiro':2,'marco':3,'abril':4,'maio':5,'junho':6,'julho':7,'agosto':8,
    'setembro':9,'outubro':10,'novembro':11,'dezembro':12}
DAY_WORDS={'um':1,'primeiro':1,'dois':2,'tres':3,'quatro':4,'cinco':5,'seis':6,'sete':7,'oito':8,'nove':9,
    'dez':10,'onze':11,'doze':12,'treze':13,'quatorze':14,'catorze':14,'quinze':15,'dezesseis':16,
    'dezessete':17,'dezoito':18,'dezenove':19,'vinte':20,'vinte e um':21,'vinte e dois':22,'vinte e tres':23,
    'vinte e quatro':24,'vinte e cinco':25,'vinte e seis':26,'vinte e sete':27,'vinte e oito':28,'vinte e nove':29,
    'trinta':30,'trinta e um':31}


def preference_intent(body):
    value=norm(body)
    choice=None
    for clause in re.split(r'[.;!?\n]|\b(?:mas|na verdade|quer dizer)\b',value):
        clause=clause.strip(' ,')
        if re.search(r'\bnao\s+(?:quero\s+)?(?:parar|interromper|sair|cancelar|cancele|cancela|tirar|retirar)\b',clause):choice=None;continue
        if re.search(r'\bquero receber\b',clause):choice=None;continue  # Retraction never grants new consent.
        if re.search(r'\b(?:nao quero|nao (?:me )?(?:mande|manda|envie|envia)|parar|pare|para de (?:mandar|enviar)|cancela|cancelar|retira|tirar|tira)\b',clause):
            if 'aniversario' in clause:choice='birthday'
            elif re.search(r'\b(?:promocao|promocoes|promocionais?|anuncio\w*|publicidade|marketing|lista)\b',clause):choice='marketing'
            elif re.search(r'\b(?:tudo|nada|mensagens?|contato)\b',clause):choice='all'
        if clause in {'parar','sair'}:choice='all'
        if re.search(r'\bso (?:os? )?lembretes?\b',clause):choice='marketing'
    return choice


async def record_optout(db,tenant,phone,scope):
    if scope not in {'birthday','marketing','all'}:raise ValueError('Invalid opt-out scope')
    phone='+'+phone.removeprefix('+')
    await db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:scope,0))'),
        {'scope':str(tenant)+':automation-consent:'+phone})
    await db.execute(text('''INSERT INTO contact_consents(tenant_id,contact_e164,marketing_opt_in,operational_opt_in,
      birthday_opt_in,birthday_declined_at,source) VALUES(:t,:phone,false,false,false,clock_timestamp(),'customer_request')
      ON CONFLICT(tenant_id,contact_e164) DO UPDATE SET
        marketing_opt_in=CASE WHEN :scope IN ('all','marketing') THEN false ELSE contact_consents.marketing_opt_in END,
        operational_opt_in=CASE WHEN :scope='all' THEN false ELSE contact_consents.operational_opt_in END,
        birthday_opt_in=false,birthday_declined_at=clock_timestamp(),
        source='customer_request',updated_at=GREATEST(clock_timestamp(),contact_consents.updated_at+interval '1 microsecond')'''),
      {'t':tenant,'phone':phone,'scope':scope})
    await db.execute(text('''UPDATE automation_jobs SET status='cancelled',last_error='customer_opt_out',updated_at=clock_timestamp()
      WHERE tenant_id=:t AND contact_e164=:phone AND status='pending'
        AND (:scope='all' OR :scope='marketing' AND event IN ('inactive_60d','cancelled_7d','birthday_7d','birthday_2d','loyalty_reward','after_service','campaign')
          OR :scope='birthday' AND event IN ('birthday_7d','birthday_2d'))'''),{'t':tenant,'phone':phone,'scope':scope})


async def handle_optout(tenant,phone,body,*,preview=False):
    scope=preference_intent(body)
    if scope is None:return None
    if preview:return 'Simulação: esse pedido interromperia as comunicações escolhidas, sem cancelar agendamentos.'
    from app.qr_intake_revision import check_before_write
    await check_before_write()
    async with get_session_factory()() as db:
        async with tenant_transaction(db,tenant):await record_optout(db,tenant,phone,scope)
    label={'all':'as comunicações automáticas deste contato','marketing':'as mensagens promocionais','birthday':'as mensagens de aniversário'}[scope]
    return 'Interrompi '+label+'. Seus agendamentos permanecem.'


async def receive_control(tenant,phone,choice,redis,*,identity=None):
    """Authenticated adapter only; no booking writes or staff assignment."""
    if choice not in {'birthday','marketing','all','human'}:raise ValueError('Invalid safety control')
    if choice!='human':
        async with get_session_factory()() as db:
            async with tenant_transaction(db,tenant):
                if identity is not None:
                    from app.automation_runtime import _notice_guard,_notice_identity
                    await _notice_guard(db,_notice_identity(tenant,phone,identity))
                await record_optout(db,tenant,phone,choice)
                if identity is not None:await _notice_guard(db,_notice_identity(tenant,phone,identity))
        return
    from app.automation_runtime import interaction
    await interaction(tenant,phone,human=True,channel_identity=identity)
    namespace=identity.conversation_namespace(phone) if identity else phone
    key=f'orion:qr:ai:{tenant}:{namespace}'
    try:state=json.loads(await redis.get(key) or '{}')
    except (ValueError,TypeError):state={}
    if not isinstance(state,dict):state={}
    for field in ('summary_message_id','expected_snapshot','last_message_id'):state.pop(field,None)
    state.update(step='human',last_answer='',confirmation_requires_review=True)
    await redis.set(key,json.dumps(state),ex=1800)


def parse_birthday(body):
    value=norm(body)
    if re.search(r'\b(?:meu filho|minha filha|esposa|marido|mae|pai|cachorro|gato|paciente|outra pessoa)\b',value):return None,'third_party',''
    if re.search(r'\b(?:agendar|marcar|remarcar|reagendar|reserva|horario)\b',value) and 'aniversario' not in value:return None,'unrelated',''
    if re.search(r'\b\d{1,2}/\d{1,2}/\d{4}\b',value):return None,'year_not_needed',''
    match=re.search(r'(?<!\d)(\d{1,2})\s*/\s*(\d{1,2})(?!\s*/?\d)',value)
    if not match:match=re.fullmatch(r'(\d{2})(\d{2})',value.strip())
    if match:day,month=int(match[1]),int(match[2])
    else:
        match=re.search(r'\b(?:dia\s+)?(\d{1,2}|'+ '|'.join(sorted(DAY_WORDS,key=len,reverse=True))+r')\s+(?:de\s+)?('+ '|'.join(MONTHS)+r')\b',value)
        if not match:return None,'unrelated',''
        day=int(match[1]) if match[1].isdigit() else DAY_WORDS[match[1]];month=MONTHS[match[2]]
    try:date(2000,month,day)
    except ValueError:return None,'invalid',''
    remainder=(body[:match.start()]+' '+body[match.end():]).strip(' ,;.')
    remainder=re.sub(r'^(?:e\s+)', '',remainder,flags=re.I)
    return (month,day),None,remainder


async def offer_birthday(tenant,phone,redis,key,protocol,*,preview=False):
    if preview or os.getenv('BIRTHDAY_COLLECTION_ENABLED')!='1':return ''
    async with get_session_factory()() as db:
        async with tenant_transaction(db,tenant):
            customer=(await db.execute(text('''SELECT c.id,c.version,c.birth_month,c.birth_day,c.birth_date,k.birthday_opt_in,k.birthday_declined_at,k.birthday_updated_at
              FROM customers c JOIN appointments a ON a.tenant_id=c.tenant_id AND a.customer_id=c.id
              LEFT JOIN contact_consents k ON k.tenant_id=c.tenant_id AND k.contact_e164=c.whatsapp_e164
              WHERE c.tenant_id=:t AND c.whatsapp_e164=:phone AND c.active AND c.deleted_at IS NULL
                AND a.protocol=:protocol AND a.status='confirmed' LIMIT 1'''),
              {'t':tenant,'phone':'+'+phone.removeprefix('+'),'protocol':protocol})).mappings().one_or_none()
    if not customer or customer['birthday_opt_in'] or customer['birthday_declined_at']:return ''
    await redis.set(key+':birthday-invite',json.dumps({'customer_id':str(customer['id']),'version':customer['version'],
        'purpose_version':PURPOSE_VERSION,'consent_version':customer['birthday_updated_at'].isoformat() if customer['birthday_updated_at'] else None}),ex=86400)
    return BIRTHDAY_INVITATION


async def birthday_turn(tenant,phone,body,redis,key,*,preview=False):
    if os.getenv('BIRTHDAY_COLLECTION_ENABLED')!='1':return None
    raw=await redis.get(key+':birthday-invite')
    if not raw:return None
    try:
        from uuid import UUID
        invitation=json.loads(raw)
        if invitation.get('purpose_version')!=PURPOSE_VERSION or type(invitation.get('version')) is not int:return None
        UUID(invitation['customer_id'])
    except (ValueError,TypeError,KeyError,AttributeError):return None
    value=norm(body)
    declined = value.strip(' .!?') in {'nao','n','nao obrigado','nao obrigada','prefiro nao'} or bool(re.fullmatch(r'(?:nao (?:quero|desejo|vou)|prefiro nao) (?:informar|fornecer|compartilhar)(?: (?:meu aniversario|a data|minha data|o dia e mes|esse dado))?(?: agora| por aqui)?',value.strip(' .!?')))
    if declined:
        if not preview:
            from app.qr_intake_revision import check_before_write
            await check_before_write()
            async with get_session_factory()() as db:
                async with tenant_transaction(db,tenant):await record_optout(db,tenant,phone,'birthday')
        await redis.delete(key+':birthday-invite')
        return {'answer':'Tudo bem, não vou insistir no aniversário. Esse pedido não altera seus agendamentos.','remainder':''}
    birthday,error,remainder=parse_birthday(body)
    if error=='unrelated':
        await redis.delete(key+':birthday-invite')  # A topic change cannot turn a later booking date into birthday consent.
        return None
    if error=='third_party':return {'answer':'Esse convite é opcional e se refere ao seu aniversário. Não vou usar a data de outra pessoa. Seu agendamento permanece.','remainder':''}
    if error=='invalid':return {'answer':'Essa data não existe no calendário. Se quiser, informe apenas dia e mês, como 15/08. Seu agendamento permanece.','remainder':''}
    if error=='year_not_needed':return {'answer':'Para essa permissão opcional, preciso apenas do dia e mês, sem o ano. Pode informar DD/MM? Seu agendamento permanece.','remainder':''}
    if preview:return {'answer':f'Simulação: anotaria apenas {birthday[1]:02}/{birthday[0]:02} para as duas mensagens de aniversário.','remainder':remainder}
    from app.qr_intake_revision import check_before_write
    await check_before_write()
    async with get_session_factory()() as db:
        async with tenant_transaction(db,tenant):
            await db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:scope,0))'),
                {'scope':str(tenant)+':automation-consent:+'+phone.removeprefix('+')})
            current=(await db.execute(text('SELECT birthday_updated_at FROM contact_consents WHERE tenant_id=:t AND contact_e164=:phone FOR UPDATE'),
                {'t':tenant,'phone':'+'+phone.removeprefix('+')})).scalar_one_or_none()
            if 'consent_version' not in invitation or (current.isoformat() if current else None)!=invitation['consent_version']:
                await redis.delete(key+':birthday-invite')
                return {'answer':'A permissão mudou desde o convite. Não alterei sua data nem reativei mensagens. A equipe pode conferir um novo pedido explícito.','remainder':''}
            updated=(await db.execute(text('''UPDATE customers SET birth_month=:month,birth_day=:day,version=version+1,updated_at=clock_timestamp()
              WHERE tenant_id=:t AND id=:id AND whatsapp_e164=:phone AND version=:v AND active AND deleted_at IS NULL RETURNING id'''),
              {'t':tenant,'id':UUID(invitation['customer_id']),'phone':'+'+phone.removeprefix('+'),'v':invitation['version'],'month':birthday[0],'day':birthday[1]})).scalar_one_or_none()
            if not updated:return {'answer':'O cadastro mudou desde o convite. Não alterei a data nem a permissão; a equipe pode conferir.','remainder':''}
            await db.execute(text('''INSERT INTO contact_consents(tenant_id,contact_e164,marketing_opt_in,operational_opt_in,birthday_opt_in,birthday_purpose_version,source)
              VALUES(:t,:phone,false,false,true,:purpose,'birthday_invitation') ON CONFLICT(tenant_id,contact_e164) DO UPDATE
              SET birthday_opt_in=true,birthday_declined_at=NULL,birthday_purpose_version=:purpose,source='birthday_invitation',
                updated_at=GREATEST(clock_timestamp(),contact_consents.updated_at+interval '1 microsecond')'''),
              {'t':tenant,'phone':'+'+phone.removeprefix('+'),'purpose':PURPOSE_VERSION})
            await db.execute(text("UPDATE automation_jobs SET status='cancelled',last_error='birthday_changed' WHERE tenant_id=:t AND contact_e164=:phone AND status='pending' AND event IN ('birthday_7d','birthday_2d')"),{'t':tenant,'phone':'+'+phone.removeprefix('+')})
    await redis.delete(key+':birthday-invite')
    return {'answer':f'Anotei apenas {birthday[1]:02}/{birthday[0]:02} para as duas mensagens de aniversário combinadas. Você pode revogar a permissão a qualquer momento. Esse registro não altera seus agendamentos.','remainder':remainder}
