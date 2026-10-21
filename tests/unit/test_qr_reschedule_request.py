"""Original booking references and new wishes are different temporal scopes."""
import json
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from app.qr_understanding import UnderstandingPlan, build_plan
from app.qr_reschedule_request import split_request, source_service_subject
from app import qr_ai, qr_profile
from test_qr_multi_flow import flow
from test_preview_audio import MemoryRedis


REAL_TRANSCRIPT = "Quero remarcar o corte com o Bruno da terça-feira do dia 20 às 3 e meia para quarta-feira às 4 da tarde."


@pytest.mark.parametrize("model_correct", [False, True])
def test_exact_real_request_keeps_only_destination_in_operational_plan(model_correct):
    cut, bruno = uuid4(), uuid4()
    proposal = UnderstandingPlan(intents=["reschedule"], service_ids=[cut], professional_id=bruno,
        requested_date="2026-10-21", requested_time="16:00", time_relation="exact") if model_correct else None
    plan = build_plan(message=REAL_TRANSCRIPT, catalog=[{"id": cut, "name": "Corte", "price_cents": 3500, "duration_min": 40}],
        professionals=[{"id": bruno, "name": "Bruno"}], context={"hours": [{"opens": "09:00", "closes": "18:00"}]},
        reference_date=date(2026, 10, 17), proposal=proposal)
    assert plan.requested_date == "2026-10-21" and plan.requested_time == "16:00"
    assert not plan.ambiguities and plan.intents == ["reschedule"] and plan.service_ids == [cut]


@pytest.mark.parametrize("connector", ["para", "pra", "por"])
def test_explicit_scope_keeps_original_clauses(connector):
    body = f"Quero reagendar o corte de 20/10 às15h30 {connector} 21/10 às16h."
    result = split_request(body)
    assert result.source == "Quero reagendar o corte de 20/10 às15h30"
    assert result.destination == "21/10 às16h."


@pytest.mark.parametrize("old_clock,new_clock", [
    ("às15h30", "16h30"), ("15:30", "16:30"), ("às3h30", "às4h30"),
    ("às15h", "16h"), ("às três e meia", "às quatro da tarde"),
])
def test_already_supported_clock_syntax_has_one_shared_scope_boundary(old_clock, new_clock):
    result = split_request(f"Quero remarcar o corte {old_clock} para {new_clock}")
    assert result is not None and result.destination == new_clock
    assert source_service_subject(result.source) == "corte"


@pytest.mark.parametrize("old_reference", ["do dia 2026-10-20", "de 20/10", "da terça-feira do dia20"])
def test_service_subject_preserves_unknown_words_but_not_calendar_qualifiers(old_reference):
    result = split_request(f"Quero remarcar a massagem {old_reference} às15h30 para quarta às16h")
    assert result is not None and source_service_subject(result.source) == "massagem"


@pytest.mark.parametrize("body", [
    "Quero corte terça às15h para quarta às16h", "Não quero remarcar de terça para quarta",
    "Quero remarcar de terça para quarta ou quinta às16h", "Quero remarcar de terça para quarta se puder",
    "Quero remarcar de terça para quarta às16h se não cobrar multa",
    "Quero remarcar de terça para quarta às16h e cancela a barba",
    "Quero remarcar para quarta às16h", "Qual a política para remarcar de terça para quarta?",
    "Quero remarcar de terça para não quarta às16h", "Quero remarcar não a terça, a outra para quarta",
])
def test_scope_is_not_a_general_correction_or_permission(body):
    assert split_request(body) is None


