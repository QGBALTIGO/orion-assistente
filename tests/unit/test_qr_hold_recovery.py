"""QR hold deadlines/identities and contextual recovery; synthetic backends only."""
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app import qr_ai
from test_qr_multi_flow import flow
from test_qr_followup_context import identified


@pytest.mark.asyncio
async def test_preparing_summary_without_message_identity_never_reaches_hold_backend(flow, monkeypatch):
    h = flow
    state = h.seed("time", price=3500, duration=40)

    async def forbidden(*args, **kwargs):
        raise AssertionError("An unidentified message cannot create or confirm a hold")

    monkeypatch.setattr(qr_ai, "book_appointment", forbidden)
    monkeypatch.setattr(qr_ai, "confirm_hold", forbidden)
    token = qr_ai._message_identity.set(None)
    try:
        result = await qr_ai.prepare_booking_summary(h.tenant, "5567999999999", state,
            f"{h.tomorrow}T15:30:00-04:00", h.business, h.catalog, h.redis, h.key, False)
    finally:
        qr_ai._message_identity.reset(token)
    assert result["step"] == "no_summary"
    assert not h.calls["writes"] and not h.calls["reads"] and not h.calls["names"]
    assert not h.state().get("hold") and not h.state().get("confirmation_summary")


@pytest.mark.asyncio
async def test_yes_to_current_draft_without_message_identity_cannot_prepare_or_confirm(flow, monkeypatch):
    h = flow
    await identified(h, "Quero agendar corte amanhã às15h com Bruno", identity="identified-original")
    offered = await identified(h, "Pera, melhor só corte às15h30. Quanto fica?", identity="identified-draft-offer")
    assert "Posso verificar e mostrar um novo resumo?" in offered["answer"]
    assert h.state()["draft_review_message_id"] == "identified-draft-offer" and not h.state().get("hold")
    h.calls["writes"].clear(); h.calls["reads"].clear(); h.calls["names"].clear()

    async def forbidden(*args, **kwargs):
        raise AssertionError("An unidentified yes cannot accept the draft review offer")

    monkeypatch.setattr(qr_ai, "book_appointment", forbidden)
    monkeypatch.setattr(qr_ai, "confirm_hold", forbidden)
    token = qr_ai._message_identity.set(None)
    try:
        # Bypass flow.turn, whose normal-channel fixture supplies a fresh identity.
        result = await qr_ai.turn(h.tenant, "5567999999999", "SIM", h.redis, h.namespace, preview=False)
    finally:
        qr_ai._message_identity.reset(token)
    assert result["step"] != "confirm" and result["step"] != "confirmed"
    assert not h.calls["writes"] and not h.calls["reads"] and not h.calls["names"]
    assert h.state()["booking_draft"] and not h.state().get("hold") and not h.state().get("confirmation_summary")


@pytest.mark.asyncio
async def test_expired_confirmation_and_repeated_fresh_yes_never_create_replacement_authority(flow, monkeypatch):
    h = flow
    h.seed("confirm", hold=str(uuid4()), price=3500, duration=40,
        confirmation_summary="Expired synthetic summary", selected_start=f"{h.tomorrow}T15:30:00-04:00",
        confirmation_expires_at=datetime.now().timestamp()-1)

    async def forbidden(*args, **kwargs):
        raise AssertionError("A stale confirmation cannot create or confirm a new proposal")

    monkeypatch.setattr(qr_ai, "book_appointment", forbidden)
    monkeypatch.setattr(qr_ai, "confirm_hold", forbidden)
    for identity in ("first-late-yes", "second-late-yes"):
        result = await identified(h, "SIM", identity=identity)
        assert result["step"] not in {"confirm", "confirmed"}
        assert not h.state().get("hold") and not h.state().get("confirmation_message_id")
    assert h.state()["services"] == [str(h.cut)] and h.state()["professional"] == str(h.bruno)
    assert h.state()["day"] == h.tomorrow.isoformat() and not h.calls["writes"] and not h.calls["names"]


@pytest.mark.asyncio
async def test_new_message_identity_never_reuses_global_facts_key_and_replay_keeps_same_key(flow, monkeypatch):
    h = flow; keys = []
    async def book(*args, **kwargs):
        keys.append(args[7])
        return h.hold_result(args[2], args[3], args[6])
    monkeypatch.setattr(qr_ai, "book_appointment", book)
    for message_id in ("proposal-one", "proposal-one", "proposal-two"):
        state = h.seed("time", price=3500, duration=40)
        token = qr_ai._message_identity.set(message_id)
        try:
            result = await qr_ai.prepare_booking_summary(h.tenant, "5567999999999", state,
                f"{h.tomorrow}T15:30:00-04:00", h.business, h.catalog, h.redis, h.key, False)
        finally:
            qr_ai._message_identity.reset(token)
        assert result["step"] == "confirm"
    assert keys[0] == keys[1] and keys[2] != keys[0]


