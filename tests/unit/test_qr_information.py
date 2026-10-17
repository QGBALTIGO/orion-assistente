"""Real composer and formatter with pinned synthetic facts; no model or I/O."""
from uuid import uuid4

import pytest

from app.business_profile_schemas import BusinessProfile
from app.qr_information import faq_catalog, info
from app.qr_presentation import STYLE_LABELS, InteractionContext, QuoteItem, render_quote
from app.qr_understanding import UnderstandingPlan


@pytest.fixture
def facts():
    cut, beard, brow, version = (uuid4() for _ in range(4))
    catalog = [{"id": cut, "name": "Corte", "price_cents": 3500, "duration_min": 40},
               {"id": beard, "name": "Barba", "price_cents": 2000, "duration_min": 20},
               {"id": brow, "name": "Sobrancelha", "price_cents": 1000, "duration_min": 15}]
    profile = BusinessProfile(business_name="Empresa publicada", assistant_name="Lia", unit_name="Centro",
        address="Rua autorizada, 10", contact_phone="+5567999999999", timezone="America/Campo_Grande",
        faqs=[{"question": "Aceitam Pix?", "answer": "Aceitamos Pix."},
              {"question": "Tem estacionamento?", "answer": "Há estacionamento na unidade."}],
        hours=[{"weekday": 0, "opens": "09:00", "closes": "18:00"},
               {"weekday": 5, "opens": "08:00", "closes": "12:00"}]).model_dump(mode="json")
    business = {"name": "Legacy name", "timezone": "America/Campo_Grande", "profile": profile,
                "profile_id": str(version), "draft": {"profile": {"business_name": "PRIVATE_DRAFT"}}}
    return cut, beard, brow, business, catalog


@pytest.mark.parametrize("style", STYLE_LABELS)
def test_price_only_uses_explicit_query_ids_and_same_backend_total_in_every_style(facts, style):
    cut, beard, brow, business, catalog = facts
    business["profile"]["style_rules"]["style"] = style
    plan = UnderstandingPlan(intents=["price"], query_service_ids=[cut, beard], service_ids=[brow], sales_optional=False)
    result = info(plan, business, catalog, message="Quanto custa corte e barba? Só quero saber.")
    assert all(x in result.answer for x in ("Corte", "Barba", "R$ 35", "R$ 20", "R$ 55", "1 hora"))
    assert "Sobrancelha" not in result.answer and "R$ 65" not in result.answer
    assert "?" not in result.answer and not result.unresolved
    assert result.profile_version == business["profile_id"]
    assert result.parts[0].source_ids == (str(cut), str(beard))


def test_price_without_a_subject_lists_individual_prices_and_never_sums_the_entire_catalog(facts):
    *_, business, catalog = facts
    result = info(UnderstandingPlan(intents=["price"]), business, catalog, message="Quais os preços?")
    assert all(x in result.answer for x in ("Corte", "Barba", "Sobrancelha", "R$ 35", "R$ 20", "R$ 10"))
    assert "Total:" not in result.answer and "Duração estimada:" not in result.answer
    assert "R$ 65" not in result.answer and "75 minutos" not in result.answer and "?" not in result.answer


def test_ambiguous_or_foreign_price_subject_never_produces_partial_total(facts):
    cut, _, _, business, catalog = facts
    for plan in (UnderstandingPlan(intents=["price"], query_service_ids=[cut], ambiguities=["service"]),
                 UnderstandingPlan(intents=["price"], query_service_ids=[cut, uuid4()])):
        result = info(plan, business, catalog, message="Quanto fica cabelo e tatuagem?")
        assert result.unresolved == ("service",) and "R$" not in result.answer and "?" in result.answer


def test_faq_and_prices_and_business_topics_compose_from_pinned_facts_without_a_booking(facts):
    cut, beard, _, business, catalog = facts
    plan = UnderstandingPlan(intents=["price", "faq", "booking"], query_service_ids=[cut, beard],
                             service_ids=[cut], faq_ids=["faq-0"])
    result = info(plan, business, catalog,
        message="Quanto custa corte e barba? Aceitam Pix? Qual seu nome, endereço e horário de funcionamento?")
    assert all(x in result.answer for x in ("R$ 55", "Aceitamos Pix", "Lia", "Empresa publicada", "Rua autorizada, 10",
                                            "Segunda-feira", "09:00 às 18:00", "Sábado", "08:00 às 12:00"))
    assert "PRIVATE_DRAFT" not in result.answer
    assert "confirmado" not in result.answer and "nenhuma reserva" not in result.answer
    assert "vagas dependem da agenda dos profissionais" in result.answer
    assert {x.kind for x in result.parts} == {"price", "faq", "identity", "address", "hours"}


def test_unknown_payment_question_does_not_turn_into_prices_or_invent_a_policy(facts):
    *_, business, catalog = facts
    business["profile"]["faqs"] = []
    result = info(UnderstandingPlan(intents=["faq"]), business, catalog, message="Vocês aceitam Pix?")
    assert result.unresolved == ("faq",) and "equipe" in result.answer
    assert "Aceitamos" not in result.answer and "R$" not in result.answer and "Corte" not in result.answer
    assert "pagamento" in result.answer


