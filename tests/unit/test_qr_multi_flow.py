"""Complete turn with the real planner guards/composer and local backend fakes.

There is no Groq, Redis, PostgreSQL, WhatsApp or production operation here.
"""
import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException

from app import qr_ai, qr_profile
from app.business_profile_schemas import BusinessProfile
from app.groq_ai import GroqServiceError
from app.qr_understanding import UnderstandingPlan, build_plan


class Redis:
    def __init__(self):
        self.data, self.ttls = {}, {}
    async def get(self, key):
        return self.data.get(key)
    async def set(self, key, value, ex=None):
        self.data[key] = value;self.ttls[key] = ex
    async def delete(self, key):
        self.data.pop(key, None)


class Session:
    async def __aenter__(self):
        return self
    async def __aexit__(self, *_):
        pass


@pytest.fixture
def flow(monkeypatch):
    monkeypatch.setenv("QR_AI_MULTI_INTENT_ENABLED", "1")
    tenant, cut, beard, bruno, breno, version, appointment = (uuid4() for _ in range(7))
    zone = ZoneInfo("America/Campo_Grande")
    today = datetime.now(zone).date();tomorrow = today + timedelta(days=1)
    catalog = [{"id": cut, "name": "Corte", "price_cents": 3500, "duration_min": 40},
               {"id": beard, "name": "Barba", "price_cents": 2000, "duration_min": 20}]
    professionals = [{"id": bruno, "name": "Bruno"}, {"id": breno, "name": "Breno"}]
    profile = BusinessProfile(business_name="Synthetic shop", capabilities=["catalog_answers", "appointments", "rescheduling"],
        faqs=[{"question": "Aceitam Pix?", "answer": "Aceitamos Pix."}],
        hours=[{"weekday": 0, "opens": "08:00", "closes": "20:00"}]).model_dump(mode="json")
    business = {"name": "Synthetic shop", "timezone": str(zone), "profile_id": str(version), "profile": profile}
    redis = Redis();namespace = "preview:owner:multi-flow";key = f"orion:qr:ai:{tenant}:{namespace}"
    calls = {"plans": [], "reads": [], "writes": [], "names": []};proposal = {"value": None, "error": False}
    async def data(t):
        assert t == tenant
        return business, catalog, professionals
    async def planner(**kwargs):
        calls["plans"].append(kwargs["message"])
        if proposal["error"]:
            raise GroqServiceError(502, "synthetic")
        return build_plan(message=kwargs["message"], catalog=kwargs["catalog"], professionals=kwargs["professionals"],
            context=kwargs["context"], faqs=kwargs["faqs"], reference_date=kwargs["reference_date"], proposal=proposal["value"])
    async def legacy(**_):
        raise AssertionError("A whole utterance must not run a second legacy classifier")
    async def eligible(t, ids):
        assert t == tenant and set(ids) <= {cut, beard}
        return professionals
    async def slots(db, t, pro, ids, day):
        assert t == tenant and pro in {bruno, breno} and set(ids) <= {cut, beard}
        calls["reads"].append((pro, list(ids), day))
        values = [datetime.combine(day, datetime.min.time(), zone).replace(hour=hour, minute=minute).isoformat()
                  for hour in range(8, 19) for minute in (0, 30)]
        items = [x for x in catalog if x["id"] in ids]
        return {"slots": values, "price_cents": sum(x["price_cents"] for x in items),
                "duration_min": sum(x["duration_min"] for x in items)}
    async def book(db, t, pro, ids, phone, name, when, idem):
        calls["writes"].append(("hold", list(ids), when, pro))
        return {"id": uuid4()}
    async def confirm(db, t, hold):
        calls["writes"].append(("confirm", hold))
        return {"start_at": datetime.combine(tomorrow, datetime.min.time(), zone).replace(hour=15),
                "total_cents": 3500, "protocol": "SYNTHETIC"}
    async def name(*_):
        return None
    async def remember(t, phone, customer):
        assert t == tenant
        calls["names"].append(customer)
    async def reschedule(db, t, appointment_id, when, **kwargs):
        assert t == tenant and appointment_id == appointment
        assert kwargs == {"expected_snapshot": "synthetic-snapshot", "customer_phone": "+5567999999999"}
        calls["writes"].append(("reschedule", when))
        return {"start_at": when, "protocol": "SYNTHETIC"}
    async def preview_move(db, t, appointment_id, when, *, customer_phone):
        assert t == tenant and appointment_id == appointment and customer_phone == "+5567999999999"
        return {"services": ["Corte"], "start_at": when, "timezone": str(zone), "total_cents": 3500,
                "professional": "Bruno", "duration_min": 40, "expected_snapshot": "synthetic-snapshot"}
    async def records(*_, **__):
        return [{"id": appointment, "status": "confirmed", "start_at": datetime.combine(tomorrow, datetime.min.time(), zone).replace(hour=14), "protocol": "SYNTHETIC"}]
    async def original(*_):
        return {"status": "confirmed", "professional_id": bruno, "services": [cut]}
    monkeypatch.setattr(qr_ai, "tenant_data", data);monkeypatch.setattr(qr_ai, "interpret_plan", planner)
    monkeypatch.setattr(qr_ai, "interpret", legacy);monkeypatch.setattr(qr_ai, "professionals_for", eligible)
    monkeypatch.setattr(qr_ai, "list_slots", slots);monkeypatch.setattr(qr_ai, "get_session_factory", lambda: Session)
    monkeypatch.setattr(qr_ai, "book_appointment", book);monkeypatch.setattr(qr_ai, "confirm_hold", confirm)
    monkeypatch.setattr(qr_ai, "customer_name", name);monkeypatch.setattr(qr_ai, "customer_appointments", records)
    monkeypatch.setattr(qr_ai, "appointment_services", original)
    monkeypatch.setattr(qr_ai, "remember_name", remember);monkeypatch.setattr(qr_ai, "reschedule_appointment", reschedule)
    monkeypatch.setattr(qr_ai, "preview_reschedule", preview_move)
    def state():
        return json.loads(redis.data.get(key, "{}"))
    def seed(step="date", **extra):
        value = {"step": step, "services": [str(cut)], "day": tomorrow.isoformat(), "professional": str(bruno),
                 "professional_name": "Bruno", **extra}
        redis.data[key] = json.dumps(value)
        redis.data[key + ":catalog-context"] = qr_profile.catalog_revision(business, catalog, professionals)
        return value
    async def turn(message, preview=True):
        return await qr_ai.turn(tenant, "5567999999999", message, redis, namespace, preview=preview)
    return SimpleNamespace(**locals())


