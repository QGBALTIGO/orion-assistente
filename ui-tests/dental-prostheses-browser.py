"""Exercise dental work orders from the Orion menu with synthetic cases."""
import importlib.util
import os
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import sync_playwright

spec = importlib.util.spec_from_file_location("dental_shell", Path(__file__).with_name("dental-reception-browser.py"))
shell = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shell)


def verify(browser, width, height, theme):
    page = browser.new_page(viewport={"width": width, "height": height}, color_scheme=theme, locale="pt-BR")
    patient = {"id": "30000000-0000-4000-8000-000000000001", "name": "Paciente sintético", "version": 1}
    record_id = "40000000-0000-4000-8000-000000000002"
    work = {"id": record_id, "patient_id": patient["id"], "patient_name": patient["name"],
            "content": {"laboratory": "Laboratório de teste", "description": "Coroa provisória de teste",
                        "status": "working", "due_date": "2026-11-21"}, "deadline_state":"overdue", "actions":["delivered"]}
    errors, writes, dental_reads = [], [], []
    page.on("pageerror", lambda error: errors.append(str(error)))

    def serve(route):
        url = urlsplit(route.request.url)
        path = url.path
        if path.endswith("/management/permissions"):
            return route.fulfill(json={"effective_plan": {**shell.PLAN, "dental_clinical_enabled": True}})
        if "/dental/" in path:
            dental_reads.append(path.rsplit("/dental/",1)[1])
        if path.endswith("/dental/access"):
            return route.fulfill(json={"clinical_access":{"level":"dentist"}, "can_manage":False, "can_read_records":True})
        if path.endswith("/dental/prostheses"):
            filters = parse_qs(url.query)
            matches = filters.get("status", [work["content"]["status"]])[0] == work["content"]["status"]
            return route.fulfill(json={"items":[work] if matches else [],"next_cursor":None,"clinic_day":"2026-11-22"})
        if path.endswith("/prostheses/"+record_id+"/status"):
            data = route.request.post_data_json
            assert route.request.method == "PATCH" and data["status"] == "delivered" and data["entry_key"]
            writes.append(data)
            work["content"]["status"] = "delivered"
            work["id"] = "40000000-0000-4000-8000-000000000003"
            work["actions"] = ["completed"]
            return route.fulfill(json={"id":work["id"],"status":"delivered"})
        if path.endswith("/dental/laboratories"):
            return route.fulfill(json={"items":[]})
        if path.endswith("/dental/patients"):
            return route.fulfill(json={"items":[patient]})
        if path.endswith("/dental/patients/"+patient["id"]):
            return route.fulfill(json=patient)
        return shell.serve(route)

    page.route("**/*", serve)
    page.goto(shell.ORIGIN+"/panel?aba=proteses-odonto", wait_until="domcontentloaded")
    page.get_by_role("heading", name="Controle de próteses", exact=True).wait_for(timeout=15000)
    root = page.locator("#osDentalClinic")
    root.get_by_text("Em atraso",exact=True).wait_for()
    page.evaluate("(theme)=>document.documentElement.dataset.theme=theme",theme)
    page.evaluate("document.fonts.ready")
    assert page.locator('nav [data-os-route="proteses-odonto"]').count() >= 1
    root.get_by_label("Prazo",exact=True).select_option("overdue")
    root.get_by_label("Etapa",exact=True).select_option("working")
    root.get_by_text("Em atraso",exact=True).wait_for()
    assert root.evaluate("""el=>[...el.querySelectorAll('h2,h3,button,select,.os-badge')].every(node=>{
        const r=node.getBoundingClientRect(),b=el.getBoundingClientRect();
        return r.width>0&&r.left>=b.left-1&&r.right<=b.right+1&&r.right<=innerWidth+1;
    })"""), (width, theme)
    assert not page.evaluate("document.documentElement.scrollWidth>innerWidth+1"), (width, theme)
    shell.OUT.mkdir(parents=True,exist_ok=True)
    page.evaluate("scrollTo(0,0)")
    page.screenshot(path=str(shell.OUT / f"proteses-{width}-{theme}.png"),full_page=True,animations="disabled")
    root.get_by_label("Próxima ação").select_option("delivered")
    root.get_by_role("button",name="Atualizar etapa").focus()
    root.get_by_role("button",name="Atualizar etapa").press("Enter")
    root.get_by_text("Nenhum trabalho em atraso para este filtro.").wait_for()
    assert root.get_by_label("Prazo",exact=True).input_value() == "overdue"
    root.get_by_label("Etapa",exact=True).select_option("delivered")
    root.get_by_text("Em atraso",exact=True).wait_for()
    root.get_by_role("button",name="Laboratórios",exact=True).click()
    root.get_by_role("heading",name="Laboratórios",exact=True).wait_for()
    root.get_by_role("button",name="Voltar às próteses").click()
    assert not any("patients" in path or "exams" in path or "records" in path for path in dental_reads)
    root.get_by_role("button",name="Abrir prontuário").click()
    root.get_by_role("tab",name="Próteses").wait_for()
    assert root.get_by_role("tab",name="Próteses").get_attribute("aria-selected") == "true"
    assert len(writes) == 1 and not errors,errors
    page.close()


if __name__ == "__main__":
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=os.getenv("ORION_TEST_CHROME") or None,
                                             args=["--no-sandbox","--disable-dev-shm-usage"])
        for width,height in ((320,720),(390,844),(820,1180),(1440,980)):
            for theme in ("light","dark"):
                verify(browser,width,height,theme)
        browser.close()
    print("Controle de próteses, prazos e vínculo com a ficha testados em oito combinações de tela e tema.")
