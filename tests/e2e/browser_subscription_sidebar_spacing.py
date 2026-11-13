"""Sidebar geometry on the shipped panel; synthetic accounts and loopback only."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import threading
from http.server import ThreadingHTTPServer
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "subscription_workspace_fixture", ROOT / "tests/e2e/browser_subscription_workspace.py")
workspace = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(workspace)
ARTIFACTS = ROOT / "test-artifacts" / "subscription-sidebar-spacing"


@pytest.fixture(scope="module")
def servers():
    running = []
    urls = {}
    for profile, handler in [("subscription", workspace.Handler), ("standard", workspace.PlansHandler)]:
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        running.append((server, thread))
        urls[profile] = f"http://127.0.0.1:{server.server_port}"
    try:
        yield urls
    finally:
        for server, thread in running:
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


@pytest.mark.parametrize("size", [(390, 844), (768, 1024), (1440, 900)], ids=lambda size: f"{size[0]}x{size[1]}")
@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("profile", ["subscription", "standard"])
def test_sidebar_context_uses_only_required_space(browser, servers, size, theme, profile):
    engine, instance = browser
    width, height = size
    url = servers[profile]
    allowed = urlsplit(url)
    context = instance.new_context(
        viewport={"width": width, "height": height}, color_scheme=theme, locale="pt-BR",
        has_touch=width == 390, is_mobile=engine == "webkit" and width == 390,
        service_workers="block")
    errors, external = [], []

    def restrict(route):
        target = urlsplit(route.request.url)
        if target.scheme in {"http", "https"} and (target.hostname, target.port) != (allowed.hostname, allowed.port):
            external.append(route.request.url)
            return route.abort()
        return route.continue_()

    context.route("**/*", restrict)
    context.add_init_script("localStorage.setItem('orion-theme', " + json.dumps(theme) + ");")
    page = context.new_page()
    page.on("pageerror", lambda error: errors.append(str(error)))
    phase = os.getenv("ORION_SIDEBAR_SCREENSHOT_PHASE", "after")
    output = ARTIFACTS / phase
    output.mkdir(parents=True, exist_ok=True)
    label = f"{profile}-{engine}-{width}x{height}-{theme}"
    try:
        page.goto(url + "/panel?fixtureRole=owner&fixturePlan=elite&fixtureDelay=1&aba=novidades")
        expect(page.locator(".os-news-list")).to_be_visible()
        workspace.open_navigation(page)
        page.evaluate("document.fonts.ready")
        panel = page.locator("#osSidebarContext")
        expect(panel).to_be_visible()
        measured = panel.evaluate("""el => {
          const style = getComputedStyle(el), rect = el.getBoundingClientRect();
          const visible = [...el.children].filter(child => child.getClientRects().length && getComputedStyle(child).display !== 'none');
          const last = visible[visible.length - 1].getBoundingClientRect();
          return {height:rect.height, width:rect.width, bottomGap:rect.bottom-last.bottom,
            paddingBottom:parseFloat(style.paddingBottom), borderBottom:parseFloat(style.borderBottomWidth),
            minHeight:style.minHeight, compact:el.classList.contains('os-sidebar-context--subscription'),
            viewport:innerWidth, scroll:document.documentElement.scrollWidth};
        }""")
        (output / f"{label}.json").write_text(json.dumps(measured, indent=2), encoding="utf-8")
        page.screenshot(path=str(output / f"{label}.png"), animations="disabled")
        assert measured["viewport"] == width and measured["scroll"] <= width + 1, measured
        assert not external, external
        assert not errors, errors
        if profile == "subscription":
            expect(panel.locator(".os-side-site")).to_have_count(0)
            assert measured["compact"], "space left for removed site shortcut"
            assert measured["minHeight"] == "0px", measured
            expected_gap = measured["paddingBottom"] + measured["borderBottom"]
            assert abs(measured["bottomGap"] - expected_gap) <= 1, measured
            assert measured["height"] < 112, measured
        else:
            expect(panel.locator(".os-side-site")).to_be_visible()
            assert not measured["compact"], measured
            assert measured["minHeight"] == "136px", measured
            baseline = ARTIFACTS / "before" / f"{label}.json"
            if phase != "before" and baseline.exists():
                previous = json.loads(baseline.read_text(encoding="utf-8"))
                for key in ("height", "width", "bottomGap", "paddingBottom", "borderBottom"):
                    assert abs(measured[key] - previous[key]) <= 1, (key, measured, previous)
    finally:
        context.close()