@pytest.mark.asyncio
async def test_whole_audio_text_correction_is_planned_once_then_only_final_hold_is_prepared(flow):
    h = flow
    reply = await h.turn("Quero corte e barba amanhã às duas, não melhor às três, deixa só o corte mesmo, com Bruno", preview=False)
    assert reply["step"] == "confirm" and "15h" in reply["answer"] and "R$ 35,00" in reply["answer"]
    assert len(h.calls["plans"]) == 1 and len(h.calls["writes"]) == 1
    assert h.calls["writes"][0][0:2] == ("hold", [h.cut])
    assert h.calls["writes"][0][2].hour == 15 and h.state()["services"] == [str(h.cut)]
    assert not any(x[0] == "confirm" for x in h.calls["writes"])
    # 15:00 is beyond the first eight 30-minute slots; full backend result was used.
    assert len(h.calls["reads"]) == 1


@pytest.mark.asyncio
async def test_price_faq_and_explicit_booking_share_one_reply_and_require_a_later_sim(flow):
    h = flow
    h.proposal["value"] = UnderstandingPlan(intents=["price", "faq", "booking", "confirm"],
        service_ids=[h.cut, h.beard], query_service_ids=[h.cut, h.beard], selection_mode="replace", faq_ids=["faq-0"])
    reply = await h.turn("Quero agendar corte e barba amanhã às 15:00 com Bruno. Quanto custa? Aceitam Pix? Pode confirmar", preview=False)
    assert reply["step"] == "confirm" and "R$ 55" in reply["answer"] and "Aceitamos Pix" in reply["answer"]
    assert [x[0] for x in h.calls["writes"]] == ["hold"]
    assert len(h.calls["plans"]) == 1
    next_reply = await h.turn("SIM", preview=False)
    assert next_reply["step"] == "confirmed" and len(h.calls["plans"]) == 1
    assert [x[0] for x in h.calls["writes"]] == ["hold", "confirm"]


@pytest.mark.asyncio
async def test_price_question_during_confirmation_preserves_selection_and_requires_resume(flow):
    h = flow
    original = h.seed("confirm", hold=str(uuid4()), price=3500, duration=40,
        confirmation_summary="Corte com Bruno às 15:00, R$ 35,00", confirmation_expires_at=datetime.now().timestamp()+240)
    reply = await h.turn("E quanto tá a barba?", preview=False)
    assert "R$ 20" in reply["answer"] and h.state()["services"] == original["services"]
    assert h.state()["hold"] == original["hold"] and h.state()["confirmation_requires_review"]
    blocked = await h.turn("SIM", preview=False)
    assert original["confirmation_summary"] in blocked["answer"] and "RETOMAR RESUMO" not in blocked["answer"] and not h.calls["writes"]
    await h.turn("RETOMAR RESUMO", preview=False);confirmed = await h.turn("SIM", preview=False)
    assert confirmed["step"] == "confirmed" and [x[0] for x in h.calls["writes"]] == ["confirm"]


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["Quero corte amanhã às duas", "Quero barba ou corte amanhã às 15:00",
                                     "Quero corte amanhã às 15:00 com Bruno ou Breno"])
async def test_ambiguous_decisive_fields_stop_before_slots_or_hold(flow, message):
    h = flow
    if "às duas" in message:
        h.business["profile"]["hours"] = []
    reply = await h.turn(message, preview=False)
    assert "?" in reply["answer"] and reply["step"] != "confirm"
    assert not h.calls["writes"] and not h.calls["reads"]


@pytest.mark.asyncio
async def test_provider_failure_does_not_execute_a_first_clause_or_reset_previous_choice(flow):
    h = flow;before = h.seed("date");h.proposal["error"] = True
    reply = await h.turn("Quero barba, não, melhor corte amanhã às 15:00", preview=False)
    assert reply["degraded"] and h.state()["services"] == before["services"]
    assert not h.calls["writes"] and not h.calls["reads"]


@pytest.mark.asyncio
async def test_after_time_is_a_constraint_and_never_a_first_slot_confirmation(flow):
    h = flow
    reply = await h.turn("Quero fazer corte amanhã depois das quatro com Bruno", preview=False)
    assert reply["step"] == "time" and "16h30" in reply["answer"] and "15h30" not in reply["answer"]
    assert not h.calls["writes"]


