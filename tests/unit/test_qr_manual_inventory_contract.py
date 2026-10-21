"""Inventory findings reproduced with pure/local fakes; no provider or database."""
from datetime import datetime
import json
from unittest.mock import AsyncMock
from uuid import uuid4

from fastapi import HTTPException
import pytest

from app import qr_ai, qr_operation_receipt, qr_presentation as presentation
from app.qr_information import InformationReply
from app.qr_understanding import UnderstandingPlan
from test_qr_multi_flow import flow


def confirmation(h, *, large=False, rescheduling=False):
    if large:
        h.catalog[:] = [{"id": uuid4(), "name": f"Serviço {index} " + "S" * 69,
                        "price_cents": 3500, "duration_min": 40} for index in range(8)]
    selected = f"{h.tomorrow}T15:00:00-04:00"
    fields = {"hold": str(uuid4()), "services": [str(row["id"]) for row in h.catalog],
              "price": sum(row["price_cents"] for row in h.catalog),
              "duration": sum(row["duration_min"] for row in h.catalog),
              "selected_start": selected, "professional_name": "Profissional " + "P" * 80 if large else "Bruno",
              "confirmation_summary": "Resumo sintético apresentado ao contato",
              "confirmation_expires_at": datetime.now().timestamp() + 240}
    if rescheduling:
        fields.update(appointment=str(h.appointment), expected_snapshot="synthetic-snapshot", new_start=selected,
                      reschedule_facts={"services": [row["name"] for row in h.catalog], "timezone": h.business["timezone"],
                                        "total_cents": fields["price"], "duration_min": fields["duration"],
                                        "professional": fields["professional_name"]})
    return h.seed("reschedule_confirm" if rescheduling else "confirm", **fields)


def rendered_receipt(h, state, *, rescheduling=False):
    return presentation.render_summary(qr_ai._summary(state, state["selected_start"], h.business, h.catalog,
        protocol="AG-SYNTHETIC"), confirmed=True, operation="reschedule" if rescheduling else "booking")


def independent_plan(monkeypatch, factual):
    monkeypatch.setattr(qr_ai, "_time_context", AsyncMock(return_value={}))
    monkeypatch.setattr(qr_ai, "interpret_plan", AsyncMock(return_value=UnderstandingPlan(intents=["confirm", "faq"])))
    monkeypatch.setattr(qr_ai, "compose_information", lambda *_, **__: InformationReply(factual, (), (), None))


@pytest.mark.asyncio
@pytest.mark.parametrize("rescheduling", [False, True])
async def test_mixed_confirmation_budgets_whole_receipt_before_any_effect(flow, monkeypatch, rescheduling):
    h = flow
    state = confirmation(h, large=True, rescheduling=rescheduling)
    factual = "F" * 500
    receipt = rendered_receipt(h, state, rescheduling=rescheduling)
    assert presentation.fits_message(receipt)
    assert len(factual) + 350 <= 1200  # The former fixed allowance incorrectly admitted this case.
    assert not presentation.fits_message(factual + "\n\n" + receipt)
    independent_plan(monkeypatch, factual)
    effect = AsyncMock(side_effect=AssertionError("Preflight must reject before the domain call"))
    monkeypatch.setattr(qr_ai, "_turn", effect)
    result = await qr_ai._multi_turn(h.tenant, "5567999999999", "Confirmo. Qual a política?", state,
        h.business, h.catalog, h.professionals, h.redis, h.key, False)
    effect.assert_not_awaited()
    assert result["step"] == state["step"] and h.state()["confirmation_requires_review"]
    assert not result.get("_verified_operation") and not h.calls["writes"]


@pytest.mark.asyncio
async def test_mixed_confirmation_with_complete_bounded_facts_preserves_normal_receipt(flow, monkeypatch):
    h = flow
    state = confirmation(h)
    receipt = rendered_receipt(h, state)
    independent_plan(monkeypatch, "Aceitamos Pix.")
    effect = AsyncMock(return_value={"answer": receipt, "step": "confirmed", "protocol": "AG-SYNTHETIC",
                                    "_verified_operation": True})
    monkeypatch.setattr(qr_ai, "_turn", effect)
    result = await qr_ai._multi_turn(h.tenant, "5567999999999", "Confirmo. Aceitam Pix?", state,
        h.business, h.catalog, h.professionals, h.redis, h.key, False)
    effect.assert_awaited_once()
    assert result["step"] == "confirmed" and result["_verified_operation"] is True
    assert "Aceitamos Pix." in result["answer"] and receipt in result["answer"]
    assert presentation.fits_message(result["answer"])


