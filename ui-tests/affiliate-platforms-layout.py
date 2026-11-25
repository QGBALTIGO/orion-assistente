"""Offline platforms visual checks; credentials and links are synthetic, no writes."""
from pathlib import Path
import importlib.util
import json
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("automation_fixture",ROOT/"ui-tests/affiliate-automations-layout.py")
shell=importlib.util.module_from_spec(spec)
spec.loader.exec_module(shell)
OUT=ROOT/"artifacts/affiliate-platforms"
OUT.mkdir(parents=True,exist_ok=True)
with sync_playwright() as p:
    for engine,widths,themes in [("chromium",[320,430,768,1280],["light","dark"]),("webkit",[320,430],["dark"])]:
        browser=getattr(p,engine).launch(headless=True)
        for width in widths:
            for theme in themes:
                for layout in ["classic","app"]:
                    for connected in [True,False]:
                        context=browser.new_context(viewport={"width":width,"height":900},is_mobile=width<768,has_touch=width<768,device_scale_factor=1,service_workers="block")
                        data=shell.base.workspace(empty=True)
                        data["connection"]=[{"id":"shopee","status":"connected","account":"••••0783","verified":1795635390}] if connected else []
                        data["phrase_ai"]=False
                        calls,errors,blocked,writes=[],[],[],[]
                        shell.install_routes(context,data,{},calls,errors,blocked,writes)
                        context.add_init_script('localStorage.setItem("orion-theme",'+json.dumps(theme)+');localStorage.setItem('+json.dumps(f"orion-navigation-v1:{shell.TENANT}:{shell.EMAIL}")+','+json.dumps(layout)+');')
                        page=context.new_page()
                        page.on("pageerror",lambda error:errors.append(str(error)))
                        try:
                            page.goto(shell.ORIGIN+"/panel?aba=afiliados-plataformas",wait_until="domcontentloaded")
                            root=page.locator(".af-platforms-workspace")
                            root.wait_for()
                            page.evaluate("document.fonts.ready")
                            assert root.locator("article").count()==4
                            assert root.locator("a[target=_blank]").count()==8
                            for key in ["shopee","groq"]:
                                root.locator(f"[data-platform-card={key}] summary").click()
                            assert root.locator("form:visible").count()==2
                            metrics=root.evaluate("""root=>{const r=root.getBoundingClientRect();return {overflow:document.documentElement.scrollWidth>innerWidth+1,clipped:[...root.querySelectorAll('article,input,button,a,summary')].filter(n=>n.getClientRects().length).filter(n=>{const b=n.getBoundingClientRect();return b.left<r.left-2||b.right>r.right+2||n.scrollWidth>n.clientWidth+2}).map(n=>n.className||n.tagName)}}""")
                            assert not metrics["overflow"] and not metrics["clipped"],metrics
                            if width<768: assert root.locator("input").first.evaluate("n=>parseFloat(getComputedStyle(n).fontSize)")>=16
                            page.evaluate("scrollTo({top:document.documentElement.scrollHeight,behavior:'instant'})")
                            page.evaluate("()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))")
                            assert root.evaluate("""r=>{const dock=document.querySelector('#osAppDock');return !dock?.getClientRects().length||r.getBoundingClientRect().bottom<=dock.getBoundingClientRect().top+2||dock.getBoundingClientRect().right<=r.getBoundingClientRect().left}""")
                            page.screenshot(path=str(OUT/f"{engine}-{width}-{theme}-{layout}-{connected}.png"),full_page=True,animations="disabled")
                            assert not errors and not blocked and not writes,(errors,blocked,writes)
                            print(f"PASS {engine} {width} {theme} {layout} connected={connected}",flush=True)
                        finally:
                            context.close()
        browser.close()
