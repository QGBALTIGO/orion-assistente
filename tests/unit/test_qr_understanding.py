"""Pure semantic-plan regressions; never reserve, send or call a provider."""
from datetime import date
from uuid import uuid4

import pytest

from app.qr_understanding import UnderstandingPlan, build_plan, clarification_for


@pytest.fixture
def facts():
    cut, beard, brow, bruno, breno = (uuid4() for _ in range(5))
    catalog = [{"id": cut, "name": "Corte de cabelo", "price_cents": 3500},
               {"id": beard, "name": "Barba", "price_cents": 2000},
               {"id": brow, "name": "Sobrancelha", "price_cents": 1000}]
    professionals = [{"id": bruno, "name": "Bruno Souza"}, {"id": breno, "name": "Breno Lima"}]
    kwargs = {"catalog": catalog, "professionals": professionals,
              "reference_date": date(2026, 10, 17),
              "context": {"hours": [{"opens": "08:00", "closes": "20:00"}]}}
    return cut, beard, brow, bruno, breno, kwargs


def test_final_whole_utterance_correction_replaces_initial_service_and_hour(facts):
    cut, beard, _, _, _, args = facts
    wrong_first_clause = UnderstandingPlan(intents=["booking"], service_ids=[cut, beard],
        requested_date="2026-10-18", requested_time="14:00", selection_mode="replace")
    plan = build_plan(message="Quero corte e barba amanhã às duas, não melhor às três, deixa só o corte mesmo",
                      proposal=wrong_first_clause, **args)
    assert plan.service_ids == [cut] and beard in plan.declined_service_ids
    assert plan.requested_date == "2026-10-18" and plan.requested_time == "15:00"
    assert not plan.requires_clarification and plan.requests_critical_action
    assert not plan.sales_optional


def test_price_and_availability_and_faq_coexist_without_booking(facts):
    cut, beard, _, bruno, _, args = facts
    args["faqs"] = [{"id": "payment", "question": "Aceitam Pix?", "aliases": ["aceitam pix"],
                     "answer": "Factual answer must never enter the plan"}]
    plan = build_plan(message="Oi, boa tarde. Queria saber quanto fica cortar o cabelo e fazer a barba. "
        "Se tiver com o Bruno amanhã depois das quatro, melhor. Ah, vocês aceitam Pix?", **args)
    assert set(plan.intents) == {"price", "availability", "faq"}
    assert plan.query_service_ids == [cut, beard] and not plan.service_ids
    assert plan.professional_id == bruno and plan.requested_time == "16:00"
    assert plan.time_relation == "after" and plan.faq_ids == ["payment"]
    assert not plan.requests_critical_action
    assert "Factual answer" not in plan.model_dump_json()


@pytest.mark.parametrize("message", ["Quanto custa cabelo e barba?", "Só quero saber o preço do corte e da barba",
                                     "Não sei o valor. Quanto custa cabelo e barba?"])
def test_price_questions_cannot_carry_a_provider_booking_selection(facts, message):
    cut, beard, _, _, _, args = facts
    plan = build_plan(message=message,
        proposal=UnderstandingPlan(intents=["booking"], service_ids=[cut, beard], selection_mode="replace"), **args)
    assert plan.intents == ["price"] and plan.query_service_ids == [cut, beard]
    assert plan.service_ids == [] and plan.selection_mode == "none" and not plan.requests_critical_action


def test_price_and_explicit_booking_keep_different_subjects(facts):
    cut, beard, _, _, _, args = facts
    proposal = UnderstandingPlan(intents=["price", "booking"], service_ids=[cut], query_service_ids=[beard],
                                 selection_mode="replace")
    plan = build_plan(message="Quero agendar corte amanhã. E quanto tá a barba?", proposal=proposal, **args)
    assert plan.service_ids == [cut] and plan.query_service_ids == [beard]
    assert set(plan.intents) == {"price", "booking"} and plan.requests_critical_action


def test_hypothetical_addition_queries_context_without_mutating_it(facts):
    cut, beard, _, _, _, args = facts
    args["context"]["service_ids"] = [str(cut)]
    plan = build_plan(message="E com barba?", **args)
    assert plan.query_service_ids == [cut, beard] and plan.service_ids == []
    assert plan.selection_mode == "simulate" and not plan.requests_critical_action
    assert args["context"]["service_ids"] == [str(cut)]


def test_negative_service_is_not_summed_but_not_knowing_price_is_not_a_negation(facts):
    cut, beard, _, _, _, args = facts
    plan = build_plan(message="Não quero corte, só barba, quanto fica?", **args)
    assert plan.query_service_ids == [beard] and plan.declined_service_ids == [cut]
    assert not plan.sales_optional
    second = build_plan(message="Não sei o valor do cabelo e da barba, quanto fica?", **args)
    assert second.query_service_ids == [cut, beard] and second.declined_service_ids == []


