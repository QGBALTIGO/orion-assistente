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
            page.get_by_role('dialog').get_by_role('button', name='Corte Masculino', exact=False).click()
            page.get_by_role('dialog').get_by_role('button', name='COMBO EM DESTAQUE', exact=False).click()
            page.evaluate('document.fonts.ready')
            metrics = page.evaluate('''() => {
              const alert=document.querySelector('#booking-error').getBoundingClientRect();
              const steps=document.querySelector('#booking-steps').getBoundingClientRect();
              const intro=document.querySelector('.booking-selection-intro').getBoundingClientRect();
              const footer=document.querySelector('.booking-footer').getBoundingClientRect();
              return {topGap:alert.top-steps.bottom,bottomGap:intro.top-alert.bottom,
                aligned:Math.abs(alert.left-intro.left)<1,aboveFooter:alert.bottom<footer.top,
                overflow:document.documentElement.scrollWidth>innerWidth};
            }''')
            gap = 16 if width < 640 else 24
            assert abs(metrics['topGap'] - gap) <= 1, metrics
            assert abs(metrics['bottomGap'] - gap) <= 1, metrics
            assert metrics['aligned'] and metrics['aboveFooter'] and not metrics['overflow'], metrics
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
