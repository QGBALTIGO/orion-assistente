"""Browser regression for the conversations-only Central; all transport is synthetic."""
from pathlib import Path
import json
import re
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts" / "central-mobile-qa"
OUT.mkdir(parents=True, exist_ok=True)
panel = (ROOT / "app/static/panel.html").read_text(encoding="utf-8")
panel_css = "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", panel, flags=re.S | re.I))
style = panel_css + "\n" + (ROOT / "app/static/orion-suite.css").read_text(encoding="utf-8") + "\n" + (ROOT / "app/static/orion-inbox.css").read_text(encoding="utf-8")
sources = [(ROOT / "app/static" / name).read_text(encoding="utf-8")
           for name in ("orion-conversations.js", "orion-inbox.js")]
fixture = {
    "tenant": "41000000-0000-4000-8000-000000000001",
    "channel": "42000000-0000-4000-8000-000000000001",
    "customer": "43000000-0000-4000-8000-000000000001",
    "account": "44000000-0000-4000-8000-000000000001",
    "phone": "+5567999999901", "second": "+5567999999902",
}
api = r"""
window.centralQA = {calls:[],writes:0,
  async request(path,method='GET',body){
    window.centralQA.calls.push({path,method});
    if(method!=='GET'){window.centralQA.writes++;throw Error('No writes allowed in this visual fixture');}
    if(path.endsWith('/capabilities'))return {enabled:true,central_enabled:true};
    if(path.endsWith('/channels'))return {account_id:QA.account,send_enabled:true,
      items:[{id:QA.channel,label:'WhatsApp de teste',provider:'qr',enabled:true}]};
    if(path.includes('/conversations?'))return {items:[
      {channel_id:QA.channel,contact_e164:QA.phone,name:'Cliente com nome completo e sobrenomes no WhatsApp',
        provider:'qr',mode:'ai',unread_count:19,preview:'Precisava confirmar um horário amanhã.',updated_at:'2026-10-25T14:00:00Z'},
      {channel_id:QA.channel,contact_e164:QA.second,name:'Outro cliente',provider:'qr',mode:'human',
        unread_count:0,preview:'Muito obrigado!',updated_at:'2026-10-25T13:40:00Z'}
    ],has_more:false};
    if(path.includes('/channels/')&&path.includes('/messages?')){
      const owned=new URL(path,'https://orion.test').searchParams.get('phone')===QA.second;
      return {customer_id:QA.customer,
        control:{mode:owned?'human':'ai',revision:3,assigned_account_id:owned?QA.account:null,pending_operation:null},
        items:[
          {id:'msg-one',direction:'in',source:'customer',kind:'text',body:'Quero marcar um corte amanhã',status:'received',created_at:'2026-10-25T12:00:00Z'},
          {id:'msg-two',direction:'out',source:'assistant',kind:'text',
            body:'Temos corte e barba disponíveis. '+('TextoLongoSemEspacos'.repeat(12)),
            status:'delivered',created_at:'2026-10-25T12:01:00Z'}
        ],has_more:false,preserved_history_available:true};
    }
    throw Error('Unexpected or removed endpoint: '+path);
  }
};
"""
html = """<!doctype html><html lang="pt-BR" data-theme="light"><head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>""" + style + """</style></head><body>
<div id="appView" class="app orion-suite-enabled">
<aside class="sidebar"><div class="brand">ORION</div></aside><main class="content">
<header class="os-header">ORION</header>
<section id="page-suite" class="page os-main" data-os-current-route="inbox">
<div class="os-title-row"><div><h1>Central de atendimento</h1><p>Acompanhe suas conversas e atendimentos.</p></div></div>
<div id="osInboxHost"></div></section></main></div></body></html>"""
sizes = [(320,568),(375,667),(393,852),(430,932),(768,1024),(1366,900)]
results = []
with sync_playwright() as p:
    cases = [(p.chromium,width,height,theme) for width,height in sizes for theme in ("light","dark")]
    if not __import__("os").getenv("ORION_CHROME_ONLY"):
        cases += [(p.webkit,393,852,theme) for theme in ("light","dark")]
    browsers = {}
    try:
        for engine,width,height,theme in cases:
            name = "webkit" if engine is p.webkit else "chromium"
            if name not in browsers:
                browsers[name] = engine.launch(args=["--no-sandbox"] if name=="chromium" else [])
            context = browsers[name].new_context(viewport={"width":width,"height":height},
                                                 is_mobile=width<768,has_touch=width<768)
            context.route("**/*",lambda route:route.abort())
            page = context.new_page()
            errors = []
            page.on("pageerror",lambda error:errors.append(str(error)))
            page.set_content(html.replace('data-theme="light"',f'data-theme="{theme}"'))
            page.evaluate("window.QA="+json.dumps(fixture)+";")
            page.evaluate(api)
            for source in sources:
                page.add_script_tag(content=source)
            page.evaluate("""async () => await OrionInbox.mount({
              host:document.querySelector('#osInboxHost'),tenantId:QA.tenant,
              canWrite:true,request:centralQA.request
            })""")
            page.locator(".os-central-thread-item-button").first.wait_for()
            assert page.locator("[data-inbox-tab],[role=tablist]").count()==0
            assert not page.locator(".os-central-filters").evaluate("(el)=>el.open")

            def inspect(state,thread=False,details=False):
                measure = page.evaluate("""() => {
                  const rect=selector=>{const el=document.querySelector(selector);if(!el)return null;
                    const r=el.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,right:r.right,bottom:r.bottom};};
                  return {viewport:innerWidth,viewportHeight:innerHeight,document:document.documentElement.scrollWidth,
                    shell:rect('.os-central-shell'),timeline:rect('.os-central-thread-scroll'),
                    composer:rect('.os-central-compose'),menu:rect('.os-central-control-options'),
                    primary:rect('[data-central-action=mode-human]:not([hidden])')};
                }""")
                assert measure["document"]<=width+2,(name,width,height,theme,state,measure)
                assert measure["shell"]["right"]<=width+2,(name,width,height,theme,state,measure)
                assert measure["shell"]["x"]>=-2,(name,width,height,theme,state,measure)
                if thread:
                    assert measure["timeline"]["height"]>=80,(name,width,height,theme,state,measure)
                    assert measure["composer"]["bottom"]<=measure["shell"]["bottom"]+2,(name,width,height,theme,state,measure)
                    if measure["primary"]:
                        assert 40<=measure["primary"]["height"]<=52,(name,width,height,theme,state,measure)
                if details:
                    menu=measure["menu"]
                    assert menu["x"]>=0 and menu["right"]<=width+1,(name,width,height,theme,state,measure)
                    assert menu["bottom"]<=measure["viewportHeight"]+2,(name,width,height,theme,state,measure)
                    assert menu["bottom"]<=measure["composer"]["y"]-7,(name,width,height,theme,state,measure)
                results.append({"engine":name,"width":width,"height":height,"theme":theme,
                                "state":state,"measurements":measure})
                return measure

            inspect("list")
            page.locator("[data-central-phone]").first.click()
            page.locator(".os-central-thread-body").wait_for()
            assert page.locator(".os-central-message").count()==2
            assert page.locator(".os-central-selected-copy p").inner_text()=="(67) 99999-9901"
            assert page.locator(".os-central-thread-scroll .os-central-history-tools").count()==0
            assert not page.locator(".os-central-control-menu").evaluate("(el)=>el.open")
            assert not page.locator("[data-central-action=mode-paused]").is_visible()
            assert not page.locator("#centralFind").is_visible()
            assert page.locator("[data-central-compose] button").is_disabled()
            before=inspect("ai-thread",thread=True)
            if (width,theme) in ((393,"light"),(393,"dark"),(1366,"light")):
                page.screenshot(path=str(OUT/f"{name}-{width}-{theme}-thread.png"),full_page=True)

            page.locator(".os-central-control-menu>summary").click()
            page.locator(".os-central-control-options").wait_for(state="visible")
            page.evaluate("() => new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))")
            opened=inspect("details-open",thread=True,details=True)
            assert abs(opened["timeline"]["height"]-before["timeline"]["height"])<=2
            assert page.locator("[data-central-action=mode-paused]").is_visible()
            page.locator("#centralFind").fill("amanhã")
            assert page.locator(".os-central-message:visible").count()==1
            page.locator("#centralFind").fill("")
            assert page.locator(".os-central-message:visible").count()==2
            page.keyboard.press("Escape")
            assert not page.locator(".os-central-control-menu").evaluate("(el)=>el.open")
            assert page.locator(".os-central-control-menu>summary").evaluate("(el)=>el===document.activeElement")
            page.locator(".os-central-control-menu>summary").click()
            page.locator(".os-central-selected-copy h2").click()
            assert not page.locator(".os-central-control-menu").evaluate("(el)=>el.open")

            if page.locator("[data-central-action=back]").is_visible():
                page.locator("[data-central-action=back]").click()
            page.locator('[data-central-phone="'+fixture["second"]+'"]').click()
            page.locator(".os-central-selected-copy h2").filter(has_text="Outro cliente").wait_for()
            assert page.locator("[data-central-compose] button").is_enabled()
            inspect("human-thread",thread=True)
            page.locator("#centralText").fill("Rascunho de teste preservado")
            page.locator(".os-central-control-menu>summary").click()
            page.keyboard.press("Escape")
            assert page.locator("#centralText").input_value()=="Rascunho de teste preservado"
            if page.locator("[data-central-action=back]").is_visible():
                page.locator("[data-central-action=back]").click()
                assert not page.locator(".os-central-show-thread").count()
                page.locator('[data-central-phone="'+fixture["second"]+'"]').click()
                page.locator(".os-central-thread-body").wait_for()
                assert page.locator("#centralText").input_value()=="Rascunho de teste preservado"
            if (name,width,theme)==("chromium",393,"light"):
                # One reduced-height regression for the keyboard-sized viewport.
                page.set_viewport_size({"width":width,"height":420})
                page.locator(".os-central-control-menu>summary").click()
                page.evaluate("() => new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))")
                inspect("details-reduced-height",details=True)
                page.keyboard.press("Escape")
                page.set_viewport_size({"width":width,"height":height})
            assert page.evaluate("centralQA.writes")==0
            assert page.evaluate("centralQA.calls.every(c=>!(/\\/(customers|labels|quick-replies|workflows)/).test(c.path))")
            assert not errors,(name,width,height,theme,errors)
            page.evaluate("OrionInbox.unmount()")
            context.close()
    finally:
        for browser in browsers.values():
            browser.close()
(OUT/"results.json").write_text(json.dumps({"results":results,"real_sends":0},indent=2),encoding="utf-8")
print(f"PASS {len(results)} browser states, no writes, compact controls and preserved drafts")
