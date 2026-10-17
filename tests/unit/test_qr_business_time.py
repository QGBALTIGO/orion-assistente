"""Natural business clocks; registered work resolves AM/PM, not occupied slots."""
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta
from uuid import uuid4

import pytest

from app import qr_ai
from app.qr_dialogue import choose_time
from app.qr_understanding import UnderstandingPlan, build_plan, clarification_for
from test_qr_multi_flow import flow


DAYTIME = [{"opens": "09:00", "closes": "18:00"}]
ZONE = "America/Campo_Grande"


@pytest.mark.parametrize("body,expected", [("3 e meia", "15:30"), ("3 e meio", "15:30"),
    ("três e meia", "15:30"), ("às3", "15:00"), ("às 3", "15:00"), ("às três e meia", "15:30")])
def test_human_clock_uses_registered_hours_with_no_provider_fact(body, expected):
    plan = build_plan(message=body, catalog=[], context={"step": "time", "hours": DAYTIME})
    assert plan.requested_time == expected and "time" not in plan.ambiguities


@pytest.mark.parametrize("hours,expected", [([{"opens": "01:00", "closes": "08:00"}], "03:30"),
    (DAYTIME, "15:30"), ([{"opens": "00:00", "closes": "20:00"}], None), ([], None)])
def test_time_period_is_derived_only_when_one_registered_interpretation_exists(hours, expected):
    plan = build_plan(message="3 e meia", catalog=[], context={"step": "time", "hours": hours})
    assert plan.requested_time == expected
    assert ("time" in plan.ambiguities) is (expected is None)


@pytest.mark.parametrize("body", ["às4mil", "as4mil", "às 4 mil"])
def test_asr_noise_does_not_become_a_clock_or_trust_a_model_guess(body):
    plan = build_plan(message=body, catalog=[], context={"step": "time", "hours": DAYTIME},
        proposal=UnderstandingPlan(intents=["booking"], requested_time="16:30", time_relation="exact"))
    assert plan.requested_time is None and "time" in plan.ambiguities
    assert "24 horas" not in clarification_for(plan)


def test_final_clear_clock_correction_can_supersede_prior_asr_noise():
    plan = build_plan(message="às4mil, não, melhor às 3 e meia", catalog=[], context={"step": "time", "hours": DAYTIME})
    assert plan.requested_time == "15:30" and "time" not in plan.ambiguities


@pytest.mark.parametrize("body", ["3 e meia", "3 e meio", "três e meia", "às 3 e meia", "às3 e meia"])
def test_registered_period_precedes_available_slot_selection(body):
    slots = ["2026-10-20T15:30:00-04:00"]
    selected = choose_time(body, slots, timezone=ZONE, business_hours=DAYTIME, numbered_menu=False)
    assert selected.kind == "selected" and selected.when == slots[0]
    ambiguous = choose_time(body, slots, timezone=ZONE,
        business_hours=[{"opens": "00:00", "closes": "20:00"}], numbered_menu=False)
    assert ambiguous.kind == "uncertain"  # Morning being occupied is no AM/PM proof.
    missing = choose_time(body, ["2026-10-20T16:00:00-04:00"], timezone=ZONE, business_hours=DAYTIME, numbered_menu=False)
    assert missing.kind == "unavailable" and missing.when is None


def test_bare_three_is_clock_in_grouped_hours_and_option_only_in_a_numbered_menu():
    slots = ["2026-10-20T09:00:00-04:00", "2026-10-20T09:30:00-04:00",
        "2026-10-20T10:00:00-04:00", "2026-10-20T15:00:00-04:00"]
    assert choose_time("3", slots, presented_slots=slots, timezone=ZONE, business_hours=DAYTIME, numbered_menu=False).when == slots[-1]
    assert choose_time("3", slots, presented_slots=slots, timezone=ZONE, business_hours=DAYTIME, numbered_menu=True).when == slots[2]
    assert build_plan(message="3", catalog=[], context={"step": "menu", "hours": DAYTIME}).requested_time is None