@pytest.mark.parametrize("message", ["Vocês dão desconto?", "Tem reembolso?", "Posso pagar depois?", "Qual a multa de cancelamento?"])
def test_unknown_financial_terms_never_receive_invented_conditions(facts, message):
    *_, business, catalog = facts
    result = info(UnderstandingPlan(intents=["faq"]), business, catalog, message=message)
    assert result.unresolved == ("faq",) and "R$" not in result.answer
    assert "10%" not in result.answer and "gratuito" not in result.answer and "sem multa" not in result.answer


def test_faq_ids_and_exact_published_question_are_version_local_not_model_answers(facts):
    *_, business, catalog = facts
    assert faq_catalog(business) == [{"id": "faq-0", "question": "Aceitam Pix?"},
                                     {"id": "faq-1", "question": "Tem estacionamento?"}]
    for catalog_row in faq_catalog(business):
        assert "answer" not in catalog_row
    exact = info(UnderstandingPlan(intents=["faq"]), business, catalog, message="Olá! Aceitam Pix?")
    assert "Aceitamos Pix" in exact.answer and not exact.unresolved
    invalid = info(UnderstandingPlan(intents=["faq"], faq_ids=["OTHER_TENANT"]), business, catalog,
                   message="Como funciona?")
    assert invalid.unresolved == ("faq",) and "Aceitamos Pix" not in invalid.answer


def test_opening_hours_never_mean_slot_availability_or_change_tenant_timezone(facts):
    *_, business, catalog = facts
    business["profile"]["timezone"] = "America/Sao_Paulo"
    result = info(UnderstandingPlan(intents=["faq"]), business, catalog, message="Qual horário de funcionamento?")
    assert "America/Sao_Paulo" in result.answer and "vagas dependem" in result.answer
    assert business["timezone"] == "America/Campo_Grande"
    assert "livre" not in result.answer and "disponível" not in result.answer and "reservado" not in result.answer
    availability_only = info(UnderstandingPlan(intents=["availability"]), business, catalog, message="Que dia tem livre?")
    assert availability_only.answer is None


def test_missing_identity_address_hours_and_contact_do_not_fall_back_to_drafts(facts):
    *_, business, catalog = facts
    business["profile"] = BusinessProfile().model_dump(mode="json")
    result = info(UnderstandingPlan(intents=["faq"]), business, catalog,
                  message="Qual endereço, telefone de contato e horário de funcionamento?")
    assert set(result.unresolved) == {"address", "contact", "hours"}
    assert "PRIVATE_DRAFT" not in result.answer and "Rua autorizada" not in result.answer
    assert "R$" not in result.answer


def test_unknown_product_query_does_not_quote_services_or_promise_stock(facts):
    *_, business, catalog = facts
    result = info(UnderstandingPlan(intents=["catalog", "price"]), business, catalog,
                  message="Vocês vendem shampoo? Quanto custa?")
    assert result.unresolved == ("products",) and "equipe" in result.answer
    assert "Corte" not in result.answer and "R$" not in result.answer and "temos shampoo" not in result.answer.lower()


def test_known_owner_policy_is_quoted_without_inventing_a_financial_exception(facts):
    *_, business, catalog = facts
    business["profile"]["policies"] = [{"topic": "reembolso", "description": "Toda solicitação precisa ser analisada pela equipe."}]
    result = info(UnderstandingPlan(intents=["faq"]), business, catalog, message="Como funciona o reembolso?")
    assert result.answer == "reembolso: Toda solicitação precisa ser analisada pela equipe."
    assert result.parts[0].source_ids == ("policy-0",) and not result.unresolved
    assert "automaticamente" not in result.answer
    combined = info(UnderstandingPlan(intents=["faq"], faq_ids=["faq-0"]), business, catalog,
                    message="Aceitam Pix? Como funciona o reembolso?")
    assert "Aceitamos Pix" in combined.answer and "Toda solicitação" in combined.answer
    assert len(combined.parts) == 2


def test_long_published_conditions_are_preserved_as_parts_and_never_silently_truncated(facts):
    *_, business, catalog = facts
    business["profile"]["faqs"][0]["answer"] = "Condição publicada. " * 50 + "EXCEÇÃO IMPORTANTE NO FINAL."
    result = info(UnderstandingPlan(intents=["faq"], faq_ids=["faq-0"]), business, catalog,
                  message="Qual condição e qual horário de funcionamento?")
    assert "EXCEÇÃO IMPORTANTE NO FINAL." in result.answer
    assert result.parts[0].text.endswith("EXCEÇÃO IMPORTANTE NO FINAL.")
    assert len(result.parts) == 2


@pytest.mark.parametrize("length", ["short", "balanced", "detailed"])
def test_formatter_total_control_is_explicit_and_independent_of_style(length):
    items = (QuoteItem("Corte", 3500, duration_min=40), QuoteItem("Barba", 2000, duration_min=20))
    for style in STYLE_LABELS:
        preferences = {"style": style, "length": length}
        listing = render_quote(items, preferences, context=InteractionContext(information_only=True), show_total=False)
        quote = render_quote(items, preferences, show_total=True)
        assert "R$ 35" in listing and "R$ 20" in listing
        assert "Total:" not in listing and "Duração estimada:" not in listing
        assert "Total:" in quote and "R$ 55" in quote and "Duração estimada:" in quote
    with pytest.raises(ValueError):
        render_quote(items, show_total="false")
