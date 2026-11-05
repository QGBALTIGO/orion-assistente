"""Explicit URL buttons reuse owner review and grants; no URLs are fetched."""
from copy import deepcopy
from uuid import uuid4

from fastapi import HTTPException
from pydantic import ValidationError
import pytest

from app import subscription_chatbot_rules as rules
from app import subscription_chatbot_test_engine as simulator
from app import subscription_direct_policy as direct
from app import subscription_direct_schemas as schemas
from app import subscription_direct_store as store
from app import subscription_sent_messages as history
from app.subscription_chatbot_schemas import RuleDefinition, UrlButtonAction, UrlButtonContent, button_destination
from test_subscription_bulk_policy import ACTOR, KEY, NOW, PHONE, TENANT
from test_subscription_bulk_store import CaptureDB, setup_capture
from test_subscription_chatbot_rules import FACTS, CONTEXT, graph, rule, sequence
from test_subscription_sent_messages import effect


def content(**changes):
    return {"text": "Acesse o sistema de {my_name}.", "footer": "Site externo", "button_text": "Acessar meu sistema",
            "url": "https://orionagenda.pro/"} | changes


def message(**changes):
    return {"kind": "url_button", "device_id": str(uuid4()),
        "recipients": {"mode": "phone", "phone": PHONE}, **content(), **changes}


def parse(value):
    return schemas.ReviewDirectMessage(idempotency_key=KEY, message=value).model_dump(mode="json")["message"]


@pytest.mark.parametrize("url", ["https://orionagenda.pro/", "https://example.com/path?x=a%20b&y=1",
    "https://8.8.8.8/", "https://[2606:4700:4700::1111]/", "https://exämple.com/café"])
def test_literal_public_url_is_validated_without_normalizing_or_fetching(url):
    assert button_destination(url) == url
    assert UrlButtonContent(**content(url=url)).url == url


@pytest.mark.parametrize("url", ["http://example.com/", "javascript:alert(1)", "file:///tmp/x", "https://user:pass@example.com/",
    "https://example.com/#fragment", "https://example.com/{name}", "https://{my_name}.example.com/", "https://example.com/%7Bname%7d",
    "https://127.0.0.1/", "https://127.1/", "https://2130706433/", "https://0x7f000001/", "https://0177.0.0.1/",
    "https://10.0.0.1/", "https://100.64.0.1/", "https://192.0.0.9/", "https://192.88.99.1/", "https://198.19.1.1/",
    "https://224.0.0.1/", "https://[::1]/", "https://[::ffff:127.0.0.1]/", "https://[::ffff:8.8.8.8]/",
    "https://[2001:db8::1]/", "https://[2002:808:808::1]/", "https://[3fff::1]/", "https://internal.local/",
    "https://example.test/", "https://example.invalid/", "https://example.onion/", "https://example.com./", "https://a..example.com/",
    "https://-bad.example.com/", "https://example.com:0/", "https://example.com/%0d%0a", "https://example.com/\x7f", "https://example.com\\@127.0.0.1/",
    "https://example.com/\ud800", None])
def test_unsafe_or_dynamic_url_fails_before_a_review_is_created(url):
    with pytest.raises(ValueError):
        button_destination(url)
    with pytest.raises(ValidationError):
        parse(message(url=url))


