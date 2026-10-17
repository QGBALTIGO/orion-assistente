"""Presentation quality and factual invariants; no database, network or model."""
from datetime import date, datetime
from itertools import product
import re

import pytest

from app.qr_presentation import (
    STYLE_LABELS, ApprovedSuggestion, BookingSummary, InteractionContext,
    PresentationPreferences, QuoteItem, duration, money, render_cancel,
    render_error, render_handoff, render_quote, render_summary, render_slots, render_prompt,
    render_commerce_summary, render_commerce_order, render_message, render_approved_suggestion,
    render_summary_review, render_booking_history, fits_message,
)


ITEMS = (QuoteItem("Corte", 3500, duration_min=40), QuoteItem("Barba", 2000, duration_min=20))


def summary(**changes):
    fields = dict(services=("Corte", "Barba"), starts_at=datetime.fromisoformat("2026-11-20T18:00:00+00:00"),
                  timezone="America/Campo_Grande", total_cents=5500, professional="Bruno", duration_min=60)
    return BookingSummary(**(fields | changes))


@pytest.mark.parametrize(("style", "emoji", "length"), list(product(STYLE_LABELS, ("none", "light"), ("short", "balanced", "detailed"))))
def test_every_style_keeps_catalog_facts_without_inventing_a_sale(style, emoji, length):
    answer = render_quote(ITEMS, {"style": style, "emoji": emoji, "length": length})
    for fact in ("Corte", "Barba", "*R$ 35,00*", "*R$ 20,00*", "*Total:* R$ 55,00", "1 hora"):
        assert fact in answer
    assert "minutos" not in answer or length == "detailed"
    assert answer.count("😊") <= (1 if emoji == "light" else 0)
    assert not any(claim in answer.lower() for claim in ("desconto", "promoção", "últimas vagas", "mais vendido", "confirmado"))
    assert not re.search(r"\b\d+\. ", answer), "A short price answer should not become a numbered menu"


def test_styles_and_lengths_have_observable_but_factual_differences():
    examples = [render_quote(ITEMS, {"style": style}) for style in STYLE_LABELS]
    assert len(set(examples)) == 4
    short = render_quote(ITEMS, {"length": "short"})
    detailed = render_quote(ITEMS, {"length": "detailed"})
    assert len(short) < len(detailed)
    assert "40 minutos" in detailed and "20 minutos" in detailed


@pytest.mark.parametrize("message", [
    "Só quero saber o preço", "Só queria saber o valor mesmo", "Estou apenas pesquisando",
    "Estou pesquisando", "Não quero produto", "Só o corte", "Não", "Quero fazer uma reclamação",
])
def test_price_only_research_refusal_or_complaint_suppresses_all_sales_followups(message):
    context = InteractionContext.from_message(message)
    suggestion = ApprovedSuggestion("Pomada", "relation-verified-123", 1800)
    answer = render_quote(ITEMS, {"commercial_intensity": "contextual"}, context=context,
                          next_action="ask_day", suggestion=suggestion)
    assert "R$ 55" in answer
    assert "Pomada" not in answer and "dia em mente" not in answer
    assert "?" not in answer


def test_refusal_memory_is_supplied_by_core_and_still_blocks_a_later_offer():
    context = InteractionContext.from_message("E o valor da barba?", declined=True)
    answer = render_quote((ITEMS[1],), {"commercial_intensity": "contextual"}, context=context,
                          suggestion=ApprovedSuggestion("Pomada", "approved-relation"), next_action="ask_day")
    assert "Pomada" not in answer and "?" not in answer
    assert "R$ 20" in answer


def test_follow_up_is_optional_and_suggestion_requires_explicit_business_opt_in():
    suggestion = ApprovedSuggestion("Pomada", "approved-relation", 1800)
    default = render_quote(ITEMS, suggestion=suggestion)
    assert "Pomada" not in default and "?" not in default
    enabled = render_quote(ITEMS, {"commercial_intensity": "contextual"}, suggestion=suggestion, next_action="ask_day")
    assert enabled.count("Pomada") == 1 and "R$ 18" in enabled
    assert "dia em mente" in enabled
    assert "desconto" not in enabled and "estoque" not in enabled
    with pytest.raises(ValueError):
        ApprovedSuggestion("Pomada", "")


