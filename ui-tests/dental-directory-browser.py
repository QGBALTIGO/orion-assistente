"""Exercise the dashboard shortcut and actual dental directory with an isolated clinic."""
from importlib.util import module_from_spec, spec_from_file_location
import os
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from playwright.sync_api import sync_playwright

spec = spec_from_file_location("shell", Path(__file__).with_name("dental-reception-browser.py"))
shell = module_from_spec(spec)
spec.loader.exec_module(shell)


def verify(browser, width, height, theme):
    page = browser.new_page(viewport={"width":width,"height":height}, color_scheme=theme, locale="pt-BR")
    patients, submissions, reads, errors = {}, [], [], []
    page.on("pageerror", lambda error:errors.append(str(error)))
    family = "Família sintética"
    tel = "(67) 99999-1030"
    def serve(route):
        path = urlsplit(route.request.url).path
        method = route.request.method
        if path.endswith("/management/permissions"):
            return route.fulfill(json={"effective_plan":{**shell.PLAN, "dental_clinical_enabled": True}})
        if path.endswith("/dental/access"):
            return route.fulfill(json={"clinical_access":{"level":"assistant"}, "can_read_records":False, "can_manage":False})
        if "/dental/" in path:
            reads.append((method,path))
        if path.endswith("/dental/reception/patients/search"):
            assert method == "POST"
            query = route.request.post_data_json["query"].lower()
            found = [p for p in patients.values() if query in p["name"].lower() or query in "67999991030"]
            return route.fulfill(json={"items":found,"has_more":False})
        if path.endswith("/dental/reception/patients") and method == "POST":
            data = route.request.post_data_json
            submissions.append(data)
            key = data["request_id"]
            if key not in patients:
                patients[key] = {**data,"id":str(uuid4()),"version":1}
            if len(submissions) == 1:
                return route.fulfill(status=503,json={"detail":"Resposta interrompida em teste"})
            return route.fulfill(status=201,json=patients[key])
        if path.endswith("/visits"):
            return route.fulfill(json={"items":[],"has_more":False})
        if path.endswith("/visit-options"):
            return route.fulfill(json={"day":"2026-11-22","timezone":"America/Campo_Grande","items":[],"has_more":False})
        if "/dental/reception/patients/" in path:
            value=next((p for p in patients.values() if path.endswith(p["id"])),None)
            if value:
                if method == "PATCH":
                    data=route.request.post_data_json
                    assert data["expected_version"] == value["version"]
                    value.update({**data,"version":value["version"]+1})
                return route.fulfill(json=value)
        return shell.serve(route)

    page.route("**/*",serve)
    page.goto(shell.ORIGIN+"/panel?aba=dashboard",wait_until="domcontentloaded")
    page.get_by_role("button",name="Novo paciente",exact=True).click(timeout=15000)
    page.evaluate("(value)=>document.documentElement.dataset.theme=value",theme)
    page.evaluate("document.fonts.ready")
    form = page.locator("[data-dental-patient-editor]")
    form.get_by_label("Nome do paciente").fill("Ana de Teste")
    form.locator("summary").get_by_text("Responsável legal",exact=True).click()
    form.get_by_label("Nome do responsável legal").fill(family)
    form.get_by_label("Celular do responsável",exact=True).fill(tel)
    submit = form.get_by_role("button",name="Cadastrar paciente")
    submit.press("Enter")
    page.locator("#osDentalDirectory [role=alert]").wait_for()
    assert form.get_by_label("Nome do paciente").input_value() == "Ana de Teste"
    submit.press("Enter")
    person=page.locator("#osDentalDirectory")
    person.get_by_role("heading",name="Ana de Teste").wait_for()
    assert len(submissions)==2 and submissions[0]["request_id"]==submissions[1]["request_id"]
    assert not any(path.endswith("/access") for _,path in reads)
    person.get_by_role("button",name="Clínica",exact=True).click()
    person.get_by_text("Os dados clínicos exigem acesso de cirurgião-dentista.").wait_for()
    assert not person.get_by_role("button",name="Prontuário",exact=True).count()
    person.get_by_role("button",name="Editar cadastro").click()
    form.get_by_label("Nome do paciente").fill("Ana Helena de Teste")
    form.get_by_role("button",name="Salvar alterações").press("Enter")
    person.get_by_role("heading",name="Ana Helena de Teste").wait_for()
    person.get_by_role("button",name="Voltar aos pacientes").click()
    person.get_by_role("button",name="Novo paciente",exact=True).click()
    form.get_by_label("Nome do paciente").fill("Bia de Teste")
    form.locator("summary").get_by_text("Responsável legal",exact=True).click()
    form.get_by_label("Nome do responsável legal").fill(family)
    form.get_by_label("Celular do responsável",exact=True).fill(tel)
    submit.press("Enter")
    person.get_by_role("heading",name="Bia de Teste").wait_for()
    person.get_by_role("button",name="Voltar aos pacientes").click()
    search = person.get_by_label("Buscar pacientes")
    search.fill("999991030")
    search.press("Enter")
    person.get_by_role("button",name="Ana Helena de Teste",exact=False).wait_for()
    assert person.locator("li").count()==2 and len(patients)==2
    assert not page.evaluate("document.documentElement.scrollWidth>innerWidth+1")
    assert person.evaluate("""node=>[...node.querySelectorAll('button,input,h2')].every(el=>{
       const r=el.getBoundingClientRect(), b=node.getBoundingClientRect();
       return r.width>0&&r.left>=b.left-1&&r.right<=b.right+1&&r.right<=innerWidth+1;
    })""")
    shell.OUT.mkdir(parents=True,exist_ok=True)
    person.screenshot(path=str(shell.OUT/f"pacientes-{width}-{theme}.png"),animations="disabled")
    person.get_by_role("button",name="Bia de Teste",exact=False).click()
    person.get_by_role("button",name="Consultas",exact=True).click()
    page.get_by_text("Nenhuma consulta vinculada.").wait_for()
    assert all("/records" not in path and "/exams" not in path for _,path in reads)
    assert not errors,errors
    page.close()


if __name__ == "__main__":
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(executable_path=os.getenv("ORION_TEST_CHROME") or None,
                                           args=["--no-sandbox","--disable-dev-shm-usage"])
        for width,height in ((320,720),(390,844),(820,1180),(1440,980)):
            for theme in ("light","dark"):
                verify(browser,width,height,theme)
        browser.close()
    print("Cadastro, edição, busca familiar e consultas percorridos em oito telas e temas.")
