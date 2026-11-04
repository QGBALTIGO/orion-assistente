"""Local simulation contracts without sockets, providers or durable effects."""
import base64
from contextlib import asynccontextmanager
from copy import deepcopy
import json
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import HTTPException, Response
from fastapi.routing import APIRoute
from pydantic import ValidationError
import pytest

from app import plan_routing
from app import subscription_chatbot_rules as live_rules
from app import subscription_chatbot_store as store
from app import subscription_chatbot_test as api
from app import subscription_chatbot_test_engine as engine
from app import subscription_chatbot_test_schemas as schemas
from app.catalog_api import Actor


def uid(number):
    return UUID(int=number)


TENANT, ACTOR, DEVICE, GENERATION, CONTEXT = (uid(i) for i in range(1, 6))
OWNER = Actor("member", account_id=ACTOR)
DEVICE_ROW = {"id": DEVICE, "name": "Dispositivo sintético", "tenant_id": TENANT,
              "generation": GENERATION, "revision": 3, "status": "disconnected", "runtime": {}}
SCOPE = engine.context_scope(TENANT, ACTOR, DEVICE_ROW, 7, CONTEXT)
FACTS = {"name": "Contato de teste", "phone_number": "Telefone de teste", "my_name": "Empresa sintética",
         "my_email": "synthetic@example.invalid", "my_contact_number": "Contato não informado"}
BASE = api.BASE.format(tenant_id=TENANT)
CONTRACT = json.loads((Path(__file__).parents[1] / "fixtures/subscription_chatbot_test_contract.json").read_text())


@pytest.fixture(autouse=True)
def test_key(monkeypatch):
    monkeypatch.setenv("INTEGRATION_FERNET_KEY", base64.urlsafe_b64encode(b"synthetic-context-key-32-bytes!!!"[:32].ljust(32, b"!")).decode())


def action(kind="text", **values):
    return {"id": str(uuid4()), "type": kind, "delay_seconds": 1, **({"text": "Olá {name}"} if kind == "text" else {}), **values}


def rule(number=10, *, enabled=True, keyword="/start", device=None, response=None, mode="equal"):
    return {"id": uid(number), "origin_id": uid(number), "revision": 1, "creation_order": number,
            "enabled": enabled, "keyword": keyword, "device_id": device, "match_mode": mode,
            "response": response or {"kind": "sequence", "actions": [action()]}}


def list_rule(number=20, *, selected=None, enabled=False):
    return rule(number, enabled=enabled, keyword="menu", response={"kind": "list", "title": "Menu {name}",
        "description": "Escolha", "footer": "Fim", "button_text": "Opções", "sections": [{"id": str(uid(100)),
            "title": "Seção", "rows": [{"id": str(uid(101)), "title": "Informações", "description": "Descrição",
                                       "action": selected or action()}]}]})


def graph(*rows, templates=None):
    return {str(row["id"]): row for row in rows}, templates or {}, {}


def request_input(value=None, **changes):
    return schemas.SimulateRequest.model_validate({"request_id": str(uid(6)), "context_id": str(CONTEXT),
        "device_id": str(DEVICE), "expected_chatbot_revision": 7,
        "input": value or {"type": "text", "text": "/start"}, **changes})


@pytest.mark.parametrize("text,expected", [("x" * 400, "x" * 400), ("😀" * 200, "😀" * 200),
    ("😀" * 199 + "ab", "😀" * 199 + "ab"), ("  /START  ", "/START"), ("a\u0301", "a\u0301")])
def test_one_line_input_accepts_exact_utf16_budget_and_trims_after_validation(text, expected):
    assert schemas.TextInput(type="text", text=text).text == expected


@pytest.mark.parametrize("text", ["", "   ", "x" * 401, "😀" * 201, " " + "x" * 400,
    "😀" * 200 + " ", "a\nb", "a\rb", "a\tb", "a\x00b", "a\x7fb", "\ud800", "\udfff"])