@pytest.mark.asyncio
async def test_factual_budget_is_checked_before_hold_and_conditions_are_not_truncated(flow):
    h = flow
    h.business["profile"]["faqs"][0]["answer"] = "Condição longa. " * 75 + "EXCEÇÃO FINAL."
    h.proposal["value"] = UnderstandingPlan(intents=["faq", "booking"], service_ids=[h.cut], faq_ids=["faq-0"])
    reply = await h.turn("Quero agendar corte amanhã às 15:00 com Bruno. Aceitam Pix?", preview=False)
    assert reply["step"] == "choose_priority" and "prefere" in reply["answer"]
    assert not h.calls["writes"] and not h.state().get("hold")
    assert "deferred_plan" in h.state()
    next_reply = await h.turn("agendamento primeiro", preview=False)
    assert next_reply["step"] == "confirm" and len(h.calls["plans"]) == 1
    assert [x[0] for x in h.calls["writes"]] == ["hold"]


@pytest.mark.asyncio
async def test_disabled_capabilities_and_human_priority_never_reach_hold(flow):
    h = flow;h.business["profile"]["capabilities"] = ["catalog_answers"]
    reply = await h.turn("Quero agendar corte amanhã às 15:00 com Bruno", preview=False)
    assert reply["step"] == "capability_unavailable" and not h.calls["reads"]
    paused = await h.turn("Quero agendar mas preciso falar com uma pessoa de verdade", preview=False)
    assert paused["step"] == "human" and not h.calls["writes"]


