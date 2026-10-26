"""Mobile/desktop screenshot geometry for redesigned Orion combo cards.

No authenticated production session or external requests: intercept the panel
with an in-memory fixture and exercise real WebKit/Chromium CSS layout.
"""
from pathlib import Path
import pytest
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[2]
HTML="""<!doctype html><html lang="pt-BR" data-theme="light"><head>
<meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body><div id="appView" class="app"><aside class="sidebar"><div class="brand">Orion</div></aside>
<main class="content"><section id="page-original" class="page">Original</section></main></div></body></html>"""
INITIALIZE=r"""() => {
  const ids={
    cut:'c0000000-0000-4000-8000-000000000001',
    hair:'c0000000-0000-4000-8000-000000000003'
  };
  const services=[
    {id:ids.cut,name:'Corte Masculino',catalog_kind:'base',duration_min:40,
      price_cents:4000,active:true,color:'#8b5cf6'},
    {id:ids.hair,name:'Alinhamento Capilar',catalog_kind:'base',duration_min:150,
      price_cents:12000,price_mode:'from',active:true,color:'#8b5cf6'}
  ];
  const combo=[
    {id:'d0000000-0000-4000-8000-000000000001',
      name:'Corte + Barba + Sobrancelha na Navalha',color:'#8b5cf6',
      service_ids:[ids.cut,ids.hair],
      items:[{service_id:ids.cut,name:'Corte Masculino'},
             {service_id:ids.hair,name:'Alinhamento Capilar'}],
      price_cents:6000,effective_price_cents:6000,price_mode:'fixed',
      duration_min:90,active:true,available:true,featured:true,version:1,
      image_url:'https://images.example.invalid/combo.png'},
    {id:'d0000000-0000-4000-8000-000000000002',
      name:'Corte + Botox Capilar + Hidratação Profunda para cabelos compridos',
      color:'#8b5cf6',service_ids:[ids.cut,ids.hair],
      items:[{service_id:ids.cut,name:'Corte Masculino'},
             {service_id:ids.hair,name:'Alinhamento Capilar (Botox)'}],
      price_cents:14000,effective_price_cents:14000,price_mode:'from',
      duration_min:150,active:true,available:true,featured:false,version:1,
      image_url:null}
  ];
  const bridge={
    user:{role:'member',name:'Gestor sintético',email:'synthetic@example.invalid'},
    tenants:[{id:'10000000-0000-4000-8000-000000000001',
      name:'Empresa sintética',slug:'synthetic',timezone:'America/Sao_Paulo',role:'owner'}],
    tenantId:'10000000-0000-4000-8000-000000000001',
    initialRoute:'servicos',
    request:async(path,method='GET')=>{
      if(path.endsWith('/services')&&method==='GET')return services;
      if(path.endsWith('/management/service-packages')&&method==='GET')return {items:combo};
      if(path.endsWith('/professionals')||path.endsWith('/professional_services'))return [];
      if(path.includes('/addons'))return {version:0,items:[]};
      return [];
    },
    openLegacy(){},onRouteChange(){}
  };
  window.OrionSuite.mount(bridge);
}"""


@pytest.mark.parametrize("browser_name",["chromium","webkit"])
@pytest.mark.parametrize("width",[320,390,768])
def test_package_cards_share_service_geometry_without_nested_surfaces(browser_name,width):
    with sync_playwright() as pw:
        browser=getattr(pw,browser_name).launch()
        page=browser.new_page(
            viewport={"width":width,"height":900},device_scale_factor=2,
            locale="pt-BR",is_mobile=(browser_name=="webkit" and width<640),
            has_touch=True
        )
        try:
            page.route("**/*",lambda route:route.abort())
            page.route("https://orion.test/panel",lambda route:route.fulfill(
                status=200,content_type="text/html",body=HTML))
            page.goto("https://orion.test/panel",wait_until="domcontentloaded")
            page.add_style_tag(content=(ROOT/"app/static/orion-suite.css").read_text())
            page.add_script_tag(content=(ROOT/"app/static/orion-suite.js").read_text())
            page.evaluate(INITIALIZE)
            first=page.locator('[data-os-package-card="d0000000-0000-4000-8000-000000000001"]')
            variable=page.locator('[data-os-package-card="d0000000-0000-4000-8000-000000000002"]')
            first.wait_for(state="visible",timeout=12000)
            service=page.locator(".os-service-card").first
            assert service.count()==1
            assert page.locator("#osPackagesSection").get_attribute("class")=="os-packages-section"
            assert page.locator("#osPackagesSection.os-card").count()==0

            # Same white card surface and photo geometry as regular services.
            bg=service.evaluate("(el)=>getComputedStyle(el).backgroundColor")
            assert first.evaluate("(el)=>getComputedStyle(el).backgroundColor")==bg
            assert first.evaluate("(el)=>getComputedStyle(el).borderRadius")=="18px"
            regular_photo=service.locator(".os-service-photo").bounding_box()
            combo_photo=first.locator(".os-package-photo").bounding_box()
            assert regular_photo and combo_photo
            assert abs(regular_photo["width"]-combo_photo["width"])<=1
            assert abs(regular_photo["height"]-combo_photo["height"])<=1
            assert first.locator(".os-package-photo img").count()==1
            assert variable.locator(".os-package-photo svg.os-icon").count()==1
            assert first.locator(".os-package-info h3").inner_text()=="Corte + Barba + Sobrancelha na Navalha"
            assert "A partir de R$" in variable.locator(".os-package-meta strong").inner_text()
            assert variable.locator(".os-package-duration").inner_text()=="2h 30min"
            assert first.locator(".os-package-featured").inner_text()=="Destaque"
            assert first.locator(".os-package-actions [data-os-package]").inner_text().strip()=="Editar"

            for item in [first,variable]:
                item.scroll_into_view_if_needed()
                box=item.bounding_box()
                assert box and box["width"]>140
                assert item.evaluate("(el)=>el.scrollWidth<=el.clientWidth+2")
                assert item.locator(".os-package-main").count()==1
                assert item.locator(".os-package-info").count()==1
                assert item.locator(".os-card-header").count()==0
                assert item.locator(".os-item-bottom").count()==0
                assert item.locator(".os-package-components").evaluate(
                    "(el)=>getComputedStyle(el).webkitLineClamp")=="2"
                assert item.locator(".os-package-info h3").evaluate(
                    "(el)=>getComputedStyle(el).webkitLineClamp")=="2"
                assert box["height"]<285,"card is no longer an oversized box"
            assert page.evaluate("document.documentElement.scrollWidth<=window.innerWidth+2"),"no horizontal page overflow"

            # A real click still opens the existing editor with its original values.
            first.locator("[data-os-package]").click()
            dialog=page.locator("#osDialog")
            assert dialog.evaluate("(el)=>el.open")
            assert dialog.locator('[name="price"]').input_value()=="60,00"
        finally:
            browser.close()
