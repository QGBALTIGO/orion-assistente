"""Reference ingestion/probes; these are not full dialogue acceptance tests."""
from datetime import date
import json
from pathlib import Path
from uuid import UUID

import pytest
from app.communication_events import safe_body
from app.qr_understanding import build_plan
from scripts.communication_corpus import customer_payloads,SOURCE_SHA256

CORPUS=json.loads((Path(__file__).parents[1]/'fixtures/communication/references.json').read_text(encoding='utf-8'))
CASES=CORPUS['cases']
LANGUAGE=[c for c in CASES if c['annex']=='F']
CATALOG=[{'id':UUID(int=1),'name':'Corte de cabelo','price_cents':3500},
    {'id':UUID(int=2),'name':'Barba','price_cents':2500}]


@pytest.mark.parametrize('case',CASES,ids=lambda c:c['id'])
def test_reference_input_oracle_and_backend_notes_have_separate_boundaries(case):
    assert case['authority']=='reference_only_never_message_authority'
    assert case['dialogue_acceptance']=='not_executed' and not case['state_materialized']
    for payload in customer_payloads(case):
        assert set(payload)=={'kind','text','order'}
        complete,issue=safe_body(payload['text'])
        assert (complete==payload['text'].strip() and issue is None) or complete=='' and issue in {'invalid','oversized'}
    assert all(event['role']=='cliente' for event in case['customer_events'])


@pytest.mark.parametrize('case',LANGUAGE,ids=lambda c:c['id'])
def test_reference_language_input_runs_only_the_pure_proposal_layer(case):
    # Deliberately no state/expected-response injection: this probe exercises
    # parser robustness and bounded proposed facts, not contextual correctness.
    body=customer_payloads(case)[0]['text']
    plan=build_plan(message=body,catalog=CATALOG,reference_date=date(2026,10,20),context={})
    assert set(plan.service_ids)<=set(item['id'] for item in CATALOG)
    assert set(plan.query_service_ids)<=set(item['id'] for item in CATALOG)
    assert len(plan.intents)<=10 and len(plan.service_ids)<=6
    assert not any(key in plan.model_dump() for key in ('protocol','confirmed','sent','account_id','tenant_id'))


def test_provenance_and_friday_reference_fix_never_invent_an_interval():
    assert CORPUS['source_sha256']==SOURCE_SHA256 and CORPUS['counts']=={'B':50,'C':160,'F':128}
    assert len(LANGUAGE)==128 and len([c for c in CASES if c['annex']=='C'])==160
    case=next(c for c in CASES if c['id']=='DIA-40')
    assert date(2026,10,23).weekday()==4
    assert 'Qual intervalo' in case['reference_oracle'][0]['body']
    assert '12h' not in case['reference_oracle'][0]['body']
    assert CORPUS['oracle_patches'][0]['original_source_preserved']


def test_backend_confirmation_reference_is_not_a_customer_command():
    case=next(c for c in CASES if c['id']=='DIA-01')
    assert any('Backend fictício' in str(note) for note in case['fixture_notes'])
    assert all('Backend fictício' not in payload['text'] for payload in customer_payloads(case))
