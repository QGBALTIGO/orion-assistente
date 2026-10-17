"""Real conversation engine regressions; providers, storage and writes stay local."""
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app import qr_ai
from app.groq_ai import GroqServiceError, Intention


class MemoryRedis:
    def __init__(self):
        self.values = {}
        self.ttls = {}

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value, ex=None):
        self.values[key] = value
        self.ttls[key] = ex

    async def delete(self, key):
        self.values.pop(key, None)


@pytest.fixture
def conversation(monkeypatch):
    tenant, cut, beard, bruno, carlos = (uuid4() for _ in range(5))
    redis = MemoryRedis()
    namespace = "preview:interruptions"
    key = f"orion:qr:ai:{tenant}:{namespace}"
    catalog = [
        {"id": cut, "name": "Corte de cabelo", "price_cents": 3500, "duration_min": 30},
        {"id": beard, "name": "Barba", "price_cents": 2000, "duration_min": 20},
    ]
    pros = [{"id": bruno, "name": "Bruno Souza"}, {"id": carlos, "name": "Carlos"}]
    calls = {"classify": [], "pros": [], "slots": [], "writes": []}
    async def data(t):
        assert t == tenant
        return {"name": "Salão Exemplo", "timezone": "America/Campo_Grande"}, catalog, pros
    async def classify(**kwargs):
        calls["classify"].append(kwargs)
        message = kwargs["message"].lower()
        if qr_ai.catalog_question(message):
            return Intention(intent="perguntar", service_ids=[cut, beard])
        if "barba" in message:
            return Intention(intent="agendar", service_ids=[beard])
        if "cabelo" in message or "corte" in message:
            return Intention(intent="agendar", service_ids=[cut])
        return Intention(intent="outro")
    async def professionals(t, services):
        assert t == tenant
        assert all(service in {cut, beard} for service in services)
        calls["pros"].append(list(services))
        return pros
    async def slots(t, state, preview):
        assert t == tenant
        calls["slots"].append(dict(state))
        selected = [item for item in catalog if str(item["id"]) in state["services"]]
        state.update(step="time", price=sum(item["price_cents"] for item in selected),
                     duration=sum(item["duration_min"] for item in selected),
                     slots=[state["day"] + "T14:00:00-04:00"])
        return "1. 14:00", state
    async def forbidden(*args, **kwargs):
        calls["writes"].append((args, kwargs))
        raise AssertionError("Information and choices cannot mutate a booking")
    monkeypatch.setattr(qr_ai, "tenant_data", data)
    monkeypatch.setattr(qr_ai, "interpret", classify)
    monkeypatch.setattr(qr_ai, "professionals_for", professionals)
    monkeypatch.setattr(qr_ai, "offer_slots", slots)
    for name in ("book_appointment", "confirm_hold", "reschedule_appointment"):
        monkeypatch.setattr(qr_ai, name, forbidden)
    async def turn(message, preview=True):
        return await qr_ai.turn(tenant, "5567999999999", message, redis, namespace, preview)
    def state():
        return json.loads(redis.values.get(key, "{}"))
    def seed(step="professional", **extra):
        value = {"step": step, "services": [str(cut)], "day": "2026-11-20",
                 "professionals": [{"id": str(p["id"]), "name": p["name"]} for p in pros],
                 "customer_name": "Cliente Teste", **extra}
        redis.values[key] = json.dumps(value)
        return value
    return SimpleNamespace(**locals())


@pytest.mark.asyncio
async def test_capture_price_interruptions_preserve_booking_and_accept_o_bruno(conversation):
    h = conversation
    await h.turn("Quero cortar o cabelo")
    await h.turn("amanhã")
    before = h.state()
    assert before["step"] == "professional"
    for question in ("Quanto custa um cabelo e uma barba?",
                     "Não, quero saber quanto custa o corte de cabelo e a barba"):
        reply = await h.turn(question)
        assert "R$ 35,00" in reply["answer"] and "R$ 20,00" in reply["answer"]
        assert "Total desses serviços: R$ 55,00" in reply["answer"]
        assert reply["step"] == "professional"
        assert h.state()["services"] == before["services"]
        assert h.state()["day"] == before["day"]
        assert h.state()["professionals"] == before["professionals"]
    reply = await h.turn("O bruno")
    assert reply["step"] == "time"
    assert h.state()["professional"] == str(h.bruno)
    assert not h.calls["writes"]


