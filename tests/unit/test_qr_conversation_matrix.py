"""Distinct end-to-end conversation decisions using real turn/local DB fakes.

These are synthetic fixtures, not proof of WhatsApp, ASR or production writes.
"""
import json
from copy import deepcopy
from datetime import date, timedelta
from uuid import uuid4

from fastapi import HTTPException
import pytest

from app import qr_ai
from test_qr_multi_flow import flow, Session
from test_qr_cancellation import cancellation, routed_cancellation


async def identified(h, body, identity):
    token = qr_ai._message_identity.set(identity)
    try:
        return await h.turn(body, preview=False)
    finally:
        qr_ai._message_identity.reset(token)


async def ask_service(h):
    result = await identified(h, "Oi", "synthetic-service-question")
    assert "serviço" in result["answer"].lower()
    return result


def forbid_provider(monkeypatch):
    async def forbidden(**_):
        raise AssertionError("An integral answer to the current service question must use scoped local facts")
    monkeypatch.setattr(qr_ai, "interpret_plan", forbidden)


def one_available(h, monkeypatch):
    original = qr_ai.list_slots
    async def actual(db, tenant, professional, ids, day):
        result = await original(db, tenant, professional, ids, day)
        return {**result, "slots": result["slots"] if professional == h.bruno else []}
    monkeypatch.setattr(qr_ai, "list_slots", actual)


def no_mutation(h):
    assert not h.calls["writes"] and not h.calls["names"]


@pytest.mark.asyncio
async def test_service_question_then_cabelo_informs_registered_price_duration_before_the_missing_day(flow, monkeypatch):
    h = flow; await ask_service(h); forbid_provider(monkeypatch)
    result = await identified(h, "Cabelo", "synthetic-cabelo")
    assert "R$ 35,00" in result["answer"] and "40" in result["answer"]
    assert "dia" in result["answer"].lower() and h.state()["services"] == [str(h.cut)]
    assert not h.calls["reads"]; no_mutation(h)


@pytest.mark.asyncio
async def test_service_published_literally_as_cabelo_advances_once_with_its_real_price(flow, monkeypatch):
    h = flow; h.catalog[0]["name"] = "Cabelo"
    await ask_service(h); forbid_provider(monkeypatch)
    result = await identified(h, "Cabelo", "synthetic-literal-cabelo")
    assert "R$ 35,00" in result["answer"] and "40" in result["answer"]
    assert h.state()["services"] == [str(h.cut)] and "Qual destas opções" not in result["answer"]
    assert not h.calls["reads"]; no_mutation(h)


@pytest.mark.asyncio
async def test_inserted_letter_typo_keeps_the_active_service_question_and_price(flow, monkeypatch):
    h = flow; await ask_service(h); forbid_provider(monkeypatch)
    result = await identified(h, "Cabnelo", "synthetic-typo")
    assert "R$ 35,00" in result["answer"] and h.state()["services"] == [str(h.cut)]
    assert "cabnelo" not in result["answer"].lower(); no_mutation(h)


@pytest.mark.asyncio
async def test_combo_quote_contains_individual_prices_total_and_combined_duration_before_next_step(flow, monkeypatch):
    h = flow; await ask_service(h); forbid_provider(monkeypatch)
    result = await identified(h, "Corte de cabelo e barba", "synthetic-combo")
    text = result["answer"]
    assert all(value in text for value in ("R$ 35,00", "R$ 20,00", "R$ 55,00"))
    assert "1 hora" in text or "60 min" in text
    assert set(h.state()["services"]) == {str(h.cut), str(h.beard)}
    assert not h.calls["reads"]; no_mutation(h)


@pytest.mark.asyncio
async def test_owner_alias_after_question_selects_the_real_id_without_a_model(flow, monkeypatch):
    h = flow; h.catalog[1]["aliases"] = ["Acabamento facial"]
    await ask_service(h); forbid_provider(monkeypatch)
    result = await identified(h, "Acabamento facial", "synthetic-owner-alias")
    assert "R$ 20,00" in result["answer"] and h.state()["services"] == [str(h.beard)]
    no_mutation(h)


@pytest.mark.asyncio
async def test_service_reply_keeps_the_day_already_known_in_the_current_task(flow, monkeypatch):
    h = flow
    monday = qr_ai.requested_day("segunda", h.business["timezone"])
    h.seed("menu", services=[], day=monday.isoformat(), professional=None, professional_name=None, service_reply_expected=True)
    forbid_provider(monkeypatch)
    result = await identified(h, "Cabelo", "synthetic-known-day")
    assert h.state()["day"] == monday.isoformat() and "qual dia" not in result["answer"].lower()
    assert "R$ 35,00" in result["answer"]; no_mutation(h)


