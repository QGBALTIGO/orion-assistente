"""Portal first access using synthetic API contracts, without sending real mail."""
from pathlib import Path
import json
import mimetypes
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright,expect

ROOT=Path(__file__).resolve().parents[2]/"app/static"
TENANT="00000000-0000-4000-8000-000000000001"
BASE="https://orion.test"
API=f"/api/v1/entregador/{TENANT}"

def run(browser,width,height):
    context=browser.new_context(viewport={"width":width,"height":height},service_workers="block")
    page=context.new_page()
    errors=[]
    requests=[]
    page.on("pageerror",lambda e:errors.append(str(e)))
    verified=False
    logged=False
    def handle(route):
        nonlocal verified,logged
        req=route.request
        path=urlsplit(req.url).path
        def send(data,status=200):
            return route.fulfill(status=status,content_type="application/json",body=json.dumps(data))
        if path=="/auth/me":
            return send({"csrf":"synthetic","account_id":"synthetic"}) if logged else send({"detail":"Faça login"},401)
        if path=="/auth/features":return send({"registration":True})
        if path=="/auth/register":
            data=req.post_data_json
            assert data["email"]=="motorista@example.test" and data["name"]=="Entregador teste"
            requests.append(path)
            return send({"verificationRequired":True},201)
        if path=="/auth/verify":
            assert req.post_data_json=={"email":"motorista@example.test","code":"123456"}
            verified=True
            requests.append(path)
            return send({"ok":True})
        if path=="/auth/login":
            assert verified and req.post_data_json["username"]=="motorista@example.test"
            logged=True
            requests.append(path)
            return send({"ok":True})
        if path==API+"/queue":
            assert logged
            return send({"name":"Entregador teste","state":"off","orders":[],"available_orders":[],"max_stops":5,"route_v2":True})
        if path==API+"/history":return send({"items":[],"totals":{},"has_more":False})
        if path.startswith("/entregador/") and not path.endswith(".webmanifest"):
            return route.fulfill(status=200,content_type="text/html",body=(ROOT/"orion-delivery-driver.html").read_bytes())
        if path.startswith(("/panel-assets/","/brand/")):
            f=ROOT/path.rsplit("/",1)[-1]
            if f.is_file():return route.fulfill(status=200,content_type=mimetypes.guess_type(str(f))[0] or "application/octet-stream",body=f.read_bytes())
        return route.fulfill(status=404,body="")
    page.route(BASE+"/**",handle)
    page.goto(BASE+"/entregador/"+TENANT+"/")
    expect(page.locator("#login")).to_be_visible()
    expect(page.locator("#activate-link")).to_be_hidden()
    page.goto(BASE+"/entregador/"+TENANT+"/?primeiro-acesso=1")
    page.get_by_role("button",name="Criar minha senha").click()
    expect(page.get_by_role("heading",name="Primeiro acesso")).to_be_visible()
    page.get_by_label("Seu nome").fill("Entregador teste")
    page.get_by_label("E-mail autorizado").fill("motorista@example.test")
    page.get_by_label("Crie sua senha").fill("synthetic-test-123")
    page.get_by_role("button",name="Criar conta e confirmar e-mail").click()
    expect(page.locator("#activate-verify")).to_be_visible()
    page.get_by_label("Código de confirmação").fill("123456")
    page.get_by_role("button",name="Confirmar e-mail").click()
    expect(page.locator("#login")).to_be_visible()
    expect(page.locator("#login input[name=username]")).to_have_value("motorista@example.test")
    page.locator("#login input[name=password]").fill("synthetic-test-123")
    page.get_by_role("button",name="Entrar",exact=True).click()
    expect(page.get_by_role("heading",name="Olá, Entregador!")).to_be_visible()
    assert requests==["/auth/register","/auth/verify","/auth/login"],requests
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert not errors,errors
    print(f"PASS first access {browser.browser_type.name} {width}x{height}")
    context.close()

if __name__=="__main__":
    with sync_playwright() as p:
        for name in ("chromium","webkit"):
            browser=getattr(p,name).launch()
            try:
                for w,h in ((320,740),(390,844),(1366,900)):run(browser,w,h)
            finally:browser.close()
