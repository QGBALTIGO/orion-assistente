"""Read-only price continuations use current facts, never booking authority."""
import json
from uuid import uuid4

import pytest

from app import qr_ai
from app.qr_understanding import UnderstandingPlan
from test_qr_multi_flow import flow  # noqa: F401


async def quote(h, monkeypatch, *, mode="from", message="Quanto custa corte?"):
    h.catalog[0].update(price_mode=mode, name="Corte")
    result = await h.turn(message, preview=False)
    assert "35,00" in result["answer"] and not h.calls["writes"]
    h.proposal["error"] = True
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize("question", ["Mas pq a partir de?", "Por que a partir de?", "O que significa a partir de?", "Esse preço é fechado?"])
async def test_variable_price_followup_uses_current_subject_without_model_or_writes(flow, monkeypatch, question):
    h = flow
    await quote(h, monkeypatch)
    before = len(h.calls["plans"])
    result = await h.turn(question, preview=False)
    assert all(term in result["answer"] for term in ("Corte", "35,00", "valor inicial", "equipe"))
    assert "confirme com a equipe" in result["answer"] and not result.get("degraded")
    assert not any(term in result["answer"].lower() for term in ("comprimento", "quantidade de", "material", "tipo de cabelo"))
    assert len(h.calls["plans"]) == before and not h.calls["reads"] and not h.calls["writes"] and not h.calls["names"]
    assert h.state()["price_information"]["service_ids"] == [str(h.cut)]
    assert not h.state().get("services") and not h.state().get("hold")


@pytest.mark.asyncio
async def test_fixed_price_followup_explains_the_current_fixed_registration(flow, monkeypatch):
    h = flow
    await quote(h, monkeypatch, mode="fixed")
    result = await h.turn("Esse preço é fechado?", preview=False)
    assert "preço fixo cadastrado" in result["answer"] and "35,00" in result["answer"]
    assert "valor inicial" not in result["answer"] and not h.calls["writes"]


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["revision", "removed", "changed_to_fixed", "expired", "future", "nan", "multiple", "foreign_phone", "foreign_namespace", "foreign_tenant", "new_topic", "withdrawn_topic_faq"])
async def test_price_reference_is_revalidated_and_cannot_cross_scope_or_topic(flow, monkeypatch, change):
    h = flow
    await quote(h, monkeypatch)
    original = h.state()
    if change == "revision":
        h.catalog[0]["price_cents"] = 9900
    elif change == "removed":
        h.catalog[:] = h.catalog[1:]
    elif change == "changed_to_fixed":
        h.catalog[0]["price_mode"] = "fixed"
    elif change in {"expired", "future", "nan", "multiple"}:
        marker = original["price_information"]
        if change == "expired": marker["quoted_at"] -= 301
        if change == "future": marker["quoted_at"] += 301
        if change == "nan": marker["quoted_at"] = float("nan")
        if change == "multiple": marker["service_ids"].append(str(h.beard))
        h.redis.data[h.key] = json.dumps(original)
    elif change == "foreign_phone":
        result = await qr_ai.turn(h.tenant, "5567999999998", "Mas pq a partir de?", h.redis, h.namespace, preview=True)
    elif change == "foreign_namespace":
        other = h.namespace+":other"
        h.redis.data[f"orion:qr:ai:{h.tenant}:{other}"] = json.dumps(original)
        result = await qr_ai.turn(h.tenant, "5567999999999", "Mas pq a partir de?", h.redis, other, preview=True)
    elif change == "foreign_tenant":
        other = uuid4()
        async def same_catalog(_): return h.business, h.catalog, h.professionals
        monkeypatch.setattr(qr_ai, "tenant_data", same_catalog)
        h.redis.data[f"orion:qr:ai:{other}:{h.namespace}"] = json.dumps(original)
        result = await qr_ai.turn(other, "5567999999999", "Mas pq a partir de?", h.redis, h.namespace, preview=True)
    elif change in {"new_topic", "withdrawn_topic_faq"}:
        h.proposal["error"] = False
        await h.turn("Esquece esse assunto. Aceitam Pix?" if change == "withdrawn_topic_faq"
            else "Esquece esse pedido, quero perguntar outra coisa", preview=False)
        assert "price_information" not in h.state()
        h.proposal["error"] = True
    if change not in {"foreign_phone", "foreign_namespace", "foreign_tenant"}:
        result = await h.turn("Mas pq a partir de?", preview=False)
    assert "Qual serviço" in result["answer"] and not result.get("degraded")
    assert "35,00" not in result["answer"] and "valor inicial" not in result["answer"]
    assert not h.calls["reads"] and not h.calls["writes"] and not h.calls["names"]


@pytest.mark.asyncio
@pytest.mark.parametrize("body", ["Mas pq a partir de? Quero agendar amanhã às 15h", "Esse preço é fechado? Troque para barba", "Por que a partir de? Pode confirmar", "Mas pq a partir de? Cancelar meu horário"])
async def test_mixed_operation_is_never_absorbed_by_price_explanation(flow, monkeypatch, body):
    h = flow
    await quote(h, monkeypatch)
    before = len(h.calls["plans"])
    result = await h.turn(body, preview=False)
    assert result.get("degraded") and len(h.calls["plans"]) == before+1
    assert "valor inicial" not in result["answer"] and not h.calls["writes"]