@pytest.mark.asyncio
async def test_optional_birthday_invitation_is_budgeted_before_mixed_confirmation(flow, monkeypatch):
    h = flow
    state = confirmation(h)
    maximum = presentation.render_summary(qr_ai._summary(state, state["selected_start"], h.business, h.catalog,
                                                        protocol="X" * 80), confirmed=True)
    factual = "F" * (1150 - presentation.message_units(maximum))
    monkeypatch.delenv("BIRTHDAY_COLLECTION_ENABLED", raising=False)
    assert qr_ai._confirmation_composition_fits(state, h.business, h.catalog, factual)
    monkeypatch.setenv("BIRTHDAY_COLLECTION_ENABLED", "1")
    assert not qr_ai._confirmation_composition_fits(state, h.business, h.catalog, factual)
    independent_plan(monkeypatch, factual)
    effect = AsyncMock(side_effect=AssertionError("An optional invitation cannot overflow after a write"))
    monkeypatch.setattr(qr_ai, "_turn", effect)
    result = await qr_ai._multi_turn(h.tenant, "5567999999999", "Confirmo. Qual a política?", state,
        h.business, h.catalog, h.professionals, h.redis, h.key, False)
    effect.assert_not_awaited()
    assert result["step"] == "confirm" and h.state()["confirmation_requires_review"]


@pytest.mark.asyncio
@pytest.mark.parametrize("information_first", [False, True])
async def test_unexpected_late_addition_keeps_verified_receipt_and_terminal_latch(flow, information_first):
    h = flow
    state = confirmation(h, large=True)
    receipt = rendered_receipt(h, state)
    verified = {"answer": receipt, "step": "confirmed", "protocol": "AG-SYNTHETIC", "_verified_operation": True}
    result = qr_ai._operation_reply(verified, "F" * 500, information_first=information_first)
    assert result == verified and result["answer"] == receipt
    assert "Nenhuma alteração foi confirmada" not in result["answer"]
    assert await qr_operation_receipt.save(h.redis, h.tenant, "5567999999999", "synthetic-completed", result,
                                           verified_operation=result["_verified_operation"])
    saved = json.loads(h.redis.data[qr_operation_receipt.key(h.tenant, "5567999999999")])
    assert saved["step"] == "confirmed" and saved["answer"] == receipt and saved["protocol"] == "AG-SYNTHETIC"


@pytest.mark.asyncio
async def test_late_presentation_growth_cannot_replace_verified_terminal_result(flow, monkeypatch):
    h = flow
    state = confirmation(h)
    receipt = rendered_receipt(h, state)
    verified = {"answer": receipt, "step": "confirmed", "protocol": "AG-SYNTHETIC", "_verified_operation": True}
    monkeypatch.setattr(qr_ai, "_turn", AsyncMock(return_value=verified))
    monkeypatch.setattr(presentation, "render_message", lambda body, *_, **__: body + "X" * 1201)
    result = await qr_ai.turn(h.tenant, "5567999999999", "sim", h.redis, h.namespace)
    assert result == verified and result["answer"] == receipt and presentation.fits_message(result["answer"])


@pytest.mark.asyncio
async def test_declining_reschedule_makes_no_claim_about_externally_changed_original(flow):
    h = flow
    state = confirmation(h, rescheduling=True)
    # The current reservation is deliberately not read: declining this request
    # grants no authority to claim that a previously displayed booking survives.
    result = await qr_ai.continue_reschedule(h.tenant, "5567999999999", "não", state,
        h.business, h.redis, h.key, False)
    assert result["step"] == "reschedule_cancelled" and h.state() == {}
    assert "não altera seus agendamentos" in result["answer"]
    assert "permanece" not in result["answer"] and not h.calls["writes"]


@pytest.mark.asyncio
async def test_concurrent_reschedule_conflict_does_not_assert_original_still_exists(flow, monkeypatch):
    h = flow
    state = confirmation(h, rescheduling=True)
    # Initial ownership read is still confirmed; the domain rejects after a
    # concurrent panel cancellation. Only an isolated fake effect is exercised.
    rejected = AsyncMock(side_effect=HTTPException(409, "synthetic-concurrent-cancellation"))
    monkeypatch.setattr(qr_ai, "reschedule_appointment", rejected)
    result = await qr_ai.continue_reschedule(h.tenant, "5567999999999", "sim", state,
        h.business, h.redis, h.key, False)
    rejected.assert_awaited_once()
    assert result["step"] == "reschedule_conflict" and "estado atual" in result["answer"]
    assert "permanece" not in result["answer"] and h.state()["reschedule_conflict_review"]
