from datetime import date
import json

import pytest

from app.contact_preferences import parse_birthday,preference_intent,birthday_turn
from app.management_schemas import CustomerFields,CustomerPatch


@pytest.mark.parametrize('message,scope',[
    ('para de mandar promoção','marketing'),('não quero anúncio','marketing'),
    ('cancela o marketing não meu corte','marketing'),('só lembrete de horário','marketing'),
    ('não quero aniversário','birthday'),('não manda mais nada','all'),('parar tudo','all'),
    ('tira meu número da lista','marketing'),('não quero parar mensagens',None),('não cancela meu horário',None)])
def test_optout_is_scoped_and_not_booking_cancellation(message,scope):
    assert preference_intent(message)==scope


@pytest.mark.parametrize('body,scope',[('não quero parar mensagens. Na verdade pare promoções','marketing'),
    ('pare mensagens. Não cancele as mensagens',None),('não me envie promoções','marketing'),
    ('parar tudo, só os lembretes','marketing')])
def test_optout_uses_the_complete_current_instruction(body,scope):
    assert preference_intent(body)==scope


@pytest.mark.parametrize('body,expected',[('15/08',(8,15)),('1508',(8,15)),('15 de agosto',(8,15)),('dia quinze de agosto',(8,15)),('29/02',(2,29))])
def test_day_month_never_requires_or_fabricates_birth_year(body,expected):
    value,error,rest=parse_birthday(body)
    assert value==expected and error is None and rest==''
    model=CustomerFields(name='Pessoa teste',birth_month=value[0],birth_day=value[1])
    assert model.birth_date is None


def test_birthday_and_independent_question_keep_both_intents():
    birthday,error,rest=parse_birthday('15/08 e qual o endereço?')
    assert birthday==(8,15) and error is None and rest=='qual o endereço?'


@pytest.mark.parametrize('body,error',[('31/02','invalid'),('é o do meu filho 15/08','third_party'),('sim','unrelated'),('21/10/2026','year_not_needed'),('quero marcar para 15/08','unrelated')])
def test_invalid_third_party_and_ambiguous_text_do_not_change_customer(body,error):
    assert parse_birthday(body)[1]==error


@pytest.mark.parametrize('fields',[{'birth_month':2,'birth_day':31},{'birth_month':8},{'birth_day':15}])
def test_partial_data_requires_valid_pair(fields):
    with pytest.raises(ValueError):CustomerFields(name='Pessoa teste',**fields)


@pytest.mark.asyncio
async def test_no_invitation_does_not_reinterpret_booking_date(monkeypatch):
    monkeypatch.setenv('BIRTHDAY_COLLECTION_ENABLED','1')
    class Redis:
        async def get(self,key):return None
    assert await birthday_turn('tenant','phone','15/08',Redis(),'key') is None


@pytest.mark.asyncio
async def test_simulated_refusal_preserves_bookings_and_avoids_database(monkeypatch):
    monkeypatch.setenv('BIRTHDAY_COLLECTION_ENABLED','1')
    class Redis:
        removed=[]
        async def get(self,key):return json.dumps({'customer_id':'00000000-0000-4000-8000-000000000001','version':1,'purpose_version':1})
        async def delete(self,key):self.removed.append(key)
    redis=Redis()
    result=await birthday_turn('tenant','phone','prefiro não informar',redis,'key',preview=True)
    assert 'não vou insistir' in result['answer'] and redis.removed==['key:birthday-invite']


@pytest.mark.asyncio
@pytest.mark.parametrize('body',['31/02','é o do meu filho 15/08','21/10/2026'])
async def test_invalid_birthday_does_not_assert_current_booking_status(monkeypatch,body):
    monkeypatch.setenv('BIRTHDAY_COLLECTION_ENABLED','1')
    class Redis:
        async def get(self,key):return json.dumps({'customer_id':'00000000-0000-4000-8000-000000000001','version':1,'purpose_version':1})
    result=await birthday_turn('tenant','phone',body,Redis(),'key')
    assert 'Esse pedido não altera seus agendamentos.' in result['answer']
    assert 'agendamento permanece' not in result['answer']
