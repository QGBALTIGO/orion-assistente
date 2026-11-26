"""Exercise the actual packaged module, with offline API fixtures, in two browser engines."""
import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

KEY = "spoo_qa_synthetic_only_12345678"
ORIGIN = "https://orion-qa.test"
LINK_ID = "507f1f77bcf86cd799439011"
LONG = "https://example.com/" + "pagina-muito-longa-" * 12 + "?ref=qa&source=teste"
SHORT = "https://spoo.me/oferta-qa"
MODULE = """
import {mount} from '/shortener.js';
window.qa = {calls:[], notices:[], connected:false, fail:false, enabled:true,
   rows:[], copied:null};
const q = window.qa;
q.rows = [{id:'507f1f77bcf86cd799439011',short_url:'https://spoo.me/oferta-qa',long_url:LONG_PLACEHOLDER,status:'ACTIVE',total_clicks:0}];
const icon=()=>'<svg aria-hidden="true" class="os-icon" width="18" height="18" viewBox="0 0 24 24"><circle cx="12" cy="12" r="8" fill="none" stroke="currentColor"/></svg>';
const request=async (path,method,body) => {
 const action=path.split('/').pop();
 q.calls.push({action,method,body});
 if(method==='GET')return {enabled:q.enabled};
 if(body.api_key!==KEY_PLACEHOLDER)throw Error('Chave inválida');
 if(action==='list')return {items:q.rows,page:body.page,total:q.rows.length,has_next:false,omitted:0};
 if(action==='create'){
   if(q.fail)throw Error('Esse endereço personalizado já está em uso.');
   return {short_url:'https://spoo.me/oferta-qa'};
 }
 if(action==='stats')return {total_clicks:8,unique_clicks:6,period:'7d'};
 if(action==='status'){q.rows[0].status=body.status;return {status:body.status};}
 if(action==='delete'){q.rows=[];return {deleted:true};}
 throw Error('unexpected action');
};
Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async value=>{q.copied=value;}}});
window.renderQA=()=>{window.qaView?.unmount();window.qaView=mount({host:document.querySelector('#host'),tenantId:'qa-tenant',request,icon,showNotice:(text,error)=>q.notices.push({text,error})});};
window.renderQA();
""".replace("LONG_PLACEHOLDER", json.dumps(LONG)).replace("KEY_PLACEHOLDER", json.dumps(KEY))

def page_html(theme):
    return f"""<!doctype html><html lang="pt-BR" data-theme="{theme}"><head>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <link rel="stylesheet" href="/suite.css"><link rel="stylesheet" href="/affiliate.css">
    <style>*,*::before,*::after{{box-sizing:border-box}}body{{margin:0;background:var(--os-bg);color:var(--os-text);font:400 16px/1.5 var(--os-font)}}h1,h2,h3,p{{margin:0}}#frame{{min-width:0;display:grid;grid-template-columns:256px minmax(0,1fr)}}#rail{{background:var(--os-card);border-right:1px solid var(--os-border);min-height:100vh;padding:18px}}#rail p{{margin-top:12px}}.qa-content{{min-width:0;padding:24px}}@media(max-width:767px){{#frame{{grid-template-columns:minmax(0,1fr)}}#rail{{display:none}}.qa-content{{padding:12px}}}}</style></head>
    <body><div id="frame"><aside id="rail"><strong>Orion · Afiliados</strong><p>Início</p><p>Ofertas</p><p>Encurtador de links</p></aside><main class="os-main qa-content"><section id="host"></section></main></div><script type="module" src="/fixture.js"></script></body></html>"""

