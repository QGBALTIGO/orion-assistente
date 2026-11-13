"""Professional identity from text; supplied transcript is not real ASR proof."""
from datetime import date
from uuid import uuid4

import pytest

from app import qr_ai
from app.qr_understanding import UnderstandingPlan, build_plan
from test_qr_multi_flow import flow

TRANSCRIPT = ('Oi, bom dia. Quero consultar os horários para corte masculino amanhã depois das duas da tarde. '
    'Pode me mostrar os profissionais disponíveis? Este é um teste autorizado')


@pytest.mark.parametrize('model',['none','correct_availability'])
async def test_supplied_transcript_lists_eligible_staff_without_name_ambiguity_or_booking(flow,monkeypatch,model):
    h=flow
    h.catalog[0]['name']='Corte Masculino';h.catalog[1]['name']='Barba Tradicional'
    h.catalog.extend({'id':uuid4(),'name':f'Serviço sintético {n:02d}',
        'price_cents':1000+n,'duration_min':30} for n in range(12))
    h.professionals[0]['name']='Guilherme';h.professionals[1]['name']='Peixoto'
    h.professionals.extend([{'id':uuid4(),'name':'Teste Orion - Excluir 30out'},
        {'id':uuid4(),'name':'Teste IA Orion 06nov'}])
    if model=='correct_availability':
        h.proposal['value']=UnderstandingPlan(intents=['availability'],query_service_ids=[h.cut],
            requested_date=h.tomorrow.isoformat(),requested_time='14:00',time_relation='after',
            professional_id=None,ambiguities=[])
    async def no_write(*a,**k):raise AssertionError('A consultation cannot create holds or capture names')
    for name in ('book_appointment','confirm_hold','reschedule_appointment','remember_name'):
        monkeypatch.setattr(qr_ai,name,no_write)
    result=await h.turn(TRANSCRIPT,preview=False)
    assert len(h.catalog)==14 and len(h.professionals)==4
    assert 'Não consegui distinguir o nome' not in result['answer']
    assert all(p['name'] in result['answer'] for p in h.professionals)
    query=h.state()['availability_query']
    assert query['services']==[str(h.cut)] and query['day']==h.tomorrow.isoformat()
    assert query['pending_clock']=='14:00' and query['pending_relation']=='after' and query['waiting_for']=='professional'
    assert 'professional' not in query and not h.calls['writes'] and not h.calls['names'] and not h.calls['reads']
    assert not set(h.state()) & {'hold','hold_snapshot','confirmation_summary','confirmation_message_id','consent'}


@pytest.fixture
def named_staff():
    rows=[{'id':uuid4(),'name':name} for name in ('João Silva','João Souza','Guilherme','Peixoto',
        'Teste Orion - Excluir 30out','Teste IA Orion 06nov')]
    return rows


def plan_for(message,rows,*,context=None):
    return build_plan(message=message,catalog=[{'id':uuid4(),'name':'Corte Masculino','price_cents':3500}],
        professionals=rows,reference_date=date(2026,11,6),context=context or {})


@pytest.mark.parametrize('message,index',[
    ('Quero consultar com João Silva amanhã',0),('Quero consultar com o João Souza amanhã',1),
    ('João Silva tem horário amanhã?',0),('Guilherme tem horário amanhã?',2),
    ('Peixoto tem horário amanhã?',3),('Quero com Guilherme amanhã',2),
    ('Quero com Peixoto amanhã',3),('Quero com Teste IA Orion 06nov amanhã',5),
    ('Quero com Teste Orion - Excluir 30out amanhã',4),
    ('Meu nome é João Silva. Quero com João Souza amanhã',1),
    ('Meu nome é João Silva. Quero com João Silva amanhã',0),
    ('Com João Silva, não, melhor João Souza',1),
])
def test_complete_name_and_existing_short_staff_names_select_only_exact_id(named_staff,message,index):
    result=plan_for(message,named_staff)
    assert result.professional_id==named_staff[index]['id'] and 'professional' not in result.ambiguities


@pytest.mark.parametrize('message',['Quero com o João amanhã','Quero com Teste amanhã',
    'Quero com João Silva ou João Souza amanhã',
    'Com João Silva, não, melhor João',
    'Com Teste IA Orion 06nov, não, melhor Teste'])
def test_real_shared_first_name_or_explicit_alternatives_stay_ambiguous(named_staff,message):
    result=plan_for(message,named_staff)
    assert result.professional_id is None and 'professional' in result.ambiguities


@pytest.mark.parametrize('message',[
    'Este é um teste autorizado','É um teste de atendimento',
    'Meu nome é João Silva. Quanto custa corte masculino?',
    'Meu filho João Souza quer consultar horários',
    'Meu filho se chama João Silva. Quero consultar horários',
    'Sou o pai do João Silva e quero consultar horários para ele',
    'Não quero com João Silva amanhã',
])
def test_generic_metadata_customer_and_family_names_do_not_select_staff(named_staff,message):
    result=plan_for(message,named_staff)
    assert result.professional_id is None and 'professional' not in result.ambiguities


def test_single_compound_test_name_cannot_be_silently_selected_from_metadata(named_staff):
    result=plan_for(TRANSCRIPT,[named_staff[4]])
    assert result.professional_id is None and 'professional' not in result.ambiguities


def test_complete_name_wins_over_overlapping_single_word_name(named_staff):
    rows=[{'id':uuid4(),'name':'João'},named_staff[0]]
    assert plan_for('Quero com João Silva amanhã',rows).professional_id==rows[1]['id']


def test_identical_complete_names_remain_ambiguous(named_staff):
    rows=[named_staff[0],{'id':uuid4(),'name':'João Silva'}]
    result=plan_for('Quero com João Silva amanhã',rows)
    assert result.professional_id is None and 'professional' in result.ambiguities


def test_unique_first_name_reply_is_accepted_only_in_professional_choice_context(named_staff):
    rows=[named_staff[0],named_staff[2]]
    assert plan_for('João',rows).professional_id is None
    assert plan_for('João',rows,context={'step':'professional'}).professional_id==rows[0]['id']
    assert plan_for('Quero com o João amanhã',rows).professional_id==rows[0]['id']
