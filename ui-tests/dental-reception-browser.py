"""Visual and keyboard checks through the real panel entry point with a synthetic, offline clinic."""
import json
import mimetypes
import os
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "static"
OUT = Path(os.environ.get("ORION_VISUAL_DIR", ROOT / "artifacts" / "dental-reception"))
ORIGIN = "https://orion-qa.invalid"
TENANT = "10000000-0000-4000-8000-000000000001"
CLINIC = {"id": TENANT,"name":"Clínica de teste","slug":"clinica-de-teste","role":"owner","timezone":"America/Campo_Grande"}
FEATURES = ["workspace.access","dashboard.basic","agenda.manage","clients.manage","services.manage",
            "professionals.manage","finance.basic","notifications.basic","settings.manage","plan.manage","account.manage"]
PLAN = {"code":"essencial","active":True,"name":"Essencial","revision":"visual-fixture","features":FEATURES,
        "permissions":dict.fromkeys(FEATURES,True),"limits":{"max_professionals":2},"business_niche":"odontologia",
        "workspace_profile":"standard","platform_workspace":False,"layout_modes":["classic"],"dental_clinical_enabled":True}
SNAPSHOT = {"day":"2026-11-22","timezone":"America/Campo_Grande","counts":{"confirmed":7,"completed":3,"cancelled":1,"no_show":0},
            "upcoming":[{"start_at":"2026-11-22T19:30:00Z","end_at":"2026-11-22T20:10:00Z","professional":"Dra. Ana Oliveira"},
                        {"start_at":"2026-11-22T20:30:00Z","end_at":"2026-11-22T21:15:00Z","professional":"Dr. Miguel de Albuquerque e Vasconcelos"}]}


def serve(route):
    url = urlsplit(route.request.url)
    if url.hostname != "orion-qa.invalid":
        return route.abort()
    path = url.path
    if path == "/panel":
        return route.fulfill(content_type="text/html",body=(STATIC/"panel.html").read_bytes())
    if path.startswith(("/brand/","/panel-assets/")):
        asset = path.rsplit("/",1)[-1]
        file = STATIC / ("orion-suite.js" if asset=="orion-suite-live.js" else asset)
        if file.is_file():
            return route.fulfill(content_type=mimetypes.guess_type(file.name)[0] or "application/octet-stream",body=file.read_bytes())
        return route.fulfill(status=404,body="")
    if path.endswith("/management/finance/summary"):
        query = parse_qs(url.query)
        return route.fulfill(json={"period": {"start_date": query["start_date"][0], "end_date": query["end_date"][0]},
                                   "summary": {"income_cents": 24590, "expense_cents": 10000, "balance_cents": 14590,
                                               "pending_income_cents": 19700}})
    values = {
        "/auth/me":{"authenticated":True,"role":"member","csrf":"synthetic-test","name":"Pessoa sintética","email":"recepcao@example.invalid"},
        "/auth/features":{"registration":False,"passwordRecovery":False},
        "/api/v1/account/businesses":[CLINIC],
        "/api/v1/onboarding/entry":{"enabled":False,"kind":"dashboard"},
        f"/api/v1/tenants/{TENANT}/management/permissions":{"effective_plan":PLAN},
        f"/api/v1/tenants/{TENANT}/dental/reception/today":SNAPSHOT,
        f"/api/v1/tenants/{TENANT}/dental/reception/chair-occupancy":{"day":SNAPSHOT["day"],"hours_status":"open",
            "active_chairs":2,"capacity_seconds":57600,"reserved_seconds":18900},
    }
    return route.fulfill(json=values.get(path,[]))


