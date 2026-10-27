"""Regression for the disproportionate iOS birthday field and stretched CTA.

Exercises the complete public template with its real assets, fonts, themes and
JavaScript. All catalogue/booking traffic is mocked; no real booking is created.
"""
import copy
import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import sync_playwright

from browser_public_confirmation_clean import DATA, HTML, CSS, JS, THEME, ROOT, URL

ARTIFACTS = ROOT / 'test-artifacts' / 'confirmation-sizing'
FONTS = {path.name: path for path in (ROOT / 'app').rglob('*.woff2')}
CASES = [(320, 760, 'dark'), (375, 812, 'light'), (390, 844, 'dark'),
         (430, 932, 'light'), (768, 1024, 'dark'), (1024, 768, 'light')]


@pytest.mark.parametrize('browser_name', ['chromium', 'webkit'])
@pytest.mark.parametrize('width,height,theme', CASES)
def test_native_fields_and_actions_have_one_scale(browser_name, width, height, theme):
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    shop = copy.deepcopy(DATA)
    shop['site']['theme_color'] = '#ffca00'
    shop['site']['title'] = 'Barbearia de teste'
    with sync_playwright() as playwright:
        browser = getattr(playwright, browser_name).launch(headless=True)
        page = browser.new_page(viewport={'width': width, 'height': height},
            device_scale_factor=2, locale='pt-BR', color_scheme=theme,
            has_touch=width <= 430, is_mobile=browser_name == 'webkit' and width <= 430)
        page.set_default_timeout(8000)
        errors, posts, metrics = [], [], []
        page.on('pageerror', lambda error: errors.append(str(error)))
        stem = f'{browser_name}-{width}-{theme}'
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
                lambda route: route.fulfill(status=200, content_type='application/json',
                                            body=json.dumps(shop)))
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
            page.locator('#booking-service-list .service-card').first.click()
            page.locator('#booking-next').click()
            page.locator('#time-slots .slot').first.click()
            page.locator('#booking-next').click()
            page.locator('#customer-form').wait_for()
            page.evaluate('document.fonts.ready')

            def measure(state):
                result = page.evaluate("""() => {
                  const box = el => {
                    const r = el.getBoundingClientRect(), c = getComputedStyle(el);
                    return {x:r.left, right:r.right, y:r.top, bottom:r.bottom,
                      width:r.width, height:r.height, font:c.fontSize,
                      radius:c.borderTopLeftRadius, text:el.textContent,
                      scroll:el.scrollWidth, client:el.clientWidth};
                  };
                  return {viewport:innerWidth, document:document.documentElement.scrollWidth,
                    dialog:box(document.querySelector('#booking-dialog')),
                    inputs:[...document.querySelectorAll('#customer-form input')].map(box),
                    back:box(document.querySelector('#booking-back')),
                    next:box(document.querySelector('#booking-next')),
                    footer:box(document.querySelector('.booking-footer'))};
                }""")
                result['state'] = state
                metrics.append(result)
                assert len(result['inputs']) == 3, result
                for control in result['inputs'] + [result['back'], result['next']]:
                    assert abs(control['height'] - 44) <= 1, (state, control)
                    assert control['x'] >= -1 and control['right'] <= width + 1, control
                    assert control['scroll'] <= control['client'] + 1, control
                    assert control['radius'] == '10px', control
                assert max(x['height'] for x in result['inputs']) - min(
                    x['height'] for x in result['inputs']) <= 1, result
                assert all(x['font'] == '16px' for x in result['inputs']), result
                assert result['next']['width'] <= 225, result
                assert abs(result['next']['y'] - result['back']['y']) <= 1, result
                assert result['next']['x'] >= result['back']['right'] + 6, result
                assert result['document'] <= width + 1, result
                return result

            birth = page.locator('#customer_birth_date')
            assert birth.input_value() == '', 'Do not silently fill today as a birthday'
            measure('empty')
            birth.fill('1990-07-06')
            measure('filled')
            birth.fill('')
            measure('cleared')
            birth.fill('2000-02-29')
            measure('leap-day')
            page.locator('#customer_name').fill('Cliente de teste')
            page.locator('#customer_phone').fill('86972676282')
            assert page.locator('#customer_phone').input_value() == '(86) 97267-6282'
            assert page.locator('#booking-total').is_hidden()
            page.evaluate("document.querySelector('#booking-dialog').scrollTop=0")
            page.screenshot(path=str(ARTIFACTS / f'{stem}.png'), full_page=True)
            page.locator('#customer-form').screenshot(path=str(ARTIFACTS / f'{stem}-fields.png'))
            page.locator('.booking-footer').screenshot(path=str(ARTIFACTS / f'{stem}-actions.png'))
            page.locator('#booking-next').click()
            page.locator('.success').wait_for()
            assert len(posts) == 1
            assert posts[0]['customer_birth_date'] == '2000-02-29'
            assert posts[0]['customer_phone'] == '(86) 97267-6282'
            assert posts[0]['confirmed'] is True
            assert not errors, errors
        finally:
            (ARTIFACTS / f'{stem}.json').write_text(json.dumps(metrics, indent=2), encoding='utf-8')
            if not page.is_closed():
                page.screenshot(path=str(ARTIFACTS / f'{stem}-final.png'), full_page=True)
            browser.close()
