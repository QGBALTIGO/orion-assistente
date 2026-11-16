"""Real browser qualification of the existing panel and public booking assets.

HTTP responses are synthetic and local to the browser route. PostgreSQL rules
are exercised separately by test_public_manual_start_times.py. No production
session, provider credential, real booking or external message is used here.

Run with the runner-only tests/e2e/requirements.txt dependencies and either
ORION_UI_BROWSER=chromium or ORION_UI_BROWSER=webkit. An optional local Chromium
binary may be supplied through ORION_UI_EXECUTABLE_PATH.
"""
from copy import deepcopy
import os
from pathlib import Path
import re
from urllib.parse import parse_qs, urlsplit

import pytest

playwright_api = pytest.importorskip(
    "playwright.sync_api", reason="Browser dependencies run in the dedicated UI job"
)
expect = playwright_api.expect
sync_playwright = playwright_api.sync_playwright


ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "app" / "static"
ARTIFACTS = ROOT / "test-results" / "manual-start-times"
TENANT = "10000000-0000-4000-8000-000000000001"
PRO = "51000000-0000-4000-8000-000000000001"
SECOND_PRO = "51000000-0000-4000-8000-000000000009"
HOUR = "51000000-0000-4000-8000-000000000002"
SERVICE = "50000000-0000-4000-8000-000000000005"
COMBO = "50000000-0000-4000-8000-000000000006"
DAY = "2030-01-07"
NEXT_DAY = "2030-01-08"
QUOTE = "synthetic-manual-start-quote-not-a-real-booking-token-0001"
MANUAL_CUT = ["08:00", "08:40", "09:20", "11:20", "13:00", "13:40", "17:20"]
MANUAL_COMBO = ["08:00", "08:40", "09:20", "13:00", "13:40"]
CASES = [(width, theme) for width in (320, 375, 768, 1366) for theme in ("light", "dark")]


@pytest.fixture(scope="module")
def manual_ui_browser():
    name = os.getenv("ORION_UI_BROWSER", "chromium")
    if name not in {"chromium", "webkit"}:
        pytest.fail("ORION_UI_BROWSER must be chromium or webkit")
    executable = os.getenv("ORION_UI_EXECUTABLE_PATH")
    if executable and name != "chromium":
        pytest.fail("The local executable override is reserved for Chromium")
    options = {"headless": True}
    if executable:
        options.update(executable_path=executable,
                       args=["--disable-gpu", "--disable-software-rasterizer", "--no-zygote"])
    with sync_playwright() as manager:
        browser = getattr(manager, name).launch(**options)
        yield browser, name
        browser.close()


def asset(route):
    path = urlsplit(route.request.url).path
    if path.startswith(("/public-assets/", "/brand/")):
        source = STATIC / Path(path).name
        if source.is_file():
            content_type = {".js": "application/javascript", ".css": "text/css",
                            ".woff2": "font/woff2", ".svg": "image/svg+xml"}.get(source.suffix)
            route.fulfill(status=200, body=source.read_bytes(), content_type=content_type)
            return True
    return False


def snapshot(page, engine, name, width, theme):
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(ARTIFACTS / f"{engine}-{name}-{width}-{theme}.png"))


def assert_fits(page, selector):
    dimensions = page.locator(selector).evaluate("""element => ({
      viewport: innerWidth,
      document: document.documentElement.scrollWidth,
      client: element.clientWidth,
      scroll: element.scrollWidth,
      left: element.getBoundingClientRect().left,
      right: element.getBoundingClientRect().right
    })""")
    assert dimensions["document"] <= dimensions["viewport"] + 1, dimensions
    assert dimensions["scroll"] <= dimensions["client"] + 1, dimensions
    assert dimensions["left"] >= -1 and dimensions["right"] <= dimensions["viewport"] + 1, dimensions


