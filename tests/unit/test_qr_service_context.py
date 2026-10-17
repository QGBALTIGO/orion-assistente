"""Catalog meaning, whole replies and bounded typo decisions; no IO/provider."""
from copy import deepcopy
from uuid import uuid4

import pytest

from app.qr_service_context import resolve_service_reply


@pytest.fixture
def shop():
    cut, beard, brow = uuid4(), uuid4(), uuid4()
    rows = [{"id": cut, "name": "Corte", "price_cents": 3500, "duration_min": 40},
            {"id": beard, "name": "Barba", "price_cents": 2000, "duration_min": 20},
            {"id": brow, "name": "Sobrancelha", "price_cents": 1000, "duration_min": 15}]
    return cut, beard, brow, rows


def answer(message, catalog, awaiting=True):
    return resolve_service_reply(message, catalog, awaiting_service=awaiting)


@pytest.mark.parametrize("message,index", [("Cabelo", 0), ("Corte", 0), ("Cortar cabelo", 0),
    ("Barba", 1), ("Sobrancelha", 2), ("Corte de barba", 1), ("Cortar a barba", 1)])
def test_service_meaning_is_derived_from_this_catalog_after_its_question(shop, message, index):
    result = answer(message, shop[3])
    assert result.kind == "resolved" and result.service_ids == (shop[index],)
    assert not result.corrected and result.clarification is None


@pytest.mark.parametrize("message,indices", [("Corte de cabelo e barba", (0, 1)),
    ("Cabelo + Barba", (0, 1)), ("Corte, barba e sobrancelha", (0, 1, 2)),
    ("Quero fazer cabelo e a barba", (0, 1)), ("Só o corte", (0,))])
def test_service_only_grammar_consumes_every_component_without_repeated_haircut(shop, message, indices):
    result = answer(message, shop[3])
    assert result.kind == "resolved" and result.service_ids == tuple(shop[index] for index in indices)


@pytest.mark.parametrize("message", ["Cabnelo", "Cabeloo", "Cableo", "Cabelp", "Cortee e barbba"])
def test_small_typographic_errors_require_a_unique_complete_catalog_parse(shop, message):
    result = answer(message, shop[3])
    expected = (shop[0], shop[1]) if " e " in message else (shop[0],)
    assert result.kind == "resolved" and result.service_ids == expected and result.corrected


def test_typos_are_never_interpreted_without_a_current_service_question(shop):
    result = answer("Cabnelo", shop[3], awaiting=False)
    assert result.kind == "inapplicable" and not result.service_ids


def test_an_owner_alias_is_local_and_does_not_create_a_global_saas_synonym(shop):
    custom = deepcopy(shop[3]); custom[2]["aliases"] = ["Design facial"]
    assert answer("Design facial", custom).service_ids == (shop[2],)
    assert answer("Design facial", shop[3]).kind == "unmatched"


def test_the_longest_published_name_wins_before_component_aliases(shop):
    special = uuid4()
    catalog = shop[3] + [{"id": special, "name": "Corte de Barba", "price_cents": 2700}]
    assert answer("Corte de barba", catalog).service_ids == (special,)


def test_shared_owner_alias_has_a_targeted_question_with_only_real_choices(shop):
    catalog = deepcopy(shop[3]); catalog[0]["aliases"] = ["Visual"]; catalog[1]["aliases"] = ["Visual"]
    result = answer("Visual", catalog)
    assert result.kind == "ambiguous" and not result.service_ids
    assert {option.id for option in result.options} == set(shop[:2])
    assert all(option.name in result.clarification for option in result.options)
    assert "Sobrancelha" not in result.clarification


def test_a_typo_equally_close_to_two_names_never_chooses_catalog_order():
    first, second = uuid4(), uuid4()
    catalog = [{"id": first, "name": "Massagem"}, {"id": second, "name": "Massagens"}]
    for rows in (catalog, list(reversed(catalog))):
        result = answer("Massagen", rows)
        assert result.kind == "ambiguous" and {option.id for option in result.options} == {first, second}
        assert not result.service_ids and result.corrected


@pytest.mark.parametrize("additional", ["Coloração", "Tratamento capilar", "Mechas"])
def test_generic_hair_is_not_automatically_a_cut_when_other_hair_offerings_exist(shop, additional):
    extra = uuid4(); catalog = shop[3] + [{"id": extra, "name": additional}]
    result = answer("Cabelo", catalog)
    assert result.kind == "ambiguous" and not result.service_ids
    assert {option.id for option in result.options} == {shop[0], extra}
    assert additional in result.clarification and "Barba" not in result.clarification


def test_a_hair_typo_preserves_the_salon_ambiguity_instead_of_forcing_haircut(shop):
    coloring = uuid4(); catalog = shop[3] + [{"id": coloring, "name": "Coloração"}]
    result = answer("Cabnelo", catalog)
    assert result.kind == "ambiguous" and result.corrected
    assert {option.id for option in result.options} == {shop[0], coloring}


def test_the_cutting_verb_resolves_the_cut_in_a_multi_service_hair_salon(shop):
    catalog = shop[3] + [{"id": uuid4(), "name": "Coloração"}]
    assert answer("Cortar cabelo", catalog).service_ids == (shop[0],)