@pytest.mark.asyncio
async def test_later_professional_choice_preserves_original_requested_clock_without_extra_model(flow):
    h = flow
    first = await h.turn("Quero fazer corte amanhã às 15:00", preview=False)
    assert first["step"] == "professional" and len(h.calls["plans"]) == 1
    second = await h.turn("O Bruno", preview=False)
    assert second["step"] == "confirm" and "15h" in second["answer"]
    assert len(h.calls["plans"]) == 1 and len(h.calls["writes"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("phrase", ["Quando tem livre", "Livre quando?", "Tem algum livre?"])
async def test_when_free_queries_real_next_days_without_reusing_old_date_or_price_menu(flow, phrase):
    h = flow;old = h.seed("date", day="2026-01-01")
    reply = await h.turn(phrase, preview=False)
    assert "Disponibilidade real" in reply["answer"] and "R$" not in reply["answer"]
    assert len(h.calls["reads"]) == 3 and all(x[2] >= h.today for x in h.calls["reads"])
    assert h.state()["day"] == old["day"] and not h.calls["writes"]


@pytest.mark.asyncio
async def test_price_faq_availability_are_read_only_and_do_not_replace_booking_selection(flow):
    h = flow;before = h.seed("date")
    h.proposal["value"] = UnderstandingPlan(intents=["price", "faq", "availability"],
        query_service_ids=[h.cut, h.beard], faq_ids=["faq-0"])
    reply = await h.turn("Quanto custa corte e barba? Se tiver com Bruno amanhã depois das quatro, melhor. Aceitam Pix?", preview=False)
    assert all(x in reply["answer"] for x in ("R$ 55", "Aceitamos Pix", "16h30"))
    assert "15h30" not in reply["answer"] and h.state()["services"] == before["services"]
    assert not h.calls["writes"] and len(h.calls["plans"]) == 1


@pytest.mark.asyncio
async def test_confirmation_plus_question_requires_review_before_consent(flow):
    h = flow;h.seed("confirm", hold=str(uuid4()), confirmation_summary="Corte com Bruno",
        confirmation_expires_at=datetime.now().timestamp()+240)
    h.proposal["value"] = UnderstandingPlan(intents=["confirm", "faq"], faq_ids=["faq-0"])
    reply = await h.turn("Pode confirmar esse resumo. Aceitam Pix?", preview=False)
    assert reply["step"] == "confirm" and "Aceitamos Pix" in reply["answer"]
    assert h.state()["confirmation_requires_review"] and not h.calls["writes"]
    resumed = await h.turn("RETOMAR RESUMO", preview=False)
    assert "Corte com Bruno" in resumed["answer"]
    result = await h.turn("pode marcar", preview=False)
    assert result["step"] == "confirmed" and [x[0] for x in h.calls["writes"]] == ["confirm"]


@pytest.mark.asyncio
async def test_unlisted_natural_affirmation_uses_existing_whole_plan_once(flow):
    h = flow;h.seed("confirm", hold=str(uuid4()), confirmation_summary="Corte com Bruno",
        confirmation_expires_at=datetime.now().timestamp()+240)
    h.proposal["value"] = UnderstandingPlan(intents=["confirm"])
    done = await h.turn("Do jeitinho que você me mostrou, podemos seguir em frente", preview=False)
    assert done["step"] == "confirmed" and [x[0] for x in h.calls["writes"]] == ["confirm"]
    assert len(h.calls["plans"]) == 1


@pytest.mark.asyncio
async def test_no_preserves_pending_request_and_deadline_and_asks_adjustment(flow):
    h = flow;deadline = datetime.now().timestamp()+240
    original = h.seed("confirm", hold=str(uuid4()), confirmation_summary="Corte com Bruno", confirmation_expires_at=deadline)
    reply = await h.turn("Não", preview=False)
    assert reply["step"] == "confirm" and "ajustar" in reply["answer"]
    assert h.state()["hold"] == original["hold"] and h.state()["services"] == original["services"]
    assert h.state()["confirmation_expires_at"] == deadline and not h.calls["writes"] and not h.calls["plans"]
    blocked = await h.turn("fechou", preview=False)
    assert blocked["step"] == "confirm" and not h.calls["writes"]


@pytest.mark.asyncio
async def test_confirmation_with_clock_amendment_prepares_new_summary_only(flow):
    h = flow;h.seed("confirm", hold=str(uuid4()), confirmation_summary="Corte às 14h",
        confirmation_expires_at=datetime.now().timestamp()+240)
    h.proposal["value"] = UnderstandingPlan(intents=["confirm"])
    reply = await h.turn("Pode confirmar, mas troca para as 15h", preview=False)
    assert reply["step"] == "confirm" and "15h" in reply["answer"]
    assert [x[0] for x in h.calls["writes"]] == ["hold"] and h.state()["confirmation_summary"] != "Corte às 14h"


@pytest.mark.asyncio
@pytest.mark.parametrize("deadline,summary", [(-1, "Original"), (240, None), (float("nan"), "Original")])
async def test_any_affirmation_requires_fresh_visible_summary_before_backend(flow, deadline, summary):
    h = flow;h.seed("confirm", hold=str(uuid4()), confirmation_summary=summary,
        confirmation_expires_at=datetime.now().timestamp()+deadline)
    reply = await h.turn("pode marcar", preview=False)
    assert reply["step"] == "expired" and not h.calls["writes"]


@pytest.mark.asyncio
async def test_greeting_in_menu_preserves_active_availability_inquiry(flow):
    h = flow;original = h.seed("menu", services=[], availability_query={"services": [str(h.beard)], "waiting_for": "professional"})
    reply = await h.turn("Oi")
    assert reply["step"] == "menu" and "Olá" in reply["answer"] and "continuar" in reply["answer"]
    assert h.state()["availability_query"] == original["availability_query"]
    assert not h.calls["plans"] and not h.calls["reads"] and not h.calls["writes"]


@pytest.mark.asyncio
async def test_completed_availability_subject_is_retained_separately_from_booking(flow):
    h = flow;h.seed("menu", services=[], availability_query={"services": [str(h.beard)], "waiting_for": "professional", "search_next": True})
    first = await h.turn("Com Bruno")
    assert "Disponibilidade real" in first["answer"] and not h.state()["services"]
    assert h.state()["last_availability"]["services"] == [str(h.beard)]
    h.calls["reads"].clear()
    again = await h.turn("Quando tem livre")
    assert "Disponibilidade real" in again["answer"] and not h.state()["services"]
    assert h.calls["reads"] and all(x[1] == [h.beard] for x in h.calls["reads"])
    assert not h.calls["writes"]


@pytest.mark.asyncio
@pytest.mark.parametrize("text,hour,minute", [("às 11", 11, 0), ("o último da manhã", 11, 30), ("o primeiro da tarde", 12, 0)])
async def test_natural_clock_and_period_ordinal_use_full_backend_grid_without_model(flow, text, hour, minute):
    h = flow;h.seed("time", slots=[f"{h.tomorrow}T08:00:00-04:00", f"{h.tomorrow}T08:30:00-04:00"])
    reply = await h.turn(text, preview=False)
    assert reply["step"] == "confirm" and not h.calls["plans"]
    assert h.calls["writes"][0][2].hour == hour and h.calls["writes"][0][2].minute == minute


@pytest.mark.asyncio
async def test_natural_time_constraint_does_not_prepare_a_hold(flow):
    h = flow;h.seed("time", slots=[f"{h.tomorrow}T08:00:00-04:00"])
    reply = await h.turn("depois das 14:00", preview=False)
    assert reply["step"] == "time" and not h.calls["writes"] and not h.calls["plans"]
    assert all(datetime.fromisoformat(x).hour >= 14 for x in h.state()["slots"])


@pytest.mark.asyncio
async def test_confirmation_needs_a_message_after_the_visible_summary_and_natural_review_works(flow):
    h = flow
    async def identified(text, identity):
        token = qr_ai._message_identity.set(identity)
        try:
            return await h.turn(text, preview=False)
        finally:
            qr_ai._message_identity.reset(token)
    await identified("Quero agendar corte amanhã às 15:00 com Bruno", "synthetic-summary")
    assert h.state()["confirmation_message_id"] == "synthetic-summary"
    blocked = await identified("SIM", "synthetic-summary")
    assert blocked["step"] == "confirm" and [x[0] for x in h.calls["writes"]] == ["hold"]
    shown = await identified("Quero ver o resumo de novo", "synthetic-review")
    assert "*Horário:* 15h" in shown["answer"] and h.state()["confirmation_message_id"] == "synthetic-review"
    done = await identified("fechou", "synthetic-new-consent")
    assert done["step"] == "confirmed" and [x[0] for x in h.calls["writes"]] == ["hold", "confirm"]


@pytest.mark.asyncio
async def test_fixed_control_formatter_does_not_rewrite_published_faq_conditions(flow):
    h = flow;literal = "Condição cadastrada: Responda SIM ou NÃO. Bruno (Demonstração). Protocolo RAW_001."
    h.business["profile"]["faqs"][0]["answer"] = literal
    h.seed("confirm", hold=str(uuid4()), confirmation_summary="Corte com Bruno",
        confirmation_expires_at=datetime.now().timestamp()+240)
    h.proposal["value"] = UnderstandingPlan(intents=["faq"], faq_ids=["faq-0"])
    result = await h.turn("Aceitam Pix?", preview=False)
    assert literal in result["answer"] and "_literal_information" not in result and not h.calls["writes"]


@pytest.mark.asyncio
async def test_professional_demonstration_label_is_preserved_in_proposal(flow):
    h = flow;h.professionals[0]["name"] = "Bruno (Demonstração)"
    result = await h.turn("Quero agendar corte amanhã às 15:00 com Bruno", preview=False)
    assert "*Profissional:* Bruno (Demonstração)" in result["answer"]
    assert [x[0] for x in h.calls["writes"]] == ["hold"]


@pytest.mark.asyncio
async def test_price_during_pending_availability_does_not_change_the_inquiry_or_cancel_old_hold(flow):
    h = flow;original = h.seed("confirm", hold=str(uuid4()), confirmation_summary="Corte original",
        confirmation_expires_at=datetime.now().timestamp()+240,
        availability_query={"services": [str(h.cut)], "waiting_for": "professional", "day": h.tomorrow.isoformat()})
    await h.turn("E quanto tá a barba?", preview=False)
    assert h.state()["availability_query"]["services"] == [str(h.cut)] and not h.calls["reads"]
    await h.turn("NÃO", preview=False)
    assert "availability_query" not in h.state() and h.state()["hold"] == original["hold"]
    blocked = await h.turn("SIM", preview=False)
    assert "Corte original" in blocked["answer"] and "RETOMAR RESUMO" not in blocked["answer"] and not h.calls["writes"]


@pytest.mark.asyncio
async def test_failed_interpretation_or_long_new_request_requires_review_before_old_confirmation(flow):
    h = flow;h.seed("confirm", hold=str(uuid4()), confirmation_summary="Pedido anterior",
        confirmation_expires_at=datetime.now().timestamp()+240)
    h.proposal["error"] = True
    await h.turn("Muda tudo, não, só a barba amanhã", preview=False)
    reply = await h.turn("SIM", preview=False)
    assert "Pedido anterior" in reply["answer"] and "RETOMAR RESUMO" not in reply["answer"] and not h.calls["writes"]


@pytest.mark.asyncio
async def test_cancel_then_final_reschedule_prepares_summary_without_cancelling_or_moving(flow):
    h = flow
    first = await h.turn("Cancela meu horário... não, pera, melhor mudar para amanhã às 15:00", preview=False)
    assert first["step"] == "reschedule_confirm" and not h.calls["writes"]
    assert h.state()["appointment"] == str(h.appointment)
    second = await h.turn("SIM", preview=False)
    assert second["step"] == "rescheduled" and [x[0] for x in h.calls["writes"]] == ["reschedule"]


@pytest.mark.asyncio
async def test_rescheduling_cannot_silently_change_professional(flow):
    h = flow;h.seed("reschedule_day", appointment=str(h.appointment))
    reply = await h.turn("Quero remarcar amanhã às 15:00 com Breno", preview=False)
    assert "preserva o profissional" in reply["answer"] and not h.calls["reads"] and not h.calls["writes"]


@pytest.mark.asyncio
async def test_choosing_one_of_multiple_appointments_keeps_the_date_and_clock_already_supplied(flow, monkeypatch):
    h = flow
    async def records(*_, **__):
        return [{"id": h.appointment, "status": "confirmed", "start_at": datetime.combine(h.tomorrow, datetime.min.time(), h.zone).replace(hour=14), "protocol": "ONE"},
                {"id": uuid4(), "status": "confirmed", "start_at": datetime.combine(h.tomorrow + timedelta(days=1), datetime.min.time(), h.zone).replace(hour=14), "protocol": "TWO"}]
    monkeypatch.setattr(qr_ai, "customer_appointments", records)
    async def candidates(*args):
        return [{"id": str(row["id"]), "protocol": row["protocol"], "start_at": row["start_at"].isoformat(), "professional": "Bruno"}
                for row in await records()]
    monkeypatch.setattr(qr_ai.qr_cancellation, "owned_candidates", candidates)
    first = await h.turn("Quero remarcar amanhã às 15:00", preview=False)
    assert first["step"] == "reschedule_pick" and not h.calls["writes"]
    second = await h.turn("1", preview=False)
    assert second["step"] == "reschedule_confirm" and "15h" in second["answer"]
    assert h.state()["new_start"].startswith(str(h.tomorrow)) and len(h.calls["plans"]) == 1


@pytest.mark.asyncio
async def test_exact_menu_day_professional_and_time_choices_need_no_model(flow):
    h = flow;h.seed("menu", menu_ids=[str(h.cut)])
    await h.turn("1");await h.turn("amanhã");await h.turn("Bruno");reply = await h.turn("1", preview=False)
    assert reply["step"] == "confirm" and not h.calls["plans"]
    assert [x[0] for x in h.calls["writes"]] == ["hold"]


@pytest.mark.asyncio
async def test_customer_self_declaration_is_kept_separate_from_professional(flow):
    h = flow
    reply = await h.turn("Meu nome é Ana. Quanto custa a barba?", preview=False)
    assert "R$ 20" in reply["answer"] and h.state()["customer_name"] == "Ana"
    assert h.calls["names"] == ["Ana"] and not h.calls["writes"]


@pytest.mark.asyncio
@pytest.mark.parametrize("step", ["date", "professional", "time", "confirm"])
@pytest.mark.parametrize("text", ["Tira o corte", "Não quero corte", "Sem o corte"])
async def test_removing_last_selected_service_never_reuses_the_previous_selection(flow, step, text):
    h = flow
    h.seed(step, hold=str(uuid4()), confirmation_summary="Corte com Bruno",
        confirmation_expires_at=datetime.now().timestamp()+240)
    reply = await h.turn(text, preview=False)
    assert reply["step"] == "menu" and "Qual serviço" in reply["answer"]
    assert h.state()["services"] == [] and "hold" not in h.state()
    assert "confirmation_summary" not in h.state()
    assert not h.calls["reads"] and not h.calls["writes"]
    await h.turn("SIM", preview=False)
    assert not h.calls["reads"] and not h.calls["writes"] and not h.state().get("services")


@pytest.mark.asyncio
async def test_removing_last_service_from_availability_preserves_separate_booking_and_stays_empty(flow):
    h = flow;old = h.seed("confirm", hold=str(uuid4()), confirmation_summary="Corte com Bruno",
        confirmation_expires_at=datetime.now().timestamp()+240,
        availability_query={"services": [str(h.cut)], "day": h.tomorrow.isoformat(), "waiting_for": "professional"})
    reply = await h.turn("Tira o corte", preview=False)
    assert "Qual serviço" in reply["answer"] and h.state()["confirmation_requires_review"]
    assert h.state()["hold"] == old["hold"] and h.state()["services"] == old["services"]
    assert h.state()["availability_query"]["services"] == []
    assert not h.calls["reads"] and not h.calls["writes"]
    again = await h.turn("Quando tem livre", preview=False)
    assert "Qual serviço" in again["answer"] and h.state()["availability_query"]["services"] == []
    assert h.state()["hold"] == old["hold"] and not h.calls["reads"] and not h.calls["writes"]


@pytest.mark.asyncio
async def test_removing_one_of_multiple_services_keeps_only_the_remaining_service(flow):
    h = flow;h.seed("date", services=[str(h.cut), str(h.beard)])
    reply = await h.turn("Tira o corte", preview=False)
    assert reply["step"] == "time" and h.state()["services"] == [str(h.beard)]
    assert h.calls["reads"] and all(read[1] == [h.beard] for read in h.calls["reads"])
    assert not h.calls["writes"]


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["Não quero corte", "Sem o corte"])
@pytest.mark.parametrize("step", ["date", "time", "confirm"])
async def test_negative_only_service_correction_derives_remaining_selection_before_fallback(flow, text, step):
    h = flow;h.seed(step, services=[str(h.cut), str(h.beard)], hold=str(uuid4()),
        confirmation_summary="Corte e Barba", confirmation_expires_at=datetime.now().timestamp()+240)
    result = await h.turn(text, preview=False)
    assert result["step"] == "time" and h.state()["services"] == [str(h.beard)]
    assert "hold" not in h.state() and "confirmation_summary" not in h.state()
    assert h.calls["reads"] and all(x[1] == [h.beard] for x in h.calls["reads"])
    assert not h.calls["writes"]


@pytest.mark.asyncio
async def test_price_without_selected_service_queries_remaining_subject_without_mutation(flow):
    h = flow;old = h.seed("confirm", services=[str(h.cut), str(h.beard)], hold=str(uuid4()),
        confirmation_summary="Corte e Barba", confirmation_expires_at=datetime.now().timestamp()+240)
    result = await h.turn("Quanto custa sem o corte?", preview=False)
    assert "R$ 20,00" in result["answer"] and "R$ 35,00" not in result["answer"] and "R$ 55,00" not in result["answer"]
    assert h.state()["services"] == old["services"] and h.state()["hold"] == old["hold"]
    assert h.state()["confirmation_requires_review"] and not h.calls["reads"] and not h.calls["writes"]


@pytest.mark.asyncio
async def test_price_without_the_only_selected_service_asks_instead_of_listing_declined_service(flow):
    h = flow;old = h.seed("date")
    result = await h.turn("Quanto custa sem o corte?", preview=False)
    assert "Qual serviço" in result["answer"] and "R$ 35,00" not in result["answer"]
    assert h.state()["services"] == old["services"] and not h.calls["reads"] and not h.calls["writes"]


@pytest.mark.asyncio
async def test_availability_without_selected_service_uses_remaining_subject_without_selection_write(flow):
    h = flow;old = h.seed("date", services=[str(h.cut), str(h.beard)])
    result = await h.turn("Tem livre sem o corte?", preview=False)
    assert "Disponibilidade real" in result["answer"] and h.state()["services"] == old["services"]
    assert h.calls["reads"] and all(x[1] == [h.beard] for x in h.calls["reads"])
    assert not h.calls["writes"]


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["Confirmo se tiver desconto", "Pode confirmar desde que aceitem Pix",
    "Caso consigam entregar, pode confirmar", "Pode seguir a menos que haja taxa",
    "Contanto que seja o mesmo preço, confirmo"])