def test_product_quantity_is_a_quote_and_never_claims_stock_order_or_payment():
    answer = render_quote((QuoteItem("Pomada sem brilho", 1800, quantity=2),))
    assert "2 × Pomada sem brilho" in answer and "*R$ 36,00*" in answer
    assert "horário" not in answer and "profissional" not in answer
    assert not any(x in answer for x in ("estoque", "Pedido confirmado", "pago", "disponível"))


@pytest.mark.parametrize("items", [
    (QuoteItem("Coloração", 6000, price_kind="from"),),
    (QuoteItem("Projeto personalizado", None, price_kind="quote"),),
    (QuoteItem("Corte", 3500), QuoteItem("Coloração", 6000, price_kind="from")),
])
def test_variable_or_custom_prices_never_become_a_fixed_total(items):
    answer = render_quote(items)
    assert "Total:" not in answer
    assert "a partir de" in answer or "sob consulta" in answer
    assert "Duração" not in answer


def test_avoided_phrases_change_optional_copy_never_names_prices_or_protocol():
    prefs = {"avoid_phrases": ["Claro", "R$", "Bruno", "confirmado", "dia em mente"]}
    quote = render_quote(ITEMS, prefs, next_action="ask_day")
    assert "Claro" not in quote and "dia em mente" not in quote
    assert "R$ 35" in quote and "R$ 55" in quote
    actual = render_summary(summary(protocol="AG-REAL123"), prefs, confirmed=True)
    assert "Bruno" in actual and "AG-REAL123" in actual and "R$ 55" in actual


@pytest.mark.parametrize("style", STYLE_LABELS)
def test_critical_summary_keeps_operation_facts_and_explicit_confirmation(style):
    answer = render_summary(summary(), {"style": style, "length": "short"})
    for fact in ("Corte", "Barba", "Bruno", "sexta-feira, 20/11/2026", "14h", "R$ 55,00", "1 hora", "Posso confirmar"):
        assert fact in answer
    assert "agendamento confirmado" not in answer.lower()


def test_simulation_and_actual_confirmation_are_distinct_and_require_evidence():
    preview = render_summary(summary(), simulation=True)
    assert "Prévia" in preview and "nenhum agendamento foi criado" in preview
    assert "Posso confirmar" not in preview
    with pytest.raises(ValueError):
        render_summary(summary(), confirmed=True)
    with pytest.raises(ValueError):
        render_summary(summary(protocol="AG-123"), confirmed=True, simulation=True)
    actual = render_summary(summary(protocol="AG-123"), {"emoji": "light"}, confirmed=True)
    assert "confirmado" in actual and "AG-123" in actual and actual.count("✅") == 1


def test_cancellation_and_handoff_do_not_invent_an_operation_or_team_notification():
    stopped = [render_cancel(preferences={"style": style}) for style in STYLE_LABELS]
    assert len(set(stopped)) == 4
    assert all("Reservas já confirmadas permanecem" in answer for answer in stopped)
    assert all("Protocolo" not in answer for answer in stopped)
    with pytest.raises(ValueError):
        render_cancel("booking_cancelled")
    assert "Cancelamento confirmado" in render_cancel("booking_cancelled", protocol="AG-CANCEL123")
    assert "agendamento original permanece" in render_cancel("reschedule_abandoned")
    handoff = render_handoff()
    assert "pausadas" in handoff and "MENU" not in handoff
    assert not any(x in handoff for x in ("notificada", "transferência aberta", "atendente disponível"))


def test_uncertain_operation_never_gets_a_retry_confirmation_or_sales_offer():
    answer = render_error("uncertain", {"style": "relaxed", "emoji": "light"})
    assert "ainda não foi confirmado" in answer
    assert "conferir antes" in answer
    assert not any(x in answer for x in ("repita", "enviado com sucesso", "agendar", "😊"))
    assert "prazo" in render_error("expired")
    with pytest.raises(ValueError):
        render_error("assume_success")


