"""Synthetic real QR product turns; no external DB, sends or production facts."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from app import qr_commerce_fulfillment as receiving, qr_commerce_store as store
from test_qr_commerce import backend, catalog, turn
from test_qr_commerce_routing import routed
from test_qr_multi_flow import flow


@pytest.fixture
def fulfillment(backend, monkeypatch):
    events, data = backend
    monkeypatch.setenv("COMMERCIAL_FULFILLMENT_ENABLED", "1")
    options = {"enabled": True, "enabled_modes": ["pickup", "delivery"], "profile_version_id": str(uuid4()),
        "profile_revision": 1, "timezone": "America/Campo_Grande", "delivery_requires_team_review": True}
    rows = [{"id": str(uuid4()), "protocol": "AG-SYNTHETIC-A", "start_at": (datetime.now(timezone.utc)+timedelta(days=1)).replace(hour=15, minute=0).isoformat(),
             "timezone": options["timezone"], "professional": "Bruno", "professional_name": "Bruno"},
            {"id": str(uuid4()), "protocol": "AG-SYNTHETIC-B", "start_at": (datetime.now(timezone.utc)+timedelta(days=2)).replace(hour=17, minute=30).isoformat(),
             "timezone": options["timezone"], "professional": "Carlos", "professional_name": "Carlos"}]
    reads = []
    async def published(scope):
        reads.append(("options", scope.tenant, scope.phone))
        return deepcopy(options)
    async def appointments(scope):
        reads.append(("appointments", scope.tenant, scope.phone))
        return deepcopy(rows)
    original_estimate = store.estimate
    async def estimate(scope, items, *, fulfillment=None):
        result = await original_estimate(scope, items)
        if fulfillment:
            assert fulfillment["mode"] in options["enabled_modes"] and options["enabled"]
            appointment = next((row for row in rows if row["id"] == str(fulfillment.get("pickup_appointment_id"))), None)
            if fulfillment.get("pickup_appointment_id"):
                assert appointment is not None
            result.update(fulfillment_mode=fulfillment["mode"], pickup_appointment_id=fulfillment.get("pickup_appointment_id"),
                fulfillment_snapshot={"schema_version": 1, "mode": fulfillment["mode"],
                    "profile_version_id": options["profile_version_id"], "profile_revision": options["profile_revision"],
                    "pickup_appointment": appointment, "pickup_location": "Local sintético publicado",
                    "requires_team_review": fulfillment["mode"] == "delivery"})
            if fulfillment["mode"] == "delivery":
                result.update(confirmation_requirements_met=False, limitations=[*result["limitations"], "delivery_requires_team_review"])
        return result
    async def quote(scope, items, *, fulfillment=None, customer_name=None, validate_summary=None):
        result = await estimate(scope, items, fulfillment=fulfillment)
        validate_summary(result)
        events.append("quote")
        result.update(id=scope.identity("quote"), expires_at=datetime.now(timezone.utc)+timedelta(minutes=15), confirmation_token="x"*43)
        data["quotes"][result["id"]] = result
        return result
    monkeypatch.setattr(store, "fulfillment_options", published, raising=False)
    monkeypatch.setattr(store, "pickup_appointments", appointments, raising=False)
    monkeypatch.setattr(store, "estimate", estimate)
    monkeypatch.setattr(store, "create_quote", quote)
    return events, data, options, rows, reads


@pytest.mark.asyncio
async def test_required_receiving_choice_precedes_quote_and_pickup_never_requires_professional(fulfillment):
    events, _, _, _, reads = fulfillment
    first = await turn("Quero comprar pomada")
    assert first["step"] == "commerce_fulfillment" and "Como prefere receber" in first["answer"]
    assert "quote" not in events and "order" not in events and not first["state"].get("pending")
    selected = await turn("Retirar na loja", state=first["state"], message="pickup-selection")
    assert selected["step"] == "commerce_confirm" and "Sem agendamento associado" in selected["answer"]
    assert selected["state"]["fulfillment"] == {"mode": "pickup", "pickup_appointment_id": None}
    assert not any(row[0] == "appointments" for row in reads) and "profissional" not in selected["answer"].lower()
    done = await turn("Pode confirmar", state=selected["state"], message="pickup-consent")
    assert done["step"] == "commerce_confirmed" and events.count("confirm") == 1


@pytest.mark.asyncio
async def test_explicit_pickup_without_appointment_has_one_review_and_no_agenda_read(fulfillment):
    events, _, _, _, reads = fulfillment
    result = await turn("Quero comprar pomada para retirar sem agendamento")
    assert result["step"] == "commerce_confirm" and "Sem agendamento associado" in result["answer"]
    assert events.count("quote") == 1 and events.count("order") == 1 and "confirm" not in events
    assert not any(row[0] == "appointments" for row in reads)


@pytest.mark.asyncio
async def test_linked_pickup_lists_only_scoped_candidates_and_requires_natural_unambiguous_selection(fulfillment):
    events, _, _, rows, reads = fulfillment
    first = await turn("Quero comprar pomada para retirar no meu horário")
    assert first["step"] == "commerce_fulfillment" and all(row["protocol"] in first["answer"] for row in rows)
    assert "quote" not in events and "order" not in events and first["state"]["fulfillment_waiting_for"] == "appointment"
    choice = await turn("Com Bruno", state=first["state"], message="appointment-choice")
    assert choice["step"] == "commerce_confirm" and "Vínculo revisado" in choice["answer"]
    assert choice["state"]["fulfillment"]["pickup_appointment_id"] == rows[0]["id"]
    assert "não cria nem altera agendamento" in choice["answer"] and "confirm" not in events
    assert all(row[1:] == (UUID("11111111-1111-4111-8111-111111111111"), "5567999999901") for row in reads)


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", ["Com Bruno e Carlos", "o primeiro ou o segundo", "AG-FOREIGN-SECRET", "os dois"])
async def test_ambiguous_or_unknown_appointment_reference_never_selects_first_or_writes(fulfillment, reply):
    events, _, _, _, _ = fulfillment
    first = await turn("Quero comprar pomada para retirar no meu horário")
    result = await turn(reply, state=first["state"], message="uncertain-choice", semantic_kind="affirm")
    assert "quote" not in events and "order" not in events and "confirm" not in events
    assert not result["state"].get("pending") and not (result["state"].get("fulfillment") or {}).get("pickup_appointment_id")


@pytest.mark.asyncio
async def test_changed_displayed_candidates_cannot_retarget_an_old_ordinal(fulfillment):
    events, _, _, rows, _ = fulfillment
    first = await turn("Quero comprar pomada para retirar no meu horário")
    rows.reverse()
    result = await turn("o primeiro", state=first["state"], message="changed-candidates")
    assert "mudaram" in result["answer"] and "quote" not in events and not result["state"].get("pending")


@pytest.mark.asyncio
async def test_changing_link_discards_old_consent_and_creates_fresh_quote_and_summary(fulfillment):
    events, _, _, rows, _ = fulfillment
    first = await turn("Quero comprar pomada para retirar no meu horário")
    one = await turn("Com Bruno", state=first["state"], message="first-link")
    changed = await turn("Pode confirmar o segundo", state=one["state"], message="new-target", semantic_kind="affirm")
    assert changed["step"] == "commerce_confirm" and changed["state"]["fulfillment"]["pickup_appointment_id"] == rows[1]["id"]
    assert changed["state"]["pending"]["quote_id"] != one["state"]["pending"]["quote_id"]
    assert changed["state"]["pending"]["summary_message_id"] == "new-target" and "confirm" not in events
    done = await turn("Pode confirmar", state=changed["state"], message="new-target-consent")
    assert done["step"] == "commerce_confirmed" and events.count("confirm") == 1


@pytest.mark.asyncio
async def test_delivery_is_team_review_and_never_creates_order_quote_or_stock_reservation(fulfillment):
    events, _, _, _, _ = fulfillment
    first = await turn("Quero comprar pomada com entrega")
    assert "equipe" in first["answer"] and "frete" in first["answer"] and "prazo" in first["answer"]
    assert not set(events) & {"quote", "order", "confirm"} and not first["state"].get("pending")
    result = await turn("Pode confirmar", state=first["state"], message="delivery-consent", semantic_kind="affirm")
    assert result["step"] == "commerce_no_confirmation" and not set(events) & {"quote", "order", "confirm"}


@pytest.mark.asyncio
async def test_change_from_pickup_to_delivery_revokes_old_pending_even_with_provider_affirm(fulfillment):
    events, _, _, _, _ = fulfillment
    first = await turn("Quero comprar pomada para retirar sem agendamento")
    changed = await turn("Melhor entrega", state=first["state"], message="delivery-change", semantic_kind="affirm")
    assert "equipe" in changed["answer"] and "pending" not in changed["state"] and "confirm" not in events
    assert events.count("quote") == 1 and events.count("order") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["revision", "mode", "capability"])
async def test_profile_change_invalidates_pending_before_any_confirmation(fulfillment, change):
    events, _, options, _, _ = fulfillment
    first = await turn("Quero comprar pomada para retirar sem agendamento")
    if change == "revision": options["profile_revision"] += 1
    elif change == "mode": options["enabled_modes"] = ["delivery"]
    else: options["enabled"] = False
    result = await turn("Pode confirmar", state=first["state"], message="changed-profile-consent")
    assert result["step"] == "commerce_new_confirmation_required" and "pending" not in result["state"] and "confirm" not in events


@pytest.mark.asyncio
async def test_receiving_price_question_is_read_only_and_does_not_change_mode_or_cart(fulfillment):
    events, _, _, _, _ = fulfillment
    first = await turn("Quero comprar pomada para retirar sem agendamento")
    before = deepcopy(first["state"])
    events.clear()
    result = await turn("Quanto custa a entrega?", state=before, message="receiving-question")
    assert result["step"] == "commerce_information" and "não calcula frete" in result["answer"]
    assert result["state"]["fulfillment"] == before["fulfillment"] and result["state"]["cart"] == before["cart"] and not events
    blocked = await turn("Pode confirmar", state=result["state"], message="after-question")
    assert blocked["step"] == "commerce_new_confirmation_required" and "confirm" not in events


@pytest.mark.asyncio
async def test_preview_pickup_uses_estimates_and_scoped_reads_only(fulfillment):
    events, _, _, _, _ = fulfillment
    first = await turn("Quero comprar pomada para retirar no meu horário", preview=True)
    result = await turn("Com Bruno", state=first["state"], message="preview-link", preview=True)
    assert result["step"] == "commerce_preview" and "Vínculo revisado" in result["answer"]
    assert not set(events) & {"quote", "order", "confirm", "cancel"} and not result["state"].get("pending")


@pytest.mark.asyncio
async def test_cancel_product_is_independent_of_appointment_and_current_modes(fulfillment):
    events, data, options, rows, _ = fulfillment
    first = await turn("Quero comprar pomada para retirar no meu horário")
    selected = await turn("Com Bruno", state=first["state"], message="cancel-link")
    confirmed = await turn("Pode confirmar", state=selected["state"], message="cancel-confirm")
    original_rows = deepcopy(rows)
    options["enabled"] = False
    options["enabled_modes"] = []
    request = await turn("cancelar meu pedido", state=confirmed["state"], message="cancel-request")
    result = await turn("Pode cancelar", state=request["state"], message="cancel-agreement")
    assert result["step"] == "commerce_cancelled" and events.count("cancel") == 1 and rows == original_rows


@pytest.mark.parametrize("body", ["Quero comprar pomada e agendar corte", "Quero remarcar e retirar pomada", "Cancelar meu agendamento"])
def test_link_router_never_reclassifies_new_booking_reschedule_or_cancellation_as_receiving(body):
    assert receiving.receiving_reference(body, {"cart": [{"variant_id": str(uuid4()), "quantity": 1}]}) is False


def test_server_receiving_snapshot_is_mandatory_and_legacy_none_is_not_inferred():
    assert receiving.render_receiving({"fulfillment_mode": None, "pickup_appointment_id": None, "fulfillment_snapshot": None}) == ""
    with pytest.raises(ValueError):
        receiving.render_receiving({"fulfillment_mode": "pickup", "fulfillment_snapshot": {"mode": "pickup"}})


@pytest.mark.asyncio
async def test_actual_qr_route_links_products_without_booking_plan_hold_or_private_response(fulfillment, routed):
    h = routed
    first = await h.message("Quero comprar pomada para retirar no meu horário")
    assert first["step"] == "commerce_fulfillment" and "Em qual dos seus agendamentos" in first["answer"]
    selected = await h.message("Com Bruno")
    assert selected["step"] == "commerce_confirm" and "Vínculo revisado" in selected["answer"]
    assert set(selected) == {"answer", "step"} and "x"*43 not in selected["answer"]
    assert not h.calls["plans"] and not h.calls["reads"] and not h.calls["writes"]
    assert "confirm" not in h.events
    done = await h.message("Pode confirmar")
    assert done["step"] == "commerce_confirmed" and h.events.count("confirm") == 1
    assert not h.calls["writes"]


@pytest.mark.asyncio
async def test_actual_qr_mixed_new_booking_and_product_still_asks_priority_before_operations(fulfillment, routed):
    h = routed
    result = await h.message("Quero comprar pomada para retirar e agendar corte amanhã às 15h com Bruno")
    assert result["step"] == "choose_priority" and not h.events and not h.calls["plans"] and not h.calls["writes"]


@pytest.mark.asyncio
async def test_actual_qr_receiving_information_obeys_catalog_permission_and_never_creates_cart(fulfillment, routed):
    h = routed
    answer = await h.message("Vocês entregam?")
    assert answer["step"] == "commerce_information" and "análise da equipe" in answer["answer"]
    assert not h.events and not h.calls["writes"]
    h.business["profile"]["capabilities"].remove("catalog_answers")
    denied = await h.message("Vocês entregam?")
    assert denied["step"] == "capability_unavailable" and not h.events and not h.calls["writes"]


@pytest.mark.asyncio
async def test_same_message_recovery_has_no_second_write_and_changed_contract_conflicts(fulfillment):
    from fastapi import HTTPException
    events, _, _, _, _ = fulfillment
    first = await turn("Quero comprar pomada para retirar sem agendamento")
    previous = events[:]
    recovered = await turn("Quero comprar pomada para retirar sem agendamento", state=first["state"])
    assert recovered["answer"] == first["answer"] and events == previous and "_verified_operation" not in recovered
    with pytest.raises(HTTPException) as error:
        await turn("Quero comprar pomada com entrega", state=first["state"])
    assert error.value.status_code == 409 and events == previous


@pytest.mark.asyncio
async def test_status_shows_immutable_link_and_separate_current_status_without_moving_receiving(fulfillment):
    _, data, _, rows, _ = fulfillment
    first = await turn("Quero comprar pomada para retirar no meu horário")
    selected = await turn("Com Bruno", state=first["state"], message="historical-link")
    order = data["orders"][UUID(selected["state"]["last_order_id"])]
    original_time = order["quote"]["fulfillment_snapshot"]["pickup_appointment"]["start_at"]
    order["quote"]["pickup_appointment_current"] = {"available": True, "status": "cancelled", "start_at": rows[1]["start_at"]}
    answer = await turn("ver meu pedido", state=selected["state"], message="historical-status")
    assert "Vínculo revisado" in answer["answer"] and "Situação atual" in answer["answer"] and "cancelado" in answer["answer"]
    assert "histórico" in answer["answer"] and order["quote"]["fulfillment_snapshot"]["pickup_appointment"]["start_at"] == original_time


@pytest.mark.asyncio
async def test_complete_receiving_summary_budget_is_checked_before_quote_or_order(fulfillment, monkeypatch):
    events, _, _, _, _ = fulfillment
    original = store.estimate
    async def oversized(*args, **kwargs):
        result = await original(*args, **kwargs)
        if result.get("fulfillment_snapshot"):
            result["fulfillment_snapshot"]["pickup_location"] = "😀"*600
        return result
    monkeypatch.setattr(store, "estimate", oversized)
    result = await turn("Quero comprar pomada para retirar sem agendamento")
    assert result["step"] == "commerce_split_review" and "quote" not in events and "order" not in events


@pytest.mark.asyncio
async def test_candidates_and_ordinals_are_bounded_to_the_actually_displayed_snapshot(fulfillment):
    events, _, _, rows, _ = fulfillment
    sample = deepcopy(rows[0])
    rows[:] = [{**sample, "id": str(uuid4()), "protocol": "AG-SYNTHETIC-"+str(i),
                "professional": "Nome "+str(i)+" X"*55, "professional_name": "Nome "+str(i)+" X"*55} for i in range(15)]
    first = await turn("Quero comprar pomada para retirar no meu horário")
    shown = first["state"]["pickup_candidates"]
    assert 0 < len(shown) < 8 and all(row["protocol"] in first["answer"] for row in shown)
    assert receiving.fits_message(first["answer"]) and "quote" not in events
    response = await turn("15", state=first["state"], message="unshown-ordinal")
    assert "quote" not in events and not response["state"].get("pending")


@pytest.mark.asyncio
async def test_receiving_operational_limitations_cannot_be_replaced_by_a_catalog_faq(fulfillment, catalog):
    events, _, _, _, _ = fulfillment
    catalog[0]["faqs"].append({"question": "Vocês entregam?", "answer": "Frete grátis imediato."})
    answer = await turn("Vocês entregam?")
    assert "análise da equipe" in answer["answer"] and "não calcula frete" in answer["answer"]
    assert "Frete grátis imediato" not in answer["answer"] and not events


@pytest.mark.asyncio
async def test_no_owned_future_appointments_allows_explicit_pickup_without_association(fulfillment):
    events, _, _, rows, _ = fulfillment
    rows.clear()
    first = await turn("Quero comprar pomada para retirar no meu horário")
    assert "Não encontrei agendamento próprio" in first["answer"] and "quote" not in events
    result = await turn("Sem agendamento", state=first["state"], message="pickup-no-booking")
    assert result["step"] == "commerce_confirm" and "Sem agendamento associado" in result["answer"]
    assert "confirm" not in events


@pytest.mark.asyncio
async def test_receiving_conditions_and_negation_do_not_override_old_summary_even_with_semantic_affirm(fulfillment):
    events, _, _, _, _ = fulfillment
    first = await turn("Quero comprar pomada para retirar sem agendamento")
    result = await turn("Pode confirmar se fizer entrega grátis", state=first["state"], message="conditional-delivery", semantic_kind="affirm")
    assert "confirm" not in events and "pending" not in result["state"]
    assert "distinguir" in result["answer"] and events.count("quote") == 1
