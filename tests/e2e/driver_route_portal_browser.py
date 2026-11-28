"""Actual portal assets in Chromium and WebKit; API responses are synthetic.
Persistence and concurrency are tested separately against real PostgreSQL.
"""
from pathlib import Path
import json
import mimetypes

from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[2]/"app/static"
TENANT="00000000-0000-4000-8000-000000000001"
BASE="https://orion.test"
API=f"/api/v1/entregador/{TENANT}"
IDS=["00000000-0000-4000-8000-00000000000"+str(n) for n in (2,3)]
VARIANT="00000000-0000-4000-8000-000000000004"

def scenario(browser,width,height,dark):
    context=browser.new_context(viewport={"width":width,"height":height},color_scheme="dark" if dark else "light",service_workers="block")
    page=context.new_page()
    errors=[]
    page.on("pageerror",lambda error:errors.append(str(error)))
    orders=[]
    available=[{"id":i,"revision":1,"total_cents":1250,"address":{"neighborhood":"Centro","city":"Campo Grande","state":"MS"},
                "items":[{"variant_id":VARIANT,"name":"Água 20 L","quantity":1}],"payment_preference":"cash"} for i in IDS]
    receipts=[]
    requests=[]
    def handle(route):
        from urllib.parse import urlsplit
        req=route.request
        path=urlsplit(req.url).path
        def send(data,status=200):
            route.fulfill(status=status,content_type="application/json",body=json.dumps(data,ensure_ascii=False))
        if path=="/auth/me":
            return send({"csrf":"synthetic","account_id":"browser-test"})
        if path==API+"/queue":
            return send({"name":"João Silva","state":"available","orders":orders,"available_orders":available,"max_stops":5,"route_v2":True})
        if path==API+"/history":
            return send({"items":receipts,"totals":{"cash":sum(r["total_cents"] for r in receipts)},"has_more":False})
        if path.startswith(API) and req.method=="POST":
            payload=req.post_data_json
            requests.append((path,payload))
            if path.endswith("/reservations"):
                selected={r["order_id"] for r in payload["orders"]}
                for o in available[:]:
                    if o["id"] in selected:
                        available.remove(o)
                        orders.append({**o,"status":"reserved","revision":2,"customer_name":"Cliente "+str(len(orders)+1),
                                       "address":{**o["address"],"street":"Rua Sintética","number":"42"}})
                return send({"orders":orders})
            if path.endswith("/routes/start"):
                assert payload["checked"] is True and payload["load"]==[{"variant_id":VARIANT,"quantity":2}],payload
                assert [r["order_id"] for r in payload["orders"]]==IDS[::-1],payload
                for position,r in enumerate(payload["orders"],1):
                    o=next(o for o in orders if o["id"]==r["order_id"])
                    o.update(status="accepted",revision=3,route_position=position)
                orders.sort(key=lambda o:o["route_position"])
                return send({"orders":orders})
            if path.endswith("/actions"):
                oid=path.split("/")[-2]
                o=next(o for o in orders if o["id"]==oid)
                if payload["action"]=="arrive":
                    o.update(status="arrived",revision=4)
                else:
                    assert payload["action"]=="complete" and payload["received"] is True and payload["payment_method"]=="cash"
                    receipts.insert(0,{"id":oid,"completed_at":"2026-11-28T14:00:00Z","total_cents":1250,"payments":[{"method":"cash","amount_cents":1250}]})
                    orders.remove(o)
                return send({"status":"ok"})
        if path.startswith("/entregador/") and not path.endswith(".webmanifest"):
            return route.fulfill(status=200,content_type="text/html",body=(ROOT/"orion-delivery-driver.html").read_bytes())
        if path.startswith(("/panel-assets/","/brand/")):
            f=ROOT/path.rsplit("/",1)[-1]
            if f.is_file():
                return route.fulfill(status=200,content_type=mimetypes.guess_type(str(f))[0] or "application/octet-stream",body=f.read_bytes())
        return route.fulfill(status=404,body="")
    page.route(BASE+"/**",handle)
    page.goto(BASE+"/entregador/"+TENANT+"/")
    expect(page.get_by_role("heading",name="Olá, João!")).to_be_visible()
    def fits():
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), (width,page.url)
    fits()
    page.get_by_role("navigation").get_by_role("button",name="Pedidos").click()
    expect(page.get_by_role("heading",name="Pedidos",exact=True)).to_be_visible()
    page.get_by_role("button",name="Sugerir pedidos").click()
    expect(page.get_by_text("2 selecionado(s)",exact=False)).to_be_visible()
    page.get_by_role("button",name="Assumir selecionados").click()
    expect(page.get_by_text("Nenhum pedido disponível",exact=True)).to_be_visible()
    page.get_by_role("navigation").get_by_role("button",name="Em rota").click()
    expect(page.get_by_role("heading",name="Conferir carga")).to_be_visible()
    qty=page.get_by_label("Quantidade carregada de Água 20 L")
    qty.fill("1")
    page.get_by_text("Conferi a carga e a capacidade do veículo.").click()
    expect(page.get_by_role("button",name="Iniciar rota")).to_be_disabled()
    qty.fill("2")
    page.get_by_role("button",name="Subir parada 2").click()
    expect(page.get_by_role("button",name="Iniciar rota")).to_be_enabled()
    fits()
    page.get_by_role("button",name="Iniciar rota").click()
    expect(page.get_by_role("heading",name="Cliente 2",exact=True)).to_be_visible()
    fits()
    page.get_by_role("button",name="Cheguei ao endereço").click()
    page.get_by_label("Pagamento recebido").select_option("cash")
    page.get_by_text("Confirmo o recebimento",exact=False).click()
    page.get_by_role("button",name="Concluir entrega").click()
    expect(page.get_by_role("heading",name="Cliente 1",exact=True)).to_be_visible()
    page.get_by_role("button",name="Voltar à rota").click()
    page.get_by_role("navigation").get_by_role("button",name="Caixa").click()
    expect(page.get_by_text("Recebimentos registrados nos últimos 7 dias",exact=True)).to_be_visible()
    expect(page.locator("#cash-content").get_by_text("R$ 12,50",exact=True).first).to_be_visible()
    page.get_by_role("navigation").get_by_role("button",name="Mais").click()
    page.get_by_role("button",name="Escuro",exact=True).click()
    assert page.locator("html").get_attribute("data-theme")=="dark"
    page.get_by_role("button",name="Alto contraste").click()
    assert page.locator("html").get_attribute("data-contrast")=="high"
    fits()
    assert not errors,errors
    assert any(path.endswith("/routes/start") for path,_ in requests)
    page.screenshot(path=f"driver-portal-{browser.browser_type.name}-{width}.png",full_page=True)
    context.close()
    print(f"PASS {browser.browser_type.name} {width}x{height} dark={dark}")

if __name__=="__main__":
    with sync_playwright() as p:
        for name in ("chromium","webkit"):
            browser=getattr(p,name).launch()
            try:
                for w,h,dark in [(320,740,False),(390,844,True),(768,1024,False),(1366,900,True)]:
                    scenario(browser,w,h,dark)
            finally:
                browser.close()
