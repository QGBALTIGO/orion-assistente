"""Conversation choices are scoped capabilities, not display-text commands."""
import hashlib
import json
import time
from uuid import UUID, uuid4
from dataclasses import replace

import pytest

from app import qr_assistant_interactions as choices
from app import qr_assistant_catalog as catalog
from app.qr_catalog_context import candidates
from test_qr_multi_flow import flow

TENANT = UUID("7f254d34-2cf4-465d-93da-c14e4fe75868")
PHONE = "5521999990001"


class Redis:
    def __init__(self): self.data = {}
    async def get(self, key): return self.data.get(key)
    async def set(self, key, value, **kwargs): self.data[key] = value; return True
    async def delete(self, *keys):
        for key in keys: self.data.pop(key, None)


def services(count):
    return [{"id": UUID(int=index+1), "name": f"Serviço {index+1:03d}", "price_cents": 1000+index,
             "duration_min": 30, "price_mode": "fixed", "category": "Cuidados" if index % 2 else "Cortes"}
            for index in range(count)]


async def confirmation():
    redis = Redis(); key = f"orion:qr:ai:{TENANT}:{PHONE}"
    summary = "📅 *Confira seu agendamento*\nServiço: Corte\nData: 10/12 às 14:00\nTotal: R$ 40,00\nEstá tudo certo?"
    state = {"step": "confirm", "confirmation_summary": summary,
             "confirmation_expires_at": time.time()+240, "confirmation_message_id": "inbound-summary",
             "hold_snapshot": "a"*64, "hold": str(uuid4())}
    await redis.set(key, json.dumps(state))
    await redis.set(key+":catalog-context", "revision-original")
    result = await choices.present(redis, TENANT, PHONE, PHONE, {"answer": summary, "step": "confirm"}, choices.Capabilities(assistant_interactions=1))
    button = result["presentation"]["action"]["buttons"][0]
    record = json.loads(await redis.get(key+":presentation"))
    record["provider_id"] = "accepted-message-1"
    await redis.set(key+":presentation", json.dumps(record))
    return redis, key, result, choices.Selection(id=button["id"], quoted_message_id="accepted-message-1")


async def test_accepts_one_current_delivered_choice_once():
    redis, key, result, selection = await confirmation()
    assert result["presentation"]["action"]["type"] == "reply_buttons"
    assert await choices.resolve(redis, TENANT, PHONE, PHONE, selection) == "sim"
    assert await choices.resolve(redis, TENANT, PHONE, PHONE, selection) is None
    # Resolving the capability only supplies a new inbound decision. It never
    # mutates the reservation or falsely claims the domain operation succeeded.
    assert json.loads(await redis.get(key))["step"] == "confirm"


