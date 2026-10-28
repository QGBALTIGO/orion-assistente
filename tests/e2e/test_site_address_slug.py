"""Real browser forms with synthetic site persistence and ViaCEP responses."""
import os
from pathlib import Path
from urllib.parse import urlsplit

import pytest

playwright_api = pytest.importorskip(
    "playwright.sync_api", reason="Browser dependencies run in the dedicated UI job"
)
sync_playwright = playwright_api.sync_playwright
expect = playwright_api.expect
ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "app/static"
CSS = (STATIC / "orion-suite.css").read_text(encoding="utf-8")
JS = (STATIC / "orion-suite.js").read_text(encoding="utf-8")
FIELDS_CSS = (Path(__file__).resolve().parents[2] / "app/static/orion-fields.css").read_text(encoding="utf-8")
FIELDS_JS = (Path(__file__).resolve().parents[2] / "app/static/orion-fields.js").read_text(encoding="utf-8")
FIXTURE = (ROOT / "tests/e2e/fixtures/site_address_slug.js").read_text(encoding="utf-8")
ORIGIN = "https://orion-fixture.invalid"
HTML = """<!doctype html><html lang="pt-BR"><head>
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<style>*{box-sizing:border-box}html,body{margin:0;padding:0}.hidden{display:none!important}</style>
</head><body><div id="appView" class="app"><aside class="sidebar">
<div class="brand">Orion</div></aside><main class="content">
<section id="page-original" class="page">Original</section></main></div></body></html>"""


def serve_fixture(route):
    """Give the page a real origin and serve only its local font files."""
    path = urlsplit(route.request.url).path
    if path == "/":
        route.fulfill(status=200, content_type="text/html; charset=utf-8", body=HTML)
        return
    font = STATIC / Path(path).name
    if font.suffix == ".woff2" and font.is_file():
        route.fulfill(status=200, content_type="font/woff2", body=font.read_bytes())
        return
    route.fulfill(status=404, content_type="text/plain", body="No synthetic resource")