def test_last_intention_changes_cancel_to_reschedule_without_executing_either(facts):
    *_, args = facts
    plan = build_plan(message="Cancela meu horário... não, pera, melhor mudar pra sábado", **args)
    assert "reschedule" in plan.intents and "cancel" not in plan.intents
    assert plan.requested_date == "2026-10-17" and plan.requests_critical_action
    assert not hasattr(plan, "execute") and "answer" not in UnderstandingPlan.model_fields


@pytest.mark.parametrize("phrase", ["Quero pra segunda", "Quero fazer tudo", "Que dia tem livre"])
def test_real_utterances_do_not_reuse_old_day_or_select_the_entire_catalog(facts, phrase):
    cut, _, _, _, _, args = facts
    args["context"].update(service_ids=[str(cut)], day="2026-01-01", professional="old")
    plan = build_plan(message=phrase, **args)
    if phrase == "Quero pra segunda":
        assert plan.requested_date == "2026-10-19" and plan.intents == ["availability"]
    elif phrase == "Quero fazer tudo":
        assert plan.requires_clarification and plan.service_ids == []
    else:
        assert plan.intents == ["availability"] and not plan.requests_critical_action
    assert plan.requested_date != "2026-01-01" and plan.professional_id is None


def test_final_text_day_correction_wins_over_late_audio_and_not_first_day(facts):
    *_, args = facts
    plan = build_plan(message="Quero corte quinta às 14:00\nPera, é sexta, não quinta", **args)
    assert plan.requested_date == "2026-10-23" and plan.requested_time == "14:00"
    assert not plan.requires_clarification


@pytest.mark.parametrize("phrase, field", [("segunda ou amanhã", "date"), ("segunda ou terça", "date"),
    ("amanhã, melhor terça ou quarta", "date"),
    ("amanhã às duas", "time"), ("amanhã às quinze ou cinquenta", "time"),
    ("com Bruno ou Breno amanhã", "professional")])
def test_uncertain_decisive_fields_trigger_targeted_clarification(facts, phrase, field):
    *_, args = facts
    args["context"] = {}
    plan = build_plan(message=phrase, **args)
    assert field in plan.ambiguities and plan.requires_clarification
    assert clarification_for(plan) is not None
    assert "R$" not in clarification_for(plan)


def test_consulting_cancellation_policy_does_not_request_cancellation(facts):
    *_, args = facts
    plan = build_plan(message="Como faço para cancelar?", **args)
    assert plan.intents == ["faq"] and not plan.requests_critical_action


def test_product_only_does_not_enter_appointments_and_unimplemented_purchase_is_explicit(facts):
    *_, args = facts
    plan = build_plan(message="Quero comprar só o produto, sem marcar horário", **args)
    assert plan.intents == ["catalog"] and not plan.requests_critical_action
    assert plan.ambiguities == ["unsupported_operation"]


def test_explicit_addition_and_removal_have_different_final_selections(facts):
    cut, beard, _, _, _, args = facts
    args["context"]["service_ids"] = [str(cut)]
    added = build_plan(message="Pode incluir a barba", **args)
    assert added.service_ids == [cut, beard] and added.selection_mode == "add"
    args["context"]["service_ids"] = [str(cut), str(beard)]
    removed = build_plan(message="Tira a barba", **args)
    assert removed.service_ids == [cut] and removed.selection_mode == "remove"
    assert removed.declined_service_ids == [beard]


def test_unknown_alternative_after_known_professional_is_still_ambiguous(facts):
    *_, args = facts
    plan = build_plan(message="Com Bruno, não, melhor Breno ou Carlos", **args)
    assert plan.professional_id is None and "professional" in plan.ambiguities


def test_ambiguous_generic_hair_does_not_choose_or_sum_both_variants(facts):
    cut, _, _, _, _, args = facts
    child = uuid4()
    args["catalog"] = [{"id": cut, "name": "Corte Masculino"}, {"id": child, "name": "Corte Infantil"}]
    plan = build_plan(message="Quanto custa cabelo?", **args)
    assert plan.query_service_ids == [] and plan.service_ids == [] and "service" in plan.ambiguities


def test_customer_self_declaration_is_not_professional_selection(facts):
    *_, args = facts
    plan = build_plan(message="Meu nome é Bruno Souza. Quanto custa barba?", **args)
    assert plan.customer_name == "Bruno Souza" and plan.professional_id is None
    inferred = build_plan(message="Quero com o Bruno", proposal=UnderstandingPlan(customer_name="Bruno"), **args)
    assert inferred.customer_name is None
    *_, breno, _args = facts
    both = build_plan(message="Meu nome é Bruno Souza. Quero com o Breno", **args)
    assert both.customer_name == "Bruno Souza" and both.professional_id == breno


