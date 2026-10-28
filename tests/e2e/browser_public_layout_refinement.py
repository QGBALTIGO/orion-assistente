"""Public booking layout regressions from the owner's mobile screenshots.

Only a loopback fixture is used: no database, external traffic or accepted POSTs.
"""
import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from scripts.public_audit_preview import Handler

ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = ROOT / 'test-artifacts' / 'public-layout-refinement'
SIZES = [(1440, 900), (1366, 768), (768, 1024), (1024, 768), (390, 844), (360, 800)]


@pytest.fixture(scope='module')
def fixture_url():
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}/s/empresa-sintetica'
    server.shutdown()
    server.server_close()
    thread.join()


@pytest.mark.parametrize('engine', ['chromium', 'webkit'])
@pytest.mark.parametrize('width,height', SIZES)
@pytest.mark.parametrize('theme', ['light', 'dark'])
def test_conflict_has_balanced_space_and_stays_visible(fixture_url, engine, width, height, theme):
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = getattr(playwright, engine).launch()
        page = browser.new_page(viewport={'width': width, 'height': height},
            color_scheme=theme, locale='pt-BR', has_touch=width <= 430,
            is_mobile=engine == 'webkit' and width <= 430)
        try:
            page.goto(fixture_url)
            page.locator('#hero-book').click()
            page.evaluate('document.fonts.ready')
            page.get_by_role('dialog').get_by_role('button', name='Corte Masculino', exact=False).click()
            before_error=page.locator('#booking-dialog').evaluate('(d)=>d.scrollTop')
            page.get_by_role('dialog').get_by_role('button', name='COMBO EM DESTAQUE', exact=False).click()
            page.evaluate('document.fonts.ready')
            metrics = page.evaluate('''() => {
              const alert=document.querySelector('#booking-error').getBoundingClientRect();
              const steps=document.querySelector('#booking-steps').getBoundingClientRect();
              const intro=document.querySelector('.booking-selection-intro').getBoundingClientRect();
              const footer=document.querySelector('.booking-footer').getBoundingClientRect();
              return {topGap:alert.top-steps.bottom,bottomGap:intro.top-alert.bottom,
                aligned:Math.abs(alert.left-intro.left)<1,aboveFooter:alert.bottom<footer.top,
                overflow:document.documentElement.scrollWidth>innerWidth,
                scrollTop:document.querySelector('#booking-dialog').scrollTop};
            }''')
            gap = 16 if width < 640 else 24
            assert abs(metrics['topGap'] - gap) <= 1, metrics
            assert abs(metrics['bottomGap'] - gap) <= 1, metrics
            assert metrics['aligned'] and metrics['aboveFooter'] and not metrics['overflow'], metrics
            assert abs(metrics['scrollTop']-before_error)<=1, 'Showing the warning must not scroll the header away'
            assert page.locator('#booking-service-list [aria-pressed=true]').count() == 1
            page.locator('#booking-dialog').evaluate('(d)=>d.scrollTop+=500')
            assert page.locator('#booking-error').is_visible()
            assert page.evaluate('''() => {
              const a=document.querySelector('#booking-error').getBoundingClientRect();
              const f=document.querySelector('.booking-footer').getBoundingClientRect();
              const d=document.querySelector('#booking-dialog').getBoundingClientRect();
              return a.top>=d.top+12 && a.bottom<=f.top;
            }''')
            stem=f'alert-{engine}-{theme}-{width}x{height}'
            page.screenshot(path=str(ARTIFACTS / f'{stem}.png'))
            (ARTIFACTS / f'{stem}.json').write_text(json.dumps(metrics, indent=2), encoding='utf-8')
        finally:
            browser.close()


@pytest.mark.parametrize('engine', ['chromium', 'webkit'])
@pytest.mark.parametrize('width,height', SIZES)
@pytest.mark.parametrize('theme', ['light', 'dark'])
def test_date_control_is_aligned_and_days_scroll_without_arrows(fixture_url, engine, width, height, theme):
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = getattr(playwright, engine).launch()
        page = browser.new_page(viewport={'width': width, 'height': height},
            color_scheme=theme, locale='pt-BR', has_touch=width <= 430,
            is_mobile=engine == 'webkit' and width <= 430)
        errors=[]
        page.on('pageerror', lambda error: errors.append(str(error)))
        try:
            page.goto(fixture_url)
            page.locator('#hero-book').click()
            page.get_by_role('dialog').get_by_role('button', name='Corte Masculino', exact=False).click()
            page.locator('#booking-next').click()
            date=page.get_by_label('Outra data', exact=True)
            date.fill('2030-02-01')
            page.evaluate('document.fonts.ready')
            assert date.input_value()=='2030-02-01'
            assert page.locator('#booking-day-value').inner_text()=='01/02/2030'
            assert page.locator('#booking-day-prev, #booking-day-next').count()==0
            metrics=page.evaluate('''() => {
              const control=document.querySelector('.booking-date-control').getBoundingClientRect();
              const input=document.querySelector('#booking-day-input').getBoundingClientRect();
              const value=document.querySelector('#booking-day-value');
              const month=document.querySelector('#booking-month-heading').getBoundingClientRect();
              const heading=document.querySelector('.date-heading').getBoundingClientRect();
              const strip=document.querySelector('#booking-day-strip').getBoundingClientRect();
              const day=document.querySelector('#booking-day-strip [aria-pressed=true]').getBoundingClientRect();
              return {height:control.height,width:control.width,hitHeight:input.height,
                monthAligned:Math.abs((month.top+month.bottom-control.top-control.bottom)/2)<1,
                textFits:value.scrollWidth<=value.clientWidth,
                stripFullWidth:Math.abs(strip.width-heading.width)<1,
                selectedVisible:day.left>=strip.left-1&&day.right<=strip.right+1,
                overflow:document.documentElement.scrollWidth>innerWidth};
            }''')
            assert abs(metrics['height']-44)<=1 and abs(metrics['hitHeight']-44)<=1, metrics
            assert metrics['width']<=160 and metrics['textFits'] and metrics['monthAligned'], metrics
            assert metrics['stripFullWidth'] and metrics['selectedVisible'] and not metrics['overflow'], metrics
            date.focus()
            assert page.locator('.booking-date-control').evaluate('(e)=>getComputedStyle(e).outlineStyle')=='solid'
            date.click()
            page.keyboard.press('Escape')
            assert page.locator('#booking-dialog').is_visible()
            assert date.input_value()=='2030-02-01'
            date.fill('')
            assert date.input_value()=='2030-02-01', 'Clear preserves the selected day instead of showing conflicting dates'
            page.locator('#booking-day-strip .day-button').last.focus()
            assert page.locator('#booking-day-strip').evaluate('(e)=>e.scrollLeft')>0
            page.locator('#booking-day-strip .day-button').nth(3).click()
            assert page.locator('#booking-day-value').inner_text()=='04/02/2030'
            stem=f'date-{engine}-{theme}-{width}x{height}'
            page.screenshot(path=str(ARTIFACTS / f'{stem}.png'))
            (ARTIFACTS / f'{stem}.json').write_text(json.dumps(metrics, indent=2), encoding='utf-8')
            assert not errors, errors
        finally:
            browser.close()
