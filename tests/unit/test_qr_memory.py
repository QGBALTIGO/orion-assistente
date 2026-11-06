"""Conversation continuity uses owned current facts without reviving consent."""
from datetime import datetime, timedelta, timezone, date
from uuid import uuid4
import json

import pytest

from app import qr_ai, qr_memory
from app.qr_customer import supplied_name, third_party_subject
from app.qr_understanding import build_plan
from test_qr_multi_flow import flow


def completed(h, **changes):
    end=datetime.now(timezone.utc)-timedelta(hours=1)
    return {"id":uuid4(),"status":"completed","professional_id":h.bruno,
        "professional_name":"Bruno","start_at":end-timedelta(minutes=40),"end_at":end,
        "services":[{"service_id":str(h.cut),"name":"Corte","duration_min":40}],**changes}


def install_history(monkeypatch,h,record):
    calls=[]
    async def read(tenant,phone):
        assert tenant==h.tenant and phone=="5567999999999"
        calls.append((tenant,phone))
        return record
    monkeypatch.setattr(qr_ai,"customer_last_completed",read)
    return calls


@pytest.mark.asyncio
async def test_known_greeting_is_short_and_keeps_catalog_on_request(flow,monkeypatch):
    h=flow
    async def name(*_):return "Lucas"
    monkeypatch.setattr(qr_ai,"customer_name",name)
    result=await h.turn("Oi",preview=False)
    assert "Lucas" in result["answer"] and "R$" not in result["answer"]
    assert len(result["answer"])<=240 and result["step"]=="greeting"
    assert not h.calls["writes"] and not h.calls["plans"]
    following=await h.turn("Aceitam Pix?",preview=False)
    assert "Aceitamos Pix" in following["answer"] and "Lucas" not in following["answer"]


@pytest.mark.asyncio
async def test_successful_booking_keeps_dialogue_and_factual_completion_without_consent(flow):
    h=flow
    await h.turn("Quero corte amanhã às 15:00 com Bruno",preview=False)
    result=await h.turn("SIM",preview=False)
    state=h.state()
    assert result["step"]=="confirmed" and state["step"]=="menu"
    assert state["history"][-1]["content"]==result["answer"]
    assert state["completed_task"]["kind"]=="confirmed"
    assert set(state)<= {"step","history","customer_name","completed_task"}
    before=len(h.calls["writes"])
    duplicate_agreement=await h.turn("SIM",preview=False)
    assert duplicate_agreement["step"]!="confirmed" and len(h.calls["writes"])==before


@pytest.mark.asyncio
async def test_greeting_during_summary_never_turns_next_agreement_into_hidden_confirmation(flow):
    h=flow
    await h.turn("Quero corte amanhã às 15:00 com Bruno",preview=False)
    greeting=await h.turn("Oi",preview=False)
    assert greeting["step"]=="greeting" and h.state()["confirmation_requires_review"]
    reply=await h.turn("SIM",preview=False)
    assert reply["step"]=="confirm" and [call[0] for call in h.calls["writes"]]==["hold"]


@pytest.mark.asyncio
async def test_explicit_subject_change_keeps_history_but_withdraws_old_confirmation(flow):
    h=flow
    await h.turn("Quero corte amanhã às 15:00 com Bruno",preview=False)
    reply=await h.turn("Esquece isso, queria perguntar outra coisa: aceitam Pix?",preview=False)
    assert "Aceitamos Pix" in reply["answer"] and h.state()["step"]=="menu"
    assert "hold" not in h.state() and "confirmation_message_id" not in h.state()
    await h.turn("SIM",preview=False)
    assert [call[0] for call in h.calls["writes"]]==["hold"]


@pytest.mark.parametrize("body",[
    "Sou o pai do Miguel, quero marcar para ele",
    "Sou a mãe da Ana, quero marcar para ela",
    "Quero corte para meu filho amanhã",
    "Minha filha precisa cortar o cabelo",
    "Quero reservar para outra pessoa",
])
def test_third_party_subject_is_not_the_contact_identity(body):
    assert third_party_subject(body)


@pytest.mark.parametrize("body",[
    "Meu pai me indicou e quero agendar para mim",
    "Sou o João, quero corte amanhã",
    "Minha mãe gostou do meu corte",
    "Qual o nome do profissional?",
])
def test_family_reference_or_self_name_is_not_a_different_beneficiary(body):
    assert not third_party_subject(body)


