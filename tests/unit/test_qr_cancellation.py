"""Confirmed-booking cancellation module; all fixtures are synthetic/local."""
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
from uuid import uuid4

from fastapi import HTTPException
import pytest

from app import qr_cancellation as flow, qr_ai

ORIGINAL_WRITE_FENCE = flow.revision.check_before_write


class MemoryRedis:
    def __init__(self):
        self.data = {}; self.ttls = {}

    async def set(self, key, value, ex=None):
        self.data[key] = value; self.ttls[key] = ex

    async def get(self, key):
        return self.data.get(key)

    async def delete(self, key):
        self.data.pop(key, None)


@pytest.fixture
def cancellation(monkeypatch):
    tenant, item, pro, customer, service = [uuid4() for _ in range(5)]
    phone = "5567999999999"
    now = datetime(2026, 10, 17, 15, tzinfo=timezone.utc)
    record = {"id": item, "customer_id": customer, "professional_id": pro, "status": "confirmed",
        "start_at": now + timedelta(days=2), "end_at": now + timedelta(days=2, minutes=40),
        "total_cents": 3500, "protocol": "AG-SYNTHETIC-001", "updated_at": now,
        "timezone": "America/Campo_Grande", "professional": "Bruno — Demonstração",
        "items": [{"service_id": service, "name_snapshot": "Corte", "duration_snapshot_min": 40, "price_snapshot_cents": 3500}],
        "expected_snapshot": "a" * 64}
    records = [record]
    events = []
    business = {"name": "Empresa sintética", "timezone": "America/Campo_Grande", "profile_id": str(uuid4()),
        "profile_revision": 1, "profile": {"capabilities": ["appointments"], "policies": [], "style_rules": {}}}
    redis = MemoryRedis()
    key = f"orion:qr:ai:{tenant}:{phone}"
    async def appointments(t, pn, limit):
        assert t == tenant and pn == phone and limit == 15
        events.append("list")
        return deepcopy(records)
    async def owned(t, sender, *, item_id):
        assert t == tenant and sender == "+" + phone
        events.append("read")
        return next((deepcopy(row) for row in records if row["id"] == item_id and row["status"] == "confirmed"), None)
    @asynccontextmanager
    async def session():
        yield object()
    async def cancel(db, t, item_id, *, customer_phone, expected_snapshot, future_only):
        assert t == tenant and customer_phone == "+" + phone and expected_snapshot == record["expected_snapshot"] and future_only is True
        events.append("cancel")
        value = next(row for row in records if row["id"] == item_id)
        value["status"] = "cancelled"
        return deepcopy(value)
    async def current():
        events.append("fence")
    monkeypatch.setattr(flow, "_now", lambda: now)
    monkeypatch.setattr(flow, "customer_appointments", appointments)
    monkeypatch.setattr(flow, "owned_booking", owned)
    monkeypatch.setattr(flow, "get_session_factory", lambda: session)
    monkeypatch.setattr(flow, "cancel_appointment", cancel)
    monkeypatch.setattr(flow.revision, "check_before_write", current)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    def state():
        return json.loads(redis.data.get(key, "{}"))
    async def message(body, *, message_id="synthetic-next", preview=False, requested=False, semantic_kind=None):
        return await flow.handle(tenant, phone, body, business=business, state=state(), redis=redis, key=key,
            message_id=message_id, preview=preview, requested=requested, semantic_kind=semantic_kind)
    async def propose():
        return await message("Quero cancelar minha reserva", requested=True, message_id="synthetic-summary")
    return SimpleNamespace(**locals())


@pytest.mark.asyncio
async def test_new_summary_then_natural_consent_cancels_only_owned_confirmed_record(cancellation):
    h = cancellation
    result = await h.propose()
    assert result["step"] == "cancel_confirm" and "selecionado para cancelamento" in result["answer"]
    composition = h.state()["confirmation_presentation"]
    assert composition["schema_version"] == 1 and composition["prompt"] == "Posso cancelar esse agendamento?"
    assert composition["body"] + "\n\n" + composition["prompt"] == h.state()["confirmation_summary"]
    assert result["answer"].count(composition["prompt"]) == 1
    assert "Bruno — Demonstração" in result["answer"] and "R$ 35,00" in result["answer"]
    assert "cancel" not in h.events and "_verified_operation" not in result
    assert 1 <= h.redis.ttls[h.key] <= 300
    result = await h.message("Pode cancelar")
    assert result["step"] == "cancelled" and result["_verified_operation"] is True
    assert "Cancelamento confirmado" in result["answer"] and h.record["protocol"] in result["answer"]
    assert h.events[-2:] == ["fence", "cancel"] and h.key not in h.redis.data


