"""Pure dialogue contracts: no database, WhatsApp, provider key or live request."""
import json
from datetime import datetime

import httpx
import pytest

from app.qr_dialogue import (choose_professional, choose_service, choose_time,
    contextual_response, interpret_contextual_response)


@pytest.mark.parametrize("text", ["sim", "confirmo", "pode marcar", "fechou", "isso mesmo", "pode ser"])
def test_brief_agreement_requires_pending_confirmation(text):
    assert contextual_response(text, awaiting_confirmation=True).kind == "affirm"
    assert contextual_response(text, awaiting_confirmation=False, semantic_kind="affirm").kind == "uncertain"


@pytest.mark.parametrize("text", ["Não", "Não obrigado", "Não quero"])
def test_decline_does_not_mean_cancel(text):
    assert contextual_response(text, awaiting_confirmation=True, semantic_kind="affirm").kind == "decline"


@pytest.mark.parametrize("text,kind", [
    ("Pode confirmar, mas troca para as 15h", "amend"),
    ("Confirmo. Só o corte mesmo", "amend"),
    ("Pode marcar sem barba", "amend"),
    ("Pode ser... quanto fica com barba?", "uncertain"),
    ("Pode confirmar?", "uncertain"),
    ("Sim, mas ainda não tenho certeza", "uncertain"),
    ("Confirmo... não, pera", "uncertain"),
    ("Sim, não confirme", "uncertain"),
    ("Se for no mesmo valor, pode marcar", "uncertain"),
    ("Pode seguir, antes de confirmar aceita Pix?", "uncertain"),
])
def test_change_or_doubt_overrides_provider_agreement(text, kind):
    assert contextual_response(text, awaiting_confirmation=True, semantic_kind="affirm").kind == kind


def test_semantic_agreement_is_not_a_phrase_whitelist():
    text = "Do jeitinho que você me mostrou, podemos seguir em frente"
    assert contextual_response(text, awaiting_confirmation=True).kind == "uncertain"
    assert contextual_response(text, awaiting_confirmation=True, semantic_kind="affirm").kind == "affirm"
    assert contextual_response("texto desconhecido", awaiting_confirmation=True).kind == "uncertain"


@pytest.mark.parametrize("text", ["Confirmo se tiver desconto", "Pode confirmar desde que aceitem Pix",
    "Caso consigam entregar, pode confirmar", "Pode seguir a menos que haja taxa",
    "Contanto que não cobre sinal, confirmo", "Confirmo sob a condição de pagar depois",
    "Dependendo da entrega, pode confirmar"])
@pytest.mark.parametrize("action", ["booking", "order", "cancel"])
def test_any_conditional_connector_overrides_semantic_agreement(text, action):
    decision = contextual_response(text, awaiting_confirmation=True, semantic_kind="affirm", confirmation_action=action)
    assert decision.kind == "uncertain" and decision.reason == "condition_or_hypothesis"


@pytest.mark.parametrize("text", ["pode cancelar", "confirmo o cancelamento", "pode desmarcar"])
def test_cancellation_consent_is_scoped_to_an_existing_cancellation_summary(text):
    assert contextual_response(text, awaiting_confirmation=True).kind == "uncertain"
    assert contextual_response(text, awaiting_confirmation=True, confirmation_action="order").kind == "uncertain"
    assert contextual_response(text, awaiting_confirmation=True, confirmation_action="cancel").kind == "affirm"
    assert contextual_response(text + "?", awaiting_confirmation=True, semantic_kind="affirm", confirmation_action="cancel").kind == "uncertain"
    assert contextual_response("pode cancelar mas troca às 15h", awaiting_confirmation=True, confirmation_action="cancel").kind == "amend"


