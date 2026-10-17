"""Whole beard-cut phrases; real turn routing with synthetic scoped facts only."""
from uuid import uuid4

import pytest

from app import qr_ai
from app.qr_understanding import UnderstandingPlan, build_plan, unique_service_reply
from test_qr_multi_flow import flow


@pytest.fixture
def services():
    cut, beard = uuid4(), uuid4()
    catalog = [{"id": cut, "name": "Corte", "price_cents": 3500},
               {"id": beard, "name": "Barba", "price_cents": 2000}]
    return cut, beard, catalog


@pytest.mark.parametrize("message", ["Quanto custa um corte de barba", "Quanto custa só corte de barba",
    "Quanto custa o corte da barba?", "Só corte de barba, quanto fica?", "Quanto custa cortar a barba",
    "Quanto custa cortar barba?"])
@pytest.mark.parametrize("proposed", ["none", "beard", "combo", "cut"])
def test_price_subject_is_beard_even_when_provider_proposes_a_haircut(services, message, proposed):
    cut, beard, catalog = services
    ids = {"none": [], "beard": [beard], "combo": [cut, beard], "cut": [cut]}[proposed]
    proposal = UnderstandingPlan(intents=["price"], query_service_ids=ids) if proposed != "none" else None
    plan = build_plan(message=message, catalog=catalog, proposal=proposal)
    assert plan.query_service_ids == [beard] and not plan.service_ids
    assert plan.selection_mode == "none" and not plan.requests_critical_action and not plan.ambiguities


@pytest.mark.parametrize("message", ["Quanto custa corte e barba?", "Quanto custa corte de cabelo e barba?",
    "Quero corte e barba", "Quero corte de cabelo e barba"])
def test_explicit_haircut_and_beard_combo_remains_two_services(services, message):
    cut, beard, catalog = services
    plan = build_plan(message=message, catalog=catalog)
    assert (plan.query_service_ids if "price" in plan.intents else plan.service_ids) == [cut, beard]
    assert not plan.ambiguities


@pytest.mark.parametrize("message,expected,declined", [
    ("Não quero corte de barba, só corte de cabelo", "cut", "beard"),
    ("Quero corte de barba, não, melhor só corte", "cut", "beard"),
    ("Quero corte de cabelo, não, melhor só corte de barba", "beard", "cut"),
    ("Não quero corte de cabelo, só corte de barba, quanto fica?", "beard", "cut"),
    ("Não quero cortar a barba, só corte de cabelo", "cut", "beard"),
])
def test_whole_phrase_keeps_negative_scope_and_final_correction(services, message, expected, declined):
    cut, beard, catalog = services
    ids = {"cut": cut, "beard": beard}
    plan = build_plan(message=message, catalog=catalog,
        proposal=UnderstandingPlan(intents=["booking"], service_ids=[cut, beard], selection_mode="replace"))
    assert (plan.query_service_ids if "price" in plan.intents else plan.service_ids) == [ids[expected]]
    assert ids[declined] in plan.declined_service_ids and not plan.ambiguities


def test_published_complete_name_or_alias_wins_over_composed_fallback(services):
    cut, beard, catalog = services
    specific = uuid4()
    for item in ({"id": specific, "name": "Corte de Barba", "price_cents": 2700},
                 {"id": specific, "name": "Acabamento Premium", "aliases": ["Corte de Barba"], "price_cents": 2700}):
        current = catalog + [item]
        plan = build_plan(message="Quanto custa um corte de barba", catalog=current,
            proposal=UnderstandingPlan(intents=["price"], query_service_ids=[cut, beard]))
        assert plan.query_service_ids == [specific] and not plan.ambiguities
        assert unique_service_reply("Corte de barba", current, context_step="menu") == [specific]


def test_complete_compound_alias_shared_by_two_ids_never_selects_first(services):
    _, _, catalog = services
    current = catalog + [{"id": uuid4(), "name": "Corte de Barba"},
                         {"id": uuid4(), "name": "Outro acabamento", "aliases": ["Corte de Barba"]}]
    plan = build_plan(message="Quanto custa só corte de barba", catalog=current)
    assert "service" in plan.ambiguities and not plan.query_service_ids and not plan.service_ids
    assert unique_service_reply("Corte de barba", current, context_step="menu") is None


def test_specific_beard_variant_matches_longest_name_and_generic_beard_stays_ambiguous():
    cut, complete, traditional = uuid4(), uuid4(), uuid4()
    catalog = [{"id": cut, "name": "Corte"}, {"id": complete, "name": "Barba Completa"},
               {"id": traditional, "name": "Barba Tradicional"}]
    generic = build_plan(message="Quanto custa um corte de barba", catalog=catalog)
    assert generic.ambiguities == ["service"] and not generic.query_service_ids
    specific = build_plan(message="Quanto custa corte de barba completa", catalog=catalog)
    assert specific.query_service_ids == [complete] and not specific.ambiguities


def test_missing_beard_in_another_company_does_not_price_its_haircut(services):
    cut, _, catalog = services
    for current in ([], catalog[:1], [{"id": uuid4(), "name": "Massagem", "price_cents": 9000}]):
        proposal = UnderstandingPlan(intents=["price"], query_service_ids=[cut]) if current == catalog[:1] else None
        plan = build_plan(message="Quanto custa um corte de barba", catalog=current, proposal=proposal)
        assert plan.ambiguities == ["service"] and not plan.query_service_ids and not plan.service_ids


