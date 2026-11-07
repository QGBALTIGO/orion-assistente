"""Real Chromium/WebKit checks for consistently sized public contact cards."""
import json
from pathlib import Path
import pytest
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[2]
HTML=(ROOT/'app/static/orion-public.html').read_text(encoding='utf-8')
CSS=(ROOT/'app/static/orion-public.css').read_text(encoding='utf-8')
JS=(ROOT/'app/static/orion-public.js').read_text(encoding='utf-8')
THEME=(ROOT/'app/static/orion-public-theme.js').read_text(encoding='utf-8')
FIELDS_JS=(ROOT/'app/static/orion-fields.js').read_text(encoding='utf-8')
FIELDS_CSS=(ROOT/'app/static/orion-fields.css').read_text(encoding='utf-8')
URL='https://orion.test/s/empresa-sintetica'
OPTIONS=[
    ('whatsapp_phone','+5511999999999'),
    ('contact_phone','+551133334444'),
    ('instagram_url','https://instagram.com/orion'),
    ('tiktok_url','https://tiktok.com/@orion'),
    ('facebook_url','https://facebook.com/orion'),
    ('external_url','https://example.com')
]
LABELS=['WhatsApp','Ligar','Instagram','TikTok','Facebook','Site']

@pytest.mark.parametrize('engine',['chromium','webkit'])
@pytest.mark.parametrize('width',[320,390,896])
@pytest.mark.parametrize('active',[0,1,2,3,4,5,6])
def test_contact_actions_fill_card_without_orphans(engine,width,active):
    site={'title':'Barbearia teste','theme_color':'#fdc700','show_prices':True}
    site.update(dict(OPTIONS[:active]))
    data={'timezone':'America/Sao_Paulo','today':'2030-01-07',
      'booking_horizon_days':60,'site':site,'opening_hours':[],
      'services':[],'professionals':[]}
    with sync_playwright() as p:
        browser=getattr(p,engine).launch()
        page=browser.new_page(viewport={'width':width,'height':760},device_scale_factor=2,
            locale='pt-BR',has_touch=True,is_mobile=(engine=='webkit' and width<640))
        try:
            page.route('**/*',lambda r:r.abort())
            page.route(URL,lambda r:r.fulfill(status=200,content_type='text/html',body=HTML))
            page.route('**/api/v1/public/sites/empresa-sintetica',lambda r:
              r.fulfill(status=200,content_type='application/json',body=json.dumps(data)))
            page.goto(URL,wait_until='domcontentloaded')
            page.add_style_tag(content=CSS)
            page.add_style_tag(content=FIELDS_CSS)
            page.add_script_tag(content=THEME)
            page.add_script_tag(content=FIELDS_JS)
            page.add_script_tag(content=JS)
            page.locator('#site-content').wait_for(state='visible')
            section=page.locator('#contact-section')
            links=page.locator('#contact-links')
            tiles=page.locator('#contact-links .social-link')
            assert tiles.count()==active
            assert links.get_attribute('data-count')==str(active)
            if not active:
                assert not section.is_visible()
            else:
                assert section.is_visible()
                assert links.evaluate('(el)=>getComputedStyle(el).justifyContent')=='center'
                assert [tiles.nth(i).locator('.social-link-label').inner_text()
                  for i in range(active)]==LABELS[:active]
                bounds=[tiles.nth(i).bounding_box() for i in range(active)]
                assert all(bounds)
                assert max(b['height'] for b in bounds)-min(b['height'] for b in bounds)<=2
                assert max(b['width'] for b in bounds)-min(b['width'] for b in bounds)<=2
                assert all(b['height']>=70 and b['width']>=55 for b in bounds)
                assert all(tiles.nth(i).locator('svg').count()==1 for i in range(active))
                assert all(tiles.nth(i).get_attribute('aria-label') for i in range(active))
                assert all(tiles.nth(i).locator('.social-link-label').evaluate(
                  '(n)=>n.scrollWidth<=n.clientWidth+2') for i in range(active)), 'All channel labels visible'
                area=links.bounding_box()
                assert area
                assert all(b['x']>=area['x']-2 and
                    b['x']+b['width']<=area['x']+area['width']+2 for b in bounds)
                rows={}
                for b in bounds:rows.setdefault(round(b['y']),[]).append(b)
                expected_rows=(
                  1 if active<=3 else
                  2 if width<740 or active>=5 else 1
                )
                assert len(rows)==expected_rows,(active,width,rows)
                for row in rows.values():
                    first,last=row[0],row[-1]
                    center=(first['x']+last['x']+last['width'])/2
                    ideal=area['x']+area['width']/2
                    assert abs(center-ideal)<=4,('Row centered',active,width,center,ideal)
                for i in range(active):
                    tile=tiles.nth(i)
                    assert tile.evaluate('(el)=>getComputedStyle(el).textAlign')=='center'
                assert page.locator('#contact-title').inner_text()=='Fale com a gente'
                assert page.locator('#contact-section').evaluate('(el)=>el.scrollWidth<=el.clientWidth+2')
            assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth+2')
        finally:
            browser.close()