@pytest.fixture
def scoped_flow(flow, monkeypatch):
    h = flow
    zone = ZoneInfo(h.business["timezone"])
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            value = cls(2026, 10, 17, 12, tzinfo=zone)
            return value.astimezone(tz) if tz else value.replace(tzinfo=None)
    monkeypatch.setattr(qr_ai, "datetime", Clock)
    source_day, target_day = date(2026, 10, 20), date(2026, 10, 21)
    source_time = datetime(2026, 10, 20, 15, 30, tzinfo=zone)
    target_time = datetime(2026, 10, 21, 16, tzinfo=zone)
    # Eight other rows include one at the NEW desired date/time. That row must
    # never become the original target, nor may an old state/receipt override it.
    rows = [{"id": uuid4(), "status": "confirmed", "start_at": source_time+timedelta(days=i+3),
             "protocol": f"SYNTHETIC-{i}"} for i in range(7)]
    rows[1]["start_at"] = target_time
    rows.append({"id": h.appointment, "status": "confirmed", "start_at": source_time, "protocol": "SYNTHETIC-SOURCE"})
    records_by_id = {str(row["id"]): {**row, "professional_id": h.bruno, "services": [h.cut]} for row in rows}
    lookups, hours = [], []
    async def records(tenant, phone, limit=15):
        assert tenant == h.tenant and limit == 15
        lookups.append(("list", tenant, phone))
        return rows if phone == "5567999999999" else []
    async def original(tenant, appointment, phone):
        assert tenant == h.tenant
        lookups.append(("original", tenant, phone, appointment))
        return records_by_id.get(str(appointment)) if phone == "5567999999999" else None
    async def working(tenant, pros, day):
        assert tenant == h.tenant
        hours.append((list(pros), day))
        return [{"opens": "09:00", "closes": "18:00"}]
    h.business["use_working_hours"] = True
    monkeypatch.setattr(qr_ai, "customer_appointments", records)
    monkeypatch.setattr(qr_ai, "appointment_services", original)
    monkeypatch.setattr(qr_ai, "working_hours_for", working)
    return SimpleNamespace(**locals())


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["none", "correct", "old"])
async def test_real_turn_scoped_source_with_eight_rows_only_prepares_destination(scoped_flow, provider):
    s = scoped_flow;h = s.h
    if provider != "none":
        h.proposal["value"] = UnderstandingPlan(intents=["reschedule"], service_ids=[h.cut], professional_id=h.bruno,
            requested_date=(s.source_day if provider == "old" else s.target_day).isoformat(),
            requested_time="15:30" if provider == "old" else "16:00", time_relation="exact")
    result = await h.turn(REAL_TRANSCRIPT, preview=False)
    assert result["step"] == "reschedule_confirm" and "16h" in result["answer"] and "21/10/2026" in result["answer"]
    assert h.state()["appointment"] == str(h.appointment) and "Bruno" in result["answer"] and "R$ 35,00" in result["answer"]
    assert h.calls["reads"] == [(h.bruno, [h.cut], s.target_day)] and not h.calls["writes"]
    assert ([h.bruno], s.source_day) in s.hours and ([h.bruno], s.target_day) in s.hours
    assert h.state()["history"][-2]["content"] == REAL_TRANSCRIPT
    done = await h.turn("Sm", preview=False)
    assert done["step"] == "rescheduled" and h.calls["writes"] == [("reschedule", s.target_time)]


@pytest.mark.asyncio
@pytest.mark.parametrize("old_step", ["reschedule_confirm", "cancel_confirm", "reschedule_pick"])
async def test_explicit_source_never_uses_a_previous_selected_target_or_receipt(scoped_flow, old_step):
    s = scoped_flow;h = s.h
    h.seed(old_step, appointment=str(s.rows[0]["id"]), candidate_ids=[str(s.rows[1]["id"])], candidate_details=[],
        confirmation_summary="Resumo antigo sintético", confirmation_expires_at=datetime.now().timestamp()+200)
    token = qr_ai._recent_operation.set({"protocol": s.rows[1]["protocol"]})
    try:
        result = await h.turn(REAL_TRANSCRIPT, preview=False)
    finally:
        qr_ai._recent_operation.reset(token)
    assert result["step"] == "reschedule_confirm" and h.state()["appointment"] == str(h.appointment)
    assert not h.calls["writes"]


@pytest.mark.asyncio
async def test_actual_admission_dedupes_whole_request_and_requires_new_consent_id(scoped_flow, monkeypatch):
    s = scoped_flow;h = s.h;redis = MemoryRedis()
    monkeypatch.setattr("redis.asyncio.from_url", lambda *_, **__: redis)
    monkeypatch.delenv("QR_AI_INTAKE_REVISION_ENABLED", raising=False)
    async def message(body, identity):
        return await qr_ai.run_locked(h.tenant, "5567999999999", body, h.namespace, message_id=identity)
    proposed = await message(REAL_TRANSCRIPT, "scoped-source-message")
    assert proposed["step"] == "reschedule_confirm" and not h.calls["writes"]
    assert (await message("Sm", "scoped-source-message"))["step"] == "duplicate"
    assert not h.calls["writes"]
    done = await message("Sm", "scoped-new-consent")
    assert done["step"] == "rescheduled" and h.calls["writes"] == [("reschedule", s.target_time)]
    assert (await message("Sm", "scoped-new-consent"))["step"] == "duplicate"


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["absent", "cancelled", "past", "other_professional", "other_service", "lookup_missing", "contradictory_day"])
async def test_nonmatching_origin_never_falls_back_to_destination_or_first(scoped_flow, invalid):
    s = scoped_flow;h = s.h;row = s.records_by_id[str(h.appointment)]
    body = REAL_TRANSCRIPT
    if invalid == "absent":
        s.rows[:] = s.rows[:-1]
    elif invalid == "cancelled":
        row["status"] = "cancelled"
    elif invalid == "past":
        row["start_at"] = datetime.now(s.zone)-timedelta(hours=1)
    elif invalid == "other_professional":
        row["professional_id"] = h.breno
    elif invalid == "other_service":
        row["services"] = [h.beard]
    elif invalid == "lookup_missing":
        s.records_by_id.pop(str(h.appointment))
    elif invalid == "contradictory_day":
        body = body.replace("dia 20", "dia 21")
    result = await h.turn(body, preview=False)
    assert result["step"] == "reschedule_pick" and not h.state().get("appointment")
    assert not h.calls["reads"] and not h.calls["writes"]
    await h.turn("Sm", preview=False)
    assert not h.calls["writes"]