def test_uncovered_semantic_description_is_not_erased_by_literal_compound(services):
    cut, beard, catalog = services
    plan = build_plan(message="Quanto custa corte de barba e dar um tapa no visual?", catalog=catalog,
        proposal=UnderstandingPlan(intents=["price"], query_service_ids=[cut, beard]))
    assert plan.ambiguities == ["service"] and not plan.query_service_ids
    existing = build_plan(message="Quanto custa dar um tapa no visual e fazer a barba?", catalog=catalog,
        proposal=UnderstandingPlan(intents=["price"], query_service_ids=[cut, beard]))
    assert existing.query_service_ids == [cut, beard] and not existing.ambiguities


@pytest.mark.asyncio
@pytest.mark.parametrize("message,total", [("Quanto custa um corte de barba", "20,00"),
    ("Quanto custa só corte de barba", "20,00"), ("Quanto custa cortar a barba", "20,00"),
    ("Quanto custa corte e barba", "55,00"),
    ("Quanto custa corte de cabelo e barba", "55,00")])
@pytest.mark.parametrize("proposed", ["none", "beard", "combo"])
async def test_real_turn_price_never_reads_grid_or_writes_or_changes_a_booking(flow, message, total, proposed):
    h = flow
    # A correct combo proposal is used for explicit combos; the beard phrase
    # additionally exercises the exact real bug with an incorrect combo.
    ids = [h.beard] if proposed == "beard" and total == "20,00" else [h.cut, h.beard]
    h.proposal["value"] = UnderstandingPlan(intents=["price"], query_service_ids=ids) if proposed != "none" else None
    original = h.seed("date")
    result = await h.turn(message, preview=False)
    assert "R$ " + total in result["answer"]
    if total == "20,00":
        assert "R$ 55" not in result["answer"] and "R$ 35" not in result["answer"]
    assert not h.calls["reads"] and not h.calls["writes"] and not h.calls["names"]
    assert h.state()["services"] == original["services"] and not h.state().get("hold")


@pytest.mark.asyncio
@pytest.mark.parametrize("proposed", ["beard", "combo"])
@pytest.mark.parametrize("message", ["Quero agendar corte de barba", "Quero cortar a barba"])
async def test_real_selection_corrects_provider_then_still_asks_for_missing_detail(flow, proposed, message):
    h = flow
    h.proposal["value"] = UnderstandingPlan(intents=["booking"],
        service_ids=[h.beard] if proposed == "beard" else [h.cut, h.beard], selection_mode="replace")
    result = await h.turn(message, preview=False)
    assert result["step"] in {"date", "professional"} and h.state()["services"] == [str(h.beard)]
    assert not h.calls["reads"] and not h.calls["writes"] and not h.calls["names"]


@pytest.mark.asyncio
async def test_real_service_menu_uses_one_whole_compound_without_calling_provider(flow, monkeypatch):
    h = flow
    async def forbidden(**_):
        raise AssertionError("The whole literal service-menu answer is resolved locally")
    monkeypatch.setattr(qr_ai, "interpret_plan", forbidden)
    await h.turn("Oi", preview=False)
    result = await h.turn("Corte de barba", preview=False)
    assert result["step"] in {"date", "professional"} and h.state()["services"] == [str(h.beard)]
    assert not h.calls["reads"] and not h.calls["writes"]


async def _message(h, body, identity):
    token = qr_ai._message_identity.set(identity)
    try:
        return await h.turn(body, preview=False)
    finally:
        qr_ai._message_identity.reset(token)


@pytest.mark.asyncio
async def test_full_booking_compound_only_prepares_beard_and_requires_later_fresh_consent(flow):
    h = flow
    h.proposal["value"] = UnderstandingPlan(intents=["booking", "confirm"],
        service_ids=[h.cut, h.beard], selection_mode="replace")
    result = await _message(h, "Quero agendar corte de barba amanhã às 15:00 com Bruno", "synthetic-beard-proposal")
    assert result["step"] == "confirm" and "R$ 20,00" in result["answer"] and "R$ 55" not in result["answer"]
    assert h.state()["services"] == [str(h.beard)]
    assert [row[0] for row in h.calls["writes"]] == ["hold"] and h.calls["writes"][0][1] == [h.beard]
    assert h.state()["confirmation_message_id"] == "synthetic-beard-proposal"
    await _message(h, "Sim", "synthetic-beard-proposal")
    assert [row[0] for row in h.calls["writes"]] == ["hold"]
    shown = await _message(h, "Quero ver o resumo de novo", "synthetic-beard-review")
    assert "R$ 20,00" in shown["answer"] and h.state()["confirmation_message_id"] == "synthetic-beard-review"
    await _message(h, "Sim", "synthetic-beard-consent")
    assert [row[0] for row in h.calls["writes"]] == ["hold", "confirm"]


@pytest.mark.asyncio
async def test_ambiguous_compound_in_real_route_never_prices_or_prepares_a_booking(flow):
    h = flow
    h.catalog[1]["name"] = "Barba Completa"
    h.catalog.append({"id": uuid4(), "name": "Barba Tradicional", "price_cents": 2500, "duration_min": 25})
    h.proposal["value"] = UnderstandingPlan(intents=["price"], query_service_ids=[h.beard])
    result = await h.turn("Quanto custa um corte de barba", preview=False)
    assert "quais serviços" in result["answer"] and "R$" not in result["answer"]
    assert not h.calls["reads"] and not h.calls["writes"] and not h.state().get("services")