@pytest.mark.asyncio
@pytest.mark.parametrize("step", ["date", "professional", "time", "confirm",
                                  "reschedule_day", "reschedule_time", "reschedule_confirm"])
async def test_catalog_questions_at_every_stage_never_select_or_confirm(conversation, step):
    h = conversation
    original = h.seed(step, professional=str(h.bruno), hold=str(uuid4()), price=3500,
                      duration=30, slots=["2026-11-20T14:00:00-04:00"], appointment=str(uuid4()))
    reply = await h.turn("Quanto custa corte de cabelo e barba?", preview=False)
    assert "R$ 55,00" in reply["answer"]
    assert reply["step"] == step
    assert {k: v for k, v in h.state().items() if k not in {"history", "confirmation_expires_at", "confirmation_requires_review"}} == original
    assert bool(h.state().get("confirmation_requires_review")) == (step in {"confirm", "reschedule_confirm"})
    assert not h.calls["writes"] and not h.calls["slots"]


@pytest.mark.asyncio
@pytest.mark.parametrize("step", ["date", "professional", "time", "confirm"])
async def test_only_beard_replaces_services_and_drops_old_confirmation(conversation, step):
    h = conversation
    h.seed(step, professional=str(h.bruno), hold=str(uuid4()), price=3500,
           duration=30, slots=["2026-11-20T15:00:00-04:00"])
    reply = await h.turn("Quero só fazer a barba")
    after = h.state()
    assert after["services"] == [str(h.beard)]
    assert after["day"] == "2026-11-20" and after["professional"] == str(h.bruno)
    assert after["step"] == reply["step"] == "time"
    assert "hold" not in after
    assert after["price"] == 2000 and after["duration"] == 20
    assert h.calls["pros"] == [[h.beard]]
    assert h.calls["slots"][0]["services"] == [str(h.beard)]
    assert not h.calls["writes"]


@pytest.mark.asyncio
async def test_date_in_change_of_service_is_not_consumed_as_plain_date(conversation):
    h = conversation
    h.seed("date", services=[str(h.beard)])
    reply = await h.turn("só o cabelo amanhã")
    assert h.calls["classify"][-1]["message"] == "só o cabelo amanhã"
    assert h.state()["services"] == [str(h.cut)]
    assert reply["step"] == "professional"


@pytest.mark.asyncio
async def test_ambiguous_professional_name_cannot_choose_first(conversation):
    h = conversation
    h.pros.append({"id": uuid4(), "name": "Bruno Lima"})
    h.seed()
    reply = await h.turn("O Bruno")
    assert reply["step"] == "professional"
    assert "professional" not in h.state()
    assert not h.calls["slots"]
    reply = await h.turn("Prefiro o Bruno Lima")
    assert reply["step"] == "time"
    assert h.state()["professional"] == str(h.pros[-1]["id"])


@pytest.mark.asyncio
async def test_named_professional_before_day_is_preserved(conversation):
    h = conversation
    reply = await h.turn("Quero barba com Bruno")
    assert reply["step"] == "date"
    assert h.state()["professional"] == str(h.bruno)
    await h.turn("amanhã")
    assert h.state()["professional"] == str(h.bruno)
    assert h.state()["step"] == "time"


@pytest.mark.asyncio
async def test_explicit_professional_replaces_previous_even_with_day_in_phrase(conversation):
    h = conversation
    h.seed("time", professional=str(h.carlos))
    reply = await h.turn("Quero barba com Bruno amanhã")
    assert reply["step"] == "time"
    assert h.state()["professional"] == str(h.bruno)
    assert h.state()["professional_name"] == "Bruno Souza"
    confirmation = await h.turn("1")
    assert "Barba com Bruno Souza" in confirmation["answer"]


@pytest.mark.asyncio
@pytest.mark.parametrize("requested", ["Jorge", "Bruno"])
async def test_explicit_unknown_or_ambiguous_professional_never_falls_back_to_previous(conversation, requested):
    h = conversation
    h.pros.append({"id": uuid4(), "name": "Bruno Lima"})
    h.seed("time", professional=str(h.carlos))
    reply = await h.turn(f"Quero barba com {requested} amanhã")
    assert reply["step"] == "professional"
    assert "professional" not in h.state()
    assert "Não identifiquei" in reply["answer"]
    assert not h.calls["slots"]


