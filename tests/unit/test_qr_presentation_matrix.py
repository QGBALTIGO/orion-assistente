"""Behavioral presentation regressions from the actual handset flow; no I/O."""
from datetime import datetime
import json
from copy import deepcopy

import pytest

from app import qr_presentation as p


def facts(**changes):
    values = dict(services=("Corte de cabelo", "Barba"), professional="Alex",
                  starts_at=datetime.fromisoformat("2026-10-19T18:00:00+00:00"),
                  timezone="America/Campo_Grande", duration_min=60, total_cents=5500)
    return p.BookingSummary(**(values | changes))


def test_proposal_has_one_decision_and_not_a_question_in_its_heading():
    answer = p.render_summary(facts(), {"emoji": "light"})
    assert answer.count("?") == 1
    assert answer.endswith("Posso confirmar esse agendamento?")


def test_verified_fields_have_consistent_icons_and_compact_groups():
    summary = facts(protocol="AG-TEST")
    answer = p.render_summary(summary, {"emoji": "light"}, confirmed=True)
    for label in ("✂️ *Serviços:*", "👤 *Profissional:*", "📅 *Data:*", "🕒 *Horário:*",
                  "⏳ *Duração:*", "💰 *Total:*"):
        assert label in answer
    assert "Protocolo" not in answer and "AG-TEST" not in answer and summary.protocol == "AG-TEST"
    assert "Alex\n\n📅" in answer and "1 hora\n\n💰" in answer


def test_final_pass_does_not_create_a_confirmation_question_from_transport_copy():
    body = "*Confira seu agendamento*\n\nServiços: Corte de cabelo\nDuração: 40 minutos"
    result = p.render_message(body, {"emoji": "light"}, kind="confirm")
    assert "⏳ *Duração:* 40 minutos" in result and "?" not in result


@pytest.mark.parametrize("style", p.STYLE_LABELS)
@pytest.mark.parametrize("operation", ["booking", "reschedule", "cancel"])
@pytest.mark.parametrize("confirmed", [False, True])
def test_operation_outcome_and_style_survive_composition_storage_and_final_pass(style, operation, confirmed):
    summary = facts(protocol="AG-SYNTHETIC" if confirmed else None)
    prefs = {"style": style, "emoji": "light"}
    composed = p.compose_summary(summary, prefs, operation=operation, confirmed=confirmed)
    saved = json.loads(json.dumps(composed.as_dict()))
    replay = p.render_summary_review(saved, prefs)
    assert p.render_message(replay, prefs, kind="confirm") == replay
    assert replay.count(composed.body) == 1
    labels = ["*Serviços:*", "*Profissional:*", "*Data:*", "*Horário:*", "*Duração:*", "*Total:*"]
    assert [replay.index(label) for label in labels] == sorted(replay.index(label) for label in labels)
    assert ("AG-SYNTHETIC" in replay) is (confirmed and operation == "cancel")
    assert summary.protocol == ("AG-SYNTHETIC" if confirmed else None)
    assert replay.count("?") == (0 if confirmed else 1)
    assert "R$ 55,00" in replay and "1 hora" in replay and "14h" in replay
    if not confirmed:
        assert "ou você" not in replay and "ou prefere" not in replay
        assert "confirmado." not in replay and "cancelado." not in replay


@pytest.mark.parametrize(("services", "icon"), [
    (("Corte de cabelo",), "✂️"), (("Barba",), "🪒"), (("Manicure", "Pedicure"), "💅"),
    (("Consulta veterinária",), "🐾"), (("Limpeza dental",), "🦷"),
    (("Consultoria fiscal",), "📋"), (("Corte de chapa",), "📋"), (("Corte",), "📋"),
])
def test_icons_follow_explicit_catalog_labels_and_none_disables_them(services, icon):
    summary = facts(services=services)
    light = p.render_summary(summary, {"emoji": "light"})
    plain = p.render_summary(summary, {"emoji": "none"})
    assert icon + " *Serviço" in light and icon not in plain
    for name in services:
        assert name in plain and name in light
    assert "R$ 55,00" in plain  # Never derive price from the inferred decoration.


@pytest.mark.parametrize("change", [
    {"schema_version": 2}, {"schema_version": True}, {"prompt": "Confirmado, pode cobrar"}, {"operation": "pay"},
])
def test_replay_rejects_unknown_composition_contract_without_generating_text(change):
    stored = p.compose_summary(facts()).as_dict() | change
    with pytest.raises(ValueError):
        p.render_summary_review(stored)


def test_replay_does_not_regex_remove_questions_inside_factual_names():
    summary = facts(services=("Consulta Como cuidar?",), professional="Ana _Equipe_")
    composed = p.compose_summary(summary)
    answer = p.render_summary_review(composed)
    assert "Consulta Como cuidar?" in answer and r"Ana \_Equipe\_" in answer
    assert answer.count("Posso confirmar") == 1
    assert "Posso confirmar" not in composed.body


