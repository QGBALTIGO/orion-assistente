"""Reset commands use the paged catalogue; they never select or book services."""
import json
from datetime import datetime
from uuid import uuid4

import pytest

from app import qr_ai, qr_assistant_catalog as catalog, qr_assistant_interactions as choices
from app.groq_ai import Intention
from app.qr_presentation import message_units
from test_qr_multi_flow import flow


ALIASES = ['MENU', ' menu ', 'Reiniciar', 'COMEÇAR', 'comecar', 'recomecar', 'Recomeçar']


@pytest.fixture(autouse=True)
def legacy_quote_boundary(flow, monkeypatch):
    flow.calls['legacy'] = []

    async def legacy(**kwargs):
        flow.calls['legacy'].append(kwargs['message'])
        return Intention(intent='perguntar', service_ids=[])

    monkeypatch.setattr(qr_ai, 'interpret', legacy)


def full_catalogue(h):
    h.catalog.extend({'id': uuid4(), 'name': f'Serviço sintético {index+1:02}',
        'price_cents': 1000+index, 'duration_min': 95 if index < 10 else 30}
        for index in range(12))
    assert len(h.catalog) == 14 and sum(row['duration_min'] for row in h.catalog[:12]) == 1010


def old_summary(h):
    state = h.seed('confirm', hold=str(uuid4()), confirmation_summary='Resumo antigo sem autorização nova.',
        confirmation_expires_at=datetime.now().timestamp()+300, customer_name='Nome anterior',
        catalog_browser={'page': 7, 'query': 'antiga', 'category': 'antiga'},
        catalog_menu_ids=[str(h.beard)], catalog_choice_expected=True,
        history=[{'role': 'user', 'content': 'Assunto anterior'}])
    h.redis.data[h.key+':commerce'] = json.dumps({'step': 'order_confirm', 'consent': True})
    return state


@pytest.mark.parametrize('command', ALIASES)
async def test_reset_uses_all_fourteen_services_in_two_native_pages_without_catalog_total(flow, command):
    h = flow; full_catalogue(h); old_summary(h)
    h.proposal['error'] = True  # A reset menu must not depend on model availability.
    first = await h.turn(command, preview=False)
    assert first['step'] == 'menu' and first['_catalog_page']['total'] == 14, first
    assert first['_catalog_page']['page'] == 0 and first['_catalog_page']['pages'] == 2
    first_ids = [str(row['id']) for row in first['_catalog_page']['rows']]
    assert first_ids == [str(row['id']) for row in h.catalog[:8]]
    assert '16h50' not in first['answer'] and 'Duração estimada' not in first['answer']
    assert '12 dos' not in first['answer'] and len(first['_catalog_page']['rows']) == 8
    assert h.state()['catalog_browser'] == {'page': 0, 'query': '', 'category': ''}
    assert h.key+':commerce' not in h.redis.data
    for field in ('hold', 'hold_snapshot', 'confirmation_summary', 'confirmation_message_id',
                  'confirmation_expires_at', 'confirmation_presentation', 'replacement_hold', 'services', 'professional', 'day'):
        assert field not in h.state()
    assert 'Assunto anterior' not in json.dumps(h.state().get('history', []))
    seen, result = [], first
    for page in range(2):
        assert result['_catalog_page']['page'] == page
        seen.extend(str(row['id']) for row in result['_catalog_page']['rows'])
        shown = await choices.present(h.redis, h.tenant, '5567999999999', h.namespace, result,
            choices.Capabilities(assistant_interactions=1))
        action = shown['presentation']['action']
        rows = action['sections'][0]['rows']
        assert action['type'] == 'list' and len(rows) <= 10
        assert all(message_units(row['title']) <= 24 and message_units(row['description']) <= 72 for row in rows)
        if page == 0:
            next_row = next(row for row in rows if row['title'] == 'Mais serviços')
            record = json.loads(await h.redis.get(h.key+':presentation'))
            record['provider_id'] = 'synthetic-current-page'
            await h.redis.set(h.key+':presentation', json.dumps(record))
            selected = choices.Selection(id=next_row['id'], quoted_message_id='synthetic-current-page')
            next_body = await choices.resolve(h.redis, h.tenant, '5567999999999', h.namespace, selected)
            assert next_body == 'ver serviços página 2'
            result = await h.turn(next_body, preview=False)
    assert seen == [str(row['id']) for row in h.catalog] and len(set(seen)) == 14
    assert not h.calls['plans'] and not h.calls['legacy'] and not h.calls['reads'] and not h.calls['writes'] and not h.calls['names']


