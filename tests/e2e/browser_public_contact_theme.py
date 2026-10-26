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
            page.emulate_media(color_scheme='light')
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
            bounds=toggle.bounding_box()
            assert bounds and 44<=bounds['width']<=46 and 44<=bounds['height']<=46
            # Icon only: no surrounding square, border, shadow or translucent backdrop.
            decoration=toggle.evaluate("""el => {
                const style=getComputedStyle(el);
                return {
                    background:style.backgroundColor,
                    border:style.borderTopWidth,
                    shadow:style.boxShadow,
                    backdrop:style.backdropFilter || 'none'
                };
            }""")
            assert decoration == {
                'background':'rgba(0, 0, 0, 0)',
                'border':'0px',
                'shadow':'none',
                'backdrop':'none'
            }
            assert toggle.inner_text().strip()==''
            assert toggle.locator('svg.appearance-symbol').count()==2
            assert page.locator('#public-theme-menu').count()==0
            assert page.evaluate('document.documentElement.dataset.themePreference')=='auto'
            assert page.evaluate('document.documentElement.dataset.theme')=='light'
            assert page.evaluate("localStorage.getItem('orion-public-theme')") is None
            assert toggle.locator('.appearance-sun').is_visible()
            assert not toggle.locator('.appearance-moon').is_visible()
            assert 'Ativar modo escuro' in toggle.get_attribute('aria-label')
            toggle.click()
            assert page.evaluate('document.documentElement.dataset.theme')=='dark'
            assert page.evaluate('document.documentElement.dataset.themePreference')=='dark'
            assert page.evaluate("localStorage.getItem('orion-public-theme')")=='dark'
            assert toggle.locator('.appearance-moon').is_visible()
            assert not toggle.locator('.appearance-sun').is_visible()
            assert page.locator('#contact-section').evaluate(
                '(el)=>getComputedStyle(el).backgroundColor')=='rgb(17, 27, 43)'
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 2')
            toggle.click()
            assert page.evaluate('document.documentElement.dataset.theme')=='light'
            assert page.evaluate('document.documentElement.dataset.themePreference')=='auto'
            assert page.evaluate("localStorage.getItem('orion-public-theme')")=='auto'
            page.emulate_media(color_scheme='dark')
            page.wait_for_function("document.documentElement.dataset.theme === 'dark'")
            toggle.click()
            assert page.evaluate('document.documentElement.dataset.theme')=='light'
            assert page.evaluate('document.documentElement.dataset.themePreference')=='light'
            page.emulate_media(color_scheme='light')
            assert page.evaluate('document.documentElement.dataset.theme')=='light'
            toggle.click()
            assert page.evaluate('document.documentElement.dataset.theme')=='dark'
            toggle.click()
            assert page.evaluate('document.documentElement.dataset.theme')=='light'
            assert page.evaluate('document.documentElement.dataset.themePreference')=='auto'
        finally:
            browser.close()
