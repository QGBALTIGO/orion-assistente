"""Regression for the empty area BELOW the public booking action bar.

Use the real no-store template/assets and synthetic API data. Check actual
coordinates, not just visibility. A reduced viewport simulates the layout space
available around browser chrome/keyboard; it is not a physical-keyboard test.
"""
import copy
import json
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import sync_playwright

from browser_public_confirmation_clean import DATA, HTML, CSS, JS, THEME, ROOT, URL

ARTIFACTS = ROOT / 'test-artifacts' / 'confirmation-sizing' / 'footer'
FONTS = {path.name: path for path in (ROOT / 'app').rglob('*.woff2')}
CASES = [(320, 568, 'dark'), (375, 812, 'light'), (390, 844, 'dark'),
         (430, 932, 'light'), (390, 1100, 'dark'),
         (768, 1024, 'light'), (1024, 768, 'dark')]


@pytest.mark.parametrize('browser_name', ['chromium', 'webkit'])
@pytest.mark.parametrize('width,height,theme', CASES)
def test_actions_end_at_dialog_bottom(browser_name, width, height, theme):
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    shop = copy.deepcopy(DATA)
    shop['site']['theme_color'] = '#ffca00'
    shop['site']['title'] = 'Barbearia de teste'
    with sync_playwright() as playwright:
        browser = getattr(playwright, browser_name).launch(headless=True)
        page = browser.new_page(viewport={'width': width, 'height': height},
            device_scale_factor=2, locale='pt-BR', color_scheme=theme,
            has_touch=width <= 430,
            is_mobile=browser_name == 'webkit' and width <= 430)
        page.set_default_timeout(10000)
        errors, posts, measurements = [], [], []
        page.on('pageerror', lambda error: errors.append(str(error)))
        stem = f'{browser_name}-{width}x{height}-{theme}'
        try:
            page.route('**/*', lambda route: route.abort())
            page.route(URL, lambda route: route.fulfill(status=200,
                content_type='text/html; charset=utf-8', body=HTML))
            assets = {'orion-public.css': ('text/css', CSS),
                      'orion-public.js': ('text/javascript', JS),
                      'orion-public-theme.js': ('text/javascript', THEME)}
            def asset(route):
                name = urlsplit(route.request.url).path.rsplit('/', 1)[-1]
                mime, body = assets[name]
                route.fulfill(status=200, content_type=mime, body=body)
            page.route('**/public-assets/*', asset)
            def font(route):
                name = urlsplit(route.request.url).path.rsplit('/', 1)[-1]
                if name in FONTS:
                    route.fulfill(status=200, content_type='font/woff2',
                                  body=FONTS[name].read_bytes())
                else:
                    route.abort()
            page.route('**/brand/*', font)
            page.route('**/api/v1/public/sites/empresa-sintetica',
                lambda route: route.fulfill(status=200,
                    content_type='application/json', body=json.dumps(shop)))
            page.route('**/api/v1/public/sites/empresa-sintetica/availability?*',
                lambda route: route.fulfill(status=200, content_type='application/json',
                    body=json.dumps({'slots': ['2030-01-07T11:30:00-04:00'],
                        'quote': 'q' * 55, 'price_cents': 4000, 'price_mode': 'fixed',
                        'duration_min': 40, 'services': ['Corte masculino'],
                        'timezone': 'America/Campo_Grande'})))
            def confirm(route):
                posts.append(route.request.post_data_json)
                route.fulfill(status=201, content_type='application/json', body=json.dumps({
                    'status': 'confirmed', 'protocol': 'SYNTHETIC-ONLY',
                    'start_at': '2030-01-07T11:30:00-04:00',
                    'end_at': '2030-01-07T12:10:00-04:00', 'price_cents': 4000,
                    'professional': 'Guilherme', 'services': ['Corte masculino'],
                    'timezone': 'America/Campo_Grande'}))
            page.route('**/api/v1/public/sites/empresa-sintetica/appointments', confirm)
            page.goto(URL, wait_until='domcontentloaded')
            page.locator('#hero-book').click()
            page.evaluate('document.fonts.ready')

            def measure(label):
                value = page.evaluate("""() => {
                  const d = document.querySelector('#booking-dialog');
                  const f = d.querySelector('.booking-footer');
                  const r = d.getBoundingClientRect(), fr = f.getBoundingClientRect();
                  const next = document.querySelector('#booking-next').getBoundingClientRect();
                  const back = document.querySelector('#booking-back').getBoundingClientRect();
                  const bottom = r.top + d.clientTop + d.clientHeight;
                  return {viewport: {width:innerWidth,height:innerHeight},
                    open:d.open, gap:bottom-fr.bottom,
                    dialog:{top:r.top,bottom:r.bottom,scroll:d.scrollHeight,client:d.clientHeight},
                    footer:{top:fr.top,bottom:fr.bottom,left:fr.left,right:fr.right},
                    next:{height:next.height,width:next.width},back:{height:back.height},
                    scrollWidth:d.scrollWidth,clientWidth:d.clientWidth,
                    confirmation:f.classList.contains('booking-footer-confirm')};
                }""")
                value['state'] = label
                measurements.append(value)
                assert value['open']
                assert abs(value['gap']) <= 2, (label, value)
                assert value['footer']['bottom'] <= value['viewport']['height'] + 2, value
                assert value['footer']['top'] >= -1, value
                assert value['footer']['left'] >= -1, value
                assert value['footer']['right'] <= width + 1, value
                assert value['scrollWidth'] <= value['clientWidth'] + 1, value
                if value['confirmation']:
                    assert abs(value['next']['height'] - 44) <= 1, value
                    assert abs(value['back']['height'] - 44) <= 1, value
                    assert value['next']['width'] <= 225, value
                return value

            measure('services-short-content')
            page.locator('#booking-service-list .service-card').first.click()
            page.locator('#booking-next').click()
            page.locator('#time-slots .slot').first.wait_for()
            measure('time-selection')
            page.locator('#time-slots .slot').first.click()
            page.locator('#booking-next').click()
            page.locator('#customer-form').wait_for()
            page.locator('#booking-dialog').evaluate('(d)=>d.scrollTop=0')
            measure('confirmation-empty-top')
            page.screenshot(path=str(ARTIFACTS / f'{stem}.png'))

            # Last field must remain reachable above the sticky bar.
            def last_field_reachable():
                page.locator('#booking-dialog').evaluate('(d)=>d.scrollTop=d.scrollHeight')
                page.wait_for_timeout(50)
                measure('confirmation-scrolled-to-last-field')
                assert page.evaluate("""() => {
                  const b=document.querySelector('[name=customer_birth_date]').getBoundingClientRect();
                  const f=document.querySelector('.booking-footer').getBoundingClientRect();
                  return b.top>=-1 && b.bottom<=f.top+1;
                }"""), measurements[-1]
            last_field_reachable()

            # Long names and service descriptions must scroll, not shrink inputs
            # or let the action bar fall beneath the viewport.
            page.locator('.review-card .review-row strong').first.evaluate(
                "e=>{e.dataset.original=e.textContent;e.textContent='Corte, barba, hidratação e tratamento personalizado — '.repeat(12)}")
            page.locator('#booking-dialog').evaluate('(d)=>d.scrollTop=0')
            measure('long-summary-top')
            page.locator('#booking-dialog').evaluate('(d)=>d.scrollTop=d.scrollHeight/2')
            measure('long-summary-middle')
            last_field_reachable()
            page.locator('.review-card .review-row strong').first.evaluate(
                'e=>e.textContent=e.dataset.original')

            # Browser UI / virtual-keyboard-sized layout viewport changes.
            if width <= 430:
                page.set_viewport_size({'width': width, 'height': 420})
                page.locator('[name=customer_phone]').focus()
                last_field_reachable()
                page.set_viewport_size({'width': width, 'height': height})
                page.locator('[name=customer_phone]').blur()
                page.locator('#booking-dialog').evaluate('(d)=>d.scrollTop=0')
                measure('restored-viewport')

            # Existing validation errors also take their normal place ABOVE actions.
            page.locator('#booking-error').evaluate(
                "e=>{e.hidden=false;e.textContent='Exemplo de erro de validação. Confira os dados e tente novamente.'}")
            page.locator('#booking-dialog').evaluate('(d)=>d.scrollTop=d.scrollHeight')
            measure('visible-error')
            assert page.evaluate("""() => document.querySelector('#booking-error').getBoundingClientRect().bottom
                <=document.querySelector('.booking-footer').getBoundingClientRect().top+1""")
            page.locator('#booking-error').evaluate('e=>{e.hidden=true;e.textContent=""}')
            page.locator('#booking-back').click()
            measure('back-to-times')
            page.locator('#booking-next').click()
            page.locator('[name=customer_name]').fill('Cliente de teste')
            page.locator('[name=customer_phone]').fill('86972676282')
            page.locator('[name=customer_birth_date]').fill('1990-07-06')
            last_field_reachable()
            page.locator('#booking-next').click()
            page.locator('.success').wait_for()
            measure('success')
            assert len(posts) == 1
            assert posts[0]['customer_birth_date'] == '1990-07-06'
            assert posts[0]['customer_phone'] == '(86) 97267-6282'
            page.locator('#booking-next').click()
            assert not page.locator('#booking-dialog').is_visible()
            assert not errors, errors
        finally:
            (ARTIFACTS / f'{stem}.json').write_text(
                json.dumps({'measurements': measurements, 'errors': errors}, indent=2),
                encoding='utf-8')
            browser.close()
