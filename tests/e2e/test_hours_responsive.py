"""Real rendering regression for Orion's weekly hours dialog (no live account)."""
from pathlib import Path
import pytest

# Core PostgreSQL CI has no browser; dedicated browser workflows install
# Playwright and run these real Chromium/WebKit tests independently.
pytest.importorskip("playwright")
from playwright.sync_api import sync_playwright
from scripts.plan_fixture import synthetic_plan_state

ROOT = Path(__file__).resolve().parents[2]
CSS = (ROOT / "app/static/orion-suite.css").read_text(encoding="utf-8")
JS = (ROOT / "app/static/orion-suite.js").read_text(encoding="utf-8")
FIELDS_CSS = (Path(__file__).resolve().parents[2] / "app/static/orion-fields.css").read_text(encoding="utf-8")
FIELDS_JS = (Path(__file__).resolve().parents[2] / "app/static/orion-fields.js").read_text(encoding="utf-8")
PRO = "51000000-0000-4000-8000-000000000001"
HOUR = "51000000-0000-4000-8000-000000000002"
BREAK = "51000000-0000-4000-8000-000000000003"

HTML = """<!doctype html><html lang="pt-BR" data-theme="dark">
<head><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"></head>
<body><div id="appView" class="app"><aside class="sidebar">
<div class="brand">Orion</div></aside><main class="content">
<section class="page" id="page-original">Original</section></main></div></body></html>"""