@pytest.mark.parametrize("step", ["confirm", "reschedule_confirm"])
@pytest.mark.parametrize("button_index, decision", [(0, "sim"), (1, "quero alterar o horário")])
async def test_real_booking_summary_offers_only_confirm_and_change_with_current_one_shot_choices(flow, step, button_index, decision):
    h = flow
    request = ("Quero agendar corte amanhã às 15:00 com Bruno" if step == "confirm"
               else "Quero remarcar amanhã às 15:00")
    proposed = await h.turn(request, preview=False)
    assert proposed["step"] == step
    state_before = h.state(); writes_before = list(h.calls["writes"])
    assert state_before["confirmation_summary"] in proposed["answer"]
    assert not state_before.get("confirmation_requires_review")
    assert [write[0] for write in writes_before] == (["hold"] if step == "confirm" else [])
    assert not any(write[0] in {"confirm", "reschedule", "cancel"} for write in writes_before)
    presented = await choices.present(h.redis, h.tenant, "5567999999999", h.namespace,
        dict(proposed), choices.Capabilities(assistant_interactions=1))
    action = presented["presentation"]["action"]
    assert action["type"] == "reply_buttons"
    assert [button["title"] for button in action["buttons"]] == ["Confirmar", "Alterar horário"]
    assert proposed["answer"] in action["text"] and presented["answer"] == proposed["answer"]
    nonce = presented["presentation"]["id"]
    assert [button["id"] for button in action["buttons"]] == [f"oa1_{nonce}_0", f"oa1_{nonce}_1"]
    record = json.loads(await h.redis.get(h.key+":presentation"))
    assert [record["choices"][choices._digest(button["id"])] for button in action["buttons"]] == [
        {"kind": "text", "body": "sim"}, {"kind": "text", "body": "quero alterar o horário"}]
    assert record["expires_at"] == state_before["confirmation_expires_at"]
    assert h.state() == state_before and h.calls["writes"] == writes_before
    selection = choices.Selection(id=action["buttons"][button_index]["id"], quoted_message_id="accepted-current-summary")
    assert await choices.selected_action(h.redis, h.tenant, "5567999999999", h.namespace, selection) is None
    record["provider_id"] = selection.quoted_message_id
    await h.redis.set(h.key+":presentation", json.dumps(record))
    removed = selection.model_copy(update={"id": f"oa1_{nonce}_2"})
    assert await choices.resolve(h.redis, h.tenant, "5567999999999", h.namespace, removed) is None
    resolved = await choices.resolve(h.redis, h.tenant, "5567999999999", h.namespace, selection)
    assert resolved == decision
    assert await choices.resolve(h.redis, h.tenant, "5567999999999", h.namespace, selection) is None
    assert h.state() == state_before and h.calls["writes"] == writes_before
    reply = await h.turn(resolved, preview=False)
    if button_index == 0:
        assert reply["step"] == ("confirmed" if step == "confirm" else "rescheduled")
        assert [write[0] for write in h.calls["writes"]] == (["hold", "confirm"] if step == "confirm" else ["reschedule"])
    else:
        assert reply["step"] == ("time" if step == "confirm" else "reschedule_day")
        assert h.calls["writes"] == writes_before
        assert not any(h.state().get(field) for field in ("confirmation_summary", "confirmation_message_id", "confirmation_expires_at"))
        if step == "reschedule_confirm":
            assert h.state()["appointment"] == state_before["appointment"]


@pytest.mark.parametrize("index, decision", [(0, "sim"), (1, "não")])
async def test_cancellation_keeps_explicit_cancel_and_keep_time_choices(index, decision):
    redis = Redis(); key = f"orion:qr:ai:{TENANT}:{PHONE}"
    state = {"step": "cancel_confirm", "appointment": str(uuid4()), "expected_snapshot": "original-snapshot",
             "confirmation_summary": "Cancelar o horário de Corte?", "confirmation_expires_at": time.time()+240}
    await redis.set(key, json.dumps(state))
    result = await choices.present(redis, TENANT, PHONE, PHONE,
        {"answer": state["confirmation_summary"], "step": "cancel_confirm"}, choices.Capabilities(assistant_interactions=1))
    buttons = result["presentation"]["action"]["buttons"]
    assert [button["title"] for button in buttons] == ["Cancelar horário", "Manter horário"]
    record = json.loads(await redis.get(key+":presentation")); record["provider_id"] = "accepted-cancellation"
    assert [record["choices"][choices._digest(button["id"])] for button in buttons] == [
        {"kind": "text", "body": "sim"}, {"kind": "text", "body": "não"}]
    await redis.set(key+":presentation", json.dumps(record))
    assert await choices.resolve(redis, TENANT, PHONE, PHONE,
        choices.Selection(id=buttons[index]["id"], quoted_message_id="accepted-cancellation")) == decision
    assert json.loads(await redis.get(key)) == state


@pytest.mark.parametrize("attack", ["forged", "wrong_quote", "no_ack", "expired", "changed_state", "changed_catalog", "other_phone", "other_tenant", "other_namespace"])
async def test_rejects_forged_stale_or_cross_contact_actions(attack):
    redis, key, result, selection = await confirmation()
    tenant, phone, namespace = TENANT, PHONE, PHONE
    record = json.loads(await redis.get(key+":presentation"))
    if attack == "forged": selection = selection.model_copy(update={"id": "oa1_forged"})
    if attack == "wrong_quote": selection = selection.model_copy(update={"quoted_message_id": "another-message"})
    if attack == "no_ack": record["provider_id"] = None
    if attack == "expired": record["expires_at"] = time.time()-1
    if attack == "changed_state":
        state = json.loads(await redis.get(key)); state["step"] = "cancel_confirm"; await redis.set(key, json.dumps(state))
    if attack == "changed_catalog": await redis.set(key+":catalog-context", "new-prices")
    if attack == "other_phone": phone = "5521999990002"
    if attack == "other_tenant": tenant = uuid4()
    if attack == "other_namespace": namespace = "qr:another-channel:"+PHONE
    await redis.set(key+":presentation", json.dumps(record))
    assert await choices.resolve(redis, tenant, phone, namespace, selection) is None


