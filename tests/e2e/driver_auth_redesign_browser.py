"""Check the actual courier login layout, recovery and authentication states.

All network calls are intercepted with synthetic data. No real emails, credentials,
or account changes. Both Chromium and WebKit exercise light and dark themes.
"""
from pathlib import Path
import json
import mimetypes
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[2]/"app/static"
TENANT="00000000-0000-4000-8000-000000000001"
BASE="https://orion.test"
API=f"/api/v1/entregador/{TENANT}"
ARTIFACTS=Path("artifacts/driver-auth")
ARTIFACTS.mkdir(parents=True,exist_ok=True)

def scenario(browser,w,h,dark):
    context=browser.new_context(viewport={"width":w,"height":h},color_scheme="dark" if dark else "light",
                                service_workers="block")
    page=context.new_page()
    errors=[]
    requests=[]
    page.on("pageerror",lambda e:errors.append(str(e)))
    state={"logged":False,"deny":False,"password":"synthetic-test-123"}
    def handle(route):
        req=route.request
        path=urlsplit(req.url).path
        def send(data,status=200):
            route.fulfill(status=status,content_type="application/json",body=json.dumps(data))
        if path=="/auth/me":
            return send({"csrf":"test-csrf","account_id":"synthetic"}) if state["logged"] else send({"detail":"Faça login no painel"},401)
        if path=="/auth/features":return send({"registration":True,"passwordRecovery":True})
        if path=="/auth/login":
            data=req.post_data_json
            requests.append(path)
            if data["password"]!=state["password"]:
                return send({"detail":"E-mail ou senha inválidos."},401)
            state["logged"]=True
            return send({"ok":True})
        if path=="/auth/password/forgot":
            assert req.post_data_json=={"email":"motorista@example.test"}
            requests.append(path)
            return send({"ok":True})
        if path=="/auth/password/reset":
            data=req.post_data_json
            assert data["email"]=="motorista@example.test" and data["code"]=="123456"
            state["password"]=data["password"]
            requests.append(path)
            return send({"ok":True})
        if path==API+"/queue":
            if state["deny"]:return send({"detail":"Sua conta não tem acesso às entregas desta empresa."},403)
            assert state["logged"]
            return send({"name":"Entregador teste","state":"off","orders":[],"available_orders":[],"max_stops":5,"route_v2":True})
        if path==API+"/history":return send({"items":[],"totals":{},"has_more":False})
        if path.startswith("/entregador/") and not path.endswith(".webmanifest"):
            return route.fulfill(status=200,content_type="text/html",body=(ROOT/"orion-delivery-driver.html").read_bytes())
        if path.startswith(("/panel-assets/","/brand/")):
            f=ROOT/path.rsplit("/",1)[-1]
            if f.is_file():return route.fulfill(status=200,content_type=mimetypes.guess_type(str(f))[0] or "application/octet-stream",body=f.read_bytes())
        return route.fulfill(status=404,body="")
    page.route(BASE+"/**",handle)
    url=BASE+"/entregador/"+TENANT+"/"
    page.goto(url+"?primeiro-acesso=1")
    expect(page.get_by_role("heading",name="Central do Entregador",exact=True)).to_be_visible()
    expect(page.locator("#auth-loading")).to_be_hidden()
    expect(page.locator("#auth-retry")).to_be_hidden()
    expect(page.locator("#retry")).to_be_hidden()
    expect(page.locator("#auth-feedback")).to_have_text("")
    expect(page.locator("#notice")).to_have_text("")
    expect(page.locator("#workspace")).to_be_hidden()
    expect(page.get_by_role("button",name="Criar minha senha")).to_be_visible()
    assert page.locator(".logo.dark" if dark else ".logo.light").is_visible()
    logo=page.locator(".logo.dark" if dark else ".logo.light")
    assert logo.get_attribute("src")=="/brand/orion-brand-"+("dark" if dark else "light")+".svg"
    assert page.locator(".driver-auth-visual").is_visible()==(w>900)
    page.evaluate("document.fonts.ready")
    assert page.evaluate("document.fonts.check('500 16px Figtree') && document.fonts.check('600 26px Outfit')")
    def fits():
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"),(w,h,dark)
        assert page.locator(".driver-auth-card").bounding_box()["width"]<=w
    fits()
    if w<=560:
        box=page.locator(".driver-auth-card").bounding_box()
        assert box["y"]<100,box
        submit=page.locator("#login button[type=submit]").bounding_box()
        assert submit["y"]+submit["height"]<=h or h<590,submit
    page.screenshot(path=str(ARTIFACTS/f"{browser.browser_type.name}-{w}-{'dark' if dark else 'light'}-login.png"),full_page=True)
    page.locator("#theme").click()
    assert page.locator("html").get_attribute("data-theme")==("light" if dark else "dark")
    assert page.locator(".logo.light" if dark else ".logo.dark").is_visible()
    page.locator("#theme").click()
    page.locator("#driver-email").fill("motorista@example.test")
    page.locator("#driver-password").fill("wrong-password")
    page.get_by_role("button",name="Mostrar senha").click()
    assert page.locator("#driver-password").get_attribute("type")=="text"
    page.get_by_role("button",name="Ocultar senha").click()
    assert page.locator("#driver-password").get_attribute("type")=="password"
    page.get_by_role("button",name="Entrar",exact=True).click()
    expect(page.locator("#auth-feedback")).to_have_text("E-mail ou senha inválidos.")
    expect(page.locator("#auth-feedback")).to_have_attribute("data-tone","error")
    expect(page.locator("#auth-retry")).to_be_hidden()
    page.get_by_role("button",name="Esqueci minha senha").click()
    expect(page.get_by_role("heading",name="Recuperar senha")).to_be_visible()
    expect(page.locator("#recovery-email")).to_have_value("motorista@example.test")
    fits()
    page.get_by_role("button",name="Enviar código").click()
    expect(page.locator("#recovery-reset")).to_be_visible()
    page.locator("#reset-code").fill("123456")
    page.locator("#reset-password").fill("new-synthetic-123")
    fits()
    page.screenshot(path=str(ARTIFACTS/f"{browser.browser_type.name}-{w}-{'dark' if dark else 'light'}-recovery.png"),full_page=True)
    page.get_by_role("button",name="Redefinir senha").click()
    expect(page.locator("#login")).to_be_visible()
    expect(page.locator("#auth-feedback")).to_have_text("Senha atualizada. Entre novamente.")
    expect(page.locator("#driver-password")).to_have_value("")
    state["deny"]=True
    page.locator("#driver-password").fill("new-synthetic-123")
    page.get_by_role("button",name="Entrar",exact=True).click()
    expect(page.locator("#auth-feedback")).to_have_text("Sua conta não tem acesso às entregas desta empresa.")
    expect(page.locator("#auth-retry")).to_be_hidden()
    state["deny"]=False
    page.locator("#driver-password").fill("new-synthetic-123")
    page.get_by_role("button",name="Entrar",exact=True).click()
    expect(page.get_by_role("heading",name="Olá, Entregador!")).to_be_visible()
    expect(page.locator("#driver-auth")).to_be_hidden()
    assert not page.locator("body").evaluate("(e)=>e.classList.contains('auth-mode')")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert requests==["/auth/login","/auth/password/forgot","/auth/password/reset","/auth/login","/auth/login"],requests
    assert not errors,errors
    print(f"PASS auth redesign {browser.browser_type.name} {w}x{h} {'dark' if dark else 'light'}")
    context.close()

if __name__=="__main__":
    with sync_playwright() as p:
        for name in ("chromium","webkit"):
            browser=getattr(p,name).launch()
            try:
                for w,h in [(320,568),(390,660),(430,932),(768,1024),(1366,900)]:
                    for dark in (False,True):
                        scenario(browser,w,h,dark)
            finally:browser.close()