def panel_routes(page, theme):
    panel = (STATIC / "panel.html").read_text()
    base_styles = "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", panel, re.S))
    html = (f'<!doctype html><html lang="pt-BR" data-theme="{theme}"><head>'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<style>{base_styles}</style><link rel="stylesheet" href="/brand/orion-suite.css">'
            '<link rel="stylesheet" href="/brand/orion-fields.css"></head><body>'
            '<div id="appView" class="app"><aside class="sidebar"><div class="brand">Orion</div></aside>'
            '<main class="content"><section id="page-original" class="page"></section></main></div></body></html>')
    policies = [{"weekday": day, "mode": "automatic", "start_times": [], "version": 0} for day in range(7)]
    writes = []
    features = ["workspace.access", "dashboard.basic", "agenda.manage", "clients.manage",
                "performance.advanced", "services.manage", "professionals.manage", "finance.basic",
                "site.manage", "notifications.basic", "settings.manage", "calendar.manage",
                "plan.manage", "account.manage", "business.setup"]
    plan = {"code": "essencial", "name": "Essencial", "source": "manual", "status": "active",
            "active": True, "version": 1, "revision": "synthetic-ui", "features": features,
            "permissions": {key: True for key in features},
            "limits": {"max_professionals": 2, "active_professionals": 1, "excess_professionals": 0}}

    def route(request):
        path = urlsplit(request.request.url).path
        if asset(request):
            return
        if path == "/panel":
            request.fulfill(status=200, body=html, content_type="text/html")
            return
        result = []
        if path.endswith("/management/permissions"):
            result = {"effective_plan": plan}
        elif "/start-policies" in path:
            if request.request.method == "PUT":
                body = request.request.post_data_json
                weekday = int(path.rsplit("/", 1)[1])
                assert body["expected_version"] == policies[weekday]["version"]
                policies[weekday] = {"weekday": weekday, "mode": body["mode"],
                                     "start_times": body["start_times"], "version": body["expected_version"] + 1}
                writes.append(body)
                result = policies[weekday]
            else:
                result = {"professional_id": PRO, "policies": policies}
        elif path.endswith("/professionals"):
            result = [{"id": PRO, "name": "Guilherme", "active": True, "commission_percent": 0, "specialty": "Barbeiro"}]
        elif path.endswith("/working_hours"):
            result = [{"id": HOUR, "professional_id": PRO, "weekday": 0,
                       "start_time": "08:00:00", "end_time": "18:00:00"}]
        elif path.endswith("/working_breaks"):
            result = [{"id": "51000000-0000-4000-8000-000000000003", "working_hour_id": HOUR,
                       "start_time": "12:00:00", "end_time": "13:00:00"}]
        elif path.endswith("/management/team"):
            result = {"can_manage": True, "members": [], "invitations": []}
        elif path.endswith("/management/service-packages"):
            result = {"items": []}
        request.fulfill(status=200, json=deepcopy(result))

    page.route("**/*", route)
    page.goto("https://orion.test/panel")
    for file in ("orion-fields.js", "orion-appointment-communications.js", "orion-suite.js"):
        page.add_script_tag(path=str(STATIC / file))
    page.evaluate("""({tenant, theme}) => {
      document.documentElement.dataset.theme = theme;
      window.OrionFields.bind(document);
      window.OrionSuite.mount({
        user: {role: 'member', name: 'Conta sintética', email: 'synthetic@example.invalid'},
        tenants: [{id: tenant, name: 'Barbearia de teste', slug: 'empresa-sintetica', timezone: 'America/Sao_Paulo', role: 'owner'}],
        tenantId: tenant, initialRoute: 'funcionarios',
        request: async (path, method='GET', data) => {
          const response = await fetch(path, {method, headers:{'Content-Type':'application/json'}, body:data?JSON.stringify(data):undefined});
          const result=await response.json();
          if(!response.ok)throw new Error(result.detail || 'Erro sintético');
          return result;
        }, openLegacy(){}, onRouteChange(){}
      });
    }""", {"tenant": TENANT, "theme": theme})
    return writes


