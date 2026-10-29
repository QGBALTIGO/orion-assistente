"""Real layout and form flows with synthetic performance data, on mobile and desktop."""
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from scripts.plan_fixture import synthetic_plan_state

sync_playwright = pytest.importorskip("playwright.sync_api", reason="Browser dependencies run in the dedicated UI job").sync_playwright

ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = ROOT / "test-artifacts" / "performance-history-goals"
BRAND = {name: ROOT / "app/static" / name for name in (
    "figtree-latin.woff2", "outfit-latin.woff2", "orion-brand-light.svg",
    "orion-brand-dark.svg", "orion-symbol.svg")}
CSS = (ROOT / "app/static/orion-suite.css").read_text(encoding="utf-8")
JS = (ROOT / "app/static/orion-suite.js").read_text(encoding="utf-8")
FIELDS_CSS = (Path(__file__).resolve().parents[2] / "app/static/orion-fields.css").read_text(encoding="utf-8")
FIELDS_JS = (Path(__file__).resolve().parents[2] / "app/static/orion-fields.js").read_text(encoding="utf-8")
FIXTURE = (ROOT / "tests/e2e/fixtures/performance_history_goals.js").read_text(encoding="utf-8")
HTML = """<!doctype html><html lang="pt-BR" data-theme="dark"><head>
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<style>*{box-sizing:border-box}html,body{margin:0;padding:0}.hidden{display:none!important}</style></head>
<body><div id="appView" class="app"><aside class="sidebar"><div class="brand">Orion</div></aside>
<main class="content"><section id="page-original" class="page">Original</section></main></div></body></html>"""


