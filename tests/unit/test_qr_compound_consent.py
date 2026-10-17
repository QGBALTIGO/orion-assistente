"""Whole-speech consent scope; synthetic fixtures only, no live operations."""
from datetime import datetime
import json
from uuid import uuid4

from fastapi import HTTPException
import httpx
import pytest

from app import qr_ai, qr_dialogue
from test_qr_commerce_routing import routed
from test_qr_multi_flow import flow
from test_qr_commerce import backend, catalog


@pytest.fixture
def compound(routed, monkeypatch):
    h = routed;h.compound_calls=[]
    h.compound_value = {"kind": "independent_information", "consent_clause": "Pode confirmar.",
        "information_clause": "E vocês vendem pomada?", "information_kind": "catalog"}
    async def scoped(**kwargs):
        h.compound_calls.append(kwargs["message"])
        def response(request):
            payload = json.loads(request.content)
            assert payload["response_format"]["json_schema"]["strict"] is True
            assert "tools" not in payload and len(payload["messages"]) == 2
            return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
                "content": json.dumps(h.compound_value)}}]})
        return await qr_dialogue.interpret_compound_response(message=kwargs["message"], key="synthetic", model="openai/gpt-oss-20b",
            transport=httpx.MockTransport(response))
    monkeypatch.setattr(qr_ai, "interpret_compound_response", scoped)
    def pending(**extra):
        return h.seed("confirm", hold=str(uuid4()), price=3500, duration=40,
            selected_start=f"{h.tomorrow}T15:00:00-04:00", confirmation_summary="Corte com Bruno, resumo vigente",
            confirmation_message_id="synthetic-previous-summary", confirmation_expires_at=datetime.now().timestamp()+240, **extra)
    h.pending = pending
    pending()
    return h


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["text", "audio-transcript"])
async def test_independent_catalog_question_confirms_same_summary_once_without_cart_or_quantity(compound, channel):
    h = compound
    result = await h.message("Pode confirmar. E vocês vendem pomada?", identity="synthetic-"+channel)
    assert result["step"] == "confirmed" and "Pomada" in result["answer"] and "R$ 35,00" in result["answer"]
    assert "R$ 25,00" not in result["answer"] and "1 un" not in result["answer"]
    assert [x[0] for x in h.calls["writes"]] == ["confirm"] and h.events == []
    assert h.private_key not in h.redis.data and len(h.compound_calls) == 1 and not h.calls["plans"]


@pytest.mark.asyncio
async def test_semantic_nonwhitelisted_consent_and_independent_faq_coexist(compound):
    h = compound;text="Do jeito que você mostrou, podemos seguir. Aceitam Pix?"
    h.compound_value.update(consent_clause="Do jeito que você mostrou, podemos seguir.",
        information_clause="Aceitam Pix?", information_kind="faq")
    result = await h.message(text)
    assert result["step"] == "confirmed" and "Aceitamos Pix" in result["answer"]
    assert [x[0] for x in h.calls["writes"]] == ["confirm"] and not h.events


@pytest.mark.asyncio
@pytest.mark.parametrize("tail", [" Mas só se tiver desconto.", " Não, espera.", " Troca para as 16h."])
async def test_provider_must_not_omit_a_trailing_condition_negative_or_change(compound, tail):
    h = compound
    result = await h.message("Pode confirmar. E vocês vendem pomada?"+tail)
    assert result["step"] == "confirm" and not h.calls["writes"] and h.state()["confirmation_requires_review"]


@pytest.mark.asyncio
async def test_question_about_the_booking_facts_is_not_independent_consent(compound):
    h = compound;text="Pode confirmar. Quanto fica com barba?"
    h.compound_value.update(information_clause="Quanto fica com barba?", information_kind="price")
    result = await h.message(text)
    assert result["step"] == "confirm" and not h.calls["writes"]


@pytest.mark.asyncio
async def test_purchase_in_second_clause_never_enters_readonly_confirmation_exception(compound):
    h = compound;text="Pode confirmar. Quero comprar duas pomadas."
    h.compound_value.update(information_clause="Quero comprar duas pomadas.")
    result = await h.message(text)
    assert result["step"] == "confirm" and not h.calls["writes"] and not h.events


@pytest.mark.asyncio
async def test_other_scope_cannot_fall_through_to_an_implicit_product_order(compound):
    h=compound;h.compound_value.update(kind="other",consent_clause=None,information_clause=None,information_kind="none")
    result=await h.message("Pode confirmar. Quero duas pomadas.")
    assert result["step"] == "choose_priority" and not h.calls["writes"] and not h.events


@pytest.mark.asyncio
async def test_unknown_product_or_faq_does_not_authorize_operation(compound):
    h = compound;text="Pode confirmar. Vocês vendem produto inexistente?"
    h.compound_value.update(information_clause="Vocês vendem produto inexistente?")
    result = await h.message(text)
    assert result["step"] == "confirm" and not h.calls["writes"] and not h.events


@pytest.mark.asyncio
async def test_long_complete_facts_fail_preflight_before_confirmation(compound):
    h = compound;h.business["profile"]["faqs"][0]["answer"] = "Condição publicada longa. " * 80
    h.pending();h.compound_value.update(information_clause="Aceitam Pix?", information_kind="faq")
    result = await h.message("Pode confirmar. Aceitam Pix?")
    assert result["step"] == "confirm" and not h.calls["writes"] and h.state()["confirmation_requires_review"]


@pytest.mark.asyncio
async def test_backend_failure_does_not_report_success_but_keeps_independent_facts(compound, monkeypatch):
    h = compound
    async def expired(*_):
        raise HTTPException(409, "synthetic-expired")
    monkeypatch.setattr(qr_ai,"confirm_hold",expired)
    result = await h.message("Pode confirmar. E vocês vendem pomada?")
    assert result["step"] == "expired" and "Agendamento confirmado" not in result["answer"]
    assert "Pomada" in result["answer"] and not h.events


@pytest.mark.asyncio
async def test_existence_question_alone_never_estimates_implicit_quantity_or_creates_cart(compound):
    h = compound;result = await h.message("Vocês vendem pomada?")
    assert result["step"] == "commerce_information" and "Pomada" in result["answer"]
    assert "R$ 25" not in result["answer"] and h.events == [] and not h.calls["writes"]
    assert h.private_key not in h.redis.data


@pytest.mark.asyncio
async def test_provider_cannot_drop_the_question_mark_from_uncertain_consent(compound):
    h = compound;h.compound_value.update(consent_clause="Pode confirmar", information_clause="E vocês vendem pomada?")
    result = await h.message("Pode confirmar? E vocês vendem pomada?")
    assert result["step"] == "confirm" and not h.calls["writes"] and not h.events


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["expired", "review", "same_message"])
async def test_independent_question_does_not_bypass_old_summary_guards(compound, change):
    h = compound;state=h.state()
    if change == "expired":
        state["confirmation_expires_at"] = datetime.now().timestamp()-1
    elif change == "review":
        state["confirmation_requires_review"] = True
    h.redis.data[h.key]=json.dumps(state)
    result = await h.message("Pode confirmar. E vocês vendem pomada?",
        identity="synthetic-previous-summary" if change == "same_message" else None)
    assert not h.calls["writes"] and not h.events and not h.compound_calls
    assert result["step"] != "confirmed"