@pytest.mark.asyncio
async def test_who_available_monday_then_hair_checks_each_professional_and_lists_only_real_openings(flow, monkeypatch):
    h = flow; one_available(h, monkeypatch)
    await identified(h, "Quem tá disponível segunda?", "synthetic-who-monday")
    forbid_provider(monkeypatch)
    result = await identified(h, "Cabelo", "synthetic-query-service")
    assert "Bruno" in result["answer"] and "Breno" not in result["answer"]
    assert {read[0] for read in h.calls["reads"]} == {h.bruno, h.breno}
    assert all(read[2].weekday() == 0 and read[1] == [h.cut] for read in h.calls["reads"])
    assert not h.state().get("services"); no_mutation(h)


@pytest.mark.asyncio
async def test_cancel_history_then_new_availability_is_a_new_readonly_task(routed_cancellation, monkeypatch):
    h = routed_cancellation
    await h.turn("Quero cancelar minha reserva", "synthetic-cancel-summary")
    deadline = h.state()["confirmation_expires_at"]
    await h.turn("Quem tá disponível segunda?", "synthetic-new-task")
    async def eligible(tenant, ids):
        assert tenant == h.tenant and ids == [h.service]
        return h.professionals
    async def slots(db, tenant, professional, ids, day):
        assert tenant == h.tenant and professional == h.pro and ids == [h.service] and day.weekday() == 0
        return {"slots": [f"{day}T14:00:00-04:00"], "price_cents": 3500, "duration_min": 40}
    monkeypatch.setattr(qr_ai, "professionals_for", eligible)
    monkeypatch.setattr(qr_ai, "list_slots", slots)
    monkeypatch.setattr(qr_ai, "get_session_factory", lambda: Session)
    forbid_provider(monkeypatch)
    result = await h.turn("Cabelo", "synthetic-hair-after-cancel")
    assert "Bruno" in result["answer"] and "cancelamento" not in result["answer"].lower()
    assert h.state().get("step") not in {"cancel_pick", "cancel_confirm"}
    assert "appointment" not in h.state() and "cancel" not in h.events
    assert h.record["status"] == "confirmed" and deadline > 0


@pytest.mark.asyncio
async def test_sm_to_service_question_cannot_authorize_any_prior_task(flow):
    h = flow
    await identified(h, "Quem tá disponível segunda?", "synthetic-query")
    result = await identified(h, "Sm", "synthetic-not-a-service")
    assert result["step"] not in {"confirmed", "cancelled"} and not h.state().get("hold")
    no_mutation(h)


@pytest.mark.asyncio
async def test_informational_barba_simulation_does_not_add_to_the_current_booking_selection(flow):
    h = flow; original = h.seed("date")
    result = await identified(h, "E com barba fica quanto?", "synthetic-simulation")
    assert "R$ 55,00" in result["answer"] and h.state()["services"] == original["services"]
    assert not h.calls["reads"]; no_mutation(h)


@pytest.mark.asyncio
async def test_salon_generic_hair_asks_only_the_real_catalog_ambiguity(flow, monkeypatch):
    h = flow; coloring = uuid4()
    h.catalog.append({"id": coloring, "name": "Coloração", "price_cents": 9000, "duration_min": 60})
    await ask_service(h); forbid_provider(monkeypatch)
    result = await identified(h, "Cabelo", "synthetic-salon")
    assert "Corte" in result["answer"] and "Coloração" in result["answer"]
    assert not h.state().get("services") and "R$ 35" not in result["answer"]
    assert not h.calls["reads"]; no_mutation(h)


@pytest.mark.asyncio
async def test_salon_typo_does_not_hide_the_same_real_ambiguity(flow, monkeypatch):
    h = flow
    h.catalog.append({"id": uuid4(), "name": "Tratamento capilar", "price_cents": 7000, "duration_min": 60})
    await ask_service(h); forbid_provider(monkeypatch)
    result = await identified(h, "Cabeloo", "synthetic-salon-typo")
    assert "Corte" in result["answer"] and "Tratamento capilar" in result["answer"]
    assert not h.state().get("services"); no_mutation(h)


@pytest.mark.asyncio
async def test_a_published_specific_beard_cut_keeps_its_own_price_in_service_reply(flow, monkeypatch):
    h = flow; special = uuid4()
    h.catalog.append({"id": special, "name": "Corte de Barba", "price_cents": 2700, "duration_min": 30})
    async def eligible(tenant, ids):
        assert tenant == h.tenant and ids == [special]
        return h.professionals
    monkeypatch.setattr(qr_ai, "professionals_for", eligible)
    await ask_service(h); forbid_provider(monkeypatch)
    result = await identified(h, "Corte de barba", "synthetic-specific-beard")
    assert "R$ 27,00" in result["answer"] and "R$ 20" not in result["answer"]
    assert h.state()["services"] == [str(special)]; no_mutation(h)


