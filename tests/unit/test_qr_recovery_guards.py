"""Operation/context boundaries, including unavailable or malformed real reads.

All fixtures are synthetic; no production endpoint, recipient or booking is used.
"""
from datetime import datetime
import json
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import HTTPException
import pytest
from sqlalchemy.exc import OperationalError

from app import qr_ai
from test_qr_multi_flow import flow
from test_qr_cancellation import cancellation, routed_cancellation


async def identified(h, message, identity):
    token = qr_ai._message_identity.set(identity)
    try:
        return await h.turn(message, preview=False)
    finally:
        qr_ai._message_identity.reset(token)


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [HTTPException(503, "synthetic"), TimeoutError(), ConnectionError(),
    OperationalError("synthetic statement", {}, Exception("synthetic unavailable"))],
    ids=["backend503", "deadline", "connection", "database-driver"])
async def test_read_failure_never_becomes_no_free_professionals_or_partial_success(flow, monkeypatch, error):
    h = flow
    async def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(qr_ai, "list_slots", fail)
    await identified(h, "Quem está disponível segunda?", "synthetic-query")
    reply = await identified(h, "Cabelo", "synthetic-service")
    assert "Não consegui consultar" in reply["answer"]
    assert "Não encontrei horários" not in reply["answer"]
    assert "last_availability" not in h.state() and h.state()["availability_query"]["waiting_for"] == "retry"
    assert not h.calls["writes"]


@pytest.mark.asyncio
@pytest.mark.parametrize("result", [None, {}, {"slots": None}, {"slots": []},
    {"slots": [], "price_cents": True, "duration_min": 40},
    {"slots": [], "price_cents": 3500, "duration_min": None}],
    ids=["unknown", "missing-grid", "unknown-grid", "missing-verified-price", "boolean-price", "unknown-duration"])
async def test_unknown_query_data_cannot_be_presented_as_verified_empty_agenda(flow, monkeypatch, result):
    h = flow
    async def malformed(*args, **kwargs):
        return result
    monkeypatch.setattr(qr_ai, "list_slots", malformed)
    await identified(h, "Quem está disponível segunda?", "synthetic-query")
    reply = await identified(h, "Cabelo", "synthetic-service")
    assert "Não consegui consultar" in reply["answer"] and "Não encontrei horários" not in reply["answer"]
    assert not h.calls["writes"] and not h.state().get("last_availability")


@pytest.mark.asyncio
async def test_greeting_after_cancel_proposal_starts_service_question_without_resurrecting_target(routed_cancellation):
    h = routed_cancellation
    await h.turn("Quero cancelar minha reserva", "synthetic-cancel-summary")
    assert h.state()["step"] == "cancel_confirm"
    greeting = await h.turn("Oi", "synthetic-new-greeting")
    assert greeting["step"] == "menu" and h.state()["service_reply_expected"]
    assert not set(h.state()) & {"appointment", "expected_snapshot", "confirmation_summary", "confirmation_message_id"}
    service = await h.turn("Cabelo", "synthetic-new-service")
    assert "R$ 35,00" in service["answer"] and "cancelamento" not in service["answer"]
    assert h.state()["services"] == [str(h.service)] and "cancel" not in h.events


@pytest.mark.asyncio
async def test_today_question_is_factual_in_tenant_zone_and_cannot_authorize_pending_cancel(routed_cancellation):
    h = routed_cancellation
    await h.turn("Quero cancelar minha reserva", "synthetic-cancel-summary")
    deadline = h.state()["confirmation_expires_at"]
    reply = await h.turn("Que dia é hoje?", "synthetic-today-question")
    today = datetime.now(ZoneInfo(h.business["timezone"]))
    assert today.strftime("%d/%m/%Y") in reply["answer"] and "Quer revisar" not in reply["answer"]
    assert h.state()["confirmation_requires_review"] and h.state()["confirmation_expires_at"] == deadline
    assert "cancel" not in h.events and "_verified_operation" not in reply


@pytest.mark.asyncio
@pytest.mark.parametrize("decline", ["Não", "Não confirma", "Cancela", "Desisto"])
async def test_closing_proposal_then_no_no_sm_never_reuses_its_authority(flow, decline):
    h = flow
    h.seed("confirm", hold=str(uuid4()), confirmation_summary="Synthetic reviewed proposal",
        confirmation_message_id="synthetic-summary", confirmation_expires_at=datetime.now().timestamp()+200)
    closed = await identified(h, decline, "synthetic-decline")
    assert closed["step"] == "request_closed" and not h.state().get("hold")
    for index, body in enumerate(("Não", "Não", "Sm")):
        reply = await identified(h, body, "synthetic-after-close-" + str(index))
        assert reply["step"] not in {"confirmed", "cancelled", "rescheduled"}
        assert not h.state().get("confirmation_message_id") and not h.calls["writes"]


@pytest.mark.asyncio
async def test_legacy_slot_read_failure_cannot_claim_that_day_has_no_free_times(flow, monkeypatch):
    h = flow; h.seed("date")
    async def failed(*args, **kwargs):
        raise HTTPException(503, "synthetic unavailable")
    monkeypatch.setattr(qr_ai, "list_slots", failed)
    text, state = await qr_ai.offer_slots(h.tenant, h.state(), False)
    assert "Não consegui consultar" in text and "Não há horários" not in text
    assert state["day"] == h.tomorrow.isoformat() and not h.calls["writes"]
