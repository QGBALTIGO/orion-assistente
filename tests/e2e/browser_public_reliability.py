"""Isolated browser acceptance: no live routes, bookings, calendars or messages."""
import json
import os
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[2]
HTML=(ROOT/'app/static/orion-public.html').read_text(encoding='utf-8')
CSS=(ROOT/'app/static/orion-public.css').read_text(encoding='utf-8')
JS=(ROOT/'app/static/orion-public.js').read_text(encoding='utf-8')
THEME=(ROOT/'app/static/orion-public-theme.js').read_text(encoding='utf-8')
URL='https://orion.test/s/empresa-sintetica'
SERVICE='10000000-0000-4000-8000-000000000001'
PRO='20000000-0000-4000-8000-000000000001'
START='2030-01-07T10:00:00-03:00'
QUOTE='synthetic-valid-quote-of-at-least-forty-characters'
DATA={
    'today':'2030-01-07','timezone':'America/Sao_Paulo',
    'booking_horizon_days':60,'opening_hours':[],
    'site':{'title':'Barbearia de testes','theme_color':'#fdc700',
            'show_prices':True,'whatsapp_phone':'+5511999999999',
            'maps_url':'https://share.google/Abc123xyZ','address':'Endereço sintético'},
    'services':[{'id':SERVICE,'name':'Hidratação e corte de avaliação',
                 'catalog_kind':'combo','featured':True,'duration_min':80,
                 'price_cents':99999999,'price_mode':'from','professional_ids':[PRO]}],
    'professionals':[{'id':PRO,'name':'Profissional sintético',
                      'bookable_weekdays':[0,1,2,3,4,5]}]
}
AVAILABLE={'slots':[START],'quote':QUOTE,'expires_in':600,
           'price_cents':99999999,'duration_min':80,'price_mode':'from',
           'timezone':'America/Sao_Paulo','services':['Hidratação e corte de avaliação']}
CONFIRMED={'status':'confirmed','protocol':'SYNTHETIC-1','start_at':START,
           'end_at':'2030-01-07T11:20:00-03:00','price_cents':99999999,
           'price_mode':'from','professional':'Profissional sintético',
           'services':['Hidratação e corte de avaliação']}

def serve(page):
    posts=[]
    page.route('**/*',lambda route:route.abort())
    page.route(URL,lambda route:route.fulfill(status=200,content_type='text/html',body=HTML))
    page.route('**/api/v1/public/sites/empresa-sintetica',lambda route:route.fulfill(
        status=200,content_type='application/json',body=json.dumps(DATA)))
    page.route('**/api/v1/public/sites/empresa-sintetica/availability?*',lambda route:route.fulfill(
        status=200,content_type='application/json',body=json.dumps(AVAILABLE)))
    def appointment(route):
        posts.append(route.request.post_data_json)
        route.fulfill(status=409,content_type='application/json',body=json.dumps({
            'detail':{'code':'agenda_busy','message':'Agenda ocupada temporariamente.','retryable':True}}))
    page.route('**/api/v1/public/sites/empresa-sintetica/appointments',appointment)
    page.route('**/api/v1/public/sites/empresa-sintetica/appointments/recover',lambda route:route.fulfill(
        status=200,content_type='application/json',body=json.dumps(CONFIRMED)))
    return posts

def boot(page):
    page.goto(URL,wait_until='domcontentloaded')
    page.add_style_tag(content=CSS)
    page.add_script_tag(content=THEME)
    page.add_script_tag(content=JS)
    page.locator('#site-content').wait_for(state='visible')

def assert_fit(page):
    assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth+2')
    assert page.locator('#booking-dialog').evaluate('(el)=>el.scrollWidth<=el.clientWidth+2')
    footer=page.locator('.booking-footer').bounding_box()
    total=page.locator('#booking-total').bounding_box()
    next_button=page.locator('#booking-next').bounding_box()
    assert footer and total and next_button
    assert total['x']>=footer['x']-1
    assert total['x']+total['width']<=footer['x']+footer['width']+1
    assert (total['y']+total['height']<=next_button['y']+1 or
            total['x']+total['width']<=next_button['x']+1), 'price and confirm button cannot overlap'