def test_invalid_single_line_or_utf16_input_is_refused(text):
    with pytest.raises(ValidationError):
        schemas.TextInput(type="text", text=text)


@pytest.mark.parametrize("key", ["simulate_request", "rule_request", "selection_request"])
def test_published_fixture_requests_are_the_actual_strict_schema(key):
    assert schemas.SimulateRequest.model_validate(CONTRACT[key])


@pytest.mark.parametrize("field", ["history", "phone", "url", "effects", "settings", "cooldowns"])
def test_request_cannot_inject_history_operational_state_or_effects(field):
    with pytest.raises(ValidationError):
        request_input(**{field: []})


def test_ticket_is_authenticated_context_bound_and_uses_separate_purposes():
    ticket, _ = engine.issue_ticket(SCOPE, "menu", rule_id=str(uid(20)), rule_revision=1)
    decoded = engine.read_ticket(ticket, "menu", expected=SCOPE)
    assert decoded["rule_id"] == str(uid(20))
    with pytest.raises(engine.InvalidContext):
        engine.read_ticket(ticket, "media", expected=SCOPE)
    for key in engine.SCOPE_KEYS:
        changed = SCOPE | {key: SCOPE[key] + 1 if isinstance(SCOPE[key], int) else str(uid(999))}
        with pytest.raises(engine.InvalidContext):
            engine.read_ticket(ticket, "menu", expected=changed)


def test_ticket_tampering_truncation_and_real_menu_tokens_cannot_be_used():
    ticket, _ = engine.issue_ticket(SCOPE, "menu", rule_id=str(uid(20)), rule_revision=1)
    for invalid in (ticket[:-1], ticket + "x", ticket.replace(".", ".x", 1), "ocb:synthetic_whatsapp_token",
                    "SECRET_WITH_NO_SIGNATURE", ".", "a.b.c", "x" * 4097, "é.x"):
        with pytest.raises(engine.InvalidContext) as error:
            engine.read_ticket(invalid, "menu", expected=SCOPE)
        assert "SECRET" not in str(error.value)


def test_expired_ticket_is_refused_without_using_a_real_cooldown(monkeypatch):
    monkeypatch.setattr(engine.time, "time", lambda: 1000)
    ticket, _ = engine.issue_ticket(SCOPE, "menu", rule_id=str(uid(20)), rule_revision=1)
    monkeypatch.setattr(engine.time, "time", lambda: 2799)
    assert engine.read_ticket(ticket, "menu", expected=SCOPE)
    monkeypatch.setattr(engine.time, "time", lambda: 2800)
    with pytest.raises(engine.InvalidContext):
        engine.read_ticket(ticket, "menu", expected=SCOPE)


def test_missing_or_invalid_signing_configuration_never_uses_a_public_default(monkeypatch):
    for value in (None, "short", base64.urlsafe_b64encode(b"tiny").decode()):
        if value is None:
            monkeypatch.delenv("INTEGRATION_FERNET_KEY", raising=False)
        else:
            monkeypatch.setenv("INTEGRATION_FERNET_KEY", value)
        with pytest.raises(engine.ContextUnavailable):
            engine.issue_ticket(SCOPE, "menu")


def test_inactive_matches_and_dependencies_are_copied_without_relaxing_the_live_engine():
    child = rule(10, enabled=False)
    parent = rule(20, enabled=False, response={"kind": "sequence", "actions": [action("nested_reply", rule_id=str(child["id"]))]})
    values = graph(child, parent)
    before = deepcopy(values)
    selected, _, reason, incoming = api._select(request_input({"type": "text", "text": "  /START  "}), values, SCOPE, FACTS)
    assert selected is parent and reason == "keyword" and incoming["text"] == "/START"
    messages = engine.compile_messages(selected, values, SCOPE, FACTS, {}, BASE)
    assert messages[0]["text"] == "Olá Contato de teste" and messages[0]["delay_seconds"] == 2
    assert values == before and not child["enabled"] and not parent["enabled"]
    assert live_rules.candidates(values[0].values(), "/start", DEVICE) == []
    with pytest.raises(ValueError):
        live_rules.compile_plan(parent["id"], values[0], values[1], FACTS, {})


