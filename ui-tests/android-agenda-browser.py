"""Reproduce an Android-only navigation to Orion Agenda with controlled 401 errors.
All data are synthetic. No authentication secrets or external network requests.
"""
from __future__ import annotations
import json
from pathlib import Path
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'artifacts'/'android-agenda'
OUT.mkdir(parents=True,exist_ok=True)
HTML=(ROOT/'app/static/panel.html').read_text(encoding='utf-8')
ORIGIN='https://orion-preview.invalid'
TENANT='10000000-0000-4000-8000-000000000001'
CSRF='synthetic-android-session-csrf'
BUSINESS={'id':TENANT,'name':'Barbearia sintética','slug':'barbearia-sintetica','role':'owner','timezone':'America/Campo_Grande'}
PROFILE={
    'business_name':'Barbearia sintética','assistant_name':'Assistente sintética',
    'timezone':'America/Campo_Grande','categories':[],'hours':[],'faqs':[],
    'policies':[],'differentiators':[],'objectives':[],'capabilities':[],
    'style_rules':{},'autonomy_rules':{},
}
ANDROID_UA='Mozilla/5.0 (Linux; Android 15; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Mobile Safari/537.36'

def run(page,expired=False):
    state={'auth_me':0,'google':0,'logout':0,'requests':[]}
    errors=[]
    page.on('pageerror',lambda err:errors.append(str(err)))
    def route_call(route):
        request=route.request
        url=urlsplit(request.url)
        path=url.path
        state['requests'].append({'path':path,'method':request.method})
        def respond(payload,status=200):
            route.fulfill(status=status,content_type='application/json',body=json.dumps(payload,ensure_ascii=False))
        if path=='/panel':
            route.fulfill(status=200,content_type='text/html',body=HTML);return
        if path.startswith(('/panel-assets/','/brand/')):
            file=ROOT/'app/static'/path.rsplit('/',1)[-1]
            if file.is_file():
                ext=file.suffix.lower()
                mime={'.js':'application/javascript','.css':'text/css','.svg':'image/svg+xml','.woff2':'font/woff2','.png':'image/png'}.get(ext,'application/octet-stream')
                route.fulfill(status=200,content_type=mime,body=file.read_bytes())
            else:
                route.fulfill(status=404,body='Not found')
            return
        if path=='/auth/me':
            state['auth_me']+=1
            if expired and state['auth_me']>1:
                respond({'detail':'Sessão expirada'},401)
            else:
                respond({'authenticated':True,'role':'support','csrf':CSRF,'name':'Pessoa sintética','email':'synthetic@example.invalid'})
            return
        if path=='/auth/features':
            respond({'registration':False,'passwordRecovery':False});return
        if path=='/auth/logout':
            state['logout']+=1;respond({'ok':True});return
        if path=='/api/v1/account/businesses':
            respond([BUSINESS]);return
        if path.endswith('/business/profile'):
            respond({'generation':0,'draft':None,'published':None,'draft_template':PROFILE,
                 'supported_capabilities':[],'scheduling_timezone':PROFILE['timezone'],
                 'runtime_connected':True,'warnings':[],'access':{
                     'role':'owner','can_edit_draft':True,'can_publish':True}});return
        if path.endswith('/business/profile/history'):
            respond([]);return
        if path.startswith('/api/v1/onboarding/entry'):
            respond({'enabled':False,'kind':'dashboard'});return
        if path.endswith('/google/status'):
            state['google']+=1
            respond({'detail':'Integração Google não autorizada'},401);return
        if path.endswith('/management/dashboard'):
            respond({'summary':{'appointments':0,'completed':0,'customers':0}});return
        if path.endswith('/appointments'):
            respond({'items':[],'total':0,'offset':0,'limit':50,'has_more':False});return
        if path.startswith('/api/v1/'):
            respond([]);return
        route.fulfill(status=404,body='Not found')
    page.route(ORIGIN+'/**',route_call)
    page.goto(ORIGIN+'/panel?aba=dashboard',wait_until='domcontentloaded')
    page.wait_for_function("document.querySelector('#appView')&&!document.querySelector('#appView').classList.contains('hidden')")
    if page.locator('#osMenu').is_visible():
        page.locator('#osMenu').click()
    page.locator('#osNav [data-os-route="agenda"]').click()
    page.wait_for_function("document.querySelector('#osAgendaGoogleLabel')?.textContent?.includes('Não foi possível') || !document.querySelector('#loginView')?.classList?.contains('hidden')")
    page.wait_for_timeout(200)
    is_login=page.locator('#loginView').evaluate("node=>!node.classList.contains('hidden')")
    if expired:
        assert is_login,(state,errors)
    else:
        assert not is_login,(state,errors)
        assert page.locator('#page-suite').get_attribute('data-os-current-route')=='agenda'
        assert 'Não foi possível' in page.locator('#osAgendaGoogleLabel').inner_text()
        assert state['auth_me']==2,(state,errors)
        assert state['logout']==0,(state,errors)
        assert page.locator('#osAgendaCount').count()==1
    assert not errors,(state,errors)
    page.screenshot(path=str(OUT/('expired' if expired else 'active')+'-'+str(page.viewport_size['width'])+'.png'),full_page=True)
    return dict(expired=expired,width=page.viewport_size['width'],status='PASS',checks=state['auth_me'],googleErrors=state['google'],logoutRequests=state['logout'])

def main():
    results=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path='/usr/bin/google-chrome',args=['--no-sandbox'])
        for width,height in ((360,800),(390,844),(412,892)):
            context=browser.new_context(viewport={'width':width,'height':height},
                is_mobile=True,has_touch=True,device_scale_factor=2.75,user_agent=ANDROID_UA)
            page=context.new_page()
            results.append(run(page))
            context.close()
        context=browser.new_context(viewport={'width':390,'height':844},
            is_mobile=True,has_touch=True,device_scale_factor=2.75,user_agent=ANDROID_UA)
        results.append(run(context.new_page(),expired=True))
        context.close()
        browser.close()
    (OUT/'results.json').write_text(json.dumps(results,indent=2,ensure_ascii=False),encoding='utf-8')
    print('PASS Android Chromium:',results)
if __name__=='__main__':
    main()
