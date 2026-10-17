"""A pending wish remains distinct from a checked, confirmable booking."""
from copy import deepcopy
from itertools import product

import pytest

from app.qr_presentation import (
    STYLE_LABELS, fits_message, render_draft_request, render_message,
)


QUESTION = "Posso verificar e mostrar um novo resumo?"
NOTICE = "_Ainda preciso consultar a disponibilidade na agenda._"


@pytest.mark.parametrize("style,emoji", list(product(STYLE_LABELS, ("none", "light"))))
def test_corrected_draft_keeps_desired_facts_grouped_without_claiming_a_checked_slot(style, emoji):
    prefs = {"style": style, "emoji": emoji}
    answer = render_draft_request(["Corte"], prefs, professional="Bruno — Demonstração",
                                  desired_day="20/10/2026", desired_time="15h30")
    groups = answer.split("\n\n")
    assert groups[0] == "*Pedido em preparação — ainda sem reserva*"
    assert "*Serviço:* Corte" in groups[1] and "*Profissional:* Bruno — Demonstração" in groups[1]
    assert "*Data desejada:* 20/10/2026" in groups[2] and "*Horário desejado:* 15h30" in groups[2]
    assert groups[3:] == [NOTICE, QUESTION]
    assert answer.count(QUESTION) == 1
    assert not any(value in answer for value in ("Protocolo:", "Total:", "Duração:", "R$", "confirmado", "disponível às"))
    assert fits_message(answer)
    assert render_message(answer, prefs, kind="time") == answer
    if emoji == "light":
        assert "📋 *Serviço:*" in answer and "👤 *Profissional:*" in answer
        assert "📅 *Data desejada:*" in answer and "🕒 *Horário desejado:*" in answer
    else:
        assert not any(marker in answer for marker in ("📋", "👤", "📅", "🕒", "✂", "🪒"))


@pytest.mark.parametrize("clock", ["15h30", "depois de 15h30", "antes de 15h30", "por volta de 15h30"])
def test_clock_relation_is_preserved_as_a_wish_not_changed_to_an_exact_verified_time(clock):
    answer = render_draft_request(["Barba"], desired_day="terça-feira, 20/10/2026", desired_time=clock)
    assert "*Horário desejado:* " + clock in answer
    assert "*Data desejada:* terça-feira, 20/10/2026" in answer
    assert "*Horário:*" not in answer and "*Data:*" not in answer
    assert NOTICE in answer and answer.endswith(QUESTION)


@pytest.mark.parametrize("kwargs,labels", [
    ({}, []),
    ({"professional": "Bruno"}, ["Profissional"]),
    ({"desired_day": "20/10/2026"}, ["Data desejada"]),
    ({"desired_time": "depois de 15h"}, ["Horário desejado"]),
])
def test_incomplete_draft_only_displays_facts_supplied_by_core(kwargs, labels):
    answer = render_draft_request([], **kwargs, ask_review=False)
    for label in ("Serviço", "Profissional", "Data desejada", "Horário desejado"):
        assert (f"*{label}:*" in answer) == (label in labels)
    assert answer.endswith(NOTICE) and "?" not in answer
    assert "\n\n\n" not in answer and "R$" not in answer


def test_core_owns_question_when_missing_details_need_clarification():
    draft = render_draft_request(["Corte"], desired_day="20/10/2026", ask_review=False)
    final = draft + "\n\nQual horário você quer consultar?"
    assert QUESTION not in final and final.count("?") == 1
    assert NOTICE in final and "Posso confirmar" not in final


def test_services_keep_core_order_and_names_without_mutating_the_input():
    services = ["Barba", "Corte de cabelo", "Barba"]
    before = deepcopy(services)
    answer = render_draft_request(services, {"emoji": "light"})
    assert "✂️ *Serviços:* Barba e Corte de cabelo e Barba" in answer
    assert services == before
    assert "R$" not in answer and "Duração" not in answer


@pytest.mark.parametrize("name,icon", [
    ("Barba", "🪒"), ("Manicure", "💅"), ("Consulta veterinária", "🐾"),
    ("Limpeza dental", "🦷"), ("Projeto de interiores", "📋"),
])
def test_draft_uses_known_niche_icons_without_assuming_every_business_is_a_barber(name, icon):
    answer = render_draft_request([name], {"emoji": "light"})
    assert f"{icon} *Serviço:* {name}" in answer
    assert "✂" not in answer


def test_names_and_display_values_escape_whatsapp_markup_and_preserve_demo_labels():
    answer = render_draft_request(["Plano *Especial* <VIP>"],
                                  professional="Bruno _Equipe_ — Demonstração",
                                  desired_day="terça *20/10*", desired_time="por volta de _15h30_")
    assert r"*Serviço:* Plano \*Especial\* <VIP>" in answer
    assert r"*Profissional:* Bruno \_Equipe\_ — Demonstração" in answer
    assert r"*Data desejada:* terça \*20/10\*" in answer
    assert r"*Horário desejado:* por volta de \_15h30\_" in answer
    assert render_message(answer, kind="time") == answer


def test_preescaped_labels_are_not_escaped_twice():
    answer = render_draft_request([r"Plano \*Especial\*"], professional=r"Ana \_Equipe\_")
    assert r"Plano \*Especial\*" in answer and r"Ana \_Equipe\_" in answer
    assert r"\\*" not in answer and r"\\_" not in answer


def test_optional_style_avoidance_cannot_remove_pending_status_or_verification_question():
    prefs = {"avoid_phrases": ["Ainda preciso", "Posso verificar", "sem reserva", "Corte"]}
    answer = render_draft_request(["Corte"], prefs)
    assert "ainda sem reserva" in answer and "*Serviço:* Corte" in answer
    assert NOTICE in answer and answer.endswith(QUESTION)


@pytest.mark.parametrize("kwargs", [
    {"ask_review": 1}, {"ask_review": "yes"}, {"professional": ""},
    {"professional": 123}, {"desired_day": ""}, {"desired_day": 20261020},
    {"desired_time": ""}, {"desired_time": 1530},
])
def test_invalid_display_contracts_fail_instead_of_inventing_facts_or_truthy_consent(kwargs):
    with pytest.raises(ValueError):
        render_draft_request(["Corte"], **kwargs)


@pytest.mark.parametrize("services", ["Corte", [""], [None], ["x"] * 13])
def test_invalid_service_collection_is_rejected(services):
    with pytest.raises(ValueError):
        render_draft_request(services)