@pytest.mark.asyncio
async def test_nonwhitelisted_semantic_consent_keeps_local_guards(cancellation):
    h = cancellation; await h.propose()
    result = await h.message("Do jeito que você mostrou, podemos prosseguir", semantic_kind="affirm")
    assert result["step"] == "cancelled" and h.events.count("cancel") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("body", ["Pode cancelar, mas só se devolver o dinheiro", "Sim?", "Não cancela", "Pode cancelar e troca para amanhã", "Acho que sim", "Pode cancelar, quanto fica?", "Sim, mas espera", "Se for sem multa, pode cancelar", "Pode marcar", "Pode cancelar e comprar pomada"])
async def test_conditions_changes_questions_and_negation_override_model_affirm(cancellation, body):
    h = cancellation; await h.propose()
    result = await h.message(body, semantic_kind="affirm")
    assert result["step"] == ("cancel_declined" if body == "Não cancela" else "cancel_confirm") and "cancel" not in h.events
    assert "_verified_operation" not in result
    assert h.state() == {} if body == "Não cancela" else h.state()["confirmation_requires_review"] is True


@pytest.mark.asyncio
async def test_decline_closes_proposal_without_cancelling_or_reusing_later_consent(cancellation):
    h = cancellation; await h.propose()
    result = await h.message("Não")
    assert result["step"] == "cancel_declined" and h.record["status"] == "confirmed" and h.state() == {}
    assert await h.message("Sim", message_id="synthetic-unrelated-yes") is None
    assert await h.message("Pode cancelar", message_id="synthetic-old-consent") is None
    assert "cancel" not in h.events
    result = await h.message("Quero cancelar minha reserva", requested=True, message_id="synthetic-new-summary")
    assert result["step"] == "cancel_confirm" and "cancel" not in h.events
    result = await h.message("Confirmo o cancelamento", message_id="synthetic-final")
    assert result["step"] == "cancelled" and h.events.count("cancel") == 1


@pytest.mark.asyncio
async def test_natural_request_to_revisit_summary_preserves_original_deadline(cancellation):
    h = cancellation; await h.propose(); deadline = h.state()["confirmation_expires_at"]
    result = await h.message("Quero ver o resumo de novo")
    assert result["step"] == "cancel_confirm" and "Corte" in result["answer"] and "cancel" not in h.events
    assert h.state()["confirmation_message_id"] == "synthetic-next"
    assert h.state()["confirmation_expires_at"] == deadline


@pytest.mark.asyncio
@pytest.mark.parametrize("identity", [None, "synthetic-summary", "invalid space"])
async def test_missing_invalid_or_same_summary_message_id_never_cancels(cancellation, identity):
    h = cancellation; await h.propose()
    result = await h.message("Pode cancelar", message_id=identity)
    assert result["step"] == "cancel_confirm" and "cancel" not in h.events


@pytest.mark.asyncio
async def test_request_without_trusted_id_cannot_prepare_an_actionable_summary(cancellation):
    h = cancellation
    result = await h.message("Quero cancelar minha reserva", requested=True, message_id=None)
    assert result["step"] == "cancel_review" and not h.redis.data and "cancel" not in h.events


@pytest.mark.asyncio
@pytest.mark.parametrize("expired", [0, float("inf"), float("nan"), "tomorrow"])
async def test_expired_or_invalid_deadline_fails_closed_without_fresh_reads(cancellation, expired):
    h = cancellation; await h.propose(); state = h.state(); state["confirmation_expires_at"] = expired
    h.redis.data[h.key] = json.dumps(state); h.events.clear()
    result = await h.message("Pode cancelar")
    assert result["step"] == "cancel_expired" and h.events == []


@pytest.mark.asyncio
async def test_changed_booking_snapshot_presents_new_facts_without_cancelling(cancellation):
    h = cancellation; await h.propose()
    h.record.update(expected_snapshot="b"*64, start_at=h.record["start_at"]+timedelta(hours=1))
    result = await h.message("Pode cancelar")
    assert result["step"] == "cancel_confirm" and "A reserva mudou" in result["answer"] and "cancel" not in h.events
    assert h.state()["expected_snapshot"] == "b"*64
    result = await h.message("Pode cancelar", message_id="synthetic-after-new-summary")
    assert result["step"] == "cancelled" and h.events.count("cancel") == 1


@pytest.mark.asyncio
async def test_profile_change_requires_a_new_summary_even_with_valid_consent(cancellation):
    h = cancellation; await h.propose(); h.business["profile_revision"] = 2
    result = await h.message("Pode cancelar")
    assert result["step"] == "cancel_confirm" and "cancel" not in h.events