@pytest.mark.parametrize('command', ALIASES)
async def test_empty_catalogue_reset_keeps_the_legacy_empty_message(flow, command):
    h = flow; h.catalog.clear()
    assert await catalog.handle_request(command, {}, h.business, h.catalog, h.redis, h.key) is None
    result = await h.turn(command, preview=False)
    assert result['step'] == 'no_catalog' and 'Ainda não há serviços ativos cadastrados' in result['answer']
    assert '_catalog_page' not in result
    shown = await choices.present(h.redis, h.tenant, '5567999999999', h.namespace, result,
        choices.Capabilities(assistant_interactions=1))
    assert 'presentation' not in shown and not h.calls['writes']


@pytest.mark.parametrize('message', ['Menu e confirme meu agendamento', 'reiniciar e cancelar minha reserva',
    'Começar agendamento de corte amanhã', 'Quero recomeçar com outro horário', 'Quero agendar corte amanhã'])
async def test_reset_words_in_mixed_operations_are_not_absorbed_by_catalogue(flow, message):
    h = flow; full_catalogue(h); state = old_summary(h); before = dict(h.redis.data)
    assert await catalog.handle_request(message, state, h.business, h.catalog, h.redis, h.key) is None
    assert h.redis.data == before and not h.calls['writes']


@pytest.mark.parametrize('capabilities', [[], ['catalog_answers'], ['catalog_answers', 'product_orders']])
async def test_reset_catalogue_does_not_take_over_non_booking_niches(flow, capabilities):
    h = flow; full_catalogue(h); state = old_summary(h); h.business['profile']['capabilities'] = capabilities
    before = dict(h.redis.data)
    assert await catalog.handle_request('MENU', state, h.business, h.catalog, h.redis, h.key) is None
    assert h.redis.data == before and not h.calls['writes']


async def test_reset_still_requires_catalogue_answer_capability(flow):
    h = flow; full_catalogue(h); h.business['profile']['capabilities'] = ['appointments']
    result = await h.turn('MENU', preview=False)
    assert result['step'] == 'capability_unavailable' and '_catalog_page' not in result
    assert not h.calls['writes'] and not h.calls['names']


async def test_reset_text_only_transport_retains_a_paged_answer_without_native_tokens(flow):
    h = flow; full_catalogue(h)
    result = await h.turn('MENU', preview=False)
    assert result['_catalog_page']['pages'] == 2
    shown = await choices.present(h.redis, h.tenant, '5567999999999', h.namespace, result, None)
    assert 'presentation' not in shown and 'Página 1/2' in shown['answer'] and '16h50' not in shown['answer']
    assert not h.calls['writes']


async def test_reset_invalidates_the_old_confirmation_selection_without_new_consent(flow):
    h = flow; full_catalogue(h); state = old_summary(h)
    old = await choices.present(h.redis, h.tenant, '5567999999999', h.namespace,
        {'answer': state['confirmation_summary'], 'step': 'confirm'}, choices.Capabilities(assistant_interactions=1))
    record = json.loads(await h.redis.get(h.key+':presentation')); record['provider_id'] = 'synthetic-old-provider'
    await h.redis.set(h.key+':presentation', json.dumps(record))
    selection = choices.Selection(id=old['presentation']['action']['buttons'][0]['id'], quoted_message_id='synthetic-old-provider')
    assert await choices.selected_action(h.redis, h.tenant, '5567999999999', h.namespace, selection) is not None
    result = await h.turn('MENU', preview=False)
    assert result['step'] == 'menu' and result.get('_catalog_page')
    assert await choices.selected_action(h.redis, h.tenant, '5567999999999', h.namespace, selection) is None
    assert not h.calls['writes'] and not h.calls['names']