@pytest.mark.asyncio
async def test_fallback_never_selects_negated_service_or_loses_current_flow(conversation, monkeypatch):
    h = conversation
    async def fail(**_):
        raise GroqServiceError(503, "offline")
    monkeypatch.setattr(qr_ai, "interpret", fail)
    original = h.seed()
    reply = await h.turn("Não quero corte de cabelo, quero só barba")
    assert reply["degraded"] and reply["step"] == "professional"
    assert h.state()["services"] == original["services"]
    assert not h.calls["slots"] and not h.calls["writes"]
    question = await h.turn("Quanto custa corte de cabelo e barba?")
    assert question["degraded"] and "R$ 35,00" in question["answer"] and "R$ 20,00" in question["answer"]
    assert h.state()["services"] == original["services"]
    partial = await h.turn("Quanto custa um cabelo e uma barba?")
    assert "R$ 35,00" in partial["answer"] and "R$ 20,00" in partial["answer"]
    assert "Total desses serviços: R$ 55,00" in partial["answer"]


@pytest.mark.asyncio
@pytest.mark.parametrize("step", ["date", "professional", "time", "confirm", "reschedule_confirm"])
async def test_human_pauses_every_state_until_menu(conversation, step):
    h = conversation
    h.seed(step)
    reply = await h.turn("Quero falar com atendente humano")
    assert reply["step"] == "human"
    called = len(h.calls["classify"])
    for message in ("Quanto custa a barba?", "sim", "Quero cortar amanhã", "oi"):
        assert (await h.turn(message))["answer"] is None
    assert len(h.calls["classify"]) == called and not h.calls["writes"]
    resumed = await h.turn("MENU")
    assert resumed["step"] == "menu"
    assert h.state()["history"][0]["content"] == "MENU"


@pytest.mark.asyncio
async def test_cancel_pending_flow_does_not_cancel_existing_reservations(conversation):
    h = conversation
    h.seed("confirm", hold=str(uuid4()))
    reply = await h.turn("quero cancelar")
    assert reply["step"] == "cancelled" and not h.state()
    assert "Reservas já confirmadas permanecem" in reply["answer"]
    assert not h.calls["writes"]
    reply = await h.turn("cancelar minha reserva")
    assert reply["step"] == "cancel_request" and not h.calls["writes"]


@pytest.mark.asyncio
async def test_recent_history_and_structured_context_reach_classifier(conversation):
    h = conversation
    h.seed()
    for i in range(6):
        await h.turn(f"Quanto custa corte de cabelo e barba? Pergunta {i}")
    seen = h.calls["classify"][-1]
    assert len(seen["history"]) == 8
    assert seen["history"][-2]["content"].endswith("Pergunta 4")
    assert "R$ 55,00" in seen["history"][-1]["content"]
    assert seen["context"]["etapa"] == "professional"
    assert seen["context"]["servicos_selecionados"] == ["Corte de cabelo"]
    assert h.state()["history"][-2]["content"].endswith("Pergunta 5")


@pytest.mark.asyncio
async def test_other_tenant_and_unknown_service_ids_never_enter_selection(conversation, monkeypatch):
    h = conversation
    other_tenant, other_service = uuid4(), uuid4()
    h.redis.values[f"orion:qr:ai:{other_tenant}:{h.namespace}"] = json.dumps({
        "step": "confirm", "services": [str(other_service)], "history": [
            {"role": "assistant", "content": "PRIVATE OTHER TENANT"}]})
    async def foreign(**kwargs):
        assert "PRIVATE OTHER TENANT" not in json.dumps(kwargs, default=str)
        return Intention(intent="agendar", service_ids=[other_service])
    monkeypatch.setattr(qr_ai, "interpret", foreign)
    reply = await h.turn("Quero o serviço do outro estabelecimento")
    assert reply["step"] == "menu"
    assert "services" not in h.state() and not h.calls["pros"] and not h.calls["writes"]


