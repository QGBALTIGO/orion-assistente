"""Playwright regression: portrait photo fills cards, long names and stable time picker."""
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[2]
HTML=(ROOT/'app/static/orion-public.html').read_text(encoding='utf-8')
CSS=(ROOT/'app/static/orion-public.css').read_text(encoding='utf-8')
JS=(ROOT/'app/static/orion-public.js').read_text(encoding='utf-8')
THEME=(ROOT/'app/static/orion-public-theme.js').read_text(encoding='utf-8')
URL='https://orion.test/s/empresa-sintetica'
SERVICE='10000000-0000-4000-8000-000000000001'
PRO1='20000000-0000-4000-8000-000000000001'
PRO2='20000000-0000-4000-8000-000000000002'
LONG_NAME='Maria Eduarda dos Santos de Albuquerque Fernandes Oliveira Filho'
FIRST='2030-01-07T09:00:00-03:00'
SECOND='2030-01-08T10:15:00-03:00'
FAKE_IMAGE='<svg xmlns="http://www.w3.org/2000/svg" width="200" height="245"><rect width="200" height="245" fill="#9ab5ca"/><circle cx="100" cy="65" r="36" fill="#6e879b"/><rect x="28" y="104" width="144" height="160" rx="50" fill="#52738f"/></svg>'
DATA={
  'site':{'title':'Barbearia de teste','theme_color':'#ffca00','show_prices':True},
  'today':'2030-01-07','timezone':'America/Sao_Paulo',
  'booking_horizon_days':60,'opening_hours':[],
  'services':[{'id':SERVICE,'name':'Corte masculino','catalog_kind':'base',
               'price_mode':'fixed','duration_min':40,'price_cents':4000,
               'professional_ids':[PRO1,PRO2]}],
  'professionals':[
    {'id':PRO1,'name':'Guilherme Cardoso de Oliveira Neto',
     'image_url':'https://images.example.invalid/pro1.webp'},
    {'id':PRO2,'name':LONG_NAME,
     'image_url':'https://images.example.invalid/pro2.webp'}
  ]
}

@pytest.mark.parametrize('browser_type',['chromium','webkit'])
@pytest.mark.parametrize('width',[320,390,768,1024])
@pytest.mark.parametrize('theme',['light','dark'])
def test_picker_photo_fill_wrapping_and_no_flicker(browser_type,width,theme):
    with sync_playwright() as p:
        browser=getattr(p,browser_type).launch()
        page=browser.new_page(viewport={'width':width,'height':780},device_scale_factor=2,
            color_scheme=theme,locale='pt-BR',has_touch=True,
            is_mobile=(browser_type=='webkit' and width<=390))
        downloads=[]
        requests=[]
        try:
            page.route('**/*',lambda route:route.abort())
            page.route('https://images.example.invalid/**',
                lambda route:(downloads.append(route.request.url),
                    route.fulfill(status=200,content_type='image/svg+xml',body=FAKE_IMAGE)))
            page.route(URL,lambda route:route.fulfill(status=200,
                content_type='text/html',body=HTML))
            page.route('**/api/v1/public/sites/empresa-sintetica',
                lambda route:route.fulfill(status=200,
                    content_type='application/json',body=json.dumps(DATA)))
            def respond_slots(route):
                params=parse_qs(urlsplit(route.request.url).query)
                requests.append(params)
                stamp=SECOND if params.get('day',[''])[0]=='2030-01-08' else FIRST
                route.fulfill(status=200,content_type='application/json',body=json.dumps({
                  'slots':[stamp],'quote':'q'*55,'price_cents':4000,
                  'duration_min':40,'price_mode':'fixed',
                  'services':['Corte masculino'],
                  'timezone':'America/Sao_Paulo'
                }))
            page.route('**/api/v1/public/sites/empresa-sintetica/availability?*',respond_slots)
            page.goto(URL,wait_until='domcontentloaded')
            page.add_style_tag(content=CSS)
            page.add_script_tag(content=THEME)
            page.add_script_tag(content=JS)
            page.locator('#site-content').wait_for(state='visible')
            page.locator('#hero-book').click()
            page.locator('#booking-service-list .service-card').click()
            page.locator('#booking-next').click()
            images=page.locator('.professional-choice .professional-photo img')
            assert images.count()==2
            page.wait_for_function("""() => [...document.querySelectorAll(
                '.professional-photo img')].every(img=>img.complete&&img.naturalWidth>0)""")
            baseline_downloads=len(downloads)
            assert baseline_downloads>=2  # Team gallery already renders the same staff photos.
            metrics=page.evaluate("""() => [...document.querySelectorAll('.professional-choice')].map(card => {
              const photo=card.querySelector('.professional-photo');
              const img=photo.querySelector('img');
              const name=card.querySelector('.professional-name');
              const area=photo.getBoundingClientRect();
              const imgRect=img.getBoundingClientRect();
              return {
                imageWithin: Math.abs(imgRect.width-area.width)<1 && Math.abs(imgRect.height-area.height)<1,
                imageFit:getComputedStyle(img).objectFit,
                imagePosition:getComputedStyle(img).objectPosition,
                photoRectangle:area.height>area.width,
                nameBelow:name.getBoundingClientRect().top>=area.bottom,
                nameFull:name.textContent,
                nameVisible:name.scrollHeight<=name.clientHeight+2,
                cardNoOverflow:card.scrollWidth<=card.clientWidth+2,
                cardHeight:card.getBoundingClientRect().height
              };
            })""")
            assert all(m['imageWithin'] and m['imageFit']=='cover'
                       and m['photoRectangle'] and m['nameBelow'] and m['nameVisible']
                       and m['cardNoOverflow'] for m in metrics),metrics
            assert metrics[1]['nameFull']==LONG_NAME
            assert abs(metrics[0]['cardHeight']-metrics[1]['cardHeight'])<=2,metrics
            assert page.evaluate("document.documentElement.scrollWidth<=window.innerWidth+2")
            assert page.locator('.professional-picker').evaluate(
                '(el)=>el.scrollWidth>=el.clientWidth')

            page.evaluate("""() => {
              window.__photoNodes=[...document.querySelectorAll('.professional-photo img')];
              window.__picker=document.querySelector('.professional-picker');
              window.__dayNodes=[...document.querySelectorAll('#booking-day-strip .day-button')];
            }""")
            page.locator('#booking-day-strip .day-button').nth(1).click()
            # A prior day's slots remain visible but disabled while updating.
            # Wait for the *new* list, not merely a visible stale slot element.
            page.wait_for_function("""() => document.querySelector('#time-slots')?.getAttribute('aria-busy')!=='true'
                && document.querySelector('#time-slots .slot')?.dataset.slotStart==='2030-01-08T10:15:00-03:00'""")
            assert requests[-1]['day']==['2030-01-08']
            assert page.evaluate("""() => window.__picker===document.querySelector('.professional-picker')
                && window.__photoNodes.every((image,i)=>image===document.querySelectorAll('.professional-photo img')[i])
                && window.__dayNodes[1]===document.querySelectorAll('#booking-day-strip .day-button')[1]""")
            slot=page.locator('#time-slots .slot').first
            page.evaluate("""() => window.__slot=document.querySelector('#time-slots .slot')""")
            slot.click()
            assert page.evaluate("""() => window.__slot===document.querySelector('#time-slots .slot')""")
            assert slot.get_attribute('aria-pressed')=='true'

            page.locator('.professional-choice').nth(1).click()
            page.wait_for_function("""() => document.querySelector('#time-slots')?.getAttribute('aria-busy')!=='true'
                && document.querySelector('#time-slots .slot')?.dataset.slotStart==='2030-01-08T10:15:00-03:00'""")
            assert requests[-1]['professional_id']==[PRO2]
            assert page.evaluate("""() => window.__photoNodes.every((image,i)=>
                image===document.querySelectorAll('.professional-photo img')[i])""")
            assert len(downloads)==baseline_downloads,'New date and professional must not refetch photo'
            assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth+2')
            page.locator('#time-slots .slot').click()
            page.locator('#booking-next').click()
            page.locator('#booking-back').click()
            assert page.evaluate("""() => window.__photoNodes.every((image,i)=>
                image===document.querySelectorAll('.professional-photo img')[i])""")
        finally:
            browser.close()