async def test_conditional_provider_confirmation_cannot_reach_confirm_hold(flow, text):
    h = flow;old = h.seed("confirm", hold=str(uuid4()), confirmation_summary="Corte com Bruno",
        confirmation_expires_at=datetime.now().timestamp()+240)
    h.proposal["value"] = UnderstandingPlan(intents=["confirm"])
    result = await h.turn(text, preview=False)
    assert result["step"] != "confirmed" and h.state()["hold"] == old["hold"]
    assert h.state()["confirmation_requires_review"] and not h.calls["writes"]
    again = await h.turn("SIM", preview=False)
    assert "Corte com Bruno" in again["answer"] and "RETOMAR RESUMO" not in again["answer"] and not h.calls["writes"]


@pytest.mark.asyncio
@pytest.mark.parametrize("flag", ["0", "1"])
@pytest.mark.parametrize("text,expected", [
    ("Não quero atendente humano, quero continuar com você", "declined"),
    ("Não quero bot, quero humano", "human"),
    ("Quero humano, não, melhor continuar com você", "declined"),
    ("Quero humano ou continuar com bot", "clarify"),
    ("Se não conseguir resolver, quero humano", "clarify"),
    ("Quero saber como falar com atendente humano", "clarify"),
])
async def test_whole_handoff_request_respects_negation_and_final_correction(flow, monkeypatch, flag, text, expected):
    h = flow;monkeypatch.setenv("QR_AI_MULTI_INTENT_ENABLED", flag);old = h.seed("date")
    result = await h.turn(text, preview=False)
    if expected == "human":
        assert result["step"] == "human" and h.state()["step"] == "human"
    else:
        assert result["step"] != "human" and h.state()["services"] == old["services"]
        assert ("continuo por aqui" if expected == "declined" else "pessoa da equipe?") in result["answer"]
        follow = await h.turn("Oi", preview=False)
        assert follow["answer"] and h.state()["step"] != "human"
    assert not h.calls["plans"] and not h.calls["reads"] and not h.calls["writes"]


