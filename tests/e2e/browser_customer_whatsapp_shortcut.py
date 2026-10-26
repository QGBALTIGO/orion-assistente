"""Synthetic, browser-native phone shortcut layout checks at iPhone widths.

No authenticated account, production APIs or WhatsApp URL are contacted.
"""
from pathlib import Path
import pytest
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[2]
PAGE="""<!doctype html><html lang="pt-BR" data-theme="light"><head>
<meta name="viewport" content="width=device-width,initial-scale=1">
</head><body><div id="appView" class="app">
<aside class="sidebar"><div class="brand">Orion</div></aside>
<main class="content"><section id="page-original" class="page">Área original</section></main>
</div></body></html>"""
INITIALIZE=r"""() => {
  const customer={id:'30000000-0000-4000-8000-000000000003',
    name:'Cliente sintético',phone:'+5521997557364',active:true,
    version:1,created_at:'2026-10-17T13:12:00Z',
    total_appointments:0,total_spent_cents:0};
  const bridge={
    user:{role:'member',name:'Conta sintética',email:'synthetic@example.invalid'},
    tenants:[{id:'10000000-0000-4000-8000-000000000001',
      name:'Empresa de teste',slug:'teste',timezone:'America/Sao_Paulo',role:'owner'}],
    tenantId:'10000000-0000-4000-8000-000000000001',
    initialRoute:'clientes',
    request:async(path,method='GET')=>{
      if(path.includes('/management/customers'))return {items:[customer],total:1};
      if(path.endsWith('/management/team'))return {can_manage:true,members:[],invitations:[]};
      return [];
    },
    openLegacy(){},onRouteChange(){}
  };
  window.OrionSuite.mount(bridge);
}"""


@pytest.mark.parametrize("engine",["chromium","webkit"])
@pytest.mark.parametrize("width",[320,390])
def test_whatsapp_shortcut_and_chevron_fit_real_mobile_browser(engine,width):
    with sync_playwright() as playwright:
        browser=getattr(playwright,engine).launch()
        page=browser.new_page(viewport={"width":width,"height":740},
                             device_scale_factor=2,locale="pt-BR",
                             is_mobile=(engine=="webkit"),has_touch=True)
        try:
            page.set_content(PAGE)
            page.add_style_tag(content=(ROOT/"app/static/orion-suite.css").read_text())
            page.add_script_tag(content=(ROOT/"app/static/orion-suite.js").read_text())
            page.evaluate(INITIALIZE)
            card=page.locator(".os-mobile-customers .os-swipe-row").first
            card.wait_for(state="visible",timeout=10000)
            name=card.locator(".os-client-mobile-main strong")
            assert name.inner_text()=="Cliente sintético"
            assert "Desde 17/10/2026" in card.inner_text()
            assert "+5521" not in card.inner_text()
            assert "21997557364" not in card.inner_text()
            wa=card.locator("a.os-client-mobile-whatsapp")
            arrow=card.locator("button.os-client-mobile-main > svg.os-icon").last
            assert wa.count()==1 and arrow.count()==1
            assert wa.get_attribute("href")=="https://wa.me/5521997557364"
            wb,ab,cb=wa.bounding_box(),arrow.bounding_box(),card.bounding_box()
            assert all((wb,ab,cb)), "missing visible bounding boxes"
            assert 43 <= wb["width"] <=45 and 43 <= wb["height"] <=45
            assert cb["x"] <= wb["x"] and wb["x"]+wb["width"] <= cb["x"]+cb["width"]
            assert wb["x"]+wb["width"] <= ab["x"]+2, "WhatsApp must be left of the chevron"
            assert ab["x"]+ab["width"] <= cb["x"]+cb["width"]
            assert card.evaluate("(el)=>el.scrollWidth>el.clientWidth"), "swipe actions must remain available"
            page.evaluate("""() => {
              window.__clickedWhatsApp=false;
              document.querySelector('.os-client-mobile-whatsapp')
                .addEventListener('click',e=>{e.preventDefault();window.__clickedWhatsApp=true},{once:true});
            }""")
            wa.click()
            assert page.evaluate("window.__clickedWhatsApp")
            assert not page.locator("#osDialog").evaluate("(el)=>el.open")
            card.locator("button.os-client-mobile-main").click()
            assert page.locator("#osDialog").evaluate("(el)=>el.open")
        finally:
            browser.close()
