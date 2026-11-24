"""Exercise the failed-login screen with real mobile browser emulation and assets.

Verify actual element geometry, not just whether horizontal scroll is hidden.
Only mocked, synthetic data; no live customer access.
"""
import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import expect, sync_playwright

from browser_public_cache_profile import URL, SLUG, routes
from browser_public_customer_area import DASHBOARD, PHONE

STATIC = Path(__file__).resolve().parents[2] / "app/static"
ERROR = ("Não foi possível acessar. Confira seu telefone e data de nascimento cadastrados. "
         "Se ainda não agendou, faça seu primeiro agendamento. "
         "Para corrigir dados antigos, fale com o estabelecimento.")


def assert_geometry(page, *, login=False, error=False):
    """Check the dialog and its individual boxes, including native DOB."""
    dimensions = page.evaluate("""({login,error}) => {
      const d=document.querySelector('#customer-area-dialog');
      const rect=n=>{const r=n.getBoundingClientRect();return {left:r.left,right:r.right,width:r.width,height:r.height,top:r.top,bottom:r.bottom}};
      const box=rect(d), body=document.querySelector('#customer-area-body'), content=rect(body);
      const cs=getComputedStyle(body), left=content.left+parseFloat(cs.paddingLeft), right=content.right-parseFloat(cs.paddingRight);
      const names=['.customer-area-header','.customer-company','.customer-area-body','.customer-area-shell'];
      if(login)names.push('.customer-area-form','.customer-area-field > input[name=phone]','.customer-area-field > input[name=birth_date]','.customer-area-form button[type=submit]','.customer-area-check','.customer-area-login-note');
      if(error)names.push('#customer-area-feedback');
      const boxes=Object.fromEntries(names.map(s=>[s,rect(d.querySelector(s))]));
      return {box,viewport:innerWidth,height:innerHeight,scrollWidth:d.scrollWidth,clientWidth:d.clientWidth,bodyScrollWidth:body.scrollWidth,bodyClientWidth:body.clientWidth,rootScrollWidth:document.documentElement.scrollWidth,left,right,boxes};
    }""", {"login":login,"error":error})
    box=dimensions["box"]
    assert box["left"] >= -1 and box["right"] <= dimensions["viewport"]+1, dimensions
    assert box["top"] >= -1 and box["bottom"] <= dimensions["height"]+1, dimensions
    assert dimensions["scrollWidth"] <= dimensions["clientWidth"]+1, dimensions
    assert dimensions["bodyScrollWidth"] <= dimensions["bodyClientWidth"]+1, dimensions
    assert dimensions["rootScrollWidth"] <= dimensions["viewport"]+1, dimensions
    for selector, element in dimensions["boxes"].items():
        assert element["left"] >= box["left"]-1 and element["right"] <= box["right"]+1, (selector,dimensions)
    if login:
        for name in ("phone","birth_date"):
            field=dimensions["boxes"][f".customer-area-field > input[name={name}]"]
            assert abs(field["left"]-dimensions["left"])<=1, dimensions
            assert abs(field["right"]-dimensions["right"])<=1, dimensions
            assert 44<=field["height"]<=49, dimensions
        phone=dimensions["boxes"][".customer-area-field > input[name=phone]"]
        dob=dimensions["boxes"][".customer-area-field > input[name=birth_date]"]
        assert abs(phone["height"]-dob["height"])<=1, dimensions
        submit=dimensions["boxes"][".customer-area-form button[type=submit]"]
        assert abs(submit["left"]-phone["left"])<=1 and abs(submit["right"]-phone["right"])<=1, dimensions
        if error:
            feedback=dimensions["boxes"]["#customer-area-feedback"]
            assert abs(feedback["left"]-phone["left"])<=1 and abs(feedback["right"]-phone["right"])<=1, dimensions