CONTRAST="""({selector,background})=>{
 const node=document.querySelector(selector),bg=document.querySelector(background);
 const canvas=document.createElement('canvas');canvas.width=canvas.height=1;
 const c=canvas.getContext('2d');
 const lum=value=>{c.fillStyle=value;c.fillRect(0,0,1,1);
  const a=[...c.getImageData(0,0,1,1).data].slice(0,3).map(n=>n/255).map(n=>n<=.04045?n/12.92:((n+.055)/1.055)**2.4);
  return a[0]*.2126+a[1]*.7152+a[2]*.0722;};
 const a=lum(getComputedStyle(node).color),b=lum(getComputedStyle(bg).backgroundColor);
 return (Math.max(a,b)+.05)/(Math.min(a,b)+.05);
}"""

@pytest.mark.parametrize('engine',['chromium','webkit','firefox'])
@pytest.mark.parametrize('width',[320,360,390,768,1024,1440])
@pytest.mark.parametrize('theme',['light','dark'])
def test_public_footer_contrast_keyboard_closed_dates_and_safe_recovery(engine,width,theme):
    with sync_playwright() as p:
        browser=getattr(p,engine).launch()
        page=browser.new_page(viewport={'width':width,'height':900},locale='pt-BR',color_scheme=theme)
        errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
        posts=serve(page)
        try:
            boot(page)
            assert page.locator('#maps-link').get_attribute('href')==DATA['site']['maps_url']
            page.locator('#service-search').fill('hidratacao')
            assert page.locator('#services-grid .service-card').count()==1
            assert page.evaluate(CONTRAST,{'selector':'.tag','background':'.tag'})>=4.5
            page.locator('#services-grid .service-card').focus()
            page.keyboard.press('Enter')
            assert page.evaluate('document.activeElement.tagName')=='H3'
            assert_fit(page)
            assert page.evaluate(CONTRAST,{'selector':'#booking-steps [aria-current=step]',
                                          'background':'#booking-steps [aria-current=step]'})>=4.5
            page.locator('#booking-next').focus();page.keyboard.press('Enter')
            page.locator('#time-slots .slot').wait_for(state='visible')
            assert page.locator('[data-day="2030-01-13"]').is_disabled()
            assert 'Fechado' in page.locator('[data-day="2030-01-13"]').inner_text()
            assert 'America/' not in page.locator('.timezone-note').inner_text()
            page.locator('#time-slots .slot').focus();page.keyboard.press('Enter')
            page.locator('#booking-next').focus();page.keyboard.press('Enter')
            assert '10 minutos' in page.locator('.booking-confirmation-note').inner_text()
            assert_fit(page)
            assert page.evaluate(CONTRAST,{'selector':'#booking-next','background':'#booking-next'})>=4.5
            if os.environ.get('ORION_PUBLIC_UI_ARTIFACTS') and width in (320,1440):
                artifacts=Path(os.environ['ORION_PUBLIC_UI_ARTIFACTS']);artifacts.mkdir(parents=True,exist_ok=True)
                page.screenshot(path=str(artifacts/f'{engine}-{width}-{theme}-confirmation.png'),full_page=True)
            page.locator('[name=customer_name]').fill('Cliente sintético')
            page.locator('[name=customer_phone]').fill('(11) 99999-9999')
            page.locator('[name=confirmed]').focus();page.keyboard.press('Space')
            page.locator('#booking-next').focus();page.keyboard.press('Enter')
            page.locator('#booking-error').wait_for(state='visible')
            assert page.evaluate('document.activeElement.id')=='booking-error'
            assert page.locator('[name=customer_phone]').get_attribute('readonly') is not None
            assert page.locator('#booking-back').is_disabled()
            assert len(posts)==1
            stored=page.evaluate('Object.values(sessionStorage).map(value=>JSON.parse(value))')
            assert stored==[{'quote':QUOTE,'start_at':START}]
            assert page.evaluate('localStorage.length')==0
            boot(page)  # Same browser tab preserves only the scoped recovery token.
            page.locator('#booking-recovery-banner button').click()
            assert page.locator('[name=customer_name]').count()==0
            page.locator('[name=customer_phone]').fill('(11) 99999-9999')
            page.locator('#booking-next').click()
            page.locator('.success').wait_for(state='visible')
            assert 'SYNTHETIC-1' in page.locator('.success').inner_text()
            assert page.locator('.booking-follow-up a').get_attribute('href')=='https://wa.me/5511999999999'
            assert page.evaluate('sessionStorage.length')==0
            assert len(posts)==1,'recovery must never create another booking'
            if os.environ.get('ORION_PUBLIC_UI_ARTIFACTS') and width in (320,1440):
                page.screenshot(path=str(artifacts/f'{engine}-{width}-{theme}-recovered.png'),full_page=True)
            assert not errors,errors
        finally:
            browser.close()

