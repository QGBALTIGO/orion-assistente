"""Browser-level accessibility and layout regression for optional public amenities."""
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
FIELDS=['amenity_wifi','amenity_parking','amenity_accessibility','amenity_children']

@pytest.mark.parametrize('engine',['chromium','webkit'])
@pytest.mark.parametrize('width',[320,390,768,1024])
@pytest.mark.parametrize('active',[0,1,2,4])
def test_public_amenities_all_combinations_and_info(engine,width,active):
    site={'title':'Empresa teste','theme_color':'#ffd000','show_prices':True}
    site.update({key:index<active for index,key in enumerate(FIELDS)})
    data={'timezone':'America/Sao_Paulo','today':'2030-01-07',
          'booking_horizon_days':60,'site':site,'opening_hours':[],
          'services':[],'professionals':[]}
    with sync_playwright() as p:
        browser=getattr(p,engine).launch()
        page=browser.new_page(viewport={'width':width,'height':780},
            device_scale_factor=2,locale='pt-BR',has_touch=True,
            is_mobile=(engine=='webkit' and width<=390))
        try:
            page.route('**/*',lambda route:route.abort())
            page.route(URL,lambda route:route.fulfill(
                status=200,content_type='text/html',body=HTML))
            page.route('**/api/v1/public/sites/empresa-sintetica',
                lambda route:route.fulfill(status=200,content_type='application/json',body=json.dumps(data)))
            page.emulate_media(color_scheme='light')
            page.goto(URL,wait_until='domcontentloaded')
            page.add_style_tag(content=CSS)
            page.add_script_tag(content=THEME)
            page.add_script_tag(content=JS)
            section=page.locator('#amenities-section')
            page.locator('#site-content').wait_for(state='visible')
            tiles=page.locator('#amenities-grid .amenity-tile')
            assert tiles.count()==active
            if not active:
                assert not section.is_visible()
                assert page.locator('#servicos').evaluate(
                    '(el)=>getComputedStyle(el).marginTop')=='0px'
            else:
                assert section.is_visible()
                assert page.locator('#amenities-title').inner_text()=='Comodidades'
                assert section.evaluate('(el)=>el.scrollWidth<=el.clientWidth+2')
                for i in range(active):
                    tile=tiles.nth(i)
                    assert tile.get_attribute('aria-expanded')=='false'
                    box=tile.bounding_box()
                    assert box and box['width']>=52 and box['height']>=60
                    assert tile.locator('svg').count()==1
                if width<=390 and active==4:
                    ycoords=[round(tiles.nth(i).bounding_box()['y']) for i in range(4)]
                    assert len(set(ycoords))==1, 'All four fit a single mobile row'
                tiles.nth(active-1).click()
                assert page.locator('#amenity-detail').is_visible()
                assert tiles.nth(active-1).get_attribute('aria-expanded')=='true'
                assert 'Disponível' in page.locator('#amenity-detail').inner_text()
                page.keyboard.press('Escape')
                assert not page.locator('#amenity-detail').is_visible()
                assert tiles.nth(active-1).get_attribute('aria-expanded')=='false'
                tiles.first.click()
                page.locator('#amenity-detail-close').click()
                assert not page.locator('#amenity-detail').is_visible()
                page.emulate_media(color_scheme='dark')
                page.wait_for_function("document.documentElement.dataset.theme === 'dark'")
                assert tiles.first.is_visible()
            assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth+2')
            assert page.locator('#servicos').is_visible()
        finally:
            browser.close()