def test_two_cut_variants_need_clarification_but_an_exact_full_name_resolves():
    child, hair = uuid4(), uuid4()
    catalog = [{"id": hair, "name": "Corte de cabelo"}, {"id": child, "name": "Corte Infantil"}]
    assert answer("Cabelo", catalog).kind == "ambiguous"
    assert answer("Corte de cabelo", catalog).service_ids == (hair,)


def test_coloring_alone_is_not_invented_as_a_haircut():
    coloring = uuid4()
    result = answer("Cabelo", [{"id": coloring, "name": "Coloração"}])
    assert result.kind == "ambiguous" and result.options[0].id == coloring and not result.service_ids


def test_single_service_published_literally_as_cabelo_resolves_without_repeating_its_question():
    service = uuid4()
    result = answer("Cabelo", [{"id": service, "name": "Cabelo"}])
    assert result.kind == "resolved" and result.service_ids == (service,) and result.clarification is None


def test_single_owner_published_hair_alias_resolves_the_actual_catalog_service():
    service = uuid4()
    result = answer("Cabelo", [{"id": service, "name": "Tratamento capilar", "aliases": ["Cabelo"]}])
    assert result.kind == "resolved" and result.service_ids == (service,)


def test_literal_cabelo_name_does_not_hide_another_hair_option_in_the_same_salon():
    first, second = uuid4(), uuid4()
    result = answer("Cabelo", [{"id": first, "name": "Cabelo"}, {"id": second, "name": "Coloração"}])
    assert result.kind == "ambiguous" and not result.service_ids
    assert {option.id for option in result.options} == {first, second}


def test_shared_owner_hair_alias_does_not_choose_one_of_its_real_services():
    first, second = uuid4(), uuid4()
    result = answer("Cabelo", [{"id": first, "name": "Corte", "aliases": ["Cabelo"]},
        {"id": second, "name": "Coloração", "aliases": ["Cabelo"]}])
    assert result.kind == "ambiguous" and not result.service_ids
    assert {option.id for option in result.options} == {first, second}


def test_a_bare_cut_in_another_trade_does_not_make_cabelo_a_universal_alias():
    result = answer("Cabelo", [{"id": uuid4(), "name": "Corte"}, {"id": uuid4(), "name": "Polimento"}])
    assert result.kind == "unmatched" and not result.service_ids


@pytest.mark.parametrize("message", ["Cabelo e tatuagem", "Cabelo e bar", "Cabelo massssagem"])
def test_unknown_or_large_errors_never_produce_a_partial_selection(shop, message):
    result = answer(message, shop[3])
    assert result.kind == "unmatched" and not result.service_ids


@pytest.mark.parametrize("message", ["Não quero cabelo", "Cabelo ou barba", "Cabelo se tiver", "Quanto custa cabelo?",
    "Cabelo amanhã", "Cabelo às 14h", "Cabelo com Bruno", "Meu nome é Cabelo", "Quero comprar cabelo",
    "Cabelo no carrinho", "Confirma cabelo", "Cabelo, melhor só barba", "Falar com humano", "Sm"])
def test_other_intents_are_left_to_whole_turn_routing_not_fuzzy_service_selection(shop, message):
    result = answer(message, shop[3])
    assert result.kind in {"inapplicable", "unmatched"} and not result.service_ids


def test_repetition_does_not_silently_imply_multiple_units(shop):
    assert answer("Cabelo e cabelo", shop[3]).kind == "inapplicable"


def test_explicit_quantity_is_not_a_service_menu_reply(shop):
    assert answer("2 cortes", shop[3]).kind == "inapplicable"


@pytest.mark.parametrize("change", ["foreign_id", "duplicate_id", "invalid_alias"])
def test_malformed_or_unbounded_catalog_fails_closed(shop, change):
    catalog = deepcopy(shop[3])
    if change == "foreign_id": catalog[0]["id"] = "not-a-uuid"
    if change == "duplicate_id": catalog[1]["id"] = catalog[0]["id"]
    if change == "invalid_alias": catalog[0]["aliases"] = "not-a-list"
    assert answer("Cabelo", catalog).kind == "inapplicable"


def test_an_empty_company_does_not_reuse_another_companys_service_ids(shop):
    assert answer("Cabelo", []).kind == "unmatched"
    foreign_cut = uuid4()
    result = answer("Cabelo", [{"id": foreign_cut, "name": "Corte de cabelo"}])
    assert result.service_ids == (foreign_cut,) and shop[0] not in result.service_ids


def test_input_and_catalog_are_never_mutated_and_no_price_is_manufactured(shop):
    original = deepcopy(shop[3]); result = answer("Corte de cabelo e barba", shop[3])
    assert shop[3] == original and result.kind == "resolved"
    assert not hasattr(result, "price_cents") and not hasattr(result, "duration_min")


def test_reply_and_catalog_bounds_prevent_unbounded_search(shop):
    assert answer("a"*181, shop[3]).kind == "inapplicable"
    assert answer("Cabelo", [{"id": uuid4(), "name": "Serviço"} for _ in range(61)]).kind == "inapplicable"


def test_consent_dates_or_operational_context_cannot_be_smuggled_in_an_alias(shop):
    catalog = deepcopy(shop[3]); catalog[0]["aliases"] = ["Pode confirmar", "Amanhã às 14h"]
    assert answer("Pode confirmar", catalog).kind == "inapplicable"
    assert answer("Amanhã às 14h", catalog).kind == "inapplicable"