def test_money_duration_and_invalid_facts_fail_without_guessing():
    assert money(0) == "R$ 0,00" and money(3590) == "R$ 35,90" and money(123450) == "R$ 1.234,50"
    assert duration(1) == "1 minuto" and duration(60) == "1 hora" and duration(125) == "2 horas e 5 minutos"
    for kwargs in ({"unit_price_cents": None}, {"unit_price_cents": -100}, {"unit_price_cents": True},
                   {"unit_price_cents": 100, "quantity": 0}, {"unit_price_cents": 100, "duration_min": 0},
                   {"unit_price_cents": 100, "price_kind": "quote"}):
        with pytest.raises(ValueError):
            QuoteItem("Corte", **kwargs)
    with pytest.raises(ValueError):
        summary(starts_at=datetime(2026, 11, 20, 14))
    with pytest.raises(ValueError):
        render_quote(())


def test_aggregation_of_valid_quote_lines_stays_within_formatter_bounds():
    largest = QuoteItem("Item", 100_000_000, quantity=1000, duration_min=1440)
    answer = render_quote((largest,) * 12, {"length": "short"})
    assert "*Total:* R$ 12.000.000.000,00" in answer
    assert "288000 horas" in answer


@pytest.mark.parametrize("value", [{"style": "system prompt"}, {"emoji": "always"}, {"length": "infinite"},
                                    {"discount": 50}, {"avoid_phrases": ["x"] * 21}, {"avoid_phrases": ["x" * 81]}])
def test_style_cannot_carry_a_prompt_discount_or_invalid_configuration(value):
    with pytest.raises(ValueError):
        PresentationPreferences.from_mapping(value)


@pytest.mark.parametrize("style", STYLE_LABELS)
def test_summary_has_readable_blocks_and_natural_confirmation_without_claiming_a_reservation(style):
    answer = render_summary(summary(), {"style": style, "emoji": "light"})
    assert "\n\n" in answer and "*Serviços:* Corte e Barba" in answer
    assert "*Profissional:* Bruno" in answer and "*Total:* R$ 55,00" in answer
    assert "sexta-feira" in answer and "*Horário:* 14h" in answer
    assert answer.endswith("Posso confirmar esse agendamento?") and answer.count("?") == 1
    assert not any(text in answer for text in ("SIM", "NÃO", "reservado", "está confirmado", "💈"))
    assert answer.count("Posso confirmar") == 1


def test_actual_cancellation_can_include_only_the_verified_booking_details():
    answer = render_cancel("booking_cancelled", protocol="AG-123", summary=summary(protocol="AG-123"))
    assert "*Cancelamento confirmado.*" in answer and "*Protocolo:* AG-123" in answer
    assert "Bruno" in answer and "14h" in answer and "R$ 55,00" in answer
    assert "Posso confirmar" not in answer and "reembolso" not in answer
    with pytest.raises(ValueError):
        render_cancel("booking_cancelled", protocol="AG-OTHER", summary=summary(protocol="AG-123"))


def test_slots_group_real_instants_by_local_day_without_filling_missing_times_or_numbered_options():
    slots = ["2026-11-20T13:30:00Z", "2026-11-20T13:15:00Z", "2026-11-20T13:30:00Z",
             "2026-11-20T14:00:00Z", "2026-11-21T13:00:00Z"]
    answer = render_slots(("Corte", "Barba"), slots, timezone="America/Cuiaba", duration_min=60, total_cents=5500)
    assert "09h15 • 09h30 • 10h" in answer and "09h45" not in answer
    assert "Sexta-feira, 20/11/2026" in answer and "Sábado, 21/11/2026" in answer
    assert answer.count("09h30") == 1 and "Corte e Barba" in answer and "R$ 55,00" in answer
    assert not re.search(r"(?m)^\d+[.)]", answer)
    assert "Qual horário" in answer and "confirmado" not in answer