@pytest.mark.asyncio
async def test_summary_uses_original_backend_deadline_without_renewing_short_lived_hold(flow, monkeypatch):
    h = flow; expires = datetime.now(h.zone)+timedelta(seconds=21)
    async def book(*args, **kwargs):
        return {**h.hold_result(args[2], args[3], args[6]), "hold_expires_at": expires}
    monkeypatch.setattr(qr_ai, "book_appointment", book)
    reply = await identified(h, "Quero agendar corte amanhã às15h30 com Bruno")
    assert reply["step"] == "confirm" and h.state()["confirmation_expires_at"] == expires.timestamp()
    assert 1 <= h.redis.ttls[h.key] <= 21


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["expired", "cancelled", "confirmed", "missing_metadata", "missing_snapshot", "price_changed", "name_changed", "duration_changed"])
async def test_stale_or_unverified_backend_result_never_becomes_fresh_summary(flow, monkeypatch, invalid):
    h = flow
    async def book(*args, **kwargs):
        if invalid == "missing_metadata":
            return {"id": uuid4()}
        result = h.hold_result(args[2], args[3], args[6])
        if invalid == "expired": result["hold_expires_at"] -= timedelta(minutes=10)
        elif invalid in {"cancelled", "confirmed"}: result["status"] = invalid
        elif invalid == "missing_snapshot": result.pop("expected_snapshot")
        elif invalid == "price_changed": result["total_cents"] = 4500
        elif invalid == "name_changed": result["items"][0]["name_snapshot"] = "Changed actual service"
        elif invalid == "duration_changed": result["end_at"] += timedelta(minutes=10)
        return result
    monkeypatch.setattr(qr_ai, "book_appointment", book)
    reply = await identified(h, "Quero agendar corte amanhã às15h30 com Bruno")
    assert reply["step"] != "confirm" and not h.state().get("hold") and not h.state().get("confirmation_summary")
    assert h.state()["services"] == [str(h.cut)] and h.state()["professional"] == str(h.bruno)
    assert h.state()["day"] == h.tomorrow.isoformat() and h.state()["booking_recovery"]
    assert not h.calls["writes"]


@pytest.mark.asyncio
async def test_failed_confirmation_keeps_facts_requeries_and_cannot_reuse_old_sim(flow, monkeypatch):
    h = flow
    await identified(h, "Quero agendar corte amanhã às15h30 com Bruno", identity="first-summary")
    prior = h.state(); h.calls["writes"].clear(); h.calls["reads"].clear()
    async def conflict(*args, **kwargs):
        assert kwargs == {"customer_phone": "+5567999999999", "expected_snapshot": "a"*64}
        raise HTTPException(409, "Hold expirado")
    monkeypatch.setattr(qr_ai, "confirm_hold", conflict)
    reply = await identified(h, "SIM", identity="first-consent")
    assert reply["step"] == "time" and "Bruno" in reply["answer"] and "R$ 35,00" in reply["answer"]
    assert h.state()["services"] == prior["services"] and h.state()["day"] == prior["day"]
    assert h.state()["professional"] == prior["professional"] and h.state()["price"] == 3500
    assert not any(h.state().get(key) for key in ("hold", "hold_snapshot", "confirmation_summary", "confirmation_message_id", "selected_start"))
    assert len(h.calls["reads"]) == 1 and not h.calls["writes"]
    h.calls["reads"].clear()
    alternatives = await identified(h, "Qual outro horário disponível", identity="ask-alternative")
    assert alternatives["step"] == "time" and len(h.calls["reads"]) == 1
    assert h.state()["services"] == prior["services"] and h.state()["day"] == prior["day"]
    assert "Qual serviço" not in alternatives["answer"] and not h.calls["writes"]
    blocked = await identified(h, "SIM", identity="stale-consent")
    assert blocked["step"] != "confirm" and not h.calls["writes"] and not h.state().get("hold")


@pytest.mark.asyncio
async def test_failed_confirmation_and_failed_grid_still_keep_context_without_authority(flow, monkeypatch):
    h = flow
    h.seed("confirm", hold=str(uuid4()), price=3500, duration=40, confirmation_summary="Synthetic summary",
        confirmation_expires_at=datetime.now().timestamp()+120)
    async def failure(*args, **kwargs): raise HTTPException(503, "synthetic")
    monkeypatch.setattr(qr_ai, "confirm_hold", failure); monkeypatch.setattr(qr_ai, "list_slots", failure)
    reply = await identified(h, "SIM")
    assert "Não consegui consultar" in reply["answer"] and h.state()["booking_recovery"]
    assert h.state()["professional"] == str(h.bruno) and h.state()["day"] == h.tomorrow.isoformat()
    assert h.state()["services"] == [str(h.cut)] and not h.state().get("hold") and not h.calls["writes"]