@pytest.mark.asyncio
@pytest.mark.parametrize("step", ["time", "reschedule_time"])
@pytest.mark.parametrize("text", ["Não às 11", "Não quero às 11"])
async def test_negated_clock_never_creates_hold_or_reschedule_summary(flow, step, text):
    h = flow;old = h.seed(step, appointment=str(h.appointment), slots=[f"{h.tomorrow}T11:00:00-04:00"])
    result = await h.turn(text, preview=False)
    assert result["step"] not in {"confirm", "reschedule_confirm"} and not h.state().get("hold")
    assert h.state()["services"] == old["services"] and not h.calls["writes"]


@pytest.mark.asyncio
async def test_utf16_factual_budget_prevents_hold_before_bridge_rejects_emoji_payload(flow):
    h = flow;h.business["profile"]["faqs"][0]["answer"] = "😀" * 600
    h.proposal["value"] = UnderstandingPlan(intents=["faq", "booking"], service_ids=[h.cut], faq_ids=["faq-0"])
    result = await h.turn("Quero agendar corte amanhã às 15:00 com Bruno. Aceitam Pix?", preview=False)
    assert result["step"] == "choose_priority" and not h.calls["writes"] and not h.state().get("hold")
    assert qr_ai.presentation.fits_message(result["answer"])


