"""Explicit current-booking references must not become remembered authority."""
from datetime import datetime, timedelta
import json
from uuid import uuid4

import pytest

from app import qr_ai, qr_memory, qr_profile
from app.qr_understanding import build_plan, same_professional_reference
from test_qr_multi_flow import flow  # noqa: F401

BODY = "Quero remarcar meu corte de amanhã para as 11h15, com o mesmo profissional."


@pytest.mark.parametrize("staff", ["mesmo profissional", "mesmo barbeiro", "mesma profissional", "mesma barbeira", "mesmo dentista"])
def test_explicit_staff_reference_needs_factual_context(flow, staff):
    h = flow
    message = BODY.replace("mesmo profissional", staff)
    assert same_professional_reference(message)
    absent = build_plan(message=message, catalog=h.catalog, professionals=h.professionals,
        context={"hours": [{"opens": "08:00", "closes": "20:00"}]}, reference_date=h.today)
    assert absent.professional_id is None and "professional" in absent.ambiguities
    factual = build_plan(message=message, catalog=h.catalog, professionals=h.professionals,
        context={"professional_id": str(h.bruno), "hours": [{"opens": "08:00", "closes": "20:00"}]}, reference_date=h.today)
    assert factual.professional_id == h.bruno and "professional" not in factual.ambiguities


@pytest.mark.parametrize("phrase", [
    "com a mesma pessoa", "com o de sempre", "sem preferência", "com qualquer um",
    "não com o mesmo profissional", "sem o mesmo profissional", "com o mesmo profissional ou Breno",
    "com o mesmo profissional se ele puder", "talvez com o mesmo profissional",
    "com o mesmo profissional, mas só se estiver livre", "com o mesmo profissional para meu filho",
    "para minha mãe, com a mesma profissional",
    "com o mesmo profissional desde que esteja disponível", "em vez do mesmo profissional",
    "com o mesmo profissional contanto que seja possível", "exceto o mesmo profissional",
])
def test_habit_indifference_beneficiary_and_conditions_are_not_current_staff_proof(phrase):
    assert not same_professional_reference("Quero remarcar amanhã às 11h15 " + phrase)


@pytest.mark.parametrize("phrase", [
    "não com o mesmo profissional", "com o mesmo profissional ou Breno",
    "com o mesmo profissional se ele puder", "talvez com o mesmo profissional",
    "com o mesmo profissional ou o outro profissional", "com o mesmo profissional, com Zeca",
    "com o mesmo profissional desde que esteja disponível", "em vez do mesmo profissional",
])
def test_uncertain_reference_stays_ambiguous_even_with_a_context_id(flow, phrase):
    h = flow
    plan = build_plan(message="Quero remarcar corte amanhã às 11h15 " + phrase,
        catalog=h.catalog, professionals=h.professionals,
        context={"professional_id": str(h.bruno)}, reference_date=h.today)
    assert plan.professional_id is None and "professional" in plan.ambiguities


def finished(h):
    retained = qr_memory.retain_dialogue({"customer_name": "Alex", "professional": str(h.bruno),
        "services": [str(h.cut)]}, {"step": "confirmed", "_verified_operation": True, "protocol": "SYNTHETIC"},
        now=datetime.now().timestamp())
    assert "professional" not in retained and "services" not in retained
    h.redis.data[h.key] = json.dumps(retained)
    h.redis.data[h.key + ":catalog-context"] = qr_profile.catalog_revision(h.business, h.catalog, h.professionals)


def owned_facts(h, monkeypatch, *, status="confirmed", past=False):
    when = datetime.combine(h.tomorrow, datetime.min.time(), h.zone).replace(hour=10, minute=15)
    if past:
        when -= timedelta(days=3)
    async def current(tenant, appointment, phone):
        assert tenant == h.tenant and appointment == h.appointment and phone == "5567999999999"
        return {"id": h.appointment, "status": status, "professional_id": h.bruno, "services": [h.cut], "start_at": when}
    monkeypatch.setattr(qr_ai, "appointment_services", current)
    return when