@pytest.mark.parametrize("body",[
    "Sou o pai do Miguel, quero marcar para ele",
    "Sou a mãe da Ana, quero marcar para ela",
])
def test_a_relationship_is_never_saved_as_a_customer_name(body):
    assert supplied_name(body) is None


@pytest.mark.asyncio
async def test_third_party_booking_cannot_update_contact_or_create_hold(flow):
    h=flow
    reply=await h.turn("Sou o pai do Miguel, quero corte amanhã às 15:00 com Bruno para ele",preview=False)
    assert reply["step"]=="third_party_identity"
    assert not h.calls["names"] and not h.calls["writes"]


@pytest.mark.asyncio
async def test_own_name_and_family_recommendation_remains_a_legitimate_booking(flow):
    h=flow
    reply=await h.turn("Sou o João, meu pai me indicou e quero corte amanhã às 15:00 com Bruno para mim",preview=False)
    assert reply["step"]=="confirm"
    assert h.calls["names"]==["João"] and [x[0] for x in h.calls["writes"]]==["hold"]


@pytest.mark.asyncio
async def test_same_as_usual_reads_owned_completed_fact_and_requires_service_choice(flow,monkeypatch):
    h=flow;record=completed(h)
    calls=install_history(monkeypatch,h,record)
    result=await h.turn("Quero o mesmo de sempre",preview=False)
    assert len(calls)==1 and result["step"]=="repeat_review" and "Corte" in result["answer"]
    assert not h.calls["writes"]
    answer=await h.turn("Isso mesmo",preview=False)
    assert len(calls)==2 and h.state()["services"]==[str(h.cut)]
    assert answer["step"]=="date" and not h.calls["writes"]


@pytest.mark.asyncio
async def test_repeating_last_visit_uses_current_price_not_historic_snapshot(flow,monkeypatch):
    h=flow
    install_history(monkeypatch,h,completed(h))
    h.catalog[0]["price_cents"]=4100
    reply=await h.turn("Quero repetir o último atendimento",preview=False)
    assert "41,00" in reply["answer"] and h.state()["services"]==[str(h.cut)]
    assert not h.calls["writes"]


@pytest.mark.asyncio
async def test_inactive_historical_service_is_not_substituted_or_partially_selected(flow,monkeypatch):
    h=flow
    install_history(monkeypatch,h,completed(h,services=[{"service_id":str(uuid4()),"name":"Serviço antigo"}]))
    reply=await h.turn("Quero repetir o último atendimento",preview=False)
    assert reply["step"]=="history_service_unavailable"
    assert not h.calls["writes"] and not h.calls["names"]


@pytest.mark.asyncio
async def test_history_change_between_suggestion_and_yes_never_uses_stale_services(flow,monkeypatch):
    h=flow;record=completed(h)
    install_history(monkeypatch,h,record)
    await h.turn("Quero o mesmo de sempre",preview=False)
    record["id"]=uuid4()
    result=await h.turn("Sim",preview=False)
    assert result["step"]=="history_changed" and not h.calls["writes"]


@pytest.mark.asyncio
async def test_historical_query_reads_recorded_completion_without_exposing_other_fields(flow,monkeypatch):
    h=flow
    install_history(monkeypatch,h,completed(h,diagnosis="DO_NOT_EXPOSE",birth_date="DO_NOT_EXPOSE"))
    reply=await h.turn("Qual foi meu último atendimento?",preview=False)
    assert "Corte" in reply["answer"] and "DO_NOT_EXPOSE" not in reply["answer"]
    assert reply["step"]=="history" and not h.calls["writes"]


@pytest.mark.asyncio
async def test_preview_never_reads_live_customer_history(flow,monkeypatch):
    h=flow
    async def forbidden(*_):raise AssertionError("Live history in preview")
    monkeypatch.setattr(qr_ai,"customer_last_completed",forbidden)
    reply=await h.turn("Quero repetir o último atendimento",preview=True)
    assert reply["step"]=="preview_history" and not h.calls["writes"]


@pytest.mark.asyncio
async def test_new_topic_invalidates_suggested_repeat_without_a_dormant_yes(flow,monkeypatch):
    h=flow;install_history(monkeypatch,h,completed(h))
    await h.turn("Quero o mesmo de sempre",preview=False)
    await h.turn("Aceitam Pix?",preview=False)
    assert "memory_repeat" not in h.state()
    await h.turn("SIM",preview=False)
    assert not h.calls["writes"]


