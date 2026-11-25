"""Responsive and interaction checks for connections, using only local fixtures."""
from __future__ import annotations
import argparse
import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/affiliate-connections"
spec = importlib.util.spec_from_file_location("automation_layout", ROOT / "ui-tests/affiliate-automations-layout.py")
shell = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shell)
base = shell.base
ORIGIN, TENANT, EMAIL, API = base.ORIGIN, base.TENANT, base.EMAIL, base.API

def workspace(state_name):
    data = base.workspace(empty=True)
    data["connection"] = [] if state_name == "empty" else [{"id":"telegram","status":"connected","username":"bot_sintetico"}]
    data["destination"] = [] if state_name == "empty" else [
        {"id":"a"*32,"name":"Promoções para casa, decoração e acessórios em um canal de WhatsApp","mode":"whatsapp_channel","verified":True},
        {"id":"b"*32,"name":"GeekHunter — ofertas no Telegram","mode":"telegram","verified":True},
        {"id":"c"*32,"name":"Grupo de ofertas especiais de tecnologia","mode":"whatsapp_group","verified":False,"issue":"A administração precisa ser verificada."},
    ]
    return data

def settled(page):
    page.evaluate("() => new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))")

def metrics(page):
    result=page.evaluate("""()=>{
      const root=document.querySelector('.af-connections-workspace');
      const bounds=root.getBoundingClientRect();
      const shown=n=>n.getClientRects().length&&getComputedStyle(n).visibility!=='hidden'&&(!n.closest('details:not([open])')||!!n.closest('summary'));
      const rect=n=>{const r=n.getBoundingClientRect();return {left:r.left,right:r.right,height:r.height,width:r.width};};
      const nodes=[...root.querySelectorAll('.af-connection-panel,.af-connection-account,.af-connection-destination,button,input:not([type=checkbox]),select,summary')].filter(shown);
      const clipped=[],walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);
      while(walker.nextNode()){
        const n=walker.currentNode,p=n.parentElement;
        if(!n.nodeValue.trim()||!shown(p)||p.closest('option,.os-sr-only,[aria-hidden="true"]'))continue;
        const range=document.createRange();range.selectNodeContents(n);
        for(const r of range.getClientRects())if(r.width&&r.height&&(r.left<bounds.left-2||r.right>bounds.right+2))clipped.push(n.nodeValue.trim().slice(0,100));
      }
      return {viewport:innerWidth,documentWidth:document.documentElement.scrollWidth,root:rect(root),clipped,theme:document.documentElement.dataset.theme,
        controls:nodes.map(n=>({kind:n.tagName,label:n.getAttribute('aria-label')||n.textContent.trim().slice(0,80),rect:rect(n),scroll:n.scrollWidth,client:n.clientWidth}))};
    }""")
    assert result["documentWidth"]<=result["viewport"]+1, result
    assert result["root"]["left"]>=-1 and result["root"]["right"]<=result["viewport"]+1,result
    assert not result["clipped"],result
    for node in result["controls"]:
        assert node["rect"]["left"]>=-1 and node["rect"]["right"]<=result["viewport"]+1,node
        if node["kind"] in ("BUTTON","INPUT","SELECT","SUMMARY"):
            assert node["rect"]["height"]>=36,node
    return result