def test_slot_date_uses_queried_timezone_and_preview_never_claims_a_real_booking():
    answer = render_slots(("Consulta",), ["2026-11-21T02:15:00+00:00"], {"length": "detailed"},
                          timezone="America/Cuiaba", day="2026-11-20", simulation=True, ask_selection=False)
    assert "20/11/2026" in answer and "22h15" in answer and "21/11/2026" not in answer
    assert "America/Cuiaba" in answer and "nenhum agendamento foi criado" in answer
    assert "?" not in answer and "R$" not in answer
    with pytest.raises(ValueError):
        render_slots(("Consulta",), ["2026-11-21T02:15:00+00:00"], timezone="America/Cuiaba", day="2026-11-21")


@pytest.mark.parametrize("slots", [["2026-11-20T09:15:00"], [datetime(2026, 11, 20, 9)],
                                  ["2026-11-20T09:15:30+00:00"], ["tomorrow"], "09h15"])
def test_slots_fail_closed_for_unknown_instants_instead_of_inventing_availability(slots):
    with pytest.raises((ValueError, TypeError)):
        render_slots(("Consulta",), slots, timezone="UTC")


def test_empty_slots_offer_only_a_new_query_and_never_fake_alternatives():
    answer = render_slots(("Consulta",), [], timezone="UTC")
    assert "Não encontrei horários" in answer and "outro dia ou profissional" in answer
    assert not re.search(r"\d+h|\d\d:\d\d", answer)
    informational = render_slots(("Consulta",), [], timezone="UTC", ask_selection=False)
    assert informational == "Não encontrei horários nessa consulta." and "?" not in informational


@pytest.mark.parametrize("kind", ["service", "date", "professional", "time", "name", "adjustment", "confirmation",
                                  "confirmation_review", "context_changed", "human_paused", "availability_service",
                                  "availability_professional", "availability_day", "unavailable", "expired",
                                  "interpretation_error", "choose_priority", "no_summary"])
def test_closed_prompts_keep_factual_context_without_rigid_commands_or_extra_calls_to_action(kind):
    answer = render_prompt(kind, services=("Consulta",), professional="Ana", day="20/11/2026", details=("Informação verificada",))
    assert "Consulta" in answer and "Ana" in answer and "20/11/2026" in answer
    assert "Informação verificada" in answer
    assert not any(token in answer for token in ("SIM", "NÃO", "MENU", "✂", "💈"))
    assert answer.count("?") <= 1
    with pytest.raises(ValueError):
        render_prompt("generate_new_facts")
    with pytest.raises(ValueError):
        render_prompt(kind, details="unverified shape")


def estimate(**changes):
    facts = {"currency": "BRL", "total_cents": 2500, "minimum_total_cents": 2500,
             "confirmation_requirements_met": True, "limitations": [], "items": [
                 {"product_name": "Creme oficial", "variant_name": "250 mL", "attributes": {"Volume": "250 mL"},
                  "quantity": 2, "price_kind": "fixed", "unit_price_cents": 1250,
                  "total_cents": 2500, "minimum_total_cents": 2500, "stock": {"mode": "tracked"}}]}
    return facts | changes


@pytest.mark.parametrize("style", STYLE_LABELS)
def test_commerce_summary_uses_complete_server_totals_and_does_not_claim_order_payment_or_stock(style):
    answer = render_commerce_summary(estimate(), {"style": style, "length": "detailed"})
    assert "*Creme oficial* / 250 mL · 2 un." in answer
    assert "R$ 12,50 cada" in answer and "subtotal *R$ 25,00*" in answer and "*Total:* R$ 25,00" in answer
    assert "Volume: 250 mL" in answer and "\n\n" in answer
    assert not any(text in answer for text in ("Posso confirmar", "Pedido confirmado", "pago", "estoque reservado", "desconto"))


