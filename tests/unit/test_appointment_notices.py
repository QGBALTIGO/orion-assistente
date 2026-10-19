import json
from datetime import datetime,timezone
from unittest.mock import AsyncMock
from uuid import uuid4
import pytest
from app.appointment_notices import notice_text,phone,clean,enqueue_notice,EVENTS

@pytest.fixture
def snapshot():
    return {'business_name':'Clínica Orion','professional_name':'Ana','start_at':'2026-10-20T15:30:00+00:00',
            'previous_start_at':'2026-10-19T15:30:00+00:00','timezone':'America/Sao_Paulo','protocol':'AG-ABC123',
            'phone':'5511999999999','reason':'SECRET_REASON','service_names':'SECRET_CLINICAL_DETAIL'}

@pytest.mark.parametrize('event',sorted(EVENTS))
def test_factual_bounded_message_without_private_details(event,snapshot):
    result=notice_text(event,snapshot)
    assert '20/10/2026 às 12:30' in result and 'Clínica Orion' in result and 'AG-ABC123' in result
    assert 'SECRET' not in result and snapshot['phone'] not in result and len(result)<=1200
    assert ('Horário anterior:' in result)==(event=='rescheduled')
    assert ('Novo horário:' in result)==(event=='rescheduled')

def test_empty_or_overlong_names_and_markup_are_safe(snapshot):
    snapshot.update(business_name='*A*\nB'+('z'*1000),professional_name='_*`Ana\x00')
    result=notice_text('confirmed',snapshot)
    assert len(result)<1200 and '\x00' not in result and 'Profissional: Ana' in result

def test_invalid_event_date_and_phone():
    with pytest.raises(ValueError):notice_text('billing',{})
    with pytest.raises(ValueError):notice_text('confirmed',{'start_at':'2026-10-19T10:00:00','timezone':'UTC'})
    for value in ['123','+0x','person@lid','+5511abc',None]:assert phone(value) is None
    assert phone('+5511999999999')=='5511999999999'
    assert clean('\u202eAna\n Silva')=='Ana Silva'

@pytest.mark.asyncio
async def test_disabled_feature_never_requires_new_schema(monkeypatch):
    monkeypatch.delenv('ORION_APPOINTMENT_NOTICES_ENABLED',raising=False)
    db=AsyncMock();await enqueue_notice(db,uuid4(),uuid4(),'confirmed',{'source':'panel'})
    db.execute.assert_not_called()

@pytest.mark.asyncio
async def test_hold_does_not_generate_a_customer_notice(monkeypatch):
    monkeypatch.setenv('ORION_APPOINTMENT_NOTICES_ENABLED','1')
    db=AsyncMock();await enqueue_notice(db,uuid4(),uuid4(),'hold_created',{'source':'panel'})
    db.execute.assert_not_called()