@pytest.mark.asyncio
async def test_partial_known_reply_does_not_silently_drop_an_unknown_service(flow):
    h = flow; await ask_service(h)
    result = await identified(h, "Cabelo e tatuagem", "synthetic-unknown-component")
    assert not h.state().get("services") and not h.state().get("hold")
    assert "R$ 35" not in result["answer"]; no_mutation(h)


@pytest.mark.asyncio
async def test_profile_capability_is_checked_even_for_a_locally_resolved_service(flow, monkeypatch):
    h = flow; h.business["profile"]["capabilities"] = ["catalog_answers"]
    await ask_service(h); forbid_provider(monkeypatch)
    result = await identified(h, "Cabnelo", "synthetic-disabled-booking")
    assert result["step"] == "capability_unavailable" and not h.state().get("services")
    assert not h.calls["reads"]; no_mutation(h)


@pytest.mark.asyncio
async def test_human_pause_takes_precedence_over_typo_service_recognition(flow, monkeypatch):
    h = flow; h.seed("human")
    forbid_provider(monkeypatch)
    result = await identified(h, "Cabnelo", "synthetic-human-pause")
    assert result["answer"] is None and result["step"] == "human"
    assert not h.calls["reads"]; no_mutation(h)


@pytest.mark.asyncio
async def test_price_changed_after_the_question_uses_new_backend_catalog_facts(flow, monkeypatch):
    h = flow; await ask_service(h)
    h.catalog[0]["price_cents"] = 3900
    result = await identified(h, "Cabelo", "synthetic-current-price")
    assert "R$ 39,00" in result["answer"] and "R$ 35" not in result["answer"]
    no_mutation(h)


@pytest.mark.asyncio
async def test_a_typo_on_another_contact_cannot_reuse_the_first_contacts_service_question(flow):
    h = flow; await ask_service(h)
    namespace = h.namespace + ":another-contact"
    result = await qr_ai.turn(h.tenant, "5567988888888", "Cabnelo", h.redis, namespace, preview=False)
    state = json.loads(h.redis.data.get(f"orion:qr:ai:{h.tenant}:{namespace}", "{}"))
    assert not state.get("services") and not state.get("hold")
    assert result["step"] not in {"confirmed", "cancelled"}; no_mutation(h)


@pytest.mark.asyncio
async def test_a_database_error_is_not_reported_as_no_professionals_available(flow, monkeypatch):
    h = flow
    await identified(h, "Quem tá disponível segunda?", "synthetic-availability-error")
    async def broken(*_, **__):
        raise HTTPException(503, "synthetic grid unavailable")
    monkeypatch.setattr(qr_ai, "list_slots", broken)
    result = await identified(h, "Cabelo", "synthetic-error-service")
    assert "não há" not in result["answer"].lower() and "nenhum profissional" not in result["answer"].lower()
    assert "consult" in result["answer"].lower() or "verific" in result["answer"].lower()
    no_mutation(h)


@pytest.mark.asyncio
async def test_quote_is_not_repeated_after_the_client_supplies_the_day(flow, monkeypatch):
    h = flow; await ask_service(h); forbid_provider(monkeypatch)
    first = await identified(h, "Cabelo", "synthetic-once-price")
    assert "R$ 35,00" in first["answer"]
    second = await identified(h, "segunda", "synthetic-day-after-price")
    assert "R$ 35,00" not in second["answer"] and h.state()["day"]
    no_mutation(h)


@pytest.mark.asyncio
async def test_complete_customer_sequence_moves_from_readonly_quote_to_new_summary_then_sm_once(flow, monkeypatch):
    h = flow; one_available(h, monkeypatch)
    await identified(h, "Quem está disponível segunda?", "synthetic-acceptance-query")
    available = await identified(h, "Cabelo", "synthetic-acceptance-hair")
    assert "Bruno" in available["answer"] and not h.calls["writes"]
    quote = await identified(h, "E com barba fica quanto?", "synthetic-acceptance-simulation")
    assert "R$ 55,00" in quote["answer"] and not h.calls["writes"]
    proposal = await identified(h, "Pode ser às 14.", "synthetic-acceptance-new-summary")
    assert proposal["step"] == "confirm" and "14h" in proposal["answer"] and "R$ 55,00" in proposal["answer"]
    assert [write[0] for write in h.calls["writes"]] == ["hold"]
    assert set(h.calls["writes"][0][1]) == {h.cut, h.beard}
    completed = await identified(h, "Sm", "synthetic-acceptance-consent")
    assert completed["step"] == "confirmed" and [write[0] for write in h.calls["writes"]] == ["hold", "confirm"]
    assert completed.get("_verified_operation") is True


