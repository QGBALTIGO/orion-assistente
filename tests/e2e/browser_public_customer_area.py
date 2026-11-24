"""Real Chromium and WebKit exercises using only synthetic customer data."""
import json
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

from browser_public_cache_profile import boot, routes, SLUG

ROOT = Path(__file__).resolve().parents[2]
CSS = (ROOT / "app/static/orion-customer-area.css").read_text(encoding="utf-8")
JS = (ROOT / "app/static/orion-customer-area.js").read_text(encoding="utf-8")
PHONE = "+5586972676282"
PROFILE = {"name": "Cliente Sintético", "phone": PHONE, "birth_date": "1990-07-06"}
ITEM = {
    "status": "confirmed", "start_at": "2030-01-07T10:30:00-04:00",
    "end_at": "2030-01-07T11:10:00-04:00",
    "services": ["Corte masculino"], "professional": "Guilherme", "duration_min": 40,
    "price_cents": 4000, "price_mode": "fixed", "timezone": "America/Campo_Grande",
}
DASHBOARD = {
    "available": True, "authenticated": True, "profile": PROFILE,
    "stats": {"appointments": 3, "completed": 1, "cancelled": 1,
              "no_show": 0, "completed_cents": 4000, "last_visit": "2029-12-01T12:00:00Z"},
    "upcoming": {"items": [ITEM], "has_more": False, "next_offset": 1},
    "timezone": "America/Campo_Grande",
}


def boot_portal(page):
    boot(page)
    page.add_style_tag(content=CSS)
    page.add_script_tag(content=JS)
    expect(page.locator("#customer-area-trigger")).to_be_visible()


@pytest.mark.parametrize("browser_name,width,theme", [
    ("chromium", 390, "light"), ("webkit", 390, "dark"),
    ("chromium", 1280, "dark"),
])
@pytest.mark.parametrize("login_method", ["birth_date", "private_link"])
def test_account_flow(browser_name, width, theme, login_method):
    with sync_playwright() as playwright:
        browser = getattr(playwright, browser_name).launch(headless=True)
        context = browser.new_context(locale="pt-BR",
            viewport={"width": width, "height": 820}, color_scheme=theme)
        page = context.new_page()
        errors, calls = [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        state, posted = {"offline": False}, []
        session = {"valid": False}
        authenticated = {**DASHBOARD}
        anonymous = {"available":True,"authenticated":False}
        if login_method == "birth_date":
            authenticated["login_method"] = "birth_date"
            anonymous["login_method"] = "birth_date"
        routes(page, state, posted)
        def account(route):
            request = route.request
            path = request.url.split("/account", 1)[1]
            payload = request.post_data_json if request.method == "POST" and request.post_data else {}
            calls.append((request.method, path, payload))
            if path == "":
                value = authenticated if session["valid"] else anonymous
            elif path == "/enter":
                assert payload["phone"] == PHONE
                if login_method == "birth_date":
                    assert payload["birth_date"] == "1990-07-06" and payload["remember"] is True
                    session["valid"] = True
                    value = authenticated
                else:
                    value = {"authenticated": False, "proof_required": True}
            elif path == "/link-login":
                assert login_method == "private_link"
                assert payload["phone"] == PHONE and payload["remember"] is True
                session["valid"] = True
                value = authenticated
            elif path.startswith("/appointments?"):
                assert session["valid"]
                items = [{**ITEM,"status":"completed"}, {**ITEM,"status":"cancelled"}, ITEM]
                if "status=cancelled" in path:
                    items = [items[1]]
                value = {"items":items,"has_more":False,"next_offset":len(items)}
            elif path == "/logout":
                session["valid"] = False
                value = {"authenticated": False}
            else:
                pytest.fail("Unexpected portal request: " + path)
            route.fulfill(status=200, content_type="application/json", body=json.dumps(value))
        page.route(f"**/api/v1/public/sites/{SLUG}/account**", account)
        try:
            boot_portal(page)
            assert not calls, "Opening the public page must not request private account data"
            trigger = page.locator("#customer-area-trigger")
            dialog = page.locator("#customer-area-dialog")
            expect(page.locator("#public-toolbar-brand")).to_be_visible()
            trigger.click()
            expect(dialog).to_be_visible()
            expect(dialog.get_by_text("Acesse sua área do cliente")).to_be_visible()
            expect(dialog.get_by_text(PROFILE["name"], exact=True)).to_have_count(0)
            dialog.locator('input[name="phone"]').fill("86972676282")
            if login_method == "birth_date":
                expect(dialog.locator('input[name="birth_date"]')).to_be_visible()
                expect(dialog.locator('input[name="booking_link"]')).to_have_count(0)
                dialog.locator('input[name="birth_date"]').fill("1990-07-06")
                dialog.get_by_role("button", name="Acessar minha conta").click()
            else:
                dialog.get_by_role("button", name="Continuar").click()
                expect(dialog.get_by_text("Primeiro acesso neste aparelho")).to_be_visible()
                expect(dialog.get_by_text("Não enviamos SMS", exact=False)).to_be_visible()
                assert not session["valid"]
                dialog.locator('input[name="booking_link"]').fill(
                    "https://orionagenda.pro/AG-ABCDEF#" + "a"*27)
                dialog.get_by_role("button", name="Acessar minha conta").click()
            expect(dialog.get_by_text("Olá, Cliente!")).to_be_visible()
            expect(dialog.get_by_text("Total em serviços realizados")).to_be_visible()
            expect(dialog.get_by_text("Próximos agendamentos")).to_be_visible()
            expect(dialog.get_by_text("Corte masculino")).to_be_visible()
            assert dialog.evaluate("(node) => node.scrollWidth <= node.clientWidth + 1")
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
            dialog.get_by_role("button",name="Histórico",exact=True).click()
            expect(dialog.locator(".customer-area-card")).to_have_count(3)
            dialog.get_by_role("button",name="Cancelados",exact=True).click()
            expect(dialog.locator(".customer-area-card")).to_have_count(1)
            expect(dialog.locator(".customer-area-status")).to_have_text("Cancelado")
            dialog.get_by_role("button",name="Meus dados",exact=True).click()
            expect(dialog.get_by_text("Cliente Sintético",exact=True)).to_be_visible()
            expect(dialog.get_by_text("06/07/1990",exact=True)).to_be_visible()
            storage = page.evaluate("JSON.stringify(localStorage)")
            assert PROFILE["name"] not in storage and PROFILE["birth_date"] not in storage
            dialog.locator("#customer-area-close").click()
            expect(dialog).not_to_be_visible()
            expect(page.locator("#customer-area-body")).to_be_empty()
            boot_portal(page)
            trigger.click()
            expect(dialog.get_by_text("Olá, Cliente!")).to_be_visible()
            dialog.get_by_role("button",name="Meus dados",exact=True).click()
            dialog.get_by_role("button",name="Sair da conta").click()
            expect(dialog.get_by_text("Acesse sua área do cliente")).to_be_visible()
            assert not session["valid"]
            if login_method == "birth_date":
                expect(dialog.locator('input[name="birth_date"]')).to_be_visible()
                assert not any(path == "/link-login" for _,path,_ in calls)
            assert not posted and not errors, (posted, errors)
        finally:
            context.close()
            browser.close()
