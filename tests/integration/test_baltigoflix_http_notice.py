"""Only a known HTTP failure may release one neutral client notice."""
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.subscription_chatbot_engine import HTTP_NOTICE_KEY, HTTP_NOTICE_TEXT
from test_subscription_chatbot_postgres import (
    accepted, chat_effects, chatbot_case, definition, device_case, disposable_environment,
    grant, input_event, internal, new_owner_http, onboarding_db, rule_create, scope,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
@pytest.mark.parametrize("code, scoped, expected", [
    ("CHATBOT_HTTP_TIMEOUT", True, True),
    ("CHATBOT_HTTP_DESTINATION_REFUSED", True, True),
    ("CHATBOT_HTTP_TIMEOUT", False, False),
    ("CHATBOT_REVOKED", True, False),
])
async def test_http_notice_is_scoped_and_deduplicated(chatbot_case, monkeypatch, code, scoped, expected):
    case = chatbot_case
    if scoped:
        monkeypatch.setenv("ORION_CHATBOT_HTTP_NOTICE_TENANT", str(case.tenant))
    await rule_create(case, definition(response={"kind": "sequence", "actions": [
        {"id": str(uuid4()), "type": "url", "url": "https://example.com/synthetic",
         "method": "POST", "custom_template": "{{reply}}", "as_file": False, "delay_seconds": 1},
        {"id": str(uuid4()), "type": "text", "text": "Não deve sair após a falha.", "delay_seconds": 1},
    ]}))
    effect, = chat_effects(await input_event(case))
    assert (await grant(case, effect))["authorized"]
    state = "not_sent" if code == "CHATBOT_HTTP_DESTINATION_REFUSED" else "uncertain"
    body = {**scope(case), "operation_id": effect["operation_id"], "chatbot_revision": case.chat_revision,
            "state": state, "error_code": code}
    result = await internal(case, "/effects/finish", body)
    assert result.status_code == 200, result.text
    notices = chat_effects(result.json()) if "effects" in result.json() else []
    assert len(notices) == int(expected)
    duplicate = await internal(case, "/effects/finish", {**body, "reconcile": True})
    assert duplicate.status_code == 200, duplicate.text
    if expected:
        notice, = notices
        permit = await grant(case, notice)
        assert permit["payload"]["action"] == {"type": "text", "text": HTTP_NOTICE_TEXT}
        assert permit["payload"]["phone"] == "5567999999901"
        await accepted(case, notice)
    async with case.ctx.migration.connect() as db:
        rows = (await db.execute(text("""SELECT effect_key,status,error_code
            FROM subscription_device_effects WHERE tenant_id=:t AND type='chatbot_action'"""),
            {"t": case.tenant})).mappings().all()
    assert sum(row["effect_key"] == HTTP_NOTICE_KEY for row in rows) == int(expected)
    assert any(row["status"] == state and row["error_code"] == code for row in rows)
    if expected:
        assert any(row["status"] == "not_sent" and row["error_code"] == "chatbot_predecessor_failed" for row in rows)