@pytest.mark.parametrize("browser_name,width,height,theme,mobile", [
    ("webkit",320,568,"dark",True),
    ("webkit",375,667,"light",True),
    ("webkit",390,844,"dark",True),
    ("webkit",430,740,"dark",True),
    ("webkit",844,390,"dark",True),
    ("webkit",768,1024,"light",True),
    ("chromium",360,640,"dark",True),
    ("chromium",412,860,"light",True),
    ("chromium",1280,800,"dark",False),
])
def test_customer_portal_fits_native_date_error_and_history(browser_name,width,height,theme,mobile):
    with sync_playwright() as playwright:
        browser=getattr(playwright,browser_name).launch(headless=True)
        context=browser.new_context(
            viewport={"width":width,"height":height}, locale="pt-BR",
            is_mobile=mobile, has_touch=mobile, device_scale_factor=3 if mobile else 1,
            color_scheme=theme,
        )
        page=context.new_page()
        errors,posted,calls=[],[],[]
        page.on("pageerror",lambda error: errors.append(str(error)))
        routes(page,{"offline":False},posted)

        # Load actual HTML-linked styles/scripts/fonts in production order.
        def asset(route):
            filename=urlsplit(route.request.url).path.rsplit("/",1)[-1]
            suffix=Path(filename).suffix
            types={".css":"text/css",".js":"application/javascript",".svg":"image/svg+xml",".woff2":"font/woff2",".woff":"font/woff"}
            path=STATIC/filename
            if suffix in types and path.is_file():
                route.fulfill(status=200,content_type=types[suffix],body=path.read_bytes())
            else:
                route.abort()
        page.route("**/public-assets/*",asset)
        page.route("**/brand/*",asset)

        session={"valid":False}
        def account(route):
            req=route.request
            path=urlsplit(req.url).path
            calls.append((req.method,path))
            if path.endswith("/enter"):
                payload=req.post_data_json
                assert payload["phone"]==PHONE
                if payload["birth_date"]!="1990-07-06":
                    route.fulfill(status=403,content_type="application/json",body=json.dumps({"detail":ERROR}))
                    return
                session["valid"]=True
                result={**DASHBOARD,"login_method":"birth_date"}
            elif path.endswith("/appointments"):
                long_service="Corte e finalização especial "+"detalhamento"*12
                result={"items":[{**DASHBOARD["upcoming"]["items"][0],"services":[long_service],"professional":"Profissional "+("NomeLongo"*12)}],"has_more":False,"next_offset":1}
            else:
                result={**DASHBOARD,"login_method":"birth_date"} if session["valid"] else {"available":True,"authenticated":False,"login_method":"birth_date"}
            route.fulfill(status=200,content_type="application/json",body=json.dumps(result))
        page.route(f"**/api/v1/public/sites/{SLUG}/account**",account)
        try:
            page.goto(URL,wait_until="domcontentloaded")
            page.locator("#site-content").wait_for(state="visible")
            page.locator("#customer-area-trigger").click()
            dialog=page.locator("#customer-area-dialog")
            expect(dialog.get_by_text("Acesse sua área do cliente")).to_be_visible()
            page.evaluate("document.fonts.ready")
            assert_geometry(page,login=True)
            dialog.locator('input[name="phone"]').fill("86972676282")
            dialog.locator('input[name="birth_date"]').fill("2004-07-24")
            assert_geometry(page,login=True)
            dialog.get_by_role("button",name="Acessar minha conta").click()
            expect(dialog.locator("#customer-area-feedback")).to_have_text(ERROR)
            assert_geometry(page,login=True,error=True)
            # Simulate the reduced visible viewport while using a phone.
            if mobile and height>500:
                page.set_viewport_size({"width":width,"height":max(360,height-260)})
                assert_geometry(page,login=True,error=True)
                page.set_viewport_size({"width":width,"height":height})
                assert_geometry(page,login=True,error=True)
            # Long error must leave both controls and submit reachable.
            dialog.locator('input[name="birth_date"]').fill("1990-07-06")
            dialog.get_by_role("button",name="Acessar minha conta").click()
            expect(dialog.get_by_text("Olá, Cliente!")).to_be_visible()
            assert_geometry(page)
            dialog.get_by_role("button",name="Histórico",exact=True).click()
            expect(dialog.locator(".customer-area-card")).to_have_count(1)
            assert_geometry(page)
            dialog.get_by_role("button",name="Meus dados",exact=True).click()
            expect(dialog.get_by_text("06/07/1990",exact=True)).to_be_visible()
            assert_geometry(page)
            dialog.locator("#customer-area-close").click()
            expect(dialog).not_to_be_visible()
            assert not posted and not errors,(posted,errors)
            assert not any(path.endswith("/link-login") for _,path in calls)
        finally:
            context.close()
            browser.close()
