"""Screenshot and layout tests for Orion's Central (synthetic tenant; no network writes)."""
from __future__ import annotations
import json
import os
import re
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'artifacts'/'central-redesign'
OUT.mkdir(parents=True,exist_ok=True)
panel=(ROOT/'app/static/panel.html').read_text(encoding='utf-8')
panel_css='\n'.join(re.findall(r'<style[^>]*>(.*?)</style>',panel,flags=re.S|re.I))
suite=(ROOT/'app/static/orion-suite.css').read_text(encoding='utf-8')
inbox=(ROOT/'app/static/orion-inbox.css').read_text(encoding='utf-8')
conversations=(ROOT/'app/static/orion-conversations.js').read_text(encoding='utf-8')
inbox_js=(ROOT/'app/static/orion-inbox.js').read_text(encoding='utf-8')
html='''<!doctype html><html lang="pt-BR" data-theme="light"><head><meta name="viewport" content="width=device-width,initial-scale=1"><base href="https://orion-preview.invalid/"><style>'''+panel_css+'\n'+suite+'\n'+inbox+'''</style></head><body><div id="appView" class="app orion-suite-enabled"><aside class="sidebar"><div class="brand" style="font-size:20px;color:var(--os-primary);padding:18px">◉ ORION</div><div class="os-sidebar-context"><span class="os-overline">Meu negócio</span><div class="os-tenant-name">Barbearia Orion</div></div><nav class="os-nav"><span class="os-nav-link" style="display:flex">Dashboard</span><span class="os-nav-link">Agenda</span><span class="os-nav-link" aria-current="page">Central de atendimento</span><span class="os-nav-link">Financeiro</span></nav></aside><main class="content"><header class="os-header">ORION · Painel</header><section id="page-suite" class="page os-main" data-os-current-route="inbox"><div class="os-title-row"><div><h1>Central de atendimento</h1><p>Acompanhe suas conversas e atendimentos.</p></div></div><div id="osPageBody"><div id="osInboxHost"></div></div></section></main></div></body></html>'''
tenant='40000000-0000-4000-8000-000000000001'
channel='20000000-0000-4000-8000-000000000001'
account='30000000-0000-4000-8000-000000000001'
customer='10000000-0000-4000-8000-000000000001'
phone='+5567999999901'
request_js=r'''async function(path,method='GET',body){
  if(method!=='GET')throw Error('No write expected in visual preview');
  if(path.endsWith('/capabilities'))return {enabled:true,central_enabled:true};
  if(path.endsWith('/channels'))return {account_id:'ACCOUNT',send_enabled:true,items:[{id:'CHANNEL',label:'WhatsApp principal',provider:'qr',enabled:true}]};
  if(path.includes('/conversations?'))return {items:[
    {channel_id:'CHANNEL',contact_e164:'PHONE',name:'Ana Oliveira',mode:'human',provider:'qr',unread_count:2,message_count:3,preview:'Bom dia! Gostaria de agendar um horário.',updated_at:'2026-10-25T14:00:00Z'},
    {channel_id:'CHANNEL',contact_e164:'+5567999999902',name:'Carlos Almeida',mode:'ai',provider:'qr',unread_count:0,message_count:2,preview:'Muito obrigado, até amanhã!',updated_at:'2026-10-25T13:50:00Z'},
    {channel_id:'CHANNEL',contact_e164:'+5567999999903',name:'Marina Costa',mode:'waiting',provider:'qr',unread_count:1,message_count:2,preview:'Pode me confirmar o endereço?',updated_at:'2026-10-25T13:35:00Z'}
  ],has_more:false};
  if(path.includes('/messages?'))return {
    customer_id:'CUSTOMER',
    control:{mode:'human',assigned_account_id:'ACCOUNT',revision:2,pending_operation:null},
    items:[
      {id:'msg-1',direction:'in',source:'customer',kind:'text',body:'Bom dia! Gostaria de agendar um horário.',status:'received',created_at:'2026-10-25T13:58:00Z'},
      {id:'msg-2',direction:'out',source:'manual',kind:'text',body:'Olá, Ana! Posso verificar os horários disponíveis. Qual serviço você deseja?',status:'delivered',created_at:'2026-10-25T13:59:00Z'},
      {id:'msg-3',direction:'in',source:'customer',kind:'text',body:'Quero fazer cabelo e barba amanhã à tarde.',status:'received',created_at:'2026-10-25T14:00:00Z'}
    ],has_more:false,preserved_history_available:true
  };
  if(path.endsWith('/quick-replies'))return {items:[{id:'reply-1',title:'Saudação',category:'Atendimento',text:'Olá {{cliente.nome}}',version:1}]};
  if(path.endsWith('/labels'))return {items:[]};
  if(path.includes('/customers?'))return {items:[],has_more:false};
  if(path.includes('/communication/pending?'))return {items:[]};
  throw Error('Unexpected mock path: '+path);
}'''.replace('ACCOUNT',account).replace('CHANNEL',channel).replace('PHONE',phone).replace('CUSTOMER',customer)
measurements=[]
with sync_playwright() as p:
    engines=[('chromium',(320,375,393,768,1024,1440))]
    if os.getenv('ORION_CHROME_ONLY')!='1':engines.append(('webkit',(393,)))
    for engine,widths in engines:
        launch={'args':['--no-sandbox']} if engine=='chromium' else {}
        if engine=='chromium' and os.getenv('ORION_CHROME_PATH'):launch['executable_path']=os.environ['ORION_CHROME_PATH']
        browser=getattr(p,engine).launch(**launch)
        for width in widths:
            for theme in ('light','dark'):
                mobile=width<768
                context=browser.new_context(viewport={'width':width,'height':900},is_mobile=mobile,has_touch=mobile)
                def block(route):
                    name=route.request.url.rsplit('/',1)[-1]
                    file=ROOT/'app/static'/name
                    if file.is_file() and name.endswith('.woff2'):
                        route.fulfill(path=str(file),content_type='font/woff2')
                    else:
                        route.abort()
                context.route('**/*',block)
                page=context.new_page()
                page.set_content(html.replace('data-theme="light"',f'data-theme="{theme}"'))
                page.add_script_tag(content=conversations)
                page.add_script_tag(content=inbox_js)
                page.evaluate(f'''async()=>await window.OrionInbox.mount({{
                    host:document.getElementById('osInboxHost'),tenantId:'{tenant}',canWrite:true,request:{request_js}
                }})''')
                page.locator('.os-central-thread-item-button').first.wait_for()
                def inspect(label):
                    value=page.evaluate('''() => {
                      const shell=document.querySelector('.os-central-shell').getBoundingClientRect();
                      return {viewport:innerWidth,document:document.documentElement.scrollWidth,
                        shellWidth:shell.width,shellLeft:shell.left,shellRight:shell.right,
                        redundantTabs:document.querySelectorAll('[data-inbox-tab],[role=tablist]').length,
                        hasThread:!!document.querySelector('.os-central-thread-body'),
                        theme:document.documentElement.dataset.theme};
                    }''')
                    assert value['document']<=width+2,(engine,width,theme,label,value)
                    assert value['shellRight']<=width+2,(engine,width,theme,label,value)
                    assert value['shellLeft']>=-2,(engine,width,theme,label,value)
                    assert value['redundantTabs']==0,(engine,width,theme,label,value)
                    measurements.append(dict(engine=engine,width=width,view=label,**value))
                inspect('list')
                if (engine,width,theme) in [('chromium',393,'light'),('chromium',1440,'light'),('webkit',393,'light')]:
                    page.screenshot(path=str(OUT/f'{engine}-{width}-{theme}-list.png'),full_page=True)
                page.locator('.os-central-thread-item-button').first.click()
                page.locator('.os-central-thread-body').wait_for()
                inspect('thread')
                assert page.locator('[data-central-compose] button').is_enabled()
                assert page.locator('.os-central-message').count()==3
                assert page.locator('.os-central-mode-action:visible').count()<=4
                if (engine,width,theme) in [('chromium',393,'light'),('chromium',1440,'light'),('webkit',393,'light'),('chromium',393,'dark')]:
                    page.screenshot(path=str(OUT/f'{engine}-{width}-{theme}-thread.png'),full_page=True)
                if page.locator('[data-central-action=back]').is_visible():
                    page.locator('[data-central-action=back]').click()
                    assert page.locator('.os-central-show-thread').count()==0
                    assert page.locator('.os-central-thread-item-button').first.is_visible()
                page.evaluate('window.OrionInbox.unmount()')
                context.close()
        browser.close()
(OUT/'measurements.json').write_text(json.dumps(measurements,indent=2,ensure_ascii=False),encoding='utf-8')
print(f'PASS {len(measurements)} layout states: desktop/mobile, light/dark, Chromium/WebKit. No API writes.')
