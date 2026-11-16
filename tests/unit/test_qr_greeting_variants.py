"""A short greeting must not hide a booking request in the same message."""
import pytest

from app.qr_behaviors import control_intent
from test_qr_multi_flow import flow


@pytest.mark.parametrize("message", ["Oi", "oiiii", "Oie", "Oiê", "Eai", "E aí", "Eae", "Opa!",
    "Bom dia! Tudo bem?", "Olá, tudo bom?", "Oi td bem?", "Boa noite, beleza?"])
async def test_natural_greeting_is_short_and_does_not_call_model_or_book(flow, message):
    result = await flow.turn(message, preview=False)
    assert result["step"] == "greeting"
    assert len(result["answer"]) <= 240 and "R$" not in result["answer"]
    assert not flow.calls["plans"] and not flow.calls["writes"]


@pytest.mark.parametrize("message", ["Oi, quero agendar corte amanhã", "Bom dia, tem horário hoje?",
    "Eai, quanto custa a barba?", "Olá, tudo bem? Quero cancelar meu horário", "Oi, posso mudar para sexta?"])
def test_a_request_after_greeting_remains_a_request(message):
    assert control_intent(message) != "greeting"
