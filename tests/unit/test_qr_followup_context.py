"""Real QR routing with synthetic scoped backends; no external messages/DB."""
import json
from datetime import datetime, timedelta
from time import time
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app import qr_ai, qr_operation_receipt, qr_profile
from app.qr_understanding import UnderstandingPlan
from test_preview_audio import MemoryRedis
from test_qr_multi_flow import flow


async def identified(h, message, identity="new-followup", *, preview=False):
    token = qr_ai._message_identity.set(identity)
    try:
        return await h.turn(message, preview=preview)
    finally:
        qr_ai._message_identity.reset(token)


def tuesday(h):
    return qr_ai.requested_day("terça", str(h.zone))


@pytest.mark.asyncio
@pytest.mark.parametrize("step", ["date", "professional", "time", "confirm"])
async def test_whole_final_day_restriction_beats_wrong_cancel_classification(flow, step):
    h = flow
    h.seed(step, hold="old-hold", selected_start="old-clock", pending_clock="15:00",
        confirmation_message_id="old-summary", confirmation_summary="Old summary",
        confirmation_expires_at=time()+200, price=3500, duration=40)
    h.proposal["value"] = UnderstandingPlan(intents=["cancel"])
    reply = await identified(h, "Não quero mais, só posso terça")
    assert reply["step"] == "time"
    assert h.state()["day"] == tuesday(h).isoformat()
    assert h.state()["services"] == [str(h.cut)] and h.state()["professional"] == str(h.bruno)
    assert all(not h.state().get(key) for key in ("hold", "selected_start", "confirmation_summary", "confirmation_message_id", "pending_clock"))
    assert h.calls["reads"] == [(h.bruno, [h.cut], tuesday(h))]
    assert h.calls["plans"] == h.calls["writes"] == []


@pytest.mark.asyncio
async def test_day_restriction_of_reschedule_preserves_original_operation(flow):
    h = flow
    h.seed("reschedule_confirm", appointment=str(h.appointment), expected_snapshot="old",
        pending_clock="15:00", confirmation_summary="Old move", confirmation_expires_at=time()+200)
    reply = await identified(h, "Não quero mais, só posso terça")
    assert reply["step"] == "reschedule_time"
    assert h.state()["appointment"] == str(h.appointment) and h.state()["day"] == tuesday(h).isoformat()
    assert not h.state().get("expected_snapshot") and not h.state().get("confirmation_summary")
    assert not h.calls["writes"] and not h.calls["plans"]


@pytest.mark.asyncio
async def test_expired_proposal_cannot_amend_using_old_authority(flow):
    h = flow; h.seed("confirm", confirmation_expires_at=time()-1, hold="old")
    reply = await identified(h, "Não quero mais, só posso terça")
    assert reply["step"] == "expired" and not h.calls["reads"] and not h.calls["writes"]


def owned_rows(h):
    return [{"id": h.appointment if index == 2 else uuid4(), "status": "confirmed",
        "start_at": datetime.combine(h.tomorrow + timedelta(days=index), datetime.min.time(), h.zone).replace(hour=14),
        "protocol": "SYNTHETIC-TARGET" if index == 2 else f"SYNTHETIC-{index}"} for index in range(4)]


def scoped_records(h, monkeypatch):
    rows, lookups = owned_rows(h), []
    async def records(tenant, phone, *, limit):
        assert tenant == h.tenant and phone == "5567999999999" and limit == 15
        lookups.append((tenant, phone)); return rows
    async def original(tenant, item, phone):
        assert tenant == h.tenant and phone == "5567999999999"
        assert item in {row["id"] for row in rows}
        return {"status": "confirmed", "professional_id": h.bruno, "services": [h.cut]}
    async def candidates(tenant, phone):
        await records(tenant, phone, limit=15)
        return [{"id": str(row["id"]), "start_at": row["start_at"].isoformat(),
            "protocol": row["protocol"], "professional": "Bruno"} for row in rows]
    async def cancel(*args, **kwargs):
        raise AssertionError("A date restriction must never cancel an appointment")
    monkeypatch.setattr(qr_ai, "customer_appointments", records)
    monkeypatch.setattr(qr_ai, "appointment_services", original)
    monkeypatch.setattr(qr_ai.qr_cancellation, "owned_candidates", candidates)
    monkeypatch.setattr(qr_ai.qr_cancellation, "cancel_appointment", cancel)
    return rows, lookups


