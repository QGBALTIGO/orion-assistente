"""Check FDI navigation, explicit findings and persistence in an isolated dental panel."""
import importlib.util
import os
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

spec = importlib.util.spec_from_file_location("shell", Path(__file__).with_name("dental-reception-browser.py"))
shell = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shell)
PATIENT = "30000000-0000-4000-8000-000000000003"
PROFESSIONAL = "40000000-0000-4000-8000-000000000004"


def verify(browser, width, height, theme):
    page = browser.new_page(viewport={"width":width, "height":height}, color_scheme=theme, locale="pt-BR")
    records, errors = [], []
    patient = {"id":PATIENT, "name":"Paciente de teste", "version":1}
    page.on("pageerror", lambda error:errors.append(str(error)))

    def serve(route):
        path = urlsplit(route.request.url).path
        if path.endswith("/management/permissions"):
            return route.fulfill(json={"effective_plan":{**shell.PLAN, "dental_clinical_enabled":True}})
        if path.endswith("/dental/access"):
            return route.fulfill(json={"clinical_access":{"level":"dentist"}, "can_read_records": True, "grants":[]})
        if path.endswith("/dental/patients"):
            return route.fulfill(json={"items":[patient]})
        if path.endswith("/dental/patients/" + PATIENT):
            return route.fulfill(json=patient)
        if path.endswith("/odontogram/history"):
            number=route.request.post_data_json["tooth"]
            history=[{"id":r["id"],"recorded_at":r["created_at"],"professional":"Profissional sintético",
                      "findings":[f for f in r["content"]["findings"] if f["tooth"]==number]} for r in records]
            return route.fulfill(json={"tooth":number,"items":[item for item in history if item["findings"]],"next_cursor":None})
        if path.endswith("/records"):
            if route.request.method == "POST":
                payload = route.request.post_data_json
                records.append({**payload, "id":payload["entry_key"], "created_at":"2026-11-22T17:00:00Z",
                                "professional_id":PROFESSIONAL})
                return route.fulfill(status=201, json={"id":payload["entry_key"],"recorded":True})
            return route.fulfill(json={"items":records})
        if "/dental/" in path:
            return route.fulfill(json={"items":[]})
        return shell.serve(route)

    page.route("**/*", serve)
    page.goto(shell.ORIGIN + "/panel?aba=clinica", wait_until="domcontentloaded")
    page.evaluate("theme => document.documentElement.dataset.theme = theme", theme)
    page.get_by_role("button", name="Paciente de teste").click()
    page.get_by_role("tab", name="Odontograma").click()
    chart = page.get_by_role("group", name="Selecionar dente FDI")
    form = page.locator('form[data-kind="odontogram"]')
    for dentition, count in [("deciduous", 20), ("mixed", 52), ("permanent", 32)]:
        form.get_by_label("Dentição", exact=True).select_option(dentition)
        assert chart.get_by_role("button").count() == count, (width, theme, dentition)
        assert chart.evaluate("""el => {
            const root = el.getBoundingClientRect();
            const buttons = [...el.querySelectorAll('button')].map(b => b.getBoundingClientRect());
            return buttons.every((b, i) => b.width >= 42 && b.height >= 44 && b.left >= root.left-1 &&
                b.right <= root.right+1 && b.right <= innerWidth+1 && buttons.every((o,j) =>
                    j === i || b.right <= o.left+1 || o.right <= b.left+1 || b.bottom <= o.top+1 || o.bottom <= b.top+1));
        }"""), (width, theme, dentition)
    assert form.get_by_label("Achado informado pelo profissional").input_value() == ""
    form.get_by_label("Observação do odontograma").fill("Observação sintética")
    chart.get_by_role("button",name="Dente 18",exact=True).press("Enter")
    assert form.get_by_label("Observação do odontograma").input_value() == "Observação sintética"
    form.get_by_label("Face",exact=True).select_option("V")
    form.get_by_label("Achado informado pelo profissional").select_option("watch")
    form.get_by_label("Nota da face").fill("Nota sintética")
    form.get_by_role("button",name="Adicionar registro da face").press("Enter")
    marked = chart.get_by_role("button",name="Dente 18, com registro",exact=True)
    assert marked.get_attribute("aria-pressed") == "true"
    assert not page.evaluate("document.documentElement.scrollWidth > innerWidth+1")
    shell.OUT.mkdir(parents=True,exist_ok=True)
    chart.evaluate("el => el.scrollIntoView({block:'start',behavior:'instant'})")
    assert chart.get_by_role("button").first.evaluate("""button => {
        const b=button.getBoundingClientRect();
        return button.contains(document.elementFromPoint(b.left+b.width/2,b.top+b.height/2));
    }"""), (width,theme)
    chart.screenshot(path=str(shell.OUT/f"odontograma-{width}-{theme}.png"),animations="disabled")
    form.get_by_role("button",name="Salvar registro clínico").press("Enter")
    page.get_by_text("Registro clínico gravado e auditado.",exact=True).wait_for()
    page.locator("details.os-dental-entry summary").click()
    assert page.locator("details.os-dental-entry").get_by_text("Observação sintética",exact=True).is_visible()
    chart.get_by_role("button",name="Dente 18",exact=True).press("Enter")
    form.get_by_role("button",name="Histórico do dente 18").press("Enter")
    history = form.get_by_role("region",name="Histórico do dente 18")
    history.get_by_text("Vestibular · Acompanhar · Nota sintética").wait_for()
    assert history.get_by_text("Profissional sintético").is_visible()
    history.evaluate("el => el.scrollIntoView({block:'center',behavior:'instant'})")
    history.screenshot(path=str(shell.OUT/f"historico-dente-{width}-{theme}.png"),animations="disabled")
    history.get_by_role("button",name="Fechar histórico").press("Enter")
    assert form.get_by_role("button",name="Histórico do dente 18").evaluate("b => document.activeElement === b")
    assert records[0]["content"]["findings"] == [{"tooth":18,"face":"V","finding":"watch","note":"Nota sintética"}]
    assert not errors, errors
    page.close()


if __name__ == "__main__":
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=os.getenv("ORION_TEST_CHROME") or None,
                                             args=["--no-sandbox", "--disable-dev-shm-usage"])
        for width,height in [(320,720),(390,844),(820,1180),(1440,980)]:
            for theme in ["light","dark"]:
                verify(browser,width,height,theme)
        browser.close()
    print("Três dentições, seleção pelo teclado e histórico aprovados em oito combinações de tela e tema.")