@pytest.mark.parametrize("browser_name,width,height,theme", [
    ("webkit", 320, 693, "dark"),
    ("webkit", 375, 812, "dark"),
    ("webkit", 430, 932, "light"),
    ("chromium", 360, 800, "light"),
    ("chromium", 768, 1024, "dark"),
    ("chromium", 1024, 768, "light"),
    ("chromium", 1366, 900, "dark"),
])
def test_performance_history_goals(browser_name, width, height, theme):
    with sync_playwright() as playwright:
        engine = getattr(playwright, browser_name)
        launch = {"channel": "chrome"} if browser_name == "chromium" and os.getenv("ORION_TEST_SYSTEM_CHROME") == "1" else {}
        browser = engine.launch(**launch)
        context = browser.new_context(viewport={"width": width, "height": height}, device_scale_factor=1,
                                      is_mobile=width < 768, has_touch=width < 768)
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            page.route("**/*", lambda route: route.abort())
            def brand_asset(route):
                name = urlsplit(route.request.url).path.rsplit("/", 1)[-1]
                source = BRAND.get(name)
                if source is None:
                    route.abort()
                else:
                    route.fulfill(status=200,
                                  content_type="font/woff2" if name.endswith(".woff2") else "image/svg+xml",
                                  body=source.read_bytes())
            page.route("https://orion.test/brand/*", brand_asset)
            page.route("https://orion.test/panel", lambda route: route.fulfill(
                status=200, content_type="text/html; charset=utf-8", body=HTML))
            page.goto("https://orion.test/panel", wait_until="domcontentloaded")
            page.evaluate("theme=>document.documentElement.dataset.theme=theme", theme)
            page.add_style_tag(content=CSS)
            page.add_style_tag(content=FIELDS_CSS)
            page.add_script_tag(content="if(!crypto.randomUUID)crypto.randomUUID=()=>String(Math.random()).slice(2)+'-fixture';")
            page.add_script_tag(content=FIELDS_JS)
            page.evaluate("() => { window.OrionFields.bind(document); }")
            page.add_script_tag(content=JS)
            page.add_script_tag(content=FIXTURE)
            page.evaluate("effectivePlan=>mountPerformanceFixture(effectivePlan)", synthetic_plan_state('10000000-0000-4000-8000-000000000001'))
            page.wait_for_selector("[data-os-performance-history]")

            def no_horizontal_cut(selector):
                boxes = page.locator(selector).evaluate_all("""nodes=>nodes.map(el=>{
                  const r=el.getBoundingClientRect();return {class:el.className,left:r.left,right:r.right,
                    width:r.width,scroll:el.scrollWidth,client:el.clientWidth};})""")
                for box in boxes:
                    assert -2 <= box["left"] and box["right"] <= width + 2, box
                    assert box["scroll"] <= box["client"] + 2, box

            page.evaluate("() => document.fonts.ready")
            page.locator(".os-professional-results").scroll_into_view_if_needed()
            assert page.locator("[data-os-performance-history]").count() == 3
            button = page.locator("[data-os-performance-history]").first
            button.scroll_into_view_if_needed()
            # Keep the geometry and hit test in one frame after fonts and scrolling settle.
            geometry = button.evaluate("""async el=>{
              await document.fonts.ready;
              const rect=node=>{
                const r=node.getBoundingClientRect();
                return {x:r.x,y:r.y,left:r.left,top:r.top,right:r.right,bottom:r.bottom,
                        width:r.width,height:r.height};
              };
              const position=()=>{
                const r=el.getBoundingClientRect(),v=visualViewport;
                return [r.x,r.y,r.width,r.height,scrollX,scrollY,
                        v?.offsetLeft||0,v?.offsetTop||0,v?.width||innerWidth,v?.height||innerHeight];
              };
              let previous=null,stableFrames=0,frames=0;
              while(stableFrames<2&&frames<120){
                await new Promise(resolve=>requestAnimationFrame(resolve));
                const current=position();
                stableFrames=previous&&current.every((value,index)=>Math.abs(value-previous[index])<0.05)
                  ? stableFrames+1 : 0;
                previous=current;frames++;
              }
              const box=rect(el),eye=rect(el.querySelector('svg'));
              const name=el.parentElement.querySelector('strong'),style=getComputedStyle(name);
              const hit={x:box.x+box.width/2,y:box.y-3};
              const target=document.elementFromPoint(hit.x,hit.y),pseudo=getComputedStyle(el,'::before');
              const ancestors=[];
              for(let node=el.parentElement;node;node=node.parentElement){
                const css=getComputedStyle(node);
                if(css.overflowX!=='visible'||css.overflowY!=='visible')
                  ancestors.push({tag:node.tagName,className:node.className,box:rect(node),
                                  overflowX:css.overflowX,overflowY:css.overflowY});
              }
              return {box,eye,hit,hitInside:el.contains(target),stableFrames,frames,
                typography:{font:parseFloat(style.fontSize),line:parseFloat(style.lineHeight),
                            family:style.fontFamily},
                target:target?{tag:target.tagName,id:target.id,className:target.getAttribute('class'),
                               box:rect(target)}:null,
                pseudo:{content:pseudo.content,top:pseudo.top,right:pseudo.right,bottom:pseudo.bottom,
                        left:pseudo.left,pointerEvents:pseudo.pointerEvents},ancestors,
                scroll:{x:scrollX,y:scrollY},viewport:{width:innerWidth,height:innerHeight,
                  offsetLeft:visualViewport?.offsetLeft,offsetTop:visualViewport?.offsetTop,
                  widthVisual:visualViewport?.width,heightVisual:visualViewport?.height},
                fonts:[...document.fonts].map(face=>({family:face.family,status:face.status})),
                userAgent:navigator.userAgent};
            }""")
            ARTIFACTS.mkdir(parents=True, exist_ok=True)
            stem = f"{browser_name}-{width}x{height}-{theme}"
            (ARTIFACTS / f"{stem}.json").write_text(
                json.dumps(geometry, ensure_ascii=False, indent=2), encoding="utf-8")
            if not geometry["hitInside"] or geometry["stableFrames"] < 2:
                page.screenshot(path=str(ARTIFACTS / f"{stem}.png"), full_page=True)
            assert geometry["stableFrames"] >= 2, geometry
            assert all(face["status"] == "loaded" for face in geometry["fonts"]), geometry
            box, typography, eye, hit = (geometry[key] for key in ("box", "typography", "eye", "hit"))
            # The affordance should read as a small action beside the name,
            # while its touch target extends beyond its compact visual box.
            assert 0 < eye["width"] <= typography["font"] * 1.25
            assert 0 < eye["height"] <= typography["line"]
            assert box["width"] <= typography["line"] * 1.6
            assert box["height"] <= typography["line"] * 1.6
            assert box["x"] >= 0 and box["x"] + box["width"] <= width
            assert geometry["hitInside"], geometry
            if width < 768:
                page.touchscreen.tap(hit["x"], hit["y"])
            else:
                page.mouse.click(hit["x"], hit["y"])
            page.wait_for_selector(".os-history-item")
            assert "Histórico · Peixoto" in page.locator("#osDialogTitle").inner_text()
            page.wait_for_function("document.activeElement===document.getElementById('osDialogTitle')")
            assert "Kayky Sousa" in page.locator(".os-history-item").first.inner_text()
            assert "09:00 – 10:00" in page.locator(".os-history-item").first.inner_text()
            no_horizontal_cut("#osDialog,.os-history-item,.os-history-item-heading,.os-history-amount")

            details = page.locator(".os-history-details").first
            summary = details.locator("summary")
            assert summary.inner_text().strip() == "Detalhes"
            assert not details.evaluate("el=>el.open")
            markers = summary.evaluate("""el=>({
              list:getComputedStyle(el).listStyleType,
              marker:getComputedStyle(el,'::marker').content,
              after:getComputedStyle(el,'::after').content
            })""")
            assert markers["list"] == "none", markers
            assert markers["marker"] in ('""', "''", "none"), markers
            assert markers["after"] in ('""', "''", "none", "normal"), markers

            def visible_symbols():
                return summary.locator("svg").evaluate_all("""nodes=>nodes
                  .filter(el=>el.getBoundingClientRect().width>0&&getComputedStyle(el).visibility!=='hidden')
                  .map(el=>[...el.querySelectorAll('path')].map(path=>path.getAttribute('d')).join(' '))""")

            collapsed_symbols = visible_symbols()
            assert len(collapsed_symbols) == 1, collapsed_symbols
            summary.focus()
            page.keyboard.press("Enter")
            page.wait_for_function("document.querySelector('.os-history-details').open")
            assert page.locator(".os-history-details[open]").count() == 1
            assert "AG-EXEMPLO1" in page.locator(".os-history-details[open]").inner_text()
            expanded_symbols = visible_symbols()
            assert len(expanded_symbols) == 1 and expanded_symbols != collapsed_symbols
            no_horizontal_cut(".os-history-details[open],.os-history-details[open] dl,.os-history-details[open] li")
            page.keyboard.press("Space")
            page.wait_for_function("!document.querySelector('.os-history-details').open")
            assert summary.evaluate("el=>document.activeElement===el")
            assert visible_symbols() == collapsed_symbols
            page.keyboard.press("Enter")
            page.wait_for_function("document.querySelector('.os-history-details').open")
            assert page.locator(".os-history-details[open]").count() == 1
            page.select_option("#osProfessionalHistoryStatus", "cancelled")
            page.wait_for_function("document.querySelectorAll('.os-history-item').length===5")
            assert all("Cancelado pelo cliente" in text for text in page.locator(".os-history-item").all_inner_texts())
            page.locator(".os-history-details summary").first.click()
            assert "imprevisto" in page.locator(".os-history-details[open]").inner_text()
            page.select_option("#osProfessionalHistoryStatus", "")
            page.wait_for_function("document.querySelectorAll('.os-history-item').length===25")
            page.locator("[data-os-action=professional-history-next]").click()
            page.wait_for_function("document.querySelectorAll('.os-history-item').length===1")
            assert "26–26 de 26" in page.locator(".os-history-pagination").inner_text()
            no_horizontal_cut(".os-history-pagination")
            page.locator("#osDialog [data-os-action=close-dialog]").click()
            assert page.locator("#osDialog").is_visible() is False
            assert button.evaluate("el=>document.activeElement===el")
            button.press("Enter")
            page.wait_for_selector(".os-history-item")
            page.wait_for_function("document.activeElement===document.getElementById('osDialogTitle')")
            page.keyboard.press("Escape")
            page.wait_for_function("!document.querySelector('#osDialog').open")
            assert button.evaluate("el=>document.activeElement===el")

            page.locator("[data-os-action=new-goal]").click()
            page.select_option("#osField-scope", "company")
            assert page.locator("[data-os-goal-professional]").is_visible() is False
            assert page.locator("#osField-start_date").count() == 0
            page.select_option("#osField-metric", "appointments")
            page.fill("#osField-target", "100")
            assert "No dia 1º" in page.locator(".os-goal-monthly-info").inner_text()
            no_horizontal_cut("#osDialog,[data-os-form=goal],.os-goal-monthly-info")
            page.locator("[data-os-form=goal] [type=submit]").click()
            page.wait_for_function("!document.querySelector('#osDialog').open")
            writes = page.evaluate("performanceFixtureCalls.filter(c=>c.method!=='GET')")
            assert len(writes) == 1 and writes[0]["data"]["scope"] == "company"
            assert writes[0]["data"]["professional_id"] is None
            assert writes[0]["data"]["recurrence"] == "monthly"
            assert writes[0]["data"]["target_value"] == 100
            assert "start_date" not in writes[0]["data"]
            page.locator(".os-performance-goals").scroll_into_view_if_needed()
            no_horizontal_cut(".os-performance-goals,.os-goal-card")
            assert not errors, errors
        finally:
            context.close()
            browser.close()