def receipt(h, **changes):
    return {"tenant": str(h.tenant), "phone": "5567999999999", "message_id": "prior-confirmation",
        "step": "confirmed", "answer": "Reserva sintética confirmada. Protocolo SYNTHETIC-TARGET.",
        "protocol": "SYNTHETIC-TARGET", "saved_at": time(), **changes}


@pytest.mark.asyncio
async def test_done_with_four_bookings_uses_fresh_receipt_only_as_scoped_reschedule_reference(flow, monkeypatch):
    import redis.asyncio as redis_asyncio
    h = flow; rows, lookups = scoped_records(h, monkeypatch)
    before = [dict(row) for row in rows]
    h.seed("done", services=[], professional=None)
    memory = MemoryRedis(); memory.values = h.redis.data
    monkeypatch.setattr(redis_asyncio, "from_url", lambda *a, **kw: memory)
    monkeypatch.setenv("QR_AI_INTAKE_REVISION_ENABLED", "0")
    memory.values[qr_operation_receipt.key(h.tenant, "5567999999999")] = json.dumps(receipt(h))
    h.proposal["value"] = UnderstandingPlan(intents=["cancel"])
    reply = await qr_ai.run_locked(h.tenant, "5567999999999", "Não quero mais, só posso terça", h.namespace,
        message_id="fresh-day-restriction")
    assert reply["step"] == "reschedule_time"
    assert h.state()["appointment"] == str(h.appointment) and h.state()["day"] == tuesday(h).isoformat()
    assert h.calls["reads"] == [(h.bruno, [h.cut], tuesday(h))]
    assert lookups and rows == before and not h.calls["writes"] and not h.calls["plans"]
    assert qr_operation_receipt.key(h.tenant, "5567999999999") not in memory.values
    assert qr_ai._recent_operation.get() is None
    # A later clock only prepares a new proposal. It does not apply the move.
    proposed = await identified(h, "às 15h", identity="new-move-summary")
    assert proposed["step"] == "reschedule_confirm" and not h.calls["writes"]
    assert h.state()["confirmation_message_id"] == "new-move-summary"
    assert rows == before


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [None, {"saved_at": time()-301}, {"phone": "5567999999998"},
    {"tenant": str(uuid4())}, {"protocol": "FOREIGN"}, {"step": "cancelled"}, {"message_id": "new-followup"}])
async def test_missing_or_untrusted_receipt_requires_own_reschedule_selection(flow, monkeypatch, changes):
    h = flow; rows, _ = scoped_records(h, monkeypatch)
    h.seed("done", services=[], professional=None)
    if changes is not None:
        h.redis.data[qr_operation_receipt.key(h.tenant, "5567999999999")] = json.dumps(receipt(h, **changes))
    reply = await identified(h, "Não quero mais, só posso terça")
    assert reply["step"] == "reschedule_pick" and "remarcar" in reply["answer"]
    assert set(h.state()["candidate_ids"]) == {str(row["id"]) for row in rows}
    assert h.state()["deferred_reschedule"]["requested_date"] == tuesday(h).isoformat()
    assert not h.calls["reads"] and not h.calls["writes"] and not h.calls["plans"]


@pytest.mark.asyncio
async def test_preview_after_completed_operation_never_reads_real_customer_records(flow, monkeypatch):
    h = flow
    async def forbidden(*a, **kw):
        raise AssertionError("Preview must not inspect a real customer's appointments")
    monkeypatch.setattr(qr_ai, "customer_appointments", forbidden)
    reply = await identified(h, "Não quero mais, só posso terça", preview=True)
    assert reply["step"] == "preview_reschedule" and not h.calls["writes"]


@pytest.mark.asyncio
@pytest.mark.parametrize("step", ["cancel_confirm", "cancel_pick", "reschedule_pick"])
async def test_historical_receipt_never_overrides_current_cancellation_or_reschedule_selection(flow, monkeypatch, step):
    h = flow; rows, _ = scoped_records(h, monkeypatch)
    selected = rows[0]["id"]
    details = [{"id": str(row["id"]), "start_at": row["start_at"].isoformat(),
        "professional": "Bruno", "protocol": row["protocol"]} for row in rows]
    h.seed(step, appointment=str(selected), candidate_ids=[row["id"] for row in details], candidate_details=details)
    h.redis.data[qr_operation_receipt.key(h.tenant, "5567999999999")] = json.dumps(receipt(h))
    reply = await identified(h, "Não quero mais, só posso terça")
    assert reply["step"] == "reschedule_pick" and "remarcar" in reply["answer"]
    assert h.state().get("appointment") != str(h.appointment)
    assert set(h.state()["candidate_ids"]) == {str(row["id"]) for row in rows}
    assert h.state()["deferred_reschedule"]["requested_date"] == tuesday(h).isoformat()
    assert not h.calls["reads"] and not h.calls["writes"] and not h.calls["plans"]