@pytest.mark.asyncio
async def test_two_matching_origins_require_choice_and_preserve_only_deferred_destination(scoped_flow):
    s = scoped_flow;h = s.h
    duplicate = {**s.rows[-1], "id": uuid4(), "protocol": "SYNTHETIC-OTHER-MATCH"}
    s.rows.append(duplicate)
    s.records_by_id[str(duplicate["id"])] = {**duplicate, "professional_id": h.bruno, "services": [h.cut]}
    result = await h.turn(REAL_TRANSCRIPT, preview=False)
    assert result["step"] == "reschedule_pick" and not h.calls["reads"] and not h.calls["writes"]
    assert set(h.state()["candidate_ids"]) == {str(h.appointment), str(duplicate["id"])}
    assert h.state()["deferred_reschedule"]["requested_date"] == s.target_day.isoformat()
    await h.turn("Sm", preview=False)
    assert not h.calls["reads"] and not h.calls["writes"]


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", [" se puder", " se não cobrar multa", " ou quinta-feira às16h", " e cancela a barba",
    " desde que tenha desconto", " contanto que tenha vaga", " dependendo do valor", " sob a condição de manter o preço"])
async def test_condition_alternative_or_second_operation_never_gets_a_reschedule_summary(scoped_flow, suffix):
    s = scoped_flow;h = s.h
    result = await h.turn(REAL_TRANSCRIPT.rstrip(".") + suffix, preview=False)
    assert result["step"] != "reschedule_confirm" and not h.calls["writes"]


@pytest.mark.asyncio
@pytest.mark.parametrize("guard", ["preview", "capability", "other_contact", "changed_professional", "changed_service", "homonym", "ambiguous_shift"])
async def test_scope_does_not_bypass_existing_operation_and_ownership_guards(scoped_flow, monkeypatch, guard):
    s = scoped_flow;h = s.h;body = REAL_TRANSCRIPT
    if guard == "capability":
        h.business["profile"]["capabilities"].remove("rescheduling")
    elif guard == "changed_professional":
        body = body.rstrip(".") + " com Breno."
    elif guard == "changed_service":
        body = body.rstrip(".") + " só barba."
    elif guard == "homonym":
        h.professionals[1]["name"] = "Bruno Silva"
    elif guard == "ambiguous_shift":
        async def hours(*_): return [{"opens": "00:00", "closes": "23:00"}]
        monkeypatch.setattr(qr_ai, "working_hours_for", hours)
    if guard == "other_contact":
        result = await qr_ai.turn(h.tenant, "5567999999998", body, h.redis, h.namespace, preview=False)
    else:
        result = await h.turn(body, preview=guard == "preview")
    assert result["step"] != "reschedule_confirm" and not h.calls["writes"]
    if guard in {"preview", "capability", "other_contact", "homonym", "ambiguous_shift"}:
        assert not h.calls["reads"]


@pytest.mark.asyncio
async def test_unknown_original_subject_cannot_inherit_current_target_or_model_service(scoped_flow):
    s = scoped_flow;h = s.h
    h.seed("reschedule_confirm", appointment=str(s.rows[0]["id"]), expected_snapshot="old-snapshot",
        confirmation_summary="Ancien résumé synthétique", confirmation_expires_at=qr_ai.datetime.now().timestamp()+200)
    h.proposal["value"] = UnderstandingPlan(intents=["reschedule"], requested_date="2026-10-21", requested_time="16:00")
    result = await h.turn(REAL_TRANSCRIPT.replace("o corte", "a massagem"), preview=False)
    assert result["step"] == "reschedule_pick" and not h.state().get("appointment")
    assert not h.calls["reads"] and not h.calls["writes"]
    assert not h.state().get("expected_snapshot") and not h.state().get("confirmation_message_id")