@pytest.mark.asyncio
async def test_price_correction_preserves_private_own_hold_reference_but_not_consent(flow, monkeypatch):
    h = flow; seen = []
    original_book = h.book; original_slots = h.slots
    async def book(*args, **kwargs):
        seen.append(("book", kwargs)); return await original_book(*args, **kwargs)
    async def slots(*args, **kwargs):
        seen.append(("slots", kwargs)); return await original_slots(*args, **kwargs)
    monkeypatch.setattr(qr_ai, "book_appointment", book); monkeypatch.setattr(qr_ai, "list_slots", slots)
    await identified(h, "Quero agendar corte amanhã às15h com Bruno", identity="first-summary")
    old = h.state(); seen.clear(); h.calls["writes"].clear()
    answer = await identified(h, "Pera, melhor só corte às15h30. Quanto fica?", identity="correction-price")
    assert "R$ 35,00" in answer["answer"] and not h.calls["writes"] and not seen
    assert not h.state().get("hold") and not h.state().get("confirmation_summary")
    assert h.state()["replacement_hold"]["id"] == old["hold"]
    next_summary = await identified(h, "SIM", identity="new-summary")
    assert next_summary["step"] == "confirm" and "15h30" in next_summary["answer"]
    expected_id = str(next(kwargs["replace_hold_id"] for event, kwargs in seen if event == "book"))
    assert expected_id == old["hold"]
    assert next(kwargs["customer_phone"] for event, kwargs in seen if event == "slots") == "+5567999999999"
    assert [row[0] for row in h.calls["writes"]] == ["hold"] and not h.state().get("replacement_hold")


@pytest.mark.asyncio
async def test_preview_deadline_and_correction_never_reach_real_hold_backend(flow, monkeypatch):
    h = flow
    async def forbidden(*args, **kwargs): raise AssertionError("Preview cannot mutate a real hold")
    monkeypatch.setattr(qr_ai, "book_appointment", forbidden); monkeypatch.setattr(qr_ai, "confirm_hold", forbidden)
    reply = await identified(h, "Quero corte amanhã às15h30 com Bruno", preview=True)
    assert reply["step"] == "confirm" and h.state()["hold"] == "preview"
    finished = await identified(h, "SIM", identity="next-preview", preview=True)
    assert finished["step"] == "preview_complete" and not h.calls["writes"]


@pytest.mark.asyncio
async def test_incomplete_readonly_query_remembers_final_clock_through_price_and_professional_reply(flow):
    from app.qr_understanding import UnderstandingPlan
    h = flow
    monday = qr_ai.requested_day("segunda", str(h.zone)).isoformat()
    h.seed("menu", services=[], professional=None, availability_query={"services": [str(h.cut)], "day": monday, "waiting_for": "professional"})
    h.proposal["value"] = UnderstandingPlan(intents=["price"], query_service_ids=[h.cut], ambiguities=["professional"])
    quoted = await identified(h, "Melhor às quatro da tarde, só corte. Quanto fica?", identity="query-price-clock")
    assert "R$ 35,00" in quoted["answer"] and h.state()["availability_query"]["pending_clock"] == "16:00"
    assert h.state()["availability_query"]["day"] == monday and not h.calls["reads"] and not h.calls["writes"]
    answer = await identified(h, "Bruno", identity="query-professional")
    assert all(value in answer["answer"] for value in ("16h", "Bruno", "R$ 35,00", "40 min"))
    assert "08h" not in answer["answer"] and "09h" not in answer["answer"]
    assert h.calls["reads"] == [(h.bruno, [h.cut], datetime.fromisoformat(monday).date())]
    assert h.state()["last_availability"]["day"] == monday and not h.state().get("services")
    assert not h.calls["writes"] and not h.state().get("hold")


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [False, True])
async def test_query_final_clock_without_slot_uses_real_nearby_choices_and_failure_never_fakes_zero(flow, monkeypatch, failure):
    h = flow
    h.seed("menu", services=[], professional=None, availability_query={"services": [str(h.cut)], "day": h.tomorrow.isoformat(),
        "pending_clock": "16:00", "pending_relation": "exact", "waiting_for": "professional"})
    async def slots(*args, **kwargs):
        if failure: raise HTTPException(503, "synthetic")
        return {"slots": [f"{h.tomorrow}T15:30:00-04:00", f"{h.tomorrow}T16:30:00-04:00"]}
    monkeypatch.setattr(qr_ai, "list_slots", slots)
    reply = await identified(h, "Bruno")
    if failure:
        assert "Não consegui consultar" in reply["answer"] and h.state()["availability_query"]["pending_clock"] == "16:00"
    else:
        assert "horário pedido não está disponível" in reply["answer"] and "15h30" in reply["answer"] and "16h30" in reply["answer"]
        assert "R$" not in reply["answer"] and "*Duração:*" not in reply["answer"]
    assert not h.calls["writes"] and not h.state().get("hold")


@pytest.mark.asyncio
async def test_incomplete_clock_query_still_checks_appointment_capability_before_read(flow):
    h = flow; h.business["profile"]["capabilities"] = ["catalog_answers"]
    h.seed("menu", services=[], professional=None, availability_query={"services": [str(h.cut)], "day": h.tomorrow.isoformat(), "waiting_for": "professional"})
    reply = await identified(h, "Melhor às quatro da tarde, só corte. Quanto fica?")
    assert reply["step"] == "capability_unavailable" and not h.calls["reads"] and not h.calls["writes"]
