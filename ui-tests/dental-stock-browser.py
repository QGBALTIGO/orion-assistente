"""Dedicated inventory route, search and movement through the real Orion shell."""
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
    errors, writes = [], []
    page.on("pageerror", lambda event: errors.append(str(event)))
    material = {"id": "30000000-0000-4000-8000-000000000001", "name": "Gaze estéril de teste",
                "quantity": 12, "unit": "box", "version": 1}

    def serve(route):
        url = urlsplit(route.request.url)
        if url.path.endswith("/management/permissions"):
            return route.fulfill(json={"effective_plan": {**shell.PLAN, "dental_clinical_enabled": True}})
        if url.path.endswith("/dental/access"):
            return route.fulfill(json={"clinical_access": None, "can_manage_inventory": True,
                                       "can_manage": True, "can_read_records": False, "grants": []})
        if url.path.endswith("/dental/supplies"):
            query = parse_qs(url.query).get("q", [""])[0]
            items = [material] if query.lower() in material["name"].lower() else []
            return route.fulfill(json={"items": items, "next_cursor": None})
        if url.path.endswith("/movements") and route.request.method == "POST":
            change = route.request.post_data_json
            assert change["movement"] == "out" and change["quantity"] == 2 and change["entry_key"]
            writes.append(change)
            material["quantity"] -= change["quantity"]
            return route.fulfill(json={"quantity": material["quantity"], "replayed": False})
        if "/dental/patients" in url.path or "/records" in url.path or "/exams" in url.path:
            errors.append("O estoque requisitou a ficha clínica")
        return shell.serve(route)

    page.route("**/*", serve)
    page.goto(shell.ORIGIN + "/panel?aba=estoque-odonto", wait_until="domcontentloaded")
    page.get_by_role("heading", name="Estoque odontológico").wait_for(timeout=13000)
    page.get_by_text(material["name"], exact=True).wait_for()
    page.evaluate("(theme)=>document.documentElement.dataset.theme=theme", theme)
    page.evaluate("document.fonts.ready")
    root = page.locator("#osDentalClinic")
    page.get_by_label("Buscar material").fill("inexistente")
    page.get_by_role("button", name="Buscar", exact=True).click()
    root.get_by_text("Nenhum material corresponde à busca.").wait_for()
    page.get_by_label("Buscar material").fill("GAZE")
    page.get_by_label("Ordenação").select_option("desc")
    page.get_by_label("Buscar material").press("Enter")
    root.get_by_text("12 cx.").wait_for()
    assert root.evaluate("""el=>[...el.querySelectorAll('h2,button,input,select,strong,summary')].filter(node=>node.checkVisibility()).every(node=>{
      const r=node.getBoundingClientRect(),bounds=el.getBoundingClientRect();
      return r.width>0&&r.left>=bounds.left-1&&r.right<=bounds.right+1&&r.right<=innerWidth+1;
    })"""), (width,theme)
    assert not page.evaluate("document.documentElement.scrollWidth>innerWidth+1"), (width,theme)
    shell.OUT.mkdir(parents=True, exist_ok=True)
    root.screenshot(path=str(shell.OUT / f"estoque-{width}-{theme}.png"), animations="disabled")
    root.get_by_role("button", name="Saída", exact=True).click()
    root.get_by_label("Quantidade").fill("2")
    root.get_by_role("button", name="Registrar saída").focus()
    root.get_by_role("button", name="Registrar saída").press("Enter")
    root.get_by_text("10 cx.").wait_for()
    assert len(writes) == 1 and not errors, errors
    page.close()


if __name__ == "__main__":
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=os.getenv("ORION_TEST_CHROME") or None,
                                             args=["--no-sandbox", "--disable-dev-shm-usage"])
        for width,height in ((320,720),(390,844),(820,1180),(1440,980)):
            for theme in ("light","dark"):
                verify(browser,width,height,theme)
        browser.close()
    print("Estoque testado em oito combinações de tela e tema.")
