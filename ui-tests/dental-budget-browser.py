"""Validate one synthetic budget draft in the protected dental record."""
import importlib.util
import os
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

spec = importlib.util.spec_from_file_location(
    "dental_shell", Path(__file__).with_name("dental-reception-browser.py")
)
shell = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shell)


def verify(browser, width, height, theme):
    page = browser.new_page(viewport={"width": width, "height": height}, color_scheme=theme, locale="pt-BR")
    patient = {
        "id": "30000000-0000-4000-8000-000000000021",
        "name": "Paciente sintético do orçamento",
        "birth_date": "2001-05-06",
        "guardian_name": "Responsável sintético",
        "version": 1,
    }
    records, writes, errors = [], [], []
    page.on("pageerror", lambda error: errors.append(str(error)))

    def serve(route):
        path = urlsplit(route.request.url).path
        if path.endswith("/management/permissions"):
            return route.fulfill(json={"effective_plan": {**shell.PLAN, "dental_clinical_enabled": True}})
        if path.endswith("/dental/access"):
            return route.fulfill(json={
                "clinical_access": {
                    "level": "dentist",
                    "professional_id": "40000000-0000-4000-8000-000000000022",
                },
                "can_manage": False,
                "can_read_records": True,
            })
        if path.endswith("/dental/patients"):
            return route.fulfill(json={"items": [patient], "total": 1})
        if path.endswith("/dental/patients/" + patient["id"]):
            return route.fulfill(json=patient)
        if path.endswith("/dental/patients/" + patient["id"] + "/records"):
            if route.request.method == "POST":
                payload = route.request.post_data_json
                writes.append(payload)
                records.insert(0, {
                    "id": "50000000-0000-4000-8000-000000000023",
                    "kind": payload["kind"],
                    "content": payload["content"],
                    "created_at": "2026-11-24T15:00:00Z",
                    "professional_id": "40000000-0000-4000-8000-000000000022",
                })
                return route.fulfill(status=201, json={"id": records[0]["id"], "recorded": True})
        if path.endswith("/dental/patients/" + patient["id"] + "/records") or \
           path.endswith("/dental/patients/" + patient["id"] + "/records/"):
            return route.fulfill(json={"items": records})
        if "/records" in path and path.endswith("/records") is False:
            return route.fulfill(json={"items": records})
        if path.endswith("/dental/patients/" + patient["id"] + "/exams"):
            return route.fulfill(json={"items": []})
        if path.endswith("/dental/intake-templates/clinical"):
            return route.fulfill(json={"items": []})
        if path.endswith("/dental/laboratories"):
            return route.fulfill(json={"items": []})
        return shell.serve(route)

    page.route("**/*", serve)
    page.goto(shell.ORIGIN + "/panel?aba=clinica", wait_until="domcontentloaded")
    root = page.locator("#osDentalClinic")
    root.get_by_text(patient["name"], exact=True).wait_for(timeout=15000)
    page.evaluate("(theme)=>document.documentElement.dataset.theme=theme", theme)
    page.evaluate("document.fonts.ready")
    root.get_by_text(patient["name"], exact=True).click()
    root.get_by_role("tab", name="Orçamentos", exact=True).click()
    proposal_date = root.get_by_label("Data da proposta", exact=True)
    description = root.get_by_label("Descrição", exact=True)
    proposal_date.fill("2026-11-22")
    description.fill("Proposta sintética individual")
    root.get_by_label("Etapa / procedimento", exact=True).fill("Procedimento sintético")
    root.get_by_label("Estimativa em reais (opcional)", exact=True).fill("123.45")
    root.get_by_role("button", name="Adicionar etapa", exact=True).click()
    assert proposal_date.input_value() == "2026-11-22"
    assert description.input_value() == "Proposta sintética individual"
    root.get_by_text("Procedimento sintético", exact=True).wait_for()
    assert not page.evaluate("document.documentElement.scrollWidth>innerWidth+1"), (width, theme)
    budget_form = root.locator('[data-dental-form="record"]')
    assert budget_form.evaluate("""form=>{
        const bounds=form.getBoundingClientRect();
        return form.scrollWidth<=form.clientWidth+1 &&
          [...form.querySelectorAll('input,textarea,select,button')].every(node=>{
            const box=node.getBoundingClientRect();
            return !box.width||(box.left>=bounds.left-1&&box.right<=bounds.right+1&&box.right<=innerWidth+1);
          });
    }"""), (width, theme)
    shell.OUT.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(shell.OUT / f"orcamento-rascunho-{width}-{theme}.png"),
                    full_page=True, animations="disabled")
    root.get_by_role("button", name="Salvar registro clínico", exact=True).click()
    root.get_by_text("Registro clínico gravado e auditado.", exact=True).wait_for()
    history = root.locator('details.os-dental-entry').filter(has_text="Procedimento sintético").first
    history.locator("summary").click()
    history.get_by_text("22/11/2026", exact=True).wait_for()
    assert len(writes) == 1
    content = writes[0]["content"]
    assert writes[0]["kind"] == "budget"
    assert content == {
        "proposal_date": "2026-11-22",
        "description": "Proposta sintética individual",
        "items": [{
            "description": "Procedimento sintético",
            "tooth": None,
            "estimate_cents": 12345,
            "state": "planned",
        }],
        "total_cents": 12345,
        "status": "draft",
    }
    assert not errors, errors
    page.screenshot(path=str(shell.OUT / f"orcamento-historico-{width}-{theme}.png"),
                    full_page=True, animations="disabled")
    page.close()


if __name__ == "__main__":
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=os.getenv("ORION_TEST_CHROME") or None,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        for width, height in ((320, 720), (390, 844), (820, 1180), (1440, 980)):
            for theme in ("light", "dark"):
                verify(browser, width, height, theme)
        browser.close()
    print("Rascunho de orçamento validado em oito combinações de tela e tema.")