def verify(browser, width, height, theme):
    page = browser.new_page(viewport={"width":width,"height":height},color_scheme=theme,device_scale_factor=1)
    errors=[]
    page.on("pageerror",lambda error:errors.append(str(error)))
    page.route("**/*",serve)
    page.goto(ORIGIN+"/panel?aba=dashboard",wait_until="domcontentloaded")
    page.locator("#osReceptionCounts dd").first.wait_for(timeout=13000)
    page.evaluate("(theme)=>document.documentElement.dataset.theme=theme",theme)
    page.evaluate("document.fonts.ready")
    assert page.get_by_role("heading",name="Recepção",exact=True).count()==1
    assert page.locator("#osReceptionCounts dd").all_text_contents()==["7","3","1","0"]
    assert page.locator("#osReceptionUpcoming time").first.inner_text()=="15:30"
    metrics=page.locator(".os-reception").evaluate("""root=>{
      const rect=node=>node.getBoundingClientRect();
      const cells=[...root.querySelectorAll('.os-reception-metrics>div')].map(rect);
      const overlap=(a,b)=>Math.min(a.right,b.right)-Math.max(a.left,b.left)>1 &&
                           Math.min(a.bottom,b.bottom)-Math.max(a.top,b.top)>1;
      return {overflow:document.documentElement.scrollWidth>innerWidth+1,
        outside:[...root.querySelectorAll('h1,h2,button,dt,dd,li,time,li>span,li>small')].some(node=>{
          const b=rect(node);return b.left < -1 || b.right>innerWidth+1 || node.scrollWidth>node.clientWidth+2;
        }),overlap:cells.some((a,i)=>cells.slice(i+1).some(b=>overlap(a,b))),
        columns:new Set(cells.map(c=>Math.round(c.left))).size,
        wrappedLabel:[...root.querySelectorAll('.os-reception-metrics dt span')].some(node=>node.getBoundingClientRect().height>parseFloat(getComputedStyle(node).lineHeight)+2),
        fg:getComputedStyle(root.querySelector('h2')).color,bg:getComputedStyle(root.querySelector('.os-reception-card')).backgroundColor};
    }""")
    assert not any(metrics[k] for k in ("overlap","overflow","outside","wrappedLabel")), (width,theme,metrics)
    assert metrics["columns"]==(2 if width<=820 else 4), (width,metrics)
    assert metrics["fg"]!=metrics["bg"]
    occupancy = page.get_by_role("region",name="Ocupação das cadeiras hoje")
    progress = occupancy.get_by_role("progressbar",name="Parcela do expediente reservada")
    progress.wait_for()
    assert progress.evaluate("p=>p.value/p.max") == 18900/57600
    assert occupancy.locator("strong").inner_text()=="32,8%"
    assert occupancy.locator("p").first.inner_text()=="Tempo reservado · 5 h 15 min de 16 h"
    occupancy.screenshot(path=str(OUT/f"cadeiras-{width}-{theme}.png"),animations="disabled")
    finance = page.locator("#osCashAmounts")
    finance.get_by_text("R$", exact=False).first.wait_for()
    finance_values = [text.replace("\xa0", " ") for text in finance.locator("dd").all_text_contents()]
    assert finance_values == ["R$ 245,90","R$ 100,00","R$ 145,90","R$ 197,00"], finance_values
    range_button=page.get_by_role("button",name="Mês até hoje")
    range_button.focus()
    range_button.press("Enter")
    assert range_button.get_attribute("aria-pressed")=="true"
    finance.get_by_text("R$", exact=False).first.wait_for()
    finance.screenshot(path=str(OUT/f"caixa-{width}-{theme}.png"),animations="disabled")
    button=page.get_by_role("button",name="Ver agenda",exact=True)
    page.screenshot(path=str(OUT/f"recepcao-{width}-{theme}.png"),animations="disabled")
    button.focus()
    assert button.evaluate("button=>button===document.activeElement")
    button.press("Enter")
    page.wait_for_function("document.querySelector('#page-suite')?.dataset.osCurrentRoute==='agenda'")
    assert not errors,errors
    page.close()
    return {"width":width,"theme":theme,**metrics}


if __name__=="__main__":
    OUT.mkdir(parents=True,exist_ok=True)
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(executable_path=os.getenv("ORION_TEST_CHROME") or None,
                                           args=["--no-sandbox","--disable-dev-shm-usage"])
        results=[verify(browser,w,h,theme) for w,h in [(320,720),(390,844),(820,1180),(1440,980)]
                 for theme in ("light","dark")]
        browser.close()
    (OUT/"verificacao.json").write_text(json.dumps(results,indent=2),encoding="utf-8")
    print(f"{len(results)} verificações de layout e teclado aprovadas.")
