"""Real browser checks of the shipped account and affiliate renderers; all data synthetic."""
import argparse
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

ORIGIN = "https://orion-ui-qa.invalid"
TENANT = "10000000-0000-4000-8000-000000000001"
MEMBER_BOOT = """
const tenant={id:'10000000-0000-4000-8000-000000000001',name:'Empresa de teste',slug:'empresa',role:'owner',status:'active',workspace_profile:'standard',timezone:'America/Campo_Grande'};
const permissions={code:'elite',name:'Elite',active:true,status:'active',workspace_profile:'standard',business_niche:'barbearia',layout_modes:['classic','app'],permissions:{'account.manage':true,'business.setup':true,'settings.manage':true,'dashboard.basic':true,'agenda.manage':true,'clients.manage':true,'professionals.manage':true,'services.manage':true},limits:{max_professionals:10,active_professionals:0},revision:'qa-ui'};
window.OrionSuite.mount({user:{role:'member',name:'Conta de teste',email:'conta@example.invalid'},tenants:[tenant],tenantId:tenant.id,initialRoute:'member',businessContextReady:true,request:async path=>path.endsWith('/management/permissions')?{effective_plan:permissions}:[],openLegacy(route){
  document.querySelectorAll('.content>.page').forEach(el=>el.classList.toggle('hidden',el.id!=='page-'+route));
  window.OrionSuite.legacyOpened(route);
},onRouteChange(){}});
"""
AFFILIATE_BOOT = """
import {mount} from '/panel-assets/orion-affiliate-workspace.js';
const now=1795635390;
const dest={id:'b'.repeat(32),name:'Canal sintético',mode:'telegram',verified:true};
const job={id:'c'.repeat(32),destination:dest.id,status:'queued',due:now+3600,changed:now,doc:{offer:{title:'Oferta de teste',retailer_name:'Shopee',price:'19.90',currency:'BRL'},rule:{timezone:'America/Campo_Grande'}}};
const state={company:'Afiliados de teste',sending:false,offer:[],decision:[],rule:[],jobs:[job],product_flag:[],niches:[{id:'casa',name:'Casa'}],sorts:{'2':'Mais vendidos'},connection:[{id:'shopee',status:'connected'}],template:[{id:'default',name:'Modelo padrão',media:'text',brand:'Orion'}],deal_template:'default',destination:[dest],automation_destinations:[{...dest,eligible:true}]};
const icon=()=>'<svg class="os-icon" width="16" height="16" aria-hidden="true" viewBox="0 0 16 16"><circle cx="8" cy="8" r="5" fill="none" stroke="currentColor"/></svg>';
const request=async(path,method='GET')=>{
 if(method!=='GET')throw Error('Synthetic QA forbids writes');
 if(path.endsWith('/state'))return structuredClone(state);
 if(path.includes('/analytics'))throw Error('Relatório de teste indisponível');
 if(path.includes('/catalog'))return {items:[],total:0};
 return [];
};
window.qaView=mount({host:document.getElementById('af-host'),tenantId:'10000000-0000-4000-8000-000000000001',view:'dashboard',request,icon,showNotice(){},open(route){window.qaView.setView(route);},whatsappAvailable:true});
"""

def shell(assets, theme, mode):
    original=(assets/"panel.html").read_text()
    styles="\n".join(re.findall(r"<style[^>]*>([\s\S]*?)</style>",original))
    start=original.index('<section id="page-member"')
    end=original.index('<section id="page-dispositivo"',start)
    member=original[start:end]
    aside='<aside class="sidebar"><div class="brand">Orion</div><div class="sidebar-bottom"></div></aside>'
    if mode=="member":
        contents=member+'<section id="page-original" class="page hidden"></section>'
        appclass="app"
        scripts='<script src="/panel-assets/orion-fields.js"></script><script src="/panel-assets/orion-suite.js"></script><script src="/member-boot.js"></script>'
    else:
        contents='<section id="page-suite" class="page os-main"><section id="af-host"></section></section>'
        appclass="app orion-suite-enabled os-structured-workspace"
        scripts='<script type="module" src="/affiliate-boot.js"></script>'
    return f'<!doctype html><html lang="pt-BR" data-theme="{theme}" data-theme-preference="{theme}"><head><meta name="viewport" content="width=device-width, initial-scale=1"><style>{styles}</style><link rel="stylesheet" href="/panel-assets/orion-suite.css"><link rel="stylesheet" href="/panel-assets/orion-fields.css"><link rel="stylesheet" href="/panel-assets/orion-affiliate-workspace.css"><style>html,body{{margin:0}}#appView{{min-height:100vh}}body{{background:var(--os-bg);color:var(--os-text)}}.brand{{min-height:48px}}</style></head><body><div id="appView" class="{appclass}">{aside}<main class="content">{contents}</main></div>{scripts}</body></html>'

