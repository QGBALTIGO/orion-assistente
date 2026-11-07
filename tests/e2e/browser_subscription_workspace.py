"""Subscription workspace using the shipped panel, CSS and local HTTP fixture.

Only synthetic in-memory identity data changes. There is no database, payment,
worker or provider. Chromium/WebKit viewport emulation is not physical-device QA.
"""
from __future__ import annotations

import hashlib
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright
import pytest

from scripts.plans_preview import Handler as PlansHandler, base


ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = ROOT / "test-artifacts" / "subscription-workspace"
SIZES = [(390, 844), (768, 1024), (1440, 900)]
PROFILE = "subscription_services"
ALLOWED_ROUTES = ["dispositivos", "chatbot", "novidades", "ajuda", "member"]
IDENTITY = {"name": "Empresa sintética de assinaturas", "contact_phone": "+5511900000001"}


class Handler(PlansHandler):
    """Extend the existing synthetic server with the workspace contract only."""

    def body(self, value, status=200, mime="application/json", cookie=None):
        path = urlsplit(self.path).path
        if cookie and path in {"/", "/panel"}:
            with base._lock:
                session = base._sessions[cookie]
                session.update(workspace_profile=PROFILE, workspace_calls=[],
                               business_identity=dict(IDENTITY))
        session = self.session()
        if session and path == "/api/v1/account/businesses" and isinstance(value, list):
            value = [{**item, "workspace_profile": PROFILE,
                      "name": session["business_identity"]["name"]} for item in value]
        if session and path.endswith("/management/permissions") and isinstance(value, dict):
            plan = {**value["effective_plan"], "workspace_profile": PROFILE}
            plan["features"] = ["account.manage", "business.setup", "devices.manage"]
            plan["permissions"] = {key: key in plan["features"] for key in
                                   {*plan["permissions"], "devices.manage"}}
            plan["revision"] = hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
            value = {"effective_plan": plan}
        return super().body(value, status, mime, cookie)

    def do_GET(self):
        path = urlsplit(self.path).path
        session = self.session()
        if session and path == "/__fixture/calls":
            return self.body({"calls": session["workspace_calls"], "unexpected": session["unexpected"]})
        if session and path.startswith(("/api/", "/auth/")):
            session["workspace_calls"].append({"method": "GET", "path": path})
        if session and path == "/api/v1/account/business-identity":
            return self.body(session["business_identity"])
        return super().do_GET()

    def mutate(self):
        path = urlsplit(self.path).path
        session = self.session()
        if not session:
            return self.reject(None, "Open the isolated workspace first")
        if self.command != "PUT" or path != "/api/v1/account/business-identity":
            return self.reject(session, "Only a synthetic account identity edit is permitted")
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 4000:
                raise ValueError()
            payload = json.loads(self.rfile.read(size))
        except (ValueError, json.JSONDecodeError):
            return self.body({"detail": "Invalid synthetic request"}, 400)
        if self.headers.get("X-Orion-CSRF") != session["csrf"]:
            return self.reject(session, "Synthetic CSRF required")
        identity = session["business_identity"]
        if (not isinstance(payload, dict) or set(payload) != {
                "name", "contact_phone", "expected_name", "expected_phone"} or
                payload["expected_name"] != identity["name"] or
                payload["expected_phone"] != identity["contact_phone"]):
            return self.body({"detail": "Synthetic identity changed"}, 409)
        session["workspace_calls"].append({"method": "PUT", "path": path})
        identity["name"] = str(payload["name"]).strip()
        return self.body(dict(identity))


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


def open_navigation(page):
    menu = page.locator("#osMenu")
    if menu.is_visible() and menu.get_attribute("aria-expanded") != "true":
        menu.click()
        expect(menu).to_have_attribute("aria-expanded", "true")
    expect(page.locator("#osNav [data-os-route=novidades]")).to_be_visible()


def assert_shell(page, width):
    expect(page.locator("#osNav [data-os-route]")).to_have_count(5)
    assert page.locator("#osNav [data-os-route]").evaluate_all(
        "nodes => nodes.map(node => node.dataset.osRoute)"
    ) == ALLOWED_ROUTES
    assert page.locator("#osNav .os-nav-section").all_text_contents() == ["Gestão", "Ajuda e conta"]
    expect(page.locator("#osNav [data-os-action]")).to_have_count(0)
    expect(page.locator("#osSidebarContext [data-os-route=site]")).not_to_be_visible()
    expect(page.locator("#osConsultantFab")).not_to_be_visible()
    expect(page.locator("#osNotificationsToggle")).not_to_be_visible()
    expect(page.locator("[data-setup-access=plano]")).not_to_be_visible()
    expect(page.locator("#osThemeToggle")).to_be_visible()
    measured = page.evaluate("""() => ({
      viewport: innerWidth,
      scroll: document.documentElement.scrollWidth,
      forbidden: [...document.querySelectorAll('#page-suite [data-os-route]')]
        .filter(node => node.getClientRects().length && getComputedStyle(node).visibility !== 'hidden')
        .map(node => node.dataset.osRoute)
        .filter(route => !['dispositivos','chatbot','novidades','ajuda','member'].includes(route))
    })""")
    assert measured["viewport"] == width and measured["scroll"] <= width + 1, measured
    assert not measured["forbidden"], measured


