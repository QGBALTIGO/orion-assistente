"""Facts may interrupt a pending booking without silently changing its purpose."""
import pytest

from app import qr_ai
from test_qr_multi_flow import flow


async def test_booking_resumes_after_three_factual_interruptions_without_repeating_service(flow):
    h = flow
    result = await h.turn('Quero agendar corte', preview=False)
    assert result['step'] == 'date' and h.state()['services'] == [str(h.cut)]
    await h.turn('Aceitam Pix?', preview=False)
    assert not h.calls['writes']
    result = await h.turn('Em '+h.tomorrow.isoformat(), preview=False)
    assert result['step'] == 'professional' and h.state()['services'] == [str(h.cut)], result
    await h.turn('Tem estacionamento?', preview=False)
    await h.turn('Com Bruno', preview=False)
    await h.turn('Qual é o endereço?', preview=False)
    assert not h.calls['writes'] and h.state()['professional'] == str(h.bruno)
    result = await h.turn('Às 10:00', preview=False)
    assert result['step'] == 'confirm' and h.state()['services'] == [str(h.cut)], result
    assert 'R$ 35' in result['answer'] and 'Bruno' in result['answer'] and '10h' in result['answer']
    assert [row[0] for row in h.calls['writes']] == ['hold']


@pytest.mark.parametrize('body', ['Em 2026-12-10?', 'Em 10/12', 'em amanhã'])
def test_a_standalone_date_preposition_remains_a_date_reply(body):
    assert qr_ai.simple_schedule_reply(body, 'date')


@pytest.mark.parametrize('body', ['Tem em 10/12?', 'Em 10/12, cancele', 'Em 10/12 e mude para barba',
                                  'Em 10/12 com outro profissional'])
def test_mixed_or_informational_date_is_not_a_plain_pending_field(body):
    assert not qr_ai.simple_schedule_reply(body, 'date')
