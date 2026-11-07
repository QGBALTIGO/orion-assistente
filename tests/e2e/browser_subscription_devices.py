"""Device workspace with the shipped Orion assets and a loopback-only fixture.

No database, bridge, WhatsApp account, provider, webhook, proxy or ASR is used.
Browser viewport emulation is not a physical-device or virtual-keyboard test.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

from playwright.sync_api import expect, sync_playwright
import pytest

from scripts.plans_preview import Handler as PlansHandler, base


ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = ROOT / "test-artifacts" / "subscription-devices"
CONTRACT = json.loads((ROOT / "tests/fixtures/subscription_devices_contract.json").read_text())
DEVICE_ID = CONTRACT["list_response"]["items"][0]["id"]
OPERATION_ID = CONTRACT["operation_response"]["operation"]["id"]
DEVICE_BASE = base.BASE + "/subscription-whatsapp/devices"
DEVICE_PATH = DEVICE_BASE + "/" + DEVICE_ID
SIZES = [(390, 844), (768, 1024), (1440, 900)]
PROFILE = "subscription_services"
ALLOWED_ROUTES = ["dispositivos", "chatbot", "novidades", "ajuda", "member"]
CAPABILITIES = {"manage": True, "webhook": True, "proxy": True,
                "transcription": True, "transcription_max_minutes": 30}


class Handler(PlansHandler):
    """Strict synthetic commands: writes change only this browser's memory."""

    def body(self, value, status=200, mime="application/json", cookie=None):
        url = urlsplit(self.path)
        if cookie and url.path in {"/", "/panel"}:
            profile = parse_qs(url.query).get("fixtureProfile", [PROFILE])[0]
            if profile not in {PROFILE, "standard"}:
                profile = PROFILE
            with base._lock:
                session = base._sessions[cookie]
                session.update(workspace_profile=profile, device_calls=[], device=None,
                               device_settings=deepcopy(CONTRACT["settings_response"]),
                               device_operations={}, device_keys={}, pairing_reads=0)
                session["device_settings"]["capabilities"] = dict(CAPABILITIES)
        session = self.session()
        if session and url.path == "/api/v1/account/businesses" and isinstance(value, list):
            value = [{**row, "workspace_profile": session["workspace_profile"],
                      "name": "Empresa sintética de dispositivos"} for row in value]
        if session and url.path.endswith("/management/permissions") and isinstance(value, dict):
            plan = {**value["effective_plan"], "workspace_profile": session["workspace_profile"]}
            if session["workspace_profile"] == PROFILE:
                plan["features"] = ["account.manage", "business.setup", "devices.manage"]
                plan["permissions"] = {key: key in plan["features"] for key in
                                       {*plan["permissions"], "devices.manage"}}
            else:
                plan["permissions"]["devices.manage"] = False
            plan["revision"] = hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
            value = {"effective_plan": plan}
        return super().body(value, status, mime, cookie)

    def snapshot(self, session):
        device = session["device"]
        if device is None:
            return None
        value = deepcopy(device)
        value["features"] = {
            "transcription": session["device_settings"]["settings"]["transcription"]["enabled"],
            "call_blocking": session["device_settings"]["settings"]["calls"]["reject_enabled"],
        }
        return value

    def set_state(self, session, status):
        device = session["device"]
        device["status"] = status
        device["capabilities"].update(connect=status in {"disabled", "stopped"},
            qr=status == "qr_ready", pair=status == "qr_ready",
            restart=status in {"qr_ready", "connected"},
            relink=status in {"qr_ready", "connected", "logged_out"})
        if status == "connected":
            device.update(phone="5567999999901", phone_verified=True,
                          connected_at="2026-11-04T12:00:00Z",
                          last_seen_at="2026-11-04T12:05:00Z")
            device["metrics"].update(available=True, today=4, last_7_days=19,
                                    last_30_days=63, total=118,
                                    tracking_since="2026-10-01T12:00:00Z")

    def do_GET(self):
        path = urlsplit(self.path).path
        session = self.session()
        if session and path == "/__fixture/device-calls":
            return self.body({"calls": session["device_calls"],
                              "unexpected": session["unexpected"],
                              "settings": session["device_settings"]["settings"]})
        if session and path.startswith(("/api/", "/auth/")):
            session["device_calls"].append({"method": "GET", "path": path})
        if session and path.startswith(DEVICE_BASE):
            if session["workspace_profile"] != PROFILE:
                return self.reject(session, "Devices are unavailable to standard workspaces")
            if path == DEVICE_BASE:
                device = self.snapshot(session)
                return self.body({"items": [device] if device else [],
                    "usage": {"used": int(device is not None), "limit": 1},
                    "can_create": device is None, "capabilities": dict(CAPABILITIES)})
            if session["device"] is None:
                return self.body({"detail": {"message": "Dispositivo não encontrado."}}, 404)
            if path == DEVICE_PATH:
                if session["device"]["status"] == "pairing":
                    session["pairing_reads"] += 1
                    if session["pairing_reads"] >= 1:
                        self.set_state(session, "connected")
                return self.body({"device": self.snapshot(session)})
            if path == DEVICE_PATH + "/settings":
                return self.body(session["device_settings"])
            if path == DEVICE_PATH + "/qr":
                if session["device"]["status"] != "qr_ready":
                    return self.reject(session, "No synthetic QR is ready")
                return self.body({"device_id": DEVICE_ID,
                    "revision": session["device"]["revision"],
                    "image": base.PNG, "expires_in_ms": 45000})
            if path == DEVICE_PATH + "/operations/" + OPERATION_ID:
                operation = session["device_operations"].get(OPERATION_ID)
                if not operation:
                    return self.reject(session, "Unknown synthetic operation")
                if operation["status"] == "pending":
                    operation["status"] = "succeeded"
                    self.set_state(session, "qr_ready")
                return self.body({"operation": operation, "device": self.snapshot(session)})
            return self.reject(session, "Unknown device read")
        return super().do_GET()

    def mutate(self):
        path = urlsplit(self.path).path
        session = self.session()
        if not session or session["workspace_profile"] != PROFILE or not path.startswith(DEVICE_BASE):
            return self.reject(session, "Only subscription fixture device writes are permitted")
        if self.headers.get("X-Orion-CSRF") != session["csrf"]:
            return self.reject(session, "Synthetic CSRF required")
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 30000:
                raise ValueError()
            payload = json.loads(self.rfile.read(size))
            if not isinstance(payload, dict):
                raise ValueError()
        except (ValueError, json.JSONDecodeError):
            return self.body({"detail": "Invalid synthetic payload"}, 400)
        call = {"method": self.command, "path": path}
        if path == DEVICE_BASE and self.command == "POST":
            if set(payload) != {"name", "idempotency_key"}:
                return self.reject(session, "Invalid create shape")
            try:
                UUID(payload["idempotency_key"])
            except (ValueError, TypeError):
                return self.reject(session, "Invalid synthetic idempotency key")
            if session["device"] is not None:
                return self.body({"detail": {"message": "Limite de um dispositivo atingido."}}, 409)
            item = deepcopy(CONTRACT["list_response"]["items"][0])
            item["name"] = payload["name"]
            session["device"] = item
            session["device_settings"]["settings"]["name"] = payload["name"]
            session["device_calls"].append(call)
            return self.body({"device": self.snapshot(session)}, 201)
        if session["device"] is None or payload.get("expected_revision") != session["device"]["revision"]:
            return self.body({"detail": {"message": "A revisão do dispositivo mudou."}}, 409)
        if path == DEVICE_PATH + "/actions" and self.command == "POST":
            if set(payload) != {"action", "expected_revision", "idempotency_key"}:
                return self.reject(session, "Invalid action shape")
            # This scenario only authorizes the initial synthetic preparation.
            if payload["action"] != "connect":
                return self.reject(session, "The visual scenario did not authorize this action")
            operation = {"id": OPERATION_ID, "device_id": DEVICE_ID,
                         "action": "connect", "status": "pending", "error": None}
            session["device_operations"][OPERATION_ID] = operation
            self.set_state(session, "starting")
            call["action"] = payload["action"]
            session["device_calls"].append(call)
            return self.body({"operation": operation, "device": self.snapshot(session)}, 202)
        if path == DEVICE_PATH + "/pair" and self.command == "POST":
            if set(payload) != {"phone", "expected_revision"} or payload["phone"] != "5567999999901":
                return self.reject(session, "Unexpected synthetic pairing number")
            self.set_state(session, "pairing")
            session["device_calls"].append(call)
            return self.body({**CONTRACT["pair_response"], "revision": session["device"]["revision"]})
        if path == DEVICE_PATH + "/settings" and self.command == "PUT":
            if set(payload) != {"settings", "expected_revision"}:
                return self.reject(session, "Invalid settings shape")
            saved = deepcopy(payload["settings"])
            if set(saved) != set(CONTRACT["settings_defaults"]) or "password_configured" in saved["proxy"]:
                return self.reject(session, "Unexpected settings fields")
            # Do not retain or serialize even synthetic secret values in reports.
            old_secret = session["device_settings"]["settings"]["proxy"]["password_configured"]
            secret = bool(saved["proxy"].pop("password", "")) or old_secret
            if saved["proxy"].pop("clear_password", False):
                secret = False
            saved["proxy"]["password_configured"] = secret
            session["device"]["revision"] += 1
            session["device"]["name"] = saved["name"]
            session["device_settings"].update(settings=saved,
                revision=session["device"]["revision"],
                applied_revision=session["device"]["revision"], restart_required=False)
            session["device_calls"].append(call)
            return self.body(session["device_settings"])
        return self.reject(session, "Unknown synthetic device write")


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


