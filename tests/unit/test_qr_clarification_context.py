"""Real QR turn routing with the shared synthetic backend; no external traffic."""
import json
from copy import deepcopy
from datetime import timedelta
from uuid import uuid4

import pytest

from app import qr_ai
from app.qr_understanding import UnderstandingPlan
from test_qr_multi_flow import flow


TRANSCRIBED_REQUEST = "Quero agendar corte barba com o Bruno na terça-feira, às 10h às 15h da tarde."


def _tuesday(h):
    return h.today + timedelta(days=(1 - h.today.weekday()) % 7)


async def _draft(h):
    result = await h.turn(TRANSCRIBED_REQUEST, preview=False)
    assert result["step"] == "time" and "hor" in result["answer"].lower()
    assert "Qual serviço" not in result["answer"] and "qual dia" not in result["answer"].lower()
    state = h.state()
    assert state["booking_draft"] is True and state["clarification_pending"] == ["time"]
    assert state["services"] == [str(h.cut), str(h.beard)]
    assert state["professional"] == str(h.bruno) and state["day"] == _tuesday(h).isoformat()
    assert state["slots"] == [] and 0 < qr_ai._draft_remaining(state) <= 300
    assert 0 < h.redis.ttls[h.key] <= 300
    assert not {"hold", "confirmation_summary", "confirmation_message_id", "selected_start", "pending_clock"} & state.keys()
    assert not h.calls["writes"] and not h.calls["names"] and not h.calls["reads"]
    return state


def _assert_prepared(h, result, *, services=None, hour=15):
    selected = services if services is not None else [h.cut, h.beard]
    assert result["step"] == "confirm"
    assert "Bruno" in result["answer"] and f"{hour}h" in result["answer"]
    assert "Qual serviço" not in result["answer"] and "qual dia" not in result["answer"].lower()
    assert h.calls["reads"][-1] == (h.bruno, selected, _tuesday(h))
    assert [row[0] for row in h.calls["writes"]] == ["hold"]
    assert h.calls["writes"][0][1] == selected and h.calls["writes"][0][2].hour == hour
    assert h.calls["writes"][0][3] == h.bruno
    assert h.state()["step"] == "confirm" and h.state()["hold"]
    assert not {"booking_draft", "clarification_pending", "clarification_expires_at"} & h.state().keys()


@pytest.mark.asyncio
async def test_transcribed_ambiguous_time_preserves_service_professional_day_until_natural_reply(flow):
    h = flow
    await _draft(h)
    result = await h.turn("Prefiro às 15 horas.", preview=False)
    _assert_prepared(h, result)
    assert "R$ 55" in result["answer"] and "Corte" in result["answer"] and "Barba" in result["answer"]


@pytest.mark.asyncio
async def test_new_ambiguous_request_cannot_reuse_old_hold_or_confirmation_authority(flow):
    h = flow
    h.seed("confirm", hold=str(uuid4()), confirmation_summary="Old synthetic summary",
        confirmation_message_id="old-summary", confirmation_expires_at=9999999999,
        selected_start="2026-10-20T10:00:00-04:00")
    await _draft(h)
    h.proposal["value"] = UnderstandingPlan(intents=["confirm"])
    result = await h.turn("SIM", preview=False)
    assert result["step"] != "confirmed" and not h.calls["writes"]
    assert h.state().get("hold") is None and h.state().get("confirmation_summary") is None


@pytest.mark.asyncio
async def test_exact_clock_reply_does_not_let_provider_reopen_previously_clear_service(flow):
    h = flow
    await _draft(h)
    h.proposal["value"] = UnderstandingPlan(intents=["booking"], ambiguities=["service"])
    async def forbidden_provider(**_):
        raise AssertionError("A clear clock reply must resolve the pending clock without a provider call")
    h.monkeypatch.setattr(qr_ai, "interpret_plan", forbidden_provider)
    result = await h.turn("Prefiro às 15 horas.", preview=False)
    _assert_prepared(h, result)
    assert len(h.calls["plans"]) == 1  # A plain clock resolves the pending clock locally.


@pytest.mark.asyncio
async def test_clock_reply_with_real_new_service_alternatives_keeps_service_ambiguity(flow):
    h = flow
    await _draft(h)
    h.proposal["value"] = UnderstandingPlan(intents=["booking"], ambiguities=["service"],
        requested_time="15:00", time_relation="exact")
    result = await h.turn("Prefiro às 15 horas, mas corte ou barba.", preview=False)
    assert result["step"] != "confirm" and not h.calls["writes"]
    assert h.state().get("booking_draft") is True
    assert "service" in h.state()["clarification_pending"] and not h.state().get("services")
    assert h.state()["day"] == _tuesday(h).isoformat() and h.state()["professional"] == str(h.bruno)


