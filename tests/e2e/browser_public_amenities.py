"""Mobile and desktop browser regression for amenities inside Onde estamos."""
import json
from pathlib import Path
import pytest
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[2]
FIELDS_CSS=(ROOT/'app/static/orion-fields.css').read_text(encoding='utf-8')
FIELDS_JS=(ROOT/'app/static/orion-fields.js').read_text(encoding='utf-8')
HTML=(ROOT/'app/static/orion-public.html').read_text(encoding='utf-8')
CSS=(ROOT/'app/static/orion-public.css').read_text(encoding='utf-8')
JS=(ROOT/'app/static/orion-public.js').read_text(encoding='utf-8')
THEME=(ROOT/'app/static/orion-public-theme.js').read_text(encoding='utf-8')
URL='https://orion.test/s/empresa-sintetica'
FIELDS=['amenity_wifi','amenity_parking','amenity_accessibility','amenity_children']
LABELS=['Wi-Fi','Estacionamento','Acessibilidade','Atende crianças']


@pytest.mark.parametrize('engine',['chromium','webkit'])
@pytest.mark.parametrize('width',[320,390,768,1024])
@pytest.mark.parametrize('active',[0,1,2,3,4])
def test_public_amenities_inside_location_with_accessible_labels(engine,width,active):
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
            page.add_style_tag(content=FIELDS_CSS)
            page.add_script_tag(content=THEME)
            page.add_script_tag(content=FIELDS_JS)
            page.add_script_tag(content=JS)
            page.locator('#site-content').wait_for(state='visible')
            card=page.locator('#address-card')
            section=page.locator('#amenities-section')
            grid=page.locator('#amenities-grid')
            tiles=page.locator('#amenities-grid .amenity-tile')
            assert page.locator('.public-main').evaluate('(el)=>el.firstElementChild.id')=='servicos'
            assert page.locator('.public-main > #amenities-section').count()==0
            assert card.locator('#amenities-section').count()==1
            assert tiles.count()==active
            assert grid.get_attribute('data-count')==str(active)
            if not active:
                assert not section.is_visible()
                assert not card.is_visible(), 'No address/map/amenities must hide the location card'
            else:
                assert card.is_visible()
                assert section.is_visible()
                assert page.locator('#amenities-title').inner_text()=='No local'
                assert page.locator('#location-subtitle').inner_text()=='Confira as comodidades disponíveis.'
                assert not page.locator('#map-preview').is_visible()
                assert not page.locator('#location-details').is_visible()
                assert grid.evaluate('(el)=>getComputedStyle(el).display')=='grid'
                assert section.evaluate('(el)=>el.scrollWidth<=el.clientWidth+2')
                assert [tiles.nth(i).locator('.amenity-label').inner_text() for i in range(active)] == LABELS[:active]
                for i in range(active):
                    tile=tiles.nth(i)
                    assert tile.get_attribute('aria-expanded')=='false'
                    assert 'disponível' in tile.get_attribute('aria-label')
                    box=tile.bounding_box()
                    assert box and box['width']>=90 and box['height']>=45
                    icon=tile.locator('svg')
                    assert icon.count()==1
                    icon_box=icon.bounding_box()
                    assert icon_box and 15<=icon_box['width']<=19
                    assert 15<=icon_box['height']<=19
                    section_box=section.bounding_box()
                    assert section_box and box['x']>=section_box['x']-1
                    assert box['x']+box['width']<=section_box['x']+section_box['width']+1
                # All rendered tiles have an identical width/height, regardless
                # of title length; there must be no orphan aligned left.
                boxes=[tiles.nth(i).bounding_box() for i in range(active)]
                assert max(box['width'] for box in boxes)-min(box['width'] for box in boxes)<=2
                assert max(box['height'] for box in boxes)-min(box['height'] for box in boxes)<=2
                assert grid.evaluate('(el)=>getComputedStyle(el).gridTemplateColumns.count(" ")')==(
                    0 if width>=720 and active==1 else (active-1 if width>=720 else 1))
                area=grid.bounding_box()
                assert area
                center=area['x']+area['width']/2
                if width<720:
                    if active==1 or active==3:
                        last=boxes[-1]
                        assert abs(last['x']+last['width']/2-center)<=2, 'single item on final row centered'
                    if active>=2:
                        assert abs(boxes[0]['y']-boxes[1]['y'])<=2, 'first row has even baseline'
                    if active>=3:
                        assert boxes[2]['y']>=boxes[0]['y']+boxes[0]['height']+6
                    if active==4:
                        assert abs(boxes[2]['y']-boxes[3]['y'])<=2
                else:
                    assert all(abs(box['y']-boxes[0]['y'])<=2 for box in boxes), 'desktop uses one aligned row'
                    assert boxes[-1]['x']+boxes[-1]['width']<=area['x']+area['width']+2
                if active>=3:
                    wheel=tiles.nth(2).locator('svg circle').last
                    assert wheel.count()==1
                    dimensions=wheel.evaluate("""node=>{
                        const svg=node.ownerSVGElement,box=node.getBBox(),view=svg.viewBox.baseVal;
                        return {left:box.x,top:box.y,right:box.x+box.width,
                                bottom:box.y+box.height,width:view.width,height:view.height};
                    }""")
                    assert dimensions['left']>=1 and dimensions['top']>=1
                    assert dimensions['right']<=dimensions['width']-1
                    assert dimensions['bottom']<=dimensions['height']-1
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
