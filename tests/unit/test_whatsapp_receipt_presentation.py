"""Regression for the actual WhatsApp screenshot: formatting must survive QR AI."""
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app import qr_ai
from app.appointment_message_templates import compose, branded_signature


def booking():
    return {
        "id": uuid4(),
        "protocol": "AG-G32XU5",
        "customer_name": "Kayky",
        "professional_name": "Guilherme",
        "timezone": "America/Campo_Grande",
        "start_at": datetime(2026, 10, 30, 17, 30, tzinfo=timezone.utc),
        "items": [
            {"name_snapshot": "Sobrancelha na navalha", "duration_snapshot_min": 20},
            {"name_snapshot": "Corte Masculino", "duration_snapshot_min": 40},
        ],
    }


def test_default_signature_includes_real_business_and_custom_signature_is_preserved(monkeypatch):
    monkeypatch.setenv("PANEL_PUBLIC_URL", "https://orionagenda.pro")
    body = compose(
        "confirmation", booking(),
        company="Barbearia Peixoto", signature="Orion Agenda",
        customer_link="https://orionagenda.pro/a#synthetic",
    )
    assert body.endswith("\n\nBarbearia Peixoto • via Orion Agenda")
    assert body.count("Barbearia Peixoto • via Orion Agenda") == 1
    assert branded_signature("Clínica Sorriso", "") == "Clínica Sorriso • via Orion Agenda"
    assert branded_signature("Barbearia Peixoto", "Assinatura personalizada") == "Assinatura personalizada"


@pytest.mark.asyncio
async def test_qr_ai_does_not_reformat_official_confirmation(monkeypatch):
    monkeypatch.setenv("PANEL_PUBLIC_URL", "https://orionagenda.pro")
    expected = compose(
        "confirmation", booking(),
        company="Barbearia Peixoto", signature="Orion Agenda",
        customer_link="https://orionagenda.pro/a#synthetic",
    )

    async def mocked_turn(*args, **kwargs):
        return {
            "answer": expected,
            "step": "appointment_confirmation",
            "_literal_information": True,
            "_verbatim_appointment_template": True,
        }

    class RedisStub:
        async def get(self, key):
            return None

    monkeypatch.setattr(qr_ai, "_turn", mocked_turn)
    result = await qr_ai.turn(uuid4(), "5567999999999", "Protocolo: AG-G32XU5", RedisStub(), "test")
    text = result["answer"]
    assert text == expected
    assert "✂️ Serviço: *Sobrancelha na navalha + Corte Masculino*" in text
    assert "📅 Data: *30/10/2026*" in text
    assert "🕒 Horário: *13:30*" in text
    assert "👤 Profissional: *Guilherme*" in text
    for label in ("Serviço", "Data", "Horário", "Profissional"):
        assert "*"+label+":" not in text
    assert text.endswith("\n\nBarbearia Peixoto • via Orion Agenda")
    assert "_Precisa cancelar ou remarcar? Utilize o link acima com até 1 hora de antecedência._" in text