@pytest.mark.asyncio
@pytest.mark.parametrize("pending", [False, True])
async def test_published_cancellation_policy_requires_human_not_customer_yes(cancellation, pending):
    h = cancellation
    if pending:
        await h.propose()
    h.business["profile"]["policies"] = [{"topic": "Cancelamento", "description": "O prazo precisa ser avaliado pela equipe."}]
    h.events.clear()
    result = await h.message("Pode cancelar", requested=not pending, semantic_kind="affirm")
    assert result["step"] == "cancel_policy_review" and h.events == []


@pytest.mark.asyncio
async def test_unrelated_policy_is_not_a_cancellation_exception(cancellation):
    h = cancellation; h.business["profile"]["policies"] = [{"topic": "Estacionamento", "description": "Consulte a equipe."}]
    assert (await h.propose())["step"] == "cancel_confirm"


@pytest.mark.asyncio
async def test_revoked_appointment_capability_blocks_pending_cancel_before_reads(cancellation):
    h = cancellation; await h.propose(); h.business["profile"]["capabilities"] = []; h.events.clear()
    assert (await h.message("Pode cancelar"))["step"] == "capability_unavailable"
    assert h.events == []


@pytest.mark.asyncio
async def test_preview_has_zero_database_redis_or_provider_operations(cancellation):
    h = cancellation
    result = await h.message("Quero cancelar minha reserva", requested=True, preview=True)
    assert result["step"] == "preview_cancel" and h.events == [] and h.redis.data == {}
    assert "_verified_operation" not in result


@pytest.mark.asyncio
async def test_multiple_reservations_accept_natural_date_time_or_ordinal_not_only_numbers(cancellation):
    h = cancellation
    second = {**deepcopy(h.record), "id": uuid4(), "protocol": "AG-SYNTHETIC-002", "start_at": h.record["start_at"]+timedelta(hours=3)}
    h.records.append(second)
    result = await h.propose()
    assert result["step"] == "cancel_pick" and h.record["protocol"] in result["answer"] and second["protocol"] in result["answer"]
    result = await h.message("o último")
    assert result["step"] == "cancel_confirm" and h.state()["appointment"] == str(second["id"])
    assert "cancel" not in h.events


@pytest.mark.asyncio
async def test_protocol_is_candidate_selector_never_authority_to_access_foreign_record(cancellation):
    h = cancellation
    result = await h.message("Cancelar AG-FOREIGN-999", requested=True)
    assert result["step"] == "cancel_pick" and "cancel" not in h.events
    result = await h.message("AG-FOREIGN-999")
    assert result["step"] == "cancel_pick" and "cancel" not in h.events


@pytest.mark.asyncio
async def test_holds_past_and_cancelled_records_are_never_cancellation_candidates(cancellation):
    h = cancellation
    h.records.extend([{**deepcopy(h.record), "id": uuid4(), "status": status} for status in ("hold", "cancelled")])
    h.record["start_at"] = h.now-timedelta(days=1)
    result = await h.propose()
    assert result["step"] == "no_reservation" and h.events == ["list"]


@pytest.mark.asyncio
async def test_bare_cancel_during_unconfirmed_hold_asks_which_operation(cancellation):
    h = cancellation; initial = {"step": "confirm", "hold": str(uuid4())}; h.redis.data[h.key] = json.dumps(initial)
    result = await h.message("Cancelar", requested=True)
    assert result["step"] == "cancel_clarify" and h.events == [] and h.state() == initial


@pytest.mark.asyncio
async def test_unrelated_turn_without_pending_cancellation_is_not_handled(cancellation):
    h = cancellation
    assert await h.message("Qual o preço do corte?") is None and h.events == []


@pytest.mark.asyncio
async def test_oversized_factual_summary_never_prepares_or_cancels_partial_contract(cancellation):
    h = cancellation; h.record["items"][0]["name_snapshot"] = "Serviço completo " * 120
    result = await h.propose()
    assert result["step"] == "cancel_review" and not h.redis.data and "cancel" not in h.events


@pytest.mark.asyncio
async def test_write_revision_fence_is_immediately_before_backend_and_can_abort(cancellation, monkeypatch):
    h = cancellation; await h.propose()
    class NewIngress(Exception):
        pass
    async def changed():
        raise NewIngress()
    monkeypatch.setattr(flow.revision, "check_before_write", changed)
    with pytest.raises(NewIngress):
        await h.message("Pode cancelar")
    assert "cancel" not in h.events and h.record["status"] == "confirmed"