@pytest.mark.asyncio
async def test_booking_conflict_still_fails_closed_and_never_confirms(conversation, monkeypatch):
    h = conversation
    h.seed("time", professional=str(h.bruno), slots=["2026-11-20T14:00:00-04:00"],
           price=3500, duration=30)
    class Session:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_):
            pass
    monkeypatch.setattr(qr_ai, "get_session_factory", lambda: Session)
    async def conflict(*args):
        raise HTTPException(409, "occupied")
    monkeypatch.setattr(qr_ai, "book_appointment", conflict)
    reply = await h.turn("1", preview=False)
    assert reply["step"] == "conflict" and not h.state()
    assert "não está mais disponível" in reply["answer"]
    assert not h.calls["writes"]


@pytest.mark.asyncio
async def test_price_question_overrides_adverse_booking_classification(conversation, monkeypatch):
    h = conversation
    original = h.seed("confirm", hold=str(uuid4()))
    async def adverse(**_):
        return Intention(intent="agendar", service_ids=[h.cut, h.beard])
    monkeypatch.setattr(qr_ai, "interpret", adverse)
    reply = await h.turn("Não, quero saber quanto custa corte de cabelo e barba")
    assert "R$ 55,00" in reply["answer"] and reply["step"] == "confirm"
    assert h.state()["hold"] == original["hold"]
    assert h.state()["services"] == original["services"]
    assert not h.calls["writes"] and not h.calls["slots"]


@pytest.mark.asyncio
async def test_price_question_does_not_renew_confirmation_window(conversation):
    h = conversation
    h.seed("confirm", hold=str(uuid4()), confirmation_expires_at=qr_ai.datetime.now().timestamp() + 40)
    await h.turn("Quanto custa corte de cabelo e barba?")
    assert 1 <= h.redis.ttls[h.key] <= 40


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["Quero remarcar com atendente humano",
                                     "Quero falar com uma pessoa sobre minha reserva"])
async def test_human_request_precedes_reschedule_or_history(conversation, message):
    h = conversation
    h.seed("confirm")
    reply = await h.turn(message)
    assert reply["step"] == "human" and not h.calls["writes"]


@pytest.mark.asyncio
async def test_disfluent_audio_price_subject_resolves_empty_model_selection(conversation, monkeypatch):
    h = conversation
    h.catalog.append({"id": uuid4(), "name": "Sobrancelha", "price_cents": 1200, "duration_min": 10})
    async def empty(**_):
        return Intention(intent="perguntar", service_ids=[])
    monkeypatch.setattr(qr_ai, "interpret", empty)
    question = "Cara, eu tava querendo cortar o cabelo, mas eu não sei o valor. Quanto custa o cabelo e a barba?"
    for message in (question, "[Áudio transcrito] " + question,
                    "Quanto custa cortar o cabelo e fazer a barba?",
                    "Quanto que custa cabelo e barba?", "Cabelo e barba ficam quanto?"):
        reply = await h.turn(message)
        assert "Total desses serviços: R$ 55,00" in reply["answer"]
        assert "Corte de cabelo" in reply["answer"] and "Barba" in reply["answer"]
        assert "Sobrancelha" not in reply["answer"]
        assert "services" not in h.state() and not h.calls["writes"]


@pytest.mark.asyncio
@pytest.mark.parametrize("subject", ["cabelo e barba", "cabelo e barba e tatuagem", "barba sem corte de cabelo",
                                     "cabelo e barba e tatuagem no total", "tatuagem no total"])
async def test_ambiguous_unknown_or_negated_price_subject_cannot_produce_false_total(conversation, monkeypatch, subject):
    h = conversation
    if subject == "cabelo e barba":
        h.catalog.append({"id": uuid4(), "name": "Corte infantil", "price_cents": 2200, "duration_min": 25})
    async def partial(**_):
        return Intention(intent="perguntar", service_ids=[h.cut, h.beard])
    monkeypatch.setattr(qr_ai, "interpret", partial)
    h.seed()
    reply = await h.turn("Quanto custa " + subject + "?")
    assert "Total desses serviços" not in reply["answer"]
    if subject != "tatuagem no total":
        assert "confirme quais serviços" in reply["answer"]
    assert not h.calls["writes"]


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["não quero corte, só barba, quanto fica?", "cabelo e barba, quanto custa?"])
async def test_price_question_after_subject_cannot_hide_negation_or_ambiguity(conversation, monkeypatch, message):
    h = conversation
    if message.startswith("cabelo"):
        h.catalog.append({"id": uuid4(), "name": "Corte infantil", "price_cents": 2200, "duration_min": 25})
    async def adverse(**_):
        return Intention(intent="perguntar", service_ids=[h.cut, h.beard])
    monkeypatch.setattr(qr_ai, "interpret", adverse)
    reply = await h.turn(message)
    assert "Total desses serviços" not in reply["answer"]
    assert "confirme quais serviços" in reply["answer"]


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", ["Mas sem barba.", "E sobrancelha?", "E tatuagem?"])
async def test_addition_or_withdrawal_after_price_question_requires_clarification(conversation, monkeypatch, suffix):
    h = conversation
    h.catalog.append({"id": uuid4(), "name": "Sobrancelha", "price_cents": 1200, "duration_min": 10})
    async def adverse(**_):
        return Intention(intent="perguntar", service_ids=[h.cut, h.beard])
    monkeypatch.setattr(qr_ai, "interpret", adverse)
    reply = await h.turn("Quanto custa cabelo e barba? " + suffix)
    assert "Total desses serviços" not in reply["answer"]
    assert "confirme quais serviços" in reply["answer"]