@pytest.fixture
def registered(flow, monkeypatch):
    h = flow
    h.business["use_working_hours"] = True
    h.business["profile"]["hours"] = []
    h.hours_reads = []
    async def actual(tenant, professional_ids, day):
        assert tenant == h.tenant and set(professional_ids) <= {h.bruno, h.breno}
        h.hours_reads.append((list(professional_ids), day))
        return DAYTIME
    monkeypatch.setattr(qr_ai, "working_hours_for", actual)
    return h


async def _message(h, body, identity):
    token = qr_ai._message_identity.set(identity)
    try:
        return await h.turn(body, preview=False)
    finally:
        qr_ai._message_identity.reset(token)


@pytest.mark.asyncio
async def test_actual_two_audios_noise_then_natural_half_hour_preserves_day_professional_and_new_consent(registered):
    h = registered
    first = await _message(h, "Quero agendar corte de cabelo com Bruno na terça-feira às 15 horas da tarde.", "synthetic-clear-audio")
    assert first["step"] == "confirm" and "15h" in first["answer"] and "R$ 35,00" in first["answer"]
    initial = h.state()
    second = await _message(h, "Espera, melhor só corte as4mil. Quanto fica?", "synthetic-noisy-correction")
    state = h.state()
    assert "R$ 35,00" in second["answer"] and "24 horas" not in second["answer"] and "Qual horário" in second["answer"]
    assert state["booking_draft"] and state["clarification_pending"] == ["time"]
    assert state["services"] == [str(h.cut)] and state["professional"] == str(h.bruno) and state["day"] == initial["day"]
    assert state["clarification_expires_at"] == initial["confirmation_expires_at"]
    assert not set(state) & {"hold", "selected_start", "pending_clock", "confirmation_summary", "confirmation_message_id"}
    assert [row[0] for row in h.calls["writes"]] == ["hold"] and len(h.calls["reads"]) == 1
    result = await _message(h, "3 e meia", "synthetic-half-hour")
    assert result["step"] == "confirm" and "15h30" in result["answer"] and "R$ 35,00" in result["answer"]
    assert [row[0] for row in h.calls["writes"]] == ["hold", "hold"]
    assert h.calls["writes"][-1][1] == [h.cut] and h.calls["writes"][-1][2].strftime("%H:%M") == "15:30"
    assert h.state()["professional"] == initial["professional"] and h.state()["day"] == initial["day"]
    assert h.state()["confirmation_message_id"] == "synthetic-half-hour"
    await _message(h, "Pode confirmar", "synthetic-after-new-summary")
    assert [row[0] for row in h.calls["writes"]] == ["hold", "hold", "confirm"]
    assert h.hours_reads[0] == ([h.bruno], date.fromisoformat(initial["day"]))


@pytest.mark.asyncio
async def test_price_correction_to_half_hour_is_only_a_draft_until_a_new_review(registered):
    h = registered
    await _message(h, "Quero agendar corte e barba com Bruno na terça-feira às 15 horas.", "synthetic-first")
    initial = h.state()
    result = await _message(h, "Pera, melhor só corte às 3 e meia. Quanto fica?", "synthetic-price-half-hour")
    assert "R$ 35,00" in result["answer"] and "15h30" in result["answer"] and h.state()["pending_clock"] == "15:30"
    assert h.state()["day"] == initial["day"] and h.state()["professional"] == initial["professional"]
    assert [row[0] for row in h.calls["writes"]] == ["hold"] and len(h.calls["reads"]) == 1
    reviewed = await _message(h, "Sim", "synthetic-new-review")
    assert reviewed["step"] == "confirm" and "15h30" in reviewed["answer"]
    assert [row[0] for row in h.calls["writes"]] == ["hold", "hold"]


@pytest.mark.asyncio
async def test_bare_three_after_actual_grouped_hours_uses_business_clock_not_third_row(registered):
    h = registered
    first = await _message(h, "Quero agendar corte com Bruno amanhã", "synthetic-hours-menu")
    assert first["step"] == "time" and h.state()["slot_menu_numbered"] is False
    result = await _message(h, "3", "synthetic-three-clock")
    assert result["step"] == "confirm" and h.calls["writes"][-1][2].hour == 15