async def test_human_control_revision_change_invalidates_old_button(monkeypatch):
    async def first(*args, **kwargs): return 5
    monkeypatch.setattr(choices, "control_revision", first)
    redis, key, _, selection = await confirmation()
    async def second(*args, **kwargs): return 6
    monkeypatch.setattr(choices, "control_revision", second)
    assert await choices.resolve(redis, TENANT, PHONE, PHONE, selection) is None


@pytest.mark.parametrize("change", ["question", "review_required", "expired", "missing_summary", "too_long"])
async def test_only_complete_fresh_summary_can_offer_confirmation(change):
    redis, key, _, _ = await confirmation()
    state = json.loads(await redis.get(key)); answer = state["confirmation_summary"]
    if change == "question": answer = "O endereço ainda não foi cadastrado."
    if change == "review_required": state["confirmation_requires_review"] = True
    if change == "expired": state["confirmation_expires_at"] = time.time()-1
    if change == "missing_summary": state.pop("confirmation_summary")
    if change == "too_long": answer += " texto"*300
    await redis.set(key, json.dumps(state))
    result = await choices.present(redis, TENANT, PHONE, PHONE, {"answer": answer, "step": "confirm"}, choices.Capabilities(assistant_interactions=1))
    assert "presentation" not in result


async def test_plain_transport_retains_answer_without_exposing_choice_tokens():
    redis, key, _, _ = await confirmation()
    result = await choices.present(redis, TENANT, PHONE, PHONE, {"answer": "Oi! Como posso ajudar?", "step": "greeting"}, None)
    assert result == {"answer": "Oi! Como posso ajudar?", "step": "greeting"}
    assert await redis.get(key+":presentation") is None


