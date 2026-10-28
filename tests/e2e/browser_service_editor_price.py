"""Mobile Safari/Chromium service editor: duration shortcuts and integer-first BRL input.

All data are synthetic, rendered on an intercepted HTTPS origin. No production
credentials, external APIs or real customer/business data are used.
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
  const id='a0000000-0000-4000-8000-000000000010';
  const service={id,name:'Corte Masculino',duration_min:40,price_cents:4000,
      price_mode:'fixed',sort_order:0,active:true,color:'#8b5cf6'};
  window.__syntheticSaves=[];
  const bridge={
    user:{role:'member',name:'Gestão sintética',email:'synthetic@example.invalid'},
    tenants:[{id:'10000000-0000-4000-8000-000000000001',
      name:'Empresa de teste',slug:'teste',timezone:'America/Sao_Paulo',role:'owner'}],
    tenantId:'10000000-0000-4000-8000-000000000001',
    initialRoute:'servicos',
    request:async(path,method='GET',payload)=>{
      if(path.endsWith('/services')&&method==='GET')return [service];
      if(path.endsWith('/professionals')||path.endsWith('/professional_services'))return [];
      if(path.endsWith('/management/service-packages'))return {items:[]};
      if(path.endsWith('/addons'))return {version:0,items:[]};
      if(path.endsWith('/services/'+id)&&method==='PUT'){
        window.__syntheticSaves.push(payload);
        Object.assign(service,payload);
        return {...service};
      }
      return [];
    },
    openLegacy(){},onRouteChange(){}
  };
  window.OrionSuite.mount(bridge);
}"""


@pytest.mark.parametrize("engine",["chromium","webkit"])
@pytest.mark.parametrize("width",[320,390,768])
def test_duration_presets_are_under_minutes_and_currency_formats_native_keyboard(engine,width):
    with sync_playwright() as playwright:
        browser=getattr(playwright,engine).launch()
        page=browser.new_page(viewport={"width":width,"height":850},
                             device_scale_factor=2,locale="pt-BR",
                             is_mobile=(engine=="webkit" and width<=390),has_touch=True)
        try:
            page.route("https://orion.test/**",lambda route:route.abort())
            page.route("https://orion.test/panel",lambda route:route.fulfill(
                status=200,content_type="text/html",body=PAGE))
            page.goto("https://orion.test/panel",wait_until="domcontentloaded")
            page.add_style_tag(content=(ROOT/"app/static/orion-suite.css").read_text())
            page.add_style_tag(content=(ROOT/"app/static/orion-fields.css").read_text())
            page.add_script_tag(content=(ROOT/"app/static/orion-fields.js").read_text())
            page.evaluate("() => { window.OrionFields.bind(document); }")
            page.add_script_tag(content=(ROOT/"app/static/orion-suite.js").read_text())
            page.evaluate(INITIALIZE)

            edit=page.locator('[data-os-service="a0000000-0000-4000-8000-000000000010"]')
            edit.wait_for(state="visible",timeout=10000)
            edit.click()
            form=page.locator('[data-os-form="service"]')
            form.wait_for(state="visible",timeout=10000)
            minutes=form.locator('[name="duration_min"]')
            shortcuts=form.locator('.os-service-duration-shortcuts')
            pricing=form.locator('.os-service-price-modes')
            value=form.locator('[name="price"]')
            assert value.input_value()=="40,00"
            assert shortcuts.count()==1 and pricing.count()==1
            assert shortcuts.locator('[data-os-duration]').count()==5
            assert minutes.evaluate("(el)=>el.closest('.os-field').nextElementSibling.classList.contains('os-service-duration-shortcuts')")
            assert shortcuts.evaluate("(el)=>el.nextElementSibling.classList.contains('os-service-price-modes')")
            assert shortcuts.evaluate("(el)=>el.scrollWidth<=el.clientWidth+2"),"chips wrap at narrow widths"

            minute_box=minutes.bounding_box()
            chip_box=shortcuts.bounding_box()
            price_type_box=pricing.bounding_box()
            assert minute_box and chip_box and price_type_box
            assert minute_box["y"]+minute_box["height"]<=chip_box["y"]+2
            assert chip_box["y"]+chip_box["height"]<=price_type_box["y"]+2

            shortcuts.locator('[data-os-duration="45"]').click()
            assert minutes.input_value()=="45"
            assert shortcuts.locator('[aria-pressed="true"]').count()==1
            assert shortcuts.locator('[aria-pressed="true"]').get_attribute('data-os-duration')=="45"
            minutes.fill("40")
            assert shortcuts.locator('[aria-pressed="true"]').count()==0

            value.fill("")
            value.press_sequentially("4")
            assert value.input_value()=="4,00"
            assert value.evaluate("(el)=>el.selectionStart")==1
            value.press_sequentially("4")
            assert value.input_value()=="44,00"
            assert value.evaluate("(el)=>el.selectionStart")==2
            value.press_sequentially("00")
            assert value.input_value()=="4.400,00"
            value.fill("")
            value.press_sequentially("2000")
            assert value.input_value()=="2.000,00"

            # Explicit replacement of centavos does not modify the integer part.
            value.fill("40,00")
            value.evaluate("(el)=>el.setSelectionRange(3,5)")
            value.press_sequentially("25")
            assert value.input_value()=="40,25"
            value.fill("R$ 2.000,00")
            assert value.input_value()=="2.000,00"
            value.fill("")
            value.press_sequentially("2000")
            form.locator('button[type="submit"]').click()
            page.wait_for_function("window.__syntheticSaves.length===1",timeout=10000)
            stored=page.evaluate("window.__syntheticSaves[0]")
            assert stored["price_cents"]==200000
            assert stored["duration_min"]==40
            assert stored["price_mode"]=="fixed"
        finally:
            browser.close()
