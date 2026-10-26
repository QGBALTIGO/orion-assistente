"""Responsive, non-networked tests of public-site icon actions in real browser engines."""
import json
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
HTML = (ROOT / "app/static/orion-public.html").read_text(encoding="utf-8")
CSS = (ROOT / "app/static/orion-public.css").read_text(encoding="utf-8")
SCRIPT = (ROOT / "app/static/orion-public.js").read_text(encoding="utf-8")
URL = "https://orion.test/s/empresa-sintetica"
DATA = {
    "timezone": "America/Sao_Paulo",
    "today": "2030-01-07",
    "booking_horizon_days": 60,
    "site": {
        "title": "Barbearia de teste", "theme_color": "#ffca00",
        "show_prices": True, "hide_orion_brand": False,
        "address": "Rua de Teste", "city": "Cidade de teste", "state": "MG",
        "maps_url": "https://www.google.com/maps?cid=123",
        "description": "Agendamentos de teste", "whatsapp_phone": "+5511999999999"
    },
    "services": [
        {"id": "50000000-0000-4000-8000-000000000005",
         "name": "Corte", "catalog_kind": "base", "category": "Serviços",
         "duration_min": 40, "price_cents": 4000, "professional_ids":
             ["70000000-0000-4000-8000-000000000007"]}
    ],
    "professionals": [
        {"id": "70000000-0000-4000-8000-000000000007",
         "name": "Barbeiro", "specialty": "Cortes"}
    ],
}


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
@pytest.mark.parametrize("width", [320, 375, 390, 768, 1024])
def test_hero_icons_align_and_share_opens_native_api_without_booking(engine, width):
    with sync_playwright() as playwright:
        browser = getattr(playwright, engine).launch()
        page = browser.new_page(
            viewport={"width": width, "height": 820},
            device_scale_factor=2, locale="pt-BR",
            is_mobile=(engine == "webkit" and width <= 390), has_touch=True,
        )
        try:
            # Deny all other network requests; the latest matching route
            # wins, so install the catch-all BEFORE the two fixtures.
            page.route("**/*", lambda route: route.abort())
            page.route(URL, lambda route: route.fulfill(
                status=200, content_type="text/html", body=HTML))
            page.route("**/api/v1/public/sites/empresa-sintetica", lambda route: route.fulfill(
                status=200, content_type="application/json", body=json.dumps(DATA)))
            page.goto(URL, wait_until="domcontentloaded")
            page.add_style_tag(content=CSS)
            page.add_script_tag(content=SCRIPT)
            page.locator("#site-content").wait_for(state="visible", timeout=10000)
            book = page.locator("#hero-book")
            map_link = page.locator("#hero-map")
            share = page.locator("#share-site")
            row = page.locator(".hero-actions")
            assert book.is_visible() and map_link.is_visible() and share.is_visible()
            assert map_link.get_attribute("href") == DATA["site"]["maps_url"]
            assert map_link.get_attribute("target") == "_blank"
            assert "noopener" in map_link.get_attribute("rel")
            for selector in ["#hero-map", "#share-site"]:
                a = page.locator(selector)
                glyph = a.locator("svg.hero-action-icon")
                assert glyph.count() == 1
                assert glyph.get_attribute("stroke-width") == "1.9"
                dims = a.bounding_box()
                assert dims is not None and dims["height"] >= 44
                stroke_box = glyph.bounding_box()
                assert stroke_box and 18 <= stroke_box["width"] <= 21
                border = a.evaluate("(el)=>getComputedStyle(el).borderRadius")
                assert border == "14px", (selector, border)
            if width < 640:
                assert not page.locator("#hero-map .hero-action-label").is_visible()
                assert not page.locator("#share-site .hero-action-label").is_visible()
                assert 43 <= map_link.bounding_box()["width"] <= 47
                assert 43 <= share.bounding_box()["width"] <= 47
            else:
                assert page.locator("#hero-map .hero-action-label").is_visible()
                assert page.locator("#share-site .hero-action-label").is_visible()
            bounds = [x.bounding_box() for x in [row, book, map_link, share]]
            whole, booking, location, sharing = bounds
            assert all(bounds)
            assert booking["x"] >= whole["x"] - 2
            assert booking["x"] + booking["width"] <= location["x"] + 2
            assert location["x"] + location["width"] <= sharing["x"] + 2
            assert sharing["x"] + sharing["width"] <= whole["x"] + whole["width"] + 2
            service_meta = page.locator("#services-grid .service-card .service-meta").first
            assert service_meta.locator(".price").count() == 1
            assert service_meta.locator(".service-meta-separator").inner_text() == "•"
            assert service_meta.locator(".service-duration").inner_text() == "40min"
            assert service_meta.evaluate(
                "(el)=>Array.from(el.children).map(x=>x.className)"
            ) == ["price", "service-meta-separator", "service-duration"]
            assert service_meta.inner_text().find("R$") < service_meta.inner_text().find("40min")
            assert page.locator("#maps-link svg.inline-action-icon").count() == 1
            assert page.locator(".location-mark svg.location-icon").count() == 1
            assert page.locator("#hero-book svg.hero-book-icon").count() == 1
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 2"), "no horizontal overflow"

            page.evaluate("""() => {
              window.__shareArgs = null;
              Object.defineProperty(navigator,'share',{
                configurable:true,
                value:async args => {window.__shareArgs=args;}
              });
            }""")
            share.click()
            page.wait_for_function("window.__shareArgs !== null", timeout=4000)
            received = page.evaluate("window.__shareArgs")
            assert received == {"title": DATA["site"]["title"], "url": URL}
            assert not page.locator("#booking-dialog").evaluate("(el)=>el.open")
        finally:
            browser.close()