@pytest.mark.asyncio
async def test_exact_combo_price_is_not_counted_twice(conversation, monkeypatch):
    h = conversation
    combo = uuid4()
    h.catalog.append({"id": combo, "name": "Corte e Barba", "price_cents": 4900, "duration_min": 50})
    async def empty(**_):
        return Intention(intent="perguntar")
    monkeypatch.setattr(qr_ai, "interpret", empty)
    reply = await h.turn("Quanto custa Corte e Barba?")
    assert "Total desses serviços: R$ 49,00" in reply["answer"]
    assert "R$ 35,00" not in reply["answer"] and "R$ 20,00" not in reply["answer"]


def readonly_slots(monkeypatch, h, slots=None):
    seen = []
    class Session:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_):
            pass
    monkeypatch.setattr(qr_ai, "get_session_factory", lambda: Session)
    async def listing(db, tenant, professional, services, day):
        assert tenant == h.tenant
        assert all(item in {h.cut, h.beard} for item in services)
        seen.append((tenant, professional, services, day))
        return {"slots": slots if slots is not None else [day.isoformat() + "T14:30:00-04:00"]}
    monkeypatch.setattr(qr_ai, "list_slots", listing)
    return seen


@pytest.mark.asyncio
async def test_availability_question_collects_missing_fields_then_reads_real_slots(conversation, monkeypatch):
    h = conversation
    seen = readonly_slots(monkeypatch, h)
    async def forbidden_classifier(**_):
        raise AssertionError("Availability routing must not depend on the LLM picking a price intent")
    monkeypatch.setattr(qr_ai, "interpret", forbidden_classifier)
    reply = await h.turn("Quais são os dias que tem disponíveis para agendamento?")
    assert "Para qual serviço" in reply["answer"]
    assert "R$" not in reply["answer"] and not seen
    reply = await h.turn("Quero consultar corte de cabelo")
    assert "qual profissional" in reply["answer"] and "Corte de cabelo" in reply["answer"]
    reply = await h.turn("20/11/2026")
    assert "qual profissional" in reply["answer"] and not seen
    reply = await h.turn("O Bruno")
    assert "14:30" in reply["answer"] and "20/11" in reply["answer"]
    assert "Corte de cabelo com Bruno Souza" in reply["answer"]
    assert "1." not in reply["answer"] and "R$" not in reply["answer"]
    assert "Consulta sem reserva" in reply["answer"]
    assert seen[0][1:3] == (h.bruno, [h.cut])
    assert not h.calls["writes"] and not h.calls["slots"]


@pytest.mark.asyncio
@pytest.mark.parametrize("step", ["professional", "time", "confirm", "reschedule_confirm"])
async def test_availability_reads_backend_without_altering_booking_or_confirmation(conversation, monkeypatch, step):
    h = conversation
    original = h.seed(step, professional=str(h.bruno), hold=str(uuid4()), slots=["2026-11-20T15:00:00-04:00"],
                      price=3500, duration=30)
    seen = readonly_slots(monkeypatch, h)
    reply = await h.turn("Tem horários disponíveis para barba amanhã?", preview=False)
    assert "Barba com Bruno Souza" in reply["answer"] and "14:30" in reply["answer"]
    assert reply["step"] == step and len(seen) == 1
    assert {k: v for k, v in h.state().items() if k not in {"history", "confirmation_expires_at", "confirmation_requires_review"}} == original
    assert bool(h.state().get("confirmation_requires_review")) == (step in {"confirm", "reschedule_confirm"})
    assert seen[0][2] == [h.beard]
    assert not h.calls["writes"] and not h.calls["slots"]


