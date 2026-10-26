"""Real browser regression: custom multi-service booking, no photo flicker and mobile fit."""
import json
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[2]
HTML=(ROOT/'app/static/orion-public.html').read_text(encoding='utf-8')
CSS=(ROOT/'app/static/orion-public.css').read_text(encoding='utf-8')
JS=(ROOT/'app/static/orion-public.js').read_text(encoding='utf-8')
THEME=(ROOT/'app/static/orion-public-theme.js').read_text(encoding='utf-8')
URL='https://orion.test/s/empresa-sintetica'
S1='10000000-0000-4000-8000-000000000001'
S2='10000000-0000-4000-8000-000000000002'
S3='10000000-0000-4000-8000-000000000003'
P1='20000000-0000-4000-8000-000000000001'
P2='20000000-0000-4000-8000-000000000002'
START='2030-01-07T10:00:00-03:00'

DATA={
    'today':'2030-01-07','timezone':'America/Sao_Paulo',
    'booking_horizon_days':60,'opening_hours':[],
    'site':{'title':'Barbearia de testes','theme_color':'#ffcb00','show_prices':True},
    'services':[
        {'id':S1,'name':'Corte masculino','catalog_kind':'base',
         'category':'Cabelo','duration_min':40,'price_cents':4000,'price_mode':'fixed',
         'image_url':'https://images.example.invalid/corte.jpg','professional_ids':[P1,P2]},
        {'id':S2,'name':'Limpeza de pele','catalog_kind':'base',
         'category':'Pele','duration_min':25,'price_cents':2500,'price_mode':'fixed',
         'image_url':'https://images.example.invalid/pele.jpg','professional_ids':[P1,P2]},
        {'id':S3,'name':'Hidratação','catalog_kind':'base',
         'category':'Cabelo','duration_min':30,'price_cents':3000,'price_mode':'from',
         'image_url':'https://images.example.invalid/hair.jpg','professional_ids':[P2]},
    ],
    'professionals':[
        {'id':P1,'name':'Profissional 1','image_url':'https://images.example.invalid/pro1.jpg'},
        {'id':P2,'name':'Profissional 2','image_url':'https://images.example.invalid/pro2.jpg'},
    ]
}

@pytest.mark.parametrize('engine',['chromium','webkit'])
@pytest.mark.parametrize('width',[320,390,768,1024])
def test_public_custom_booking_keeps_photos_mounted_and_sums_services(engine,width):
    with sync_playwright() as p:
        browser=getattr(p,engine).launch()
        page=browser.new_page(viewport={'width':width,'height':780},
            device_scale_factor=2,locale='pt-BR',color_scheme='dark',
            has_touch=True,is_mobile=(engine=='webkit' and width<=390))
        requests=[]
        try:
            page.route('**/*',lambda route:route.abort())
            page.route(URL,lambda route:route.fulfill(
                status=200,content_type='text/html',body=HTML))
            page.route('**/api/v1/public/sites/empresa-sintetica',
                lambda route:route.fulfill(status=200,content_type='application/json',body=json.dumps(DATA)))
            def availability(route):
                url=route.request.url
                requests.append(url)
                from urllib.parse import parse_qs,urlsplit
                qs=parse_qs(urlsplit(url).query)
                ids=qs.get('service_ids',[])
                assert ids==[S1,S2],qs
                assert 'addon_ids' not in qs
                route.fulfill(status=200,content_type='application/json',body=json.dumps({
                    'slots':[START], 'quote':'synthetic-valid-quote-of-at-least-forty-characters',
                    'price_cents':6500,'duration_min':65,'price_mode':'fixed',
                    'timezone':'America/Sao_Paulo',
                    'services':['Corte masculino','Limpeza de pele']}))
            page.route('**/api/v1/public/sites/empresa-sintetica/availability?*',availability)
            page.goto(URL,wait_until='domcontentloaded')
            page.add_style_tag(content=CSS)
            page.add_script_tag(content=THEME)
            page.add_script_tag(content=JS)
            page.locator('#site-content').wait_for(state='visible')
            page.locator('#hero-book').click()
            cards=page.locator('#booking-service-list .service-card')
            assert cards.count()==3
            page.evaluate("""() => {
                window.__originalCards=[...document.querySelectorAll('#booking-service-list .service-card')];
                window.__originalImages=[...document.querySelectorAll('#booking-service-list .service-thumb img')];
            }""")
            cards.nth(0).click()
            assert cards.nth(0).get_attribute('aria-pressed')=='true'
            cards.nth(1).click()
            assert cards.nth(1).get_attribute('aria-pressed')=='true'
            assert page.evaluate("""() => {
                const cards=[...document.querySelectorAll('#booking-service-list .service-card')];
                const images=[...document.querySelectorAll('#booking-service-list .service-thumb img')];
                return window.__originalCards.every((node,i)=>node===cards[i])
                    && window.__originalImages.every((node,i)=>node===images[i]);
            }"""), 'Selection does not recreate photo nodes'
            assert '65,00' in page.locator('#booking-total').inner_text()
            assert '1 h 5 min' in page.locator('#booking-total').inner_text()
            assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth+2')
            assert page.locator('#booking-dialog').evaluate('(el)=>el.scrollWidth<=el.clientWidth+2')
            page.locator('#booking-next').click()
            page.locator('#time-slots .slot').wait_for(state='visible')
            assert requests, 'Availability is fetched for both services'
            page.evaluate("""() => window.__originalStaffImage =
                document.querySelector('.professional-picker .mini-portrait img')""")
            page.locator('.professional-choice').nth(1).click()
            page.locator('#time-slots .slot').wait_for(state='visible')
            assert page.evaluate("""() => document.querySelector('.professional-picker .mini-portrait img')
                ===window.__originalStaffImage"""), 'Professional photo also stays mounted'
            page.locator('#time-slots .slot').click()
            page.locator('#booking-next').click()
            review=page.locator('.review-card').inner_text()
            assert 'Corte masculino + Limpeza de pele' in review
            assert '65,00' in review
            assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth+2')
        finally:
            browser.close()