@pytest.mark.asyncio
async def test_twenty_natural_followups_keep_only_recent_dialogue_and_never_book(flow):
    h=flow
    for _ in range(20):
        reply=await h.turn("Aceitam Pix?",preview=False)
        assert "Aceitamos Pix" in reply["answer"]
    history=h.state()["history"]
    assert len(history)==8 and [x["role"] for x in history]==["user","assistant"]*4
    assert not h.calls["writes"] and not h.calls["names"]


def test_retention_whitelist_cannot_preserve_old_holds_or_private_capabilities():
    previous={"customer_name":"Lucas","hold":"PRIVATE","hold_snapshot":"PRIVATE","confirmation_summary":"PRIVATE",
        "confirmation_message_id":"PRIVATE","completed_task":{"kind":"confirmed","protocol":"PUBLIC","occurred_at":50,"token":"PRIVATE"}}
    kept=qr_memory.retain_dialogue(previous,{},now=100)
    assert kept=={"step":"menu","customer_name":"Lucas","completed_task":{"kind":"confirmed","protocol":"PUBLIC","occurred_at":50}}


@pytest.mark.parametrize("reference,expected",[
    ("mesmo dia","requested_date"),("mesmo horário","requested_time"),("com o mesmo profissional","professional_id"),
])
def test_explicit_reference_reuses_only_current_validated_facts(reference,expected):
    cut,pro=uuid4(),uuid4();today=date.today()
    context={"step":"time","service_ids":[str(cut)],"requested_date":today.isoformat(),
        "requested_time":"15:30","time_relation":"exact","professional_id":str(pro)}
    plan=build_plan(message=reference,catalog=[{"id":cut,"name":"Corte"}],professionals=[{"id":pro,"name":"Bruno"}],
        reference_date=today,context=context)
    assert str(getattr(plan,expected))==context[expected]
    assert "confirm" not in plan.intents


@pytest.mark.parametrize("body,field",[
    ("mesmo dia","date"),("mesmo horário","time"),("com o mesmo profissional","professional"),
])
def test_missing_reference_is_clarified_not_guessed(body,field):
    plan=build_plan(message=body,catalog=[],reference_date=date.today())
    assert field in plan.ambiguities and "confirm" not in plan.intents


def test_unmentioned_prior_clock_date_or_professional_is_not_inherited():
    pro=uuid4()
    plan=build_plan(message="Quero saber o preço",catalog=[],professionals=[{"id":pro,"name":"Bruno"}],
        context={"requested_date":date.today().isoformat(),"requested_time":"15:30","professional_id":str(pro)},
        reference_date=date.today())
    assert plan.requested_date is None and plan.requested_time is None and plan.professional_id is None


@pytest.mark.asyncio
async def test_change_pending_hold_time_reads_slots_and_cannot_confirm_old_summary(flow):
    h=flow
    await h.turn("Quero corte amanhã às 15:00 com Bruno",preview=False)
    old_hold=h.state()["hold"]
    reply=await h.turn("quero alterar o horário",preview=False)
    assert reply["step"]=="time" and [x[0] for x in h.calls["writes"]]==["hold"]
    assert h.state()["replacement_hold"]["id"]==old_hold
    assert not any(k in h.state() for k in ("hold","confirmation_message_id","confirmation_summary","hold_snapshot"))
    await h.turn("SIM",preview=False)
    assert [x[0] for x in h.calls["writes"]]==["hold"]


@pytest.mark.parametrize("body",[
    "Meu CPF é 111.222.333-44", "11122233344", "Meu documento é 11122233344",
    "Meu nascimento é 12/03/1990", "Estou com muita dor no dente", "Meu laudo diz isso",
    "Minha senha é segredo-sintetico", "Meu token: token-sintetico",
])
@pytest.mark.asyncio
async def test_explicit_personal_details_are_not_sent_to_the_planner_or_retained(flow,body):
    h=flow
    reply=await h.turn(body,preview=False)
    assert reply["step"]=="privacy_guidance"
    assert not h.calls["plans"] and not h.calls["writes"] and not h.calls["names"]
    stored=json.dumps(h.state(),ensure_ascii=False)
    assert body not in stored and "11122233344" not in stored and "12/03/1990" not in stored


@pytest.mark.parametrize("service",["Tratamento capilar","Canal","Consulta","Exame admissional"])
@pytest.mark.asyncio
async def test_published_clinic_and_salon_services_are_not_blocked_by_privacy_words(flow,service):
    h=flow;h.catalog[0]["name"]=service
    reply=await h.turn(f"Quero agendar {service} amanhã às 15:00 com Bruno",preview=False)
    assert reply["step"]=="confirm" and [x[0] for x in h.calls["writes"]]==["hold"]