def test_unknown_professional_cannot_fall_back_to_previously_selected_known_name(facts):
    _, _, _, bruno, _, args = facts
    plan = build_plan(message="Quero com Jorge amanhã",
        proposal=UnderstandingPlan(intents=["booking"], professional_id=bruno), **args)
    assert plan.professional_id is None and "professional" in plan.ambiguities


def test_clear_last_time_can_resolve_an_initial_ambiguous_time(facts):
    *_, args = facts
    args["context"] = {}
    plan = build_plan(message="Quero corte amanhã às duas, não, melhor às quinze", **args)
    assert plan.requested_time == "15:00" and "time" not in plan.ambiguities


def test_refused_product_and_information_only_disable_sales_and_handoff_interrupts(facts):
    *_, args = facts
    plan = build_plan(message="Não quero produto, estou apenas pesquisando", **args)
    assert not plan.sales_optional and "products" in plan.declined_offers
    handoff = build_plan(message="Quero cortar e falar com uma pessoa de verdade", **args)
    assert handoff.intents == ["human"] and not handoff.service_ids and not handoff.sales_optional


def test_cross_tenant_ids_and_model_added_facts_are_rejected(facts):
    *_, args = facts
    with pytest.raises(ValueError, match="outside"):
        build_plan(message="Quanto custa corte?", proposal=UnderstandingPlan(query_service_ids=[uuid4()]), **args)
    with pytest.raises(ValueError):
        UnderstandingPlan.model_validate({"intents": ["price"], "price_cents": 1, "answer": "Invented"})
    with pytest.raises(ValueError):
        UnderstandingPlan(requested_date="2026-02-30")


def test_model_cannot_silently_reuse_date_time_or_grant_confirmation(facts):
    *_, args = facts
    plan = build_plan(message="Quanto tá a barba?", proposal=UnderstandingPlan(intents=["confirm", "price"],
        requested_date="2026-01-01", requested_time="14:00"), **args)
    assert plan.requested_date is None and plan.requested_time is None and "confirm" not in plan.intents
    reversed_confirmation = build_plan(message="Pode confirmar... não, pera", **args)
    assert "confirm" not in reversed_confirmation.intents and reversed_confirmation.requires_clarification


def test_semantic_alias_is_not_discarded_when_another_service_has_an_exact_name(facts):
    cut, beard, _, _, _, args = facts
    plan = build_plan(message="Quanto custa dar um tapa no visual e fazer a barba?",
        proposal=UnderstandingPlan(intents=["price"], query_service_ids=[cut, beard]), **args)
    assert plan.query_service_ids == [cut, beard]
    selected = build_plan(message="Quero dar um tapa no visual e fazer a barba amanhã",
        proposal=UnderstandingPlan(intents=["booking"], service_ids=[cut, beard], selection_mode="replace"), **args)
    assert selected.service_ids == [cut, beard]


def test_bare_hour_in_time_selection_and_explicit_period_do_not_require_retyping(facts):
    *_, args = facts
    args["context"]["step"] = "time"
    assert build_plan(message="15", **args).requested_time == "15:00"
    assert build_plan(message="três da tarde", **args).requested_time == "15:00"


def test_confirmation_with_separate_product_refusal_preserves_refusal_without_assuming_summary(facts):
    *_, args = facts
    plan = build_plan(message="Pode confirmar. E não quero produto", **args)
    assert "confirm" not in plan.intents and "products" in plan.declined_offers
    assert plan.requires_clarification and not plan.sales_optional


@pytest.mark.parametrize("phrase", ["Quero barba, não, melhor corte", "Troca a barba pelo corte",
                                     "Em vez da barba quero corte"])
def test_final_service_substitution_without_so_never_keeps_the_initial_service(facts, phrase):
    cut, beard, _, _, _, args = facts
    plan = build_plan(message=phrase,
        proposal=UnderstandingPlan(intents=["booking"], service_ids=[cut], selection_mode="replace"), **args)
    assert plan.service_ids == [cut] and beard in plan.declined_service_ids
    assert not plan.requires_clarification


def test_service_alternatives_require_clarification_until_an_explicit_final_choice(facts):
    cut, _, _, _, _, args = facts
    unresolved = build_plan(message="Quero barba ou corte", **args)
    assert "service" in unresolved.ambiguities and not unresolved.service_ids
    resolved = build_plan(message="Quero barba ou corte, melhor só o corte", **args)
    assert resolved.service_ids == [cut] and not resolved.requires_clarification


def test_weekday_today_and_explicit_next_follow_the_same_policy_as_live_engine(facts):
    *_, args = facts
    assert build_plan(message="sábado", **args).requested_date == "2026-10-17"
    assert build_plan(message="próximo sábado", **args).requested_date == "2026-10-24"
