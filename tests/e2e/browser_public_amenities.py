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
@pytest.mark.parametrize('active',[0,1,2,3,4])
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
            grid=page.locator('#amenities-grid')
            tiles=page.locator('#amenities-grid .amenity-tile')
            assert tiles.count()==active
            assert grid.get_attribute('data-count')==str(active)
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
                    # Compact square hit targets remain stable for all 1–4 options.
                    expected=66 if width>=640 else (58 if width<360 else 62)
                    assert box and expected-0.5<=box['width']<=expected+0.5
                    assert expected-0.5<=box['height']<=expected+0.5
                    icon_box=tile.locator('svg').bounding_box()
                    icon_size=24 if width>=640 else (21 if width<360 else 23)
                    assert icon_box and icon_size-0.5<=icon_box['width']<=icon_size+0.5
                    assert icon_size-0.5<=icon_box['height']<=icon_size+0.5
                    assert tile.locator('svg').count()==1
                # The tiles remain a single row on phones and desktops, and
                # only groups of 3 or 4 are centered inside the site container.
                boxes=[tiles.nth(i).bounding_box() for i in range(active)]
                ycoords=[round(box['y']) for box in boxes]
                assert len(set(ycoords))==1, 'No wrapped or uneven icon rows'
                if active>1:
                    expected_gap=16 if width>=640 else (12 if width<360 else 14)
                    horizontal_gaps=[boxes[i+1]['x']-(boxes[i]['x']+boxes[i]['width'])
                                     for i in range(active-1)]
                    assert all(abs(gap-expected_gap)<=1.5 for gap in horizontal_gaps), (
                        'Even, comfortable spacing between active amenities', horizontal_gaps)
                    assert boxes[-1]['x']+boxes[-1]['width'] <= section.bounding_box()['x']+section.bounding_box()['width']+1
                area=grid.bounding_box()
                assert area
                group_center=(boxes[0]['x'] + boxes[-1]['x'] + boxes[-1]['width'])/2
                if active>=3:
                    grid_center=area['x']+area['width']/2
                    assert abs(group_center-grid_center)<=1.5, '3/4 amenities centered horizontally'
                    assert grid.evaluate('(el)=>getComputedStyle(el).justifyContent')=='center'
                    page.locator('#amenity-detail').evaluate(
                        '(el)=>getComputedStyle(el).marginLeft')
                else:
                    assert abs(boxes[0]['x']-area['x'])<=1.5, '1/2 amenities stay left-aligned'
                if active>=3:
                    # Fully closed wheel remains inside the SVG viewport,
                    # rather than an amputated arc touching the edge.
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