@pytest.mark.parametrize("message", ["Não cancela, só posso terça", "Não quero mais, só posso terça?",
    "Não quero mais, só posso terça ou quarta", "Não quero mais, só posso terça se tiver desconto",
    "Não quero mais, não posso terça", "Não quero mais", "Cancela minha reserva de terça",
    "Não quero mais, só posso terça, mas não confirme", "Não quero mais, só posso terça e barba"])
def test_day_shortcut_never_swallow_negation_condition_alternative_or_another_subject(message):
    assert qr_ai._weekday_restriction(message) is None


def grid(h, monkeypatch, mapping, *, fail=None):
    async def slots(db, tenant, pro, ids, day):
        assert tenant == h.tenant and set(ids) <= {h.cut, h.beard}
        h.calls["reads"].append((pro, list(ids), day))
        if pro == fail:
            raise HTTPException(503, "synthetic")
        items = [row for row in h.catalog if row["id"] in ids]
        return {"slots": [datetime.combine(day, datetime.min.time(), h.zone).replace(hour=hour, minute=minute).isoformat()
            for hour, minute in mapping.get(pro, [])], "price_cents": sum(row["price_cents"] for row in items),
            "duration_min": sum(row["duration_min"] for row in items)}
    monkeypatch.setattr(qr_ai, "list_slots", slots)


def professional_question(h, **extra):
    return h.seed("professional", professional=None, professional_name=None,
        professionals=[{"id": str(row["id"]), "name": row["name"]} for row in h.professionals], **extra)


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["Tanto faz", "Qualquer um", "Sem preferência"])
async def test_indifference_checks_all_actual_eligible_professionals_not_catalog_order(flow, monkeypatch, message):
    h = flow; professional_question(h)
    grid(h, monkeypatch, {h.bruno: [], h.breno: [(14, 0), (15, 0)]})
    reply = await identified(h, message)
    assert reply["step"] == "time" and "Breno" in reply["answer"] and "R$ 35,00" in reply["answer"]
    assert h.state()["professional"] == str(h.breno) and h.state()["professional_name"] == "Breno"
    assert {row[0] for row in h.calls["reads"]} == {h.bruno, h.breno}
    assert not h.calls["writes"] and not h.calls["plans"]


@pytest.mark.asyncio
async def test_multiple_free_professionals_choose_earliest_real_slot_deterministically(flow, monkeypatch):
    h = flow; professional_question(h)
    grid(h, monkeypatch, {h.bruno: [(17, 0)], h.breno: [(9, 0)]})
    reply = await identified(h, "Tanto faz")
    assert reply["step"] == "time" and h.state()["professional"] == str(h.breno)
    assert "9h" in reply["answer"] and not h.calls["writes"]


@pytest.mark.asyncio
@pytest.mark.parametrize("preview", [False, True])
async def test_indifference_respects_pending_exact_clock_prepares_only_new_summary(flow, monkeypatch, preview):
    h = flow; professional_question(h, services=[str(h.cut), str(h.beard)], pending_clock="15:00", pending_relation="exact")
    grid(h, monkeypatch, {h.bruno: [(14, 0)], h.breno: [(15, 0)]})
    reply = await identified(h, "Tanto faz", identity="new-indifference", preview=preview)
    assert reply["step"] == "confirm" and "Breno" in reply["answer"] and "R$ 55,00" in reply["answer"]
    assert h.state()["professional"] == str(h.breno) and h.state()["confirmation_message_id"] == "new-indifference"
    assert [row[0] for row in h.calls["writes"]] == ([] if preview else ["hold"])
    assert not any(row[0] == "confirm" for row in h.calls["writes"])


@pytest.mark.asyncio
@pytest.mark.parametrize("fail", [False, True])
async def test_empty_or_failed_availability_never_invents_a_professional_or_false_zero(flow, monkeypatch, fail):
    h = flow; before = professional_question(h)
    grid(h, monkeypatch, {h.bruno: [(15, 0)] if fail else []}, fail=h.breno if fail else None)
    reply = await identified(h, "Tanto faz")
    assert reply["step"] == "professional" and h.state()["professional"] is None
    assert ("Não consegui verificar" if fail else "Não encontrei horário livre") in reply["answer"]
    assert h.state()["services"] == before["services"] and not h.calls["writes"] and not h.calls["plans"]