@pytest.mark.parametrize("kind", ["from", "on_request", "custom"])
def test_non_fixed_commerce_price_never_becomes_zero_or_an_exact_total(kind):
    facts = estimate(total_cents=None, minimum_total_cents=2500 if kind == "from" else None,
                     confirmation_requirements_met=False, limitations=["non_fixed_price", "unknown_stock"])
    facts["items"][0].update(price_kind=kind, total_cents=None, unit_price_cents=1250 if kind == "from" else None,
                              minimum_total_cents=2500 if kind == "from" else None, stock={"mode": "unknown"})
    answer = render_commerce_summary(facts)
    assert "*Total:*" not in answer and "R$ 0" not in answer
    assert "disponibilidade de estoque precisa ser consultada" in answer
    assert "Valor mínimo" in answer if kind == "from" else "Total sob consulta" in answer
    proposal = render_commerce_order(facts)
    assert "Posso confirmar" not in proposal and "conferir preço e disponibilidade" in proposal


@pytest.mark.parametrize("mutation", ["missing_item", "missing_unit", "inconsistent_line", "inconsistent_total", "partial_total",
                                     "unknown_stock_ready", "non_fixed_ready", "boolean_price", "unknown_price_kind"])
def test_contradictory_or_missing_commerce_facts_fail_closed(mutation):
    facts = estimate()
    if mutation == "missing_item": facts["items"] = []
    elif mutation == "missing_unit": facts["items"][0]["unit_price_cents"] = None
    elif mutation == "inconsistent_line": facts["items"][0]["total_cents"] = 2000
    elif mutation == "inconsistent_total": facts["total_cents"] = 4000
    elif mutation == "partial_total":
        facts["items"].append({"product_name": "Sob consulta", "quantity": 1, "price_kind": "on_request", "unit_price_cents": None})
    elif mutation == "unknown_stock_ready": facts["items"][0]["stock"] = {"mode": "unknown"}
    elif mutation == "non_fixed_ready": facts["limitations"] = ["non_fixed_price"]
    elif mutation == "boolean_price": facts["items"][0]["unit_price_cents"] = True
    elif mutation == "unknown_price_kind": facts["items"][0]["price_kind"] = "free_discount"
    with pytest.raises(ValueError):
        render_commerce_summary(facts)


def test_missing_server_total_stays_missing_and_formatting_never_mutates_the_estimate():
    from copy import deepcopy
    facts = estimate(total_cents=None, minimum_total_cents=None, confirmation_requirements_met=False)
    before = deepcopy(facts)
    answer = render_commerce_summary(facts, simulation=True)
    assert "*Total sob consulta.*" in answer and "*Total:* R$ 25,00" not in answer
    assert "Simulação" in answer and "nenhum orçamento ou pedido foi criado" in answer
    assert facts == before


@pytest.mark.parametrize("state", ["proposal", "confirmed", "cancel_proposal", "cancelled", "preview"])
def test_commerce_operation_copy_distinguishes_proposal_and_actual_outcome_and_never_exposes_token(state):
    facts = estimate() | {"confirmation_token": "TOP_SECRET_TOKEN", "token_hash": "SECRET_DIGEST"}
    answer = render_commerce_order(facts, {"emoji": "light"}, state=state, order_id="ORDER-REAL-123")
    assert "TOP_SECRET" not in answer and "SECRET_DIGEST" not in answer
    assert "ORDER-REAL-123" in answer and "Pagamento não verificado" in answer
    assert not any(text in answer for text in ("responda SIM", "responda NÃO", "CONFIRMO", "pagamento aprovado", "reembolso"))
    if state == "proposal":
        assert "ainda não confirmado" in answer and "Posso confirmar" in answer
    elif state == "confirmed":
        assert "✅ *Pedido confirmado*" in answer and "Posso confirmar" not in answer
    elif state == "cancel_proposal":
        assert "ainda não cancelado" in answer and "Posso cancelar" in answer
    elif state == "cancelled":
        assert "Cancelamento do pedido confirmado" in answer and "Posso cancelar" not in answer
    else:
        assert "nenhuma operação foi realizada" in answer and "Posso confirmar" not in answer


@pytest.mark.parametrize("state", ["confirmed", "cancel_proposal", "cancelled"])
def test_real_commerce_outcome_requires_a_verified_order_identifier(state):
    with pytest.raises(ValueError):
        render_commerce_order(estimate(), state=state)


