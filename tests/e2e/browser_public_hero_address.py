"""Verify intentional address lines in public hero at phone and desktop widths."""
import json
from pathlib import Path
import pytest
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[2]
HTML=(ROOT/'app/static/orion-public.html').read_text(encoding='utf-8')
CSS=(ROOT/'app/static/orion-public.css').read_text(encoding='utf-8')
JS=(ROOT/'app/static/orion-public.js').read_text(encoding='utf-8')
FIELDS_JS=(ROOT/'app/static/orion-fields.js').read_text(encoding='utf-8')
FIELDS_CSS=(ROOT/'app/static/orion-fields.css').read_text(encoding='utf-8')
URL='https://orion.test/s/barbearia-teste'

@pytest.mark.parametrize('engine',['chromium','webkit'])
@pytest.mark.parametrize('width',[320,375,390,640,768,1024])
def test_hero_address_is_readable_and_separated(engine,width):
    site={
       'title':'Barbearia Peixoto','theme_color':'#fdc700',
       'address':'Rua Canuto de Quadros','address_number':'122',
       'neighborhood':'São Judas Tadeu',
       'address_display':'Rua Canuto de Quadros, 122 — São Judas Tadeu',
       'city':'Montes Claros','state':'MG','postal_code':'39402-366',
       'show_prices':True,
    }
    data={'timezone':'America/Sao_Paulo','today':'2030-01-07',
          'booking_horizon_days':60,'site':site,'opening_hours':[],
          'services':[],'professionals':[]}
    with sync_playwright() as p:
        browser=getattr(p,engine).launch()
        page=browser.new_page(viewport={'width':width,'height':820},device_scale_factor=2,
            locale='pt-BR',is_mobile=(engine=='webkit' and width<=390),has_touch=True)
        try:
            page.route('**/*',lambda route:route.abort())
            page.route(URL,lambda route:route.fulfill(status=200,content_type='text/html',body=HTML))
            page.route('**/api/v1/public/sites/barbearia-teste',
                lambda route:route.fulfill(status=200,content_type='application/json',
                    body=json.dumps(data)))
            page.goto(URL,wait_until='domcontentloaded')
            page.add_style_tag(content=CSS)
            page.add_style_tag(content=FIELDS_CSS)
            page.add_script_tag(content=FIELDS_JS)
            page.add_script_tag(content=JS)
            page.locator('#site-content').wait_for(state='visible',timeout=10000)
            root=page.locator('#hero-address')
            street=page.locator('#hero-address-street')
            locality=page.locator('#hero-address-locality')
            logo=page.locator('#site-logo')
            status=page.locator('.identity-text .online-label')
            assert root.is_visible()
            assert street.inner_text()=='Rua Canuto de Quadros, 122'
            assert locality.inner_text()=='Montes Claros · MG'
            sb=street.bounding_box()
            lb=locality.bounding_box()
            sign=status.bounding_box()
            logo_box=logo.bounding_box()
            root_box=root.bounding_box()
            assert all((sb,lb,sign,logo_box,root_box))
            assert sb['y']+sb['height']<=lb['y']+2,'municipality below street'
            assert lb['y']+lb['height']<=sign['y']+3,'status beneath city'
            assert root_box['x']>=logo_box['x']+logo_box['width']+7,'street not overlapping logo'
            assert root_box['x']+root_box['width']<=width+2
            assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth+2')
            assert 'São Judas Tadeu' not in root.inner_text()
            assert 'São Judas Tadeu' in page.locator('#full-address').inner_text()
            assert '39402-366' in page.locator('#full-address').inner_text()
            assert page.locator('#hero-book').is_visible()
            assert page.locator('.online-label').is_visible()
        finally:
            browser.close()
