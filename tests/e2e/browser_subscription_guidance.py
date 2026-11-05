"""Static WhatsApp guidance through real Orion assets, with every write denied.

The loopback fixture proves navigation, access boundaries and responsive layout.
Viewport emulation is not a physical-device test or a WhatsApp delivery test.
"""
from __future__ import annotations

from http.server import ThreadingHTTPServer
import json
import re
import threading
from urllib.parse import urlsplit

from playwright.sync_api import expect
import pytest

from browser_subscription_chatbot import ROOT, SIZES, assert_layout, browser, navigation  # noqa: F401
from browser_subscription_chatbot_simulator import setup_page
from browser_subscription_devices import DEVICE_BASE
from subscription_guidance_fixture import Handler, base

ARTIFACTS = ROOT / "test-artifacts" / "subscription-guidance"
BOOTSTRAP_PATHS = {
    "/auth/me", "/auth/features", "/api/v1/account/businesses",
    "/api/v1/onboarding/entry", "/api/v1/account/business-identity",
    base.BASE + "/management/permissions",
}
ARTICLE = 'article.os-help-article[data-os-guidance="warmup"]'


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


def report(page, fixture_url):
    value = page.request.get(fixture_url + "/__fixture/guidance-report").json()
    assert not value["unexpected"], value["unexpected"]
    assert all(call["method"] == "GET" for call in value["calls"]), value["calls"]
    return value


def record_requests(page):
    calls = []
    page.on("request", lambda request: calls.append(
        {"method": request.method, "path": urlsplit(request.url).path}))
    return calls


def save_evidence(label, browser, size, theme, snapshot, requests, external, errors):
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    (ARTIFACTS / (label + ".json")).write_text(json.dumps({
        "engine": browser[0], "viewport": size, "theme": theme,
        "fixture": snapshot, "requests": requests,
        "external_requests": external, "page_errors": errors,
        "limits": "Synthetic local data and viewport emulation; no real provider, device or delivery.",
    }, ensure_ascii=False, indent=2))


