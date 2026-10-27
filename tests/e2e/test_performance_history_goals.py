"""Real layout and form flows with synthetic performance data, on mobile and desktop."""
import os
from pathlib import Path

import pytest

sync_playwright = pytest.importorskip("playwright.sync_api", reason="Browser dependencies run in the dedicated UI job").sync_playwright

ROOT = Path(__file__).resolve().parents[2]
CSS = (ROOT / "app/static/orion-suite.css").read_text(encoding="utf-8")
JS = (ROOT / "app/static/orion-suite.js").read_text(encoding="utf-8")
FIXTURE = (ROOT / "tests/e2e/fixtures/performance_history_goals.js").read_text(encoding="utf-8")
HTML = """<!doctype html><html lang="pt-BR" data-theme="dark"><head>
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<style>*{box-sizing:border-box}html,body{margin:0;padding:0}.hidden{display:none!important}</style></head>
<body><div id="appView" class="app"><aside class="sidebar"><div class="brand">Orion</div></aside>
<main class="content"><section id="page-original" class="page">Original</section></main></div></body></html>"""


@pytest.mark.parametrize("browser_name,width,height,theme", [
    ("webkit", 320, 693, "dark"),
    ("webkit", 375, 812, "dark"),
    ("webkit", 430, 932, "light"),
    ("chromium", 360, 800, "light"),
    ("chromium", 768, 1024, "dark"),
    ("chromium", 1024, 768, "light"),
    ("chromium", 1366, 900, "dark"),
])
def test_performance_history_goals(browser_name, width, height, theme):
    with sync_playwright() as playwright:
        engine = getattr(playwright, browser_name)
        launch = {"channel": "chrome"} if browser_name == "chromium" and os.getenv("ORION_TEST_SYSTEM_CHROME") == "1" else {}
        browser = engine.launch(**launch)
        context = browser.new_context(viewport={"width": width, "height": height}, device_scale_factor=1,
                                      is_mobile=width < 768, has_touch=width < 768)
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            page.goto("about:blank")
            page.set_content(HTML)
            page.evaluate("theme=>document.documentElement.dataset.theme=theme", theme)
            page.add_style_tag(content=CSS)
            page.add_script_tag(content="if(!crypto.randomUUID)crypto.randomUUID=()=>String(Math.random()).slice(2)+'-fixture';")
            page.add_script_tag(content=JS)
            page.add_script_tag(content=FIXTURE)
            page.evaluate("mountPerformanceFixture()")
            page.wait_for_selector("[data-os-performance-history]")

            def no_horizontal_cut(selector):
                boxes = page.locator(selector).evaluate_all("""nodes=>nodes.map(el=>{
                  const r=el.getBoundingClientRect();return {class:el.className,left:r.left,right:r.right,
                    width:r.width,scroll:el.scrollWidth,client:el.clientWidth};})""")
                for box in boxes:
                    assert -2 <= box["left"] and box["right"] <= width + 2, box
                    assert box["scroll"] <= box["client"] + 2, box

            page.locator(".os-professional-results").scroll_into_view_if_needed()
            assert page.locator("[data-os-performance-history]").count() == 3
            button = page.locator("[data-os-performance-history]").first
            box = button.bounding_box()
            assert box["width"] >= 40 and box["height"] >= 40
            assert box["x"] >= 0 and box["x"] + box["width"] <= width
            button.click()
            page.wait_for_selector(".os-history-item")
            assert "Histórico · Peixoto" in page.locator("#osDialogTitle").inner_text()
            assert "Kayky Sousa" in page.locator(".os-history-item").first.inner_text()
            assert "09:00 – 10:00" in page.locator(".os-history-item").first.inner_text()
            no_horizontal_cut("#osDialog,.os-history-item,.os-history-item-heading,.os-history-amount")
            page.locator(".os-history-details summary").first.click()
            assert page.locator(".os-history-details[open]").count() == 1
            assert "AG-EXEMPLO1" in page.locator(".os-history-details[open]").inner_text()
            no_horizontal_cut(".os-history-details[open],.os-history-details[open] dl,.os-history-details[open] li")
            page.select_option("#osProfessionalHistoryStatus", "cancelled")
            page.wait_for_function("document.querySelectorAll('.os-history-item').length===5")
            assert all("Cancelado pelo cliente" in text for text in page.locator(".os-history-item").all_inner_texts())
            page.locator(".os-history-details summary").first.click()
            assert "imprevisto" in page.locator(".os-history-details[open]").inner_text()
            page.select_option("#osProfessionalHistoryStatus", "")
            page.wait_for_function("document.querySelectorAll('.os-history-item').length===25")
            page.locator("[data-os-action=professional-history-next]").click()
            page.wait_for_function("document.querySelectorAll('.os-history-item').length===1")
            assert "26–26 de 26" in page.locator(".os-history-pagination").inner_text()
            no_horizontal_cut(".os-history-pagination")
            page.locator("#osDialog [data-os-action=close-dialog]").click()
            assert page.locator("#osDialog").is_visible() is False

            page.locator("[data-os-action=new-goal]").click()
            page.select_option("#osField-scope", "company")
            assert page.locator("[data-os-goal-professional]").is_visible() is False
            assert page.locator("#osField-start_date").count() == 0
            page.select_option("#osField-metric", "appointments")
            page.fill("#osField-target", "100")
            assert "No dia 1º" in page.locator(".os-goal-monthly-info").inner_text()
            no_horizontal_cut("#osDialog,[data-os-form=goal],.os-goal-monthly-info")
            page.locator("[data-os-form=goal] [type=submit]").click()
            page.wait_for_function("!document.querySelector('#osDialog').open")
            writes = page.evaluate("performanceFixtureCalls.filter(c=>c.method!=='GET')")
            assert len(writes) == 1 and writes[0]["data"]["scope"] == "company"
            assert writes[0]["data"]["professional_id"] is None
            assert writes[0]["data"]["recurrence"] == "monthly"
            assert writes[0]["data"]["target_value"] == 100
            assert "start_date" not in writes[0]["data"]
            page.locator(".os-performance-goals").scroll_into_view_if_needed()
            no_horizontal_cut(".os-performance-goals,.os-goal-card")
            assert not errors, errors
        finally:
            context.close()
            browser.close()