def run(context,width,theme,layout,engine,output,state_name):
    data=workspace(state_name)
    control={"hold_reads":False,"held_reads":[],"fail_next_read":False,"hold_writes":False,"held":[],"fail_next_write":False,"fail_activate":False}
    calls,errors,blocked,writes=[],[],[],[]
    shell.install_routes(context,data,control,calls,errors,blocked,writes)
    context.add_init_script('localStorage.setItem("orion-theme", '+json.dumps(theme)+');'+"localStorage.setItem("+json.dumps(f"orion-navigation-v1:{TENANT}:{EMAIL}")+","+json.dumps(layout)+");")
    page=context.new_page()
    page.on("pageerror",lambda error:errors.append(str(error)))
    name=f"{state_name}-{layout}-{width}-{theme}"
    result={"engine":engine,"width":width,"theme":theme,"layout":layout,"state":state_name}
    try:
        page.goto(ORIGIN+"/panel?aba=afiliados-conexoes",wait_until="domcontentloaded")
        page.locator('.af-connections-workspace').wait_for()
        page.evaluate('document.fonts.ready')
        page.evaluate("""async()=>{await Promise.all((document.querySelector('#appView > .sidebar')?.getAnimations()||[]).map(a=>a.finished.catch(()=>{})));await new Promise(resolve=>requestAnimationFrame(resolve));}""")
        assert page.locator('.af-connections-workspace h1').inner_text()=='Conexões e destinos'
        assert page.locator('#appView').evaluate("node=>node.classList.contains('os-layout-app')")==(layout=="app")
        result['initial']=metrics(page)
        assert result['initial']['theme']==theme
        page.screenshot(path=str(output/(name+'-initial.png')),full_page=True,animations='disabled')
        page.locator('[data-act=new-destination]').click()
        editor=page.locator('#af-destination-editor')
        assert editor.evaluate('node=>node.open')
        page.locator('#af-mode').select_option('whatsapp_channel')
        field=page.locator('[data-form=destination] input[name=address]')
        field.fill('https://whatsapp.com/channel/synthetic')
        page.locator('[data-form=destination] input[name=name]').fill('Canal de ofertas sintético')
        page.locator('[data-connection-filter=telegram]').evaluate('node=>node.click()')
        assert page.locator('[data-connection-dest]').count()==(1 if state_name=='ready' else 0)
        page.locator('[data-connection-filter=all]').evaluate('node=>node.click()')
        field.evaluate('node=>{node.focus({preventScroll:true});node.setSelectionRange(5,10);window.__connectionField=node}')
        before=field.evaluate('node=>({y:scrollY,top:node.getBoundingClientRect().top})')
        with page.expect_response(lambda r:r.url==ORIGIN+API+"state"):
            page.locator('[data-act=refresh-connections]').evaluate('node=>node.click()')
        page.locator('[data-act=refresh-connections][aria-busy=false]').wait_for()
        settled(page)
        after=field.evaluate('node=>({y:scrollY,top:node.getBoundingClientRect().top,focused:document.activeElement===node,same:window.__connectionField===node,start:node.selectionStart,end:node.selectionEnd})')
        assert after['focused'] and after['same'] and (after['start'],after['end'])==(5,10),after
        assert abs(after['y']-before['y'])<=3,(before,after)
        assert field.input_value()=='https://whatsapp.com/channel/synthetic'
        result['editor']=metrics(page)
        page.screenshot(path=str(output/(name+'-editor.png')),full_page=True,animations='disabled')
        page.locator('#af-mode').select_option('whatsapp_group')
        assert page.locator('[data-wa-group-picker]').is_visible()
        assert not page.locator('[data-destination-address]').is_visible()
        page.locator('#af-mode').select_option('telegram')
        if not page.locator('#af-telegram-credentials').evaluate('node=>node.open'):
            page.locator('#af-telegram-credentials > summary').click()
        settled(page)
        result['credentials']=metrics(page)
        page.screenshot(path=str(output/(name+'-credentials.png')),full_page=True,animations='disabled')
        page.evaluate("scrollTo({top:document.documentElement.scrollHeight,behavior:'instant'})")
        settled(page)
        bottom=page.evaluate("""()=>{const root=document.querySelector('.af-connections-workspace'),dock=document.querySelector('#osAppDock');const end=root.getBoundingClientRect(),d=dock?.getBoundingClientRect();const overlaps=dock?.getClientRects().length&&d.height>0&&d.right>end.left&&d.left<end.right;return {end:end.bottom,dock:overlaps?d.top:innerHeight};}""")
        assert bottom['end']<=bottom['dock']+2,bottom
        assert not errors,errors
        assert not blocked,blocked
        assert not writes,writes
        (output/(name+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2))
        return result
    except Exception:
        page.screenshot(path=str(output/(name+'-failure.png')),full_page=True,animations='disabled')
        (output/(name+'-failure.json')).write_text(json.dumps({'result':result,'errors':errors,'blocked':blocked,'writes':writes,'calls':calls},ensure_ascii=False,indent=2))
        raise
    finally:
        page.close()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine', choices=['chromium', 'webkit'], default='chromium')
    parser.add_argument('--widths', type=int, nargs='+', default=[320, 430, 768, 1280])
    parser.add_argument('--themes', choices=['light', 'dark'], nargs='+', default=['light', 'dark'])
    parser.add_argument('--layouts', choices=['classic', 'app'], nargs='+', default=['classic', 'app'])
    parser.add_argument('--states', choices=['ready', 'empty'], nargs='+', default=['ready'])
    args = parser.parse_args()
    output = OUT / args.engine
    output.mkdir(parents=True, exist_ok=True)
    measurements = []
    with sync_playwright() as playwright:
        engine = getattr(playwright, args.engine)
        bundled = Path(engine.executable_path)
        executable = str(bundled) if bundled.is_file() else None
        if not executable and args.engine == 'chromium':
            executable = os.getenv('ORION_TEST_BROWSER') or shutil.which('chromium') or shutil.which('google-chrome')
        browser = engine.launch(headless=True, executable_path=executable)
        try:
            for width in args.widths:
                for theme in args.themes:
                    for layout in args.layouts:
                        for state_name in args.states:
                            context = browser.new_context(viewport={'width': width, 'height': 900}, is_mobile=width < 768,
                                has_touch=width < 768, device_scale_factor=1, service_workers='block')
                            try:
                                measurements.append(run(context, width, theme, layout, args.engine, output, state_name))
                                print(f'PASS {args.engine} {width}px {theme} {layout} {state_name}', flush=True)
                            finally:
                                context.close()
        finally:
            browser.close()
            (output / 'measurements.json').write_text(json.dumps(measurements, ensure_ascii=False, indent=2))
    print(f'PASS: {len(measurements)} connection layouts; no external writes', flush=True)


if __name__ == '__main__':
    main()
