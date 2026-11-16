"""Shipped agenda UI in real Chromium/WebKit with local synthetic HTTP only.

The separate PostgreSQL suite verifies persistence and tenant permissions.
These browser cases verify actual controls, submission and responsive layout.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import re
from urllib.parse import urlsplit

import pytest

from test_manual_start_times_responsive import (
    STATIC, TENANT, PRO, SERVICE, CASES, assert_fits, manual_ui_browser, expect,
)

ARTIFACTS = STATIC.parents[1] / "test-results" / "appointment-operations"
APPOINTMENT = "90000000-0000-4000-8000-000000000009"
EXTRA = "50000000-0000-4000-8000-000000000006"
FUTURE_DAY = "2030-04-05"
PAST_DAY = (datetime.now(timezone.utc) - timedelta(days=2)).date().isoformat()
START = FUTURE_DAY + "T19:00:00-04:00"
SERVICES = [
    {"id": SERVICE, "name": "Corte", "duration_min": 40, "price_cents": 3500,
     "price_mode": "fixed", "active": True, "catalog_kind": "base"},
    {"id": EXTRA, "name": "Barba", "duration_min": 20, "price_cents": 2000,
     "price_mode": "fixed", "active": True, "catalog_kind": "base", "recommended": True, "add_duration": True},
]


def booking():
    return {"id": APPOINTMENT, "protocol": "AG-SYN123", "customer_id": "30000000-0000-4000-8000-000000000003",
            "customer_name": "Cliente sintético", "customer_display_name": "Cliente sintético",
            "customer_phone": "+5567999999910", "professional_id": PRO, "professional_name": "Guilherme",
            "start_at": START, "end_at": FUTURE_DAY + "T19:40:00-04:00",
            "updated_at": "2026-11-04T18:00:00Z", "created_at": "2026-11-04T18:00:00Z",
            "status": "confirmed", "booking_mode": "standard", "total_cents": 3500,
            "price_mode": "fixed", "timezone": "America/Cuiaba", "actual_duration_min": None,
            "items": [{"service_id": SERVICE, "name_snapshot": "Corte", "duration_snapshot_min": 40,
                       "price_snapshot_cents": 3500, "price_mode_snapshot": "fixed", "item_origin": "booked"}],
            "services": [{"name": "Corte", "duration_min": 40}], "history": [],
            "allowed_actions": ["cancel", "reschedule", "add_services"]}


class Responses:
    def __init__(self):
        self.writes = []
        self.row = booking()
        self.manual_rows = []

    def result(self, path, method, data):
        if method != "GET":
            self.writes.append({"path": path, "method": method, "body": deepcopy(data)})
        if path == "/auth/me":
            return {"authenticated": True, "role": "member", "csrf": "synthetic-only"}
        if path == "/api/v1/professional/me":
            return {"id": PRO, "name": "Guilherme", "business_name": "Barbearia sintética", "timezone": "America/Cuiaba"}
        if path == "/api/v1/professional/dashboard":
            return {"date": FUTURE_DAY, "profile": {"name": "Guilherme"},
                    "today": {"confirmed": 1, "completed": 0, "revenue_cents": 0, "commission_cents": 0}, "goals": []}
        if path == "/api/v1/professional/agenda":
            return {"date": FUTURE_DAY, "appointments": [deepcopy(self.row)], "blocks": []}
        if path.endswith("/management/permissions"):
            features = ["workspace.access", "dashboard.basic", "agenda.manage", "clients.manage", "services.manage",
                        "professionals.manage", "finance.basic", "site.manage", "notifications.basic", "settings.manage",
                        "calendar.manage", "plan.manage", "account.manage", "business.setup"]
            return {"effective_plan": {"code": "essencial", "name": "Essencial", "source": "manual",
                    "status": "active", "active": True, "version": 1, "revision": "synthetic-operations",
                    "features": features, "permissions": {key: True for key in features},
                    "limits": {"max_professionals": 2, "active_professionals": 1, "excess_professionals": 0}}}
        if path.endswith("/appointments/manual"):
            result = deepcopy(self.row)
            result.update(booking_mode=data["mode"], status="completed" if data["mode"] == "retroactive" else "confirmed",
                          start_at=data["start_at"], customer_name=data["customer_name"],
                          total_cents=data.get("final_total_cents") if data.get("final_total_cents") is not None else 3500)
            self.manual_rows.append(result)
            return result
        if path.endswith("/extras-catalog"):
            return {"appointment": deepcopy(self.row), "services": [deepcopy(SERVICES[1])], "max_items": 6}
        if path.endswith("/extras/preview"):
            return {"appointment": deepcopy(self.row), "services": [deepcopy(SERVICES[1])], "total_cents": 5500,
                    "services_duration_min": 60, "reserved_duration_min": 40,
                    "new_end_at": FUTURE_DAY + "T20:00:00-04:00", "duration_conflict": True,
                    "conflict_message": "Há outro atendimento após este horário."}
        if path.endswith("/extras") and method == "POST":
            assert data["preserve_reserved_time"] is True
            self.row["items"].append({"service_id": EXTRA, "name_snapshot": "Barba", "duration_snapshot_min": 20,
                                      "price_snapshot_cents": 2000, "price_mode_snapshot": "fixed", "item_origin": "extra"})
            self.row.update(total_cents=5500, actual_duration_min=60, updated_at="2026-11-04T18:01:00Z")
            self.row["services"].append({"name": "Barba", "duration_min": 20, "item_origin": "extra"})
            return deepcopy(self.row)
        if path.endswith("/appointments/" + APPOINTMENT):
            return deepcopy(self.row)
        if path.endswith("/appointments"):
            return {"items": [deepcopy(self.row)], "total": 1, "offset": 0, "limit": 50, "has_more": False}
        if path.endswith("/management/customers"):
            return {"items": [], "total": 0}
        if path.endswith("/professionals"):
            return [{"id": PRO, "name": "Guilherme", "active": True, "accepts_appointments": True}]
        if path.endswith("/professional_services"):
            return [{"professional_id": PRO, "service_id": item["id"]} for item in SERVICES]
        if path.endswith("/services"):
            return {"items": deepcopy(SERVICES)} if "/professional/" in path else deepcopy(SERVICES)
        if path.endswith("/addons"):
            return {"items": []}
        if path.endswith("/availability"):
            return {"slots": [START], "timezone": "America/Cuiaba", "duration_min": 40, "price_cents": 3500}
        if path.endswith("/working_hours"):
            return [{"id": "wh-test", "professional_id": PRO, "weekday": 0, "start_time": "09:00", "end_time": "18:00"}]
        if path.endswith("/management/team"):
            return {"can_manage": True, "members": [], "invitations": []}
        if path.endswith("/management/service-packages"):
            return {"items": []}
        return []


def boot(page, theme, professional=False):
    responses = Responses()
    panel = (STATIC / "panel.html").read_text()
    base_styles = "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", panel, re.S))
    html = (f'<!doctype html><html lang="pt-BR" data-theme="{theme}"><head>'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<style>{base_styles}</style><link rel="stylesheet" href="/brand/orion-suite.css">'
            '<link rel="stylesheet" href="/brand/orion-fields.css"></head><body>'
            '<div id="appView" class="app"><aside class="sidebar"><div class="brand">Orion</div></aside>'
            '<main class="content"><section id="page-original" class="page"></section></main></div></body></html>')

    def route(r):
        parsed = urlsplit(r.request.url)
        path = parsed.path
        if parsed.netloc != "orion.test":
            return r.fulfill(status=204, body="")
        if path.startswith(("/brand/", "/public-assets/", "/panel-assets/")):
            source = STATIC / path.rsplit("/", 1)[-1]
            if source.is_file():
                mime = {".js": "application/javascript", ".css": "text/css", ".woff2": "font/woff2", ".svg": "image/svg+xml"}.get(source.suffix)
                return r.fulfill(status=200, body=source.read_bytes(), content_type=mime)
        if path in {"/panel", "/profissional"}:
            return r.fulfill(status=200, body=(STATIC / "orion-professional.html").read_text() if professional else html,
                             content_type="text/html")
        data = r.request.post_data_json if r.request.method != "GET" else None
        r.fulfill(status=201 if path.endswith("/appointments/manual") else 200,
                  json=responses.result(path, r.request.method, data))

    page.route("**/*", route)
    page.add_init_script("localStorage.setItem('orion-professional-theme', '" + theme + "');")
    page.goto("https://orion.test/profissional" if professional else "https://orion.test/panel")
    if not professional:
        for name in ("orion-fields.js", "orion-appointment-communications.js", "orion-suite.js"):
            page.add_script_tag(path=str(STATIC / name))
        page.evaluate("""({tenant,theme})=>{
          document.documentElement.dataset.theme=theme;
          OrionFields.bind(document);
          OrionSuite.mount({user:{role:'member',name:'Conta sintética'},
            tenants:[{id:tenant,name:'Barbearia sintética',slug:'empresa-sintetica',timezone:'America/Cuiaba',role:'owner'}],
            tenantId:tenant,initialRoute:'agenda',request:async(path,method='GET',body)=>{
              const r=await fetch(path,{method,headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});
              const result=await r.json();if(!r.ok){const e=Error(result.detail?.message||result.detail||'Erro');e.status=r.status;e.detail=result.detail;throw e;}return result;
            },openLegacy(){},onRouteChange(){}});
        }""", {"tenant": TENANT, "theme": theme})
    return responses


def photograph(page, engine, label, width, theme):
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(ARTIFACTS / f"{engine}-{label}-{width}-{theme}.png"), animations="disabled")


@pytest.mark.parametrize("width,theme", CASES, ids=[f"{w}-{t}" for w, t in CASES])
def test_company_manual_and_retroactive_use_current_form(manual_ui_browser, width, theme):
    browser, engine = manual_ui_browser
    context = browser.new_context(viewport={"width": width, "height": 1000}, color_scheme=theme,
                                  locale="pt-BR", timezone_id="America/Cuiaba", has_touch=width < 768)
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        result = boot(page, theme)
        page.locator('[data-os-action="new-appointment"]').click()
        form = page.locator('[data-os-form="appointment"]')
        form.locator('[name="booking_mode"]').select_option("exceptional")
        form.locator('[name="customer_name"]').fill("Cliente das 19h")
        form.locator('[name="walk_in"]').check()
        form.locator('[name="day"]').fill(FUTURE_DAY)
        form.locator('[name="clock"]').fill("19:00")
        form.locator('[data-os-book-service="' + SERVICE + '"]').click()
        form.locator('[name="professional_id"]').select_option(PRO)
        expect(form.locator('[name="customer_phone"]')).to_be_hidden()
        expect(form.locator('[name="start_at"]')).to_be_hidden()
        assert_fits(page, "#osDialog")
        photograph(page, engine, "company-exceptional", width, theme)
        form.locator('[type="submit"]').click()
        expect(page.locator("#osDialog")).not_to_be_visible()
        sent = [r for r in result.writes if r["path"].endswith("/appointments/manual")]
        assert len(sent) == 1 and sent[0]["body"]["mode"] == "exceptional"
        assert sent[0]["body"]["walk_in"] is True and sent[0]["body"]["customer_phone"] is None
        assert datetime.fromisoformat(sent[0]["body"]["start_at"]) == datetime.fromisoformat(START)
        page.locator('[data-os-action="new-appointment"]').click()
        form = page.locator('[data-os-form="appointment"]')
        form.locator('[name="booking_mode"]').select_option("retroactive")
        form.locator('[name="customer_name"]').fill("Cliente de ontem")
        form.locator('[name="walk_in"]').check()
        form.locator('[name="day"]').fill(PAST_DAY)
        form.locator('[name="clock"]').fill("17:00")
        form.locator('[data-os-book-service="' + SERVICE + '"]').click()
        form.locator('[name="professional_id"]').select_option(PRO)
        form.locator('[name="final_total"]').fill("40,00")
        expect(form.locator('[name="appointment_messages_opt_in"]')).to_be_hidden()
        assert_fits(page, "#osDialog")
        photograph(page, engine, "company-retroactive", width, theme)
        form.locator('[type="submit"]').click()
        expect(page.locator("#osDialog")).not_to_be_visible()
        sent = [r for r in result.writes if r["path"].endswith("/appointments/manual")]
        assert len(sent) == 2 and sent[-1]["body"]["mode"] == "retroactive"
        assert sent[-1]["body"]["final_total_cents"] == 4000
        assert sent[-1]["body"]["appointment_messages_opt_in"] is False
        assert not errors, errors
    finally:
        context.close()


@pytest.mark.parametrize("width,theme", CASES, ids=[f"{w}-{t}" for w, t in CASES])
def test_company_extras_require_explicit_duration_conflict_choice(manual_ui_browser, width, theme):
    browser, engine = manual_ui_browser
    context = browser.new_context(viewport={"width": width, "height": 1000}, color_scheme=theme,
                                  locale="pt-BR", timezone_id="America/Cuiaba", has_touch=width < 768)
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        result = boot(page, theme)
        page.locator('[data-os-appointment="' + APPOINTMENT + '"]').click()
        page.locator('[data-os-action="appointment-extras"]').click()
        form = page.locator('[data-os-form="appointment-extras"]')
        form.locator('[name="extra_service_ids"]').check()
        form.locator('[type="submit"]').click()
        expect(page.locator("#osExtraPreview")).to_contain_text("55,00")
        choice = form.locator('[name="preserve_reserved_time"]')
        expect(choice).not_to_be_checked()
        expect(choice).to_have_attribute("required", "")
        assert not any(item["path"].endswith("/extras") for item in result.writes)
        assert_fits(page, "#osDialog")
        photograph(page, engine, "company-extras-conflict", width, theme)
        choice.check()
        form.locator('[type="submit"]').click()
        expect(page.locator("#osDialogTitle")).to_have_text("Detalhes do agendamento")
        expect(page.locator("#osDialog .os-appointment-services li")).to_have_count(2)
        writes = [item for item in result.writes if item["path"].endswith("/extras")]
        assert len(writes) == 1 and writes[0]["body"]["preserve_reserved_time"] is True
        assert writes[0]["body"]["expected_updated_at"] == booking()["updated_at"]
        assert result.row["end_at"] == booking()["end_at"]
        assert_fits(page, "#osDialog")
        photograph(page, engine, "company-extras-saved", width, theme)
        assert not errors, errors
    finally:
        context.close()


@pytest.mark.parametrize("width,theme", CASES, ids=[f"{w}-{t}" for w, t in CASES])
def test_professional_manual_retroactive_and_extras_stay_in_own_portal(manual_ui_browser, width, theme):
    browser, engine = manual_ui_browser
    context = browser.new_context(viewport={"width": width, "height": 1000}, color_scheme=theme,
                                  locale="pt-BR", timezone_id="America/Cuiaba", has_touch=width < 768)
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        result = boot(page, theme, professional=True)
        page.locator('[data-page="agenda"]:visible').first.click()
        for mode, day in (("exceptional", FUTURE_DAY), ("retroactive", PAST_DAY)):
            page.locator('[data-action="new-appointment"]').click()
            form = page.locator("#modalForm")
            form.locator('[name="booking_mode"]').select_option(mode)
            form.locator('[name="customer_name"]').fill("Cliente do profissional")
            form.locator('[name="walk_in"]').check()
            form.locator('[name="day"]').fill(day)
            form.locator('[name="clock"]').fill("19:00" if mode == "exceptional" else "16:00")
            form.locator('[name="service_ids"][value="' + SERVICE + '"]').check()
            if mode == "retroactive":
                form.locator('[name="final_total"]').fill("40,00")
            before = len(result.writes)
            page.locator("#modalSubmit").click()
            expect(page.locator("#modalReview")).to_be_visible()
            assert len(result.writes) == before, "Revisão não deve criar um atendimento"
            assert_fits(page, "#modal .dialog")
            photograph(page, engine, "professional-" + mode, width, theme)
            page.locator("#modalSubmit").click()
            expect(page.locator("#modal")).to_be_hidden()
            sent = result.writes[-1]
            assert sent["path"] == "/api/v1/professional/appointments/manual"
            assert sent["body"]["mode"] == mode and "professional_id" not in sent["body"]
            assert sent["body"]["customer_phone"] is None
            assert sent["body"]["appointment_messages_opt_in"] is False
        page.locator('[data-action="extras"]').first.click()
        form = page.locator("#modalForm")
        form.locator('[name="extra_service_ids"]').check()
        page.locator("#modalSubmit").click()
        expect(page.locator("#modalExtraPreview")).to_contain_text("55,00")
        choice = form.locator('[name="preserve_reserved_time"]')
        expect(choice).not_to_be_checked()
        assert not any(r["path"].endswith("/extras") for r in result.writes)
        assert_fits(page, "#modal .dialog")
        photograph(page, engine, "professional-extras", width, theme)
        choice.check()
        page.locator("#modalSubmit").click()
        expect(page.locator("#modal")).to_be_hidden()
        assert result.writes[-1]["body"]["preserve_reserved_time"] is True
        assert all(item["path"].startswith("/api/v1/professional/") for item in result.writes)
        assert not errors, errors
    finally:
        context.close()