@pytest.mark.parametrize("size", SIZES, ids=lambda size: f"{size[0]}x{size[1]}")
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_subscription_shell_keeps_only_enabled_devices_help_and_account(browser, fixture_url, size, theme):
    engine, instance = browser
    width, height = size
    context = instance.new_context(viewport={"width": width, "height": height},
        color_scheme=theme, locale="pt-BR", has_touch=width == 390,
        is_mobile=engine == "webkit" and width == 390, service_workers="block")
    allowed = urlsplit(fixture_url)
    unexpected_network, errors, calls = [], [], []

    def route(request_route):
        url = urlsplit(request_route.request.url)
        if url.scheme in {"http", "https"} and (url.hostname, url.port) != (allowed.hostname, allowed.port):
            unexpected_network.append(request_route.request.url)
            return request_route.abort()
        return request_route.continue_()

    context.route("**/*", route)
    context.add_init_script("localStorage.setItem('orion-theme', " + json.dumps(theme) + ");")
    page = context.new_page()
    page.on("pageerror", lambda error: errors.append(str(error)))
    label = f"{engine}-{width}x{height}-{theme}"
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    try:
        # A paid fixture also must not reveal the scheduling workspace by accident.
        page.goto(f"{fixture_url}/panel?fixtureRole=owner&fixturePlan=elite&fixtureDelay=1&aba=plano")
        expect(page.locator("#page-suite")).to_have_attribute("data-os-current-route", "novidades")
        expect(page.locator(".os-news-list")).to_be_visible()
        page.evaluate("document.fonts.ready")
        assert_shell(page, width)
        open_navigation(page)
        expect(page.locator("#logout")).to_be_visible()
        page.screenshot(path=str(ARTIFACTS / f"{label}-navigation.png"), full_page=False, animations="disabled")

        page.locator("#osNav [data-os-route=ajuda]").click()
        expect(page.locator("#osHelpSearch")).to_be_visible()
        assert_shell(page, width)
        page.locator("#osHelpSearch").fill("conta")
        expect(page.locator("#osHelpResults")).to_contain_text("conta")
        page.screenshot(path=str(ARTIFACTS / f"{label}-help.png"), full_page=False, animations="disabled")

        open_navigation(page)
        page.locator("#osNav [data-os-route=member]").click()
        expect(page.locator("#memberBusinessForm")).to_be_visible()
        expect(page.locator("#memberBusinessName")).to_have_value(IDENTITY["name"])
        expect(page.locator("#memberPasswordForm")).to_be_visible()
        expect(page.locator("#memberBusinessSave")).to_be_enabled()
        assert_shell(page, width)
        page.locator("#memberBusinessName").fill("Assinaturas sintéticas atualizadas")
        page.locator("#memberBusinessSave").click()
        expect(page.locator("#memberBusinessFeedback")).to_contain_text("atualizados com sucesso")
        assert_shell(page, width)
        page.locator("#memberProfileHeading").scroll_into_view_if_needed()
        page.screenshot(path=str(ARTIFACTS / f"{label}-account.png"), full_page=False, animations="disabled")

        # Old operational bookmarks are resolved by the same normal bootstrap.
        page.goto(fixture_url + "/panel?aba=site")
        expect(page.locator("#page-suite")).to_have_attribute("data-os-current-route", "novidades")
        expect(page.locator(".os-news-list")).to_be_visible()
        assert_shell(page, width)
        expect(page.locator("#osSidebarContext .os-tenant-name")).to_have_text("Assinaturas sintéticas atualizadas")
        snapshot = page.request.get(fixture_url + "/__fixture/calls").json()
        calls = snapshot["calls"]
        allowed_paths = {"/auth/me", "/auth/features", "/api/v1/account/businesses",
                         "/api/v1/onboarding/entry", "/api/v1/account/business-identity",
                         base.BASE + "/management/permissions"}
        assert all(call["path"] in allowed_paths for call in calls), calls
        assert [call for call in calls if call["method"] != "GET"] == [
            {"method": "PUT", "path": "/api/v1/account/business-identity"}]
        assert not snapshot["unexpected"], snapshot["unexpected"]
        assert not unexpected_network, unexpected_network
        assert not errors, errors
    finally:
        (ARTIFACTS / f"{label}.json").write_text(json.dumps({
            "engine": engine, "viewport": size, "theme": theme, "calls": calls,
            "errors": errors, "external_requests": unexpected_network,
            "limitations": ["synthetic state", "viewport emulation", "no payment or messages"],
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        context.close()
