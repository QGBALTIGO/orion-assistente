"""URL/API/webhook JSON path parity, with synthetic provider replies only."""
import json
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.subscription_chatbot_tags import discover_tags, parse_path, render_template, validate_json
from app.subscription_chatbot_schemas import UrlAction, HttpResult
from app.subscription_chatbot_rules import render_http_result

FIXTURE = json.loads((Path(__file__).parents[1] / "fixtures/subscription_url_tag_paths.json").read_text())


@pytest.mark.parametrize("path", FIXTURE["valid"])
def test_valid_paths_are_accepted_by_saved_url_action(path):
    assert parse_path(path)
    assert UrlAction(url="https://api.example.com", custom_template="{{"+path+"}}")


@pytest.mark.parametrize("path", FIXTURE["invalid"])
def test_paths_do_not_evaluate_expressions_or_access_attributes(path):
    with pytest.raises(ValueError):
        parse_path(path)
    # Whitespace surrounding a placeholder is explicitly accepted.
    if path not in {" user", "user "}:
        with pytest.raises(ValidationError):
            UrlAction(url="https://api.example.com", custom_template="{{"+path+"}}")


@pytest.mark.parametrize("case", FIXTURE["responses"])
def test_discovery_and_execution_resolve_exact_same_fields(case):
    assert discover_tags(case["body"]) == case["tags"]
    action = UrlAction(url="https://api.example.com", custom_template=case["template"])
    assert render_http_result(action.model_dump(), case["body"]) == case["expected"]
    receipt = HttpResult(operation_id=uuid4(), device_id=uuid4(), generation=uuid4(),
                         settings_revision=1, chatbot_revision=1, response_json=case["body"])
    assert receipt.response_json == case["body"]


@pytest.mark.parametrize("body", [{"data":{}}, {"data":{"nome":None}}, {"data":{"nome":[]}}, {"data":{"nome":{}}}])
def test_missing_null_or_container_is_never_a_literal_placeholder(body):
    with pytest.raises(ValueError):
        render_http_result({"custom_template":"{{data.nome}}"}, body)


def test_discovery_omits_null_containers_and_reserved_keys_but_keeps_false_zero_and_empty():
    assert discover_tags({"missing":None,"empty":[],"obj":{},"zero":0,"false":False,"text":"",
                          "__proto__":{"token":"private"},"constructor":"private"}) == ["zero","false","text"]
    assert discover_tags([]) == []


def test_default_reply_is_backwards_compatible_and_never_guesses_wrappers():
    assert render_http_result({}, {"reply":"Resposta anterior"}) == "Resposta anterior"
    for value in ({"data":{"reply":"x"}}, [{"reply":"x"}], {"reply":False}):
        with pytest.raises(ValueError):
            render_http_result({}, value)


def test_ambiguous_dotted_keys_use_explicit_brackets_not_guessing():
    body={"data.nome":"literal","data":{"nome":"nested"}}
    assert render_template('{{["data.nome"]}} {{data.nome}}',body) == 'literal nested'


def test_repeated_large_values_are_bounded_before_joining():
    with pytest.raises(ValueError, match="expandida"):
        render_template("{{value}}"*3000,{"value":"x"*100000})
    assert render_template("{{v}}{{v}}",{"v":"😀"},maximum=4) == "😀😀"
    with pytest.raises(ValueError):
        render_template("{{v}}{{v}}",{"v":"😀"},maximum=3)


def test_depth_count_tag_count_and_numeric_bounds():
    assert len(discover_tags({f"v{i}":i for i in range(128)})) == 128
    with pytest.raises(ValueError):
        discover_tags({f"v{i}":i for i in range(129)})
    for value in ({"id":2**53},{"id":float("inf")},{"id":float("nan")},{"text":"\x00"},[None]*8192):
        with pytest.raises(ValueError):
            validate_json(value)
    assert discover_tags({"id":str(2**53)}) == ["id"]
    nested={"v":1}
    for _ in range(16):
        nested={"data":nested}
    with pytest.raises(ValueError):
        discover_tags(nested)


def test_case_and_literal_bracket_names_are_never_guessed():
    body={"Name":"A","name":"B","items[0]":"C"}
    assert render_template('{{Name}} {{name}} {{["items[0]"]}}',body)=="A B C"
    with pytest.raises(ValueError):
        render_template("{{NAME}}",body)


def test_discovery_exposes_nonempty_json_containers_when_requested_without_changing_old_tags():
    # Same shape as a third-party API result; all values are synthetic.
    body = {"dns": "https://example.invalid", "username": "sample-user",
            "connections": 1, "reply": "*Mensagem de demonstração*",
            "data": [{"message": "Olá, teste."}]}
    assert discover_tags(body) == ["dns", "username", "connections", "reply", "data[0].message"]
    tags = discover_tags(body, include_containers=True)
    assert tags[:-1] == discover_tags(body)
    assert tags[-1] == "data"
    assert render_template("{{data}}", body) == '[{"message":"Olá, teste."}]'
    assert render_template("{{reply}}", body) == "*Mensagem de demonstração*"
    assert render_template("{{data[0].message}}", body) == "Olá, teste."


def test_nested_json_container_aliases_are_bounded_and_cannot_execute_expressions():
    body = {"response": {"data": [{"name": "Pessoa de teste"}]}, "empty": {}, "none": None}
    discovered = discover_tags(body, include_containers=True)
    assert discovered == ["response.data[0].name", "response", "response.data"]
    assert render_template("{{response.data}}", body) == '[{"name":"Pessoa de teste"}]'
    for expression in ("{{response.__class__}}", "{{response.data[0].name.upper()}}",
                       "{{response.data[*]}}"):
        with pytest.raises(ValueError):
            render_template(expression, body)
    with pytest.raises(ValueError):
        render_template("{{empty}}", body)
    with pytest.raises(ValueError):
        render_template("{{none}}", body)
    assert len(discover_tags({**{f"x{i}":i for i in range(128)}, "data": [1]}, include_containers=True)) == 128