@pytest.mark.asyncio
async def test_backend_stale_race_does_not_claim_success_or_emit_verified_marker(cancellation, monkeypatch):
    h = cancellation; await h.propose()
    async def stale(*args, **kwargs):
        raise HTTPException(409, "Reserva mudou")
    monkeypatch.setattr(flow, "cancel_appointment", stale)
    result = await h.message("Pode cancelar")
    assert result["step"] == "cancel_review" and "_verified_operation" not in result
    assert h.record["status"] == "confirmed" and h.state()["confirmation_requires_review"]


@pytest.mark.asyncio
async def test_cross_contact_scope_cannot_reuse_a_valid_pending_summary(cancellation):
    h = cancellation; await h.propose(); state = h.state(); state["cancellation_scope"] = flow._scope(uuid4(), h.phone)
    h.redis.data[h.key] = json.dumps(state); h.events.clear()
    result = await h.message("Pode cancelar")
    assert result["step"] == "cancel_expired" and not h.events


@pytest.mark.asyncio
@pytest.mark.parametrize("selector", ["com Carlos", "a das 14h", "a de segunda às 14h", "AG-SYNTHETIC-002"])
async def test_unique_natural_name_date_time_or_protocol_selects_only_matching_owned_candidate(cancellation, selector):
    h = cancellation
    second = {**deepcopy(h.record), "id": uuid4(), "protocol": "AG-SYNTHETIC-002", "professional": "Carlos",
        "start_at": h.record["start_at"].replace(hour=18)}  # 14h in the verified timezone.
    h.records.append(second)
    await h.propose()
    result = await h.message(selector)
    assert result["step"] == "cancel_confirm" and h.state()["appointment"] == str(second["id"])
    assert "cancel" not in h.events


@pytest.mark.asyncio
@pytest.mark.parametrize("selector", ["segunda", "com Bruno", "com Jorge", "AG-SYNTHETIC-001 ou AG-SYNTHETIC-002"])
async def test_ambiguous_or_unknown_natural_selection_never_chooses_first_candidate(cancellation, selector):
    h = cancellation
    second = {**deepcopy(h.record), "id": uuid4(), "protocol": "AG-SYNTHETIC-002", "start_at": h.record["start_at"]+timedelta(hours=2)}
    h.records.append(second)
    await h.propose()
    result = await h.message(selector)
    assert result["step"] == "cancel_pick" and "appointment" not in h.state() and "cancel" not in h.events


@pytest.mark.asyncio
@pytest.mark.parametrize("body", ["Pode cancelar AG-FOREIGN-999", "Pode cancelar com Jorge"])
async def test_consent_cannot_point_to_another_unknown_reservation(cancellation, body):
    h = cancellation; await h.propose()
    result = await h.message(body, semantic_kind="affirm")
    assert result["step"] == "cancel_confirm" and "cancel" not in h.events


@pytest.mark.asyncio
@pytest.mark.parametrize("body", ["Pode cancelar o segundo", "Pode cancelar o outro", "Confirmo o cancelamento de ambos", "Pode cancelar com Bruno e Carlos", "Pode cancelar a barba", "Confirmo o cancelamento da barba"])
async def test_new_target_reference_overrides_semantic_affirm_and_requires_new_summary(cancellation, body):
    h = cancellation
    h.records.append({**deepcopy(h.record), "id": uuid4(), "protocol": "AG-SYNTHETIC-002", "start_at": h.record["start_at"]+timedelta(hours=3)})
    await h.propose()
    assert (await h.message("o primeiro"))["step"] == "cancel_confirm"
    result = await h.message(body, message_id="synthetic-wrong-target", semantic_kind="affirm")
    assert result["step"] == "cancel_confirm" and "cancel" not in h.events and h.state()["confirmation_requires_review"]
    assert (await h.message("Quero ver o resumo de novo", message_id="synthetic-review"))["step"] == "cancel_confirm"
    result = await h.message("Pode cancelar", message_id="synthetic-after-review")
    assert result["step"] == "cancelled" and h.record["status"] == "cancelled" and h.records[1]["status"] == "confirmed"


@pytest.mark.asyncio
async def test_booking_that_starts_during_five_minute_summary_window_cannot_be_cancelled(cancellation, monkeypatch):
    h = cancellation
    h.record.update(start_at=h.now+timedelta(minutes=2), end_at=h.now+timedelta(minutes=42))
    await h.propose()
    monkeypatch.setattr(flow, "_now", lambda: h.now+timedelta(minutes=3))
    result = await h.message("Pode cancelar")
    assert result["step"] == "cancel_expired" and "cancel" not in h.events and h.record["status"] == "confirmed"


