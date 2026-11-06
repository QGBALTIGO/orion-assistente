"""A requested window filters actual slots and never authorizes a booking."""
import pytest

from app import qr_ai
from test_qr_multi_flow import flow


@pytest.mark.parametrize('window,expected', [
    ('depois das 14h e antes das 16h', ['14:30','15:00','15:30']),
    ('entre 14h e 16h', ['14:00','14:30','15:00','15:30','16:00']),
])
async def test_window_uses_backend_slots_with_correct_endpoints_without_hold(flow,window,expected):
    h = flow
    result = await h.turn(f'Tem horário para corte em {h.tomorrow} com Bruno {window}?', preview=False)
    assert '15h' in result['answer'] and h.calls['reads'], result
    assert not h.calls['writes'] and not h.calls['names']
    assert '13h' not in result['answer'] and '17h' not in result['answer']
    # Check the filter independently on the same actual slot representation.
    source = [(h.tomorrow.isoformat()+'T'+clock+':00-04:00') for clock in ['14:00','14:30','15:00','15:30','16:00']]
    pending = qr_ai._availability_window(window)
    assert [value[11:16] for value in qr_ai._window_slots(source, pending, str(h.zone))] == expected


async def test_window_survives_professional_question_and_number_stays_read_only(flow):
    h = flow
    result = await h.turn(f'Tem horário para corte em {h.tomorrow} depois das 14h e antes das 16h?', preview=False)
    assert result.get('_professional_page') and not h.calls['writes'], result
    assert h.state()['availability_query']['pending_window']['lower'] == '14:00'
    result = await h.turn('2', preview=False)
    assert 'Breno' in result['answer'] and '15h' in result['answer'], result
    assert '13h' not in result['answer'] and '17h' not in result['answer'] and not h.calls['writes']


@pytest.mark.parametrize('window', ['depois das 16h e antes das 14h', 'depois das 14h ou antes das 16h',
    'depois das 14h e antes das 16h ou às 17h', 'entre 25h e 26h'])
async def test_invalid_or_multiple_windows_cannot_be_silently_reduced_to_a_valid_query(flow,window):
    h = flow
    result = await h.turn(f'Tem horário para corte em {h.tomorrow} com Bruno {window}?', preview=False)
    assert not h.calls['writes'] and not h.calls['reads'], result
    assert not (h.state().get('availability_query') or {}).get('pending_window')


async def test_window_does_not_strip_time_ambiguity_from_booking_with_consent(flow):
    h = flow
    result = await h.turn(f'Quero agendar corte em {h.tomorrow} com Bruno depois das 14h e antes das 16h, pode confirmar', preview=False)
    assert result['step'] not in {'confirm','confirmed'} and not h.calls['writes'], result


async def test_window_filters_each_professional_agenda_without_a_reservation(flow):
    h = flow
    result = await h.turn(f'Quais profissionais têm horário para corte em {h.tomorrow} depois das 14h e antes das 16h?', preview=False)
    assert len(h.calls['reads']) == 2 and '15h' in result['answer'], result
    assert '13h' not in result['answer'] and '17h' not in result['answer']
    assert not h.calls['writes'] and not h.calls['names']


async def test_new_exact_clock_supersedes_previous_read_only_window(flow):
    h = flow
    await h.turn(f'Tem horário para corte em {h.tomorrow} com Bruno depois das 14h e antes das 16h?', preview=False)
    result = await h.turn('Tem horário às 17h?', preview=False)
    assert '17h' in result['answer'] and '15h' not in result['answer'], result
    assert not h.calls['writes'] and not h.calls['names']
