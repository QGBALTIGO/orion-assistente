"""Real rendering regression for Orion's weekly hours dialog (no live account)."""
from pathlib import Path
import pytest
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
CSS = (ROOT / "app/static/orion-suite.css").read_text(encoding="utf-8")
JS = (ROOT / "app/static/orion-suite.js").read_text(encoding="utf-8")
PRO = "51000000-0000-4000-8000-000000000001"
HOUR = "51000000-0000-4000-8000-000000000002"
BREAK = "51000000-0000-4000-8000-000000000003"

HTML = """<!doctype html><html lang="pt-BR" data-theme="dark">
<head><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"></head>
<body><div id="appView" class="app"><aside class="sidebar">
<div class="brand">Orion</div></aside><main class="content">
<section class="page" id="page-original">Original</section></main></div></body></html>"""


@pytest.mark.parametrize("browser_name,width,height", [
    ("webkit", 375, 812),
    ("webkit", 430, 932),
    ("chromium", 768, 1024),
    ("chromium", 1366, 768),
])
def test_weekly_hours_has_edit_controls_no_horizontal_overflow(browser_name, width, height):
    with sync_playwright() as playwright:
        browser = getattr(playwright, browser_name).launch(headless=True)
        page = browser.new_page(viewport={"width": width, "height": height}, device_scale_factor=2)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            page.set_content(HTML)
            page.add_style_tag(content=CSS)
            page.add_script_tag(content=JS)
            page.evaluate("""({pro,hour,breakId}) => {
              const tenant='10000000-0000-4000-8000-000000000001';
              window.OrionSuite.mount({
                user:{role:'member',name:'Conta sintética',email:'test@example.invalid'},
                tenants:[{id:tenant,name:'Empresa sintética',slug:'sintetica',timezone:'America/Campo_Grande',role:'owner'}],
                tenantId:tenant,initialRoute:'funcionarios',
                request:async (path,method='GET',data) => {
                  if(path.endsWith('/professionals'))return [{id:pro,name:'Guilherme',active:true,commission_percent:0}];
                  if(path.endsWith('/working_hours'))return [
                    {id:hour,professional_id:pro,weekday:0,start_time:'09:00:00',end_time:'18:00:00'},
                    {id:'51000000-0000-4000-8000-000000000004',professional_id:pro,weekday:6,start_time:'09:00:00',end_time:'13:00:00'}
                  ];
                  if(path.endsWith('/working_breaks'))return [{id:breakId,working_hour_id:hour,start_time:'12:00:00',end_time:'13:00:00'}];
                  if(path.endsWith('/management/team'))return {can_manage:true,members:[],invitations:[]};
                  return [];
                },openLegacy(){},onRouteChange(){}
              });
            }""", {"pro": PRO, "hour": HOUR, "breakId": BREAK})
            page.locator(f'[data-os-hours="{PRO}"]').click()
            page.wait_for_timeout(200)
            diagnostic = page.evaluate("""() => ({
              dialog:document.querySelector('#osDialog')?.outerHTML.slice(0,1800),
              open:document.querySelector('#osDialog')?.open,
              hourCount:document.querySelectorAll('[data-os-hours-weekday]').length,
              currentRoute:document.querySelector('#page-suite')?.dataset.osCurrentRoute
            })""")
            assert diagnostic["hourCount"] == 7, f"Rendered schedule unavailable: {diagnostic!r}; browser errors={errors!r}"
            page.locator('[data-os-hours-weekday="6"]').wait_for(timeout=2000)
            names = page.locator(".os-hours-day-name").all_text_contents()
            assert names == ["Segunda-feira", "Terça-feira", "Quarta-feira", "Quinta-feira",
                             "Sexta-feira", "Sábado", "Domingo"]
            assert page.locator(f'[data-os-edit-hour="{HOUR}"]').is_visible()
            assert page.locator(f'[data-os-edit-break="{BREAK}"]').is_visible()

            # The modal and every direct card fit within the device viewport.
            layout = page.evaluate("""() => {
              const d=document.querySelector('#osDialog');
              const rect=d.getBoundingClientRect();
              const dayRects=[...d.querySelectorAll('.os-hours-day')].map(x=>x.getBoundingClientRect());
              return {visible:d.open,left:rect.left,right:rect.right,scroll:d.scrollWidth,client:d.clientWidth,
                width:innerWidth,
                cards:dayRects.map(x=>({left:x.left,right:x.right,width:x.width}))};
            }""")
            assert layout["visible"]
            assert layout["left"] >= -2
            assert layout["right"] <= width + 2
            assert layout["scroll"] <= layout["client"] + 3, layout
            assert all(day["left"] >= -2 and day["right"] <= width + 2 for day in layout["cards"]), layout

            page.locator(f'[data-os-edit-hour="{HOUR}"]').click()
            page.locator('[data-os-form="hours-edit"]').wait_for()
            page.locator('[data-os-form="hours-edit"] input[name="start_time"]').fill("10:00")
            assert page.locator('[data-os-form="hours-edit"] button[type="submit"]').is_visible()
            assert page.locator('[data-os-form="hours-edit"]').evaluate(
                "(form)=>form.getBoundingClientRect().width <= form.parentElement.getBoundingClientRect().width+2"
            )
            assert not errors, errors
        finally:
            browser.close()