@pytest.mark.asyncio
async def test_completed_confirmation_cannot_be_authorized_again_by_another_sm(flow):
    h = flow
    await identified(h, "Quero agendar corte amanhã às 15:00 com Bruno", "synthetic-original-summary")
    completed = await identified(h, "Sim", "synthetic-original-consent")
    assert completed["step"] == "confirmed"
    await identified(h, "Sm", "synthetic-after-completion")
    assert [write[0] for write in h.calls["writes"]] == ["hold", "confirm"]


async def simulated_combo(h, monkeypatch):
    one_available(h, monkeypatch)
    await identified(h, "Quem está disponível segunda?", "synthetic-option-query")
    await identified(h, "Cabelo", "synthetic-option-hair")
    quote = await identified(h, "E com barba fica quanto?", "synthetic-option-quote")
    assert "R$ 55,00" in quote["answer"] and not h.calls["writes"]
    assert set(h.state()["service_quote_option"]["services"]) == {str(h.cut), str(h.beard)}


@pytest.mark.asyncio
async def test_expired_commercial_option_cannot_silently_fall_back_when_its_price_is_accepted(flow, monkeypatch):
    h = flow; await simulated_combo(h, monkeypatch)
    state = h.state(); state["service_quote_option"]["expires_at"] = 0
    h.redis.data[h.key] = json.dumps(state)
    result = await identified(h, "Pode ser às 14", "synthetic-expired-option")
    assert result["step"] != "confirm" and not h.state().get("hold")
    assert "valor" in result["answer"].lower() or "orçamento" in result["answer"].lower() or "confer" in result["answer"].lower()
    no_mutation(h)


@pytest.mark.asyncio
async def test_last_service_withdrawal_invalidates_combo_before_a_later_clock_acceptance(flow, monkeypatch):
    h = flow; await simulated_combo(h, monkeypatch)
    await identified(h, "Pode ser às 14, só o corte", "synthetic-option-withdrawal")
    assert not h.calls["writes"]
    option = h.state().get("service_quote_option")
    assert not option or str(h.beard) not in option.get("services", [])
    result = await identified(h, "Pode ser às 14", "synthetic-after-withdrawal")
    assert not any(write[0] == "confirm" for write in h.calls["writes"])
    for write in h.calls["writes"]:
        assert write[0] == "hold" and write[1] == [h.cut]
    if result["step"] == "confirm":
        assert "R$ 35,00" in result["answer"] and "R$ 55" not in result["answer"]


@pytest.mark.asyncio
async def test_wrong_option_revision_or_day_does_not_authorize_an_original_subject_fallback(flow, monkeypatch):
    h = flow; await simulated_combo(h, monkeypatch)
    original = h.state()
    wrong_day = (date.fromisoformat(original["service_quote_option"]["day"])+timedelta(days=1)).isoformat()
    for index, changed in enumerate(({"revision": "unknown-revision"}, {"day": wrong_day})):
        state = deepcopy(original); state["service_quote_option"].update(changed)
        h.redis.data[h.key] = json.dumps(state)
        result = await identified(h, "Pode ser às 14", "synthetic-wrong-option-"+str(index))
        assert result["step"] != "confirm" and not h.state().get("hold")
        no_mutation(h)


@pytest.mark.asyncio
async def test_bare_clock_does_not_accept_a_different_simulated_composition(flow, monkeypatch):
    h = flow; await simulated_combo(h, monkeypatch)
    result = await identified(h, "às 14", "synthetic-bare-clock")
    assert not any(write[0] == "confirm" for write in h.calls["writes"])
    for write in h.calls["writes"]:
        assert write[0] == "hold" and write[1] == [h.cut]
    if result["step"] == "confirm":
        assert "R$ 35,00" in result["answer"] and "R$ 55" not in result["answer"]
        assert h.state()["services"] == [str(h.cut)]
    else:
        assert not h.state().get("hold") and "?" in result["answer"]


@pytest.mark.asyncio
async def test_later_professional_correction_cannot_resurrect_old_available_professional_or_combo(flow, monkeypatch):
    h = flow; await simulated_combo(h, monkeypatch)
    day = h.state()["last_availability"]["day"]
    result = await identified(h, "Pode ser às 14 com Breno", "synthetic-option-new-professional")
    state = h.state()
    assert result["step"] == "menu" and state["booking_draft"]
    assert state["professional"] == str(h.breno) and state["day"] == day and state["pending_clock"] == "14:00"
    assert "service" in state["clarification_pending"]
    assert not state.get("service_quote_option") and not state.get("last_availability")
    no_mutation(h)
    after = await identified(h, "Pode ser às 14", "synthetic-after-new-professional")
    assert after["step"] != "confirm" and not h.state().get("hold")
    assert h.state()["professional"] == str(h.breno) and h.state()["day"] == day
    no_mutation(h)
