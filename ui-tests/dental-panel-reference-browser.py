"""Browser-level navigation and clinical handoff through the real Orion shell."""
from pathlib import Path
import runpy
import os
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

shell = runpy.run_path(str(Path(__file__).with_name("dental-reception-browser.py")))
BASE = shell["ORIGIN"]
PATIENT = {"id": "30000000-0000-4000-8000-000000000003", "name": "Paciente sintético",
           "birth_date": "1990-05-12", "version": 1}
DAY = shell["SNAPSHOT"]["day"]
DESTINATIONS = {
    "Dashboard":"dashboard","Agenda":"agenda","Pacientes":"pacientes-odonto",
    "Prontuários":"clinica","Planos de tratamento":"planos-odonto",
    "Jornada do paciente":"jornada-odonto","Odontograma":"odontograma-odonto",
    "Periodontia":"periodontia-odonto","Exames e imagens":"exames-odonto",
    "Procedimentos":"servicos","Orçamentos":"orcamentos-odonto","Prótese e laboratório":"proteses-odonto",
    "Consultórios":"consultorios-odonto","Financeiro":"financeiro","Estoque":"estoque-odonto",
    "Convênios":"convenios-odonto","Equipe":"funcionarios","Relatórios":"desempenho",
    "Conversas":"inbox","Meu site":"site",
}
PLAN = dict(shell["PLAN"])
PLAN["permissions"] = {**PLAN["permissions"], "site.manage": True, "conversations.manual": True,
                       "performance.advanced": True}
CLINICAL = {
    "access": {"can_manage": True, "can_manage_inventory": True, "can_read_records": True,
               "clinical_access": {"level": "dentist"}, "grants": []},
    "patients": {"items": [PATIENT], "total": 1},
    f"patients/{PATIENT['id']}": PATIENT,
    f"patients/{PATIENT['id']}/visits": {"items": [], "has_more": False},
    "visit-options": {"items": [], "has_more": False, "day": DAY, "timezone": "America/Campo_Grande"},
    "appointments": {"items": [], "timezone": "America/Campo_Grande"},
    "rooms": {"items": []},
}


def check(browser, config, theme):
    page = browser.new_page(**config, color_scheme=theme)
    page.set_default_timeout(8000)
    errors, calls, plans = [], [], []
    page.on("pageerror", lambda error: errors.append(str(error)))

    def serve(route):
        request = route.request
        path = urlsplit(request.url).path
        if path.endswith("/management/permissions"):
            return route.fulfill(json={"effective_plan": PLAN})
        if "/dental/" in path:
            endpoint = path.split("/dental/", 1)[1]
            calls.append((endpoint, request.method))
            if endpoint == "insurance-plans":
                if request.method == "POST":
                    plans.append({**request.post_data_json,"version":1,"active":True})
                    return route.fulfill(status=201, json=plans[-1])
                return route.fulfill(json={"items":plans,"has_more":False})
            if endpoint in CLINICAL:
                return route.fulfill(json=CLINICAL[endpoint])
            if not endpoint.startswith("reception/"):
                return route.fulfill(json={"items":[],"has_more":False})
        return shell["serve"](route)

    page.route("**/*", serve)
    page.goto(BASE + "/panel?aba=dashboard", wait_until="domcontentloaded")
    app = page.locator("#appView")
    suite = page.locator("#page-suite")
    expect(suite).to_have_attribute("data-os-current-route", "dashboard")
    expect(suite.get_by_role("heading", name="Dashboard", exact=True)).to_be_visible()

    def navigate(label):
        toggle = page.locator("#osMenu")
        if toggle.is_visible() and toggle.get_attribute("aria-expanded") == "false":
            toggle.click()
        row = app.locator("#osNav").get_by_role("button",name=label,exact=True)
        row.click()
        expect(suite).to_have_attribute("data-os-current-route", DESTINATIONS[label])
        expect(app.locator("#osNav [aria-current=page]")).to_have_count(1)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), (config, theme, label)

    for label in DESTINATIONS:
        navigate(label)

    navigate("Jornada do paciente")
    suite.get_by_role("button", name=PATIENT["name"]).click()
    expect(suite.get_by_role("heading", name="Consultas de " + PATIENT["name"])).to_be_visible()
    assert (f"patients/{PATIENT['id']}/visits","GET") in calls
    assert not any(endpoint.endswith("/records") for endpoint,_ in calls)
    suite.get_by_role("button",name="Odontograma",exact=True).click()
    expect(suite).to_have_attribute("data-os-current-route", "odontograma-odonto")
    expect(suite.locator("[data-dental-tooth]")).to_have_count(32)
    assert (f"patients/{PATIENT['id']}/records","GET") in calls
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")

    navigate("Convênios")
    suite.get_by_role("button",name="Novo convênio").click()
    suite.get_by_label("Operadora",exact=True).fill("Operadora sintética")
    suite.get_by_label("Plano",exact=True).fill("Plano A")
    suite.get_by_role("button",name="Salvar convênio").click()
    expect(suite.get_by_text("Operadora sintética · Plano A",exact=True)).to_be_visible()
    assert len(plans) == 1
    assert not errors, errors
    page.close()


if __name__ == "__main__":
    with sync_playwright() as playwright:
        chromium = playwright.chromium.launch(executable_path=os.getenv("ORION_TEST_CHROME") or None)
        for width,height in [(320,720),(390,844),(820,1180),(1440,950)]:
            for theme in ("light","dark"):
                check(chromium, {"viewport":{"width":width,"height":height}}, theme)
        check(chromium,playwright.devices["Pixel 7"],"light")
        chromium.close()
        webkit = playwright.webkit.launch()
        check(webkit,playwright.devices["iPhone 13"],"light")
        webkit.close()
    print("20 destinos, jornada, odontograma e convênios em 10 combinações de tela, navegador e tema.")