@pytest.mark.parametrize("mode,keyword,message", [("equal", "ação", "  AÇÃO  "),
    ("like", "renovar", "Quero RENOVAR agora"), ("array", "plano, fatura", "Minha FÁTURA")])
def test_simulator_uses_the_published_matching_semantics(mode, keyword, message):
    entry = rule(mode=mode, keyword=keyword, enabled=False)
    selected, _, _, _ = api._select(request_input({"type": "text", "text": message}), graph(entry), SCOPE, FACTS)
    assert selected is entry


@pytest.mark.parametrize("keyword", ["*", "**", "***", "****", "*****"])
def test_every_wildcard_can_be_repeated_without_local_or_real_reservations(keyword):
    entry = rule(keyword=keyword, enabled=False)
    before = deepcopy(entry)
    for _ in range(3):
        selected, _, reason, _ = api._select(request_input({"type": "text", "text": "Outro texto"}), graph(entry), SCOPE, FACTS)
        assert selected is entry and reason == "wildcard"
    assert entry == before


def test_scope_newest_specific_priority_and_forced_first_rule_are_independent():
    old, recent = rule(10), rule(20, enabled=False)
    foreign = rule(30, device=uid(999))
    wildcard = rule(40, keyword="****")
    values = graph(old, recent, foreign, wildcard)
    assert api._select(request_input(), values, SCOPE, FACTS)[0] is recent
    assert api._select(request_input({"type": "rule", "rule_id": str(old["id"])}), values, SCOPE, FACTS)[0] is old
    assert api._select(request_input(), values, SCOPE, FACTS)[0] is recent
    with pytest.raises(HTTPException) as error:
        api._select(request_input({"type": "rule", "rule_id": str(foreign["id"])}), values, SCOPE, FACTS)
    assert error.value.status_code == 404
    assert [item["rule_id"] for item in engine.keyword_catalog(values, DEVICE)] == [str(wildcard["id"]), str(recent["id"]), str(old["id"])]


def test_nested_lists_get_their_own_scoped_ticket_and_only_list_rows_are_selectable():
    listing = list_rule()
    root = rule(10, response={"kind": "sequence", "actions": [action("nested_list", rule_id=str(listing["id"]))]})
    values = graph(root, listing)
    before = deepcopy(values)
    message, = engine.compile_messages(root, values, SCOPE, FACTS, {}, BASE)
    assert message["type"] == "list" and message["title"] == "Menu Contato de teste"
    assert "action" not in message["sections"][0]["rows"][0]
    assert "_test_rule_id" not in json.dumps(message)
    signed = engine.read_ticket(message["selection_token"], "menu", expected=SCOPE)
    assert signed["rule_id"] == str(listing["id"])
    payload = request_input({"type": "selection", "selection_token": message["selection_token"], "row_id": str(uid(101))})
    selected, selected_action, reason, incoming = api._select(payload, values, SCOPE, FACTS)
    assert selected is listing and reason == "selection" and incoming["text"] == "Informações"
    result, = engine.compile_messages(selected, values, SCOPE, FACTS, {}, BASE, selected_action=selected_action)
    assert result["type"] == "text" and result["text"] == "Olá Contato de teste"
    assert values == before
    payload.input.row_id = uid(999)
    with pytest.raises(HTTPException) as error:
        api._select(payload, values, SCOPE, FACTS)
    assert error.value.detail["code"] == "CHATBOT_TEST_CONTEXT_INVALID"