@pytest.mark.asyncio
async def test_semantic_call_has_closed_schema_no_tools_no_context_and_one_request():
    seen = []
    def handler(request):
        data = json.loads(request.content);seen.append(data)
        assert data["response_format"]["json_schema"]["strict"] is True
        schema = data["response_format"]["json_schema"]["schema"]
        assert schema["additionalProperties"] is False and schema["required"] == ["kind"]
        assert set(schema["properties"]["kind"]["enum"]) == {"affirm", "decline", "amend", "uncertain"}
        assert "tools" not in data and "max_tokens" not in data
        assert len(data["messages"]) == 2
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": '{"kind":"affirm"}'}}]})
    result = await interpret_contextual_response(message="Do jeito que você mostrou, pode seguir", awaiting_confirmation=True,
        key="synthetic", model="openai/gpt-oss-20b", transport=httpx.MockTransport(handler))
    assert result.kind == "affirm" and len(seen) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {"choices": [{"finish_reason": "length", "message": {"content": '{"kind":"affirm"}'}}]},
    {"choices": [{"finish_reason": "stop", "message": {"content": '{"kind":"affirm","execute":true}'}}]},
    {"choices": [{"finish_reason": "stop", "message": {"content": '{"kind":"affirm"}', "tool_calls": [{}]}}]},
    {"choices": [{"finish_reason": "stop", "message": {"content": '{"kind":"yes"}'}}]},
    {"choices": []},
])
async def test_semantic_invalid_output_never_falls_back_to_agreement(payload):
    result = await interpret_contextual_response(message="Do jeito que você mostrou, pode seguir", awaiting_confirmation=True,
        key="synthetic", model="openai/gpt-oss-20b", transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload)))
    assert result.kind == "uncertain"


@pytest.mark.asyncio
async def test_no_key_unsupported_model_and_transport_failure_are_closed():
    def handler(_):
        raise AssertionError("No provider request allowed")
    for key, model in [("", "openai/gpt-oss-20b"), ("synthetic", "arbitrary-model")]:
        result = await interpret_contextual_response(message="Vamos seguir desse jeito", awaiting_confirmation=True,
            key=key, model=model, transport=httpx.MockTransport(handler))
        assert result.kind == "uncertain"
    def timeout(_):
        raise httpx.ReadTimeout("synthetic")
    result = await interpret_contextual_response(message="Vamos seguir desse jeito", awaiting_confirmation=True,
        key="synthetic", model="openai/gpt-oss-20b", transport=httpx.MockTransport(timeout))
    assert result.kind == "uncertain"


@pytest.mark.asyncio
async def test_doubt_changes_canonical_answers_and_absent_question_do_not_call_provider():
    def handler(_):
        raise AssertionError("No provider call for guarded/simple answers")
    for text, pending in [("Pode confirmar?", True), ("Troca às 15h", True), ("Sim", True), ("Do jeito que você mostrou", False)]:
        result = await interpret_contextual_response(message=text, awaiting_confirmation=pending,
            key="synthetic", model="openai/gpt-oss-20b", transport=httpx.MockTransport(handler))
        assert result.kind in {"uncertain", "amend", "affirm"}


ZONE = "America/Campo_Grande"
SLOTS = [f"2026-10-19T{clock}:00-04:00" for clock in ["08:00", "08:30", "11:00", "11:30", "14:00", "15:00", "18:00"]]


@pytest.mark.parametrize("text,index", [("às 11", 2), ("11:00", 2), ("às 3 da tarde", 5),
    ("o último da manhã", 3), ("o primeiro da tarde", 4), ("às 11, não melhor às 15", 5)])
def test_time_resolves_only_from_complete_real_result(text, index):
    result = choose_time(text, SLOTS, presented_slots=SLOTS[:2], timezone=ZONE)
    assert result.kind == "selected" and result.when == SLOTS[index]


def test_bare_ordinal_targets_presented_subset_not_unshown_last():
    assert choose_time("o último", SLOTS, presented_slots=SLOTS[:2], timezone=ZONE).when == SLOTS[1]
    assert choose_time("o primeiro", SLOTS, timezone=ZONE).kind == "uncertain"
    assert choose_time("2", SLOTS, presented_slots=SLOTS[:2], timezone=ZONE).when == SLOTS[1]


