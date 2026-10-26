"""Browser screenshots for Orion's read-only today agenda preview.

Uses the complete panel page and assets, synthetic company and appointment data,
and blocks ALL network writes. Never uses production credentials or real clients.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
from zoneinfo import ZoneInfo
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts' / 'dashboard-today'
OUT.mkdir(parents=True, exist_ok=True)
PANEL = (ROOT / 'app' / 'static' / 'panel.html').read_text(encoding='utf-8')
ORIGIN = 'https://orion-preview.invalid'
TIMEZONE = 'America/Campo_Grande'
TODAY = datetime.now(ZoneInfo(TIMEZONE)).date().isoformat()
TENANT = '10000000-0000-4000-8000-000000000001'
BUSINESS = {'id': TENANT, 'name': 'Barbearia de testes', 'slug': 'barbearia-de-testes',
            'timezone': TIMEZONE, 'role': 'owner'}
PROFILE = {'business_name': 'Barbearia de testes', 'assistant_name': 'Orion',
           'timezone': TIMEZONE, 'categories': [], 'hours': [], 'faqs': [],
           'policies': [], 'differentiators': [], 'objectives': [], 'capabilities': [],
           'style_rules': {}, 'autonomy_rules': {}}

def booking(index, clock, duration, status='confirmed'):
    begin = f'{TODAY}T{clock}:00-04:00'
    hour, minute = map(int, clock.split(':'))
    end = datetime.fromisoformat(begin).timestamp() + 60 * duration
    return {
      'id': f'00000000-0000-4000-8000-{index:012d}',
      'status': status,
      'start_at': begin,
      'end_at': datetime.fromtimestamp(end, ZoneInfo(TIMEZONE)).isoformat(),
      'customer_name': ['Ana Oliveira', 'Carlos Ferreira', 'Mariana Costa'][index-1],
      'professional_id': '20000000-0000-4000-8000-000000000001',
      'professional_name': 'Lucas • Barbeiro',
      'professional_color': '#2e7bf6',
      'total_cents': 4500 if index!=2 else 2800,
      'items': [{'name_snapshot': 'Corte + Barba' if index != 2 else 'Barba e acabamento'}],
    }

ITEMS = [booking(1,'09:00',55,'completed'),
         booking(2,'11:30',35,'confirmed'),
         booking(3,'15:00',50,'confirmed')]

def run(page, width, theme):
    calls, errors = [], []
    page.on('pageerror', lambda err: errors.append(str(err)))
    def respond_route(route):
        req=route.request
        u=urlsplit(req.url)
        path=u.path
        calls.append((req.method,path))
        if req.method!='GET':
            route.fulfill(status=405,body='No writes allowed in layout QA');errors.append('Unsafe write '+path);return
        def fulfill(payload,status=200):
            route.fulfill(status=status,content_type='application/json',body=json.dumps(payload,ensure_ascii=False))
        if path=='/panel':
            route.fulfill(status=200,content_type='text/html',body=PANEL);return
        if path.startswith(('/panel-assets/','/brand/')):
            asset=ROOT / 'app' / 'static' / path.rsplit('/',1)[-1]
            if asset.is_file():
                mime={'.js':'application/javascript','.css':'text/css','.svg':'image/svg+xml',
                      '.woff2':'font/woff2','.png':'image/png','.webp':'image/webp'}.get(asset.suffix.lower(),'application/octet-stream')
                route.fulfill(status=200,content_type=mime,body=asset.read_bytes())
            else:
                route.fulfill(status=404,body='Not found')
            return
        if path=='/auth/me':
            fulfill({'authenticated':True,'role':'support','csrf':'synthetic-dashboard-csrf',
                     'name':'Profissional sintético','email':'test@example.invalid'});return
        if path=='/auth/features':
            fulfill({'registration':False,'passwordRecovery':False});return
        if path in ('/api/v1/account/businesses','/api/v1/tenants'):
            fulfill([BUSINESS]);return
        if path.startswith('/api/v1/onboarding/entry'):
            fulfill({'enabled':False,'kind':'dashboard'});return
        if path.endswith('/business/profile'):
            fulfill({'generation':0,'draft':None,'published':None,'draft_template':PROFILE,
                     'supported_capabilities':[],'scheduling_timezone':TIMEZONE,
                     'runtime_connected':True,'warnings':[],
                     'access':{'role':'owner','can_edit_draft':True,'can_publish':True}});return
        if path.endswith('/business/profile/history'):
            fulfill([]);return
        if path.endswith('/management/dashboard'):
            fulfill({'summary':{'appointments':3,'completed':1,'cancelled':0,'no_show':0},
                     'onboarding':{'has_appointment':True,'scheduling_ready':True,'customers_with_phone':6}});return
        if path.endswith('/finance/summary'):
            params=parse_qs(u.query)
            assert params.get('start_date')==[TODAY] and params.get('end_date')==[TODAY]
            fulfill({'summary':{'count':2,'balance_cents':90000,'income_cents':100000,
                     'expense_cents':10000}});return
        if path.endswith('/goals'):
            params=parse_qs(u.query)
            assert params.get('start_date')==[TODAY] and params.get('end_date')==[TODAY]
            fulfill({'items':[
                {'id':'30000000-0000-4000-8000-000000000001','professional_id':'20000000-0000-4000-8000-000000000001',
                 'metric':'revenue_cents','active':True,'start_date':TODAY,'end_date':TODAY,
                 'target_value':10000,'actual_value':8000},
                {'id':'30000000-0000-4000-8000-000000000002','professional_id':'20000000-0000-4000-8000-000000000002',
                 'metric':'revenue_cents','active':True,'start_date':TODAY,'end_date':TODAY,
                 'target_value':30000,'actual_value':22000}
            ]});return
        if path.endswith('/appointments'):
            params=parse_qs(u.query)
            assert params.get('day')==[TODAY], ('Not today',params)
            assert params.get('paged')==['true'],('Not paged',params)
            fulfill({'items':ITEMS,'total':len(ITEMS),'limit':50,'offset':0,'has_more':False});return
        if path.startswith('/api/v1/'):
            fulfill([]);return
        route.fulfill(status=404,body='Unknown path')
    page.route(ORIGIN+'/**',respond_route)
    page.add_init_script(f"localStorage.setItem('orion-theme','{theme}')")
    page.goto(ORIGIN+'/panel?aba=dashboard',wait_until='domcontentloaded')
    page.locator('#osDashboardAgendaContent .os-agenda-item').first.wait_for(timeout=12000)
    assert page.locator('#osDashboardAgendaContent .os-agenda-item').count()==3
    page.locator('.os-dashboard-finance-values').wait_for(timeout=12000)
    page.locator('#osDashboardGoals [role=progressbar]').wait_for(timeout=12000)
    assert page.locator('.os-dashboard-finance-value').count()==3
    assert '400,00' in page.locator('#osDashboardGoals').inner_text()
    assert page.locator('#osDashboardGoals [role=progressbar]').get_attribute('aria-valuenow')=='75'
    assert page.locator('.os-dashboard-agenda-head').inner_text().find('Agendamentos de hoje')!=-1
    assert page.locator('.os-dashboard-agenda-preview input[type=date]').count()==0
    assert len([c for c in calls if c[1].endswith('/appointments')])==1
    assert not errors,(width,theme,errors)
    metrics=page.evaluate("""() => {
      const container=document.querySelector('.os-dashboard-agenda-preview');
      const bounds=container.getBoundingClientRect();
      const row=container.querySelector('.os-agenda-item').getBoundingClientRect();
      const finance=document.querySelector('.os-dashboard-finance').getBoundingClientRect();
      const goals=document.querySelector('#osDashboardGoals').getBoundingClientRect();
      return {viewport:innerWidth,documentWidth:document.documentElement.scrollWidth,
        bounds:{left:bounds.left,right:bounds.right,width:bounds.width},
        finance:{left:finance.left,right:finance.right,width:finance.width},
        goals:{left:goals.left,right:goals.right,width:goals.width},
        firstRow:{left:row.left,right:row.right,width:row.width},
        theme:document.documentElement.dataset.theme,
        cardRows:container.querySelectorAll('.os-agenda-item').length};
    }""")
    assert metrics['bounds']['left']>=-1,(width,theme,metrics)
    assert metrics['bounds']['right']<=width+2,(width,theme,metrics)
    assert metrics['firstRow']['right']<=width+2,(width,theme,metrics)
    assert metrics['documentWidth']<=width+2,(width,theme,metrics)
    for section in ('finance','goals'):
        assert metrics[section]['left']>=-1,(width,theme,section,metrics)
        assert metrics[section]['right']<=width+2,(width,theme,section,metrics)
    if (width,theme) in ((390,'dark'),(390,'light'),(1280,'dark'),(375,'dark'),(768,'light')):
        page.locator('.os-dashboard-agenda-preview').screenshot(path=str(OUT/f'card-{width}-{theme}.png'))
        page.locator('.os-dashboard-finance').screenshot(path=str(OUT/f'finance-{width}-{theme}.png'))
        page.locator('#osDashboardGoals').screenshot(path=str(OUT/f'goals-{width}-{theme}.png'))
        page.screenshot(path=str(OUT/f'full-{width}-{theme}.png'),full_page=True)
    return dict(width=width,**metrics)

def main():
    measurements=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path='/usr/bin/google-chrome',args=['--no-sandbox'])
        for width in (320,375,390,414,768,1280):
            for theme in ('light','dark'):
                mobile=width<768
                context=browser.new_context(
                    viewport={'width':width,'height':900},is_mobile=mobile,has_touch=mobile,
                    device_scale_factor=2 if mobile else 1)
                page=context.new_page()
                measurements.append(run(page,width,theme))
                context.close()
        browser.close()
    (OUT/'measurements.json').write_text(json.dumps(measurements,ensure_ascii=False,indent=2),encoding='utf-8')
    print(f'PASS: {len(measurements)} states. Chromium synthetic company, today only, zero writes.')
if __name__=='__main__':
    main()
