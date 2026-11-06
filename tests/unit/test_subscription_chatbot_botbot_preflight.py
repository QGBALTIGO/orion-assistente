"""Bateria de regressão do importador BotBot: conversão, prévia e segurança.

Somente dados sintéticos. Nada é enviado ao WhatsApp, nenhum endpoint é
consultado e arquivos particulares não são adicionados ao repositório.
"""
from __future__ import annotations

import io
import json
from copy import deepcopy
from uuid import UUID
import zipfile

import pytest
from app.subscription_chatbot_archive_format import read_archive
from app.subscription_chatbot_botbot_preflight import _safe_reason
from app.subscription_chatbot_botbot import _records


def fixture_zip(replies, filename="botbot_replies_sample.zip"):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as target:
        target.writestr("reply-export.json",
                        json.dumps({"replies": replies, "exported_at": "2026-11-06T12:00:00Z"},
                                   ensure_ascii=False))
    return read_archive(buffer.getvalue(), filename)


def txt(index, value="Olá"):
    return {"id": index, "keyword": "key-" + str(index),
            "response": {"type": "text", "text": value}}


def test_botbot_menu_uses_neutral_defaults_and_preserves_choices_exactly():
    rows = [txt(5), {"id": 4, "keyword": "menu", "response": {
        "kind": "list", "lists": [{"title": "Suporte", "options": [
            {"label": "Abrir ajuda", "action": {"type": "nested_reply", "reply_id": 5}}
        ]}]}}]
    document = fixture_zip(rows)
    assert len(document["rules"]) == 2
    menu = document["rules"][1]["definition"]["response"]
    assert menu["title"] == "Opções"
    assert menu["description"] == "Escolha uma opção."
    assert menu["button_text"] == "Ver opções"
    assert menu["sections"][0]["rows"][0]["title"] == "Abrir ajuda"
    assert menu["sections"][0]["rows"][0]["action"]["rule_id"] == document["rules"][0]["id"]
    assert all(not item["definition"]["enabled"] for item in document["rules"])


def test_batch_preflight_reports_multiple_errors_in_one_attempt_and_no_private_data():
    source = [
        txt(1),
        txt(2, "{{account_password}}"),
        {"id": 3, "keyword": "SENSITIVE-LABEL", "response": {
            "kind": "list", "sections": [{"rows": [
                {"title": "Y" * 55, "action": {"type": "text", "text": "resposta de exemplo"}}
            ]}]}},
        {"id": 4, "keyword": "SENSITIVE-URL", "response": {
            "type": "url", "url": "http://127.0.0.1/PRIVATE-URL",
            "method": "GET", "custom_template": "{{reply}}"}},
    ]
    with pytest.raises(ValueError) as info:
        fixture_zip(source)
    message = str(info.value)
    assert "3 resposta(s)" in message
    for position in ("Resposta 2:", "Resposta 3:", "Resposta 4:"):
        assert position in message
    for secret in ("SENSITIVE-LABEL", "SENSITIVE-URL", "PRIVATE-URL",
                   "account_password", "Y" * 55):
        assert secret not in message
    assert len(message) <= 235


@pytest.mark.parametrize("bad", [
    "{{unknown}}",
    "{script}",
    "{{name}}",
    "Hello {{password}}",
    "<text> {address}",
])
def test_text_with_unresolvable_placeholders_is_not_mistaken_for_a_valid_rule(bad):
    with pytest.raises(ValueError, match="conteúdo ou tags incompatíveis"):
        fixture_zip([txt(23, bad)])


@pytest.mark.parametrize("malformed", [
    {"id": 1, "keyword": "x", "response": {"kind": "list", "sections": [{"rows": [
        {"title": "", "action": {"type": "text", "text": "ok"}}
    ]}]}},
    {"id": 1, "keyword": "x", "response": {"kind": "list", "sections": [{"rows": [
        {"title": "Z" * 50, "action": {"type": "text", "text": "ok"}}
    ]}]}},
    {"id": 1, "keyword": "x", "response": {"type": "url",
        "url": "http://127.0.0.1/private", "method": "POST"}},
])
def test_invalid_menu_or_url_preflight_rejects_in_one_step(malformed):
    with pytest.raises(ValueError) as err:
        fixture_zip([malformed])
    assert "1:" in str(err.value)
    assert "http://" not in str(err.value)