def test_cleared_context_or_changed_list_revision_cannot_reuse_a_selection():
    listing = list_rule()
    values = graph(listing)
    message, = engine.compile_messages(listing, values, SCOPE, FACTS, {}, BASE)
    payload = request_input({"type": "selection", "selection_token": message["selection_token"], "row_id": str(uid(101))})
    for scope in (SCOPE | {"context_id": str(uid(999))}, SCOPE | {"device_revision": 4}, SCOPE | {"chatbot_revision": 8}):
        with pytest.raises(HTTPException) as error:
            api._select(payload, values, scope, FACTS)
        assert error.value.detail["code"] == "CHATBOT_TEST_CONTEXT_INVALID"
    listing["revision"] = 2
    with pytest.raises(HTTPException):
        api._select(payload, values, SCOPE, FACTS)


def test_external_actions_are_declarative_and_never_invent_responses_or_fetchable_images():
    url = "https://example.invalid/synthetic?private=sentinel"
    entry = rule(response={"kind": "sequence", "actions": [action("url", url=url, method="POST", custom_template="{{reply}}", as_file=True),
        action("carousel", body="Escolha", cards=[{"id": str(uid(200)), "title": "Título", "body": "Descrição",
            "button_text": "Botão", "image_url": "https://example.invalid/external-image.jpg"}])]})
    messages = engine.compile_messages(entry, graph(entry), SCOPE, FACTS, {}, BASE)
    assert messages[0]["type"] == "external_request" and messages[0]["executed"] is False
    assert messages[0]["url"] == url and "reply" not in messages[0]
    assert messages[1]["type"] == "carousel" and messages[1]["cards"][0]["image_status"] == "not_requested"
    assert "external-image.jpg" not in json.dumps(messages)


@pytest.mark.parametrize("kind", list(engine.MEDIA_TYPES))
def test_normalized_media_has_private_scoped_paths_and_canonical_mime(kind):
    mime, _ = engine.MEDIA_TYPES[kind]
    descriptor = {"id": uid(300), "kind": kind, "mime": mime, "size": 10, "sha256": "a"*64,
        "width": 32, "height": 24, "duration_ms": None, "file_name": "resposta.txt" if kind == "document" else "arquivo",
        "thumbnail": {"mime": "image/jpeg", "size": 5, "sha256": "b"*64}}
    template = {"id": uid(400), "kind": kind, "body": "" if kind in {"sticker", "ptv"} else "Legenda", "asset": descriptor}
    entry = rule(response={"kind": "sequence", "actions": [action("template", template_id=str(template["id"]))]})
    message, = engine.compile_messages(entry, graph(entry, templates={str(template["id"]): template}), SCOPE, FACTS, {}, BASE)
    assert message["type"] == "media" and message["kind"] == kind
    assert message["asset"]["mime"] == mime
    assert message["asset"]["content_url"].startswith(BASE + "/assets/" + str(uid(300)) + "/content?ticket=")
    assert "variant=thumbnail" in message["asset"]["thumbnail_url"]
    assert "sha256" not in message["asset"]


class Request:
    def __init__(self, value):
        self.headers = {"content-type": "application/json"}
        self.body = json.dumps(value).encode()
        self.reads = 0
    async def stream(self):
        self.reads += 1
        yield self.body


def stub_reads(monkeypatch, values):
    visits = []
    @asynccontextmanager
    async def owner(tenant, actor, **kwargs):
        assert tenant == TENANT and actor is OWNER
        assert not kwargs.get("write") is False
        visits.append("owner")
        yield object()
    async def revision(db, tenant, expected):
        assert expected == 7
        return 7
    async def device(db, tenant, identity, *, actor_id, live):
        assert identity == DEVICE and actor_id == ACTOR and live is False
        return deepcopy(DEVICE_ROW), {"enabled": False, "lease_expires_at": None}
    async def load(db, tenant):
        return values
    async def facts(db, tenant, actor):
        return FACTS
    monkeypatch.setattr(store, "owner_transaction", owner)
    monkeypatch.setattr(store, "check_revision", revision)
    monkeypatch.setattr(store, "check_device", device)
    monkeypatch.setattr(store, "load_graph", load)
    monkeypatch.setattr(api, "_facts", facts)
    return visits


