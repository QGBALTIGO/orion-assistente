from uuid import uuid4

import httpx
import json
import pytest
from pydantic import ValidationError

from app.inbox_templates import LabelSave, QuickReplySave, render_reply, template_variables
from app.main import app


@pytest.mark.parametrize("template", ["{{cliente.senha}}", "{{cliente.nome", "{cliente.nome}", "{{__class__.mro}}", "{{cliente.nome!r}}", "{{cliente.nome:>100}}", "\x00"])
def test_templates_reject_unknown_and_executable_expressions(template):
    with pytest.raises(ValueError):
        template_variables(template)


def test_only_real_facts_fill_quick_reply():
    assert render_reply("Olá {{ cliente.primeiro_nome }} · {{empresa.nome}}", {
        "cliente.primeiro_nome": "Ana", "empresa.nome": "Orion"}) == "Olá Ana · Orion"
    with pytest.raises(ValueError, match="Dados reais indisponíveis"):
        render_reply("Seu horário é {{agendamento.horario}}", {})
    with pytest.raises(ValueError):
        render_reply("{{cliente.nome}}", {"cliente.nome": "😀"*601})


def test_factual_values_are_not_executed_as_templates():
    assert render_reply("{{cliente.nome}}", {"cliente.nome": "{{outro.campo}}"}) == "{{outro.campo}}"


def test_labels_and_replies_are_bounded_and_versioned():
    assert LabelSave(name="  Novo   cliente ").name == "Novo cliente"
    for payload in ({"name": ""}, {"name": "x", "color": "red;display:none"}, {"name": "x", "expected_version": True}):
        with pytest.raises(ValidationError):
            LabelSave(**payload)
    with pytest.raises(ValidationError):
        QuickReplySave(title="x", text="{{cliente.preco_inventado}}")


@pytest.mark.asyncio
async def test_toolbox_has_no_anonymous_access_and_disabled_flag_never_uses_db(monkeypatch):
    from app.catalog_api import Actor, authenticate
    monkeypatch.delenv("INBOX_TOOLS_ENABLED", raising=False)
    monkeypatch.setenv("SUPPORT_API_TOKEN", "synthetic-token-"+"x"*40)
    base = f"/api/v1/tenants/{uuid4()}/management/inbox"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get(base+"/labels")).status_code == 401
        app.dependency_overrides[authenticate] = lambda: Actor("support")
        try:
            assert (await client.get(base+"/labels")).status_code == 423
            assert (await client.get(base+"/quick-replies")).status_code == 423
            capabilities = (await client.get(base+"/capabilities")).json()
            assert capabilities["sends_messages"] is False
            assert capabilities["labels_prove_consent"] is False
        finally:
            app.dependency_overrides.pop(authenticate, None)


@pytest.mark.asyncio
@pytest.mark.parametrize("step", ["human", "confirm", "reschedule_confirm", "menu"])
async def test_persistent_resume_clears_only_old_transient_handoff(step):
    from app.inbox_control import clear_transient_handoff
    from tests.unit.test_qr_ai import FakeRedis
    redis = FakeRedis()
    await redis.set("conversation", json.dumps({"step":step}))
    await redis.set("conversation:commerce", "preserve other domain state")
    await clear_transient_handoff(redis, "conversation")
    assert (await redis.get("conversation") is None) == (step == "human")
    assert (await redis.get("conversation:commerce") is None) == (step == "human")
