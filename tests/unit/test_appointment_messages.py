from datetime import datetime, timezone
from uuid import uuid4

import pytest
from app.appointment_messages import compose, label, safe_link


def row():
    return {'id':uuid4(),'customer_name':'João Silva','professional_name':'Peixoto',
        'start_at':datetime(2026,11,1,14,tzinfo=timezone.utc),'timezone':'America/Campo_Grande',
        'protocol':'AG-ABCDEF','items':[{'name_snapshot':'Corte','duration_snapshot_min':40}]}


@pytest.mark.parametrize('event',['confirmation','professional_new','professional_cancelled',
    'professional_rescheduled','customer_cancelled','customer_rescheduled','reminder','review'])
def test_central_templates_use_current_facts_without_missing_placeholders(event,monkeypatch):
    monkeypatch.setenv('PANEL_PUBLIC_URL','https://orionagenda.pro')
    result=compose(event,row(),company='Barbearia',customer_link='https://orionagenda.pro/a#secret',
        booking_link='https://orionagenda.pro/s/barbearia',previous={'start_at':'2026-10-31T14:00:00+00:00'})
    assert '{' not in result and 'None' not in result and 'undefined' not in result
    assert len(result.encode('utf-16-le'))//2<=1200
    assert 'João' in result


def test_labels_cannot_inject_links_or_whatsapp_markup():
    assert 'https://' not in label('*Oi* https://evil.example/test\n_Nome_')
    assert '*' not in label('*Olá*')
    assert safe_link('https://evil.example/a#secret') is None


def test_staff_link_requires_panel_auth_and_does_not_reuse_customer_token(monkeypatch):
    monkeypatch.setenv('PANEL_PUBLIC_URL','https://orionagenda.pro')
    result=compose('professional_new',row(),company='Barbearia',customer_link='https://orionagenda.pro/a#secret')
    assert '/panel?aba=agenda&agendamento=' in result and '#secret' not in result
