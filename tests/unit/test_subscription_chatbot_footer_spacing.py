"""Footer spacing contracts for rendered WhatsApp chatbot messages.

Only synthetic tenant/contact data. No database, network, or real WhatsApp.
"""
from copy import deepcopy
from uuid import uuid4

import pytest

from app.subscription_chatbot_rules import _spaced_footer, compile_plan
from app.subscription_chatbot_schemas import RuleDefinition

FACTS = {
    "name": "Cliente de teste",
    "phone_number": "5511000000001",
    "my_name": "Empresa sintética",
    "my_email": "owner@example.invalid",
    "my_contact_number": "5511000000000",
}


def outbound(response):
    identity = str(uuid4())
    definition = RuleDefinition.model_validate({
        "keyword": "iniciar", "enabled": True, "response": response
    }).model_dump(mode="json")
    original = deepcopy(definition)
    stored = {"id": identity, "creation_order": 1, "revision": 1, **definition}
    plan, deps = compile_plan(identity, {identity: stored}, {}, FACTS,
                              {"name": FACTS["name"], "phone": FACTS["phone_number"], "message": "Oi"})
    assert deps == [identity]
    assert definition == original, "Saved rule must remain unmodified"
    return plan[0]["action"]


def url_button(text="Olá, {name}!", footer="Atendimento {my_name}"):
    return outbound({"kind": "sequence", "actions": [{
        "type": "url_button", "text": text, "footer": footer,
        "button_text": "Abrir suporte", "url": "https://example.com/suporte",
        "delay_seconds": 1,
    }]})


def list_message(description="Escolha um serviço.", footer="Atendimento {my_name}"):
    return outbound({"kind": "list", "title": "Serviços", "description": description,
                     "footer": footer, "button_text": "Ver opções",
                     "sections": [{"title": "Atendimento",
                                   "rows": [{"title": "Ajuda", "description": "",
                                             "action": {"type": "text", "text": "Olá", "delay_seconds": 1}}]}]})


def test_url_button_footer_has_exactly_one_blank_line_and_keeps_cta():
    output = url_button()
    assert output["text"] == "Olá, Cliente de teste!\n\nAtendimento Empresa sintética"
    assert output["footer"] == ""
    assert output["button_text"] == "Abrir suporte"
    assert output["url"] == "https://example.com/suporte"
    assert output["type"] == "url_button"


def test_list_footer_has_one_blank_line_and_preserves_selection():
    output = list_message()
    assert output["description"] == "Escolha um serviço.\n\nAtendimento Empresa sintética"
    assert output["footer"] == ""
    assert output["sections"][0]["rows"][0]["title"] == "Ajuda"
    assert output["sections"][0]["rows"][0]["action"]["text"] == "Olá"
    assert output["button_text"] == "Ver opções"


@pytest.mark.parametrize("body,footer,result", [
    ("Olá", "", ("Olá", "")),
    ("Olá\n", "Rodapé", ("Olá\n\nRodapé", "")),
    ("Olá\n\n\n", "Rodapé", ("Olá\n\nRodapé", "")),
    ("Olá", "Rodapé", ("Olá\n\nRodapé", "")),
    ("Olá", "Linha 1\nLinha 2", ("Olá\n\nLinha 1\nLinha 2", "")),
])
def test_blank_line_is_idempotent_without_changing_user_text(body, footer, result):
    assert _spaced_footer(body, footer, maximum=1024) == result


def test_without_configured_footer_original_text_is_untouched():
    out = url_button(footer="")
    assert out["footer"] == ""
    assert out["text"] == "Olá, Cliente de teste!"


@pytest.mark.parametrize("kind", ["url_button", "list"])
def test_full_authored_message_and_footer_are_rejected_before_transport_plan(kind):
    # The explicit spacing requirement supersedes the old native-footer fallback.
    # Neither content truncation nor conversion into a different action is valid.
    with pytest.raises(ValueError, match="rodapé"):
        if kind == "url_button":
            url_button(text="A" * 1024, footer="Crédito final")
        else:
            list_message(description="A" * 1000, footer="Crédito final")


def test_utf16_units_govern_footer_composition_and_no_surrogate_is_split():
    assert _spaced_footer("😀" * 510, "A", maximum=1024) == ("😀" * 510 + "\n\nA", "")
    with pytest.raises(ValueError, match="rodapé"):
        _spaced_footer("😀" * 511, "A", maximum=1024)


def test_footer_text_remains_data_not_an_executable_template():
    body = "{untrusted} text"
    assert _spaced_footer(body, "{{not_evaluated}}", maximum=1024) == (
        "{untrusted} text\n\n{{not_evaluated}}", ""
    )