@pytest.mark.parametrize("text", ["às 9", "às 13:00"])
def test_missing_clock_is_not_invented(text):
    assert choose_time(text, SLOTS, timezone=ZONE).kind == "unavailable"


@pytest.mark.parametrize("text", ["às 11 ou às 15", "às 11?", "às 11 amanhã", "às 11 com Bruno", "talvez às 11"])
def test_time_ambiguity_or_another_topic_is_not_a_simple_choice(text):
    assert choose_time(text, SLOTS, presented_slots=SLOTS[:2], timezone=ZONE).kind == "uncertain"


@pytest.mark.parametrize("text", ["Não às 11", "Não quero às 11", "Nem às 11", "Às 15, melhor não às 11"])
def test_explicitly_negated_clock_cannot_be_selected_by_removing_negation(text):
    assert choose_time(text, SLOTS, presented_slots=SLOTS[:2], timezone=ZONE).kind == "uncertain"


def test_final_clear_clock_correction_can_replace_a_negated_first_clock():
    result = choose_time("Não às 11, melhor às 15", SLOTS, presented_slots=SLOTS[:2], timezone=ZONE)
    assert result.kind == "selected" and result.when == SLOTS[5]


@pytest.mark.parametrize("text", ["depois das 14:00", "antes das 11:00", "por volta das 11:00", "de manhã"])
def test_constraints_only_return_backend_alternatives_never_exact_selection(text):
    result = choose_time(text, SLOTS, timezone=ZONE)
    assert result.kind == "alternatives" and result.when is None
    assert result.alternatives and set(result.alternatives) <= set(SLOTS)


def test_ambiguous_ampm_multi_day_naive_or_foreign_presented_slot_is_closed():
    assert choose_time("às 11", [*SLOTS, "2026-10-19T23:00:00-04:00"], timezone=ZONE).kind == "uncertain"
    assert choose_time("às 11", [*SLOTS, "2026-10-20T11:00:00-04:00"], timezone=ZONE).kind == "uncertain"
    assert choose_time("o último da manhã", [*SLOTS, "2026-10-20T11:00:00-04:00"], timezone=ZONE).kind == "uncertain"
    assert choose_time("às 11", [datetime(2026, 10, 19, 11)], timezone=ZONE).kind == "uncertain"
    assert choose_time("o último", SLOTS, presented_slots=["2026-10-19T10:00:00-04:00"], timezone=ZONE).kind == "uncertain"


def test_professional_name_unique_and_demo_metadata_preserved():
    people = [{"id": "a", "name": "Bruno Santos (Demonstração)"}, {"id": "b", "name": "Breno"}]
    assert choose_professional("o Bruno", people) is people[0]
    assert choose_professional("pode ser com Bruno Santos", people)["name"] == people[0]["name"]
    assert choose_professional("2", people) is people[1]
    for text in ["Bruno ou Breno", "Bruno amanhã", "Não Bruno", "Com Jorge", "me chamo Bruno"]:
        assert choose_professional(text, people) is None
    assert choose_professional("Bruno", [*people, {"id": "c", "name": "Bruno Silva"}]) is None


def test_service_simple_unique_choice_does_not_sum_ambiguous_or_combined_items():
    services = [{"id": "cut", "name": "Corte", "aliases": ["cabelo"]}, {"id": "beard", "name": "Barba"}]
    assert choose_service("quero fazer cabelo", services) is services[0]
    for text in ["corte e barba", "só barba e corte", "não quero corte", "quanto custa barba?", "tudo"]:
        assert choose_service(text, services) is None
    assert choose_service("cabelo", [*services, {"id": "child", "name": "Corte Infantil", "aliases": ["cabelo"]}]) is None
