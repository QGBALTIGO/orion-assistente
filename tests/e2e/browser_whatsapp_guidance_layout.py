"""Visual regression of the three WhatsApp guidance screens on a local synthetic tenant.

No real WhatsApp connections, payments, database writes or public site access.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "guidance_workspace_fixture", ROOT / "tests/e2e/browser_subscription_workspace.py"
)
workspace = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(workspace)

GUIDES = [
    ("aquecimento", "warmup"),
    ("evitar-banimento", "avoid-ban"),
    ("resolver-banimento", "resolve-ban"),
]
ARTIFACTS = ROOT / "test-artifacts" / "whatsapp-guidance-layout"


class GuidanceHandler(workspace.Handler):
    """Enable only the read-only guidance and diagnostic links for a synthetic owner."""

    def body(self, value, status=200, mime="application/json", cookie=None):
        path = urlsplit(self.path).path
        if cookie and path in {"/", "/panel"}:
            with workspace.base._lock:
                workspace.base._sessions[cookie].update(
                    workspace_profile="subscription_services",
                    workspace_calls=[],
                    business_identity=dict(workspace.IDENTITY),
                )
        session = self.session()
        if session and path == "/api/v1/account/businesses" and isinstance(value, list):
            value = [{**item, "role": "owner", "status": "active",
                      "workspace_profile": "subscription_services",
                      "name": session["business_identity"]["name"]} for item in value]
        if session and path.endswith("/management/permissions") and isinstance(value, dict):
            plan = {**value["effective_plan"], "workspace_profile": "subscription_services"}
            features = [
                "account.manage", "business.setup", "devices.manage",
                "diagnostics.manage", "whatsapp_guidance.view",
            ]
            plan["features"] = features
            plan["permissions"] = {key: key in features for key in
                                   {*plan.get("permissions", {}), *features}}
            plan["revision"] = hashlib.sha256(
                json.dumps(plan, sort_keys=True).encode()
            ).hexdigest()
            value = {"effective_plan": plan}
        return workspace.PlansHandler.body(self, value, status, mime, cookie)


@pytest.fixture(scope="module")
def fixture_url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), GuidanceHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        instance = p.chromium.launch()
        yield instance
        instance.close()


@pytest.mark.parametrize("size", [(320, 700), (390, 844), (768, 1024), (1366, 900)])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_whatsapp_guides_have_no_overflow_and_align_notices(browser, fixture_url, size, theme):
    width, height = size
    context = browser.new_context(
        viewport={"width": width, "height": height}, color_scheme=theme,
        locale="pt-BR", has_touch=width <= 390, service_workers="block"
    )
    origin = urlsplit(fixture_url)
    external, errors, measurements = [], [], []

    def restrict(route):
        target = urlsplit(route.request.url)
        if target.scheme in {"http", "https"} and (
            target.hostname, target.port
        ) != (origin.hostname, origin.port):
            external.append(route.request.url)
            return route.abort()
        return route.continue_()

    context.route("**/*", restrict)
    context.add_init_script(
        "localStorage.setItem('orion-theme'," + json.dumps(theme) + ");"
    )
    page = context.new_page()
    page.on("pageerror", lambda error: errors.append(str(error)))
    prefix = f"chromium-{width}x{height}-{theme}"
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    try:
        page.goto(fixture_url + "/panel?fixtureRole=owner&fixturePlan=elite&fixtureDelay=1&aba=novidades")
        expect(page.locator("#page-suite")).to_be_visible()
        for route, slug in GUIDES:
            page.evaluate("(route) => window.OrionSuite.open(route)", route)
            article = page.locator("article[data-os-guidance='" + slug + "']")
            expect(article).to_be_visible()
            expect(article.locator(".os-help-prose .os-notice").first).to_be_visible()
            page.evaluate("document.fonts.ready")
            notice = article.locator(".os-help-prose .os-notice").first
            notice.scroll_into_view_if_needed()
            measured = page.evaluate("""() => {
              const article = document.querySelector('article[data-os-guidance]');
              const box = article.querySelector('.os-help-prose .os-notice');
              const icon = box.querySelector(':scope > svg');
              const p = box.querySelector(':scope > p');
              const rect = el => {
                const r=el.getBoundingClientRect();
                return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,width:r.width,height:r.height};
              };
              const buttons = [...article.querySelectorAll('.os-form-actions > .os-button')]
                .map(button => rect(button));
              return {
                viewport: innerWidth,
                scrollWidth: document.documentElement.scrollWidth,
                article: rect(article), notice: rect(box), icon: rect(icon), paragraph: rect(p),
                noteDisplay:getComputedStyle(box).display,
                noteMarginBottom:getComputedStyle(p).marginBottom,
                pagePaddingBottom:parseFloat(getComputedStyle(document.querySelector('#page-suite')).paddingBottom),
                buttons,
              };
            }""")
            measurements.append({"route": route, **measured})
            assert measured["scrollWidth"] <= width + 1, (route, measured)
            assert measured["article"]["left"] >= -1, (route, measured)
            assert measured["article"]["right"] <= width + 1, (route, measured)
            assert measured["notice"]["right"] <= width + 1, (route, measured)
            assert measured["paragraph"]["right"] <= measured["notice"]["right"] + 1, (route, measured)
            assert abs(measured["icon"]["top"] - measured["paragraph"]["top"]) <= 8, (route, measured)
            assert measured["noteDisplay"] == "grid", (route, measured)
            assert measured["noteMarginBottom"] == "0px", (route, measured)
            assert all(b["height"] >= 43 for b in measured["buttons"]), (route, measured)
            if width <= 390:
                assert measured["pagePaddingBottom"] >= 80, (route, measured)
            page.screenshot(path=str(ARTIFACTS / f"{prefix}-{route}.png"),
                            animations="disabled")
        assert not external, external
        assert not errors, errors
    finally:
        (ARTIFACTS / f"{prefix}.json").write_text(
            json.dumps({"theme": theme, "viewport": size, "measurements": measurements,
                        "errors": errors, "external": external},
                       ensure_ascii=False, indent=2), encoding="utf-8"
        )
        context.close()
