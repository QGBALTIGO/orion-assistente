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
OUT = ROOT / 'artifacts' / 'dashboard-finance-responsive'
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

MAX_CENTS = 9_007_199_254_740_991
FINANCES = {
    'normal': {'count': 2, 'balance_cents': 90_000,
               'pending_income_cents': 15_000, 'income_cents': 100_000, 'expense_cents': 10_000},
    'large': {'count': MAX_CENTS, 'balance_cents': MAX_CENTS,
              'pending_income_cents': MAX_CENTS, 'income_cents': MAX_CENTS, 'expense_cents': 0},
    'negative': {'count': 1_234_567_890_123, 'balance_cents': -MAX_CENTS,
                 'pending_income_cents': 123_456_789_012_345, 'income_cents': 0,
                 'expense_cents': MAX_CENTS},
}

def money_exact(cents):
    amount = abs(cents)
    return ('-' if cents < 0 else '') + 'R$\N{NO-BREAK SPACE}' + format(amount // 100, ',').replace(',', '.') + f',{amount%100:02d}'


def run(page, width, theme, scenario='normal', engine='chromium'):
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
            fulfill({'summary':FINANCES[scenario]});return
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
    expected = [money_exact(FINANCES[scenario]['balance_cents']),
                money_exact(FINANCES[scenario]['pending_income_cents']),
                format(FINANCES[scenario]['count'], ',').replace(',', '.')]
    displayed = page.locator('.os-dashboard-finance-value>strong').all_inner_texts()
    assert displayed == expected, (engine,width,theme,scenario,displayed,expected)
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
        cardRows:container.querySelectorAll('.os-agenda-item').length,
        financeCards:[...document.querySelectorAll('.os-dashboard-finance-value')].map(card=>{
          const rect=card.getBoundingClientRect(),amount=card.querySelector('strong'),
                valueRect=amount.getBoundingClientRect();
          return {left:rect.left,right:rect.right,top:rect.top,width:rect.width,height:rect.height,
             valueLeft:valueRect.left,valueRight:valueRect.right,
             valueScroll:amount.scrollWidth,valueWidth:amount.clientWidth,
             font:getComputedStyle(amount).fontSize,
             text:amount.textContent,title:amount.getAttribute('title')};
        }),
        financeGridTracks:getComputedStyle(document.querySelector('.os-dashboard-finance-values')).gridTemplateColumns
          .trim().split(/\\s+/).length};
    }""")
    assert metrics['bounds']['left']>=-1,(width,theme,metrics)
    assert metrics['bounds']['right']<=width+2,(width,theme,metrics)
    assert metrics['firstRow']['right']<=width+2,(width,theme,metrics)
    assert metrics['documentWidth']<=width+2,(width,theme,metrics)
    for section in ('finance','goals'):
        assert metrics[section]['left']>=-1,(width,theme,section,metrics)
        assert metrics[section]['right']<=width+2,(width,theme,section,metrics)
    cards = metrics['financeCards']
    assert len(cards) == 3, (engine,width,theme,scenario,cards)
    assert all(x['title'] == x['text'] for x in cards), (engine,width,theme,scenario,cards)
    assert max(x['width'] for x in cards) - min(x['width'] for x in cards) <= 2, (engine,width,theme,scenario,cards)
    for card in cards:
        assert card['left'] >= metrics['finance']['left'] - 2, (engine,width,theme,scenario,card)
        assert card['right'] <= metrics['finance']['right'] + 2, (engine,width,theme,scenario,card)
        assert card['valueLeft'] >= card['left'] - 1, (engine,width,theme,scenario,card)
        assert card['valueRight'] <= card['right'] + 1, (engine,width,theme,scenario,card)
        assert card['valueScroll'] <= card['valueWidth'] + 2, (engine,width,theme,scenario,card)
    if metrics['finance']['width'] <= 840:
        assert metrics['financeGridTracks'] == 1, (engine,width,theme,scenario,metrics)
        assert len({round(x['top']) for x in cards}) == 3, (engine,width,theme,scenario,cards)
    elif metrics['finance']['width'] > 840:
        assert metrics['financeGridTracks'] == 3, (engine,width,theme,scenario,metrics)
        assert max(x['top'] for x in cards)-min(x['top'] for x in cards)<=2, (engine,width,theme,scenario,cards)
    if (width,theme,scenario) in ((320,'light','large'),(390,'dark','negative'),
                                 (768,'light','large'),(1280,'dark','large'),(390,'light','normal')):
        page.locator('.os-dashboard-finance').screenshot(
            path=str(OUT/f'finance-{engine}-{width}-{theme}-{scenario}.png'))
    return dict(engine=engine,width=width,scenario=scenario,**metrics)

def main():
    measurements=[]
    with sync_playwright() as playwright:
        for engine,widths in ((playwright.chromium,(320,375,390,430,600,768,1024,1280,1600)),
                              (playwright.webkit,(375,390,768))):
            browser=engine.launch(args=['--no-sandbox'] if engine is playwright.chromium else [])
            try:
                for width in widths:
                    for theme in ('light','dark'):
                        for scenario in FINANCES:
                            mobile=width<768
                            context=browser.new_context(
                                viewport={'width':width,'height':900},
                                is_mobile=mobile,has_touch=mobile,
                                device_scale_factor=2 if mobile else 1)
                            try:
                                page=context.new_page()
                                measurements.append(run(page,width,theme,scenario,
                                                        'webkit' if engine is playwright.webkit else 'chromium'))
                            finally:
                                context.close()
            finally:
                browser.close()
    (OUT/'measurements.json').write_text(json.dumps(measurements,ensure_ascii=False,indent=2),encoding='utf-8')
    print(f'PASS: {len(measurements)} states across Chromium, WebKit, and extreme monetary values; zero writes.')
if __name__=='__main__':
    main()
