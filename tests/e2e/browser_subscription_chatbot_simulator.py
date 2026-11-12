"""Stage-03 simulator with shipped Orion assets and a loopback-only fixture.

The HTTP input schema and private JPEG bytes are real. API answers are synthetic,
all external origins are blocked, and no live transport, ASR, job or URL action
is invoked. Viewport emulation does not claim physical keyboard/device coverage.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from io import BytesIO
import json
from pathlib import Path
import re
import threading
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import uuid4

from PIL import Image
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, expect
import pytest

from app.subscription_chatbot_test_schemas import SimulateRequest
from browser_subscription_chatbot import (
    Handler as ChatbotHandler, ROOT, SIZES, RULE_ID, CHATBOT_BASE,
    assert_layout, base, browser, navigation,
)  # noqa: F401 - use the same browser fixture and synthetic full-panel server.

ARTIFACTS = ROOT / "test-artifacts" / "subscription-chatbot-test"
CONTRACT = json.loads((ROOT / "tests/fixtures/subscription_chatbot_test_contract.json").read_text())
SIMULATOR_BASE = base.BASE + "/subscription-whatsapp/chatbot-test"
DEVICE = deepcopy(CONTRACT["bootstrap_response"]["devices"][0])
DEVICE["name"] = "Atendimento sintético"
DEVICE["phone_e164"] = "+5511900000001"
DEVICE_ID = DEVICE["id"]
ASSET_ID = CONTRACT["message_examples"]["media"]["asset"]["id"]
ROUTES = ["dashboard", "dispositivos", "chatbot", "testar-chatbot", "templates", "novidades", "ajuda", "member"]
KEYWORDS = [{"rule_id": RULE_ID, "keyword": "Olá", "match_mode": "equal", "enabled": True},
    {"rule_id": "60000000-0000-4000-8000-000000000002", "keyword": "menu", "match_mode": "equal", "enabled": True},
    {"rule_id": "60000000-0000-4000-8000-000000000003", "keyword": "imagem", "match_mode": "equal", "enabled": True},
    {"rule_id": "60000000-0000-4000-8000-000000000004", "keyword": "inativa", "match_mode": "equal", "enabled": False},
    {"rule_id": "60000000-0000-4000-8000-000000000005", "keyword": "externo", "match_mode": "equal", "enabled": True}]
image_file = BytesIO()
Image.new("RGB", (320, 200), (40, 95, 145)).save(image_file, format="JPEG")
IMAGE_BYTES = image_file.getvalue()


class Handler(ChatbotHandler):
    def send_header(self, keyword, value):
        if keyword.lower() == "content-security-policy":
            # The parent fixture is stricter than the shipped /painel response.
            # Allow only locally-created media Blob URLs, never remote media.
            value = value.replace("img-src 'self' data:", "img-src 'self' data: blob:")
            value += "; media-src 'self' blob:"
        return super().send_header(keyword, value)

    def body(self, value, status=200, mime="application/json", cookie=None):
        if cookie and urlsplit(self.path).path in {"/", "/panel"}:
            with base._lock:
                base._sessions[cookie].update(simulator_calls=[], simulator_revoked=False,
                    simulator_menus={}, simulator_assets={}, simulator_gate=threading.Event())
        try:
            return super().body(value, status, mime, cookie)
        except (BrokenPipeError, ConnectionResetError):
            # Deliberately abandoned reads must not outlive their UI context.
            return None

    def simulator_authorized(self, session):
        return (session and session.get("workspace_profile") == "subscription_services"
                and not session.get("chatbot_readonly") and not session.get("simulator_revoked"))

    def do_GET(self):
        url = urlsplit(self.path)
        path, query, session = url.path, parse_qs(url.query), self.session()
        if session and path == "/__fixture/simulator-report":
            return self.body({"calls": session["simulator_calls"], "unexpected": session["unexpected"],
                              "chatbot_calls": session["chatbot_calls"]})
        if path == SIMULATOR_BASE or path.startswith(SIMULATOR_BASE + "/"):
            if not self.simulator_authorized(session):
                return self.body({"detail": {"code": "WORKSPACE_FEATURE_UNAVAILABLE", "message": "Acesso sintético revogado."}}, 403)
            session["simulator_calls"].append({"method": "GET", "path": path})
            if path == SIMULATOR_BASE:
                data = deepcopy(CONTRACT["bootstrap_response"])
                data.update(chatbot_revision=session["chatbot_revision"], devices=[deepcopy(DEVICE)])
                return self.body(data)
            if path == SIMULATOR_BASE + "/assets/" + ASSET_ID + "/content":
                ticket = query.get("ticket", [None])[0]
                if ticket not in session["simulator_assets"] or query.get("variant") != ["original"]:
                    return self.body({"detail": "Invalid synthetic asset ticket"}, 409)
                return self.body(IMAGE_BYTES, mime="image/jpeg")
            return self.reject(session, "Unknown synthetic simulator read")
        return super().do_GET()

    def simulation_result(self, session, payload):
        value = payload.input
        data = deepcopy(CONTRACT["simulate_response"])
        data.update(request_id=str(payload.request_id), context_id=str(payload.context_id),
            chatbot_revision=session["chatbot_revision"], device=deepcopy(DEVICE), keywords=deepcopy(KEYWORDS))
        key = None
        if value.type == "text":
            incoming = {"type": "text", "text": value.text, "rule_id": None, "row_id": None}
            key = next((item for item in KEYWORDS if item["keyword"].casefold() == value.text.casefold()), None)
        elif value.type == "rule":
            key = next((item for item in KEYWORDS if item["rule_id"] == str(value.rule_id)), None)
            if key is None:
                return None
            incoming = {"type": "rule", "text": key["keyword"], "rule_id": str(value.rule_id), "row_id": None}
        else:
            menu = session["simulator_menus"].get(value.selection_token)
            if not menu or menu != {"context_id": str(payload.context_id), "device_id": str(payload.device_id), "row_id": str(value.row_id)}:
                return None
            key = KEYWORDS[1]
            incoming = {"type": "selection", "text": "Informações", "rule_id": key["rule_id"], "row_id": str(value.row_id)}
        data["input"] = incoming
        if key is None:
            data.update(outcome="no_match", matched_rule=None,
                        messages=deepcopy(CONTRACT["no_match_response"]["messages"]))
        else:
            data["matched_rule"] = {"id": key["rule_id"], **{name: key[name] for name in ("keyword", "enabled", "match_mode")},
                "reason": "forced_rule" if value.type == "rule" else "selection" if value.type == "selection" else "keyword"}
            if value.type == "selection":
                messages = [{"type": "text", "text": "Detalhes escolhidos na simulação.", "delay_seconds": 1}]
            elif key["keyword"] == "menu":
                menu = deepcopy(CONTRACT["message_examples"]["list"])
                token = "synthetic-menu-" + uuid4().hex
                menu.update(selection_token=token, expires_at=(datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat())
                session["simulator_menus"][token] = {"context_id": str(payload.context_id), "device_id": str(payload.device_id),
                                                    "row_id": menu["sections"][0]["rows"][0]["id"]}
                messages = [menu]
            elif key["keyword"] == "imagem":
                output = deepcopy(CONTRACT["message_examples"]["media"])
                ticket = "synthetic-media-" + uuid4().hex
                session["simulator_assets"][ticket] = str(payload.context_id)
                output["asset"].update(size=len(IMAGE_BYTES), width=320, height=200, thumbnail_url=None,
                    thumbnail_mime=None, thumbnail_size=None,
                    content_url=SIMULATOR_BASE + "/assets/" + ASSET_ID + "/content?" + urlencode({"ticket": ticket, "variant": "original"}))
                messages = [output]
            elif key["keyword"] == "externo":
                external = deepcopy(CONTRACT["message_examples"]["external_request"])
                carousel = deepcopy(CONTRACT["message_examples"]["carousel"])
                carousel["cards"] = [deepcopy(carousel["cards"][0]) | {"title": f"Opção sintética {index + 1}"} for index in range(3)]
                messages = [external, carousel]
            else:
                messages = [{"type": "text", "text": "*Olá*! Conteúdo sintético local." if key["enabled"] else "Conteúdo de uma resposta inativa.", "delay_seconds": 1}]
            data["messages"] = messages
        for message in data["messages"]:
            message["id"] = str(uuid4())
        return data

    def mutate(self):
        path, session = urlsplit(self.path).path, self.session()
        if session and path in {"/__fixture/simulator-release", "/__fixture/simulator-revoke"} and self.command == "POST":
            size = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(size)
            if path.endswith("release"):
                session["simulator_gate"].set()
            else:
                session["simulator_revoked"] = True
                session["chatbot_readonly"] = True
            return self.body({"ok": True})
        if path != SIMULATOR_BASE + "/simulate" or self.command != "POST":
            return self.reject(session, "Only the local simulator is allowed in this visual scenario")
        if not self.simulator_authorized(session):
            return self.body({"detail": {"code": "WORKSPACE_FEATURE_UNAVAILABLE", "message": "Acesso sintético revogado."}}, 403)
        if self.headers.get("X-Orion-CSRF") != session["csrf"]:
            return self.reject(session, "Synthetic CSRF is required")
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 8192:
                raise ValueError()
            payload = SimulateRequest.model_validate_json(self.rfile.read(size))
        except ValueError:
            return self.body({"detail": "Invalid synthetic simulator input"}, 422)
        if str(payload.device_id) != DEVICE_ID or payload.expected_chatbot_revision != session["chatbot_revision"]:
            return self.body({"detail": {"code": "CHATBOT_CONFLICT", "message": "Revisão sintética alterada."}}, 409)
        session["simulator_calls"].append({"method": "POST", "path": path, "data": payload.model_dump(mode="json")})
        if payload.input.type == "text" and payload.input.text == "aguardar":
            if not session["simulator_gate"].wait(15):
                return self.body({"detail": "Synthetic delayed response was not released"}, 408)
        data = self.simulation_result(session, payload)
        if data is None:
            return self.body({"detail": {"code": "CHATBOT_TEST_CONTEXT_INVALID", "message": "Contexto sintético alterado."}}, 409)
        return self.body(data)


@pytest.fixture(scope="module")
def fixture_url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def setup_page(browser, fixture_url, size=(1440, 900), theme="light"):
    engine, instance = browser
    width, height = size
    context = instance.new_context(viewport={"width": width, "height": height}, color_scheme=theme,
        locale="pt-BR", has_touch=width == 390, is_mobile=engine == "webkit" and width == 390,
        service_workers="block")
    origin = urlsplit(fixture_url)
    external, errors = [], []

    def local_only(route):
        url = urlsplit(route.request.url)
        # WebKit routes local Blob reads through this guard; Chromium does not.
        # Compare the embedded origin, never allow arbitrary Blob origins.
        if url.scheme == "blob":
            url = urlsplit(url.path)
        if (url.scheme, url.netloc) != (origin.scheme, origin.netloc):
            external.append(route.request.url)
            return route.abort()
        return route.continue_()

    context.route("**/*", local_only)
    context.add_init_script("localStorage.setItem('orion-theme', " + json.dumps(theme) + ");")
    page = context.new_page()
    page.on("pageerror", lambda error: errors.append(str(error)))
    return context, page, external, errors


def report(page, fixture_url):
    value = page.request.get(fixture_url + "/__fixture/simulator-report").json()
    assert not value["unexpected"], value["unexpected"]
    assert all(call["method"] == "GET" for call in value["chatbot_calls"])
    assert all(call["method"] == "GET" or call["path"] == SIMULATOR_BASE + "/simulate" for call in value["calls"])
    return value


def send_text(page, text, count):
    page.locator("[data-sct-input]").fill(text)
    page.locator("[data-sct-input]").press("Enter")
    expect(page.locator("[data-sct-message]")).to_have_count(count)
    expect(page.locator("[data-sct-pending]")).to_be_hidden()


def keyword(page, name):
    return page.locator("[data-sct-action=keyword]").filter(has=page.locator("span", has_text=re.compile("^" + re.escape(name) + "$")))


@pytest.mark.parametrize("size", SIZES, ids=lambda size: f"{size[0]}x{size[1]}")
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_local_conversation_menu_media_fullscreen_reload_and_rule_shortcut(browser, fixture_url, size, theme):
    engine, _ = browser
    width, height = size
    context, page, external, errors = setup_page(browser, fixture_url, size, theme)
    label, evidence = f"{engine}-{width}x{height}-{theme}", []
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    try:
        page.goto(f"{fixture_url}/panel?fixtureRole=owner&fixturePlan=elite&aba=testar-chatbot")
        expect(page.locator("#page-suite")).to_have_attribute("data-os-current-route", "testar-chatbot")
        expect(page.locator("[data-sct-device]")).to_be_enabled()
        assert page.locator("#osNav [data-os-route]").evaluate_all("nodes=>nodes.map(node=>node.dataset.osRoute)") == ROUTES
        expect(page.locator("[data-sct-message]")).to_have_count(0)
        expect(page.locator("[data-sct-count]")).to_have_text("0 mensagens")
        expect(page.locator("[data-sct-input]")).to_be_disabled()
        expect(page.locator("[data-sct-send]")).to_be_disabled()
        page.locator("[data-sct-device]").select_option(DEVICE_ID)
        expect(page.locator("[data-sct-heading]")).to_contain_text(DEVICE["phone_e164"])
        expect(page.locator("[data-sct-input]")).to_be_enabled()
        page.evaluate("document.fonts.ready")
        assert_layout(page, width)
        page.screenshot(path=str(ARTIFACTS / f"{label}-empty.png"), animations="disabled")

        # Artificial assignment exercises the JS guard beyond HTML maxlength.
        page.locator("[data-sct-input]").evaluate("node=>{node.value='x'.repeat(401);node.dispatchEvent(new Event('input',{bubbles:true}));}")
        expect(page.locator("[data-sct-character-count]")).to_have_text("401/400")
        page.locator("[data-sct-input]").press("Enter")
        expect(page.locator("[data-sct-input-error]")).to_contain_text("400")
        assert not [call for call in report(page, fixture_url)["calls"] if call["method"] == "POST"]
        send_text(page, "🙂" * 200, 2)
        expect(page.locator("[data-sct-message][data-direction=in]").last).to_contain_text("Nenhuma resposta correspondente")
        send_text(page, "  oLÁ  ", 4)
        expect(page.locator("[data-sct-message][data-direction=out]").last).to_contain_text("oLÁ")
        expect(page.locator("[data-sct-message][data-direction=in]").last).to_contain_text("*Olá*!")
        keyword(page, "menu").click()
        expect(page.locator("[data-sct-message]")).to_have_count(6)
        menu = page.locator("[data-sct-message][data-type=list]").last
        menu.locator("summary").click()
        expect(menu.locator("[data-sct-row]")).to_be_enabled()
        page.locator("[data-sct-device]").select_option("")
        expect(page.locator("[data-sct-message]")).to_have_count(6)
        expect(page.locator("[data-sct-input]")).to_be_disabled()
        expect(menu.locator("[data-sct-row]")).to_be_disabled()
        page.locator("[data-sct-device]").select_option(DEVICE_ID)
        expect(menu.locator("[data-sct-row]")).to_be_disabled()
        send_text(page, "menu", 8)
        latest = page.locator("[data-sct-message][data-type=list]").last
        latest.locator("summary").click()
        latest.locator("[data-sct-row]").click()
        expect(page.locator("[data-sct-message]")).to_have_count(10)
        expect(page.locator("[data-sct-message][data-direction=in]").last).to_contain_text("Detalhes escolhidos")
        keyword(page, "inativa").click()
        expect(page.locator("[data-sct-message]")).to_have_count(12)
        expect(page.locator("[data-sct-message][data-direction=in]").last).to_contain_text("Resposta inativa")
        keyword(page, "imagem").click()
        expect(page.locator("[data-sct-message]")).to_have_count(14)
        preview = page.locator("[data-sct-media-content] img")
        expect(preview).to_have_attribute("src", re.compile(r"^blob:"))
        preview.scroll_into_view_if_needed()
        expect(preview).to_be_visible()
        try:
            page.wait_for_function("() => {const image=document.querySelector('[data-sct-media-content] img');return image?.complete&&image.naturalWidth===320;}")
        except PlaywrightTimeoutError as error:
            state = preview.evaluate("image=>({src:image.currentSrc,complete:image.complete,width:image.naturalWidth,height:image.naturalHeight})")
            raise AssertionError("Private fixture JPEG did not decode: " + json.dumps({
                "image": state, "external_requests": external, "page_errors": errors,
            }, ensure_ascii=False)) from error
        assert_layout(page, width)
        page.screenshot(path=str(ARTIFACTS / f"{label}-media.png"), animations="disabled")
        keyword(page, "externo").click()
        expect(page.locator("[data-sct-message]")).to_have_count(17)
        expect(page.locator("[data-sct-message][data-type=external_request]")).to_contain_text("não executada")
        expect(page.locator("[data-sct-message][data-type=carousel] img")).to_have_count(0)
        expect(page.locator("[data-sct-count]")).to_have_text("17 mensagens")
        page.locator("[data-sct-action=fullscreen]").click()
        expect(page.locator("[data-sct-action=fullscreen]")).to_have_attribute("aria-pressed", "true")
        expect(page.locator("#osSubscriptionChatbotTest")).to_have_class(re.compile("os-is-fullscreen"))
        assert_layout(page, width)
        page.screenshot(path=str(ARTIFACTS / f"{label}-fullscreen.png"), animations="disabled")
        page.keyboard.press("Escape")
        expect(page.locator("[data-sct-action=fullscreen]")).to_have_attribute("aria-pressed", "false")
        expect(page.locator("[data-sct-message]")).to_have_count(17)
        evidence.append(report(page, fixture_url))
        page.locator("[data-sct-action=clear]").click()
        expect(page.locator("[data-sct-message]")).to_have_count(0)
        expect(page.locator("[data-sct-device]")).to_have_value(DEVICE_ID)
        send_text(page, "Olá", 2)
        page.reload()
        expect(page.locator("[data-sct-device]")).to_have_value(DEVICE_ID)
        expect(page.locator("[data-sct-message]")).to_have_count(0)
        assert not [call for call in report(page, fixture_url)["calls"] if call["method"] == "POST"]

        navigation(page)
        page.locator("#osNav [data-os-route=chatbot]").click()
        expect(page.locator("[data-sc-card]")).to_have_count(2)
        page.locator(f'[data-sc-details="{RULE_ID}"]>summary').click()
        shortcut = page.locator(f'[data-sc-card="{RULE_ID}"] [data-sc-action=simulate]')
        expect(shortcut).to_be_visible()
        expect(page.locator(f'[data-sc-card="{RULE_ID}"] [data-sc-action=test]')).to_be_visible()
        shortcut.click()
        expect(page.locator("#page-suite")).to_have_attribute("data-os-current-route", "testar-chatbot")
        expect(page.locator("[data-sct-message]")).to_have_count(2)
        assert "reply_id" not in parse_qs(urlsplit(page.url).query)
        send_text(page, "não corresponde", 4)
        final = report(page, fixture_url)
        posts = [call["data"]["input"] for call in final["calls"] if call["method"] == "POST"]
        assert posts == [{"type": "rule", "rule_id": RULE_ID}, {"type": "text", "text": "não corresponde"}]
        evidence.append(final)
        assert not external, external
        assert not errors, errors
    finally:
        (ARTIFACTS / f"{label}.json").write_text(json.dumps({"engine": engine, "viewport": size, "theme": theme,
            "evidence": evidence, "page_errors": errors, "external_requests": external,
            "limitations": ["synthetic API", "viewport emulation", "no WhatsApp, ASR or external HTTP"]}, ensure_ascii=False, indent=2), encoding="utf-8")
        context.close()


def test_late_response_clear_revocation_and_standard_niche_cannot_reuse_context(browser, fixture_url):
    context, page, external, errors = setup_page(browser, fixture_url)
    try:
        page.goto(f"{fixture_url}/panel?fixtureRole=owner&fixturePlan=elite&aba=testar-chatbot&device_id={DEVICE_ID}")
        expect(page.locator("[data-sct-input]")).to_be_enabled()
        with page.expect_request(lambda request: request.method == "POST" and request.url.endswith("/simulate")):
            page.locator("[data-sct-input]").fill("aguardar")
            page.locator("[data-sct-input]").press("Enter")
        expect(page.locator("[data-sct-pending]")).to_be_visible()
        page.locator("[data-sct-action=clear]").click()
        expect(page.locator("[data-sct-message]")).to_have_count(0)
        with page.expect_response(lambda response: response.url.endswith("/simulate")) as delayed:
            assert page.request.post(fixture_url + "/__fixture/simulator-release", data={}).ok
        delayed.value.finished()
        send_text(page, "Olá", 2)
        calls = report(page, fixture_url)["calls"]
        posts = [call["data"] for call in calls if call["method"] == "POST"]
        assert len(posts) == 2 and posts[0]["context_id"] != posts[1]["context_id"]
        expect(page.locator("[data-sct-message][data-direction=out]")).to_have_text(re.compile("Olá"))
        assert page.request.post(fixture_url + "/__fixture/simulator-revoke", data={}).ok
        page.locator("[data-sct-input]").fill("Depois da revogação")
        page.locator("[data-sct-input]").press("Enter")
        expect(page.locator("#page-suite")).to_have_attribute("data-os-current-route", "dashboard")
        expect(page.locator("[data-sct-message]")).to_have_count(0)
        expect(page.locator("#osNav [data-os-route=testar-chatbot]")).to_have_count(0)
        report(page, fixture_url)

        page.goto(f"{fixture_url}/panel?fixtureRole=owner&fixturePlan=elite&fixtureProfile=standard&aba=testar-chatbot&device_id={DEVICE_ID}&reply_id={RULE_ID}")
        expect(page.locator("#page-suite")).to_have_attribute("data-os-current-route", "plano")
        expect(page.locator("#osNav [data-os-route=testar-chatbot]")).to_have_count(0)
        expect(page.locator("[data-sct-form]")).to_have_count(0)
        assert not report(page, fixture_url)["calls"]
        assert "reply_id" not in parse_qs(urlsplit(page.url).query)
        assert not errors, errors
        assert not external, external
    finally:
        context.close()
