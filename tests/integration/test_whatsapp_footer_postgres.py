"""New resend display policy over real owner auth, PostgreSQL and RLS.

Legacy-shaped frozen rows are synthetic fixtures. No original attempt is
rewritten by the runtime, and no provider or WhatsApp execution is available.
"""
from copy import deepcopy
import json
from uuid import uuid4

import pytest
from sqlalchemy import text

from test_subscription_sent_messages_postgres import (  # noqa: F401
    messages_case, chatbot_case, device_case, disposable_environment, onboarding_db, new_owner_http,
    chat_effects, definition, rule_create, input_event, message, finish_message,
    effect_row, detail, resend, no_execution,
)

pytestmark = pytest.mark.integration


async def legacy_attempt(c, kind, body, footer, outcome="not_sent"):
    if kind == "list":
        menu = {"kind": "list", "title": "Menu sintético", "description": "Consulta",
            "footer": "Rodapé", "button_text": "Abrir", "sections": [{"id": str(uuid4()),
            "title": "Opções", "rows": [{"id": str(uuid4()), "title": "Ajuda", "description": "",
            "action": {"id": str(uuid4()), "type": "text", "text": "Ação original", "delay_seconds": 1}}]}]}
        await rule_create(c, definition(keyword="footer-menu", response=menu))
        item, = chat_effects(await input_event(c, "footer-menu"))
        if outcome != "pending": await finish_message(c, item, outcome)
    else:
        item = await message(c, outcome)
    source = await effect_row(c, item["operation_id"])
    payload = deepcopy(source["payload"])
    if kind == "list":
        payload["action"].update(description=body, footer=footer)
    else:
        payload["action"] = {"type": "url_button", "text": body, "footer": footer,
            "button_text": "Abrir suporte", "url": "https://example.com/support"}
    # Model a pre-release frozen payload, retaining the actual admitted source,
    # status, ownership and (for a list) real SQL selection bindings.
    async with c.ctx.migration.begin() as db:
        await db.execute(text("UPDATE subscription_device_effects SET payload=CAST(:payload AS jsonb) WHERE tenant_id=:t AND id=:id"),
            {"t": c.tenant, "id": source["id"], "payload": json.dumps(payload)})
    return await effect_row(c, source["id"])


@pytest.mark.parametrize("kind", ["url_button", "list"])
@pytest.mark.parametrize("body", ["Texto literal {name}", "😀"*480], ids=["literal", "unicode"])
async def test_new_resend_composes_footer_once_and_replay_keeps_same_frozen_attempt(messages_case, monkeypatch, kind, body):
    c = messages_case
    source = await legacy_attempt(c, kind, body, "Rodapé sintético")
    original = deepcopy(source)
    version = (await detail(c, source["id"]))["version"]
    key = uuid4()
    async with no_execution(c, monkeypatch):
        created = await resend(c, source["id"], key=key, version=version)
        assert created.status_code == 202, created.text
        replay = await resend(c, source["id"], key=key, version=version)
        assert replay.status_code == 202 and replay.json()["effect_id"] == created.json()["effect_id"], replay.text
    assert await effect_row(c, source["id"]) == original
    fresh = await effect_row(c, created.json()["effect_id"])
    field = "description" if kind == "list" else "text"
    assert fresh["payload"]["action"][field] == body + "\n\nRodapé sintético"
    assert fresh["payload"]["action"]["footer"] == ""
    assert fresh["payload"]["phone"] == original["payload"]["phone"]
    if kind == "url_button":
        assert fresh["payload"]["action"]["url"] == original["payload"]["action"]["url"]
        assert fresh["payload"]["action"]["button_text"] == original["payload"]["action"]["button_text"]
    else:
        old = original["payload"]["action"]["sections"][0]["rows"][0]
        new = fresh["payload"]["action"]["sections"][0]["rows"][0]
        assert new["id"] != old["id"] and new["title"] == old["title"]
        async with c.ctx.migration.connect() as db:
            bindings = (await db.execute(text("SELECT action FROM subscription_chatbot_selections WHERE tenant_id=:t AND outgoing_effect_id IN (:old,:new)"),
                {"t": c.tenant, "old": source["id"], "new": fresh["id"]})).scalars().all()
            assert len(bindings) == 2 and bindings[0] == bindings[1]


@pytest.mark.parametrize("kind,maximum", [("list", 1000), ("url_button", 1024)])
async def test_resend_overflow_fails_before_admitting_any_effect_or_mutating_original(messages_case, monkeypatch, kind, maximum):
    c = messages_case
    source = await legacy_attempt(c, kind, "A"*maximum, "Rodapé")
    async with no_execution(c, monkeypatch, readonly=True):
        denied = await resend(c, source["id"])
        assert denied.status_code == 409 and denied.json()["detail"]["code"] == "MESSAGE_FOOTER_TOO_LONG", denied.text
        assert "rodapé" in denied.json()["detail"]["message"]
    assert await effect_row(c, source["id"]) == source


@pytest.mark.parametrize("outcome", ["pending", "accepted", "uncertain"])
async def test_old_pending_accepted_and_uncertain_footer_snapshots_are_never_rewritten_or_replayed(messages_case, monkeypatch, outcome):
    c = messages_case
    source = await legacy_attempt(c, "url_button", "Texto congelado", "Rodapé nativo antigo", outcome)
    async with no_execution(c, monkeypatch, readonly=True):
        denied = await resend(c, source["id"])
        assert denied.status_code == 409, denied.text
    assert await effect_row(c, source["id"]) == source