@pytest.mark.asyncio
async def test_stale_pick_cannot_prepare_summary_after_booking_started(cancellation, monkeypatch):
    h = cancellation
    h.record.update(start_at=h.now+timedelta(minutes=2), end_at=h.now+timedelta(minutes=42))
    h.records.append({**deepcopy(h.record), "id": uuid4(), "protocol": "AG-SYNTHETIC-002", "start_at": h.now+timedelta(days=1)})
    await h.propose()
    monkeypatch.setattr(flow, "_now", lambda: h.now+timedelta(minutes=3))
    result = await h.message("o primeiro")
    assert result["step"] == "cancel_expired" and "cancel" not in h.events


@pytest.mark.asyncio
@pytest.mark.parametrize("body", ["com Bruno e Carlos", "com Carlos e Jorge"])
async def test_mixed_complete_and_partial_professional_names_do_not_select_one(cancellation, body):
    h = cancellation
    h.records.append({**deepcopy(h.record), "id": uuid4(), "professional": "Carlos", "protocol": "AG-SYNTHETIC-002", "start_at": h.record["start_at"]+timedelta(hours=2)})
    await h.propose()
    result = await h.message(body)
    assert result["step"] == "cancel_pick" and "appointment" not in h.state() and "cancel" not in h.events


@pytest.fixture
def routed_cancellation(cancellation, monkeypatch):
    h = cancellation
    h.business["profile"]["capabilities"].extend(["catalog_answers", "rescheduling"])
    h.catalog = [{"id": h.service, "name": "Corte", "price_cents": 3500, "duration_min": 40},
        {"id": uuid4(), "name": "Barba", "price_cents": 2000, "duration_min": 20}]
    h.business["profile"]["faqs"] = [{"question": "Aceitam Pix?", "answer": "Aceitamos Pix, conforme cadastro."}]
    h.professionals = [{"id": h.pro, "name": h.record["professional"]}]
    async def data(t):
        assert t == h.tenant
        return h.business, h.catalog, h.professionals
    async def planner(**kwargs):
        return flow.build_plan(message=kwargs["message"], catalog=h.catalog, professionals=h.professionals,
            context=kwargs["context"], faqs=kwargs["faqs"], reference_date=kwargs["reference_date"])
    async def legacy(**kwargs):
        return qr_ai.Intention(intent="perguntar" if qr_ai.catalog_question(kwargs["message"]) else "outro", service_ids=[])
    monkeypatch.setattr(qr_ai, "tenant_data", data)
    monkeypatch.setattr(qr_ai, "interpret_plan", planner)
    monkeypatch.setattr(qr_ai, "interpret", legacy)
    monkeypatch.setenv("QR_AI_MULTI_INTENT_ENABLED", "0")
    monkeypatch.setenv("QR_AI_COMMERCE_ENABLED", "0")
    async def turn(body, identity="synthetic-next", preview=False):
        token = qr_ai._message_identity.set(identity)
        try:
            return await qr_ai.turn(h.tenant, h.phone, body, h.redis, h.phone, preview=preview)
        finally:
            qr_ai._message_identity.reset(token)
    h.turn = turn
    return h


@pytest.mark.asyncio
@pytest.mark.parametrize("multi", ["0", "1"])
async def test_real_qr_turn_cancels_after_only_a_fresh_natural_consent(routed_cancellation, monkeypatch, multi):
    h = routed_cancellation; monkeypatch.setenv("QR_AI_MULTI_INTENT_ENABLED", multi)
    proposal = await h.turn("Quero cancelar minha reserva", "synthetic-summary")
    assert proposal["step"] == "cancel_confirm" and "cancel" not in h.events
    result = await h.turn("Pode cancelar", "synthetic-confirm")
    assert result["step"] == "cancelled" and result["_verified_operation"] is True and h.events.count("cancel") == 1


@pytest.mark.asyncio
async def test_price_interrupts_real_cancel_turn_then_review_and_new_consent_are_required(routed_cancellation):
    h = routed_cancellation
    await h.turn("Quero cancelar minha reserva", "synthetic-summary")
    deadline = h.state()["confirmation_expires_at"]
    answer = await h.turn("Quanto custa o corte?", "synthetic-question")
    assert "R$ 35,00" in answer["answer"] and h.state()["confirmation_requires_review"] and "cancel" not in h.events
    review = await h.turn("Pode confirmar", "synthetic-review")
    assert review["step"] == "cancel_confirm" and h.state()["confirmation_message_id"] == "synthetic-review"
    assert h.state()["confirmation_expires_at"] == deadline and "cancel" not in h.events
    result = await h.turn("Confirmo o cancelamento", "synthetic-final")
    assert result["step"] == "cancelled" and h.events.count("cancel") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("multi", ["0", "1"])
