"""Stage-06 diagnostics using shipped assets and a loopback synthetic server.

Selection and refresh never admit effects. Preparation records only an intent;
explicit confirmation records one synthetic effect. No real proxy or WhatsApp
connection is opened, and no message or external request can leave this server.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from http.server import ThreadingHTTPServer
import json
import re
import threading
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

from playwright.sync_api import expect
import pytest

from browser_subscription_chatbot import (
    Handler as ChatbotHandler, ROOT, SIZES, assert_layout, base, browser, navigation, now,
)  # noqa: F401 - use the official Chrome fixture and prior browser matrix.
from browser_subscription_chatbot_simulator import setup_page
from scripts.plans_preview import Handler as PlansHandler

CONTRACT = json.loads((ROOT / "tests/fixtures/subscription_diagnostics_contract.json").read_text())
DIAGNOSTICS_BASE = base.BASE + "/subscription-whatsapp/diagnostics"
DEVICE_ID = CONTRACT["detail_response"]["device"]["id"]
DEVICE_PATH = DIAGNOSTICS_BASE + "/" + DEVICE_ID
SELF_PHONE = CONTRACT["detail_response"]["test_message"]["default_recipient_e164"]
ARTIFACTS = ROOT / "test-artifacts" / "subscription-diagnostics"


def stamp(value):
    return value.isoformat().replace("+00:00", "Z")


def current_time():
    return datetime.now(timezone.utc)


class Handler(ChatbotHandler):
    """All apparent writes are scoped to the synthetic browser session."""

    def body(self, value, status=200, mime="application/json", cookie=None):
        path = urlsplit(self.path).path
        if cookie and path in {"/", "/panel"}:
            query = parse_qs(urlsplit(self.path).query)
            with base._lock:
                session = base._sessions[cookie]
                session.update(dg_calls=[], dg_operations={}, dg_effects=[], dg_proxy_calls=[],
                    dg_next=None, dg_revoked=False, dg_empty=query.get("fixtureEmpty") == ["1"],
                    dg_detail=deepcopy(CONTRACT["detail_response"]))
        session = self.session()
        if session and path.endswith("/management/permissions") and isinstance(value, dict):
            plan = deepcopy(value["effective_plan"])
            profile = session["workspace_profile"]
            plan["workspace_profile"] = profile
            if profile == "subscription_services":
                features = ["account.manage", "business.setup", "devices.manage"]
                if not session["chatbot_readonly"]:
                    features.extend(["chatbot.manage", "templates.manage", "sent_messages.manage"])
                    if not session["dg_revoked"]:
                        features.append("diagnostics.manage")
                plan["features"] = features
                plan["permissions"] = {name: name in features for name in
                    {*plan["permissions"], *features, "diagnostics.manage", "sent_messages.manage",
                     "templates.manage", "chatbot.manage", "devices.manage"}}
            else:
                plan["permissions"].update({name: False for name in
                    ("devices.manage", "chatbot.manage", "templates.manage", "sent_messages.manage", "diagnostics.manage")})
            plan["revision"] = sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
            return PlansHandler.body(self, {"effective_plan": plan}, status, mime, cookie)
        try:
            return super().body(value, status, mime, cookie)
        except (BrokenPipeError, ConnectionResetError):
            return None

    def allowed(self, session):
        return (session and session["workspace_profile"] == "subscription_services"
                and not session["chatbot_readonly"] and not session["dg_revoked"])

    def error(self, code, status=409, *, rejection=None):
        detail = {"code": code, "message": {
            "DIAGNOSTIC_NOT_FOUND": "Diagnóstico não encontrado.",
            "DIAGNOSTIC_CONTEXT_CHANGED": "O dispositivo mudou. Atualize antes de continuar.",
            "WORKSPACE_FEATURE_UNAVAILABLE": "O acesso aos diagnósticos foi revogado.",
        }.get(code, "Não foi possível concluir a consulta sintética.")}
        if rejection is not None:
            detail["rejection"] = rejection
        return self.body({"detail": detail}, status)

    def do_GET(self):
        url, session = urlsplit(self.path), self.session()
        path = url.path
        if session and path == "/__fixture/diagnostics-report":
            return self.body({"calls": session["dg_calls"], "operations": list(session["dg_operations"].values()),
                "effects": session["dg_effects"], "proxy_calls": session["dg_proxy_calls"],
                "unexpected": session["unexpected"]})
        if not path.startswith(DIAGNOSTICS_BASE):
            return super().do_GET()
        if session:
            session["dg_calls"].append({"method": "GET", "path": path, "query": parse_qs(url.query)})
        if not self.allowed(session):
            return self.error("WORKSPACE_FEATURE_UNAVAILABLE", 403)
        suffix = path[len(DIAGNOSTICS_BASE):]
        if not suffix:
            result = deepcopy(CONTRACT["list_response"])
            result.update(devices=[] if session["dg_empty"] else [deepcopy(session["dg_detail"]["device"])],
                          generated_at=now(), selected_device_id=None)
            return self.body(result)
        if suffix.startswith("/requests/"):
            saved = session["dg_operations"].get(suffix.rsplit("/", 1)[-1])
            return self.body({**deepcopy(saved["result"]), "replayed": True}) if saved else self.error("DIAGNOSTIC_NOT_FOUND", 404)
        if suffix == "/" + DEVICE_ID and not session["dg_empty"]:
            result = deepcopy(session["dg_detail"])
            result["generated_at"] = now()
            if result["connection"]["freshness"] == "current":
                result["connection"]["observed_at"] = now()
            if result["server"]["state"] == "available":
                result["server"]["observed_at"] = now()
            return self.body(result)
        return self.error("DIAGNOSTIC_NOT_FOUND", 404)

    def mutate(self):
        path, session = urlsplit(self.path).path, self.session()
        if session and path == "/__fixture/diagnostics-control" and self.command == "POST":
            data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            mode = data["mode"]
            if mode in {"lost_prepare", "lost_confirm", "lost_proxy", "missing_prepare", "missing_proxy",
                        "conflict", "admitted_conflict", "marked_confirm_conflict"}:
                session["dg_next"] = mode
            elif mode == "bump_revision":
                revision = sha256(session["dg_detail"]["device"]["version"].encode()).hexdigest()
                session["dg_detail"]["device"]["version"] = revision
                session["dg_detail"]["proxy"]["configuration_version"] = sha256(("proxy:" + revision).encode()).hexdigest()
            elif mode == "revoke":
                session["dg_revoked"] = True
            elif mode == "disconnected":
                session["dg_detail"]["connection"].update(state="disconnected", label="Desconectado",
                    source="bridge", freshness="current", phone_e164=None)
                session["dg_detail"]["test_message"].update(can_prepare=False, default_recipient_e164=None,
                    blocked_reason="Conecte o dispositivo antes de preparar uma mensagem.")
            elif mode == "unknown":
                session["dg_detail"]["server"].update(state="unavailable", latency_ms=None, observed_at=now())
                session["dg_detail"]["connection"].update(state="unknown", label="Não informado",
                    source="stored", freshness="unknown", observed_at=None, phone_e164=None)
                session["dg_detail"]["test_message"].update(can_prepare=False, default_recipient_e164=None,
                    blocked_reason="Não foi possível confirmar a conexão do dispositivo.")
            elif mode == "connected":
                session["dg_detail"] = deepcopy(CONTRACT["detail_response"])
            elif mode == "accept_message":
                operation = next((row["result"] for row in reversed(list(session["dg_operations"].values()))
                    if row["result"]["kind"] == "message_test" and row["result"]["message"] is not None), None)
                if operation is None:
                    return self.reject(session, "No admitted synthetic message to acknowledge")
                operation.update(status="succeeded", finished_at=now())
                operation["message"].update(status="sent", outcome="accepted", error_code=None, reason=None)
            else:
                return self.reject(session, "Unknown synthetic diagnostics control")
            return self.body({"ok": True})
        if not path.startswith(DIAGNOSTICS_BASE):
            return super().mutate()
        if not self.allowed(session):
            return self.error("WORKSPACE_FEATURE_UNAVAILABLE", 403)
        if self.command != "POST" or not path.startswith(DEVICE_PATH + "/"):
            return self.reject(session, "Only explicit scoped diagnostics POSTs are allowed")
        if self.headers.get("X-Orion-CSRF") != session["csrf"]:
            return self.reject(session, "Synthetic CSRF required")
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 4096:
                raise ValueError()
            data = json.loads(self.rfile.read(size))
            key = str(UUID(data["idempotency_key"]))
            kind = "proxy_test" if path.endswith("/proxy-tests") else "message_test"
            prepare = path.endswith("/test-messages/prepare")
            confirm = bool(re.fullmatch(re.escape(DEVICE_PATH) + r"/test-messages/[0-9a-f-]{36}/confirm", path))
            allowed = ({"idempotency_key", "expected_version", "phone"} if prepare else
                       {"idempotency_key", "confirmation_version", "confirmation"} if confirm else
                       {"idempotency_key", "expected_version"} if kind == "proxy_test" else set())
            if not allowed or set(data) != allowed:
                raise ValueError()
            if not re.fullmatch("[0-9a-f]{64}", data["confirmation_version" if confirm else "expected_version"]):
                raise ValueError()
            if prepare and data["phone"] is not None:
                if (not isinstance(data["phone"], str) or len(data["phone"]) > 40
                        or not re.fullmatch(r"[+0-9 ()-]+", data["phone"])
                        or not re.fullmatch(r"\+?[1-9][0-9]{7,14}", re.sub(r"[ ()-]", "", data["phone"]))):
                    raise ValueError()
            if confirm and data["confirmation"] != "ENVIAR":
                raise ValueError()
        except (KeyError, TypeError, ValueError):
            return self.body({"detail": "Invalid synthetic diagnostic request"}, 422)
        session["dg_calls"].append({"method": "POST", "path": path, "data": deepcopy(data)})
        saved = session["dg_operations"].get(key)
        if confirm:
            if saved is None or saved["result"]["kind"] != "message_test" or saved["result"]["request_id"] != path.rsplit("/", 2)[-2]:
                return self.error("DIAGNOSTIC_NOT_FOUND", 404)
            result = saved["result"]
            if data["confirmation_version"] != result["preview"]["confirmation_version"]:
                return self.error("DIAGNOSTIC_CONFIRMATION_INVALID")
            if result["message"] is not None:
                return self.body({**deepcopy(result), "replayed": True}, 202)
            if session["dg_next"] == "conflict" or saved["data"]["expected_version"] != session["dg_detail"]["device"]["version"]:
                session["dg_next"] = None
                return self.error("DIAGNOSTIC_CONTEXT_CHANGED")
            if datetime.fromisoformat(result["preview"]["expires_at"].replace("Z", "+00:00")) <= current_time():
                return self.error("DIAGNOSTIC_PREVIEW_EXPIRED", 410)
            effect = {"id": str(uuid4()), "request_id": result["request_id"],
                "phone": result["preview"]["recipient"]["phone_e164"], "text": result["preview"]["text"]}
            session["dg_effects"].append(effect)
            result.update(status="pending", expires_at=stamp(current_time() + timedelta(seconds=120)),
                message={"effect_id": effect["id"], "status": "pending", "outcome": "pending", "error_code": None, "reason": None})
            if session["dg_next"] == "marked_confirm_conflict":
                session["dg_next"] = None
                # A marker on this endpoint must be ignored by both the real
                # panel wrapper and controller, even when its identity matches.
                return self.error("DIAGNOSTIC_CONTEXT_CHANGED", rejection={
                    "reason": "stale_device_version", "idempotency_key": key, "device_id": DEVICE_ID,
                    "kind": "message_test", "expected_version": saved["data"]["expected_version"]})
            if session["dg_next"] == "lost_confirm":
                session["dg_next"] = None
                return self.error("SYNTHETIC_REPLY_LOST", 503)
            return self.body(deepcopy(result), 202)
        if saved is not None:
            if saved["path"] != path or saved["data"] != data:
                return self.error("DIAGNOSTIC_KEY_CONFLICT")
            return self.body({**deepcopy(saved["result"]), "replayed": True}, 201 if prepare else 202)
        if data["expected_version"] != session["dg_detail"]["device"]["version"]:
            # Exactly the backend's fenced rejection: no existing key, before
            # creating an intent or dispatching a synthetic proxy operation.
            return self.error("DIAGNOSTIC_CONTEXT_CHANGED", rejection={
                "reason": "stale_device_version", "idempotency_key": key, "device_id": DEVICE_ID,
                "kind": kind, "expected_version": data["expected_version"]})
        if session["dg_next"] == ("missing_prepare" if prepare else "missing_proxy"):
            session["dg_next"] = None
            return self.error("SYNTHETIC_REPLY_LOST", 503)
        at = current_time()
        if prepare:
            if not session["dg_detail"]["test_message"]["can_prepare"]:
                return self.error("DIAGNOSTIC_DEVICE_UNAVAILABLE")
            result = deepcopy(CONTRACT["prepare_response"])
            result.update(request_id=str(uuid4()), idempotency_key=key, created_at=stamp(at),
                          expires_at=stamp(at + timedelta(seconds=120)))
            result["preview"].update(confirmation_version=sha256((key + result["request_id"]).encode()).hexdigest(),
                                     expires_at=result["expires_at"])
            own = session["dg_detail"]["test_message"]["default_recipient_e164"]
            recipient = "+" + re.sub(r"[ ()-]", "", data["phone"]).lstrip("+") if data["phone"] else own
            result["preview"]["recipient"] = {"mode": "self" if recipient == own else "explicit", "phone_e164": recipient}
        else:
            result = deepcopy(CONTRACT["proxy_response"])
            result.update(request_id=str(uuid4()), idempotency_key=key, created_at=stamp(at),
                          expires_at=stamp(at + timedelta(seconds=60)), finished_at=stamp(at))
            result["proxy"].update(observed_at=stamp(at))
            session["dg_proxy_calls"].append({"key": key, "device_id": DEVICE_ID,
                "configuration_version": result["proxy"]["configuration_version"]})
            session["dg_detail"]["proxy"]["last_test"] = {**deepcopy(result["proxy"]),
                "request_id": result["request_id"], "freshness": "current"}
        session["dg_operations"][key] = {"path": path, "data": deepcopy(data), "result": result}
        if session["dg_next"] == "admitted_conflict":
            session["dg_next"] = None
            # scope can fail after proxy dispatch; this generic 409 carries no
            # proof that the operation was absent and must retain recovery.
            return self.error("DIAGNOSTIC_CONTEXT_CHANGED")
        if session["dg_next"] == ("lost_prepare" if prepare else "lost_proxy"):
            session["dg_next"] = None
            return self.error("SYNTHETIC_REPLY_LOST", 503)
        return self.body(deepcopy(result), 201 if prepare else 202)


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


def action(page, name):
    return page.locator('[data-dg-action="' + name + '"]')


def report(page, fixture_url):
    data = page.request.get(fixture_url + "/__fixture/diagnostics-report").json()
    assert not data["unexpected"], data["unexpected"]
    assert all(call["method"] == "GET" or call["path"].startswith(DEVICE_PATH + "/") for call in data["calls"])
    return data


def control(page, fixture_url, mode):
    response = page.request.post(fixture_url + "/__fixture/diagnostics-control", data={"mode": mode})
    assert response.ok, response.text()


def writes(data):
    return [call for call in data["calls"] if call["method"] == "POST"]


def selected(page):
    page.locator("[data-dg-field=device]").select_option(DEVICE_ID)
    expect(page.locator("[data-dg-section=connection]")).to_be_visible()


def assert_dialog_layout(page, width, height):
    assert_layout(page, width)
    metrics = page.locator("[data-dg-dialog]").evaluate("""node => {
        const box = element => {
            const r = element.getBoundingClientRect();
            return {top:r.top,bottom:r.bottom,left:r.left,right:r.right,height:r.height};
        };
        const body = node.querySelector('.os-dg-dialog-body'), dialog = box(node), rect = box(body);
        const header = box(node.querySelector('.os-dg-dialog-header')), footer = box(node.querySelector('.os-dg-dialog-footer'));
        const visible = {
            top:Math.max(rect.top + body.clientTop, header.bottom, dialog.top, 0),
            bottom:Math.min(rect.top + body.clientTop + body.clientHeight, footer.top, dialog.bottom, innerHeight),
            left:Math.max(rect.left + body.clientLeft, dialog.left, 0),
            right:Math.min(rect.left + body.clientLeft + body.clientWidth, dialog.right, innerWidth)
        };
        return {dialog,body:rect,header,footer,visible,scrollTop:body.scrollTop,scrollHeight:body.scrollHeight,
            recipient:box(node.querySelector('[data-dg-preview-recipient]')),
            preview:box(node.querySelector('.os-dg-preview-text'))};
    }""")
    dialog, visible = metrics["dialog"], metrics["visible"]
    assert dialog["top"] >= -1 and dialog["bottom"] <= height + 1, metrics
    # All fixture viewports have room for this bounded preview. WebKit must
    # not collapse a flex body into a tiny scroll strip while the dialog fits.
    assert visible["bottom"] - visible["top"] >= 200, metrics
    for name in ("recipient", "preview"):
        target = metrics[name]
        assert target["height"] > 0, (name, metrics)
        assert visible["top"] - 1 <= target["top"] and target["bottom"] <= visible["bottom"] + 1, (name, metrics)
        assert visible["left"] - 1 <= target["left"] and target["right"] <= visible["right"] + 1, (name, metrics)


def operation_action(page, name):
    return page.locator('[data-dg-operation] [data-dg-action="' + name + '"]')


def open_diagnostics(page, fixture_url):
    page.goto(fixture_url + "/panel?fixtureRole=owner&fixturePlan=elite&aba=diagnosticos")
    expect(page.locator("#page-suite")).to_have_attribute("data-os-current-route", "diagnosticos")
    expect(page.locator("[data-dg-field=device]")).to_be_enabled()


def reload_same_session(page):
    # Remove only reset parameters of the synthetic server. Preserve the
    # application's bounded history.state so the real bootstrap must recover.
    page.evaluate("history.replaceState(history.state, '', '/panel?aba=diagnosticos')")
    page.reload()
    expect(page.locator("[data-dg-operation]")).to_be_visible()
    expect(operation_action(page, "recover")).to_be_enabled()


def assert_private_recovery(page, key, phone=None):
    recovery = page.evaluate("history.state.orionDiagnosticRecovery")
    assert recovery["idempotency_key"] == key
    assert set(recovery) == {"tenant_id", "account_scope", "idempotency_key", "device_id", "kind", "expected_version"}
    assert re.fullmatch("[0-9a-f]{64}", recovery["account_scope"])
    encoded = json.dumps(recovery)
    for value in (SELF_PHONE, phone, CONTRACT["detail_response"]["test_message"]["text"], "synthetic@example.invalid"):
        if value:
            assert value not in encoded


@pytest.mark.parametrize("size", SIZES, ids=lambda size: f"{size[0]}x{size[1]}")
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_read_only_selection_status_proxy_and_confirmed_message(browser, fixture_url, size, theme):
    context, page, external, errors = setup_page(browser, fixture_url, size, theme)
    try:
        open_diagnostics(page, fixture_url)
        expect(page.locator("[data-dg-field=device]")).to_have_value("")
        expect(page.locator("[data-dg-row]")).to_have_count(0)
        initial = report(page, fixture_url)
        assert not writes(initial) and not initial["operations"] and not initial["effects"]
        assert not any(call["path"] == DEVICE_PATH for call in initial["calls"])
        navigation(page)
        expect(page.locator('#osNav [data-os-route="diagnosticos"]')).to_be_visible()
        menu = page.locator("#osMenu")
        if menu.is_visible() and menu.get_attribute("aria-expanded") == "true":
            page.locator("#osCollapse").click()
            expect(menu).to_have_attribute("aria-expanded", "false")
            expect(page.locator("#osSidebar")).not_to_be_visible()
        selected(page)
        expect(page.locator("[data-dg-row]")).to_have_count(3)
        expect(page.locator("[data-dg-section=server]")).to_contain_text("Disponível")
        expect(page.locator("[data-dg-section=server]")).to_contain_text("17 ms")
        expect(page.locator("[data-dg-section=connection]")).to_contain_text("Conectado")
        expect(page.locator("[data-dg-section=recent]")).to_contain_text("Recebimento não confirmado")
        assert page.locator("[data-dg-section=recent] img, [data-dg-section=recent] script").count() == 0
        assert not writes(report(page, fixture_url))
        action(page, "refresh").click()
        expect(page.locator("[data-dg-row]")).to_have_count(3)
        assert not report(page, fixture_url)["effects"]
        assert not report(page, fixture_url)["operations"]
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        label = f"{browser[0]}-{size[0]}x{size[1]}-{theme}"
        assert_layout(page, size[0])
        page.screenshot(path=str(ARTIFACTS / (label + "-report.png")), full_page=True, animations="disabled")

        # The server can be healthy while WhatsApp is disconnected.
        control(page, fixture_url, "disconnected")
        action(page, "refresh").click()
        expect(page.locator("[data-dg-section=connection]")).to_contain_text("Desconectado")
        expect(page.locator("[data-dg-section=server]")).to_contain_text("Disponível")
        expect(action(page, "prepare")).to_be_disabled()
        assert not writes(report(page, fixture_url))
        control(page, fixture_url, "connected")
        action(page, "refresh").click()
        expect(action(page, "prepare")).to_be_enabled()

        action(page, "proxy-test").click()
        expect(page.locator("[data-dg-proxy-result]")).to_be_visible()
        expect(operation_action(page, "acknowledge")).to_be_enabled()
        proxy_state = report(page, fixture_url)
        proxy_call, = writes(proxy_state)
        assert set(proxy_call["data"]) == {"idempotency_key", "expected_version"}
        assert len(proxy_state["proxy_calls"]) == 1 and proxy_state["effects"] == []
        assert proxy_state["operations"][0]["result"]["request_id"] != proxy_call["data"]["idempotency_key"]
        expect(page.locator("[data-dg-dialog]")).to_have_count(0)
        operation_action(page, "acknowledge").click()

        action(page, "prepare").click()
        dialog = page.locator("[data-dg-dialog]")
        expect(dialog).to_have_attribute("data-dg-view", "confirm")
        expect(dialog.locator("[data-dg-preview-recipient]")).to_contain_text(SELF_PHONE)
        expect(dialog).to_contain_text("Próprio dispositivo")
        expect(dialog).to_contain_text(CONTRACT["detail_response"]["test_message"]["text"])
        assert not report(page, fixture_url)["effects"]
        assert_dialog_layout(page, *size)
        page.screenshot(path=str(ARTIFACTS / (label + "-confirmation.png")), animations="disabled")
        dialog.locator('[data-dg-action="cancel"]').click()
        expect(dialog).to_have_count(0)
        assert not report(page, fixture_url)["effects"]

        action(page, "prepare").click()
        expect(dialog).to_have_attribute("data-dg-view", "confirm")
        prepared = report(page, fixture_url)
        prepare_call = writes(prepared)[-1]
        assert prepare_call["data"]["phone"] is None
        dialog.locator('[data-dg-action="confirm"]').click()
        expect(dialog).to_have_attribute("data-dg-view", "pending")
        expect(dialog).to_contain_text("aguarda o resultado do transporte")
        admitted = report(page, fixture_url)
        confirmed = writes(admitted)[-1]
        assert set(confirmed["data"]) == {"idempotency_key", "confirmation_version", "confirmation"}
        assert confirmed["data"]["confirmation"] == "ENVIAR"
        assert confirmed["data"]["idempotency_key"] == prepare_call["data"]["idempotency_key"]
        assert len(admitted["effects"]) == 1 and admitted["effects"][0]["phone"] == SELF_PHONE
        assert_private_recovery(page, prepare_call["data"]["idempotency_key"])
        before = writes(admitted)
        dialog.locator('[data-dg-action="recover"]').click()
        expect(dialog).to_have_attribute("data-dg-view", "pending")
        expect(dialog.locator('[data-dg-action="recover"]')).to_be_enabled()
        assert writes(report(page, fixture_url)) == before
        control(page, fixture_url, "accept_message")
        dialog.locator('[data-dg-action="recover"]').click()
        expect(dialog).to_have_attribute("data-dg-view", "result")
        expect(dialog).to_contain_text("O recebimento pelo contato não está confirmado")
        assert len(report(page, fixture_url)["effects"]) == 1
        dialog.locator('[data-dg-action="acknowledge"]').click()
        expect(dialog).to_have_count(0)
        expect(page.locator("[data-dg-field=device]")).to_be_enabled()
        assert not page.evaluate("Boolean(history.state.orionDiagnosticRecovery)")
        assert_layout(page, size[0])
        assert not errors, errors
        assert not external, external
    finally:
        context.close()


@pytest.mark.parametrize("phase", ["prepare", "proxy"])
@pytest.mark.parametrize("lost_first", [False, True], ids=["direct", "after-missing-response"])
def test_stale_version_rejection_releases_only_after_proof_and_never_reposts(browser, fixture_url, phase, lost_first):
    context, page, external, errors = setup_page(browser, fixture_url)
    try:
        open_diagnostics(page, fixture_url)
        selected(page)
        entered_phone = "+5567999999903"
        page.locator("[data-dg-field=phone]").fill(entered_phone)
        ending = "/prepare" if phase == "prepare" else "/proxy-tests"
        if lost_first:
            control(page, fixture_url, "missing_" + phase)
            with page.expect_response(lambda response: response.request.method == "POST"
                    and response.url.endswith(ending) and response.status == 503):
                action(page, "prepare" if phase == "prepare" else "proxy-test").click()
            expect(page.locator("[data-dg-feedback]")).to_contain_text("Não foi possível confirmar a solicitação")
            missing = report(page, fixture_url)
            original, = writes(missing)
            assert missing["operations"] == missing["effects"] == missing["proxy_calls"] == []
            assert_private_recovery(page, original["data"]["idempotency_key"], entered_phone)
            reload_same_session(page)
            expect(page.locator("[data-dg-operation]")).to_contain_text("não foi encontrada")
            assert writes(report(page, fixture_url)) == [original]
            operation_action(page, "recover").click()
            expect(page.locator("[data-dg-dialog]")).to_have_attribute("data-dg-view", "missing")
            assert_private_recovery(page, original["data"]["idempotency_key"], entered_phone)
        control(page, fixture_url, "bump_revision")
        if lost_first and phase == "prepare":
            action(page, "repeat-prepare").click()
            expect(page.locator("[data-dg-field=phone]")).to_be_enabled()
            page.locator("[data-dg-field=phone]").fill(entered_phone)
        with page.expect_response(lambda response: response.request.method == "POST"
                and response.url.endswith(ending) and response.status == 409) as response_info:
            action(page, "repeat-proxy" if lost_first and phase == "proxy" else
                   "prepare" if phase == "prepare" else "proxy-test").click()
        payload = response_info.value.json()
        expect(page.locator("[data-dg-feedback]")).to_contain_text("solicitação não foi criada")
        expect(page.locator("[data-dg-field=device]")).to_be_enabled()
        expect(action(page, "prepare")).to_be_enabled()
        expect(action(page, "proxy-test")).to_be_enabled()
        expect(page.locator("[data-dg-field=phone]")).to_have_value("")
        expect(page.locator("[data-dg-dialog]")).to_have_count(0)
        expect(page.locator("[data-dg-operation]")).to_have_count(0)
        assert not page.evaluate("Boolean(history.state.orionDiagnosticRecovery)")
        rejected = report(page, fixture_url)
        attempted = writes(rejected)
        assert len(attempted) == (2 if lost_first else 1)
        if lost_first:
            assert attempted[0] == attempted[1]
        previous = attempted[-1]
        assert payload["detail"]["code"] == "DIAGNOSTIC_CONTEXT_CHANGED"
        assert payload["detail"]["rejection"] == {
            "reason": "stale_device_version", "idempotency_key": previous["data"]["idempotency_key"],
            "device_id": DEVICE_ID, "kind": "message_test" if phase == "prepare" else "proxy_test",
            "expected_version": previous["data"]["expected_version"],
        }
        last_write = max(i for i, call in enumerate(rejected["calls"]) if call["method"] == "POST")
        assert [(call["method"], call["path"]) for call in rejected["calls"][last_write + 1:]] == [
            ("GET", DIAGNOSTICS_BASE), ("GET", DEVICE_PATH)]
        assert rejected["operations"] == rejected["effects"] == rejected["proxy_calls"] == []

        # Reload must not revive the rejected key. A fresh admission occurs
        # only after a further deliberate click using the updated report.
        page.evaluate("history.replaceState(history.state, '', '/panel?aba=diagnosticos&device_id=' + " + json.dumps(DEVICE_ID) + ")")
        page.reload()
        expect(page.locator("[data-dg-section=connection]")).to_be_visible()
        expect(action(page, "prepare")).to_be_enabled()
        expect(page.locator("[data-dg-operation]")).to_have_count(0)
        assert not page.evaluate("Boolean(history.state.orionDiagnosticRecovery)")
        assert writes(report(page, fixture_url)) == attempted
        action(page, "prepare" if phase == "prepare" else "proxy-test").click()
        if phase == "prepare":
            expect(page.locator("[data-dg-dialog]")).to_have_attribute("data-dg-view", "confirm")
        else:
            expect(operation_action(page, "acknowledge")).to_be_enabled()
        admitted = report(page, fixture_url)
        assert len(writes(admitted)) == len(attempted) + 1
        new = writes(admitted)[-1]
        assert new["data"]["idempotency_key"] != previous["data"]["idempotency_key"]
        assert new["data"]["expected_version"] != previous["data"]["expected_version"]
        assert len(admitted["operations"]) == 1 and admitted["effects"] == []
        assert len(admitted["proxy_calls"]) == (1 if phase == "proxy" else 0)
        assert not errors, errors
        assert not external, external
    finally:
        context.close()


@pytest.mark.parametrize("phase", ["proxy", "confirm"])
def test_conflict_after_admission_keeps_http_recovery_and_never_repeats(browser, fixture_url, phase):
    context, page, external, errors = setup_page(browser, fixture_url)
    try:
        open_diagnostics(page, fixture_url)
        selected(page)
        if phase == "confirm":
            action(page, "prepare").click()
            expect(page.locator("[data-dg-dialog]")).to_have_attribute("data-dg-view", "confirm")
        control(page, fixture_url, "marked_confirm_conflict" if phase == "confirm" else "admitted_conflict")
        ending = "/confirm" if phase == "confirm" else "/proxy-tests"
        with page.expect_response(lambda response: response.request.method == "POST"
                and response.url.endswith(ending) and response.status == 409) as response_info:
            action(page, phase if phase == "confirm" else "proxy-test").click()
        payload = response_info.value.json()
        assert ("rejection" in payload["detail"]) == (phase == "confirm")
        expect(page.locator("[data-dg-feedback]")).to_contain_text("contexto do dispositivo mudou")
        admitted = report(page, fixture_url)
        before = writes(admitted)
        key = before[-1]["data"]["idempotency_key"]
        assert len(before) == (2 if phase == "confirm" else 1)
        assert len(admitted["operations"]) == 1
        assert len(admitted["effects"]) == (1 if phase == "confirm" else 0)
        assert len(admitted["proxy_calls"]) == (1 if phase == "proxy" else 0)
        assert_private_recovery(page, key)
        reload_same_session(page)
        expect(page.locator("[data-dg-operation]")).to_contain_text(
            "aguarda o resultado do transporte" if phase == "confirm" else "conexão pelo proxy")
        recovered = report(page, fixture_url)
        assert writes(recovered) == before
        assert len(recovered["operations"]) == 1
        assert recovered["effects"] == admitted["effects"]
        assert recovered["proxy_calls"] == admitted["proxy_calls"]
        assert_private_recovery(page, key)
        assert not errors, errors
        assert not external, external
    finally:
        context.close()


@pytest.mark.parametrize("phase", ["prepare", "confirm", "proxy"])
def test_lost_response_recovers_by_reads_without_duplicate_admission(browser, fixture_url, phase):
    context, page, external, errors = setup_page(browser, fixture_url)
    try:
        open_diagnostics(page, fixture_url)
        selected(page)
        supplied_phone = "+5567999999903"
        if phase != "proxy":
            page.locator("[data-dg-field=phone]").fill(supplied_phone)
        if phase == "confirm":
            action(page, "prepare").click()
            expect(page.locator("[data-dg-dialog]")).to_have_attribute("data-dg-view", "confirm")
        control(page, fixture_url, "lost_" + phase)
        ending = "/proxy-tests" if phase == "proxy" else "/" + phase
        with page.expect_response(lambda response: response.request.method == "POST"
                and response.url.endswith(ending) and response.status == 503):
            action(page, "proxy-test" if phase == "proxy" else phase).click()
        expect(page.locator("[data-dg-feedback]")).to_contain_text("Não foi possível confirmar a solicitação")
        data = report(page, fixture_url)
        original_writes = writes(data)
        assert len(original_writes) == (2 if phase == "confirm" else 1)
        assert len(data["operations"]) == 1
        assert len(data["effects"]) == (1 if phase == "confirm" else 0)
        assert len(data["proxy_calls"]) == (1 if phase == "proxy" else 0)
        key = original_writes[-1]["data"]["idempotency_key"]
        assert_private_recovery(page, key, supplied_phone)
        reload_same_session(page)
        expect(page.locator("[data-dg-dialog]")).to_have_count(0)
        expected = {"prepare": "A mensagem está preparada", "confirm": "aguarda o resultado do transporte",
                    "proxy": "A conexão pelo proxy configurado foi verificada"}[phase]
        expect(page.locator("[data-dg-operation]")).to_contain_text(expected)
        recovered = report(page, fixture_url)
        assert writes(recovered) == original_writes
        reads = [call for call in recovered["calls"] if "/requests/" in call["path"]]
        assert reads and all(call["method"] == "GET" for call in reads)
        assert reads[-1]["path"].endswith("/" + key)
        assert len(recovered["effects"]) == (1 if phase == "confirm" else 0)
        assert len(recovered["proxy_calls"]) == (1 if phase == "proxy" else 0)
        if phase == "prepare":
            operation_action(page, "recover").click()
            expect(page.locator("[data-dg-dialog]")).to_have_attribute("data-dg-view", "confirm")
            expect(page.locator("[data-dg-preview-recipient]")).to_contain_text(supplied_phone)
            assert report(page, fixture_url)["effects"] == []
        assert not errors, errors
        assert not external, external
    finally:
        context.close()


def test_missing_prepare_requires_explicit_reentry_with_the_same_key(browser, fixture_url):
    context, page, external, errors = setup_page(browser, fixture_url)
    try:
        open_diagnostics(page, fixture_url)
        selected(page)
        phone = "+5567999999903"
        page.locator("[data-dg-field=phone]").fill(phone)
        control(page, fixture_url, "missing_prepare")
        action(page, "prepare").click()
        expect(page.locator("[data-dg-feedback]")).to_contain_text("Não foi possível confirmar a solicitação")
        initial = report(page, fixture_url)
        first, = writes(initial)
        assert initial["operations"] == [] and initial["effects"] == []
        assert_private_recovery(page, first["data"]["idempotency_key"], phone)
        reload_same_session(page)
        expect(page.locator("[data-dg-feedback]")).to_contain_text("Nenhuma repetição foi iniciada")
        assert writes(report(page, fixture_url)) == [first]
        expect(page.locator("[data-dg-field=phone]")).to_have_value("")
        operation_action(page, "recover").click()
        dialog = page.locator("[data-dg-dialog]")
        expect(dialog).to_have_attribute("data-dg-view", "missing")
        dialog.locator('[data-dg-action="repeat-prepare"]').click()
        expect(dialog).to_have_count(0)
        expect(page.locator("[data-dg-field=phone]")).to_be_enabled()
        assert writes(report(page, fixture_url)) == [first]
        page.locator("[data-dg-field=phone]").fill(phone)
        action(page, "prepare").click()
        expect(dialog).to_have_attribute("data-dg-view", "confirm")
        repeated = report(page, fixture_url)
        assert len(writes(repeated)) == 2
        assert writes(repeated)[1]["data"] == first["data"]
        assert len(repeated["operations"]) == 1 and repeated["effects"] == []
        expect(dialog.locator("[data-dg-preview-recipient]")).to_contain_text(phone)
        dialog.locator('[data-dg-action="cancel"]').click()
        assert report(page, fixture_url)["effects"] == []
        assert not errors, errors
        assert not external, external
    finally:
        context.close()


def test_unknown_revocation_other_niche_and_empty_state_never_admit(browser, fixture_url):
    context, page, external, errors = setup_page(browser, fixture_url)
    try:
        open_diagnostics(page, fixture_url)
        selected(page)
        control(page, fixture_url, "unknown")
        action(page, "refresh").click()
        expect(page.locator("[data-dg-section=server]")).to_contain_text("Indisponível")
        expect(page.locator("[data-dg-section=server]")).to_contain_text("Não medida")
        expect(page.locator("[data-dg-section=connection]")).to_contain_text("Não confirmado")
        expect(action(page, "prepare")).to_be_disabled()
        assert not writes(report(page, fixture_url))
        control(page, fixture_url, "revoke")
        action(page, "refresh").click()
        expect(page.locator("#page-suite")).to_have_attribute("data-os-current-route", "novidades")
        expect(page.locator('#osNav [data-os-route="diagnosticos"]')).to_have_count(0)
        expect(page.locator("[data-dg-row]")).to_have_count(0)
        assert not writes(report(page, fixture_url))
        for query, fallback in (("fixtureProfile=standard", "plano"), ("fixtureReadonly=1", "novidades")):
            page.goto(fixture_url + "/panel?fixtureRole=owner&fixturePlan=elite&aba=diagnosticos&" + query)
            expect(page.locator("#page-suite")).to_have_attribute("data-os-current-route", fallback)
            assert report(page, fixture_url)["calls"] == []
        page.goto(fixture_url + "/panel?fixtureRole=owner&fixturePlan=elite&aba=diagnosticos&fixtureEmpty=1")
        expect(page.locator("[data-dg-empty]")).to_contain_text("Nenhum dispositivo")
        expect(page.locator("[data-dg-row]")).to_have_count(0)
        assert report(page, fixture_url)["effects"] == []
        assert not writes(report(page, fixture_url))
        assert not errors, errors
        assert not external, external
    finally:
        context.close()