@pytest.mark.asyncio
async def test_same_professional_reuses_request_identity_but_professional_correction_replaces_private_hold(flow, monkeypatch):
    h = flow;seen = [];holds = {}
    async def book(db, tenant, professional, ids, phone, name, when, idempotency_key):
        fingerprint = qr_ai.booking_fingerprint(professional, ids, when, phone)
        if idempotency_key in holds:
            assert holds[idempotency_key][0] == fingerprint
        else:
            holds[idempotency_key] = (fingerprint, uuid4())
        seen.append((professional, idempotency_key, holds[idempotency_key][1]))
        return {"id": holds[idempotency_key][1]}
    monkeypatch.setattr(qr_ai, "book_appointment", book)
    first = await h.turn("Quero agendar corte amanhã às 15:00 com Bruno", preview=False)
    assert first["step"] == "confirm"
    old_hold = h.state()["hold"]
    repeated = await h.turn("Prefiro Bruno às 15:00", preview=False)
    assert repeated["step"] == "confirm" and h.state()["hold"] == old_hold
    corrected = await h.turn("Prefiro Breno às 15:00", preview=False)
    assert corrected["step"] == "confirm" and h.state()["professional"] == str(h.breno)
    assert h.state()["hold"] != old_hold and "Breno" in h.state()["confirmation_summary"]
    assert seen[0][1] == seen[1][1] and seen[2][1] != seen[0][1]
    assert seen[0][0] == h.bruno and seen[2][0] == h.breno
    assert not h.calls["writes"]  # No confirmed appointment is changed or cancelled.


@pytest.mark.asyncio
async def test_hold_request_identity_uses_backend_normalized_instant_and_service_order(flow, monkeypatch):
    h = flow;seen = []
    async def book(db, tenant, professional, ids, phone, name, when, idempotency_key):
        seen.append(idempotency_key)
        return {"id": uuid4()}
    monkeypatch.setattr(qr_ai, "book_appointment", book)
    base = h.seed("time", price=5500, duration=60, services=[str(h.cut), str(h.beard)])
    local = f"{h.tomorrow}T15:00:00-04:00";utc = f"{h.tomorrow}T19:00:00+00:00"
    for when, services in [(local, base["services"]), (utc, base["services"]),
                           (local, list(reversed(base["services"])) )]:
        state = {**base, "services": services}
        result = await qr_ai.prepare_booking_summary(h.tenant, "5567999999999", state, when,
            h.business, h.catalog, h.redis, h.key, False)
        assert result["step"] == "confirm"
    assert seen[0] == seen[1] and seen[0] != seen[2]


@pytest.mark.asyncio
@pytest.mark.parametrize("selection", ["15:00", "15"])
async def test_exact_clock_after_a_short_listing_uses_full_live_slots_without_a_model(flow, selection):
    h = flow
    h.seed("time", slots=[f"{h.tomorrow}T{hour:02d}:{minute:02d}:00-04:00" for hour in range(8, 12) for minute in (0, 30)],
           price=3500, duration=40)
    result = await h.turn(selection, preview=False)
    assert result["step"] == "confirm" and "15h" in result["answer"]
    assert not h.calls["plans"] and len(h.calls["reads"]) == 1 and h.calls["writes"][0][2].hour == 15


@pytest.mark.asyncio
@pytest.mark.parametrize("step", ["date", "professional", "time", "confirm", "reschedule_day"])
@pytest.mark.parametrize("flag", ["0", "1"])
async def test_real_oi_is_a_greeting_in_every_stage_in_both_engines(flow, monkeypatch, step, flag):
    h = flow;monkeypatch.setenv("QR_AI_MULTI_INTENT_ENABLED", flag)
    original = h.seed(step, hold=str(uuid4()))
    result = await h.turn("Oi", preview=False)
    assert result["answer"].startswith("Olá") and "dias livres" in result["answer"]
    assert "Informe o dia" not in result["answer"] and h.state()["hold"] == original["hold"]
    assert not h.calls["plans"] and not h.calls["reads"] and not h.calls["writes"]