@pytest.fixture(scope="module", params=["chromium", "webkit"])
def browser(request):
    with sync_playwright() as playwright:
        options = {}
        executable = os.getenv("ORION_TEST_CHROMIUM_EXECUTABLE")
        if request.param == "chromium" and executable:
            options["executable_path"] = executable
        instance = getattr(playwright, request.param).launch(**options)
        yield request.param, instance
        instance.close()


def navigation(page):
    menu = page.locator("#osMenu")
    if menu.is_visible() and menu.get_attribute("aria-expanded") != "true":
        menu.click()
        expect(menu).to_have_attribute("aria-expanded", "true")


def assert_layout(page, width):
    measured = page.evaluate("""() => ({
      width: innerWidth, scroll: document.documentElement.scrollWidth,
      dialogs: [...document.querySelectorAll('dialog[open]')]
        .map(node => ({left: node.getBoundingClientRect().left,
                      right: node.getBoundingClientRect().right}))
    })""")
    assert measured["width"] == width and measured["scroll"] <= width + 1, measured
    assert all(row["left"] >= 0 and row["right"] <= width + 1
               for row in measured["dialogs"]), measured


def assert_subscription_shell(page):
    assert page.locator("#osNav [data-os-route]").evaluate_all(
        "nodes => nodes.map(node => node.dataset.osRoute)") == ALLOWED_ROUTES
    expect(page.locator("#osSidebarContext [data-os-route=site]")).to_have_count(0)
    expect(page.locator("#osConsultantFab")).not_to_be_visible()
    expect(page.locator("#osNotificationsToggle")).not_to_be_visible()
    expect(page.locator("#osNav [data-os-route=plano]")).to_have_count(0)