@pytest.mark.parametrize("width,theme", CASES, ids=[f"{w}-{t}" for w, t in CASES])
def test_manual_editor_preserves_orion_layout_and_explicit_save(manual_ui_browser, width, theme):
    browser, engine = manual_ui_browser
    context = browser.new_context(viewport={"width": width, "height": 1000}, color_scheme=theme,
                                  locale="pt-BR", timezone_id="America/Sao_Paulo", has_touch=width < 768)
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        writes = panel_routes(page, theme)
        page.locator(f'[data-os-hours="{PRO}"]').click()
        toggle = page.locator('[data-os-start-policy-toggle="0"]')
        expect(toggle).to_have_attribute("aria-expanded", "false")
        expect(toggle).to_contain_text("Automático")
        toggle.click()
        page.locator('[data-os-start-policy-mode][value="manual"]').click()
        for value in ("08:40", "08:00", "09:20", "13:00", "13:40", "17:20"):
            page.locator('[data-os-start-time-input]').fill(value)
            page.locator('[data-os-start-time-input]').press("Enter")
        chips = page.locator('.os-hours-start-chip>span')
        expect(chips).to_have_text(["08:00", "08:40", "09:20", "13:00", "13:40", "17:20"])
        assert not writes, "Enter adds to the draft; it must not save the policy"
        for value, message in (("08:40", "adicionado"), ("12:40", "pausa"), ("07:00", "expediente")):
            page.locator('[data-os-start-time-input]').fill(value)
            page.locator('[data-os-start-time-add]').click()
            expect(page.locator('[data-os-form="hours-start-policy"] .os-form-message')).to_contain_text(message)
            expect(chips).to_have_count(6)
        page.locator('[data-os-start-time-input]').fill("")
        page.locator('[data-os-form="hours-start-policy"] [type="submit"]').click()
        expect(page.locator('[data-os-form="hours-start-policy"]')).to_have_count(0)
        expect(toggle).to_contain_text("Manual")
        assert len(writes) == 1
        assert writes[0]["start_times"] == ["08:00", "08:40", "09:20", "13:00", "13:40", "17:20"]
        toggle.click()
        page.locator('[data-os-start-time-remove="08:40"]').click()
        page.locator('[data-os-start-policy-cancel="0"]').click()
        toggle.click()
        expect(page.locator('[data-os-start-time-remove="08:40"]')).to_be_visible()
        assert len(writes) == 1, "Cancel must not mutate the persisted policy"
        assert_fits(page, "#osDialog")
        assert_fits(page, '[data-os-form="hours-start-policy"]')
        page.locator("#osDialog").evaluate("element => element.scrollTop = 0")
        snapshot(page, engine, "panel-manual", width, theme)
        assert not errors, errors
    finally:
        context.close()


def public_catalogue():
    return {"timezone": "America/Sao_Paulo", "today": DAY, "booking_horizon_days": 60,
            "booking_enabled": True, "published": True,
            "opening_hours": [{"weekday": weekday, "intervals": ([{"open": "08:00", "close": "12:00"},
                               {"open": "13:00", "close": "18:00"}] if weekday < 6 else [])} for weekday in range(7)],
            "site": {"title": "Barbearia de teste", "theme_color": "#2e7bf6", "show_prices": True,
                     "hide_orion_brand": False, "description": "Atendimento com horário marcado."},
            "services": [{"id": identity, "name": name, "category": "Serviços", "catalog_kind": kind,
                          "duration_min": duration, "price_cents": price, "price_mode": "fixed",
                          "professional_ids": [PRO, SECOND_PRO], "components": []}
                         for identity, name, kind, duration, price in [(SERVICE, "Corte", "base", 40, 3500),
                                                                      (COMBO, "Corte + barba", "combo", 60, 5500)]],
            "professionals": [{"id": PRO, "name": "Guilherme", "specialty": "Cabelo e barba"},
                              {"id": SECOND_PRO, "name": "Peixoto", "specialty": "Cabelo e barba"}]}