@pytest.mark.parametrize('engine',['chromium','webkit','firefox'])
def test_public_confirmation_reflows_at_two_hundred_percent_browser_content_zoom(engine):
    with sync_playwright() as p:
        browser=getattr(p,engine).launch()
        page=browser.new_page(viewport={'width':768,'height':900},locale='pt-BR')
        serve(page)
        try:
            boot(page)
            page.evaluate("document.documentElement.style.zoom='2'")
            page.locator('#services-grid .service-card').click();page.locator('#booking-next').click()
            page.locator('#time-slots .slot').click();page.locator('#booking-next').click()
            assert_fit(page)
            assert page.locator('#booking-next').is_visible()
        finally:
            browser.close()

@pytest.mark.parametrize('engine',['chromium','webkit','firefox'])
def test_public_requests_reuse_http_only_same_origin_browser_budget_cookie(engine):
    # WebKit's intercepted request headers do not expose its Cookie header.
    # A loopback HTTP receiver verifies the actual network transport instead.
    observed=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*_args):pass
        def do_GET(self):
            assets={'/s/empresa-sintetica':('text/html',HTML),
                    '/public-assets/orion-public.js':('text/javascript',JS),
                    '/public-assets/orion-public.css':('text/css',CSS),
                    '/public-assets/orion-public-theme.js':('text/javascript',THEME)}
            cookie=False
            if self.path=='/api/v1/public/sites/empresa-sintetica':
                content_type,body='application/json',json.dumps(DATA);cookie=True
            elif self.path.startswith('/api/v1/public/sites/empresa-sintetica/availability?'):
                observed.append(self.headers.get('Cookie',''))
                content_type,body='application/json',json.dumps(AVAILABLE)
            elif self.path in assets:content_type,body=assets[self.path]
            else:self.send_error(404);return
            self.send_response(200);self.send_header('Content-Type',content_type)
            if cookie:self.send_header('Set-Cookie','orion_public_browser=synthetic-browser; HttpOnly; SameSite=Lax; Path=/')
            encoded=body.encode('utf-8');self.send_header('Content-Length',str(len(encoded)));self.end_headers();self.wfile.write(encoded)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    worker=Thread(target=server.serve_forever,daemon=True);worker.start()
    origin=f'http://127.0.0.1:{server.server_port}'
    try:
        with sync_playwright() as p:
            browser=getattr(p,engine).launch()
            page=browser.new_page(viewport={'width':390,'height':900},locale='pt-BR')
            page.route('**/*',lambda route:route.continue_() if route.request.url.startswith(origin+'/') else route.abort())
            try:
                page.goto(origin+'/s/empresa-sintetica',wait_until='domcontentloaded')
                page.locator('#site-content').wait_for(state='visible')
                page.locator('#services-grid .service-card').click();page.locator('#booking-next').click()
                page.locator('#time-slots .slot').wait_for(state='visible')
                assert observed and all('orion_public_browser=synthetic-browser' in cookie for cookie in observed)
                assert 'orion_public_browser' not in page.evaluate('document.cookie'),'cookie is not readable by JavaScript'
            finally:browser.close()
    finally:
        server.shutdown();server.server_close();worker.join(timeout=2)

@pytest.mark.parametrize('engine',['chromium','webkit','firefox'])
def test_recovered_confirmation_downloads_the_original_calendar_times(engine):
    with sync_playwright() as p:
        browser=getattr(p,engine).launch()
        page=browser.new_page(viewport={'width':390,'height':900},locale='pt-BR')
        posts=serve(page)
        page.add_init_script("sessionStorage.setItem('orion-public-recovery:/api/v1/public/sites/empresa-sintetica',"+
                             json.dumps(json.dumps({'quote':QUOTE,'start_at':START}))+');')
        try:
            boot(page);page.locator('#booking-recovery-banner button').click()
            page.locator('[name=customer_phone]').fill('(11) 99999-9999');page.locator('#booking-next').click()
            page.locator('.success').wait_for(state='visible')
            with page.expect_download() as pending:
                page.get_by_role('button',name='Adicionar ao meu calendário').click()
            download=pending.value
            assert download.suggested_filename=='agendamento.ics'
            calendar=Path(download.path()).read_text(encoding='utf-8')
            assert 'DTSTART:20300107T130000Z' in calendar
            assert 'DTEND:20300107T142000Z' in calendar
            assert 'UID:SYNTHETIC-1-20300107T130000Z@orion.invalid' in calendar
            assert not posts,'downloading a receipt/calendar never creates a new appointment'
        finally:
            browser.close()

