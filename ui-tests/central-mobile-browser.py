"""Real-browser Central de Atendimento QA: safe synthetic data, no sends."""
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
js = [(ROOT / "app/static" / name).read_text(encoding="utf-8") for name in
      ("orion-flows.js", "orion-conversations.js", "orion-inbox.js")]
tenant = "41000000-0000-4000-8000-000000000001"
channel = "42000000-0000-4000-8000-000000000001"
customer = "43000000-0000-4000-8000-000000000001"
account = "44000000-0000-4000-8000-000000000001"
reply = "46000000-0000-4000-8000-000000000001"
phone = "+5567999999901"
history = [{"id": f"47000000-0000-4000-8000-{i:012d}",
            "start_at": "2026-10-25T13:30:00Z", "status": "completed" if i % 4 else "cancelled",
            "services": "Corte, barba e acabamento", "professional": "Alex", "protocol": f"AG-{i:03d}"}
           for i in range(20)]
fixture = {
    "tenant": tenant, "channel": channel, "customer": customer, "account": account, "reply": reply, "phone": phone, "history": history
}

api = r"""
window.centralQA = {
  requestCount:0, previewCount:0, writes:0, failed:[],
  async request(path,method='GET',body){
    window.centralQA.requestCount++;
    if(method!=='GET'){
      if(path.includes('/quick-replies/') && path.endsWith('/preview')){
        window.centralQA.previewCount++; return {text:'Olá, cliente de teste. Confira seu atendimento.',sent:false};
      }
      window.centralQA.writes++;throw Error('A qualified UI must not mutate real data');
    }
    if(path.endsWith('/capabilities'))return {enabled:true,central_enabled:true};
    if(path.endsWith('/channels'))return {account_id:QA.account,send_enabled:true,items:[{id:QA.channel,label:'WhatsApp de teste',provider:'qr',enabled:true}]};
    if(path.includes('/conversations?'))return {items:[
      {channel_id:QA.channel,contact_e164:QA.phone,name:'Cliente com nome completo no WhatsApp',provider:'qr',mode:'human',unread_count:19,preview:'Precisava confirmar um horário, é possível?',message_count:23,updated_at:'2026-10-25T14:00:00Z'},
      {channel_id:QA.channel,contact_e164:'+5567999999902',name:'Outro cliente',provider:'qr',mode:'ai',unread_count:0,preview:'Muito obrigado!',updated_at:'2026-10-25T13:40:00Z'}
    ],has_more:false};
    if(path.includes('/channels/')&&path.includes('/messages?'))
      return {customer_id:QA.customer,control:{mode:'human',revision:3,assigned_account_id:QA.account,pending_operation:null},
        items:[
          {id:QA.account,direction:'in',source:'customer',kind:'text',body:'Quero marcar um corte amanhã',status:'received',created_at:'2026-10-25T12:00:00Z'},
          {id:QA.customer,direction:'out',source:'manual',kind:'text',body:'Vou consultar a agenda para você.',status:'delivered',created_at:'2026-10-25T12:01:00Z'}
        ],has_more:false,preserved_history_available:true};
    if(path.endsWith('/labels'))return {items:[
      {id:'45000000-0000-4000-8000-000000000001',name:'Cliente recorrente',color:'#3183dd',version:1},
      {id:'45000000-0000-4000-8000-000000000002',name:'Preferências especiais',color:'#18836b',version:1}
    ]};
    if(path.endsWith('/quick-replies'))return {items:[
      {id:QA.reply,title:'Confirmação',category:'Agendamento',version:2,visibility:'company',text:'Olá {{cliente.nome}}'}
    ]};
    if(path.includes('/customers?'))return {items:[
      {id:QA.customer,name:'Cliente com nome completo no WhatsApp',phone:QA.phone}
    ],has_more:false};
    if(path.endsWith('/customers/'+QA.customer))return {
      customer:{id:QA.customer,name:'Cliente com nome completo no WhatsApp',whatsapp_e164:QA.phone,notes:'Observações sintéticas de serviço.'},
      timezone:'America/Campo_Grande',labels:[],upcoming:{
        id:QA.customer,start_at:'2026-10-29T14:00:00Z',services:'Corte e barba',professional:'Alex',protocol:'AG-TEST'
      },history:QA.history,history_limit:20
    };
    if(path.includes('/communication/pending?'))return {items:[]};
    if(path.endsWith('/workflows'))return {items:[]};
    throw Error('API não simulada: '+path);
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
<div class="os-title-row"><div><h1>Central de atendimento</h1><p>Conversas, clientes e respostas com dados reais.</p></div></div>
<div id="osInboxHost"></div></section></main></div></body></html>"""

