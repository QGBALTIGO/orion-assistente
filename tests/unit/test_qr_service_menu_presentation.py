"""Catalog alternatives keep their facts and order through the final renderer."""
from copy import deepcopy
from itertools import product

import pytest

from app.qr_presentation import (
    STYLE_LABELS, fits_message, render_message, render_prompt, render_service_menu,
)


CATALOG = [
    {"id": "cut", "name": "Corte de cabelo", "price_cents": 3500, "duration_min": 40},
    {"id": "beard", "name": "Barba", "price_cents": 2000, "duration_min": 20},
]


@pytest.mark.parametrize("style,emoji,length", list(product(STYLE_LABELS, ("none", "light"), ("short", "balanced", "detailed"))))
def test_menu_keeps_individual_facts_and_single_question_in_every_profile(style, emoji, length):
    prefs = {"style": style, "emoji": emoji, "length": length}
    answer = render_service_menu("Empresa cadastrada", CATALOG, prefs, greeting="Olá! ")
    assert answer.startswith("Olá! Bem-vindo à *Empresa cadastrada*!")
    assert "*Corte de cabelo* — *R$ 35,00*\n" in answer
    assert "*Barba* — *R$ 20,00*\n" in answer
    assert "*Duração:* 40 minutos" in answer and "*Duração:* 20 minutos" in answer
    assert answer.index("Corte de cabelo") < answer.index("Barba")
    assert answer.endswith(render_prompt("service", prefs)) and answer.count("?") == 1
    assert "R$ 55" not in answer and "Total:" not in answer and "1 hora" not in answer
    assert not any(claim in answer for claim in ("confirmado", "Protocolo:", "horários disponíveis", "últimas vagas"))
    assert fits_message(answer)
    # The central legacy boundary must not strip, duplicate or restyle this menu.
    assert render_message(answer, prefs, kind="menu") == answer
    assert render_message(answer, prefs, kind="service") == answer
    if emoji == "none":
        assert not any(icon in answer for icon in ("✂", "🪒", "📋", "⏳", "💰", "😊"))
    else:
        assert "✂️ *Corte de cabelo*" in answer and "🪒 *Barba*" in answer
        assert answer.count("⏳ *Duração:*") == 2


def test_menu_preserves_first_twelve_ordinals_without_sorting_or_mutating_catalog():
    catalog = [{"id": str(i), "name": f"Serviço {13-i}", "price_cents": i * 100, "duration_min": i + 1}
               for i in range(13)]
    before = deepcopy(catalog)
    answer = render_service_menu("Empresa", catalog, {"emoji": "light", "length": "short"})
    positions = [answer.index(f"*Serviço {13-i}*") for i in range(12)]
    assert positions == sorted(positions)
    assert "*Serviço 1*" not in answer
    assert answer.count("*Duração:*") == 12
    assert "R$ 0,00" in answer and "R$ 11,00" in answer
    assert catalog == before
    assert fits_message(answer)


@pytest.mark.parametrize("service,icon", [
    ("Limpeza dental", "🦷"), ("Consulta veterinária", "🐾"),
    ("Manicure", "💅"), ("Projeto de interiores", "📋"),
    ("Corte", "📋"),  # The ambiguous name alone does not establish a barber niche.
])
def test_menu_uses_factual_niche_or_neutral_icon(service, icon):
    answer = render_service_menu("Outra empresa", [{"name": service, "price_cents": 18000, "duration_min": 90}],
                                 {"emoji": "light"})
    assert f"{icon} *{service}* — *R$ 180,00*" in answer
    assert "⏳ *Duração:* 1 hora e 30 minutos" in answer
    assert "✂" not in answer


def test_menu_keeps_original_demo_and_custom_names_without_interpreting_metadata():
    catalog = [{"name": "Plano _Especial_ *2026*", "price_cents": 0, "duration_min": 1}]
    answer = render_service_menu("Clínica *Ana* — DEMONSTRAÇÃO FICTÍCIA", catalog,
                                 greeting=r"Olá, \*Ana\*! ")
    assert r"*Clínica \*Ana\* — DEMONSTRAÇÃO FICTÍCIA*" in answer
    assert r"*Plano \_Especial\_ \*2026\** — *R$ 0,00*" in answer
    assert r"Olá, \*Ana\*!" in answer  # No double escaping of the caller-owned greeting.
    assert "*Duração:* 1 minuto" in answer
    assert "Simulação concluída" not in answer and "Protocolo" not in answer


def test_avoid_rules_change_optional_heading_not_facts_or_service_question():
    prefs = {"avoid_phrases": ["Conheça", "Barba", "R$", "Duração"]}
    answer = render_service_menu("Empresa", CATALOG, prefs)
    assert "Conheça" not in answer
    assert "Barba" in answer and "R$ 20,00" in answer and "*Duração:* 20 minutos" in answer
    assert answer.endswith(render_prompt("service", prefs))


def test_styles_change_only_optional_heading_and_not_catalog_values():
    answers = [render_service_menu("Empresa", CATALOG, {"style": style}) for style in STYLE_LABELS]
    assert len(set(answers)) == 4
    for answer in answers:
        assert "R$ 35,00" in answer and "R$ 20,00" in answer
        assert answer.count("*Duração:*") == 2


def test_same_named_alternatives_are_not_merged_or_totalled():
    catalog = [{"name": "Consulta", "price_cents": price, "duration_min": minutes}
               for price, minutes in ((10000, 30), (15000, 60))]
    answer = render_service_menu("Empresa", catalog)
    assert answer.count("*Consulta*") == 2
    assert answer.index("R$ 100,00") < answer.index("R$ 150,00")
    assert "R$ 250,00" not in answer and "Total:" not in answer


def test_empty_catalog_does_not_invent_a_service_price_or_bookable_slot():
    answer = render_service_menu("Empresa", [])
    assert "Não há serviços no catálogo para exibir." in answer
    assert answer.endswith("Como posso ajudar?") and answer.count("?") == 1
    assert "R$" not in answer and "Duração:" not in answer


def test_degraded_greeting_is_preserved_once_without_changing_catalog_facts():
    greeting = "A IA está temporariamente indisponível; vou usar o menu. "
    answer = render_service_menu("Empresa", CATALOG, greeting=greeting)
    assert answer.count(greeting.strip()) == 1
    assert "R$ 35,00" in answer and answer.count("?") == 1


@pytest.mark.parametrize("changes", [
    {"price_cents": -1}, {"price_cents": None}, {"price_cents": True},
    {"duration_min": 0}, {"duration_min": None}, {"duration_min": "40"},
])
def test_invalid_facts_are_rejected_instead_of_guessed(changes):
    with pytest.raises(ValueError):
        render_service_menu("Empresa", [dict(CATALOG[0], **changes)])


def test_long_registered_names_are_preserved_for_existing_final_size_boundary():
    catalog = [{"name": "Serviço " + chr(65+i) * 110, "price_cents": 100, "duration_min": 15}
               for i in range(12)]
    answer = render_service_menu("Empresa", catalog)
    assert not fits_message(answer)
    assert all(item["name"] in answer for item in catalog)
    assert answer.count("*Duração:*") == 12