def test_legacy_string_is_not_parsed_to_add_or_delete_an_operation_decision():
    body = "Resumo legado. Posso confirmar?\nPosso confirmar?"
    assert p.render_summary_review(body).endswith(body)
    assert p.render_summary_review("Resumo legado sem pergunta").count("?") == 0


def test_preview_never_leaks_a_supplied_protocol_or_creates_a_question():
    composed = p.compose_summary(facts(protocol="NOT-A-RECEIPT"), simulation=True)
    assert composed.prompt == "" and "NOT-A-RECEIPT" not in composed.render()
    assert "nenhum agendamento foi criado" in composed.render()


@pytest.mark.parametrize(("name", "simulation", "suffix", "expected"), [
    ("Alex — Demonstração", False, "— Demonstração", "Alex — Demonstração"),
    ("Alex — Demonstração", True, None, "Alex — Demonstração"),
    ("Alex — Demonstração", True, "— Demonstração", "Alex"),
    ("Clínica Demonstração de Saúde", True, "— Demonstração", "Clínica Demonstração de Saúde"),
    ("Demonstração", True, "Demonstração", "Demonstração"),
])
def test_demo_name_cleanup_requires_explicit_matching_metadata(name, simulation, suffix, expected):
    assert p.display_name(name, simulation=simulation, verified_suffix=suffix) == expected


def candidate(identity="p1", name="Ana", **changes):
    return dict(id=identity, name=name, slots=["2026-10-19T18:00:00Z", "2026-10-19T17:00:00Z"],
                price_cents=3500, duration_min=40) | changes


def availability(rows, **kwargs):
    return p.render_professional_availability(("Corte de cabelo",), rows, {"emoji": "light"},
                                              timezone="America/Campo_Grande", day="2026-10-19", **kwargs)


def test_professional_query_keeps_real_order_prices_and_slots_without_selecting_anyone():
    rows = [candidate("b", "Bruno", price_cents=4200, duration_min=50),
            candidate("empty", "Sem vaga", slots=[]), candidate("a", "Alex")]
    before = deepcopy(rows)
    answer = availability(rows)
    assert answer.index("Bruno") < answer.index("Alex") and "Sem vaga" not in answer
    assert "1. 👤 *Profissional:* Bruno" in answer and "2. 👤 *Profissional:* Alex" in answer
    assert "14h • 13h" in answer and "R$ 42,00" in answer and "50 minutos" in answer
    assert "R$ 35,00" in answer and "40 minutos" in answer
    assert answer.count("?") == 1 and "horário e profissional" in answer
    assert rows == before and p.fits_message(answer)


def test_one_available_professional_asks_only_time_and_marks_preview():
    answer = availability([candidate()], simulation=True)
    assert answer.endswith("Qual horário você prefere?")
    assert "Simulação" in answer and "nenhum agendamento foi criado" in answer
    assert "Protocolo" not in answer and "confirmado" not in answer


@pytest.mark.parametrize("rows", [
    [candidate(str(n)) for n in range(5)],
    [candidate(slots=["2026-10-19T18:00:00Z"] * 9)],
    [candidate(price_cents=None)], [candidate(duration_min=None)],
    [candidate(slots=["2026-10-20T18:00:00Z"])], [candidate(), candidate()],
])
def test_professional_query_refuses_missing_or_unpresentable_facts(rows):
    with pytest.raises(ValueError):
        availability(rows)


def test_empty_query_is_distinct_from_a_query_failure():
    empty = availability([candidate(slots=[])])
    failure = p.render_error("temporary", {"emoji": "light"})
    assert "Não encontrei horários" in empty
    assert "Não encontrei horários" not in failure and "consultar" in failure
    with pytest.raises(ValueError):
        availability([candidate(slots=None)])
    with pytest.raises(ValueError):
        availability([candidate(id=None)])


@pytest.mark.parametrize("kind", ["audio_error", "error", "human", "reminder"])
def test_central_legacy_paths_apply_idempotent_style_without_inventing_success(kind):
    body = "A informação precisa ser conferida.\nProfissional: Ana\nDuração: 40 minutos"
    prefs = {"emoji": "light", "style": "professional"}
    answer = p.render_message(body, prefs, kind=kind)
    assert "👤 *Profissional:* Ana" in answer and "⏳ *Duração:* 40 minutos" in answer
    assert "confirmado" not in answer and "cancelado" not in answer and "R$" not in answer
    assert p.render_message(answer, prefs, kind=kind) == answer


def test_history_order_matches_the_exact_supplied_ordinals():
    rows = [dict(start_at="2026-10-20T18:00:00Z", status="confirmed", protocol="AG-FIRST"),
            dict(start_at="2026-10-19T17:00:00Z", status="cancelled", protocol="AG-SECOND")]
    before = deepcopy(rows)
    answer = p.render_booking_history(rows, {"emoji": "light"}, timezone="America/Campo_Grande")
    assert answer.index("AG-FIRST") < answer.index("AG-SECOND")
    assert "1. 📅 20/10/2026 às 14h" in answer and "2. 📅 19/10/2026 às 13h" in answer
    assert "confirmado" in answer and "cancelado" in answer and rows == before