@pytest.mark.parametrize("question,fact", [("Quanto custa só a barba?", "R$ 20,00"),
    ("Aceitam Pix?", "Aceitamos Pix, conforme cadastro."), ("Pode cancelar. Quanto custa só a barba?", "R$ 20,00")])
async def test_pending_cancel_answers_price_or_faq_without_mutation_then_requires_review_and_another_id(routed_cancellation, monkeypatch, multi, question, fact):
    h = routed_cancellation; monkeypatch.setenv("QR_AI_MULTI_INTENT_ENABLED", multi)
    await h.turn("Quero cancelar minha reserva", "synthetic-summary")
    original = h.state()
    answer = await h.turn(question, "synthetic-question")
    assert fact in answer["answer"] and "cancel" not in h.events and h.record["status"] == "confirmed"
    assert h.state()["appointment"] == original["appointment"] and h.state()["confirmation_requires_review"]
    assert h.state()["confirmation_expires_at"] == original["confirmation_expires_at"]
    review = await h.turn("Confirmo", "synthetic-review")
    assert review["step"] == "cancel_confirm" and "Corte" in review["answer"] and "cancel" not in h.events
    assert h.state()["confirmation_message_id"] == "synthetic-review"
    result = await h.turn("Pode cancelar", "synthetic-after-review")
    assert result["step"] == "cancelled" and h.events.count("cancel") == 1


@pytest.mark.asyncio
async def test_real_cancel_turn_cannot_accept_same_ingress_id_or_write_in_preview(routed_cancellation):
    h = routed_cancellation
    preview = await h.turn("Quero cancelar minha reserva", preview=True)
    assert preview["step"] == "preview_cancel" and not h.events
    await h.turn("Quero cancelar minha reserva", "synthetic-summary")
    answer = await h.turn("Pode cancelar", "synthetic-summary")
    assert answer["step"] == "cancel_confirm" and "cancel" not in h.events


@pytest.mark.asyncio
async def test_cancel_completed_before_late_ingress_is_preserved_in_receipt_not_replayed(routed_cancellation, monkeypatch):
    import redis.asyncio as redis_asyncio
    from test_qr_revision_hooks import RevisionRedis
    from app import qr_operation_receipt
    h = routed_cancellation
    client = RevisionRedis(); client.values = h.redis.data
    monkeypatch.setattr(redis_asyncio, "from_url", lambda *args, **kwargs: client)
    monkeypatch.setenv("QR_AI_INTAKE_REVISION_ENABLED", "1")
    monkeypatch.setattr(flow.revision, "check_before_write", ORIGINAL_WRITE_FENCE)
    proposal = await qr_ai.run_locked(h.tenant, h.phone, "Quero cancelar minha reserva", h.phone,
        "synthetic-message", intake_revision=1)
    assert proposal["step"] == "cancel_confirm"
    backend = flow.cancel_appointment
    async def cancel_then_new_message(*args, **kwargs):
        value = await backend(*args, **kwargs)
        client.current = 3; client.seen["synthetic-correction"] = 3
        return value
    monkeypatch.setattr(flow, "cancel_appointment", cancel_then_new_message)
    client.current = 2; client.seen["synthetic-confirm"] = 2
    result = await qr_ai.run_locked(h.tenant, h.phone, "Pode cancelar", h.phone,
        "synthetic-confirm", intake_revision=2)
    assert result["step"] == "superseded_after_write" and result["not_committed"] is False
    assert qr_operation_receipt.key(h.tenant, h.phone) in client.values and h.events.count("cancel") == 1
    receipt = await qr_ai.run_locked(h.tenant, h.phone, "Pera, quero outro horário", h.phone,
        "synthetic-correction", intake_revision=3, require_summary_review=True)
    assert receipt["step"] == "completed_operation_review" and h.record["protocol"] in receipt["answer"]
    assert "_verified_operation" not in receipt and h.events.count("cancel") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("body", ["Oi", "Quanto custa o corte?", "Não quero continuar com você", "Quero continuar com o bot se tiver desconto", "Quero continuar com você?", "Quero continuar com você, não, melhor falar com humano"])
async def test_paused_real_turn_stays_silent_for_questions_negation_or_conditional_resume(routed_cancellation, body):
    h = routed_cancellation; h.redis.data[h.key] = json.dumps({"step": "human"})
    result = await h.turn(body)
    assert result == {"answer": None, "step": "human"} and h.state()["step"] == "human" and not h.events