@pytest.mark.asyncio
async def test_saturday_short_workday_does_not_invent_three_am_or_afternoon(registered, monkeypatch):
    h = registered
    async def saturday(tenant, people, day):
        assert tenant == h.tenant and people == [h.bruno] and day.weekday() == 5
        return [{"opens": "09:00", "closes": "13:00"}]
    monkeypatch.setattr(qr_ai, "working_hours_for", saturday)
    reply = await _message(h, "Quero agendar corte com Bruno no próximo sábado às 3 e meia", "synthetic-saturday-half-hour")
    assert reply["step"] != "confirm" and "9h–13h" in reply["answer"] and "24 horas" not in reply["answer"]
    assert not h.calls["writes"] and not h.calls["reads"] and not h.state().get("pending_clock")


@pytest.mark.asyncio
@pytest.mark.parametrize("correction", ["melhor", "não, melhor", "quer dizer", "na verdade"])
async def test_final_half_hour_correction_without_repeated_as_wins_before_real_hold(registered, correction):
    h = registered
    h.proposal["value"] = UnderstandingPlan(intents=["booking"], requested_time="16:30", time_relation="exact")
    result = await _message(h, "Quero agendar corte com Bruno na terça às 3 e meia, "+correction+" 4 e meia", "synthetic-final-half-hour")
    assert result["step"] == "confirm" and "16h30" in result["answer"]
    assert len(h.calls["writes"]) == 1 and h.calls["writes"][0][2].strftime("%H:%M") == "16:30"


@pytest.mark.asyncio
async def test_two_half_hour_alternatives_are_not_a_final_correction_or_hold(registered):
    h = registered
    result = await _message(h, "Quero agendar corte com Bruno na terça às 3 e meia ou 4 e meia", "synthetic-half-hour-alternatives")
    assert result["step"] != "confirm" and not h.calls["writes"]


@pytest.mark.asyncio
@pytest.mark.parametrize("final_clock", ["4 mil", "4mil"])
async def test_noisy_final_clock_without_as_invalidates_first_time_and_preserves_other_facts(registered, final_clock):
    h = registered
    h.proposal["value"] = UnderstandingPlan(intents=["booking"], requested_time="16:30", time_relation="exact")
    result = await _message(h, "Quero agendar corte com Bruno na terça às 3 e meia, melhor "+final_clock, "synthetic-noisy-final-clock")
    assert result["step"] != "confirm" and not h.calls["writes"] and not h.calls["reads"]
    assert h.state()["clarification_pending"] == ["time"] and not h.state().get("pending_clock")
    assert h.state()["services"] == [str(h.cut)] and h.state()["professional"] == str(h.bruno) and h.state()["day"]
    assert "Qual horário" in result["answer"] and "24 horas" not in result["answer"]


@pytest.mark.asyncio
async def test_prior_noisy_clock_then_final_corrected_half_hour_prepares_only_last_time(registered):
    h = registered
    result = await _message(h, "Quero agendar corte com Bruno na terça às 4 mil, melhor 3 e meia", "synthetic-noisy-then-clear")
    assert result["step"] == "confirm" and "15h30" in result["answer"]
    assert len(h.calls["writes"]) == 1 and h.calls["writes"][0][2].strftime("%H:%M") == "15:30"


@pytest.mark.asyncio
async def test_working_hours_read_is_scoped_bounded_and_does_not_read_availability(monkeypatch):
    tenant, professional = uuid4(), uuid4()
    calls = []
    class Result:
        def mappings(self): return self
        def all(self): return [{"start_time": time(9), "end_time": time(18)}]
    class Session:
        async def __aenter__(self): return self
        async def __aexit__(self, *_): pass
        async def execute(self, statement, params):
            calls.append((str(statement), params))
            return Result()
    @asynccontextmanager
    async def scoped(db, target):
        assert target == tenant
        yield db
    monkeypatch.setattr(qr_ai, "get_session_factory", lambda: Session)
    monkeypatch.setattr(qr_ai, "tenant_transaction", scoped)
    actual = await qr_ai.working_hours_for(tenant, [professional], date(2026, 10, 20))
    assert actual == DAYTIME and len(calls) == 1
    sql, params = calls[0]
    assert "working_hours" in sql and "h.tenant_id=:tenant" in sql and "h.professional_id=ANY(:professionals)" in sql
    assert "LIMIT 210" in sql and "appointments" not in sql and params == {"tenant": tenant, "professionals": [professional], "weekday": 1}
