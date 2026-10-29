from datetime import datetime,timedelta,timezone,date
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest

from app.automation_templates import compose,library_template,EVENTS
from app.automation_policy import schedule_for,schedule_explanation
from app.communication_links import delivery_link
from app.management_preferences import DeliveryPolicy

NOW=datetime(2026,10,20,14,tzinfo=timezone.utc)
FACTS={'empresa':'Empresa teste','cliente':'Pessoa teste','servico':'Corte','profissional':'Carlos',
    'data':'21/10/2026','horario':'14h','valor':'R$ 35,00','protocolo':'DEMO-123','cupom':'DEMO-FIEL'}


@pytest.mark.parametrize('event',EVENTS)
@pytest.mark.parametrize('name,link',[(True,True),(True,False),(False,True),(False,False)])
def test_all_library_variants_compose_with_optional_groups(event,name,link):
    facts={**FACTS}
    if not name:facts.pop('cliente')
    if link:facts.update(link_agendar='https://orion.example.com/s/teste',link_review='https://g.page/r/review-test/review')
    result=compose(event,{'template_source':'library'},facts,public_origin='https://orion.example.com')
    assert result.variant==('com_nome' if name else 'sem_nome')+'__'+('com_link' if link else 'sem_link')
    assert '\n\n' in result.body and '{cliente}' not in result.body and '{link_' not in result.body
    assert result.metadata()['utf16_units']<=1200
    if not name:assert 'Pessoa teste' not in result.body


def test_custom_template_is_not_replaced_when_optional_fact_absent():
    custom='Meu texto exclusivo: {cliente} {link_review}'
    with pytest.raises(ValueError,match='missing_template_fact'):
        compose('after_service',{'template':custom},FACTS)
    assert custom=='Meu texto exclusivo: {cliente} {link_review}'


def test_names_are_literalized_by_existing_channel_renderer():
    result=compose('loyalty_reward',{'template':'Olá {cliente}'},{'cliente':'*Nome* _teste_'})
    assert '\\*Nome\\*' in result.body and '\\_teste\\_' in result.body


def test_destination_is_checked_at_composition_even_for_custom_template():
    with pytest.raises(ValueError):
        compose('after_service',{'template':'{link_review}'},{'link_review':'https://outsider.example.com/r'})
    assert delivery_link('https://review.empresa.com/avaliar',purpose='review',approved_hosts=['review.empresa.com'])
    for value in ['http://g.page/r/review','https://g.page.evil.com/r','https://g.page/r?token=private','https://user:secret@g.page/r']:
        with pytest.raises(ValueError):delivery_link(value,purpose='review')


def test_preview_and_job_composition_share_exact_body_and_version():
    rule={'template_source':'library','template_revision':1}
    preview=compose('booking_confirmed',rule,FACTS)
    job=compose('booking_confirmed',rule,FACTS)
    assert preview==job and preview.metadata()==job.metadata()


def test_pre_activation_notification_is_rejected_but_future_reminder_is_not():
    facts={'occurred_at':NOW-timedelta(hours=2),'status':'confirmed'}
    assert schedule_explanation('booking_confirmed',facts,NOW,'UTC',approved_at=NOW)==(None,'before_activation')
    schedule,reason=schedule_explanation('reminder_1h',{'status':'confirmed','start_at':NOW+timedelta(hours=2)},NOW,'UTC',approved_at=NOW)
    assert schedule.due_at==NOW+timedelta(hours=1) and reason is None


def test_quiet_window_explains_suppression_and_only_explicit_policy_changes_it():
    now=NOW.replace(hour=7);facts={'status':'confirmed','start_at':NOW.replace(hour=9)}
    assert schedule_explanation('reminder_1h',facts,now,'UTC')==(None,'quiet_window_expired')
    value=schedule_for('reminder_1h',facts,now,'UTC',policy={'operational_start_hour':7,'operational_end_hour':21})
    assert value.due_at.hour==8
    holiday=NOW.date().isoformat()
    assert schedule_for('reminder_1h',facts,now,'UTC',policy={'holidays':[holiday]}) is None


def test_partial_birthday_and_explicit_leap_day_policy_do_not_store_fake_year():
    facts={'birth_month':2,'birth_day':29,'has_future_booking':False}
    now=datetime(2027,2,21,10,tzinfo=timezone.utc)
    assert schedule_for('birthday_7d',facts,now,'UTC') is None
    assert schedule_for('birthday_7d',facts,now,'UTC',policy={'birthday_leap_day':'feb28'}) is not None
    assert facts=={'birth_month':2,'birth_day':29,'has_future_booking':False}


@pytest.mark.parametrize('value',[{'operational_start_hour':18,'operational_end_hour':9},{'weekdays':[7]},{'weekdays':[]},{'holidays':['2026-02-31']}])
def test_policy_cannot_claim_invalid_window(value):
    with pytest.raises(ValueError):DeliveryPolicy(**value)


@pytest.mark.parametrize('value',[{'operational_start_hour':True},{'marketing_end_hour':False},{'weekdays':[True]}])
def test_policy_rejects_boolean_hours_and_weekdays(value):
    with pytest.raises(ValueError):DeliveryPolicy(**value)


@pytest.mark.parametrize('url',['https://unknown.example.com/path','https://g.page/r?session=private',
    'https://www.google.com/url?q=https%3A%2F%2Fevil.example.com','https://g.page/r?next=https%3A%2F%2Fevil.example.com'])
def test_literal_custom_links_cannot_bypass_destination_approval(url):
    with pytest.raises(ValueError):compose('after_service',{'template':'Texto exclusivo '+url},FACTS)


def test_approved_literal_custom_link_keeps_exact_custom_body():
    body='Texto exclusivo https://review.empresa.com/avaliar'
    result=compose('after_service',{'template':body},FACTS,business={'approved_link_hosts':['review.empresa.com']})
    assert result.body==body and result.source=='custom'


@pytest.mark.asyncio
async def test_human_notice_goes_to_team_only_without_send_or_customer_job(monkeypatch):
    from plan_fixture import install_plan_fixture
    install_plan_fixture(monkeypatch, "essencial")
    from app import automation_runtime as runtime
    calls=[]
    class DB:
        async def execute(self,query,params=None):calls.append((str(query),params));return None
    monkeypatch.setattr(runtime,'execution_enabled',lambda:True)
    monkeypatch.setattr(runtime,'execution_authorized',lambda x:True)
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):return NOW
    monkeypatch.setattr(runtime,'datetime',Clock)
    async def contact(*args):return {'operational_opt_in':True},{'human_paused':True}
    monkeypatch.setattr(runtime,'contact_facts',contact)
    result=await runtime.queue_notification(DB(),uuid4(),{'timezone':'UTC','approved_at':NOW-timedelta(minutes=1)},
        {'preferences':{'automations':{'rules':[{'event':'cancelled','requested_enabled':True,'template':'{protocolo}'}]}}},
        {'whatsapp_e164':'+5567999999901'},'cancelled','cancel-synthetic',{'status':'cancelled','occurred_at':NOW},FACTS)
    assert result is False
    assert len(calls)==1 and 'INSERT INTO communication_pending' in calls[0][0]
    assert calls[0][1]['reason']=='human_paused'