@pytest.mark.asyncio
@pytest.mark.parametrize("body", ["Quero continuar com você", "Quero continuar com o assistente", "Quero continuar com o bot", "Quero continuar com a IA", "Voltar ao atendimento automático", "Quero continuar com você e agendar um corte"])
async def test_explicit_natural_resume_clears_old_plan_and_only_acknowledges_no_operation(routed_cancellation, body):
    h = routed_cancellation
    h.redis.data[h.key] = json.dumps({"step": "human", "hold": str(uuid4()), "services": [str(h.service)]})
    h.redis.data[h.key+":commerce"] = json.dumps({"pending": {"token": "private-synthetic"}})
    result = await h.turn(body)
    assert result["step"] == "menu" and "Voltei ao atendimento automático" in result["answer"]
    assert h.state() == {} and h.key+":commerce" not in h.redis.data and not h.events


@pytest.mark.asyncio
@pytest.mark.parametrize("body", ["Cancela", "Desmarca"])
async def test_exact_current_cancel_reply_needs_no_provider_even_after_semantic_decline(cancellation, monkeypatch, body):
    h = cancellation
    async def forbidden(**_):
        raise AssertionError("The complete explicit reply is resolved locally")
    monkeypatch.setattr(flow, "interpret_contextual_response", forbidden)
    await h.propose()
    result = await h.message(body, semantic_kind="decline")
    assert result["step"] == "cancelled" and h.events.count("cancel") == 1 and h.state() == {}
    assert await h.message(body, message_id="synthetic-after-completion") is None
    assert h.events.count("cancel") == 1


@pytest.mark.asyncio
async def test_bulk_pick_explains_single_reservation_then_exact_reply_cancels_only_displayed_first(cancellation):
    h = cancellation
    second = {**deepcopy(h.record), "id": uuid4(), "protocol": "AG-SYNTHETIC-002", "start_at": h.record["start_at"]+timedelta(hours=2)}
    h.records.append(second)
    assert (await h.propose())["step"] == "cancel_pick"
    result = await h.message("Todos")
    assert "uma reserva por vez" in result["answer"] and "appointment" not in h.state()
    await h.message("O primeiro", message_id="synthetic-selected-summary")
    result = await h.message("Cancela", message_id="synthetic-selected-consent")
    assert result["step"] == "cancelled" and h.record["status"] == "cancelled" and second["status"] == "confirmed"
    assert h.events.count("cancel") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("stage,body", [("pick", "Não"), ("confirm", "Não cancela"),
    ("confirm", "Prefiro manter a reserva"), ("confirm", "Desisto do cancelamento"),
    ("confirm", "Cancela… não, deixa como está")])
async def test_whole_withdrawal_revokes_only_request_and_unrelated_yes_cannot_resurrect_it(cancellation, stage, body):
    h = cancellation
    if stage == "pick":
        h.records.append({**deepcopy(h.record), "id": uuid4(), "protocol": "AG-SYNTHETIC-002"})
    await h.propose()
    before = deepcopy(h.records); reads = h.events[:]
    result = await h.message(body)
    assert result["step"] == "cancel_declined" and "_verified_operation" not in result and h.state() == {}
    assert "Quando precisar" in result["answer"] and "ajustar" not in result["answer"]
    assert h.records == before and h.events == reads
    assert await h.message("Não", message_id="synthetic-repeated-no") is None
    assert await h.message("Sim", message_id="synthetic-unrelated-yes") is None
    assert h.records == before and "cancel" not in h.events


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["policy", "capability", "expired"])
async def test_withdrawal_still_clears_request_when_operation_is_no_longer_permitted(cancellation, monkeypatch, change):
    h = cancellation; await h.propose()
    if change == "policy":
        h.business["profile"]["policies"] = [{"topic": "Cancelamento", "description": "Conferir com a equipe."}]
    elif change == "capability":
        h.business["profile"]["capabilities"] = []
    else:
        monkeypatch.setattr(flow, "_now", lambda: h.now+timedelta(minutes=6))
    result = await h.message("Mantenha")
    assert result["step"] == "cancel_declined" and h.state() == {} and h.record["status"] == "confirmed"
    assert "cancel" not in h.events


@pytest.mark.asyncio
@pytest.mark.parametrize("body", ["Como manter a reserva?", "Não cancela se tiver multa", "Não cancela hoje, melhor amanhã", "Não, cancela"])
async def test_question_condition_or_correction_is_not_an_unequivocal_withdrawal_or_consent(cancellation, body):
    h = cancellation; await h.propose()
    result = await h.message(body, semantic_kind="affirm")
    assert result["step"] == "cancel_confirm" and h.state()["confirmation_requires_review"]
    assert h.record["status"] == "confirmed" and "cancel" not in h.events