@pytest.mark.asyncio
async def test_natural_acceptance_of_review_offer_only_shows_original_summary_then_requires_new_consent(flow):
    h = flow
    original = h.seed("confirm", hold=str(uuid4()), price=3500, duration=40,
        confirmation_summary="Corte original com Bruno, R$ 35,00", confirmation_message_id="old-summary",
        confirmation_expires_at=datetime.now().timestamp()+40)
    async def trusted(body, identity):
        token = qr_ai._message_identity.set(identity)
        try:
            return await h.turn(body, preview=False)
        finally:
            qr_ai._message_identity.reset(token)
    answer = await trusted("Quanto custa a barba?", "question")
    assert "Quer conferir o resumo" in answer["answer"] and "RETOMAR RESUMO" not in answer["answer"]
    h.proposal["value"] = UnderstandingPlan(intents=["confirm"])
    review = await trusted("Do jeito que você mostrou, podemos continuar", "natural-review")
    assert original["confirmation_summary"] in review["answer"] and not h.calls["writes"]
    assert h.state()["confirmation_message_id"] == "natural-review"
    assert h.state()["confirmation_expires_at"] == original["confirmation_expires_at"]
    same = await trusted("Pode confirmar", "natural-review")
    assert same["step"] == "confirm" and not h.calls["writes"]
    await trusted("Quero conferir o resumo de novo", "review-again")
    done = await trusted("Pode confirmar", "final-fresh")
    assert done["step"] == "confirmed" and [row[0] for row in h.calls["writes"]] == ["confirm"]


@pytest.mark.asyncio
@pytest.mark.parametrize("selector,choice", [("com Bruno", 0), ("o último", 1), ("TWO", 1)])
async def test_reschedule_pick_accepts_unambiguous_natural_reference_without_model(flow, selector, choice):
    h = flow; second = uuid4()
    details = [{"id": str(h.appointment), "protocol": "ONE", "professional": "Bruno", "start_at": f"{h.tomorrow}T14:00:00-04:00"},
        {"id": str(second), "protocol": "TWO", "professional": "Breno", "start_at": f"{h.tomorrow}T16:00:00-04:00"}]
    h.seed("reschedule_pick", candidate_ids=[str(h.appointment), str(second)], candidate_details=details)
    reply = await h.turn(selector, preview=False)
    assert reply["step"] == "reschedule_day" and h.state()["appointment"] == details[choice]["id"]
    assert not h.calls["plans"] and not h.calls["writes"]


@pytest.mark.asyncio
@pytest.mark.parametrize("selector", ["com Bruno e Breno", "com Jorge", "amanhã"])
async def test_reschedule_pick_ambiguity_keeps_concrete_owned_choices_and_never_picks_first(flow, selector):
    h = flow; second = uuid4()
    details = [{"id": str(h.appointment), "protocol": "ONE", "professional": "Bruno", "start_at": f"{h.tomorrow}T14:00:00-04:00"},
        {"id": str(second), "protocol": "TWO", "professional": "Breno", "start_at": f"{h.tomorrow}T16:00:00-04:00"}]
    h.seed("reschedule_pick", candidate_ids=[str(h.appointment), str(second)], candidate_details=details)
    reply = await h.turn(selector, preview=False)
    assert reply["step"] == "reschedule_pick" and "ONE" in reply["answer"] and "TWO" in reply["answer"]
    assert "Bruno" in reply["answer"] and "Breno" in reply["answer"]
    assert not h.calls["plans"] and not h.calls["writes"] and "appointment" not in h.state()


@pytest.mark.asyncio
async def test_reschedule_conflict_preserves_original_and_only_new_message_reads_other_slots(flow, monkeypatch):
    h = flow; attempts = []
    async def conflict(*args, **kwargs):
        attempts.append(1)
        raise HTTPException(409, "Synthetic unavailable")
    monkeypatch.setattr(qr_ai, "reschedule_appointment", conflict)
    h.seed("reschedule_confirm", appointment=str(h.appointment), new_start=f"{h.tomorrow}T15:00:00-04:00",
        confirmation_summary="Remarcação proposta", confirmation_expires_at=datetime.now().timestamp()+40,
        confirmation_message_id="old-summary", expected_snapshot="synthetic-snapshot",
        reschedule_facts={"services": ["Corte"], "professional": "Bruno", "total_cents": 3500,
            "duration_min": 40, "timezone": str(h.zone)})
    failed = await h.turn("Pode confirmar", preview=False)
    assert failed["step"] == "reschedule_conflict" and "Escreva REMARCAR" not in failed["answer"]
    assert attempts == [1] and not h.calls["reads"] and not h.calls["writes"]
    assert h.state()["appointment"] == str(h.appointment) and "expected_snapshot" not in h.state()
    queried = await h.turn("Quero consultar outros horários", preview=False)
    assert queried["step"] == "reschedule_time" and "08h" in queried["answer"]
    assert len(h.calls["reads"]) == 1 and attempts == [1] and not h.calls["writes"]
    proposed = await h.turn("às 16h", preview=False)
    assert proposed["step"] == "reschedule_confirm" and "16h" in proposed["answer"]
    assert h.state()["appointment"] == str(h.appointment) and attempts == [1] and not h.calls["writes"]