@pytest.mark.asyncio
async def test_indifference_before_day_asks_day_then_checks_real_slots_without_provider(flow, monkeypatch):
    h = flow; professional_question(h, day=None)
    grid(h, monkeypatch, {h.bruno: [], h.breno: [(15, 0)]})
    first = await identified(h, "Tanto faz")
    assert first["step"] == "date" and not h.calls["reads"] and not h.state().get("professional")
    second = await identified(h, "terça", identity="new-day")
    assert second["step"] == "time" and h.state()["professional"] == str(h.breno)
    assert h.state()["day"] == tuesday(h).isoformat()
    assert not h.calls["plans"] and not h.calls["writes"]


@pytest.mark.asyncio
async def test_informational_professional_indifference_stays_readonly_and_preserves_booking(flow, monkeypatch):
    h = flow; before = h.seed("confirm", hold="old-hold", confirmation_summary="Old summary", confirmation_message_id="old-summary-id",
        confirmation_expires_at=time()+180, availability_query={"services": [str(h.beard)],
            "day": h.tomorrow.isoformat(), "waiting_for": "professional"})
    grid(h, monkeypatch, {h.bruno: [], h.breno: [(15, 0)]})
    reply = await identified(h, "Qualquer um")
    assert "Breno" in reply["answer"] and "Bruno" not in reply["answer"]
    assert h.state()["services"] == before["services"] and h.state()["hold"] == before["hold"]
    assert h.state()["confirmation_requires_review"] and h.state()["confirmation_expires_at"] == before["confirmation_expires_at"]
    assert all(row[1] == [h.beard] for row in h.calls["reads"])
    assert not h.calls["writes"] and not h.calls["plans"]
    review = await identified(h, "SIM", identity="review-only")
    assert review["step"] == "confirm" and before["confirmation_summary"] in review["answer"]
    assert not h.calls["writes"] and h.state()["confirmation_message_id"] == "review-only"


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["Tanto faz", "Não quero mais, só posso terça"])
async def test_capability_gate_precedes_followup_reads_or_operations(flow, message):
    h = flow; h.business["profile"]["capabilities"] = ["catalog_answers"]
    professional_question(h)
    reply = await identified(h, message)
    assert reply["step"] == "capability_unavailable" and not h.calls["reads"] and not h.calls["writes"]


@pytest.mark.parametrize("message", ["Tanto faz?", "Não, tanto faz", "Tanto faz se tiver desconto", "Tanto faz ou Bruno",
    "Qualquer um, menos Bruno", "Sem preferência, pode confirmar", "Tanto faz, só barba"])
def test_indifference_shortcut_is_whole_positive_choice_without_changes_or_consent(message):
    assert not qr_ai._professional_indifference(message)


@pytest.fixture
def multiple_query(flow, monkeypatch):
    import redis.asyncio as redis_asyncio
    h = flow
    h.professionals[1]["name"] = "Alex"
    h.professionals.append({"id": uuid4(), "name": "Carlos"})
    memory = MemoryRedis(); memory.values = h.redis.data
    monkeypatch.setattr(redis_asyncio, "from_url", lambda *a, **kw: memory)
    monkeypatch.setenv("QR_AI_INTAKE_REVISION_ENABLED", "0")
    monkeypatch.setenv("QR_AI_ENABLED", "1")
    monkeypatch.setenv("QR_AI_ALLOWED_SENDERS", "5567999999999")
    monkeypatch.setenv("QR_AI_TENANT_ID", str(h.tenant))
    monkeypatch.setenv("QR_BRIDGE_TOKEN", "synthetic-test-bridge-secret-1234567890")
    h.proposal["value"] = UnderstandingPlan(intents=["availability"], query_service_ids=[h.cut],
        professional_id=h.bruno, ambiguities=["professional"])
    grid(h, monkeypatch, {h.bruno: [(14, 0)], h.breno: [(15, 0)], h.professionals[2]["id"]: [(17, 0)]})
    return h


@pytest.mark.asyncio
@pytest.mark.parametrize("message", [
    "Quais horários Bruno e Alex tem na segunda pra cortar o cabelo? É só consulta.",
    "Quais horários o Bruno e o Alex têm na segunda pra cortar o cabelo? É só consulta."])