@pytest.mark.asyncio
async def test_payment_policy_question_does_not_become_a_health_data_block(flow):
    reply=await flow.turn("Aceita plano?",preview=False)
    assert reply["step"]!="privacy_guidance" and not flow.calls["writes"]


def test_old_personal_history_is_sanitized_before_future_interpretation():
    history=[{"role":"assistant","content":"Qual a data do seu aniversário?"},
        {"role":"user","content":"12/03/1990"},
        {"role":"assistant","content":"Anotado."},
        {"role":"user","content":"Meu CPF é 111.222.333-44"},
        {"role":"assistant","content":"Posso ajudar com agendamento."},
        {"role":"user","content":"Estou com muita dor"}]
    cleaned=qr_memory.clean_history(history)
    serialized=json.dumps(cleaned)
    assert "1990" not in serialized and "111.222" not in serialized and "muita dor" not in serialized
    assert len(cleaned)==len(history)


def test_birthday_preference_persists_only_a_factual_context_marker():
    stored=qr_memory.history_exchange("12/03/1990","Aniversário 12/03/1990 cadastrado","birthday_preference")
    assert "1990" not in json.dumps(stored) and "12/03" not in json.dumps(stored)


def test_selected_clock_is_converted_from_utc_to_the_business_timezone():
    choices=qr_memory.factual_choices({"day":"2026-11-06","selected_start":"2026-11-06T18:00:00+00:00"},
        [],[],today=date(2026,11,6),timezone="America/Cuiaba")
    assert choices["requested_time"]=="14:00" and choices["requested_date"]=="2026-11-06"


@pytest.mark.asyncio
async def test_complete_visit_statement_and_new_booking_keep_the_new_day_without_old_hold(flow,monkeypatch):
    h=flow;install_history(monkeypatch,h,completed(h))
    reply=await h.turn("Já fui aí hoje e quero marcar para amanhã",preview=False)
    assert "concluído" in reply["answer"] and "serviço" in reply["answer"].lower()
    assert h.state()["day"]==h.tomorrow.isoformat() and not h.calls["writes"]
    assert not any(k in h.state() for k in ("hold","hold_snapshot","appointment"))
    next_reply=await h.turn("Cabelo",preview=False)
    assert h.state()["services"]==[str(h.cut)] and h.state()["day"]==h.tomorrow.isoformat()


@pytest.mark.asyncio
async def test_new_booking_clause_does_not_select_a_service_only_mentioned_in_completed_visit(flow,monkeypatch):
    h=flow;install_history(monkeypatch,h,completed(h))
    reply=await h.turn("Acabei de fazer meu corte e quero marcar barba amanhã às 15:00 com Bruno",preview=False)
    assert reply["step"]=="confirm" and h.state()["services"]==[str(h.beard)]
    assert [call[0:2] for call in h.calls["writes"]]==[("hold",[h.beard])]


@pytest.mark.parametrize("status",["completed","cancelled","no_show","in_progress"])
@pytest.mark.asyncio
async def test_pending_reschedule_reloads_real_status_and_never_reopens_a_finished_visit(flow,monkeypatch,status):
    h=flow
    h.seed("reschedule_confirm",appointment=str(h.appointment),new_start=h.tomorrow.isoformat()+"T15:00:00-04:00",
        confirmation_summary="resumo",confirmation_expires_at=datetime.now().timestamp()+200,expected_snapshot="synthetic-snapshot")
    async def current(tenant,appointment,phone):
        assert tenant==h.tenant and appointment==h.appointment and phone=="5567999999999"
        return {"status":status,"professional_id":h.bruno,"services":[h.cut]}
    monkeypatch.setattr(qr_ai,"appointment_services",current)
    result=await h.turn("SIM",preview=False)
    assert result["step"]=="appointment_changed" and h.state()["step"]=="menu"
    assert "appointment" not in h.state() and not h.calls["writes"]


@pytest.mark.asyncio
async def test_change_reschedule_summary_time_preserves_only_original_booking_facts(flow):
    h=flow
    h.seed("reschedule_confirm",appointment=str(h.appointment),new_start=h.tomorrow.isoformat()+"T15:00:00-04:00",
        confirmation_summary="resumo",confirmation_expires_at=datetime.now().timestamp()+200,expected_snapshot="synthetic-snapshot")
    reply=await h.turn("quero alterar o horário",preview=False)
    assert reply["step"]=="reschedule_day" and h.state()["appointment"]==str(h.appointment)
    assert not any(k in h.state() for k in ("new_start","hold","confirmation_summary","confirmation_message_id"))
    await h.turn("SIM",preview=False)
    assert not h.calls["writes"]