@pytest.mark.asyncio
async def test_second_distinct_price_subject_replaces_information_without_selecting_booking(flow, monkeypatch):
    h = flow
    await quote(h, monkeypatch)
    h.proposal["error"] = False
    await h.turn("Quanto custa barba?", preview=False)
    h.proposal["error"] = True
    result = await h.turn("Esse preço é fechado?", preview=False)
    assert "Barba" in result["answer"] and "20,00" in result["answer"] and "Corte" not in result["answer"]
    assert h.state()["price_information"]["service_ids"] == [str(h.beard)]
    assert not h.state().get("services") and not h.calls["writes"]


@pytest.mark.asyncio
async def test_read_only_faqs_keep_price_subject_without_extending_its_deadline(flow, monkeypatch):
    h = flow
    await quote(h, monkeypatch)
    quoted_at = h.state()["price_information"]["quoted_at"]
    h.proposal["error"] = False
    h.business["profile"]["faqs"].append({"question": "Tem estacionamento?", "answer": "Temos estacionamento."})
    # Add published facts before re-establishing the quote's catalog revision.
    await h.turn("Quanto custa corte?", preview=False)
    quoted_at = h.state()["price_information"]["quoted_at"]
    for question in ("Aceitam Pix?", "Tem estacionamento?"):
        await h.turn(question, preview=False)
        assert h.state()["price_information"]["quoted_at"] == quoted_at
    h.proposal["error"] = True
    result = await h.turn("Esse preço é fechado?", preview=False)
    assert "valor inicial" in result["answer"] and "Corte" in result["answer"] and not h.calls["writes"]
    assert h.state()["price_information"]["quoted_at"] == quoted_at


@pytest.mark.asyncio
async def test_real_pigmentation_sequence_answers_without_inventing_price_criteria(flow, monkeypatch):
    h = flow
    h.catalog[0].update(name="Pigmentação Capilar Permanente", price_cents=3000, price_mode="from", duration_min=40)
    h.proposal["value"] = UnderstandingPlan(intents=["price"], query_service_ids=[h.cut])
    first = await h.turn("Quanto custa uma pigmentação", preview=False)
    assert "Pigmentação Capilar Permanente" in first["answer"] and "30,00" in first["answer"]
    h.proposal["error"] = True
    before = len(h.calls["plans"])
    result = await h.turn("Mas pq a partir de?", preview=False)
    assert all(text in result["answer"] for text in ("Pigmentação Capilar Permanente", "30,00", "valor inicial", "equipe"))
    assert len(h.calls["plans"]) == before and not result.get("degraded")
    assert not h.calls["writes"] and not h.calls["names"]


@pytest.mark.asyncio
async def test_actual_multi_service_quote_does_not_guess_which_price_needs_explaining(flow, monkeypatch):
    h = flow
    await quote(h, monkeypatch, message="Quanto custa corte e barba?")
    assert len(h.state()["price_information"]["service_ids"]) == 2
    result = await h.turn("Mas pq a partir de?", preview=False)
    assert "Qual serviço" in result["answer"] and not result.get("degraded") and not h.calls["writes"]


@pytest.mark.asyncio
async def test_price_explanation_keeps_pending_booking_and_requires_fresh_review(flow, monkeypatch):
    h = flow
    h.catalog[0]["price_mode"] = "from"
    original = h.seed("confirm", hold=str(uuid4()), price=3500, duration=40)
    await quote(h, monkeypatch)
    result = await h.turn("Mas pq a partir de?", preview=False)
    state = h.state()
    assert "valor inicial" in result["answer"] and state["confirmation_requires_review"]
    assert state["hold"] == original["hold"] and state["services"] == original["services"]
    assert state["confirmation_message_id"] == original["confirmation_message_id"]
    assert not h.calls["writes"] and not h.calls["names"]


@pytest.mark.asyncio
async def test_omitted_quote_cannot_install_a_price_reference(flow, monkeypatch):
    h = flow
    monkeypatch.setattr(qr_ai, "_bounded_reply", lambda *_args, **_kw:
        {"answer": "Não consegui apresentar os valores agora.", "step": "information"})
    await h.turn("Quanto custa corte?", preview=False)
    assert "price_information" not in h.state()
    h.proposal["error"] = True
    result = await h.turn("Esse preço é fechado?", preview=False)
    assert "Qual serviço" in result["answer"] and not h.calls["writes"]


@pytest.mark.asyncio
async def test_final_formatter_fallback_cannot_retain_an_undelivered_quote_subject(flow, monkeypatch):
    h = flow
    original = qr_ai.presentation.render_message
    monkeypatch.setattr(qr_ai.presentation, "render_message", lambda *_args, **_kwargs: "x" * 4097)
    result = await h.turn("Quanto custa corte?", preview=False)
    assert result["step"] == "choose_priority" and "price_information" not in h.state()
    monkeypatch.setattr(qr_ai.presentation, "render_message", original)
    h.proposal["error"] = True
    result = await h.turn("Esse preço é fechado?", preview=False)
    assert "Qual serviço" in result["answer"] and not h.calls["writes"]