results = []
with sync_playwright() as p:
    cases = [(p.chromium, width, theme) for width in (320, 375, 390, 768, 1280) for theme in ("light", "dark")]
    if not __import__("os").getenv("ORION_CHROME_ONLY"):
        cases += [(p.webkit, 390, theme) for theme in ("light", "dark")]
    browsers = {}
    try:
        for engine, width, theme in cases:
            name = "webkit" if engine is p.webkit else "chromium"
            browser = browsers.get(name)
            if browser is None:
                browser = engine.launch(args=["--no-sandbox"] if name == "chromium" else [])
                browsers[name] = browser
            context = browser.new_context(viewport={"width": width, "height": 900},
                                          is_mobile=width < 768, has_touch=width < 768)
            context.route("**/*", lambda route: route.abort())
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.set_content(html.replace('data-theme="light"', f'data-theme="{theme}"'))
            page.evaluate("window.QA=" + json.dumps(fixture) + ";")
            page.evaluate(api)
            for source in js:
                page.add_script_tag(content=source)
            page.evaluate(f"""async () => await OrionInbox.mount({{
                host:document.querySelector('#osInboxHost'),
                tenantId:'{tenant}',canWrite:true,request:window.centralQA.request
            }})""")
            page.locator(".os-central-thread-item-button").first.wait_for()
            assert page.locator(".os-inbox-more").count() == 0
            assert not page.locator(".os-central-filters").evaluate("(el)=>el.open")
            assert page.locator(".os-inbox-primary-nav [role=tab]").count() == 4

            def inspect(state):
                dimensions = page.evaluate("""() => ({
                    viewport:innerWidth,doc:document.documentElement.scrollWidth,
                    content:document.querySelector('#page-suite').getBoundingClientRect().right,
                    tab:document.querySelector('.os-inbox-topbar').getBoundingClientRect().right
                })""")
                assert dimensions["doc"] <= width + 2, (name, width, theme, state, dimensions)
                assert dimensions["tab"] <= width + 2, (name, width, theme, state, dimensions)
                assert dimensions["content"] <= width + 2, (name, width, theme, state, dimensions)
                results.append({"engine": name, "width": width, "theme": theme, "view": state,
                                "overflow": False, "page_errors": len(errors)})
            inspect("conversations")
            if (width,theme) in ((390,"light"),(390,"dark"),(1280,"light")):
                page.screenshot(path=str(OUT/f"{name}-{width}-{theme}-conversations.png"), full_page=True)
            page.locator("[data-central-phone]").first.click()
            page.locator(".os-central-thread-body").wait_for()
            assert page.locator(".os-central-messages .os-central-message").count() == 2
            inspect("thread")
            if (width,theme) in ((390,"light"),(1280,"light")):
                page.screenshot(path=str(OUT/f"{name}-{width}-{theme}-thread.png"),full_page=True)
            page.locator("[data-inbox-tab=customers]").click()
            page.locator("[data-inbox-customer]").first.click()
            page.locator(".os-inbox-customer-profile").wait_for()
            assert page.locator(".os-inbox-appointments:not(.os-inbox-notes)").count() == 1
            assert not page.locator(".os-inbox-appointments:not(.os-inbox-notes)").evaluate("(x)=>x.open")
            inspect("client")
            if (width,theme) in ((390,"light"),(1280,"light")):
                page.screenshot(path=str(OUT/f"{name}-{width}-{theme}-client.png"),full_page=True)
            page.locator(".os-inbox-appointments:not(.os-inbox-notes)>summary").click()
            assert page.locator(".os-inbox-appointments:not(.os-inbox-notes) li").count() == 20
            inspect("client-history")
            if (width,theme) == (390,"light"):
                page.screenshot(path=str(OUT/f"{name}-{width}-{theme}-client-history.png"),full_page=True)
            page.locator("[data-inbox-view=labels]").click()
            page.locator(".os-inbox-label-list").wait_for()
            inspect("labels")
            page.locator("[data-inbox-tab=replies]").click()
            page.locator(".os-inbox-replies-panel").wait_for()
            inspect("replies")
            if (width,theme) in ((390,"light"),(1280,"light")):
                page.screenshot(path=str(OUT/f"{name}-{width}-{theme}-replies.png"),full_page=True)
            page.locator("[data-inbox-tab=flows]").click()
            page.locator("[data-flow-action=new]").wait_for()
            inspect("simulations")
            assert page.evaluate("centralQA.writes") == 0
            assert not errors, (name, width, theme, errors)
            page.evaluate("OrionInbox.unmount()")
            context.close()
    finally:
        for browser in browsers.values():
            browser.close()

(OUT / "results.json").write_text(json.dumps({"results":results,"real_sends":0},indent=2),encoding="utf-8")
print(f"PASS {len(results)} browser views, no real sends, no overflow")
