"""An owned opaque protocol selects a candidate; it is not natural language."""
from copy import deepcopy
from datetime import datetime, timezone

import pytest

from app import qr_cancellation as flow
from app.qr_cancellation import choose_candidate


@pytest.fixture
def records(monkeypatch):
    # Relative dates must not change meaning with the CI runner's wall clock.
    monkeypatch.setattr(flow, '_now', lambda: datetime(2026, 11, 10, tzinfo=timezone.utc))
    return [
        {'id': '11111111-1111-4111-8111-111111111111', 'protocol': 'AG-T4H7LY',
         'start_at': '2026-11-13T14:00:00+00:00', 'professional': 'Bruno'},
        {'id': '22222222-2222-4222-8222-222222222222', 'protocol': 'AG-3JL6PQ',
         'start_at': '2026-11-14T18:00:00+00:00', 'professional': 'Carlos'},
    ]


@pytest.mark.parametrize('protocol', ['AG-T4H7LY', 'AG-3JL6PQ', 'AG-ABCD2Z', 'AG-11H30Z', 'AG-CARLOS', 'AG-TALVEZ', 'AG-ESPERA', 'AG-ULTIMO'])
@pytest.mark.parametrize('envelope', ['{}', 'Quero cancelar {}'])
def test_exact_owned_opaque_protocol_is_not_a_clock_or_professional(records, protocol, envelope):
    records[0]['protocol'] = protocol
    # Keep every protocol unique: the opaque ID never chooses by list order.
    records[1]['protocol'] = 'AG-X9V2KQ'
    before = deepcopy(records)
    assert choose_candidate(envelope.format(protocol), records, 'America/Campo_Grande') == ('selected', records[0]['id'])
    assert records == before


@pytest.mark.parametrize('suffix', ['em 13/11/2026', 'às 10:00 da manhã', 'com Bruno', 'em 13/11/2026 às 10:00 da manhã com Bruno'])
def test_owned_protocol_preserves_matching_external_date_clock_and_professional(records, suffix):
    assert choose_candidate('AG-T4H7LY '+suffix, records, 'America/Campo_Grande') == ('selected', records[0]['id'])


@pytest.mark.parametrize('body', [
    'AG-T4H7LY em 14/11/2026',
    'AG-T4H7LY às 14:00',
    'AG-T4H7LY com Carlos',
    'AG-T4H7LY amanhã',
    'AG-T4H7LY às 10:00',  # The existing parser still requires AM/PM disambiguation.
    'AG-T4H7LY depois das 10:00',
    'Não quero cancelar AG-T4H7LY',
    'AG-T4H7LY ou AG-3JL6PQ',
    'AG-T4H7LY AG-3JL6PQ',
    'AG-T4H7LY AG-FOREIGN',
    'AG-T4H7LY-FOREIGN',
    'AG-T4H7LY o segundo',
    'AG-T4H7LY 2',
    'Se puder, cancelar AG-T4H7LY',
    'AG-T4H7LY talvez',
    'AG-FOREIGN às 10:00',
    'T4H7LY',
    'XAG-T4H7LYY',
])
def test_opaque_protocol_never_overrides_external_conflicts_negation_or_unknown_references(records, body):
    selected, identity = choose_candidate(body, records, 'America/Campo_Grande')
    assert selected != 'selected' and identity is None


def test_two_records_with_the_same_protocol_remain_ambiguous(records):
    records[1]['protocol'] = records[0]['protocol']
    assert choose_candidate('AG-T4H7LY', records, 'America/Campo_Grande') == ('ambiguous', None)


@pytest.mark.parametrize('protocol', ['AG-TALVEZ', 'AG-ESPERA'])
@pytest.mark.parametrize('prefix', ['Não cancele ', 'Talvez cancelar '])
def test_external_negation_or_condition_is_retained_for_an_owned_keyword_like_protocol(records, protocol, prefix):
    records[0]['protocol'] = protocol
    assert choose_candidate(prefix+protocol, records, 'America/Campo_Grande') == ('ambiguous', None)


@pytest.mark.parametrize('reference', ['o primeiro', '1'])
def test_external_ordinal_or_number_may_only_select_the_same_owned_protocol(records, reference):
    assert choose_candidate('AG-T4H7LY '+reference, records, 'America/Campo_Grande') == ('selected', records[0]['id'])