@pytest.mark.asyncio
@pytest.mark.parametrize("body", ["Cancela?", "Cancela se não tiver multa", "Cancela o segundo", "Cancela ambos"])
async def test_exact_imperative_guard_does_not_accept_question_condition_or_changed_target(cancellation, body):
    h = cancellation; await h.propose()
    result = await h.message(body, semantic_kind="affirm")
    assert result["step"] == "cancel_confirm" and h.state()["confirmation_requires_review"]
    assert h.record["status"] == "confirmed" and "cancel" not in h.events


@pytest.mark.asyncio
async def test_exact_cancel_reply_still_requires_another_trusted_message_id(cancellation):
    h = cancellation; await h.propose()
    result = await h.message("Cancela", message_id="synthetic-summary")
    assert result["step"] == "cancel_confirm" and "cancel" not in h.events
    result = await h.message("Cancela", message_id="synthetic-later-consent")
    assert result["step"] == "cancelled" and h.events.count("cancel") == 1


@pytest.mark.asyncio
async def test_exact_cancel_reply_after_expiry_cannot_write(cancellation, monkeypatch):
    h = cancellation; await h.propose()
    monkeypatch.setattr(flow, "_now", lambda: h.now+timedelta(minutes=6))
    result = await h.message("Cancela")
    assert result["step"] == "cancel_expired" and "cancel" not in h.events and h.state() == {}
    assert await h.message("Sm", message_id="synthetic-after-expiry") is None


@pytest.mark.asyncio
async def test_context_change_requires_reproposal_before_exact_cancel_reply_can_write(cancellation):
    h = cancellation; await h.propose(); deadline = h.state()["confirmation_expires_at"]
    h.business["profile_revision"] += 1
    result = await h.message("Cancela", message_id="synthetic-context-review")
    assert result["step"] == "cancel_confirm" and "cancel" not in h.events
    assert h.state()["confirmation_expires_at"] == deadline
    result = await h.message("Cancela", message_id="synthetic-after-context-review")
    assert result["step"] == "cancelled" and h.events.count("cancel") == 1


@pytest.mark.asyncio
async def test_uncertain_interruption_then_natural_review_needs_new_exact_cancel_consent(cancellation):
    h = cancellation; await h.propose(); deadline = h.state()["confirmation_expires_at"]
    await h.message("Ainda estou decidindo", message_id="synthetic-interruption")
    result = await h.message("Mostra de novo", message_id="synthetic-natural-review")
    assert result["step"] == "cancel_confirm" and "Corte" in result["answer"]
    assert h.state()["appointment"] == str(h.record["id"])
    assert h.state()["confirmation_expires_at"] == deadline and "cancel" not in h.events
    same = await h.message("Cancela", message_id="synthetic-natural-review")
    assert same["step"] == "cancel_confirm" and "cancel" not in h.events
    result = await h.message("Cancela", message_id="synthetic-after-natural-review")
    assert result["step"] == "cancelled" and h.events.count("cancel") == 1


@pytest.mark.asyncio
async def test_cross_contact_state_cannot_be_used_or_erased_by_a_short_reply(cancellation):
    h = cancellation; await h.propose(); before = deepcopy(h.state())
    for body in ("Cancela", "Não"):
        result = await flow.handle(h.tenant, "5567999999901", body, business=h.business, state=deepcopy(before),
            redis=h.redis, key=h.key, message_id="synthetic-foreign")
        assert result["step"] == "cancel_expired" and h.state() == before
    assert "cancel" not in h.events and h.record["status"] == "confirmed"


@pytest.mark.asyncio
async def test_preview_does_not_cancel_or_erase_a_real_pending_request(cancellation):
    h = cancellation; await h.propose(); before = deepcopy(h.state()); events = h.events[:]
    for body in ("Cancela", "Não"):
        assert (await h.message(body, preview=True))["step"] == "preview_cancel"
    assert h.state() == before and h.events == events


@pytest.mark.asyncio
async def test_exact_reply_after_record_revocation_cannot_cancel_another_record(cancellation):
    h = cancellation; await h.propose()
    h.record["status"] = "cancelled"
    h.records.append({**deepcopy(h.record), "id": uuid4(), "status": "confirmed", "protocol": "AG-SYNTHETIC-OTHER"})
    result = await h.message("Cancela")
    assert result["step"] == "no_reservation" and "cancel" not in h.events
    assert h.records[-1]["status"] == "confirmed"