@pytest.mark.parametrize("browser_name,width,height,theme", [
    ("webkit", 320, 693, "dark"),
    ("webkit", 375, 812, "dark"),
    ("webkit", 430, 932, "light"),
    ("chromium", 375, 812, "light"),
    ("chromium", 768, 1024, "dark"),
    ("chromium", 1024, 768, "light"),
    ("chromium", 1366, 768, "dark"),
])
def test_weekly_hours_has_edit_controls_no_horizontal_overflow(browser_name, width, height, theme):
    with sync_playwright() as playwright:
        browser = getattr(playwright, browser_name).launch(headless=True)
        page = browser.new_page(viewport={"width": width, "height": height}, device_scale_factor=2)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            # Serve over HTTPS, as the deployed panel does. about:blank lacks
            # secure-context crypto.randomUUID and cannot faithfully test dialogs.
            page.route("https://orion.test/panel", lambda route: route.fulfill(
                status=200, content_type="text/html; charset=utf-8",
                body=HTML.replace('data-theme="dark"', f'data-theme="{theme}"')
            ))
            page.goto("https://orion.test/panel")
            page.add_style_tag(content=CSS)
            page.add_style_tag(content=FIELDS_CSS)
            page.add_script_tag(content=FIELDS_JS)
            page.evaluate("() => { window.OrionFields.bind(document); }")
            page.add_script_tag(content=JS)
            page.evaluate("""({pro,hour,breakId,effectivePlan}) => {
              const tenant='10000000-0000-4000-8000-000000000001';
              window.OrionSuite.mount({
                user:{role:'member',name:'Conta sintética',email:'test@example.invalid'},
                tenants:[{id:tenant,name:'Empresa sintética',slug:'sintetica',timezone:'America/Campo_Grande',role:'owner'}],
                tenantId:tenant,initialRoute:'funcionarios',
                request:async (path,method='GET',data) => {
                  if(path.endsWith('/management/permissions')&&method==='GET')return {effective_plan:effectivePlan};
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
            }""", {"pro": PRO, "hour": HOUR, "breakId": BREAK,
                    "effectivePlan": synthetic_plan_state('10000000-0000-4000-8000-000000000001')})
            assert page.evaluate("window.isSecureContext === true")
            assert page.evaluate("typeof crypto.randomUUID === 'function'")
            page.locator(f'[data-os-hours="{PRO}"]').click()
            page.locator('[data-os-hours-weekday="6"]').wait_for(timeout=5000)
            names = page.locator(".os-hours-day-name").all_text_contents()
            assert names == ["Segunda-feira", "Terça-feira", "Quarta-feira", "Quinta-feira",
                             "Sexta-feira", "Sábado", "Domingo"]
            assert page.locator(f'[data-os-edit-hour="{HOUR}"]').is_visible()
            assert page.locator(f'[data-os-edit-break="{BREAK}"]').is_visible()

            # The modal and every direct card fit within the device viewport.
            layout = page.evaluate("""() => {
              const d=document.querySelector('#osDialog');
              const rect=d.getBoundingClientRect();
              const days=[...d.querySelectorAll('.os-staff-hours-day')];
              return {
                visible:d.open,left:rect.left,right:rect.right,
                scroll:d.scrollWidth,client:d.clientWidth,width:innerWidth,
                cards:days.map(card=>{
                  const day=card.getBoundingClientRect();
                  const header=card.querySelector('.os-hours-day-head').getBoundingClientRect();
                  const shift=card.querySelector('.os-hours-shift')?.getBoundingClientRect();
                  return {left:day.left,right:day.right,width:day.width,display:getComputedStyle(card).display,
                    headTop:header.top,headBottom:header.bottom,headLeft:header.left,
                    shiftTop:shift?.top,shiftLeft:shift?.left,shiftWidth:shift?.width};
                })
              };
            }""")
            assert layout["visible"]
            assert layout["left"] >= -2
            assert layout["right"] <= width + 2
            assert layout["scroll"] <= layout["client"] + 3, layout
            assert len(layout["cards"]) == 7, layout
            assert all(day["left"] >= -2 and day["right"] <= width + 2 for day in layout["cards"]), layout
            assert all(day["display"] == "flex" for day in layout["cards"]), layout
            # Regression: the old settings .os-hours-day grid created the exact
            # 140px left gutter seen in the user screenshot. Header and shift
            # must occupy the same column with the shift BELOW its day title.
            for day in layout["cards"]:
                if day["shiftTop"] is not None:
                    assert day["shiftTop"] >= day["headBottom"] - 2, day
                    assert abs(day["shiftLeft"] - day["headLeft"]) <= 3, day
                    assert day["shiftWidth"] >= day["width"] - 52, day

            page.locator(f'[data-os-edit-hour="{HOUR}"]').click()
            form=page.locator('[data-os-form="hours-edit"]')
            form.wait_for()
            form.locator('input[name="start_time"]').fill("10:00")
            assert form.locator('button[type="submit"]').is_visible()
            editor=page.evaluate("""() => {
                const form=document.querySelector('[data-os-form="hours-edit"]');
                const parent=form.parentElement;
                const box=form.getBoundingClientRect();
                const inside=(name)=>form.querySelector('[name="'+name+'"]').getBoundingClientRect();
                return {width:box.width,parentWidth:parent.getBoundingClientRect().width,
                    left:box.left,right:box.right,
                    day:inside('weekday').toJSON(),start:inside('start_time').toJSON(),
                    end:inside('end_time').toJSON()};
            }""")
            assert editor["right"] <= width + 2 and editor["left"] >= -2, editor
            assert editor["width"] >= editor["parentWidth"] - 3, editor
            if width <= 767:
                # Native time inputs stack vertically on mobile instead of being
                # squeezed into three miniature desktop columns.
                assert editor["day"]["bottom"] <= editor["start"]["top"] + 1, editor
                assert editor["start"]["bottom"] <= editor["end"]["top"] + 1, editor
            else:
                assert abs(editor["start"]["top"] - editor["end"]["top"]) <= 2, editor

            page.locator('[data-os-hours-cancel]').click()
            page.locator(f'[data-os-edit-break="{BREAK}"]').click()
            pause_form=page.locator('[data-os-form="pause-edit"]')
            pause_form.wait_for()
            assert pause_form.evaluate("""form=>{
                const card=form.closest('[data-os-hours-break]').getBoundingClientRect();
                const box=form.getBoundingClientRect();
                return box.left>=card.left-2 && box.right<=card.right+2 &&
                       box.width>=card.width-28;
            }"""), "Break editor must fill its row rather than become a narrow column"
            assert not errors, errors
        finally:
            browser.close()
