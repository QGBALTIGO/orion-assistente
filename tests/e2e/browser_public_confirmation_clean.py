"""Browser checks for streamlined public booking confirmation, synthetic data only."""
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
PRO='70000000-0000-4000-8000-000000000007'
SERVICE='50000000-0000-4000-8000-000000000005'
DATA={
 'timezone':'America/Campo_Grande','today':'2030-01-07',
 'booking_horizon_days':60,'opening_hours':[],
 'site':{'title':'Barbearia sintética','theme_color':'#2e7bf6','show_prices':True},
 'services':[{'id':SERVICE,'name':'Corte masculino','catalog_kind':'base',
             'price_cents':4000,'price_mode':'fixed','duration_min':40,
             'professional_ids':[PRO]}],
 'professionals':[{'id':PRO,'name':'Guilherme'}]
}

@pytest.mark.parametrize('browser_name,width,theme',[
 ('chromium',320,'dark'),
 ('chromium',390,'light'),
 ('chromium',768,'dark'),
 ('webkit',375,'dark'),
 ('webkit',430,'light')
])
def test_compact_confirmation_birth_and_phone(browser_name,width,theme):
    with sync_playwright() as p:
        browser=getattr(p,browser_name).launch(headless=True)
        page=browser.new_page(viewport={'width':width,'height':820},
                              color_scheme=theme,locale='pt-BR',device_scale_factor=2,
                              has_touch=width<=430,
                              is_mobile=(browser_name=='webkit' and width<=430))
        errors=[];posted=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        try:
            page.route('**/*',lambda route:route.abort())
            page.route(URL,lambda route:route.fulfill(status=200,
                       content_type='text/html',body=HTML))
            page.route('**/api/v1/public/sites/empresa-sintetica',
                       lambda route:route.fulfill(status=200,
                       content_type='application/json',body=json.dumps(DATA)))
            page.route('**/api/v1/public/sites/empresa-sintetica/availability?*',
                       lambda route:route.fulfill(status=200,content_type='application/json',
                       body=json.dumps({'slots':['2030-01-07T10:30:00-04:00'],
                           'quote':'q'*55,'price_cents':4000,'price_mode':'fixed',
                           'duration_min':40,'services':['Corte masculino'],
                           'timezone':'America/Campo_Grande'})))
            def post_booking(route):
                posted.append(route.request.post_data_json)
                route.fulfill(status=201,content_type='application/json',
                    body=json.dumps({'status':'confirmed','protocol':'SINTETICO-1',
                       'start_at':'2030-01-07T10:30:00-04:00',
                       'end_at':'2030-01-07T11:10:00-04:00',
                       'price_cents':4000,'professional':'Guilherme',
                       'services':['Corte masculino'],
                       'timezone':'America/Campo_Grande'}))
            page.route('**/api/v1/public/sites/empresa-sintetica/appointments',post_booking)
            page.goto(URL,wait_until='domcontentloaded')
            page.add_style_tag(content=CSS)
            page.add_script_tag(content=THEME)
            page.add_script_tag(content=JS)
            page.locator('#site-content').wait_for(state='visible')
            page.locator('#hero-book').click()
            page.locator('#booking-service-list .service-card').click()
            page.locator('#booking-next').click()
            page.locator('#time-slots .slot').first.wait_for()
            page.locator('#time-slots .slot').first.click()
            page.locator('#booking-next').click()
            form=page.locator('#customer-form');form.wait_for()
            assert page.locator('.review-card').count()==1
            assert page.locator('.booking-confirmation-body>.booking-contact-heading').count()==1
            assert page.locator('.consent-label').count()==0
            assert page.locator('.privacy-note').count()==0
            assert page.locator('[name=confirmed]').count()==0
            assert page.locator('#booking-total').is_hidden()
            assert page.locator('.review-row.total').count()==1
            assert page.locator('.review-row.total').inner_text().count('40,00')==1
            assert page.locator('.booking-footer-confirm').count()==1
            fields=form.locator('input')
            assert fields.count()==3

            phone=form.locator('[name=customer_phone]')
            phone.fill('86972676282')
            assert phone.input_value()=='(86) 97267-6282'
            form.locator('[name=customer_name]').fill('Cliente sintético')
            birth=form.locator('[name=customer_birth_date]')
            assert birth.get_attribute('type')=='date'
            assert birth.get_attribute('max')=='2030-01-07'
            birth.fill('1990-07-06')

            geometry=page.evaluate("""() => {
              const dialog=document.querySelector('#booking-dialog').getBoundingClientRect();
              const form=document.querySelector('#customer-form').getBoundingClientRect();
              const footer=document.querySelector('.booking-footer').getBoundingClientRect();
              const button=document.querySelector('#booking-next').getBoundingClientRect();
              const card=document.querySelector('.review-card').getBoundingClientRect();
              return {doc:document.documentElement.scrollWidth,screen:innerWidth,
                dialogRight:dialog.right,formLeft:form.left,formRight:form.right,
                footerRight:footer.right,buttonRight:button.right,
                cardLeft:card.left,cardRight:card.right};
            }""")
            assert geometry['doc']<=width+2,geometry
            for key in ('dialogRight','formRight','footerRight','buttonRight','cardRight'):
                assert geometry[key]<=width+2,geometry
            assert geometry['formLeft']>=-2 and geometry['cardLeft']>=-2,geometry

            page.locator('#booking-next').click()
            page.locator('.success').wait_for()
            assert len(posted)==1
            assert posted[0]['customer_birth_date']=='1990-07-06'
            assert posted[0]['customer_phone']=='(86) 97267-6282'
            assert posted[0]['confirmed'] is True
            assert not errors,errors
        finally:
            browser.close()