@pytest.mark.parametrize("field,maximum", [("text", 1024), ("footer", 60), ("button_text", 20)])
def test_limits_are_utf16_units_for_direct_and_chatbot(field, maximum):
    valid = content(**{field: "😀" * (maximum // 2)})
    assert UrlButtonContent(**valid)
    assert UrlButtonAction(**valid)
    for model in (UrlButtonContent, UrlButtonAction):
        with pytest.raises(ValidationError):
            model(**(valid | {field: valid[field] + "a"}))


@pytest.mark.parametrize("button", ["a\nb", "a\rb", "a\tb", "a\x01b", "a\x7fb", "", "  "])
def test_button_label_is_one_nonempty_control_free_line(button):
    with pytest.raises(ValidationError):
        UrlButtonContent(**content(button_text=button))


@pytest.mark.parametrize("extra", [{"title": "unsupported"}, {"merchant_url": "https://example.com/"}, {"method": "GET"},
    {"selection_id": "ocb:invented"}, {"save_template": {"name": "Hidden"}}, {"nativeFlowMessage": {}}])
def test_direct_contract_has_no_hidden_template_or_protocol_injection(extra):
    with pytest.raises(ValidationError):
        parse(message(**extra))


def test_direct_keeps_brace_text_literal_and_builds_one_exact_action_without_ids():
    value = parse(message(text='*Olá* {name} e {qualquer}', footer="Rodapé {literal}"))
    assert direct.url_button_plan(value) == [{"type": "url_button", **content(text=value["text"], footer=value["footer"])}]
    assert direct.expanded_definition(value, KEY) == direct.expanded_definition(value, KEY)
    assert "id" not in direct.url_button_plan(value)[0]
    assert "delay_seconds" not in direct.url_button_plan(value)[0]


def test_chatbot_renders_only_known_tenant_facts_without_url_interpolation_or_mutation():
    action = UrlButtonAction(**content(text="Olá {name}, acesse {my_name}.", footer="{my_email}", button_text="Abrir {name}")).model_dump(mode="json")
    root = rule(response=sequence(action))
    before = deepcopy(root)
    facts = FACTS | {"name": "Ana", "my_name": "Clínica sintética"}
    plan, deps = rules.compile_plan(root["id"], graph(root), {}, facts, CONTEXT)
    assert plan == [{"delay_seconds": 1, "action": {"type": "url_button", "text": "Olá Ana, acesse Clínica sintética.",
        "footer": FACTS["my_email"], "button_text": "Abrir Ana", "url": "https://orionagenda.pro/"}}]
    assert root == before and deps == [root["id"]]


@pytest.mark.parametrize("field,maximum", [("text", 1024), ("footer", 60), ("button_text", 20)])
def test_contact_expansion_is_revalidated_at_each_protocol_limit(field, maximum):
    root = rule(response=sequence(UrlButtonAction(**content(**{field: "{name}"})).model_dump(mode="json")))
    with pytest.raises(ValueError):
        rules.compile_plan(root["id"], graph(root), {}, FACTS | {"name": "😀" * (maximum // 2) + "a"}, CONTEXT)


def test_missing_unknown_or_control_injected_contact_variable_cannot_make_a_button():
    with pytest.raises(ValidationError):
        UrlButtonAction(**content(text="Olá {password}"))
    root = rule(response=sequence(UrlButtonAction(**content(button_text="{name}")).model_dump(mode="json")))
    for value in (None, "", "A\nB"):
        with pytest.raises(ValueError):
            rules.compile_plan(root["id"], graph(root), {}, FACTS | {"name": value}, CONTEXT)


@pytest.mark.asyncio
async def test_review_freezes_link_label_footer_and_keeps_owner_optout_and_self_rules(monkeypatch):
    setup_capture(monkeypatch)
    value = parse(message())
    db = CaptureDB()
    snapshot = await store.capture_review(db, TENANT, ACTOR, value, KEY)
    row, = snapshot["rows"]
    assert row["plan"] == direct.url_button_plan(value)
    assert row["context"]["template"] is None and snapshot["save_template"] is None
    assert all(sql.startswith("SELECT") for sql, _ in db.calls)
    preview = store.review_preview(snapshot, KEY, NOW)
    assert preview["kind"] == preview["items"][0]["message_type"] == "url_button"
    assert preview["items"][0]["message_preview"]["parts"] == row["plan"]
    for field, changed in (("url", "https://orionagenda.pro/ajuda"), ("button_text", "Outro botão"), ("footer", "Outro rodapé")):
        other = await store.capture_review(CaptureDB(), TENANT, ACTOR, value | {field: changed}, KEY)
        assert store.fingerprint(snapshot) != store.fingerprint(other)
    denied = await store.capture_review(CaptureDB([{"phone": PHONE, "version": 5, "revoked": True}]), TENANT, ACTOR, value, KEY)
    assert denied["rows"][0]["blocked_reason"] == "BULK_OPTED_OUT" and denied["counts"]["eligible"] == 0
    setup_capture(monkeypatch, verified=True)
    own = await store.capture_review(CaptureDB(), TENANT, ACTOR, value, KEY)
    assert own["rows"][0]["blocked_reason"] == "BULK_SELF_CONTACT" and own["counts"]["eligible"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["text", "template"])
async def test_existing_blocked_text_and_template_rows_keep_their_original_preview(monkeypatch, kind):
    setup_capture(monkeypatch)
    value = {"kind": kind, "device_id": str(uuid4()), "recipients": {"mode": "phone", "phone": PHONE}}
    if kind == "text":
        value["text"] = "Texto literal {name}"
    else:
        identity = str(uuid4())
        value["template_id"] = identity
        async def snapshot(*args):
            return {"id": identity, "name": "Texto salvo", "kind": "text", "revision": 1,
                "body": "Olá {name}", "followup_body": ""}
        monkeypatch.setattr(store.bulk, "template_snapshot", snapshot)
    saved = await store.capture_review(CaptureDB([{"phone": PHONE, "version": 1, "revoked": True}]),
        TENANT, ACTOR, parse(value), KEY)
    public = store.review_preview(saved, KEY, NOW)
    row, = public["items"]
    assert row["available"] is False and row["error_code"] == "BULK_OPTED_OUT"
    assert row["message_type"] == kind and row["message_preview"]["type"] == "text"
    assert len(row["message_preview"]["parts"]) == 1


@pytest.mark.asyncio
async def test_history_displays_full_cta_and_freezes_resend_without_fetch_or_selection():
    action = {"type": "url_button", **content()}
    row = effect(message_type="url_button", payload={"phone": PHONE, "action": action})
    shown = history.item_dto(row, NOW)
    assert "Site externo" in shown["sent"]["text"] and "https://orionagenda.pro/" in shown["sent"]["text"]
    payload, result, selections = await history.frozen_payload(object(), row, clone_tokens=True)
    assert payload == row["payload"] and result is None and selections == []
    row["payload"]["action"]["url"] = "https://127.1/"
    with pytest.raises(HTTPException):
        await history.frozen_payload(object(), row, clone_tokens=True)


def test_simulator_exposes_declarative_button_without_accessing_link():
    root = rule(response=sequence(UrlButtonAction(**content()).model_dump(mode="json")))
    messages = simulator.compile_messages(root, (graph(root), {}, {}), {"device_id": root["device_id"]}, FACTS, CONTEXT, "/private")
    item, = messages
    assert item["type"] == "url_button" and item["url"] == content()["url"]
    assert "não é acessado" in item["notice"] and "selection_token" not in item