def run(engine, assets, width, theme, output):
    errors, unexpected = [], []
    with sync_playwright() as pw:
        browser = getattr(pw, engine).launch(headless=True)
        context = browser.new_context(viewport={"width": width, "height": 900})
        page = context.new_page()
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        sources = {"/": (page_html(theme), "text/html"),
                   "/suite.css": ((assets / "orion-suite.css").read_text(), "text/css"),
                   "/affiliate.css": ((assets / "orion-affiliate-workspace.css").read_text(), "text/css"),
                   "/shortener.js": ((assets / "orion-affiliate-shortener.js").read_text(), "text/javascript"),
                   "/fixture.js": (MODULE, "text/javascript")}
        def route(r):
            u = urlsplit(r.request.url)
            if f"{u.scheme}://{u.netloc}" != ORIGIN:
                unexpected.append(r.request.url)
                r.abort()
            elif u.path in sources:
                content, mime = sources[u.path]
                r.fulfill(status=200, content_type=mime, body=content)
            elif u.path.startswith("/brand/"):
                r.fulfill(status=404, body="")
            else:
                unexpected.append(r.request.url)
                r.abort()
        page.route("**/*", route)
        page.goto(ORIGIN)
        page.locator("#shortener-key").fill(KEY)
        page.locator('[data-shortener-form="connect"] button[type=submit]').click()
        page.locator("#shortener-url").fill(LONG)
        page.locator("#shortener-alias").fill("oferta-qa")
        page.locator('input[name=accepted]').check()
        page.locator('[data-shortener-form="create"] button[type=submit]').click()
        page.locator('[data-shortener-result] strong').wait_for()
        assert page.locator('[data-shortener-result] strong').text_content() == SHORT
        page.locator('[data-shortener="copy-result"]').click()
        assert page.evaluate("qa.copied") == SHORT
        page.locator('[data-shortener="stats"]').click()
        page.get_by_text("Únicos estimados:").wait_for()
        page.locator('[data-shortener="toggle"]').click()
        page.get_by_text("Pausado", exact=True).wait_for()
        page.locator('[data-shortener="toggle"]').click()
        page.get_by_text("Ativo", exact=True).wait_for()
        page.locator("#shortener-url").fill(LONG)
        page.locator("#shortener-alias").fill("oferta-qa")
        page.locator('input[name=accepted]').check()
        assert page.locator("#shortener-alias").evaluate("(el)=>el.checkValidity()")
        page.evaluate("qa.fail=true")
        page.locator('[data-shortener-form="create"] button[type=submit]').click()
        page.get_by_text("Esse endereço personalizado já está em uso.").wait_for()
        # Scroll width and component width must remain within the viewport.
        metrics=page.evaluate("""()=>({viewport:innerWidth,document:document.documentElement.scrollWidth,host:document.querySelector('#host').getBoundingClientRect().width,scroll:document.querySelector('#host').scrollWidth})""")
        assert metrics["document"] <= width + 1 and metrics["scroll"] <= metrics["host"] + 1, metrics
        output.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(output / f"{engine}-{width}-{theme}.png"),full_page=True)
        page.locator('[data-shortener="delete"]').click()
        page.locator('[data-shortener-dialog] button[value=cancel]').click()
        assert page.evaluate("qa.rows.length") == 1
        page.locator('[data-shortener="delete"]').click()
        page.locator('[data-shortener-dialog] button[value=confirm]').click()
        page.get_by_text("Nenhum link encontrado nesta página.", exact=False).wait_for()
        page.locator('[data-shortener="disconnect"]').click()
        page.locator("#shortener-key").wait_for()
        assert page.locator("#shortener-key").input_value() == ""
        page.evaluate("qa.enabled=false;renderQA()")
        page.get_by_text("O encurtamento dentro da Orion aguarda liberação", exact=False).wait_for()
        assert page.locator('a[href="https://spoo.me/"]').count() == 1
        assert page.locator("#shortener-key").count() == 0
        assert not errors and not unexpected, (errors, unexpected)
        browser.close()
        print(f"PASS {engine} {width}px {theme}: creation, copy, statistics, pause, collision, delete/cancel, disconnect and disabled state", flush=True)

if __name__ == "__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--engine",required=True,choices=["chromium","webkit"])
    p.add_argument("--assets",type=Path,required=True)
    p.add_argument("--output",type=Path,default=Path("artifacts/affiliate-shortener"))
    args=p.parse_args()
    for width in (320,430,768,1024,1440):
        for theme in ("light","dark"):
            run(args.engine,args.assets,width,theme,args.output)
