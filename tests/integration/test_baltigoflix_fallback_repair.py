"""The scoped maintenance operation must use the same rule lifecycle as Orion."""
import pytest
from sqlalchemy import text

from deploy.baltigoflix_fallback_repair import REPLY, RULE_ID, _apply
from test_subscription_chatbot_postgres import (
    accepted, chat_effects, chatbot_case, definition, device_case, disposable_environment,
    grant, input_event, new_owner_http, onboarding_db, rule_create,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_baltigoflix_fallback_answers_unknown_text_and_preserves_specific_rules(chatbot_case):
    case = chatbot_case
    specific = await rule_create(case, definition("menu", value="Aqui está o menu."))
    async with case.ctx.migration.begin() as db:
        await db.execute(text("UPDATE tenants SET name='BaltigoFlix' WHERE id=:tenant"),
                         {"tenant": case.tenant})
        await db.execute(text("UPDATE subscription_chatbot_state SET revision=41 WHERE tenant_id=:tenant"),
                         {"tenant": case.tenant})
    assert await _apply() == ("applied", {"revision": 42, "previous_revision": 41, "cooldown_minutes": 60})
    assert await _apply() == ("already_applied", {"revision": 42})
    case.chat_revision = 42

    response = await input_event(case, "Minha solicitação não coincide com as opções")
    effects = chat_effects(response)
    assert len(effects) == 1
    permission = await grant(case, effects[0])
    assert permission["action"]["text"] == REPLY
    await accepted(case, effects[0])
    response = await input_event(case, "Ainda é a mesma solicitação")
    assert chat_effects(response) == []
    menu = chat_effects(await input_event(case, "menu"))
    assert len(menu) == 1
    assert (await grant(case, menu[0]))["action"]["text"] == "Aqui está o menu."
    async with case.ctx.migration.connect() as db:
        rows = (await db.execute(text("SELECT id FROM subscription_chatbot_rules WHERE tenant_id=:tenant"),
                                 {"tenant": case.tenant})).scalars().all()
    assert {str(row) for row in rows} == {specific["id"], str(RULE_ID)}
