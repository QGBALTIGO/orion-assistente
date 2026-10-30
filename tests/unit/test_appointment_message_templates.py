"""Regression coverage for the fifteen user-approved WhatsApp message models."""
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.appointment_messages import (
    TEMPLATES, compose, compose_prefill, generic_response,
    is_generic_response, render_template,
)

LINK = "https://orionagenda.pro/a#customer-capability"
BOOKING = "https://orionagenda.pro/s/barbearia-peixoto"
SIGNATURE = "Barbearia Peixoto • via Orion Agenda"
EVENTS = [
    "confirmation", "professional_new", "professional_cancelled",
    "customer_cancelled", "professional_rescheduled", "customer_rescheduled",
    "customer_proposal", "professional_proposal_declined", "reminder",
    "review", "professional_reassigned", "status",
]


def example(status="confirmed"):
    return {
        "id": uuid4(),
        "customer_name": "Lucas Gabriel",
        "professional_name": "Peixoto",
        "status": status,
        "start_at": datetime(2026, 10, 31, 14, 30, tzinfo=timezone.utc),
        "timezone": "America/Campo_Grande",
        "protocol": "AG-ABCDEF",
        "items": [
            {"name_snapshot": "Corte", "duration_snapshot_min": 40},
            {"name_snapshot": "Barba", "duration_snapshot_min": 25},
        ],
    }


def previous():
    return {
        "start_at": "2026-10-30T14:30:00+00:00",
        "new_start_at": "2026-11-01T14:30:00+00:00",
        "expires_at": "2026-10-30T19:00:00+00:00",
    }


def test_all_fifteen_official_models_are_stored_verbatim():
    assert len(TEMPLATES) == 15
    assert TEMPLATES["public_prefill"] == (
        "*Olá, Orion!* 👋\n\n"
        "Acabei de realizar um agendamento pelo site e gostaria de confirmar meu horário. 😊\n\n"
        "🧾 Protocolo: *{{protocolo}}*\n\n"
        "_Aguardo os detalhes da minha confirmação._"
    )
    assert "📩 *NOVO AGENDAMENTO RECEBIDO!*" in TEMPLATES["professional_new"]
    assert "~{{data_anterior}} às {{horario_anterior}}~" in TEMPLATES["professional_rescheduled"]
    assert "🤖 " not in TEMPLATES["confirmation"]
    assert TEMPLATES["professional_reassigned"].startswith("🔄 *PROFISSIONAL ALTERADO!*")


@pytest.mark.parametrize("event", EVENTS)
def test_real_facts_resolve_without_leaking_placeholders_or_breaking_native_markup(event, monkeypatch):
    monkeypatch.setenv("PANEL_PUBLIC_URL", "https://orionagenda.pro")
    rendered = compose(
        event, example("cancelled" if event == "status" else "confirmed"),
        company="Barbearia Peixoto",
        signature=SIGNATURE,
        customer_link=LINK,
        booking_link=BOOKING,
        address="Rua Exemplo, 123, Aquidauana, MS",
        previous=previous(),
    )
    assert "{{" not in rendered and "}}" not in rendered
    assert "**" not in rendered
    assert rendered.endswith("\n\n" + SIGNATURE)
    assert rendered.count(SIGNATURE) == 1
    assert len(rendered.encode("utf-16-le")) // 2 <= 1200
    assert "✂️ Serviço: *Corte + Barba*" in rendered if event not in ("status","review") else True


def test_native_formatting_emerges_as_literal_transport_text(monkeypatch):
    monkeypatch.setenv("PANEL_PUBLIC_URL", "https://orionagenda.pro")
    rendered = compose(
        "professional_rescheduled", example(), company="Barbearia Peixoto",
        signature=SIGNATURE, previous=previous(),
    )
    assert "~30/10/2026 às 10:30~" in rendered
    assert "_O cliente realizou uma alteração no horário do atendimento._" in rendered
    assert "📅 Data: *31/10/2026*" in rendered
    assert rendered.count("~") == 2
    assert "\n\n✅ *Novo horário confirmado:*\n" in rendered


def test_prefill_and_private_protocol_error_are_safe_and_branded(monkeypatch):
    monkeypatch.setenv("PANEL_PUBLIC_URL", "https://orionagenda.pro")
    assert compose_prefill("AG-ABCDEF") == TEMPLATES["public_prefill"].replace(
        "{{protocolo}}", "AG-ABCDEF"
    )
    invalid = generic_response(SIGNATURE)
    assert is_generic_response(invalid)
    assert invalid.endswith(SIGNATURE)
    assert "AG-ABCDEF" not in invalid
    assert "{{" not in invalid


def test_return_invitation_is_embedded_once_and_not_an_independent_notification(monkeypatch):
    monkeypatch.setenv("PANEL_PUBLIC_URL", "https://orionagenda.pro")
    base = dict(company="Barbearia Peixoto", signature=SIGNATURE,
                customer_link=LINK, booking_link=BOOKING)
    with_return = compose("review", example("completed"), include_return=True, **base)
    without_return = compose("review", example("completed"), include_return=False, **base)
    assert with_return.count("✂️ *Que tal garantir seu próximo horário?*") == 1
    assert without_return.count("✂️ *Que tal garantir seu próximo horário?*") == 0
    assert with_return.count(SIGNATURE) == 1
    assert with_return.index("✂️ *Que tal garantir seu próximo horário?*") < with_return.index(SIGNATURE)


def test_optional_unavailable_link_is_removed_without_orphan_placeholder(monkeypatch):
    monkeypatch.setenv("PANEL_PUBLIC_URL", "https://orionagenda.pro")
    rendered = compose("confirmation", example(), company="Barbearia Peixoto",
                       customer_link=None, signature=SIGNATURE)
    assert "{{" not in rendered
    assert "🔗 *Gerencie seu agendamento:*" not in rendered
    assert "Utilize o link acima" not in rendered


def test_untrusted_names_cannot_inject_links_or_break_whatsapp_markup(monkeypatch):
    monkeypatch.setenv("PANEL_PUBLIC_URL", "https://orionagenda.pro")
    row = example()
    row["customer_name"] = "*Lucas* _Gabriel_ https://evil.example/"
    row["professional_name"] = "~Peixoto~"
    row["items"][0]["name_snapshot"] = "Corte *premium*"
    rendered = compose("professional_new", row, company="Barbearia Peixoto", signature=SIGNATURE)
    assert "https://evil.example/" not in rendered
    assert "Cliente: *Lucas Gabriel [link removido]*" in rendered
    assert "Serviço: *Corte premium + Barba*" in rendered
    assert rendered.count("~") == 0


def test_unknown_variables_fail_closed():
    with pytest.raises(ValueError, match="missing_appointment_message_fact"):
        render_template("confirmation", {"primeiro_nome_cliente": "Lucas"})