@pytest.mark.parametrize("body",["Você é uma pessoa ou um robô?","Você é uma IA?","É humano ou bot?"])
@pytest.mark.asyncio
async def test_identity_question_is_truthful_without_handoff_or_commerce(flow,body):
    reply=await flow.turn(body,preview=False)
    assert "assistente virtual" in reply["answer"].lower() and reply["step"]!="human"
    assert not flow.calls["writes"]


@pytest.mark.asyncio
async def test_polite_addition_reprices_full_current_bundle_and_prepares_only_a_new_hold(flow):
    h=flow
    await h.turn("Quero corte amanhã às 14:00 com Bruno",preview=False)
    reply=await h.turn("Pode pôr barba junto amanhã às 15:00 com Bruno?",preview=False)
    assert reply["step"]=="confirm" and set(h.state()["services"])=={str(h.cut),str(h.beard)}
    assert "55,00" in reply["answer"] and h.state()["price"]==5500
    assert [x[0] for x in h.calls["writes"]]==["hold","hold"]


@pytest.mark.parametrize("body",["Pode pôr barba junto, quanto fica?","Se eu adicionar barba, quanto fica?","Pode incluir barba se tiver vaga às 15:00?"])
@pytest.mark.asyncio
async def test_addition_price_or_condition_does_not_mutate_the_current_booking(flow,body):
    h=flow
    old=h.seed("confirm",hold=str(uuid4()),price=3500,duration=40,hold_snapshot="a"*64,
        confirmation_summary="Corte35",confirmation_expires_at=datetime.now().timestamp()+200)
    reply=await h.turn(body,preview=False)
    assert not h.calls["writes"] and h.state()["services"]==old["services"]


def test_full_500_catalog_can_resolve_last_service_but_501_is_rejected():
    catalog=[{"id":uuid4(),"name":f"Serviço único {index}"} for index in range(500)]
    plan=build_plan(message="Quero agendar Serviço único 499",catalog=catalog,reference_date=date.today())
    assert plan.service_ids==[catalog[-1]["id"]]
    with pytest.raises(ValueError):
        build_plan(message="Quero agendar",catalog=[*catalog,{"id":uuid4(),"name":"Extra"}])


@pytest.mark.asyncio
async def test_verified_professional_id_handles_homonyms_and_never_calls_the_model(flow):
    from app import qr_assistant_interactions as interactions
    h=flow
    for person in h.professionals: person["name"]="Alex"
    h.seed("professional",professional=None,professional_name=None,pending_clock="15:00",pending_relation="exact",
        professionals=[{"id":str(h.breno),"name":"Alex"},{"id":str(h.bruno),"name":"Alex"}])
    token=interactions._choice.set({"kind":"professional","professional_id":str(h.breno)})
    try:
        reply=await h.turn("[Escolha de profissional recebida]",preview=False)
        assert interactions.current_choice() is None
    finally:
        interactions.reset_choice(token)
    assert reply["step"]=="confirm" and h.calls["writes"][0][3]==h.breno
    assert not h.calls["plans"]


@pytest.mark.asyncio
async def test_plain_numeric_professional_uses_filtered_menu_order_not_global_catalog(flow):
    h=flow
    h.seed("professional",professional=None,professional_name=None,pending_clock="15:00",
        professionals=[{"id":str(h.breno),"name":"Breno"}])
    reply=await h.turn("1",preview=False)
    assert reply["step"]=="confirm" and h.calls["writes"][0][3]==h.breno
    assert not h.calls["plans"]


@pytest.mark.asyncio
async def test_verified_professional_removed_from_eligibility_cannot_create_hold(flow,monkeypatch):
    from app import qr_assistant_interactions as interactions
    h=flow
    h.seed("professional",professional=None,pending_clock="15:00",
        professionals=[{"id":str(h.breno),"name":"Breno"}])
    async def now_eligible(*_): return [h.professionals[0]]
    monkeypatch.setattr(qr_ai,"professionals_for",now_eligible)
    token=interactions._choice.set({"kind":"professional","professional_id":str(h.breno)})
    try:reply=await h.turn("[Escolha de profissional recebida]",preview=False)
    finally:interactions.reset_choice(token)
    assert reply["step"]=="professional" and not h.calls["writes"]
    assert h.state()["professionals"]==[{"id":str(h.bruno),"name":"Bruno"}]