@pytest.mark.parametrize("size", SIZES, ids=lambda size: f"{size[0]}x{size[1]}")
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_subscription_device_flow_and_standard_workspace_isolation(browser, fixture_url, size, theme):
    engine, instance = browser
    width, height = size
    context = instance.new_context(viewport={"width": width, "height": height},
        color_scheme=theme, locale="pt-BR", has_touch=width == 390,
        is_mobile=engine == "webkit" and width == 390, service_workers="block")
    expected_origin = urlsplit(fixture_url)
    external, errors, calls = [], [], []
    label = f"{engine}-{width}x{height}-{theme}"
    ARTIFACTS.mkdir(parents=True, exist_ok=True)

    def local_only(route):
        url = urlsplit(route.request.url)
        if (url.scheme, url.netloc) != (expected_origin.scheme, expected_origin.netloc):
            external.append(route.request.url)
            return route.abort()
        return route.continue_()

    context.route("**/*", local_only)
    context.add_init_script("localStorage.setItem('orion-theme', " + json.dumps(theme) + ");")
    page = context.new_page()
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        page.goto(f"{fixture_url}/panel?fixtureRole=owner&fixturePlan=elite&aba=dispositivos")
        expect(page.locator("#page-suite")).to_have_attribute("data-os-current-route", "dispositivos")
        expect(page.locator(".os-devices-usage .os-badge")).to_have_text("0 de 1")
        assert_subscription_shell(page)
        assert_layout(page, width)
        page.locator(".os-devices-header [data-sd-action=create]").click()
        expect(page.locator("dialog[open]")).to_be_visible()
        page.locator("[data-sd-field=new_name]").fill("WhatsApp sintético")
        page.locator("dialog[open] button[type=submit]").click()
        expect(page.locator("[data-sd-card] h2")).to_have_text("WhatsApp sintético")
        page.locator(".os-device-card-actions [data-sd-action=open-connect]").click()
        page.locator("[data-sd-action=connect]").click()
        page.locator("dialog[open] button[type=submit]").click()
        expect(page.locator(".wizard-timeline-step")).to_have_count(7)
        page.locator("[data-sd-method=qr]").click()
        page.locator("[data-sd-action=tutorial-next]").click()
        expect(page.locator("[data-sd-qr] img")).to_be_visible()
        assert_layout(page, width)
        page.screenshot(path=str(ARTIFACTS / f"{label}-qr.png"), full_page=False, animations="disabled")
        page.locator("[data-sd-action=hide-qr]").click()
        page.locator("[data-sd-method=code]").click()
        page.locator("[data-sd-action=tutorial-next]").click()
        page.locator("[data-sd-field=pair_phone]").fill("(67) 99999-9901")
        page.locator("[data-sd-form=pair] button[type=submit]").click()
        expect(page.locator("[data-sd-code]")).to_have_text("ABCD-EFGH")
        page.locator("[data-sd-action=hide-code]").click()
        expect(page.locator("[data-sd-code]")).to_have_count(0)
        page.locator("[data-sd-action=show-code]").click()
        expect(page.locator("[data-sd-code]")).to_have_text("ABCD-EFGH")
        expect(page.locator(".os-device-connected")).to_be_visible(timeout=10000)
        page.locator(".os-device-back[data-sd-action=back]").click()
        expect(page.locator(".os-device-metric strong").first).to_have_text("4")
        page.locator(".os-devices-header [data-sd-action=create]").click()
        expect(page.locator("[data-sd-feedback]")).to_contain_text("limite de 1 dispositivo")
        page.evaluate("document.fonts.ready")
        assert_layout(page, width)
        page.screenshot(path=str(ARTIFACTS / f"{label}-connected.png"), full_page=False, animations="disabled")

        page.locator(".os-device-card-actions [data-sd-action=settings]").click()
        expect(page.locator("[data-sd-form=settings]")).to_be_visible()
        page.locator("[data-sd-field=name]").fill("WhatsApp configurado")
        page.locator("[data-sd-field=timezone]").select_option("America/Cuiaba")
        page.locator("[data-sd-field=test_phone_e164]").fill("(67) 99999-9902")
        page.locator("[data-sd-section=transcription]>summary").click()
        page.locator("[data-sd-field='transcription.enabled']").check()
        page.locator("[data-sd-field='transcription.max_minutes']").fill("30")
        page.locator("[data-sd-field='transcription.reply_enabled']").check()
        page.locator("[data-sd-section=transcription]>summary").click()
        page.locator("[data-sd-section=calls]>summary").click()
        page.locator("[data-sd-field='calls.reject_enabled']").check()
        page.locator("[data-sd-field='calls.schedule_enabled']").check()
        call_day = page.locator("[data-sd-schedule=calls][data-sd-weekday='0']")
        call_day.locator("[data-sd-day-enabled]").check()
        call_day.locator("[data-sd-time=start]").fill("09:00")
        call_day.locator("[data-sd-time=end]").fill("17:00")
        call_day.locator("[data-sd-copy]").select_option("weekdays")
        page.locator("[data-sd-field='calls.holiday_off']").check()
        page.locator("[data-sd-section=calls]>summary").click()
        page.locator("[data-sd-section=business_hours]>summary").click()
        page.locator("[data-sd-field='business_hours.enabled']").check()
        day = page.locator("[data-sd-schedule=business_hours][data-sd-weekday='0']")
        day.locator("[data-sd-day-enabled]").check()
        day.locator("[data-sd-time=start]").fill("08:00")
        day.locator("[data-sd-time=end]").fill("18:00")
        day.locator("[data-sd-time=break_start]").fill("12:00")
        day.locator("[data-sd-time=break_end]").fill("13:00")
        day.locator("[data-sd-copy]").select_option("weekdays")
        expect(page.locator("[data-sd-schedule=business_hours][data-sd-weekday='4'] [data-sd-time=start]")).to_have_value("08:00")
        page.locator("[data-sd-schedule=business_hours][data-sd-weekday='5'] [data-sd-action=clear-day]").click()
        page.locator("[data-sd-field='business_hours.holiday_off']").check()
        page.locator("[data-sd-field='business_hours.block_replies_outside_hours']").check()
        page.locator("[data-sd-field='business_hours.outside_message']").fill("Voltamos no próximo expediente.")
        page.locator("[data-sd-field='business_hours.break_message']").fill("Voltamos após a pausa.")
        day.scroll_into_view_if_needed()
        assert_layout(page, width)
        # Native time controls must retain room for both the value and picker;
        # a non-overflowing row can still hide every digit at tablet widths.
        time_widths = page.locator("[data-sd-schedule=business_hours] [data-sd-time]").evaluate_all(
            "nodes => nodes.map(node => node.clientWidth)")
        assert all(value >= 110 for value in time_widths), time_widths
        page.screenshot(path=str(ARTIFACTS / f"{label}-hours.png"), full_page=False, animations="disabled")
        page.locator("[data-sd-form=settings] button[type=submit]").click()
        expect(page.locator("[data-sd-feedback]")).to_contain_text("Configurações salvas")
        page.locator(".os-device-back[data-sd-action=back]").click()
        expect(page.locator("[data-sd-card] h2")).to_have_text("WhatsApp configurado")
        page.locator(".os-device-menu>summary").click()
        page.locator("[data-sd-action=remove]").click()
        expect(page.locator("[data-sd-action=alternative-settings]")).to_be_visible()
        expect(page.locator("[data-sd-action=alternative-relink]")).to_be_visible()
        assert_layout(page, width)
        page.screenshot(path=str(ARTIFACTS / f"{label}-delete-confirmation.png"), full_page=False, animations="disabled")
        page.locator("dialog[open] .os-form-actions [data-sd-action=close-dialog]").click()
        expect(page.locator("[data-sd-card]")).to_have_count(1)
        navigation(page)
        page.locator("#osNav [data-os-route=ajuda]").click()
        expect(page.locator("#osHelpSearch")).to_be_visible()
        assert_subscription_shell(page)
        navigation(page)
        page.locator("#osNav [data-os-route=dispositivos]").click()
        expect(page.locator("[data-sd-card] h2")).to_have_text("WhatsApp configurado")
        assert_layout(page, width)

        report = page.request.get(fixture_url + "/__fixture/device-calls").json()
        calls = report["calls"]
        writes = [call for call in calls if call["method"] != "GET"]
        assert [(call["method"], call["path"]) for call in writes] == [
            ("POST", DEVICE_BASE), ("POST", DEVICE_PATH + "/actions"),
            ("POST", DEVICE_PATH + "/pair"), ("PUT", DEVICE_PATH + "/settings")]
        assert writes[1]["action"] == "connect"
        assert report["settings"]["business_hours"]["days"][4]["break_end"] == "13:00"
        assert report["settings"]["business_hours"]["days"][5]["start"] is None
        assert report["settings"]["transcription"]["max_minutes"] == 30
        assert not report["unexpected"], report["unexpected"]
        assert not any("/api/v1/qr/" in call["path"] or "/qr-channels" in call["path"]
                       or "/plan-" in call["path"] for call in calls)

        # A normal business keeps its original menu and cannot enter this route.
        page.goto(f"{fixture_url}/panel?fixtureRole=owner&fixturePlan=elite&fixtureProfile=standard&aba=dispositivos")
        expect(page.locator("#osNav [data-os-route=dispositivos]")).to_have_count(0)
        expect(page.locator("#osNav [data-os-route=agenda]")).to_have_count(1)
        expect(page.locator("#osSidebarContext [data-os-route=site]")).to_have_count(1)
        expect(page.locator("#page-suite")).to_have_attribute("data-os-current-route", "plano")
        ordinary = page.request.get(fixture_url + "/__fixture/device-calls").json()
        assert not any(call["path"].startswith(DEVICE_BASE) for call in ordinary["calls"])
        assert not ordinary["unexpected"], ordinary["unexpected"]
        assert not errors, errors
        assert not external, external
    finally:
        (ARTIFACTS / f"{label}.json").write_text(json.dumps({
            "engine": engine, "viewport": size, "theme": theme,
            "calls": calls, "errors": errors, "external_requests": external,
            "limitations": ["synthetic device runtime", "viewport emulation",
                            "no provider pairing, messages, webhook, proxy or ASR"],
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        context.close()