@pytest.mark.parametrize(("kind", "command", "natural"), [
    ("confirm", "Posso confirmar? Responda SIM ou NÃO.", "Posso confirmar esse agendamento"),
    ("reschedule_confirm", "A reserva original permanece. Confirme a remarcação com SIM ou desista com NÃO.", "A reserva original permanece."),
    ("reschedule_pick", "Responda com o número do agendamento que deseja remarcar.", "Qual agendamento"),
    ("professional", "Qual profissional prefere? Pode responder pelo nome ou número indicado.", "preferência"),
    ("time", "Escolha um dos horários disponíveis pelo número ou horário (ex.: 14:00).", "Qual horário"),
    ("menu", "Escolha o número de um serviço listado.", "Qual serviço"),
])
def test_legacy_final_pass_only_replaces_known_control_instructions_in_matching_steps(kind, command, natural):
    answer = render_message(command, kind=kind)
    assert natural in answer and "SIM" not in answer and "NÃO" not in answer
    assert "número" not in answer and answer.count("?") <= 1
    assert render_message(answer, kind=kind) == answer


def test_final_pass_keeps_all_facts_metadata_and_large_content_without_truncation_or_filling_values():
    body = ("Serviços: Corte II e Barba\r\nProfissional: João 2\r\nData: 20/11/2026\r\n"
            "Horário: 09:15\r\nDuração: 60 minutos\r\nTotal: R$ 55,00\r\nProtocolo: AG-12345\r\n\r\n\r\n"
            "UUID 11111111-1111-4111-8111-111111111111 https://example.invalid/a?id=22\n" + "Fato aprovado. " * 1000)
    answer = render_message(body, {"style": "direct", "length": "short"}, kind="confirmed")
    for fact in ("Corte II e Barba", "João 2", "20/11/2026", "09:15", "60 minutos", "R$ 55,00", "AG-12345",
                 "11111111-1111-4111-8111-111111111111", "https://example.invalid/a?id=22"):
        assert fact in answer
    assert answer.count("Fato aprovado.") == 1000 and "\n\n\n" not in answer
    assert "*Total:* R$ 55,00" in answer and render_message(answer, kind="confirmed") == answer
    assert "?" not in answer.replace("https://example.invalid/a?id=22", "")


def test_faq_instruction_or_number_in_product_name_is_never_silently_reinterpreted():
    text = "1. Kit de 2 unidades\nA regra oficial diz: Responda SIM ou NÃO.\nCódigo MENU-123"
    for kind in ("information", "commerce_information", "faq", "price", "message"):
        assert render_message(text, kind=kind) == text
    menu = render_message("1. Corte\n2. Barba", kind="menu")
    assert menu == "• Corte\n• Barba"


def test_no_legacy_instruction_to_restart_turns_into_an_unimplemented_natural_command():
    answer = render_message("O resultado ainda não foi confirmado. Escreva MENU para tentar novamente.", kind="expired")
    assert answer == "O resultado ainda não foi confirmado."
    assert not any(text in answer for text in ("tente", "reinicie", "confirme", "?"))


def test_rescheduling_and_cancellation_summaries_do_not_claim_booking_success_before_the_actual_operation():
    reschedule = render_summary(summary(), operation="reschedule")
    cancellation = render_summary(summary(), operation="cancel")
    assert "*Proposta de remarcação*" in reschedule and "original permanece" in reschedule
    assert "*Agendamento selecionado para cancelamento*" in cancellation and "Posso cancelar esse agendamento?" in cancellation
    assert "confirmada." not in reschedule and "Cancelamento confirmado." not in cancellation
    actual = render_summary(summary(protocol="REAL-123"), operation="reschedule", confirmed=True)
    assert "*Remarcação confirmada.*" in actual and "REAL-123" in actual and "Posso confirmar" not in actual
    preview = render_summary(summary(), operation="cancel", simulation=True)
    assert "nenhuma alteração foi realizada" in preview and "Posso cancelar" not in preview