@pytest.mark.parametrize("selected",["foreign","not_rendered"])
@pytest.mark.asyncio
async def test_typed_professional_must_belong_to_both_current_menu_and_tenant_catalog(flow,selected):
    from app import qr_assistant_interactions as interactions
    h=flow
    h.seed("professional",professional=None,pending_clock="15:00",
        professionals=[{"id":str(h.bruno),"name":"Bruno"}])
    identity=uuid4() if selected=="foreign" else h.breno
    token=interactions._choice.set({"kind":"professional","professional_id":str(identity)})
    try:reply=await h.turn("[Escolha de profissional recebida]",preview=False)
    finally:interactions.reset_choice(token)
    assert reply["step"]=="professional_changed" and not h.calls["writes"] and not h.calls["plans"]


@pytest.mark.parametrize("text",["①","²","፩"])
@pytest.mark.asyncio
async def test_nondecimal_digit_symbols_are_clarified_without_int_conversion(flow,text):
    from app.qr_dialogue import choose_professional,choose_service
    h=flow;h.seed("professional",professionals=[{"id":str(h.bruno),"name":"Bruno"}])
    assert choose_professional(text,h.professionals) is None
    assert choose_service(text,h.catalog) is None
    reply=await h.turn(text,preview=False)
    assert reply["step"]=="clarify" and not h.calls["writes"]


def test_question_copula_is_not_fabricated_compound_consent():
    from app.qr_dialogue import compound_candidate
    assert not compound_candidate("Qual é o endereço?")
    assert not compound_candidate("Qual é o valor de corte?")
    assert compound_candidate("Sim e aceitam Pix?")
    assert compound_candidate("Sim, aceitam Pix?")


@pytest.mark.parametrize("message",["Pode me mostrar valores do corte?","Qual é o valor de corte?","Quais os preços do corte?"])
@pytest.mark.asyncio
async def test_price_singular_plural_and_polite_wording_return_scoped_price_without_writes(flow,message):
    reply=await flow.turn(message,preview=False)
    assert "35,00" in reply["answer"] and not flow.calls["writes"]


@pytest.mark.parametrize("mode",["literal","different_faq","no_faq","model_only"])
def test_only_a_current_literal_address_faq_can_supply_missing_structured_address(mode):
    from app.qr_information import info
    from app.qr_understanding import UnderstandingPlan
    profile={"faqs":[]}
    message="Qual é o endereço?"
    faq_ids=[]
    if mode in {"literal","model_only"}:
        profile["faqs"]=[{"question":"Qual é o endereço?","answer":"Rua Sintética, 100."}]
        if mode=="model_only":
            message="Onde fica?";faq_ids=["faq-0"]
    elif mode=="different_faq":
        profile["faqs"]=[{"question":"Tem acessibilidade?","answer":"Temos rampa."}]
        message+=" Tem acessibilidade?";faq_ids=["faq-0"]
    result=info(UnderstandingPlan(intents=["faq"],faq_ids=faq_ids),
        {"name":"Synthetic","profile":profile,"timezone":"America/Cuiaba"},[],message=message)
    if mode=="literal":
        assert "Rua Sintética" in result.answer and "ainda não" not in result.answer
    else:
        assert "endereço ainda não" in result.answer


@pytest.mark.asyncio
async def test_independent_prices_do_not_depend_on_refresh_of_an_unrelated_reschedule(flow,monkeypatch):
    h=flow
    h.seed("reschedule_confirm",appointment=str(h.appointment),confirmation_summary="original",
        confirmation_expires_at=datetime.now().timestamp()+200)
    reads=[]
    async def unavailable(*_):
        reads.append(True)
        raise RuntimeError("synthetic database read failure")
    monkeypatch.setattr(qr_ai,"appointment_services",unavailable)
    reply=await h.turn("Qual é o valor de corte?",preview=False)
    assert "35,00" in reply["answer"] and not reads and not h.calls["writes"]
    blocked=await h.turn("SIM",preview=False)
    assert blocked["step"]=="history_unavailable" and reads==[True] and not h.calls["writes"]


def test_merged_birthday_reply_cannot_leave_a_date_in_later_history():
    exchange=qr_memory.history_exchange("12/03/1990 e tenho outra dúvida",
        "Seu aniversário 12/03 foi registrado. Como posso ajudar?","information")
    assert "12/03" not in exchange[1]["content"]