async def test_catalog_pages_cover_services_after_original_sixty_item_limit():
    redis = Redis(); key = "test-catalog"; state = {}
    rows = services(150); business = {"name": "Empresa de teste"}
    seen = set()
    for page in range((len(rows)+7)//8):
        request = "lista" if page == 0 else "mais serviços"
        result = await catalog.handle_request(request, state, business, rows, redis, key)
        current = result["_catalog_page"]
        assert current["page"] == page
        assert [row["id"] for row in current["rows"]] == [row["id"] for row in rows[page*8:(page+1)*8]]
        assert choices.message_units(result["answer"]) <= 1200
        seen.update(row["id"] for row in current["rows"])
    assert seen == {row["id"] for row in rows}


async def test_catalog_search_and_category_use_registered_service_data():
    redis = Redis(); state = {}; rows = services(150)
    found = await catalog.handle_request("buscar serviço 147", state, {}, rows, redis, "k")
    assert [row["id"] for row in found["_catalog_page"]["rows"]] == [UUID(int=147)]
    found = await catalog.handle_request("categoria cuidados", state, {}, rows, redis, "k")
    assert all(row["category"] == "Cuidados" for row in found["_catalog_page"]["rows"])


async def test_catalog_browsing_preserves_draft_but_withdraws_old_consent():
    redis = Redis(); state = {"step": "confirm", "services": [str(UUID(int=1))], "day": "2026-12-10"}
    await catalog.handle_request("serviços", state, {}, services(20), redis, "k")
    assert state["step"] == "confirm" and state["day"] == "2026-12-10"
    assert state["confirmation_requires_review"] is True


async def test_greeting_does_not_open_catalog():
    redis = Redis()
    assert await catalog.handle_request("Oi", {}, {}, services(20), redis, "k") is None
    assert redis.data == {}


async def test_number_selects_visible_catalog_page_instead_of_old_menu():
    redis = Redis(); state = {}; rows = services(20)
    await catalog.handle_request("lista", state, {}, rows, redis, "k")
    await catalog.handle_request("mais serviços", state, {}, rows, redis, "k")
    assert await catalog.numeric_choice(redis, "k", "2") == "1"
    selected = json.loads(await redis.get("k"))
    assert selected["menu_ids"] == [str(rows[9]["id"])]
    assert selected["step"] == "menu" and "confirmation_summary" not in selected


async def test_unusual_digit_is_not_parsed_as_an_integer_catalog_option():
    redis = Redis(); state = {}
    await catalog.handle_request("lista", state, {}, services(10), redis, "k")
    assert await catalog.numeric_choice(redis, "k", "①") == "①"


async def test_same_named_professionals_keep_distinct_verified_ids():
    from app.qr_assistant_professionals import professional_menu
    redis = Redis(); key = f"orion:qr:ai:{TENANT}:{PHONE}"
    people = [{"id": str(uuid4()), "name": "João"}, {"id": str(uuid4()), "name": "João"}]
    await redis.set(key, json.dumps({"step": "professional", "professionals": people}))
    result = await choices.present(redis, TENANT, PHONE, PHONE,
        {"answer": "Qual profissional você prefere?\n• João\n• João", "step": "professional", "_professional_page": professional_menu(people)}, choices.Capabilities(assistant_interactions=1))
    selected = result["presentation"]["action"]["sections"][0]["rows"][1]
    record = json.loads(await redis.get(key+":presentation")); record["provider_id"] = "accepted-professionals"
    await redis.set(key+":presentation", json.dumps(record))
    scope = choices.begin_choice()
    try:
        await choices.resolve(redis, TENANT, PHONE, PHONE, choices.Selection(id=selected["id"], quoted_message_id="accepted-professionals"))
        assert choices.consume_choice() == {"kind": "professional", "professional_id": people[1]["id"]}
        assert choices.consume_choice() is None
    finally:
        choices.reset_choice(scope)


async def test_catalog_choice_token_points_to_specific_service_not_label():
    redis = Redis(); key = f"orion:qr:ai:{TENANT}:{PHONE}"
    rows = services(20); state = {}
    result = await catalog.handle_request("lista", state, {}, rows, redis, key)
    presented = await choices.present(redis, TENANT, PHONE, PHONE, result, choices.Capabilities(assistant_interactions=1))
    option = presented["presentation"]["action"]["sections"][0]["rows"][3]
    record = json.loads(await redis.get(key+":presentation")); record["provider_id"] = "accepted-catalog"
    await redis.set(key+":presentation", json.dumps(record))
    selection = choices.Selection(id=option["id"], quoted_message_id="accepted-catalog")
    assert await choices.resolve(redis, TENANT, PHONE, PHONE, selection) == "1"
    assert json.loads(await redis.get(key))["menu_ids"] == [str(rows[3]["id"])]


def test_large_catalog_model_window_keeps_current_selection_and_late_exact_subject():
    rows = services(500)
    result = candidates("Quero o Serviço 499", rows, {"service_ids": [str(rows[399]["id"])]})
    assert len(result) == 60
    assert {rows[399]["id"], rows[498]["id"]} <= {row["id"] for row in result}


@pytest.mark.parametrize("invalid", [True, 0, 2, "1"])
def test_capability_version_requires_exact_strict_integer(invalid):
    with pytest.raises(ValueError): choices.Capabilities(assistant_interactions=invalid)


@pytest.mark.parametrize("field", ["fence", "lease_owner", "owner_account_id", "preserved_session_fingerprint"])
async def test_channel_ownership_or_session_change_revokes_selection(monkeypatch, field):
    from app.qr_channels import ChannelIdentity
    identity = ChannelIdentity(uuid4(), TENANT, uuid4(), "baileys", 3, uuid4(), True, True)
    monkeypatch.delenv("INBOX_CONTROL_ENABLED", raising=False)
    monkeypatch.setattr(choices.qr_channel_scope, "current", lambda: identity)
    redis, key, _, selection = await confirmation()
    value = {"fence": 4, "lease_owner": uuid4(), "owner_account_id": uuid4(), "preserved_session_fingerprint": "changed"}[field]
    changed = replace(identity, **{field: value})
    monkeypatch.setattr(choices.qr_channel_scope, "current", lambda: changed)
    assert await choices.resolve(redis, TENANT, PHONE, PHONE, selection) is None


def test_incoming_selection_requires_negotiation_and_single_original_message():
    from app.qr_ai import Incoming
    data = {"phone": PHONE, "message_id": "inbound-1", "text": "[Escolha recebida]",
            "selection": {"id": "oa1_synthetic", "quoted_message_id": "accepted-1"}}
    with pytest.raises(ValueError): Incoming.model_validate(data)
    data["capabilities"] = {"assistant_interactions": 1}
    assert Incoming.model_validate(data).selection.id == "oa1_synthetic"
    data["message_ids"] = ["inbound-1", "correction-2"]
    with pytest.raises(ValueError): Incoming.model_validate(data)