def verify(page, selector, width):
    group=page.locator(selector)
    group.wait_for()
    metrics=group.evaluate("""el=>{
      const css=getComputedStyle(el),r=el.getBoundingClientRect(),buttons=[...el.querySelectorAll(':scope > button')];
      const selected=buttons.filter(b=>b.getAttribute('aria-pressed')==='true'||b.getAttribute('aria-selected')==='true');
      const probe=document.createElement('span');probe.style.backgroundColor='var(--os-bg)';el.appendChild(probe);
      const bg=getComputedStyle(probe).backgroundColor;probe.remove();
      return {display:css.display,wrap:css.flexWrap,radius:parseFloat(css.borderRadius),overflow:css.overflowX,
        count:buttons.length,classes:buttons.every(b=>b.classList.contains('os-pill-tab')),
        top:buttons.map(b=>Math.round(b.getBoundingClientRect().top)),selected:selected.length,
        activeBackground:selected.length?getComputedStyle(selected[0]).backgroundColor:null,bg,
        left:r.left,right:r.right,width:r.width,scroll:el.scrollWidth,client:el.clientWidth,
        viewport:innerWidth,document:document.documentElement.scrollWidth};
    }""")
    assert metrics["display"]=="flex" and metrics["wrap"]=="nowrap", (selector,metrics)
    assert metrics["radius"]>=90 and metrics["overflow"]=="auto", (selector,metrics)
    assert metrics["classes"] and metrics["count"]>=2 and metrics["selected"]==1, (selector,metrics)
    assert max(metrics["top"])-min(metrics["top"])<=1, (selector,metrics)
    assert metrics["activeBackground"]==metrics["bg"], (selector,metrics)
    assert metrics["left"]>=-1 and metrics["right"]<=width+1, (selector,metrics)
    assert metrics["document"]<=width+1, (selector,metrics)
    return metrics

def run(engine, assets, width, theme, output, browser):
    context=browser.new_context(viewport={"width":width,"height":900},color_scheme=theme)
    page=context.new_page()
    errors=[]
    page.on("pageerror",lambda e:errors.append(str(e)))
    def route(r):
        u=urlsplit(r.request.url)
        if f"{u.scheme}://{u.netloc}"!=ORIGIN:
            r.abort();return
        if u.path=="/member":
            r.fulfill(status=200,content_type="text/html",body=shell(assets,theme,"member"));return
        if u.path=="/affiliate":
            r.fulfill(status=200,content_type="text/html",body=shell(assets,theme,"affiliate"));return
        if u.path=="/member-boot.js":
            r.fulfill(status=200,content_type="text/javascript",body=MEMBER_BOOT);return
        if u.path=="/affiliate-boot.js":
            r.fulfill(status=200,content_type="text/javascript",body=AFFILIATE_BOOT);return
        if u.path.startswith("/panel-assets/"):
            file=assets/Path(u.path).name
            if file.is_file():
                mime="text/javascript" if file.suffix==".js" else "text/css"
                r.fulfill(status=200,content_type=mime,body=file.read_text());return
        # Fonts/images may be mapped by the production server; test still uses
        # the actual declared fallback fonts and all geometry from shipped CSS.
        r.fulfill(status=404,body="")
    page.route("**/*",route)
    page.goto(ORIGIN+"/member")
    page.locator('#osMemberAccount').wait_for()
    verify(page,".os-account-menu",width)
    for key in ("business","security","appearance","profile"):
        page.locator(f'[data-os-account-tab="{key}"]').click()
        assert page.locator(f'[data-os-account-tab="{key}"]').get_attribute("aria-pressed")=="true"
        verify(page,".os-account-menu",width)
    page.locator('[data-os-account-tab="appearance"]').click()
    page.locator('#osMemberAppearance [data-os-theme="'+theme+'"]').click()
    output.mkdir(parents=True,exist_ok=True)
    page.screenshot(path=str(output/f"{engine}-{width}-{theme}-account.png"),full_page=True)
    page.goto(ORIGIN+"/affiliate")
    groups=[("dashboard",".af-dashboard-periods"),("dashboard",".af-dashboard-metric-tabs"),
            ("dashboard",".af-dashboard-publication-tabs"),("ofertas",".af-offer-tabs"),
            ("automacoes",".af-auto-filters"),("fila",".af-history-filters")]
    current="dashboard"
    for view,selector in groups:
        if view!=current:
            page.evaluate("(view)=>qaView.setView(view)",view)
            current=view
        verify(page,selector,width)
        if view in ("dashboard","ofertas","automacoes","fila"):
            assert page.locator('.os-ui-heading').count()==1
        # Click a second choice and verify the real renderer maintains selection.
        choices=page.locator(selector+" > button")
        choices.nth(1).click()
        page.wait_for_timeout(80)
        verify(page,selector,width)
        page.screenshot(path=str(output/f"{engine}-{width}-{theme}-{selector[1:]}.png"),full_page=True)
    assert not errors, errors
    context.close()
    print(f"PASS {engine} {width}px {theme}: conta + seis grupos nativos de Afiliados",flush=True)

if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--assets",type=Path,required=True)
    parser.add_argument("--engine",choices=["chromium","webkit"],required=True)
    parser.add_argument("--output",type=Path,default=Path("artifacts/orion-design-system"))
    args=parser.parse_args()
    with sync_playwright() as pw:
        browser=getattr(pw,args.engine).launch(headless=True)
        for width in (320,430,768,1024,1440):
            for theme in ("light","dark"):
                run(args.engine,args.assets,width,theme,args.output,browser)
        browser.close()
