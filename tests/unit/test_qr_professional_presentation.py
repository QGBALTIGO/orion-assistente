"""Readable, complete professional choices retain verified IDs and query intent."""
import json
from uuid import uuid4

import pytest

from app import qr_ai, qr_assistant_interactions as interactions
from test_qr_multi_flow import flow


async def test_homonym_clarification_offers_both_verified_ids_without_a_hold(flow):
    h = flow
    for person in h.professionals:
        person['name'] = 'Bruno'
    result = await h.turn(f'Quero agendar corte em {h.tomorrow} às 10:00 com Bruno', preview=False)
    assert result['step'] == 'professional' and not h.calls['writes'], result
    assert '1.' in result['answer'] and '2.' in result['answer']
    shown = await interactions.present(h.redis, h.tenant, '5567999999999', h.namespace, result,
        interactions.Capabilities(assistant_interactions=1))
    action = shown['presentation']['action']
    assert action['button_text'] == 'Ver profissionais'
    rows = action['sections'][0]['rows']
    record = json.loads(await h.redis.get(h.key+':presentation'))
    assert {value['professional_id'] for value in record['choices'].values()} == {str(h.bruno), str(h.breno)}
    assert [row['description'] for row in rows] == ['Opção 1', 'Opção 2']


async def test_availability_professional_list_and_number_remain_read_only(flow):
    h = flow
    first = await h.turn(f'Tem horário para corte em {h.tomorrow}?', preview=False)
    assert not h.calls['writes'], first
    assert '1.' in first['answer'] and '2.' in first['answer'], first
    shown = await interactions.present(h.redis, h.tenant, '5567999999999', h.namespace, first,
        interactions.Capabilities(assistant_interactions=1))
    action = shown['presentation']['action']
    assert action['button_text'] == 'Ver profissionais'
    assert 'reserva' in action['description'].lower()
    result = await h.turn('2', preview=False)
    assert 'Breno' in result['answer'] and not h.calls['writes'], result
    assert h.state().get('step') != 'confirm'


async def test_faq_during_professional_choice_does_not_emit_unrelated_buttons(flow):
    h = flow
    await h.turn(f'Quero agendar corte em {h.tomorrow}', preview=False)
    result = await h.turn('Qual é o endereço?', preview=False)
    shown = await interactions.present(h.redis, h.tenant, '5567999999999', h.namespace, result,
        interactions.Capabilities(assistant_interactions=1))
    assert 'presentation' not in shown and not h.calls['writes']


async def test_factual_answer_naming_staff_cannot_issue_an_implicit_professional_choice(flow):
    h = flow
    h.business['profile']['faqs'].append({'question': 'Quem é o Bruno?', 'answer': 'Bruno faz parte da nossa equipe.'})
    await h.turn(f'Quero agendar corte em {h.tomorrow}', preview=False)
    result = await h.turn('Quem é o Bruno?', preview=False)
    assert result['answer'] == 'Bruno faz parte da nossa equipe.' and '_professional_page' not in result
    shown = await interactions.present(h.redis, h.tenant, '5567999999999', h.namespace, result,
        interactions.Capabilities(assistant_interactions=1))
    assert 'presentation' not in shown and not h.calls['writes']


@pytest.mark.parametrize('question,expected', [
    ('Aceitam Pix?', ['Aceitamos Pix.']),
    ('Quanto custa? Aceitam Pix?', ['R$ 35', 'Aceitamos Pix.']),
])
async def test_native_professional_menu_preserves_all_composed_price_and_faq_facts(flow,question,expected):
    h = flow
    result = await h.turn(f'Quero agendar corte em {h.tomorrow}. {question}', preview=False)
    assert result.get('_professional_page') and all(value in result['answer'] for value in expected), result
    shown = await interactions.present(h.redis, h.tenant, '5567999999999', h.namespace, result,
        interactions.Capabilities(assistant_interactions=1))
    action = shown['presentation']['action']
    assert action['button_text'] == 'Ver profissionais'
    assert all(value in action['description'] for value in expected), action
    assert len(action['sections'][0]['rows']) == 2 and not h.calls['writes'] and not h.calls['names']


async def test_native_menu_budget_falls_back_to_complete_factual_answer_without_cutting_it(flow):
    h = flow
    state = h.seed('professional', professionals=[{'id': str(row['id']), 'name': row['name']} for row in h.professionals])
    factual = 'A'*1000
    result = qr_ai._professional_result(state, h.business, factual=factual)
    assert result['step'] == 'professional' and factual in result['answer']
    shown = await interactions.present(h.redis, h.tenant, '5567999999999', h.namespace, result,
        interactions.Capabilities(assistant_interactions=1))
    assert 'presentation' not in shown and shown['answer'] == result['answer']
    assert not h.calls['writes']


async def test_professional_choice_in_exact_clock_query_cannot_create_a_hold(flow):
    h = flow
    first = await h.turn(f'Tem horário para corte em {h.tomorrow} às 10:00?', preview=False)
    assert first.get('_professional_page') and not h.calls['writes'], first
    result = await h.turn('2', preview=False)
    assert 'Breno' in result['answer'] and '10h' in result['answer'], result
    assert not h.calls['writes'] and not h.calls['names']
    assert 'sem reserva' in result['answer'] and result['step'] != 'confirm'


@pytest.mark.parametrize('count', [4, 10, 14, 50])
async def test_professional_pages_show_every_id_once_and_numbers_select_visible_page(count):
    from app.qr_assistant_professionals import professional_menu
    people = [{'id': str(uuid4()), 'name': f'Profissional {i:02d}'} for i in range(count)]
    seen = []
    page = 0
    while True:
        result = professional_menu(people, page=page)
        assert result['pages'] >= 1 and len(result['rows']) <= 10
        assert '1.' in result['answer'] and result['intro'].count('Profissional ') == 0
        seen.extend(row['id'] for row in result['rows'])
        if page + 1 == result['pages']:
            break
        page += 1
    assert seen == [row['id'] for row in people]


async def test_numeric_professional_page_selection_uses_that_pages_id(flow):
    h = flow
    original = {**h.professionals[1], 'id': str(h.breno)}
    people = [{'id': str(uuid4()), 'name': f'Pessoa {i}'} for i in range(8)] + [original] + [
        {'id': str(uuid4()), 'name': f'Pessoa extra {i}'} for i in range(2)]
    h.seed('professional', professionals=people, professional_page=1)
    result = await h.turn('1', preview=False)
    assert 'Breno' in result['answer'] and h.state()['professional'] == str(h.breno), result
    assert not h.calls['writes']


@pytest.mark.parametrize('body', ['Ver profissionais', 'Pode mostrar os profissionais?', 'Quero ver os profissionais'])
async def test_explicitly_reopening_professionals_returns_current_menu_without_hold(flow,body):
    h = flow
    await h.turn(f'Quero agendar corte em {h.tomorrow}', preview=False)
    result = await h.turn(body, preview=False)
    assert result.get('_professional_page') and '1.' in result['answer'] and '2.' in result['answer'], result
    assert not h.calls['writes']