@pytest.mark.parametrize('browser_type',['chromium','webkit'])
@pytest.mark.parametrize('width',[320,390,768])
def test_picker_preview_date_change_does_not_remount_photos(browser_type,width):
    preview_url='https://orion.test/app/site-preview/10000000-0000-4000-8000-000000000001'
    with sync_playwright() as p:
        browser=getattr(p,browser_type).launch()
        page=browser.new_page(viewport={'width':width,'height':780},color_scheme='dark',
            device_scale_factor=2,has_touch=True,
            is_mobile=(browser_type=='webkit' and width<=390))
        try:
            page.route('**/*',lambda route:route.abort())
            page.route('https://images.example.invalid/**',
                lambda route:route.fulfill(
                    status=200,content_type='image/svg+xml',body=FAKE_IMAGE))
            page.route(preview_url,lambda route:route.fulfill(
                status=200,content_type='text/html',body=HTML))
            page.route('**/api/v1/tenants/*/management/site/preview',lambda route:route.fulfill(
                status=200,content_type='application/json',body=json.dumps(DATA)))
            page.goto(preview_url,wait_until='domcontentloaded')
            page.add_style_tag(content=CSS)
            page.add_script_tag(content=THEME)
            page.add_script_tag(content=JS)
            page.locator('#site-content').wait_for(state='visible')
            page.locator('#hero-book').click()
            page.locator('#booking-service-list .service-card').click()
            page.locator('#booking-next').click()
            page.wait_for_function("""() => [...document.querySelectorAll(
                '.professional-photo img')].every(img=>img.complete&&img.naturalWidth>0)""")
            page.evaluate("""() => window.__people=[...document.querySelectorAll(
                '.professional-choice .professional-photo img')]""")
            page.locator('#booking-day-strip .day-button').nth(2).click()
            page.locator('.professional-choice').nth(1).click()
            assert page.evaluate("""() => window.__people.every((img,i)=>
                img===document.querySelectorAll('.professional-choice img')[i])""")
            assert page.locator('#time-slots .preview-note').is_visible()
            assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth+2')
        finally:
            browser.close()