def public_routes(page):
    bookings = []

    def route(request):
        parsed = urlsplit(request.request.url)
        if asset(request):
            return
        if parsed.path == "/s/empresa-sintetica":
            request.fulfill(status=200, body=(STATIC / "orion-public.html").read_text(), content_type="text/html")
            return
        if parsed.path.endswith("/availability"):
            query = parse_qs(parsed.query)
            combo_selected = query.get("service_id") == [COMBO]
            allowed = MANUAL_COMBO if combo_selected else MANUAL_CUT
            if query.get("professional_id") == [SECOND_PRO]:
                allowed = ["08:00", "08:15", "08:30", "08:45", "09:00"]
            if query.get("day") == [NEXT_DAY]:
                allowed = []
            result = {"slots": [f"{DAY}T{clock}:00-03:00" for clock in allowed],
                      "quote": QUOTE if allowed else None, "timezone": "America/Sao_Paulo",
                      "availability_reason": "available" if allowed else "no_matching_slots",
                      "duration_min": 60 if combo_selected else 40, "price_cents": 5500 if combo_selected else 3500,
                      "price_mode": "fixed", "services": ["Corte + barba" if combo_selected else "Corte"]}
        elif parsed.path.endswith("/appointments"):
            body = request.request.post_data_json
            bookings.append(body)
            result = {"status": "confirmed", "protocol": "SYNTHETIC-1", "start_at": body["start_at"],
                      "end_at": f"{DAY}T09:20:00-03:00", "price_cents": 3500, "price_mode": "fixed",
                      "timezone": "America/Sao_Paulo", "professional": "Guilherme", "services": ["Corte"]}
            request.fulfill(status=201, json=result)
            return
        else:
            result = public_catalogue()
        request.fulfill(status=200, json=result)

    page.route("**/*", route)
    return bookings


@pytest.mark.parametrize("width,theme", CASES, ids=[f"{w}-{t}" for w, t in CASES])
def test_public_booking_uses_only_offered_starts_and_keeps_existing_design(manual_ui_browser, width, theme):
    browser, engine = manual_ui_browser
    context = browser.new_context(viewport={"width": width, "height": 1000}, color_scheme=theme,
                                  locale="pt-BR", timezone_id="America/Sao_Paulo", has_touch=width < 768)
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        bookings = public_routes(page)
        page.goto("https://orion.test/s/empresa-sintetica")
        page.locator("#services-grid .service-card").first.click()
        page.locator("#booking-next").click()
        slots = page.locator("#time-slots .slot")
        expect(slots).to_have_text(MANUAL_CUT)
        page.locator(f'[data-professional-id="{SECOND_PRO}"]').click()
        expect(slots).to_have_text(["08:00", "08:15", "08:30", "08:45", "09:00"])
        page.locator(f'[data-professional-id="{PRO}"]').click()
        expect(slots).to_have_text(MANUAL_CUT)
        page.locator(f'[data-day="{NEXT_DAY}"]').click()
        expect(slots).to_have_count(0)
        expect(page.locator("#booking-next")).to_be_disabled()
        page.locator(f'[data-day="{DAY}"]').click()
        expect(slots).to_have_text(MANUAL_CUT)
        assert_fits(page, "#booking-dialog")
        assert_fits(page, "#time-slots")
        snapshot(page, engine, "public-manual-slots", width, theme)
        slots.filter(has_text="08:40").click()
        page.locator("#booking-next").click()
        expect(page.locator(".review-card")).to_contain_text("08:40")
        assert_fits(page, "#booking-dialog")
        assert_fits(page, ".booking-form")
        snapshot(page, engine, "public-confirmation", width, theme)
        page.locator('[name="customer_name"]').fill("Cliente sintético")
        page.locator('[name="customer_phone"]').fill("(11) 99876-5432")
        page.locator('[name="customer_birth_date"]').fill("1990-01-15")
        page.locator("#booking-next").click()
        expect(page.locator("#booking-dialog")).to_contain_text("SYNTHETIC-1")
        assert len(bookings) == 1 and bookings[0]["start_at"] == f"{DAY}T08:40:00-03:00"
        assert bookings[0]["quote"] == QUOTE

        page.reload()
        page.locator("#services-grid .service-card").nth(1).click()
        page.locator("#booking-next").click()
        expect(page.locator("#time-slots .slot")).to_have_text(MANUAL_COMBO)
        assert_fits(page, "#booking-dialog")
        snapshot(page, engine, "public-manual-combo", width, theme)
        assert not errors, errors
    finally:
        context.close()
