"""Synthetic real-browser checks for returning customer and catalogue caching."""
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
HTML = (ROOT / "app/static/orion-public.html").read_text(encoding="utf-8")
CSS = (ROOT / "app/static/orion-public.css").read_text(encoding="utf-8")
FIELDS_CSS = (ROOT / "app/static/orion-fields.css").read_text(encoding="utf-8")
THEME = (ROOT / "app/static/orion-public-theme.js").read_text(encoding="utf-8")
FIELDS = (ROOT / "app/static/orion-fields.js").read_text(encoding="utf-8")
JS = (ROOT / "app/static/orion-public.js").read_text(encoding="utf-8")
URL = "https://orion.test/s/empresa-sintetica"
SLUG = "empresa-sintetica"
PRO = "70000000-0000-4000-8000-000000000007"
SERVICE = "50000000-0000-4000-8000-000000000005"
CATALOGUE = {
    "timezone": "America/Campo_Grande", "today": "2030-01-07",
    "booking_horizon_days": 60, "opening_hours": [],
    "site": {"title": "Barbearia sintética", "theme_color": "#2e7bf6",
             "show_prices": True},
    "services": [{"id": SERVICE, "name": "Corte masculino",
                  "catalog_kind": "base", "price_cents": 4000,
                  "price_mode": "fixed", "duration_min": 40,
                  "professional_ids": [PRO]}],
    "professionals": [{"id": PRO, "name": "Guilherme"}],
}


def boot(page):
    page.goto(URL, wait_until="domcontentloaded")
    page.add_style_tag(content=CSS)
    page.add_style_tag(content=FIELDS_CSS)
    page.add_script_tag(content=THEME)
    page.add_script_tag(content=FIELDS)
    page.add_script_tag(content=JS)
    page.locator("#site-content").wait_for(state="visible")


def routes(page, state, posted):
    page.route("**/*", lambda route: route.abort())
    page.route(URL, lambda route: route.fulfill(status=200,
        content_type="text/html", body=HTML))

    def catalogue(route):
        if state["offline"]:
            route.abort()
        else:
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps(CATALOGUE))
    page.route(f"**/api/v1/public/sites/{SLUG}", catalogue)
    page.route(f"**/api/v1/public/sites/{SLUG}/availability?*",
        lambda route: route.fulfill(status=200,
            content_type="application/json",
            body=json.dumps({"slots": ["2030-01-07T10:30:00-04:00"],
                             "quote": "q" * 55, "price_cents": 4000,
                             "price_mode": "fixed", "duration_min": 40,
                             "services": ["Corte masculino"],
                             "timezone": "America/Campo_Grande"})))

    def appointment(route):
        posted.append(route.request.post_data_json)
        route.fulfill(status=201, content_type="application/json",
            body=json.dumps({"status": "confirmed", "protocol": "TESTE-1",
                             "start_at": "2030-01-07T10:30:00-04:00",
                             "end_at": "2030-01-07T11:10:00-04:00",
                             "price_cents": 4000, "professional": "Guilherme",
                             "services": ["Corte masculino"],
                             "timezone": "America/Campo_Grande"}))
    page.route(f"**/api/v1/public/sites/{SLUG}/appointments", appointment)


def enter_confirmation(page):
    page.locator("#hero-book").click()
    page.locator("#booking-service-list .service-card").first.click()
    page.locator("#booking-next").click()
    page.locator("#time-slots .slot").first.wait_for()
    page.locator("#time-slots .slot").first.click()
    page.locator("#booking-next").click()
    page.locator("#customer-form").wait_for()


def test_catalogue_is_fast_offline_but_never_allows_stale_booking():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(locale="pt-BR",
                                      viewport={"width": 390, "height": 820})
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        state, posted = {"offline": False}, []
        routes(page, state, posted)
        try:
            boot(page)
            assert page.locator("#hero-book").is_enabled()
            key = "orion:public:catalogue:v1:" + SLUG
            assert page.evaluate("(key) => !!localStorage.getItem(key)", key)
            state["offline"] = True
            boot(page)
            page.get_by_text("Não foi possível atualizar as informações",
                             exact=False).wait_for()
            assert page.locator("#services-grid .service-card").count() == 1
            assert page.locator("#hero-book").is_disabled()
            assert page.locator("#services-grid .service-card").first.is_disabled()
            assert not posted
            assert not errors, errors
        finally:
            browser.close()


def test_opt_in_customer_profile_restores_and_can_be_cleared():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(locale="pt-BR",
                                      viewport={"width": 390, "height": 820})
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        state, posted = {"offline": False}, []
        routes(page, state, posted)
        try:
            boot(page)
            enter_confirmation(page)
            form = page.locator("#customer-form")
            checkbox = form.locator('[name="remember_customer"]')
            assert not checkbox.is_checked()
            form.locator('[name="customer_name"]').fill("Cliente sintético")
            form.locator('[name="customer_phone"]').fill("86972676282")
            form.locator('[name="customer_birth_date"]').fill("1990-07-06")
            checkbox.check()
            page.locator("#booking-next").click()
            page.locator(".success").wait_for()
            assert len(posted) == 1
            key = "orion:public:customer:v1:" + SLUG
            stored = page.evaluate("(key) => JSON.parse(localStorage.getItem(key))", key)
            assert stored["value"] == {
                "name": "Cliente sintético",
                "phone": "+5586972676282",
                "birthDate": "1990-07-06",
            }
            boot(page)
            enter_confirmation(page)
            form = page.locator("#customer-form")
            assert form.locator('[name="customer_name"]').input_value() == "Cliente sintético"
            assert form.locator('[name="customer_birth_date"]').input_value() == "1990-07-06"
            assert form.locator('[name="remember_customer"]').is_checked()
            form.locator(".forget-customer").click()
            assert not form.locator('[name="remember_customer"]').is_checked()
            assert page.evaluate("(key) => localStorage.getItem(key)", key) is None
            assert not errors, errors
        finally:
            browser.close()
