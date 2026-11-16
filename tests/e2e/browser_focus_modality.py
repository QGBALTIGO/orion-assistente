"""Browser regression for Orion's focus treatment on taps versus keyboard.

Exercises real Chromium and WebKit at phone, tablet and desktop widths.
It never requests production data and does not modify focus semantics.
"""
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
FOCUS_JS = (ROOT / "app/static/orion-focus.js").read_text(encoding="utf-8")
FOCUS_CSS = (ROOT / "app/static/orion-focus.css").read_text(encoding="utf-8")
PUBLIC_CSS = (ROOT / "app/static/orion-public.css").read_text(encoding="utf-8")
PANEL_CSS = (ROOT / "app/static/orion-suite.css").read_text(encoding="utf-8")


def css(page, selector):
    return page.locator(selector).evaluate("""element => ({
      outline: getComputedStyle(element).outlineStyle,
      border: getComputedStyle(element).borderColor,
      focusVisible: element.matches(':focus-visible'),
      active: document.activeElement === element
    })""")


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
@pytest.mark.parametrize("width", [320, 390, 768, 1280])
def test_public_booking_close_has_no_touch_ring_but_preserves_keyboard(engine, width):
    with sync_playwright() as playwright:
        browser = getattr(playwright, engine).launch()
        page = browser.new_page(
            viewport={"width": width, "height": 850},
            device_scale_factor=2, has_touch=True,
            is_mobile=(engine == "webkit" and width <= 390),
        )
        try:
            page.set_content("""<!doctype html><html lang="pt-BR"><body>
              <main><div class="booking-header"><button class="icon-button"
                type="button" id="booking-close">×</button></div>
              <a href="#after" id="keyboard-link">Continuar</a>
              <div class="booking-date-control" id="date-shell"><input
                type="date" id="date" aria-label="Data"></div>
              <button class="service-card" aria-pressed="true"
                id="selected-service">Corte masculino</button>
            </main></body></html>""")
            page.add_style_tag(content=PUBLIC_CSS)
            page.add_style_tag(content=FOCUS_CSS)
            page.add_script_tag(content=FOCUS_JS)
            page.evaluate("document.documentElement.style.setProperty('--primary','#fdc700')")
            page.locator("#booking-close").tap()
            assert page.evaluate("document.documentElement.dataset.orionInput") == "pointer"
            page.locator("#booking-close").focus()
            state = css(page, "#booking-close")
            assert state["active"] is True
            assert state["outline"] == "none", state
            # The selected service's border must not be lost with its outline.
            before = css(page, "#selected-service")["border"]
            page.locator("#selected-service").tap()
            page.locator("#selected-service").focus()
            assert css(page, "#selected-service")["border"] == before

            page.locator("#date").tap()
            assert page.locator("#date-shell").evaluate(
                "el => getComputedStyle(el).outlineStyle"
            ) == "none"
            assert page.locator("#date").evaluate(
                "el => getComputedStyle(el).outlineStyle"
            ) == "none", "The invisible date input must not paint a second ring"

            page.keyboard.press("Tab")
            assert page.evaluate("document.documentElement.dataset.orionInput") == "keyboard"
            page.locator("#keyboard-link").focus()
            keyboard = css(page, "#keyboard-link")
            if keyboard["focusVisible"]:
                assert keyboard["outline"] != "none", keyboard
        finally:
            browser.close()


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
@pytest.mark.parametrize("width", [320, 390, 768, 1280])
def test_panel_dialog_heading_keeps_programmatic_focus_without_blue_touch_box(engine, width):
    with sync_playwright() as playwright:
        browser = getattr(playwright, engine).launch()
        page = browser.new_page(
            viewport={"width": width, "height": 850}, has_touch=True,
            is_mobile=(engine == "webkit" and width <= 390),
        )
        try:
            page.set_content("""<!doctype html><html lang="pt-BR"><body>
              <div class="os-appointment-sheet-header">
                <div><h2 id="osDialogTitle" tabindex="-1">
                  Detalhes do agendamento</h2></div>
                <button id="detail-close" class="os-close" type="button">×</button>
              </div>
              <section class="os-main"><input id="edit" type="text"
                aria-label="Nome do cliente"></section>
            </body></html>""")
            page.add_style_tag(content=PANEL_CSS)
            page.add_style_tag(content=FOCUS_CSS)
            page.add_script_tag(content=FOCUS_JS)
            page.locator("#detail-close").tap()
            page.locator("#osDialogTitle").focus()
            title = css(page, "#osDialogTitle")
            assert title["active"] is True, "Focus must remain available to screen readers"
            assert title["outline"] == "none", title
            page.locator("#detail-close").focus()
            assert css(page, "#detail-close")["outline"] == "none"
            page.keyboard.press("Tab")
            assert page.evaluate("document.documentElement.dataset.orionInput") == "keyboard"
            page.locator("#detail-close").focus()
            keyboard = css(page, "#detail-close")
            if keyboard["focusVisible"]:
                assert keyboard["outline"] != "none", keyboard
            page.locator("#edit").tap()
            shadow = page.locator("#edit").evaluate(
                "el => getComputedStyle(el).boxShadow"
            )
            assert shadow != "none", "Input editing focus should remain visible"
        finally:
            browser.close()