@pytest.mark.asyncio
async def test_empty_availability_never_invents_times_or_uses_another_tenant(conversation, monkeypatch):
    h = conversation
    h.seed("confirm", professional=str(h.bruno), hold=str(uuid4()))
    seen = readonly_slots(monkeypatch, h, slots=[])
    reply = await h.turn("Quais horários estão disponíveis?", preview=False)
    assert "não encontrei horários livres" in reply["answer"]
    assert "14:" not in reply["answer"] and "R$" not in reply["answer"]
    assert seen[0][0] == h.tenant and not h.calls["writes"]


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", ["sim", "não", "cancelar"])
async def test_yes_no_during_availability_inquiry_cannot_confirm_or_cancel_old_booking(conversation, answer):
    h = conversation
    hold = str(uuid4())
    h.seed("confirm", hold=hold, availability_query={"services": [str(h.beard)], "waiting_for": "professional"})
    reply = await h.turn(answer, preview=False)
    assert reply["step"] == "confirm" and h.state()["hold"] == hold
    assert h.state()["services"] == [str(h.cut)]
    assert not h.calls["writes"]
    if answer == "sim":
        assert "qual profissional" in reply["answer"]
    else:
        assert "Encerrei somente a consulta" in reply["answer"]
        assert "availability_query" not in h.state()


@pytest.mark.asyncio
async def test_price_reply_during_availability_asks_for_inquiry_field_not_booking_confirmation(conversation):
    h = conversation
    h.seed("confirm", hold=str(uuid4()), availability_query={"services": [str(h.beard)], "waiting_for": "professional"})
    reply = await h.turn("Quanto custa cabelo e barba?")
    assert "R$ 55,00" in reply["answer"]
    assert "consulta de disponibilidade" in reply["answer"] and "qual profissional" in reply["answer"]
    assert "responda SIM" not in reply["answer"]
    assert not h.calls["writes"]


@pytest.mark.asyncio
@pytest.mark.parametrize("question", ["Quanto custa a barba?", "Tem horários disponíveis para barba amanhã?"])
async def test_a_query_about_b_requires_explicit_review_before_confirming_original_a(conversation, monkeypatch, question):
    h = conversation
    hold = str(uuid4())
    deadline = qr_ai.datetime.now().timestamp() + 40
    summary = "Resumo: Corte de cabelo com Bruno Souza, 20/11 às 14:00, 30 minutos, R$ 35,00."
    h.seed("confirm", hold=hold, professional=str(h.bruno), confirmation_summary=summary,
           confirmation_expires_at=deadline)
    readonly_slots(monkeypatch, h)
    confirmed = []
    async def confirm(db, tenant, item_id):
        assert tenant == h.tenant
        confirmed.append(str(item_id))
        return {"start_at": qr_ai.datetime.fromisoformat("2026-11-20T14:00:00-04:00"),
                "total_cents": 3500, "protocol": "SYNTHETIC-A"}
    monkeypatch.setattr(qr_ai, "confirm_hold", confirm)
    await h.turn(question, preview=False)
    assert h.state()["hold"] == hold and h.state()["confirmation_requires_review"]
    assert h.state()["confirmation_expires_at"] == deadline
    assert 1 <= h.redis.ttls[h.key] <= 40
    blocked = await h.turn("sim", preview=False)
    assert "RETOMAR RESUMO" in blocked["answer"] and not confirmed
    review = await h.turn("retomar resumo", preview=False)
    assert summary in review["answer"]
    assert "Barba" not in review["answer"]
    assert h.state()["services"] == [str(h.cut)] and h.state()["hold"] == hold
    assert h.state()["confirmation_expires_at"] == deadline and 1 <= h.redis.ttls[h.key] <= 40
    assert not confirmed
    done = await h.turn("sim", preview=False)
    assert done["step"] == "confirmed" and confirmed == [hold]
    assert not h.calls["writes"]


