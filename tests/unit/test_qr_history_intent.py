"""Natural own-booking queries use the scoped read path, never a price reply."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app import qr_ai
from app.qr_behaviors import control_intent
from test_qr_ai_interruptions import conversation


@pytest.mark.parametrize("message", [
    "Tenho algum agendamento?", "Eu tenho algum agendamento?",
    "tenho algum agendamento confirmado?", "Eu tenho agendamentos confirmados?",
    "Tenho alguma reserva marcada?", "Eu tenho reserva?", "Tenho algum horário marcado?",
])
def test_positive_whole_question_requests_own_bookings(message):
    assert control_intent(message) == "my_bookings"


@pytest.mark.parametrize("message", [
    "Não tenho algum agendamento?", "Se eu tenho algum agendamento, cancela",
    "Tenho algum agendamento? Quero cancelar", "Tenho algum agendamento para João?",
    "A Maria tem algum agendamento?", "Quanto custa um agendamento?",
    "Quero agendar um corte", "Tenho algum agendamento? Melhor remarcar",
])
def test_other_subjects_and_operations_are_not_taken_by_new_history_shortcut(message):
    assert control_intent(message) != "my_bookings"


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["Tenho algum agendamento?", "Eu tenho algum agendamento?"])
async def test_actual_reported_question_reads_only_authenticated_contact(conversation, monkeypatch, message):
    h = conversation
    calls = []
    async def records(tenant, phone):
        calls.append((tenant, phone))
        return [{"status": "confirmed", "start_at": datetime(2026, 10, 20, 15,
            tzinfo=ZoneInfo("America/Campo_Grande")), "protocol": "SYNTHETIC-OWN"}]
    async def forbidden(*args, **kwargs):
        raise AssertionError("A history question cannot invoke interpretation or a booking operation")
    monkeypatch.setattr(qr_ai, "customer_appointments", records)
    monkeypatch.setattr(qr_ai, "interpret_plan", forbidden)
    monkeypatch.setattr(qr_ai, "interpret", forbidden)
    monkeypatch.setattr(qr_ai, "professionals_for", forbidden)
    result = await h.turn(message, preview=False)
    assert result["step"] == "history" and "SYNTHETIC-OWN" in result["answer"]
    assert "confirmado" in result["answer"].lower() and "15h" in result["answer"]
    assert calls == [(h.tenant, "5567999999999")]
    assert not h.calls["writes"] and not h.calls["slots"]