async def test_actual_private_route_answers_both_requested_professionals_with_real_slots(multiple_query, message):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    h = multiple_query
    app = FastAPI(); app.include_router(qr_ai.router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://synthetic") as client:
        response = await client.post("/internal/qr/ai/reply", json={"phone": "5567999999999",
            "message_id": "multi-pro-transcript", "text": message},
            headers={"x-orion-bridge-key": "synthetic-test-bridge-secret-1234567890"})
    assert response.status_code == 200
    reply = response.json()
    assert "Bruno" in reply["answer"] and "Alex" in reply["answer"] and "Carlos" not in reply["answer"]
    assert "14h" in reply["answer"] and "15h" in reply["answer"]
    assert "prefer" not in reply["answer"].lower()
    assert [(row[0], row[1], row[2].weekday()) for row in h.calls["reads"]] == [(h.bruno, [h.cut], 0), (h.breno, [h.cut], 0)]
    state = json.loads(h.redis.data[f"orion:qr:ai:{h.tenant}:5567999999999"])
    assert {row["id"] for row in state["last_availability"]["professionals"]} == {str(h.bruno), str(h.breno)}
    assert not state.get("professional") and not state.get("services") and not state.get("hold")
    assert h.calls["plans"] == [message] and not h.calls["writes"] and not h.calls["names"]


@pytest.mark.asyncio
async def test_audio_prepare_uses_same_multiprofessional_readonly_pipeline(multiple_query):
    h = multiple_query
    transcript = "Quais horários Bruno e Alex tem na segunda pra cortar o cabelo? É só consulta."
    async def prepare():
        return {"text": transcript}
    reply = await qr_ai.run_locked(h.tenant, "5567999999999", "", h.namespace, message_id="audio-multi-pro", prepare=prepare)
    assert reply["input_kind"] == "audio" and reply["transcript"] == transcript
    assert "Bruno" in reply["answer"] and "Alex" in reply["answer"] and "Carlos" not in reply["answer"]
    assert {row[0] for row in h.calls["reads"]} == {h.bruno, h.breno}
    assert not h.calls["writes"] and not h.calls["names"]


@pytest.mark.asyncio
async def test_multiprofessional_inquiry_preserves_existing_booking_and_requires_new_review(multiple_query):
    h = multiple_query
    before = h.seed("confirm", services=[str(h.beard)], professional=str(h.bruno), hold="prior-hold",
        confirmation_message_id="prior-summary", confirmation_summary="Barba synthetic summary", confirmation_expires_at=time()+160)
    reply = await identified(h, "Quais horários Bruno e Alex tem na segunda pra cortar o cabelo? É só consulta.")
    assert "Bruno" in reply["answer"] and "Alex" in reply["answer"]
    assert h.state()["services"] == before["services"] and h.state()["hold"] == before["hold"]
    assert h.state()["confirmation_expires_at"] == before["confirmation_expires_at"] and h.state()["confirmation_requires_review"]
    assert not h.calls["writes"]


@pytest.mark.asyncio
async def test_clock_after_multiprofessional_inquiry_rechecks_both_and_asks_who_if_both_free(multiple_query, monkeypatch):
    h = multiple_query
    grid(h, monkeypatch, {h.bruno: [(14, 0)], h.breno: [(14, 0)]})
    await identified(h, "Quais horários Bruno e Alex tem na segunda pra cortar o cabelo? É só consulta.")
    assert not h.state().get("services") and not h.calls["writes"]
    h.calls["reads"].clear()
    reply = await identified(h, "às 14h", identity="new-pro-choice")
    assert reply["step"] == "professional" and "Bruno" in reply["answer"] and "Alex" in reply["answer"]
    assert "Carlos" not in reply["answer"] and not h.state().get("professional")
    assert {row[0] for row in h.calls["reads"]} == {h.bruno, h.breno} and not h.calls["writes"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [False, True])
async def test_multiprofessional_empty_and_failed_grids_never_fake_both_available(multiple_query, monkeypatch, failure):
    h = multiple_query
    grid(h, monkeypatch, {h.bruno: [(14, 0)], h.breno: []}, fail=h.breno if failure else None)
    reply = await identified(h, "Quais horários Bruno e Alex tem na segunda pra cortar o cabelo? É só consulta.")
    if failure:
        assert "Não consegui consultar" in reply["answer"] and not h.state().get("last_availability")
    else:
        assert "Bruno" in reply["answer"] and "Alex" not in reply["answer"]
        assert {row["id"] for row in h.state()["last_availability"]["professionals"]} == {str(h.bruno)}
    assert not h.calls["writes"] and not h.calls["names"]


@pytest.mark.parametrize("subject", ["Bruno e Jorge", "Bruno e Alex e Jorge", "Bruno ou Alex", "Bruno e Bruno", "Brunoo e Alex"])
def test_entire_professional_list_requires_unique_known_names(multiple_query, subject):
    h = multiple_query
    assert qr_ai._queried_professionals(f"Quais horários {subject} tem na segunda pra cortar o cabelo? É só consulta.", h.professionals) is None


def test_professional_names_aliases_and_accents_resolve_without_substring_or_homonym_choice(multiple_query):
    h = multiple_query
    h.professionals[0].update(name="Brúno Silva", aliases=["Bê"])
    h.professionals[1].update(name="Álex Souza")
    assert qr_ai._queried_professionals("Quais horários o Bê e o Alex têm na segunda?", h.professionals) == [str(h.bruno), str(h.breno)]
    h.professionals.append({"id": uuid4(), "name": "Bruno Costa"})
    assert qr_ai._queried_professionals("Quais horários Bruno e Alex tem na segunda?", h.professionals) is None
    assert qr_ai._queried_professionals("Quais horários Bruno Silva e Alex Souza tem na segunda?", h.professionals) == [str(h.bruno), str(h.breno)]


@pytest.mark.asyncio
@pytest.mark.parametrize("message", [
    "Quais horários Bruno e Alex tem na segunda pra cortar o cabelo? Pode marcar às 14h.",
    "Quais horários Bruno e Alex tem na segunda pra cortar o cabelo? Confirma pra mim.",
    "Quais horários Bruno e Alex tem na segunda pra cortar o cabelo, se tiver desconto?",
    "Quais horários Bruno e Alex tem na segunda pra cortar o cabelo? Não com Alex.",
    "Quais horários Bruno e Alex tem na segunda pra cortar o cabelo com Jorge?",
    "Quais horários Bruno ou Alex tem na segunda pra cortar o cabelo?"])
async def test_operational_conditional_or_ambiguous_query_never_escapes_professional_review(multiple_query, message):
    h = multiple_query
    reply = await identified(h, message)
    assert not h.calls["reads"] and not h.calls["writes"] and not h.calls["names"]
    assert not h.state().get("last_availability") and reply["step"] != "confirm"


@pytest.mark.asyncio
async def test_provider_mixed_booking_plan_does_not_use_readonly_exception(multiple_query):
    h = multiple_query
    h.proposal["value"] = UnderstandingPlan(intents=["availability", "booking"], service_ids=[h.cut], ambiguities=["professional"])
    reply = await identified(h, "Quais horários Bruno e Alex tem na segunda pra cortar o cabelo? É só consulta.")
    assert reply["step"] != "confirm" and not h.calls["reads"] and not h.calls["writes"]


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["service", "day"])
async def test_multi_professional_exception_requires_clear_service_and_day(multiple_query, missing):
    h = multiple_query
    message = "Quais horários Bruno e Alex tem " + ("na segunda?" if missing == "service" else "pra cortar o cabelo?")
    reply = await identified(h, message)
    assert not h.calls["reads"] and not h.calls["writes"] and reply["step"] != "confirm"


@pytest.mark.asyncio
async def test_explicit_question_also_works_when_provider_only_reports_other_plus_name_ambiguity(multiple_query):
    h = multiple_query
    h.proposal["value"] = UnderstandingPlan(intents=["other"], ambiguities=["professional"])
    reply = await identified(h, "Quais horários Bruno e Alex tem na segunda pra cortar o cabelo? É só consulta.")
    assert "Bruno" in reply["answer"] and "Alex" in reply["answer"] and not h.calls["writes"]
    assert {row[0] for row in h.calls["reads"]} == {h.bruno, h.breno}


@pytest.mark.asyncio
async def test_requested_professional_not_eligible_is_not_reported_as_no_slots(multiple_query, monkeypatch):
    h = multiple_query
    async def eligible(tenant, ids):
        assert tenant == h.tenant and ids == [h.cut]
        return [h.professionals[0]]
    monkeypatch.setattr(qr_ai, "professionals_for", eligible)
    reply = await identified(h, "Quais horários Bruno e Alex tem na segunda pra cortar o cabelo? É só consulta.")
    assert "Não consegui validar todos" in reply["answer"] and "Não encontrei" not in reply["answer"]
    assert not h.calls["reads"] and not h.calls["writes"] and not h.state().get("last_availability")