@pytest.mark.asyncio
async def test_repeating_the_fixed_request_recovers_surviving_clarification_without_reset(scoped_flow):
    s = scoped_flow;h = s.h
    await h.turn("Oi", preview=False)
    state = h.state()
    # These are the surviving facts of the published parser's old ambiguous
    # answer, not a fabricated database record or any authorization to move it.
    state.update(clarification_pending=["date", "time"], appointment=str(s.rows[0]["id"]),
        expected_snapshot="previous-wrong-target", pending_clock="15:30",
        history=[{"role": "user", "content": REAL_TRANSCRIPT},
            {"role": "assistant", "content": "Expediente cadastrado para este pedido: 9h–13h; 9h–18h. Qual horário você prefere?"}])
    h.redis.data[h.key] = json.dumps(state)
    result = await h.turn(REAL_TRANSCRIPT, preview=False)
    assert result["step"] == "reschedule_confirm" and h.state()["appointment"] == str(h.appointment)
    assert h.state()["expected_snapshot"] == "synthetic-snapshot" and not h.state().get("clarification_pending")
    assert not h.calls["writes"] and h.calls["reads"] == [(h.bruno, [h.cut], s.target_day)]


@pytest.mark.asyncio
@pytest.mark.parametrize("identity", [None, "same-summary-id"])
async def test_new_scoped_summary_never_accepts_missing_or_same_message_consent(scoped_flow, identity):
    s = scoped_flow;h = s.h
    await h.turn(REAL_TRANSCRIPT, preview=False)
    current = h.state();current["confirmation_message_id"] = "same-summary-id"
    h.redis.data[h.key] = json.dumps(current)
    token = qr_ai._message_identity.set(identity)
    try:
        result = await qr_ai.turn(h.tenant, "5567999999999", "Sm", h.redis, h.namespace, preview=False)
    finally:
        qr_ai._message_identity.reset(token)
    assert result["step"] != "rescheduled" and not h.calls["writes"]


@pytest.mark.parametrize("destination, expected", [
    ("quarta-feira às4 da tarde, não, melhor quinta-feira às5 da tarde", ("2026-10-22", "17:00")),
    ("quarta-feira do dia22 às4 da tarde", (None, "16:00")),
    ("dia21 às4 da tarde", (None, "16:00")),
])
def test_destination_final_correction_or_contradiction_is_checked_independently(destination, expected):
    body = REAL_TRANSCRIPT.split(" para ")[0] + " para " + destination
    result = build_plan(message=body, catalog=[], reference_date=date(2026, 10, 17),
        context={"hours": [{"opens": "09:00", "closes": "18:00"}]})
    assert (result.requested_date, result.requested_time) == expected
    assert ("date" in result.ambiguities) == (expected[0] is None)


@pytest.mark.asyncio
@pytest.mark.parametrize("incorrect", ["day", "clock"])
async def test_incorrect_origin_then_short_correction_keeps_destination_without_authority(scoped_flow, incorrect):
    s = scoped_flow;h = s.h
    body = REAL_TRANSCRIPT.replace("dia 20", "dia 21") if incorrect == "day" else REAL_TRANSCRIPT.replace("3 e meia", "2 e meia")
    first = await h.turn(body, preview=False)
    assert first["step"] == "reschedule_pick" and not h.calls["writes"]
    corrected = await h.turn("20/10 às15h30 com Bruno", preview=False)
    assert corrected["step"] == "reschedule_confirm" and "21/10/2026" in corrected["answer"] and "16h" in corrected["answer"]
    assert h.state()["appointment"] == str(h.appointment) and not h.calls["writes"]
    done = await h.turn("Sm", preview=False)
    assert done["step"] == "rescheduled" and h.calls["writes"] == [("reschedule", s.target_time)]


