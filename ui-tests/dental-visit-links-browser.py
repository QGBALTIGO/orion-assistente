"""Exercise explicit patient links through the Orion panel with an isolated family."""
import importlib.util
import os
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

spec = importlib.util.spec_from_file_location("reception_fixture", Path(__file__).with_name("dental-reception-browser.py"))
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
PATIENT = "30000000-0000-4000-8000-000000000001"
VISIT = "40000000-0000-4000-8000-000000000001"


def verify(browser, width, height, theme):
    page = browser.new_page(viewport={"width": width, "height": height}, color_scheme=theme, locale="pt-BR")
    writes, errors = [], []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    person = {"id": PATIENT, "name": "Paciente Alfa", "guardian_name": "Responsável da família", "version": 1}
    slot = {"id": VISIT, "protocol": "AG-TEST01", "start_at": "2026-11-22T16:45:00Z", "status": "confirmed",
            "professional": "Dra. Oliveira", "booking_contact": "Responsável da família", "patient_name": None,
            "patient_id": None, "version": 0, "timezone": "America/Campo_Grande"}

    def serve(route):
        path = urlsplit(route.request.url).path
        if path.endswith("/management/permissions"):
            return route.fulfill(json={"effective_plan": {**fixture.PLAN, "dental_clinical_enabled": True}})
        if path.endswith("/dental/access"):
            return route.fulfill(json={"clinical_access": {"level": "assistant"}, "can_read_records": False, "grants": []})
        if path.endswith("/dental/patients"):
            return route.fulfill(json={"items": [person], "total": 1})
        if path.endswith("/dental/patients/" + PATIENT):
            return route.fulfill(json=person)
        if path.endswith("/visits"):
            return route.fulfill(json={"items": [slot] if slot["patient_id"] else [], "has_more": False})
        if path.endswith("/visit-options"):
            return route.fulfill(json={"day": "2026-11-22", "timezone": slot["timezone"], "items": [slot], "has_more": False})
        if path.endswith("/appointments/" + VISIT + "/patient"):
            payload = route.request.post_data_json
            assert route.request.method == "PUT" and payload["expected_version"] == slot["version"]
            writes.append(payload)
            slot.update(version=slot["version"] + 1, patient_id=payload["patient_id"],
                        patient_name=person["name"] if payload["patient_id"] else None)
            return route.fulfill(json={"appointment_id": VISIT, "patient_id": slot["patient_id"], "version": slot["version"]})
        if "/records" in path or "/exams" in path:
            errors.append("A recepção consultou uma área clínica")
        return fixture.serve(route)

    page.route("**/*", serve)
    page.goto(fixture.ORIGIN + "/panel?aba=clinica", wait_until="domcontentloaded")
    page.evaluate("(theme)=>document.documentElement.dataset.theme=theme", theme)
    page.get_by_role("button", name="Paciente Alfa", exact=False).first.click()
    page.get_by_role("button", name="Consultas do paciente").click()
    page.get_by_text("Nenhuma consulta vinculada a este paciente.").wait_for()
    page.get_by_label("Consulta na agenda").select_option(VISIT)
    action = page.get_by_role("button", name="Vincular ao paciente")
    action.focus()
    action.press("Enter")
    unlink = page.get_by_role("button", name="Desvincular")
    unlink.wait_for()
    host = page.locator('[aria-labelledby="dental-visits-heading"]')
    assert host.locator("li").inner_text().count("12:45") == 1
    assert host.evaluate("""node=>[...node.querySelectorAll('button,input,select,h2,li')].every(el=>{
      const b=el.getBoundingClientRect(), r=node.getBoundingClientRect();
      return b.width>0 && b.left>=r.left-1 && b.right<=r.right+1 && b.right<=innerWidth+1;
    })"""), (width, theme)
    assert not page.evaluate("document.documentElement.scrollWidth>innerWidth+1"), (width,theme)
    fixture.OUT.mkdir(parents=True,exist_ok=True)
    host.screenshot(path=str(fixture.OUT / f"paciente-consultas-{width}-{theme}.png"),animations="disabled")
    if width==820:
        assert host.bounding_box()["width"]>=390
    unlink.click()
    page.get_by_role("button", name="Voltar", exact=True).click()
    assert len(writes)==1
    unlink.click()
    page.get_by_role("button", name="Confirmar desvínculo").click()
    page.get_by_text("Nenhuma consulta vinculada a este paciente.").wait_for()
    assert writes == [{"patient_id": PATIENT, "expected_version": 0},{"patient_id": None,"expected_version": 1}]
    assert not errors, errors
    page.close()


if __name__ == "__main__":
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=os.getenv("ORION_TEST_CHROME") or None,
                                            args=["--no-sandbox", "--disable-dev-shm-usage"])
        for width, height in [(320,720), (390,844), (820,1180), (1440,980)]:
            for theme in ("light","dark"):
                verify(browser,width,height,theme)
        browser.close()
    print("Vínculo e desvínculo validados em oito combinações de largura e tema.")
