import pytest
from pydantic import ValidationError
from app.workflow_drafts import Draft,simulate_steps


def test_simulation_is_bounded_ordered_and_contains_no_execution():
    payload=Draft(name=' Acompanhamento ',steps=[{'kind':'handoff','label':'Equipe de atendimento'},{'kind':'reminder_draft','label':'Revisar agendamento'}])
    assert payload.name=='Acompanhamento'
    trace=simulate_steps([s.model_dump() for s in payload.steps])
    assert [s['position'] for s in trace]==[1,2]
    assert all(s['executed'] is False for s in trace)
    assert trace[1]['kind']=='reminder_draft'


@pytest.mark.parametrize('changes',[{'steps':[]},{'steps':[{'kind':'send','label':'Enviar'}]},
    {'name':'   '},{'expected_version':True},{'steps':[{'kind':'tag','label':'a'}]*11}])
def test_draft_rejects_unknown_executors_empty_or_oversize_flow(changes):
    with pytest.raises(ValidationError):
        Draft.model_validate({'name':'Sintético','steps':[{'kind':'handoff','label':'Equipe'}],**changes})