@pytest.mark.asyncio
async def test_unknown_service_stays_unresolved_until_an_explicit_catalog_correction(scoped_flow):
    s = scoped_flow;h = s.h
    await h.turn(REAL_TRANSCRIPT.replace("o corte", "a massagem"), preview=False)
    before = h.state()["reschedule_source_query"]
    assert "service" in before["required"] and not h.state().get("appointment")
    answer = await h.turn("20/10 às15h30 com Bruno", preview=False)
    assert answer["step"] == "reschedule_pick" and "serviço" in answer["answer"]
    assert not h.calls["reads"] and not h.calls["writes"]
    corrected = await h.turn("É corte", preview=False)
    assert corrected["step"] == "reschedule_confirm" and h.state()["appointment"] == str(h.appointment)
    assert h.calls["reads"] == [(h.bruno, [h.cut], s.target_day)] and not h.calls["writes"]


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", ["15:30", "às três e meia", "20/10 às três e meia com Bruno"])
async def test_correction_replaces_only_the_explicit_clock_and_does_not_renew_request_deadline(scoped_flow, answer):
    s = scoped_flow;h = s.h
    await h.turn(REAL_TRANSCRIPT.replace("3 e meia", "2 e meia"), preview=False)
    query = h.state()["reschedule_source_query"]
    assert not any(name in h.state() for name in ("hold", "appointment", "expected_snapshot", "confirmation_message_id"))
    result = await h.turn(answer, preview=False)
    assert result["step"] == "reschedule_confirm" and h.state()["appointment"] == str(h.appointment)
    assert h.state()["day"] == s.target_day.isoformat() and "16h" in result["answer"]
    assert not h.calls["writes"] and query["expires_at"] > qr_ai.datetime.now().timestamp()


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["expired", "revision", "conditional", "negated"])
async def test_stale_or_conditional_correction_cannot_prepare_or_confirm_stored_destination(scoped_flow, invalid):
    s = scoped_flow;h = s.h
    await h.turn(REAL_TRANSCRIPT.replace("dia 20", "dia 21"), preview=False)
    state = h.state();query = state["reschedule_source_query"]
    if invalid == "expired": query["expires_at"] = 0
    if invalid == "revision": query["revision"] = "synthetic-old-revision"
    h.redis.data[h.key] = json.dumps(state)
    answer = "20/10 às15h30 com Bruno"
    if invalid == "conditional": answer += " desde que não cobre multa"
    if invalid == "negated": answer = "Não, " + answer
    result = await h.turn(answer, preview=False)
    assert result["step"] != "reschedule_confirm" and not h.state().get("reschedule_source_query")
    await h.turn("Sm", preview=False)
    assert not h.calls["reads"] and not h.calls["writes"] and not h.state().get("appointment")


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", ["A das três e meia", "20/10 às três e meia com Bruno"])
async def test_multiple_origins_accept_natural_clock_against_registered_hours(scoped_flow, answer):
    s = scoped_flow;h = s.h
    other = {**s.rows[-1], "id": uuid4(), "protocol": "SYNTHETIC-MORNING", "start_at": s.source_time.replace(hour=10)}
    s.rows.append(other);s.records_by_id[str(other["id"])] = {**other, "professional_id": h.bruno, "services": [h.cut]}
    result = await h.turn(REAL_TRANSCRIPT.replace(" às 3 e meia", ""), preview=False)
    assert result["step"] == "reschedule_pick" and len(h.state()["candidate_ids"]) == 2
    selected = await h.turn(answer, preview=False)
    assert selected["step"] == "reschedule_confirm" and h.state()["appointment"] == str(h.appointment)
    assert "16h" in selected["answer"] and not h.calls["writes"]


@pytest.mark.asyncio
async def test_repeated_nonmatching_correction_does_not_extend_source_or_destination_deadline(scoped_flow):
    s = scoped_flow;h = s.h
    await h.turn(REAL_TRANSCRIPT.replace("3 e meia", "2 e meia"), preview=False)
    old = h.state()["reschedule_source_query"]
    await h.turn("às14h", preview=False)
    current = h.state()["reschedule_source_query"]
    assert current["expires_at"] == old["expires_at"] and current["revision"] == old["revision"]
    assert current["destination"] == old["destination"] and current["source"]["requested_time"] == "14:00"
    assert not h.calls["writes"] and not h.state().get("appointment")


@pytest.mark.asyncio
async def test_conditional_protocol_choice_never_releases_a_deferred_destination(scoped_flow):
    s = scoped_flow;h = s.h
    other = {**s.rows[-1], "id": uuid4(), "protocol": "SYNTHETIC-MORNING", "start_at": s.source_time.replace(hour=10)}
    s.rows.append(other);s.records_by_id[str(other["id"])] = {**other, "professional_id": h.bruno, "services": [h.cut]}
    await h.turn(REAL_TRANSCRIPT.replace(" às 3 e meia", ""), preview=False)
    result = await h.turn("SYNTHETIC-SOURCE desde que tenha desconto", preview=False)
    assert result["step"] == "reschedule_pick" and not h.state().get("deferred_reschedule")
    assert not h.state().get("reschedule_source_query") and not h.calls["reads"] and not h.calls["writes"]