@pytest.mark.parametrize('engine',['chromium','webkit','firefox'])
@pytest.mark.parametrize('width',[320,1024])
@pytest.mark.parametrize('theme',['light','dark'])
def test_owner_notices_keep_timezone_review_and_financial_estimates_readable(engine,width,theme):
    html='''<!doctype html><html lang="pt-BR"><head><meta name="viewport" content="width=device-width,initial-scale=1">
      <style>*{box-sizing:border-box}html,body{margin:0}.hidden{display:none!important}</style></head>
      <body><div id="appView" class="app"><aside class="sidebar"><div class="brand">Orion</div></aside>
      <main class="content"><section class="page">Original</section></main></div></body></html>'''
    fixture={
        'tenant':'10000000-0000-4000-8000-000000000001','appointment':'20000000-0000-4000-8000-000000000010',
        'review':{'timezone_review_count':1,'notices':[{'message':'Confira os telefones e o endereço com o proprietário.'}],
                  'timezone_policy':'Os instantes gravados das reservas foram preservados. A revisão registra ciência.',
                  'timezone_reviews':[{'appointment_id':'20000000-0000-4000-8000-000000000010','protocol':'SYNTHETIC-10',
                    'previous_local_start':'2030-01-07T09:00:00-04:00','current_local_start':'2030-01-07T10:00:00-03:00',
                    'from_timezone':'America/Campo_Grande','to_timezone':'America/Sao_Paulo'}]}}
    with sync_playwright() as p:
        browser=getattr(p,engine).launch();page=browser.new_page(viewport={'width':width,'height':900},locale='pt-BR')
        page.route('**/*',lambda route:route.abort())
        page.route('https://orion.test/panel',lambda route:route.fulfill(status=200,content_type='text/html',body=html))
        errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
        try:
            page.goto('https://orion.test/panel',wait_until='domcontentloaded')
            page.add_style_tag(content=(ROOT/'app/static/orion-suite.css').read_text(encoding='utf-8'))
            page.add_script_tag(content=(ROOT/'app/static/orion-suite.js').read_text(encoding='utf-8'))
            page.evaluate('''({fixture,theme})=>{
              document.documentElement.dataset.theme=theme;window.__ownerWrites=[];
              window.OrionSuite.mount({user:{role:'member',name:'Conta sintética'},
                tenants:[{id:fixture.tenant,name:'Empresa sintética',slug:'empresa-sintetica',timezone:'America/Sao_Paulo',role:'owner'}],
                tenantId:fixture.tenant,initialRoute:'site',openLegacy(){},onRouteChange(){},
                request:async(path,method='GET')=>{
                  if(method!=='GET'){window.__ownerWrites.push({path,method});throw Error('Writes disabled in layout test');}
                  if(path.endsWith('/management/site'))return {title:'Empresa sintética',version:4,slug:'empresa-sintetica',operational_review:fixture.review};
                  if(path.endsWith('/management/site/publication'))return {can_edit:true,can_publish:true,draft_version:4,state:'ready',ready:true,issues:[]};
                  if(path.includes('/site/metrics?'))return {visits:0,daily:[]};
                  if(path.includes('/finance/summary?'))return {summary:{income_cents:5000,expense_cents:1000,balance_cents:4000,count:2,
                    awaiting_final_price_count:1,awaiting_final_price:[{id:fixture.appointment,protocol:'SYNTHETIC-10',start_at:'2030-01-07T13:00:00Z',total_cents:99999999}]}};
                  return [];
                }});
            }''',{'fixture':fixture,'theme':theme})
            notice=page.locator('#osSiteOperationalReview');notice.wait_for(state='visible')
            notice.locator('summary').click()
            assert '09:00' in notice.inner_text() and '10:00' in notice.inner_text()
            assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth+2')
            page.locator('[data-os-site-tab=settings]').click()
            page.locator('#osSiteOperationalReview summary').click()
            assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth+2')
            if width<768:page.locator('[data-os-action=menu]').first.click()
            page.locator('[data-os-route=financeiro]:visible').first.click()
            pending=page.locator('#osAwaitingFinalPrice');pending.wait_for(state='visible');pending.locator('summary').click()
            assert 'recebimento' in pending.inner_text()
            assert page.locator('.os-finance-metrics>.os-metric').count()==6
            assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth+2')
            assert not page.evaluate('window.__ownerWrites')
            assert not errors,errors
        finally:browser.close()