def test_technical_order_ids_are_used_as_evidence_but_never_dumped_in_customer_copy():
    technical = "11111111-1111-4111-8111-111111111111"
    answer = render_commerce_order(estimate(), state="confirmed", order_id=technical)
    assert "Pedido confirmado" in answer and technical not in answer and "Pagamento não verificado" in answer


def test_factual_labels_escape_delimiters_idempotently_without_rewriting_identifiers_or_faq_prose():
    from app.qr_presentation import _label
    raw = "Consulta *Especial* / Linha _Premium_ SKU_250_ML"
    escaped = _label(raw)
    assert escaped == r"Consulta \*Especial\* / Linha \_Premium\_ SKU_250_ML"
    assert _label(escaped) == escaped
    quote = render_quote((QuoteItem(raw, 5500),))
    assert "*" + escaped + "*" in quote
    booked = render_summary(summary(services=("Consulta *Especial*",), professional="Ana _Equipe_", protocol="AG_123"), confirmed=True)
    assert r"Consulta \*Especial\*" in booked and r"Ana \_Equipe\_" in booked and "AG_123" in booked
    facts = estimate();facts["items"][0].update(product_name="Creme *Oficial*",variant_name="Linha _Premium_",attributes={"SKU_250_ML":"Nome *real*"})
    answer = render_commerce_summary(facts,{"length":"detailed"})
    assert r"Creme \*Oficial\*" in answer and r"Linha \_Premium\_" in answer and r"SKU_250_ML: Nome \*real\*" in answer
    faq = "A informação oficial usa *ênfase* e _itálico_; não a reescreva."
    assert render_message(faq,kind="information") == faq


@pytest.mark.asyncio
async def test_active_greeting_escapes_customer_service_and_company_labels_without_changing_saved_facts(monkeypatch):
    import json
    from uuid import uuid4
    from app import qr_ai, qr_profile
    from tests.unit.test_qr_ai import FakeRedis
    tenant, service = uuid4(), uuid4()
    business = {"name": "Empresa *Oficial*", "timezone": "UTC"}
    catalog = [{"id": service, "name": "Consulta _Especial_ SKU_250_ML", "price_cents": 5500, "duration_min": 60}]
    async def data(_): return business, catalog, []
    monkeypatch.setattr(qr_ai,"tenant_data",data)
    redis = FakeRedis(); namespace = "preview:escape-labels"; key = f"orion:qr:ai:{tenant}:{namespace}"
    saved = {"step":"professional","services":[str(service)],"customer_name":"Ana *Equipe*"}
    await redis.set(key,json.dumps(saved));await redis.set(key+":catalog-context",qr_profile.catalog_revision(business,catalog,[]))
    result = await qr_ai.turn(tenant,"5567999999999","Oi",redis,namespace,preview=True)
    assert r"Olá, Ana \*Equipe\*!" in result["answer"]
    assert r"Consulta \_Especial\_ SKU_250_ML" in result["answer"]
    assert json.loads(await redis.get(key))["customer_name"] == saved["customer_name"]
    menu = qr_ai.catalog_menu(business,catalog)
    assert r"Empresa \*Oficial\*" in menu and r"Consulta \_Especial\_ SKU_250_ML" in menu


@pytest.mark.parametrize("context", [InteractionContext(information_only=True),InteractionContext(declined=True),
                                   InteractionContext(complaint=True),InteractionContext(handoff=True)])
def test_standalone_suggestion_respects_context_and_never_invents_a_sale(context):
    item=ApprovedSuggestion("Complemento *oficial*","verified-relation")
    assert render_approved_suggestion(item,{"commercial_intensity":"contextual"},context=context)==""
    assert render_approved_suggestion(item,context=InteractionContext())==""
    answer=render_approved_suggestion(item,{"commercial_intensity":"contextual"},context=InteractionContext())
    assert r"Complemento \*oficial\*" in answer and "R$" not in answer and "?" not in answer
    assert not any(claim in answer for claim in ("desconto","disponível","mais vendido","últimas","estoque"))