@pytest.mark.asyncio
@pytest.mark.parametrize("question,answer,intent", [
    ("Quanto custa corte e barba?", "R$ 55", "price"),
    ("Aceitam Pix?", "Aceitamos Pix", "faq"),
])
async def test_information_during_time_clarification_preserves_draft_without_writes(flow, question, answer, intent):
    h = flow
    before = await _draft(h)
    h.proposal["value"] = UnderstandingPlan(intents=[intent], query_service_ids=[h.cut, h.beard] if intent == "price" else [],
        faq_ids=["faq-0"] if intent == "faq" else [])
    result = await h.turn(question, preview=False)
    assert answer in result["answer"]
    after = h.state()
    for field in ("booking_draft", "services", "professional", "day", "clarification_pending", "clarification_expires_at"):
        assert after[field] == before[field]
    assert not h.calls["writes"] and not h.calls["names"]
    assert 0 < h.redis.ttls[h.key] <= qr_ai._draft_remaining(before) + 1
    h.proposal["value"] = None
    _assert_prepared(h, await h.turn("Prefiro às 15 horas.", preview=False))


@pytest.mark.asyncio
async def test_refused_hour_during_draft_never_creates_hold_or_confirms(flow):
    h = flow
    before = await _draft(h)
    await h.turn("Não às 15 horas.", preview=False)
    assert not h.calls["writes"] and not h.calls["names"]
    assert h.state().get("hold") is None and h.state().get("confirmation_summary") is None
    assert h.state().get("services") == before["services"] and h.state().get("day") == before["day"]


@pytest.mark.asyncio
async def test_corrected_hour_creates_only_new_proposal_never_confirms_previous_draft(flow):
    h = flow
    await _draft(h)
    result = await h.turn("Não às 10h, melhor às 15h.", preview=False)
    _assert_prepared(h, result)


@pytest.mark.asyncio
async def test_service_removal_during_clarification_preserves_other_fields_and_cannot_confirm(flow):
    h = flow
    await _draft(h)
    result = await h.turn("Quero só corte, às 15 horas.", preview=False)
    _assert_prepared(h, result, services=[h.cut])
    assert "R$ 35" in result["answer"]


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["catalog", "profile", "professional", "expired"])
async def test_old_draft_cannot_survive_changed_catalog_profile_professional_or_expiry(flow, change):
    h = flow
    await _draft(h)
    if change == "catalog":
        h.catalog[0]["price_cents"] = 9900
    elif change == "profile":
        h.business["profile"]["business_name"] = "Changed synthetic shop"
    elif change == "professional":
        h.professionals.remove(next(row for row in h.professionals if row["id"] == h.bruno))
    else:
        state = h.state()
        state["clarification_expires_at"] = 0
        h.redis.data[h.key] = json.dumps(state)
    result = await h.turn("Prefiro às 15 horas.", preview=False)
    assert result["step"] != "confirm" and not h.calls["writes"] and not h.calls["names"]
    assert h.state().get("booking_draft") is not True and h.state().get("hold") is None
    assert not h.calls["reads"]


@pytest.mark.asyncio
async def test_draft_redis_context_does_not_cross_phone_namespace(flow):
    h = flow
    original = deepcopy(await _draft(h))
    result = await qr_ai.turn(h.tenant, "5567999999998", "Prefiro às 15 horas.", h.redis,
        "preview:owner:other-contact", preview=False)
    assert result["step"] != "confirm" and not h.calls["writes"] and not h.calls["reads"]
    for field in ("services", "professional", "day", "clarification_pending", "clarification_expires_at"):
        assert h.state()[field] == original[field]


async def _corrected_price_draft(h):
    before = await _draft(h)
    result = await h.turn("Pera, melhor só corte às 16h30. Quanto fica?", preview=False)
    assert "Corte" in result["answer"] and "R$ 35" in result["answer"] and "16h30" in result["answer"]
    assert "Bruno" in result["answer"] and _tuesday(h).strftime("%d/%m") in result["answer"]
    assert "consult" in result["answer"].lower() and "ainda" in result["answer"].lower()
    assert not h.calls["reads"] and not h.calls["writes"] and not h.calls["names"]
    state = h.state()
    assert state["booking_draft"] is True and state["services"] == [str(h.cut)]
    assert state["professional"] == str(h.bruno) and state["day"] == before["day"]
    assert state["pending_clock"] == "16:30" and state["pending_relation"] == "exact"
    assert state["clarification_expires_at"] == before["clarification_expires_at"]
    assert not {"hold", "confirmation_summary", "confirmation_message_id", "selected_start"} & state.keys()
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize("continuation", ["Quero seguir com esse pedido", "Pode mostrar o resumo"])
async def test_price_correction_preserves_private_request_then_natural_resume_reads_grid_and_prepares_new_summary(flow, continuation):
    h = flow
    await _corrected_price_draft(h)
    result = await h.turn(continuation, preview=False)
    _assert_prepared(h, result, services=[h.cut], hour=16)
    assert "16h30" in result["answer"] and "R$ 35" in result["answer"]
    assert h.calls["writes"][0][2].minute == 30


@pytest.mark.asyncio
async def test_price_correction_never_turns_isolated_yes_into_confirmation_before_new_summary(flow):
    h = flow
    await _corrected_price_draft(h)
    h.proposal["value"] = UnderstandingPlan(intents=["confirm"])
    result = await h.turn("SIM", preview=False)
    assert result["step"] == "confirm" and not any(row[0] == "confirm" for row in h.calls["writes"]) and not h.calls["names"]
    assert [row[0] for row in h.calls["writes"]] == ["hold"] and h.state().get("hold") is not None
