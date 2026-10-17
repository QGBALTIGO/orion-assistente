"""Testes isolados: nunca enviam chamadas à Groq real."""
import json
from uuid import uuid4

import httpx
import pytest

from app.groq_ai import GroqServiceError, interpret


@pytest.mark.asyncio
async def test_detects_natural_request_and_uses_only_catalog_ids():
    service_id = uuid4()
    other_id = uuid4()
    attempts = []

    def fake(request):
        assert request.url.host == "api.groq.com"
        assert request.headers["authorization"] == "Bearer test-fake-key"
        payload = json.loads(request.content)
        assert payload["response_format"]["type"] == "json_object"
        assert payload["tools"] if "tools" in payload else True
        attempts.append(payload)
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({
            "intent": "agendar",
            "service_ids": [str(service_id), str(other_id), str(service_id)],
            "requested_when": "amanhã depois do almoço",
        })}}]})

    result = await interpret(
        message="Quero dar um tapa no visual amanhã depois do almoço",
        history=[], catalog=[{"id": service_id, "name": "Corte masculino"}],
        key="test-fake-key", model="llama-3.3-70b-versatile",
        transport=httpx.MockTransport(fake),
    )
    assert result.intent == "agendar"
    assert result.service_ids == [service_id]
    assert result.requested_when == "amanhã depois do almoço"
    assert len(attempts) == 1


@pytest.mark.asyncio
async def test_rejects_invalid_json_and_leaves_no_reservation():
    transport = httpx.MockTransport(lambda _request: httpx.Response(
        200, json={"choices": [{"message": {"content": "ok"}}]}))
    with pytest.raises(GroqServiceError) as exc:
        await interpret(
            message="Reserve o salão inteiro",
            history=[], catalog=[], key="fake", model="llama-3.3-70b-versatile",
            transport=transport,
        )
    assert exc.value.status == 502


@pytest.mark.asyncio
async def test_cannot_override_model_or_use_missing_key():
    with pytest.raises(GroqServiceError) as missing:
        await interpret(message="oi", history=[], catalog=[], key="", model="llama-3.3-70b-versatile")
    assert missing.value.status == 503
    with pytest.raises(GroqServiceError) as invalid:
        await interpret(message="oi", history=[], catalog=[], key="fake", model="unknown-model")
    assert invalid.value.status == 503


@pytest.mark.asyncio
async def test_invalid_provider_auth_error_not_leaked():
    transport = httpx.MockTransport(lambda _request: httpx.Response(
        401, json={"error": "any sensitive provider response"}))
    with pytest.raises(GroqServiceError) as exc:
        await interpret(
            message="oi", history=[], catalog=[], key="fake", model="llama-3.3-70b-versatile",
            transport=transport,
        )
    assert exc.value.status == 502
    assert "sensitive" not in exc.value.message


@pytest.mark.asyncio
async def test_handoff_intention_does_not_execute_any_tool():
    transport = httpx.MockTransport(lambda _request: httpx.Response(
        200, json={"choices": [{"message": {"content": json.dumps({
            "intent": "humano", "service_ids": [], "requested_when": None,
        })}}]}))
    result = await interpret(
        message="Preciso de um atendente humano", history=[], catalog=[],
        key="fake", model="llama-3.3-70b-versatile", transport=transport,
    )
    assert result.intent == "humano"
    assert result.service_ids == []


@pytest.mark.asyncio
async def test_current_context_and_recent_exchanges_are_sent_as_classification_only():
    cut, beard = uuid4(), uuid4()
    context = {"etapa": "professional", "data": "2026-11-20",
               "servicos_selecionados": ["Corte de cabelo"]}
    history = [{"role": "user", "content": "Quero corte de cabelo"},
               {"role": "assistant", "content": "Qual profissional prefere? Bruno ou Carlos?"}]
    def provider(request):
        payload = json.loads(request.content)
        assert "tools" not in payload
        messages = payload["messages"]
        assert "Uma pergunta de preço" in messages[0]["content"]
        contextual = next(m for m in messages if m["content"].startswith("CONTEXTO ATUAL"))
        assert json.loads(contextual["content"].split(": ", 1)[1]) == context
        assert messages[-3:-1] == history
        assert messages[-1] == {"role": "user", "content": "Quanto custa cabelo e barba?"}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({
            "intent": "perguntar", "service_ids": [str(cut), str(beard)], "requested_when": None
        })}}]})
    result = await interpret(message="Quanto custa cabelo e barba?", history=history,
        catalog=[{"id": cut, "name": "Corte de cabelo"}, {"id": beard, "name": "Barba"}],
        context=context, key="synthetic-key", model="openai/gpt-oss-120b",
        transport=httpx.MockTransport(provider))
    assert result.intent == "perguntar" and result.service_ids == [cut, beard]
