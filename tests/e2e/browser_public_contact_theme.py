"""Mobile and desktop regression checks for public contact icon layout and theme choices."""
import json
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
HTML=(ROOT/'app/static/orion-public.html').read_text(encoding='utf-8')
CSS=(ROOT/'app/static/orion-public.css').read_text(encoding='utf-8')
SCRIPT=(ROOT/'app/static/orion-public.js').read_text(encoding='utf-8')
THEME=(ROOT/'app/static/orion-public-theme.js').read_text(encoding='utf-8')
URL='https://orion.test/s/empresa-sintetica'
DATA={
    'timezone':'America/Sao_Paulo',
    'today':'2030-01-07',
    'booking_horizon_days':60,
    'site':{
        'title':'Barbearia de teste','theme_color':'#ffcc00','show_prices':True,
        'whatsapp_phone':'+5511999999999','contact_phone':'+551133334444',
        'instagram_url':'https://instagram.com/orion',
        'tiktok_url':'https://tiktok.com/@orion',
        'facebook_url':'https://facebook.com/orion'
    },
    'opening_hours':[],
    'services':[],
    'professionals':[]
}

@pytest.mark.parametrize('engine',['chromium','webkit'])
@pytest.mark.parametrize('width',[320,390,768,1024])
def test_public_contacts_and_theme_responsive(engine,width):
    with sync_playwright() as p:
        browser=getattr(p,engine).launch()
        page=browser.new_page(viewport={'width':width,'height':800},
            device_scale_factor=2,locale='pt-BR',
            is_mobile=(engine=='webkit' and width<=390),has_touch=True)
        try:
            page.route('**/*',lambda route:route.abort())
            page.route(URL,lambda route:route.fulfill(
                status=200,content_type='text/html',body=HTML))
            page.route('**/api/v1/public/sites/empresa-sintetica',
                lambda route:route.fulfill(status=200,
                    content_type='application/json',body=json.dumps(DATA)))
            page.goto(URL,wait_until='domcontentloaded')
            page.add_style_tag(content=CSS)
            page.add_script_tag(content=THEME)
            page.add_script_tag(content=SCRIPT)
            page.locator('#contact-section').wait_for(state='visible')
            links=page.locator('#contact-links a')
            assert links.count()==5
            assert page.locator('#contact-links a.social-link svg').count()==5
            assert all(links.nth(i).get_attribute('aria-label') for i in range(5))
            assert links.nth(0).get_attribute('href')=='https://wa.me/5511999999999'
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 2')
            assert page.locator('#contact-section').evaluate('(el)=>el.scrollWidth <= el.clientWidth + 2')
            for i in range(5):
                bounds=links.nth(i).bounding_box()
                assert bounds and bounds['width']>=44 and bounds['height']>=44
            toggle=page.locator('#public-theme-toggle')
            assert toggle.is_visible()
            toggle.click()
            assert toggle.get_attribute('aria-expanded')=='true'
            page.locator('[data-public-theme=dark]').click()
            assert page.evaluate('document.documentElement.dataset.theme')=='dark'
            assert page.evaluate("localStorage.getItem('orion-theme')")=='dark'
            assert page.locator('#contact-section').evaluate(
                '(el)=>getComputedStyle(el).backgroundColor')=='rgb(17, 27, 43)'
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 2')
            toggle.click()
            page.locator('[data-public-theme=light]').click()
            assert page.evaluate('document.documentElement.dataset.theme')=='light'
            toggle.click()
            page.locator('[data-public-theme=auto]').click()
            page.emulate_media(color_scheme='dark')
            page.wait_for_function("document.documentElement.dataset.theme === 'dark'")
            page.emulate_media(color_scheme='light')
            page.wait_for_function("document.documentElement.dataset.theme === 'light'")
        finally:
            browser.close()