@pytest.mark.asyncio
@pytest.mark.parametrize("expired, missing", [(True, False), (False, True)])
async def test_query_cannot_restore_an_expired_or_missing_confirmation_summary(conversation, expired, missing):
    h = conversation
    h.seed("confirm", hold=str(uuid4()), confirmation_requires_review=True,
           confirmation_summary=None if missing else "Original summary",
           confirmation_expires_at=qr_ai.datetime.now().timestamp() + (-1 if expired else 40))
    reply = await h.turn("retomar resumo", preview=False)
    assert reply["step"] == "expired" and not h.state()
    assert not h.calls["writes"]


@pytest.mark.asyncio
async def test_rescheduling_review_keeps_original_proposal_and_deadline(conversation):
    h = conversation
    appointment = str(uuid4())
    deadline = qr_ai.datetime.now().timestamp() + 40
    summary = "Remarcar para 20/11 às 14:00?"
    h.seed("reschedule_confirm", appointment=appointment, new_start="2026-11-20T14:00:00-04:00",
           confirmation_summary=summary, confirmation_expires_at=deadline)
    await h.turn("Quanto custa a barba?")
    assert h.state()["confirmation_requires_review"]
    blocked = await h.turn("sim", preview=False)
    assert "RETOMAR RESUMO" in blocked["answer"] and not h.calls["writes"]
    reply = await h.turn("retomar resumo", preview=False)
    assert summary in reply["answer"] and h.state()["appointment"] == appointment
    assert h.state()["new_start"] == "2026-11-20T14:00:00-04:00"
    assert h.state()["confirmation_expires_at"] == deadline and 1 <= h.redis.ttls[h.key] <= 40
    assert not h.calls["writes"]


@pytest.mark.asyncio
@pytest.mark.parametrize("step", ["date", "professional", "time", "confirm"])
async def test_weekday_replaces_old_date_and_hold_before_consulting(conversation, monkeypatch, step):
    h = conversation
    h.seed(step, professional=str(h.bruno), hold=str(uuid4()), day="2026-01-01")
    async def forbidden_classifier(**_):
        raise AssertionError("A simple weekday correction needs no classifier")
    monkeypatch.setattr(qr_ai, "interpret", forbidden_classifier)
    reply = await h.turn("Quero pra segunda", preview=False)
    assert reply["step"] == "time" and h.state()["day"] != "2026-01-01"
    assert qr_ai.date.fromisoformat(h.state()["day"]).weekday() == 0
    assert "hold" not in h.state() and not h.calls["writes"]
    assert h.calls["slots"][-1]["day"] == h.state()["day"]


@pytest.mark.asyncio
@pytest.mark.parametrize("phrase", ["Que dia tem livre", "Qual dia tem horário livre?", "Quais dias estão disponíveis?"])
async def test_next_days_query_ignores_old_day_preserves_booking_and_has_bounded_reads(conversation, monkeypatch, phrase):
    h = conversation
    original = h.seed("confirm", professional=str(h.bruno), day="2026-01-01", hold=str(uuid4()))
    seen = readonly_slots(monkeypatch, h)
    reply = await h.turn(phrase, preview=False)
    assert "próximos sete dias" in reply["answer"] and "14:30" in reply["answer"]
    assert "R$" not in reply["answer"] and len(seen) == 3
    assert all(row[3].isoformat() != "2026-01-01" for row in seen)
    assert h.state()["day"] == original["day"] and h.state()["hold"] == original["hold"]
    assert h.state()["confirmation_requires_review"] and not h.calls["writes"]


@pytest.mark.asyncio
async def test_empty_next_week_is_exactly_seven_reads_and_no_invented_slots(conversation, monkeypatch):
    h = conversation
    h.seed("time", professional=str(h.bruno))
    seen = readonly_slots(monkeypatch, h, slots=[])
    reply = await h.turn("Que dia tem livre", preview=False)
    assert len(seen) == 7 and "Não encontrei horários livres nesse período" in reply["answer"]
    assert "14:" not in reply["answer"] and not h.calls["writes"]


@pytest.mark.asyncio
async def test_quero_fazer_tudo_requires_service_clarification_without_changing_selection(conversation):
    h = conversation
    original = h.seed("date")
    reply = await h.turn("Quero fazer tudo", preview=False)
    assert "quais serviços" in reply["answer"] and h.state()["services"] == original["services"]
    assert not h.calls["classify"] and not h.calls["slots"] and not h.calls["writes"]