@pytest.mark.asyncio
async def test_unique_owned_current_booking_supplies_staff_after_terminal_memory_is_cleared(flow, monkeypatch):
    h = flow
    finished(h)
    owned_facts(h, monkeypatch)
    result = await h.turn(BODY.replace("11h15", "15:00"), preview=False)
    assert result["step"] == "reschedule_confirm", result
    assert h.state()["professional"] == str(h.bruno) and h.state()["appointment"] == str(h.appointment)
    assert h.calls["reads"] and not h.calls["writes"] and not h.calls["names"]
    assert len(h.calls["plans"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status,past", [("cancelled", False), ("completed", False), ("confirmed", True)])
async def test_selected_reschedule_target_is_revalidated_before_resolving_staff(flow, monkeypatch, status, past):
    h = flow
    h.seed("reschedule_day", appointment=str(h.appointment))
    owned_facts(h, monkeypatch, status=status, past=past)
    result = await h.turn(BODY, preview=False)
    assert result["step"] == ("no_reservation" if past else "appointment_changed"), result
    assert not h.calls["reads"] and not h.calls["writes"] and not h.calls["names"]


@pytest.mark.asyncio
async def test_unowned_selected_target_never_uses_a_remembered_staff_id(flow, monkeypatch):
    h = flow
    h.seed("reschedule_day", appointment=str(h.appointment))
    async def absent(*args):
        return None
    monkeypatch.setattr(qr_ai, "appointment_services", absent)
    result = await h.turn(BODY, preview=False)
    assert result["step"] == "appointment_changed"
    assert not h.calls["reads"] and not h.calls["writes"]


@pytest.mark.asyncio
async def test_multiple_owned_targets_require_appointment_selection_before_staff_resolution(flow, monkeypatch):
    h = flow
    finished(h)
    second = uuid4()
    when = datetime.combine(h.tomorrow, datetime.min.time(), h.zone).replace(hour=10)
    async def records(*args, **kwargs):
        return [{"id": h.appointment, "status": "confirmed", "start_at": when, "protocol": "AG-ABCDEF"},
                {"id": second, "status": "confirmed", "start_at": when + timedelta(hours=2), "protocol": "AG-GHJKLM"}]
    async def details(*args):
        return [{"id": str(h.appointment), "start_at": when.isoformat(), "protocol": "AG-ABCDEF", "professional": "Bruno"},
                {"id": str(second), "start_at": (when + timedelta(hours=2)).isoformat(), "protocol": "AG-GHJKLM", "professional": "Breno"}]
    async def unexpected(*args):
        raise AssertionError("No selected target exists yet")
    monkeypatch.setattr(qr_ai, "customer_appointments", records)
    monkeypatch.setattr(qr_ai.qr_cancellation, "owned_candidates", details)
    monkeypatch.setattr(qr_ai, "appointment_services", unexpected)
    result = await h.turn(BODY, preview=False)
    assert result["step"] == "reschedule_pick", result
    state = h.state()
    assert set(state["candidate_ids"]) == {str(h.appointment), str(second)}
    assert not state.get("professional") and not state["deferred_reschedule"]["professional_id"]
    assert state["deferred_reschedule"]["requested_time"] == "11:15"
    assert not h.calls["reads"] and not h.calls["writes"]


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", ["ou Breno", "se estiver disponível", "para meu filho"])
async def test_uncertain_or_beneficiary_request_never_prepares_reschedule_summary(flow, monkeypatch, suffix):
    h = flow
    finished(h)
    async def unexpected(*args, **kwargs):
        raise AssertionError("Uncertain reference cannot load a target as staff proof")
    monkeypatch.setattr(qr_ai, "customer_appointments", unexpected)
    result = await h.turn(BODY.rstrip(".") + " " + suffix, preview=False)
    assert result["step"] != "reschedule_confirm"
    assert not h.calls["writes"] and not h.calls["reads"]
