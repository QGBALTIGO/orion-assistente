"""Navigate the live Orion shell with synthetic clinic permissions at each supported width."""
import importlib.util
import os
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import sync_playwright

spec = importlib.util.spec_from_file_location("shell", Path(__file__).with_name("dental-reception-browser.py"))
shell = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shell)


def check_menu(browser, width, height, theme):
    page = browser.new_page(viewport={"width":width,"height":height}, color_scheme=theme, locale="pt-BR")
    errors, accesses = [], []
    opportunities = []
    stages = [
        {"id": key, "name": name, "custom": False}
        for key, name in [("open", "Em aberto"), ("in_progress", "Em andamento"),
                          ("won", "Ganha"), ("lost", "Perdida")]
    ]
    page.on("pageerror", lambda error: errors.append(str(error)))

    def serve(route):
        path = urlsplit(route.request.url).path
        if path.endswith("/management/permissions"):
            return route.fulfill(json={"effective_plan": {**shell.PLAN, "dental_clinical_enabled":True}})
        if "/dental/" in path:
            accesses.append(path.split("/dental/")[1])
        if path.endswith("/dental/access"):
            return route.fulfill(json={"clinical_access":None,"can_manage":True,"can_manage_inventory":True,"grants":[]})
        if path.endswith("/dental/chair-settings"):
            return route.fulfill(json={"items":[]})
        if path.endswith("/dental/sales/opportunities/search"):
            details = route.request.post_data_json
            assert 2 <= len(details["query"]) <= 80
            query = details["query"].casefold()
            visible = [item for item in opportunities
                       if query in (item["title"] + " " + item["description"]).casefold()]
            return route.fulfill(json={"items":visible,"next":None})
        if path.endswith("/dental/sales/opportunities"):
            if route.request.method == "POST":
                details = route.request.post_data_json
                assert details["id"] and details["description"] == "Retorno solicitado pela família."
                item = {**details, "created_by":"conta-sintética", "created_at":"2026-11-22T15:00:00Z"}
                opportunities.insert(0, item)
                return route.fulfill(status=201,json=item)
            query = parse_qs(urlsplit(route.request.url).query)
            start = query.get("start_date", [""])[0]
            end = query.get("end_date", [""])[0]
            visible = [item for item in opportunities
                       if (not start or item["created_at"][:10] >= start) and (not end or item["created_at"][:10] <= end)]
            return route.fulfill(json={"items": visible, "next": None})
        if path.endswith("/dental/sales/stages"):
            if route.request.method == "POST":
                data = route.request.post_data_json
                assert data["name"] == "Em revisão" and data["id"]
                stages.insert(2, {**data, "custom": True})
            return route.fulfill(json={"items": stages})
        return shell.serve(route)

    def menu():
        toggle = page.get_by_role("button",name="Abrir menu",exact=True)
        if toggle.is_visible() and toggle.get_attribute("aria-expanded") != "true":
            toggle.click()
        return page.locator("#osNav")

    page.route("**/*", serve)
    page.goto(shell.ORIGIN+"/panel?aba=dashboard",wait_until="domcontentloaded")
    page.get_by_role("heading",name="Recepção",exact=True).wait_for(timeout=15000)
    page.evaluate("(theme)=>document.documentElement.dataset.theme=theme",theme)
    page.evaluate("document.fonts.ready")
    nav = menu()
    for label in ["Recepção","Agenda","Pacientes","Prontuários","Controle de próteses","Estoque","Financeiro","Ajustes da clínica"]:
        assert nav.get_by_role("button",name=label,exact=True).count() == 1, (width,label)
    assert not nav.get_by_role("button",name="Procedimentos",exact=True).count()
    assert not nav.get_by_role("button",name="Equipe clínica",exact=True).count()
    sidebar_context=page.locator("#osSidebarContext")
    assert sidebar_context.evaluate("""el=>{
        const end=el.querySelector('.os-sidebar-badges').getBoundingClientRect().bottom;
        return el.getBoundingClientRect().bottom-end<=24;
    }"""),(width,theme)
    shell.OUT.mkdir(parents=True,exist_ok=True)
    page.screenshot(path=str(shell.OUT/f"menu-{width}-{theme}.png"),animations="disabled")
    setup = nav.get_by_role("button",name="Ajustes da clínica",exact=True)
    setup.focus()
    setup.press("Enter")
    page.get_by_role("heading",name="Ajustes da clínica",exact=True).wait_for()
    group = page.locator("#osPageBody")
    chair = group.get_by_role("button",name="Consultórios e cadeiras",exact=True)
    chair.wait_for()
    assert not any(p.startswith(("patients","access","prostheses","supplies")) for p in accesses), accesses
    assert not page.evaluate("document.documentElement.scrollWidth>innerWidth+1"), (width,theme)
    assert group.evaluate("""el=>[...el.querySelectorAll('button')].every(n=>{
        const b=n.getBoundingClientRect();return b.width>0&&b.height>=44&&b.left>=-1&&b.right<=innerWidth+1;
    })"""),(width,theme)
    page.screenshot(path=str(shell.OUT/f"ajustes-{width}-{theme}.png"),animations="disabled")
    chair.focus()
    chair.press("Enter")
    page.get_by_text("Nenhuma cadeira cadastrada",exact=True).wait_for(timeout=8000)
    assert "chair-settings" in accesses
    assert menu().locator('[aria-current="page"]').inner_text() == "Ajustes da clínica"
    menu().get_by_role("button", name="Ajustes da clínica", exact=True).click()
    group.get_by_role("button", name="Etapas de vendas", exact=True).click()
    group.get_by_role("button", name="Nova etapa", exact=True).press("Enter")
    assert page.get_by_role("heading", name="Ajustes da clínica", exact=True).count() == 1
    page.get_by_label("Nome da etapa").fill("Em revisão")
    page.get_by_role("button", name="Criar etapa", exact=True).press("Enter")
    stage_list = group.get_by_role("list", name="Ordem das etapas")
    stage_list.get_by_text("Em revisão", exact=True).wait_for()
    assert [r["name"] for r in stages] == ["Em aberto", "Em andamento", "Em revisão", "Ganha", "Perdida"]
    assert stage_list.evaluate("""el => [...el.children].every(row => {
        const label = row.firstElementChild.getBoundingClientRect();
        const type = row.lastElementChild.getBoundingClientRect();
        return label.right <= type.left + 1 && type.right <= window.innerWidth + 1;
    })"""), (width, theme)
    assert not page.evaluate("document.documentElement.scrollWidth > innerWidth + 1"), (width, theme)
    stage_list.scroll_into_view_if_needed()
    page.screenshot(path=str(shell.OUT / f"etapas-{width}-{theme}.png"), animations="disabled")
    menu().get_by_role("button", name="Vendas", exact=True).press("Enter")
    page.get_by_text("Nenhuma oportunidade em aberto", exact=True).wait_for()
    create = page.get_by_role("button", name="Nova oportunidade", exact=True)
    if width <= 760:
        assert create.bounding_box()["height"] >= 44, (width, theme)
    create.press("Enter")
    page.get_by_label("Título", exact=True).fill("Avaliação da família")
    page.get_by_label("Descrição (opcional)").fill("Retorno solicitado pela família.")
    page.get_by_role("button", name="Criar oportunidade", exact=True).press("Enter")
    page.get_by_text("Avaliação da família", exact=True).wait_for()
    assert len(opportunities) == 1
    search = page.get_by_role("search")
    search.get_by_label("Buscar oportunidades por título ou descrição").fill("família")
    search.get_by_role("button",name="Buscar oportunidades").press("Enter")
    page.get_by_text("Avaliação da família",exact=True).wait_for()
    assert search.get_by_role("button",name="Limpar busca").count() == 1
    controls = search.evaluate("""el=>[...el.querySelectorAll('input,button')].map(n=>{
        const b=n.getBoundingClientRect();return [b.left,b.right,b.height];
    })""")
    assert all(left>=0 and right<=width+1 and size>=40 for left,right,size in controls),(width,theme,controls)
    search.get_by_role("button",name="Limpar busca").press("Enter")
    row = page.locator("#osDentalSalesList li").first
    assert row.evaluate("""el=>{
       const title=el.firstElementChild.getBoundingClientRect(), date=el.lastElementChild.getBoundingClientRect();
       return title.right<=date.left+1 && date.right<=innerWidth+1;
    }"""), (width,theme)
    assert not page.evaluate("document.documentElement.scrollWidth > innerWidth+1"), (width,theme)
    page.screenshot(path=str(shell.OUT/f"vendas-{width}-{theme}.png"), animations="disabled")
    picker = page.locator(".os-dental-sales-period")
    picker.locator("summary").press("Enter")
    picker.get_by_label("De",exact=True).fill("2026-11-23")
    picker.get_by_role("button",name="Aplicar período").press("Enter")
    page.get_by_text("Nenhuma oportunidade neste período",exact=True).wait_for()
    assert not page.evaluate("document.documentElement.scrollWidth>innerWidth+1"), (width,theme)
    controls = picker.evaluate("""el=>[...el.querySelectorAll('input,button,summary')].map(n=>{
        const b=n.getBoundingClientRect();return [n.name||n.textContent, b.left, b.right, b.height];
    })""")
    assert all(left>=0 and right<=width+1 and size>=44 for _,left,right,size in controls), (width,theme,controls)
    assert picker.locator("summary").inner_text().endswith("23/11/2026")
    page.locator("#osToastRegion .os-toast").first.wait_for(state="hidden",timeout=10000)
    page.screenshot(path=str(shell.OUT/f"periodo-vendas-{width}-{theme}.png"),animations="disabled")
    picker.get_by_role("button",name="Limpar período").press("Enter")
    row.get_by_text(opportunities[0]["title"],exact=True).wait_for()
    opportunities[0]["title"] = "R" * 40
    opportunities[0]["description"] = "D" * 300
    page.reload(wait_until="domcontentloaded")
    page.get_by_text("R" * 40, exact=True).wait_for()
    assert row.evaluate("""el=>el.scrollWidth<=el.clientWidth+1 &&
       el.firstElementChild.getBoundingClientRect().right <= el.lastElementChild.getBoundingClientRect().left+1""")
    assert not page.evaluate("document.documentElement.scrollWidth>innerWidth+1")
    assert not errors,errors
    page.close()


if __name__=="__main__":
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(executable_path=os.getenv("ORION_TEST_CHROME") or None,
                                           args=["--no-sandbox","--disable-dev-shm-usage"])
        for w,h in [(320,720),(390,844),(820,1180),(1440,980)]:
            for theme in ["light","dark"]:
                check_menu(browser,w,h,theme)
        browser.close()
    print("Menu, cadeiras, etapas e oportunidades aprovados em oito combinações de tela e tema.")