@pytest.mark.parametrize("delay", [
    {"delayMilliseconds": 301000},
    {"delay_seconds": 301},
    {"delayMs": 1499},
    {"delay": "nope"},
    {"delay": -5},
])
def test_invalid_delays_are_reported_without_implicit_approximation(delay):
    record = txt(12)
    record["response"].update(delay)
    with pytest.raises(ValueError):
        fixture_zip([record])


def test_large_valid_export_and_repeated_import_is_deterministic():
    collection = [txt(i, "Olá {name}!") for i in range(1, 121)]
    original = deepcopy(collection)
    first = fixture_zip(collection)
    second = fixture_zip(collection)
    assert first == second
    assert collection == original
    assert len(first["rules"]) == 120
    assert len({r["id"] for r in first["rules"]}) == 120
    assert all(isinstance(r["definition"]["enabled"], bool)
               and not r["definition"]["enabled"] for r in first["rules"])


def test_preflight_errors_are_scoped_and_not_affected_by_another_import():
    bad = [txt(1, "{{unresolvable}}")]
    good = [txt(1, "Mensagem válida {name}")]
    with pytest.raises(ValueError):
        fixture_zip(bad)
    document = fixture_zip(good)
    assert document["rules"][0]["definition"]["response"]["actions"][0]["text"] == "Mensagem válida {name}"


def test_non_numeric_or_mixed_object_keys_do_not_form_fake_reply_collections():
    assert _records({"foo": {"keyword": "abc", "response": "yes"}}) is None
    assert _records({1: {"keyword": "abc", "response": "yes"}}) is None
    assert len(_records({"1": {"keyword": "abc", "response": "yes"}})) == 1


def test_preflight_error_renderer_is_whitelist_only():
    issue = {"type": "value_error",
             "loc": ("response", "sequence", "actions", 1, "text"),
             "msg": "secret 123456", "input": "sensitive data", "ctx": {"error": "secret"}}
    rendered = _safe_reason(issue)
    assert rendered == "mensagem 2: texto conteúdo ou tags incompatíveis"
    assert "secret" not in rendered


def test_multiple_conversion_errors_are_aggregated_together_with_schema_errors():
    records = [
        txt(1, "mensagem válida"),
        {"id": 2, "keyword": "private-keyword", "response": {
            "type": "sticker", "asset": {"private": "SENSITIVE"}}},
        {"id": 3, "keyword": "menu", "response": {
            "kind": "list", "sections": [{"rows": [
                {"title": "X" * 40, "action": {"type": "text", "text": "hello"}}]}]}},
        {"id": 4, "keyword": "url", "response": {
            "type": "url", "url": "https://example.com/secret",
            "method": "PUT", "custom_template": "{{reply}}"}},
        {"id": 5, "keyword": "hello", "response": "Text {name}"},
    ]
    with pytest.raises(ValueError) as error:
        fixture_zip(records)
    value = str(error.value)
    assert "3 resposta(s)" in value
    for number in (2, 3, 4):
        assert f"Resposta {number}" in value
    assert "SENSITIVE" not in value
    assert "private-keyword" not in value
    assert "example.com/secret" not in value
    assert len(value) <= 235


def test_duplicate_invalid_and_valid_imports_never_mutate_the_input():
    records = [txt(1, "Olá"), txt(2, "{{invalid}}"), txt(3, "Oi")]
    before = deepcopy(records)
    with pytest.raises(ValueError):
        fixture_zip(records)
    assert records == before
    del records[1]
    converted = fixture_zip(records)
    assert len(converted["rules"]) == 2
    assert all(not r["definition"]["enabled"] for r in converted["rules"])
