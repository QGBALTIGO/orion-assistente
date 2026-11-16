"""Explicit professional indifference, coordinated queries and reschedule consent."""
import json
from datetime import datetime

import pytest

from app import qr_ai, qr_dialogue, qr_profile
from fastapi import HTTPException
from test_qr_multi_flow import flow


def availability(h,monkeypatch):
    async def slots(db,tenant,professional,services,day,**replacement):
        assert tenant==h.tenant and professional in {h.bruno,h.breno} and services==[h.cut] and day==h.tomorrow
        h.calls['reads'].append((professional,list(services),day))
        hours=[11] if professional==h.bruno else [10,14,15]
        return {'slots':[datetime.combine(day,datetime.min.time(),h.zone).replace(hour=hour).isoformat()
            for hour in hours],'price_cents':3500,'duration_min':40}
    monkeypatch.setattr(qr_ai,'list_slots',slots)


async def test_inline_indifference_prepares_only_currently_free_professional_then_needs_confirmation(flow,monkeypatch):
    h=flow;availability(h,monkeypatch)
    h.redis.data[h.key]=json.dumps({'step':'menu','customer_name':'Alex'})
    h.redis.data[h.key+':catalog-context']=qr_profile.catalog_revision(h.business,h.catalog,h.professionals)
    result=await h.turn(f'Quero agendar corte em {h.tomorrow} às 10:00, tanto faz o profissional',preview=False)
    assert result['step']=='confirm' and 'Breno' in result['answer'] and '10h' in result['answer']
    assert len(h.calls['writes'])==1 and h.calls['writes'][0][0]=='hold' and h.calls['writes'][0][3]==h.breno
    assert h.state()['professional']==str(h.breno) and not h.calls['names']
    assert not any(x[0]=='confirm' for x in h.calls['writes'])


async def test_inline_indifference_cannot_claim_success_from_partial_agenda_read(flow,monkeypatch):
    h=flow
    async def slots(db,tenant,professional,services,day,**replacement):
        assert tenant==h.tenant and services==[h.cut] and day==h.tomorrow
        if professional==h.breno:raise HTTPException(503,'Synthetic unavailable second agenda')
        assert professional==h.bruno
        return {'slots':[datetime.combine(day,datetime.min.time(),h.zone).replace(hour=10).isoformat()],
            'price_cents':3500,'duration_min':40}
    monkeypatch.setattr(qr_ai,'list_slots',slots)
    result=await h.turn(f'Quero agendar corte em {h.tomorrow} às 10:00, tanto faz o profissional',preview=False)
    assert 'Não consegui verificar' in result['answer'] and 'Encontrei disponibilidade' not in result['answer']
    assert not h.calls['writes'] and not h.calls['names']


@pytest.mark.parametrize('invalid', [None, 'expired', 'malformed_id', 'missing_snapshot'])
async def test_inline_indifference_without_day_retains_only_valid_replacement_handle(flow, invalid):
    h = flow
    first = await h.turn(f'Quero agendar corte em {h.tomorrow} às 14:00 com Bruno', preview=False)
    assert first['step'] == 'confirm'
    before = h.state()
    if invalid == 'expired':
        before['confirmation_expires_at'] = datetime.now().timestamp()-1
    elif invalid == 'malformed_id':
        before['hold'] = 'not-a-hold-id'
    elif invalid == 'missing_snapshot':
        before.pop('hold_snapshot')
    previous = qr_ai._replacement_hold(before)
    h.redis.data[h.key] = json.dumps(before)
    response = await h.turn('Quero agendar barba, tanto faz o profissional', preview=False)
    after = h.state()
    assert response['step'] == 'date' and after['services'] == [str(h.beard)]
    assert qr_ai._replacement_hold(after) == previous
    assert (previous is not None) is (invalid is None)
    for name in ('hold', 'hold_snapshot', 'confirmation_summary', 'confirmation_message_id',
                 'confirmation_expires_at', 'confirmation_presentation', 'snapshot', 'consent'):
        assert name not in after
    assert [row[0] for row in h.calls['writes']] == ['hold'] and not h.calls['names']


async def test_inline_indifference_does_not_override_a_contemporaneously_named_professional(flow,monkeypatch):
    h=flow;availability(h,monkeypatch)
    result=await h.turn(f'Quero agendar corte em {h.tomorrow} às 10:00 com Bruno, tanto faz o profissional',preview=False)
    assert result['step']!='confirm' and not h.calls['writes']
    assert all(row[0]==h.bruno for row in h.calls['reads'])


async def test_comparing_named_agendas_uses_current_hours_and_only_requested_free_clock(flow,monkeypatch):
    h=flow;availability(h,monkeypatch)
    result=await h.turn(f'Quais horários Bruno e Breno têm para corte em {h.tomorrow} às 10:00?',preview=False)
    assert 'Breno' in result['answer'] and '10h' in result['answer'] and 'Bruno' not in result['answer']
    assert len(h.calls['reads'])==2 and not h.calls['writes'] and not h.calls['names']
    assert h.state()['last_availability']['professionals']==[{'id':str(h.breno),'name':'Breno'}]