@pytest.mark.parametrize("size", SIZES, ids=lambda size: f"{size[0]}x{size[1]}")
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_warmup_article_layout_and_read_only_navigation(browser, fixture_url, size, theme):
    context, page, external, errors = setup_page(browser, fixture_url, size, theme)
    requests, snapshot = record_requests(page), {}
    label = f"{browser[0]}-{size[0]}x{size[1]}-{theme}"
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    try:
        page.goto(fixture_url + "/panel?fixtureRole=owner&fixturePlan=elite&aba=aquecimento")
        expect(page.locator("#page-suite")).to_have_attribute("data-os-current-route", "aquecimento")
        article = page.locator(ARTICLE)
        expect(article).to_be_visible()
        expect(article.get_by_role("heading", name="Aquecimento", exact=True)).to_be_visible()
        expect(page.locator('#osNav [data-os-route="aquecimento"]')).to_have_count(1)
        expect(article.locator("[data-os-guidance-step]")).to_have_count(4)
        for number in range(1, 5):
            expect(article.locator(f'[data-os-guidance-step="{number}"] h2')).to_have_text(
                re.compile(rf"^{number}\. \S"))
        table = article.locator("table[data-os-warmup-example]")
        expect(table.locator("caption")).to_have_text("Exemplo ilustrativo de progressão manual")
        expect(table.locator("thead th")).to_have_text(["Dia", "Contatos"])
        assert table.locator("tbody tr").evaluate_all(
            "rows => rows.map(row => [...row.cells].map(cell => cell.textContent.trim()))"
        ) == [[str(day), str(volume)] for day, volume in enumerate([10, 20, 50, 80, 100], 1)]
        expect(article.locator("#osWarmupExampleNote")).to_contain_text(
            "não são limites oficiais nem metas obrigatórias")
        expect(article.locator("#osWarmupExampleNote")).to_contain_text("não garantem entrega")
        expect(article.get_by_role("heading", name="Erros a evitar", exact=True)).to_have_count(1)
        expect(article.get_by_role("heading", name="Quando interromper e revisar", exact=True)).to_have_count(1)
        official_links = article.locator('[aria-labelledby="osWarmupOfficial"] a')
        assert official_links.evaluate_all("nodes => nodes.map(node => ({href: node.href, target: node.target, rel: node.rel}))") == [
            {"href": "https://whatsappbusiness.com/policy/", "target": "_blank", "rel": "noopener noreferrer"},
            {"href": "https://www.whatsapp.com/legal/messaging-guidelines", "target": "_blank", "rel": "noopener noreferrer"},
        ]
        expect(article.locator("form, input, textarea, select")).to_have_count(0)
        page.evaluate("document.fonts.ready")
        assert_layout(page, size[0])
        assert table.evaluate("node => node.getBoundingClientRect().width <= innerWidth"), "Example table exceeds viewport"
        page.screenshot(path=str(ARTIFACTS / (label + ".png")), full_page=True, animations="disabled")
        snapshot = report(page, fixture_url)
        assert all(call["path"] in BOOTSTRAP_PATHS for call in snapshot["calls"]), snapshot

        # Exercise the real internal CTA on mobile and desktop without repeating
        # a device-connection workflow: the empty device list is only read.
        if theme == "light" and size[0] in {390, 1440}:
            article.locator('button[data-os-route="dispositivos"]').click()
            expect(page.locator("#page-suite")).to_have_attribute("data-os-current-route", "dispositivos")
            expect(page.locator(".os-devices-usage .os-badge")).to_have_text("0 de 1")
            assert_layout(page, size[0])
            navigation(page)
            page.locator('#osNav [data-os-route="aquecimento"]').click()
            expect(page.locator(ARTICLE)).to_be_visible()
            expect(page.locator("#page-suite")).to_have_attribute("data-os-current-route", "aquecimento")
            snapshot = report(page, fixture_url)
            assert any(call["path"] == DEVICE_BASE for call in snapshot["calls"]), snapshot
            assert all(call["path"] in BOOTSTRAP_PATHS | {DEVICE_BASE} for call in snapshot["calls"]), snapshot
        assert all(call["method"] == "GET" for call in requests), requests
        assert not external, external
        assert not errors, errors
    finally:
        save_evidence(label, browser, size, theme, snapshot, requests, external, errors)
        context.close()


@pytest.mark.parametrize("case,query,profile,active,granted", [
    ("standard-with-stale-grant", "&fixtureProfile=standard", "standard", True, True),
    ("missing-feature", "&fixtureGuidance=0", "subscription_services", True, False),
    ("inactive-with-stale-grant", "&fixtureInactive=1", "subscription_services", False, True),
])
def test_warmup_deep_link_requires_active_subscription_workspace_and_feature(
    browser, fixture_url, case, query, profile, active, granted,
):
    size, theme = (1440, 900), "light"
    context, page, external, errors = setup_page(browser, fixture_url, size, theme)
    requests, snapshot = record_requests(page), {}
    try:
        page.goto(fixture_url + "/panel?fixtureRole=owner&fixturePlan=elite&aba=aquecimento" + query)
        expect(page.locator("#page-suite")).to_have_attribute(
            "data-os-current-route", "plano" if profile == "standard" else "novidades")
        expect(page.locator(ARTICLE)).to_have_count(0)
        expect(page.locator('#osNav [data-os-route="aquecimento"]')).to_have_count(0)
        snapshot = report(page, fixture_url)
        assert snapshot["state"] == {"profile": profile, "active": active,
                                     "feature_granted": granted, "role": "owner"}
        assert not any("/subscription-whatsapp/" in call["path"] for call in snapshot["calls"]), snapshot
        assert all(call["method"] == "GET" for call in requests), requests
        assert not external, external
        assert not errors, errors
    finally:
        save_evidence(f"{browser[0]}-{case}", browser, size, theme, snapshot, requests, external, errors)
        context.close()