@pytest.mark.asyncio
async def test_public_simulation_reads_only_disconnected_device_and_returns_canonical_text(monkeypatch):
    values = graph(rule(enabled=False))
    before = deepcopy(values)
    visits = stub_reads(monkeypatch, values)
    payload = request_input({"type": "text", "text": "  /START  "})
    request = Request(payload.model_dump(mode="json"))
    response = Response()
    value = await api.simulate(TENANT, request, response, OWNER)
    assert value["input"]["text"] == "/START" and value["outcome"] == "matched"
    assert value["matched_rule"]["enabled"] is False and value["device"]["status"] == "disconnected"
    assert value["request_id"] == str(payload.request_id) and value["context_id"] == str(CONTEXT)
    assert value["messages"][0]["text"] == "Olá Contato de teste"
    assert response.headers["cache-control"] == "no-store" and visits == ["owner", "owner"]
    assert values == before


@pytest.mark.asyncio
async def test_no_match_is_one_visible_reply_and_bad_dependency_is_an_explicit_unavailable_reply(monkeypatch):
    stub_reads(monkeypatch, graph())
    no_match = await api.simulate(TENANT, Request(request_input().model_dump(mode="json")), Response(), OWNER)
    assert no_match["outcome"] == "no_match" and no_match["matched_rule"] is None
    assert len(no_match["messages"]) == 1 and no_match["messages"][0]["text"] == engine.NO_MATCH
    bad = rule(response={"kind": "sequence", "actions": [action("nested_reply", rule_id=str(uid(999)))]})
    stub_reads(monkeypatch, graph(bad))
    unavailable = await api.simulate(TENANT, Request(request_input().model_dump(mode="json")), Response(), OWNER)
    assert unavailable["outcome"] == "unavailable" and unavailable["messages"][0]["type"] == "unavailable"


@pytest.mark.asyncio
async def test_current_owner_is_checked_before_consuming_submitted_text(monkeypatch):
    @asynccontextmanager
    async def denied(*args, **kwargs):
        raise HTTPException(403, "Revoked")
        yield
    monkeypatch.setattr(store, "owner_transaction", denied)
    request = Request(request_input().model_dump(mode="json"))
    with pytest.raises(HTTPException):
        await api.simulate(TENANT, request, Response(), OWNER)
    assert request.reads == 0


@pytest.mark.parametrize("runtime,expected", [({}, None), ({"phone": "5511999999999", "phone_verified": False}, None),
    ({"phone": "5511999999999", "phone_verified": True}, "+5511999999999"),
    ({"phone": "SECRET", "phone_verified": True}, None)])
def test_device_header_exposes_only_a_verified_phone(runtime, expected):
    assert api.device_dto(DEVICE_ROW | {"runtime": runtime})["phone_e164"] == expected


@pytest.mark.parametrize("route", [route for route in api.router.routes if isinstance(route, APIRoute)])
def test_every_simulator_route_has_exact_subscription_management_scope(route):
    relative = route.path.removeprefix(plan_routing.TENANT_PREFIX)
    for method in route.methods:
        assert plan_routing.feature_for_path(relative, method) == "chatbot.manage"
        assert plan_routing.subscription_workspace_route(relative, method, {})


@pytest.mark.parametrize("path", ["/subscription-whatsapp/chatbot-testing", "/subscription-whatsapp/chatbot-test-other",
                                  "/subscription-whatsapp/chatbot-tests"])
def test_similar_unknown_routes_are_not_granted_the_new_scope(path):
    assert plan_routing.feature_for_path(path) is None
    assert not plan_routing.subscription_workspace_route(path, "POST", {})