@pytest.mark.parametrize("browser_name,width,height,theme", [
    ("webkit", 320, 693, "dark"),
    ("webkit", 375, 812, "light"),
    ("webkit", 430, 932, "dark"),
    ("chromium", 360, 800, "light"),
    ("chromium", 768, 1024, "dark"),
    ("chromium", 1024, 768, "light"),
    ("chromium", 1366, 900, "dark"),
])
def test_site_address_slug(browser_name, width, height, theme):
    with sync_playwright() as playwright:
        launch = {"channel": "chrome"} if browser_name == "chromium" and os.getenv("ORION_TEST_SYSTEM_CHROME") == "1" else {}
        browser = getattr(playwright, browser_name).launch(**launch)
        context = browser.new_context(
            viewport={"width": width, "height": height}, device_scale_factor=1,
            is_mobile=width < 768, has_touch=width < 768,
        )
        page = context.new_page()
        page.set_default_timeout(10000)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        context.route(ORIGIN + "/**", serve_fixture)
        try:
            page.goto(ORIGIN + "/")
            page.evaluate("theme=>document.documentElement.dataset.theme=theme", theme)
            page.add_style_tag(content=CSS)
            page.add_style_tag(content=FIELDS_CSS)
            page.add_script_tag(content=FIELDS_JS)
            page.add_script_tag(content=JS)
            page.add_script_tag(content=FIXTURE)
            page.evaluate("mountSiteAddressFixture()")
            page.locator("[data-os-site-tab=settings]").click()
            form = page.locator("#osSiteForm")
            expect(form).to_be_visible()

            def field(name):
                return form.locator(f"[name={name}]")

            def no_horizontal_cut():
                boxes = page.locator(
                    "#osSiteForm,.os-site-config-card,.os-site-location,"
                    ".os-site-number-field,.os-site-slug-preview,#osSiteSlugSuggestions"
                ).evaluate_all("""nodes=>nodes.map(el=>{
                  const r=el.getBoundingClientRect();return {class:el.className,left:r.left,
                    right:r.right,width:r.width,scroll:el.scrollWidth,client:el.clientWidth};
                }).filter(box=>box.width>0)""")
                for box in boxes:
                    assert -2 <= box["left"] and box["right"] <= width + 2, box
                    assert box["scroll"] <= box["client"] + 2, box
                assert page.evaluate("document.documentElement.scrollWidth") <= width + 2

            def save(version):
                form.locator("button[type=submit]").click()
                expect(form).to_have_attribute("data-version", str(version))

            # A legacy address stays intact; loading the form performs no CEP request.
            expect(field("address")).to_have_value("Rua do Cadastro, 99")
            expect(field("address_number")).to_have_value("")
            assert page.evaluate("siteAddressFixture.cepCalls") == []
            no_horizontal_cut()

            # CEP fills geographic fields, while the user owns the house number/complement.
            field("address_number").fill("42A")
            field("address_complement").fill("Sala 2")
            field("postal_code").fill("01001000")
            expect(field("postal_code")).to_have_value("01001-000")
            expect(field("address")).to_have_value("Praça da Sé")
            expect(field("neighborhood")).to_have_value("Sé")
            expect(field("city")).to_have_value("São Paulo")
            expect(field("state")).to_have_value("SP")
            expect(field("address_number")).to_have_value("42A")
            expect(field("address_complement")).to_have_value("Sala 2")
            expect(page.locator("#osSiteCepStatus")).to_have_attribute("data-status", "found")

            # Typing remains natural, and a suggested address can be chosen by keyboard.
            field("slug").fill("Barbearía São José")
            expect(field("slug")).to_have_value("barbearia-sao-jose")
            field("slug").press("End")
            field("slug").press("-")
            expect(field("slug")).to_have_value("barbearia-sao-jose-")
            field("slug").press("Tab")
            expect(field("slug")).to_have_value("barbearia-sao-jose")
            suggestions = page.locator("#osSiteSlugSuggestions button[data-os-site-slug-suggestion]")
            expect(suggestions).to_have_count(3)
            no_horizontal_cut()
            suggestions.first.focus()
            page.keyboard.press("Enter")
            expect(field("slug")).to_have_value("barbearia-sao-jose-2")
            expect(page.locator("#osSiteSlugStatus")).to_have_attribute("data-status", "available")
            expect(page.locator("#osSiteSlugPreview")).to_have_text("/s/barbearia-sao-jose-2")
            assert page.evaluate("siteAddressFixture.calls.every(call=>call.method==='GET')")

            save(5)
            writes = page.evaluate("siteAddressFixture.calls.filter(call=>call.method==='PUT')")
            assert len(writes) == 1
            payload = writes[0]["data"]
            assert payload["address"] == "Praça da Sé"
            assert payload["address_number"] == "42A" and payload["address_no_number"] is False
            assert payload["address_complement"] == "Sala 2" and payload["neighborhood"] == "Sé"
            assert payload["slug"] == "barbearia-sao-jose-2"
            page.locator("[data-os-site-tab=dashboard]").click()
            page.locator("[data-os-site-tab=settings]").click()
            expect(field("address_number")).to_have_value("42A")
            expect(field("address_complement")).to_have_value("Sala 2")

            # Explicit "Sem número" is keyboard-accessible and survives the saved form.
            field("address_no_number").focus()
            page.keyboard.press("Space")
            expect(field("address_no_number")).to_be_checked()
            expect(field("address_number")).to_be_disabled()
            save(6)
            assert page.evaluate("siteAddressFixture.site.address_number") is None
            assert page.evaluate("siteAddressFixture.site.address_no_number") is True
            expect(field("address_no_number")).to_be_checked()

            # Network failure is visible and retry is explicit; no external request is made.
            field("postal_code").fill("88010000")
            expect(page.locator("#osSiteCepStatus")).to_have_attribute("data-status", "unknown")
            retry = form.locator("[data-os-action=site-cep-retry]")
            expect(retry).to_be_visible()
            retry.focus()
            page.keyboard.press("Enter")
            expect(field("city")).to_have_value("Florianópolis")
            expect(field("state")).to_have_value("SC")
            expect(page.locator("#osSiteCepStatus")).to_have_attribute("data-status", "found")

            # An unknown CEP never erases the address or blocks deliberate manual entry.
            field("postal_code").fill("99999999")
            expect(page.locator("#osSiteCepStatus")).to_have_attribute("data-status", "unknown")
            expect(field("address")).to_have_value("Rua Felipe Schmidt")
            field("address").fill("Rua preenchida manualmente")
            field("address_no_number").uncheck()
            field("address_number").fill("10")
            save(7)
            expect(field("address")).to_have_value("Rua preenchida manualmente")
            expect(field("address_number")).to_have_value("10")

            # Native form validation must allow Enter before blur normalizes the final hyphen.
            field("slug").fill("Meu Negócio ")
            expect(field("slug")).to_have_value("meu-negocio-")
            field("slug").press("Enter")
            expect(form).to_have_attribute("data-version", "8")
            expect(field("slug")).to_have_value("meu-negocio")
            assert page.evaluate("siteAddressFixture.site.slug") == "meu-negocio"
            no_horizontal_cut()
            calls = page.evaluate("siteAddressFixture.cepCalls")
            assert [call["cep"] for call in calls] == ["01001000", "88010000", "88010000", "99999999"]
            assert all(call["credentials"] == "omit" and call["referrerPolicy"] == "no-referrer" for call in calls)
            writes = page.evaluate("siteAddressFixture.calls.filter(call=>call.method!=='GET')")
            assert len(writes) == 4 and all(call["method"] == "PUT" and call["path"].endswith("/site") for call in writes)
            assert writes[-1]["data"]["slug"] == "meu-negocio"
            assert not errors, errors
        finally:
            context.close()
            browser.close()