@pytest.mark.parametrize('question', ['Quais horários têm', 'Que horários há', 'Qual horário tem'])
async def test_current_hours_question_is_read_only_availability_without_booking(flow, question):
    from app.qr_understanding import build_plan
    h = flow
    body = f'{question} para corte em {h.tomorrow}?'
    plan = build_plan(message=body, catalog=h.catalog, professionals=h.professionals,
        reference_date=h.today, context={})
    assert 'availability' in plan.intents and 'booking' not in plan.intents
    assert plan.requested_date == str(h.tomorrow)
    response = await h.turn(body, preview=False)
    assert response['step'] == 'professional' and all(row['name'] in response['answer'] for row in h.professionals)
    query = h.state()['availability_query']
    assert query['day'] == str(h.tomorrow) and query['services'] == [str(h.cut)]
    assert not h.calls['writes'] and not h.calls['names']


async def reschedule_summary(h):
    h.seed('reschedule_day',appointment=str(h.appointment))
    result=await h.turn(f'Quero remarcar para {h.tomorrow} às 15:00',preview=False)
    assert result['step']=='reschedule_confirm' and not h.calls['writes']
    return result


async def test_pode_remarcar_accepts_only_fresh_reschedule_summary_in_a_new_turn(flow):
    h=flow;await reschedule_summary(h)
    result=await h.turn('Pode remarcar',preview=False)
    assert result['step']=='rescheduled' and [x[0] for x in h.calls['writes']]==['reschedule']


@pytest.mark.parametrize('action',['booking','order','cancel','reschedule'])
def test_reschedule_words_only_affirm_reschedule_action(action):
    result=qr_dialogue.contextual_response('Pode remarcar',awaiting_confirmation=True,confirmation_action=action)
    assert (result.kind=='affirm') is (action=='reschedule')


@pytest.mark.parametrize('body',['Pode remarcar amanhã','Pode remarcar às 16:00','Pode remarcar com Breno',
    'Pode remarcar se tiver desconto','Pode remarcar?','Pode remarcar, não','Pode remarcar e cancelar'])
def test_mixed_or_questioned_reschedule_consent_never_affirms_even_with_model_affirm(body):
    assert qr_dialogue.contextual_response(body,awaiting_confirmation=True,
        confirmation_action='reschedule',semantic_kind='affirm').kind!='affirm'


def test_reschedule_words_without_pending_summary_are_not_consent():
    assert qr_dialogue.contextual_response('Pode remarcar',awaiting_confirmation=False,
        confirmation_action='reschedule').kind!='affirm'


@pytest.mark.parametrize('body',['Pode remarcar às 16:00','Pode remarcar com Breno'])
async def test_reschedule_change_requires_another_summary_and_never_commits_current_one(flow,body):
    h=flow;await reschedule_summary(h)
    result=await h.turn(body,preview=False)
    assert result['step']!='rescheduled' and not h.calls['writes']


async def test_expired_reschedule_summary_cannot_be_authorized_by_pode_remarcar(flow):
    h=flow;await reschedule_summary(h)
    state=h.state();state['confirmation_expires_at']=datetime.now().timestamp()-1
    h.redis.data[h.key]=json.dumps(state)
    result=await h.turn('Pode remarcar',preview=False)
    assert result['step']!='rescheduled' and not h.calls['writes']


@pytest.mark.parametrize('change',['same_message','missing_summary','review_required'])
async def test_reschedule_words_cannot_bypass_summary_identity_or_review(flow,change):
    h=flow;await reschedule_summary(h);state=h.state()
    if change=='missing_summary':state['confirmation_summary']=''
    if change=='review_required':state['confirmation_requires_review']=True
    h.redis.data[h.key]=json.dumps(state)
    token=qr_ai._message_identity.set(state['confirmation_message_id']) if change=='same_message' else None
    try:
        result=await h.turn('Pode remarcar',preview=False)
    finally:
        if token is not None:qr_ai._message_identity.reset(token)
    assert result['step']!='rescheduled' and not h.calls['writes']


@pytest.mark.parametrize('body',[
    'Quero apenas consultar horários de corte amanhã às 10:00, tanto faz o profissional',
    'Quero agendar corte amanhã às 10:00 se tiver desconto, tanto faz o profissional',
    'Não quero agendar corte amanhã às 10:00, tanto faz o profissional',
])
async def test_inline_indifference_does_not_turn_queries_conditions_or_negation_into_holds(flow,body):
    h=flow
    await h.turn(body,preview=False)
    assert not h.calls['writes'] and not h.calls['names']