@pytest.mark.parametrize("style", STYLE_LABELS)
def test_light_icons_identify_date_time_and_price_once_without_changing_summary_or_questions(style):
    plain = render_summary(summary(), {"style": style, "emoji": "none"})
    light = render_summary(summary(), {"style": style, "emoji": "light"})
    for icon in ("✂️", "👤", "📅", "🕒", "⏳", "💰"):
        assert light.count(icon) == 1 and icon not in plain
        light = light.replace(icon + " ", "")
    assert light == plain
    assert not any(icon in light for icon in ("✂", "💈"))


def test_light_slot_icons_are_not_repeated_per_day_and_do_not_imply_a_missing_price():
    slots = ["2026-11-20T09:15:00Z", "2026-11-20T09:30:00Z", "2026-11-21T10:00:00Z"]
    kwargs = {"timezone": "UTC", "total_cents": 5500, "duration_min": 60, "ask_selection": False}
    plain = render_slots(("Consulta",), slots, {"emoji": "none"}, **kwargs)
    light = render_slots(("Consulta",), slots, {"emoji": "light"}, **kwargs)
    for icon in ("📋", "📅", "🕒", "⏳", "💰"):
        assert light.count(icon) == 1 and icon not in plain
        light = light.replace(icon + " ", "")
    assert light == plain and "09h15 • 09h30" in plain and "21/11/2026" in plain
    unknown_price = render_slots(("Consulta",), slots, {"emoji": "light"}, timezone="UTC")
    assert "💰" not in unknown_price and "R$" not in unknown_price


@pytest.mark.parametrize("style", STYLE_LABELS)
def test_review_of_typed_summary_preserves_all_facts_and_contains_one_confirmation_question(style):
    stored = render_summary(summary(professional="Alex — Demonstração"), {"style": style})
    answer = render_summary_review(stored, {"style": style})
    assert stored in answer and answer.count("Posso confirmar") == 1
    for fact in ("Corte", "Barba", "Alex — Demonstração", "20/11/2026", "14h", "R$ 55,00"):
        assert fact in answer
    assert fits_message(answer)


def test_review_legacy_summary_without_confirmation_never_invents_authorization():
    stored = "Resumo: Corte, 19/10 às 14h, 40 minutos, R$ 35,00."
    answer = render_summary_review(stored)
    assert stored in answer and "Posso confirmar" not in answer


def test_customer_history_translates_verified_statuses_uses_local_clock_and_never_invents_price():
    records = [{"id": "technical-private-id", "start_at": datetime.fromisoformat("2026-10-19T15:00:00Z"),
                "status": status, "protocol": f"AG-SYNTHETIC-{index}"}
               for index, status in enumerate(("confirmed", "cancelled", "hold", "completed", "no_show", "unknown_internal_state"))]
    answer = render_booking_history(records, timezone="America/Campo_Grande")
    for word in ("confirmado", "cancelado", "aguardando confirmação", "concluído", "não compareceu", "a conferir"):
        assert word in answer
    assert answer.count("19/10/2026 às 11h") == len(records)
    assert all(row["protocol"] in answer for row in records)
    assert not any(word in answer for word in ("confirmed", "cancelled", "unknown_internal_state", "technical-private-id", "11:00", "R$"))
    assert fits_message(answer)


def test_customer_history_keeps_eight_long_protocols_inside_transport_budget():
    records = [{"start_at": datetime.fromisoformat("2026-10-19T15:15:00Z"), "status": "confirmed",
                "protocol": "AG-" + str(index) + "X"*76} for index in range(8)]
    answer = render_booking_history(records, timezone="America/Campo_Grande")
    assert all(row["protocol"] in answer for row in records) and "11h15" in answer and fits_message(answer)


def test_customer_history_never_displays_an_unverified_floating_timezone():
    with pytest.raises(ValueError):
        render_booking_history([{"start_at": datetime(2026, 10, 19, 11), "status": "confirmed", "protocol": "AG-SYNTHETIC"}], timezone="UTC")
