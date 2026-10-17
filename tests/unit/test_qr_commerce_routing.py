"""QR's real commerce/planner routing with the existing synthetic backend."""
import json
from uuid import uuid4

import pytest

from app import qr_ai, qr_profile
from app.qr_dialogue import DialogueDecision
from test_qr_multi_flow import flow
from test_qr_commerce import backend, catalog


@pytest.fixture
def routed(flow, backend):
    flow.business["profile"]["capabilities"].append("product_orders")
    events, data = backend
    async def message(text, *, preview=False, identity=None):
        token = qr_ai._message_identity.set(identity or "synthetic-" + str(uuid4()))
        try:
            return await flow.turn(text, preview)
        finally:
            qr_ai._message_identity.reset(token)
    flow.message, flow.events, flow.commercial_data = message, events, data
    flow.private_key = flow.key + ":commerce"
    return flow


@pytest.mark.asyncio
async def test_qr_product_order_summary_then_later_consent_no_private_token_in_http(routed):
    h = routed
    first = await h.message("Quero comprar duas pomadas")
    assert first["step"] == "commerce_confirm" and "R$ 50,00" in first["answer"]
    assert set(first) == {"answer", "step"} and "x" * 43 not in first["answer"]
    assert h.events.count("order") == 1 and "confirm" not in h.events and not h.calls["writes"]
    private = json.loads(h.redis.data[h.private_key])
    assert private["tenant"] == str(h.tenant) and private["phone"] == "5567999999999" and private["pending"]["token"]
    done = await h.message("pode confirmar")
    assert done["step"] == "commerce_confirmed" and h.events.count("confirm") == 1
    assert "Pagamento não verificado" in done["answer"] and "state" not in done
    assert not h.calls["plans"] and not h.calls["writes"]


@pytest.mark.asyncio
async def test_product_price_and_preview_never_create_quote_order_or_stock_reservation(routed):
    h = routed;old = h.seed("date")
    reply = await h.message("Quanto custa duas pomadas?")
    assert reply["step"] == "commerce_information" and "R$ 50,00" in reply["answer"]
    assert h.events == ["estimate"] and h.state()["services"] == old["services"]
    preview = await h.message("Quero comprar duas pomadas", preview=True)
    assert preview["step"] == "commerce_preview" and "nenhuma operação" in preview["answer"]
    done = await h.message("pode confirmar", preview=True)
    assert done["step"] == "commerce_preview_complete" and set(h.events) == {"estimate"}
    assert not h.calls["writes"]


@pytest.mark.asyncio
@pytest.mark.parametrize("text", [
    "Quero comprar duas pomadas e agendar corte amanhã às 15:00 com Bruno",
    "Quanto custa pomada e corte? Quero fazer barba amanhã",
])
async def test_mixed_product_and_booking_asks_priority_before_any_estimate_or_mutation(routed, text):
    h = routed;reply = await h.message(text)
    assert reply["step"] == "choose_priority" and not h.events
    assert not h.calls["writes"] and not h.calls["reads"] and not h.calls["plans"]


@pytest.mark.asyncio
async def test_commerce_summary_cannot_be_confirmed_after_an_intervening_greeting(routed):
    h = routed;await h.message("Quero comprar duas pomadas")
    await h.message("Oi")
    result = await h.message("pode confirmar")
    assert result["step"] == "commerce_new_confirmation_required" and "confirm" not in h.events


@pytest.mark.asyncio
async def test_product_orders_capability_absent_only_returns_facts(routed):
    h = routed;h.business["profile"]["capabilities"].remove("product_orders")
    result = await h.message("Quero comprar duas pomadas")
    assert result["step"] == "commerce_information" and h.events == ["estimate"]
    assert not h.calls["writes"]


@pytest.mark.asyncio
@pytest.mark.parametrize("flag", ["0", "1"])
@pytest.mark.parametrize("text", ["Quanto custa duas pomadas?", "Quais produtos vocês vendem?",
    "Tem pomada disponível?", "Para que serve a pomada?", "A pomada tem perfume?"])
async def test_product_consultations_require_published_catalog_answers_capability(routed, monkeypatch, flag, text):
    h = routed;monkeypatch.setenv("QR_AI_MULTI_INTENT_ENABLED", flag)
    h.business["profile"]["capabilities"] = ["product_orders"]
    reply = await h.message(text)
    assert reply["step"] == "capability_unavailable" and "R$" not in reply["answer"]
    assert not h.events and not h.calls["reads"] and not h.calls["writes"]


@pytest.mark.asyncio
async def test_catalog_answers_gate_does_not_replace_independent_product_orders_capability(routed):
    h = routed;h.business["profile"]["capabilities"] = ["product_orders"]
    proposal = await h.message("Quero comprar duas pomadas")
    assert proposal["step"] == "commerce_confirm" and h.events.count("order") == 1
    done = await h.message("pode confirmar")
    assert done["step"] == "commerce_confirmed" and h.events.count("confirm") == 1


@pytest.mark.asyncio
async def test_unpublished_legacy_profile_has_no_new_catalog_capability_gate(routed):
    h = routed;h.business.pop("profile_id");h.business["profile"]["capabilities"] = []
    reply = await h.message("Quanto custa duas pomadas?")
    assert reply["step"] == "commerce_information" and "R$ 50,00" in reply["answer"]
    assert h.events == ["estimate"] and not h.calls["writes"]


@pytest.mark.asyncio
async def test_natural_semantic_commerce_consent_does_not_need_a_second_planner(routed, monkeypatch):
    h = routed;await h.message("Quero comprar duas pomadas")
    async def semantic(**kwargs):
        assert kwargs["confirmation_action"] == "order"
        return DialogueDecision("affirm", "synthetic-semantic")
    monkeypatch.setattr(qr_ai, "interpret_contextual_response", semantic)
    result = await h.message("Do jeitinho que você mostrou, podemos seguir em frente")
    assert result["step"] == "commerce_confirmed" and h.events.count("confirm") == 1
    assert not h.calls["plans"]


@pytest.mark.asyncio
async def test_two_pending_summaries_require_target_and_neither_is_committed(routed):
    h = routed;await h.message("Quero comprar duas pomadas")
    from datetime import datetime
    h.seed("confirm", hold=str(uuid4()), confirmation_summary="Corte com Bruno",
        confirmation_expires_at=datetime.now().timestamp()+240)
    result = await h.message("pode confirmar")
    assert result["step"] == "choose_priority" and "confirm" not in h.events and not h.calls["writes"]
    assert h.state()["confirmation_requires_review"]


@pytest.mark.asyncio
async def test_business_revision_change_revokes_private_commerce_consent(routed):
    h = routed;await h.message("Quero comprar duas pomadas")
    h.business["profile_id"] = str(uuid4())
    result = await h.message("pode confirmar")
    assert result["step"] == "context_changed" and "confirm" not in h.events
    assert h.private_key not in h.redis.data


@pytest.mark.asyncio
async def test_private_cart_from_another_phone_is_rejected_before_effects(routed):
    h = routed
    h.redis.data[h.private_key] = json.dumps({"kind": "commerce", "tenant": str(h.tenant), "phone": "5567999999998", "cart": []})
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as error:
        await h.message("Quero comprar duas pomadas")
    assert error.value.status_code == 403 and not h.events and not h.calls["writes"]
