"""Offline browser regression for text actions in Orion's scrollable tables.

Run: python ui-tests/table-action-layout.py
Requires Playwright and Chromium (ORION_TEST_BROWSER can select a local binary).
Only synthetic rows are rendered. No Orion API or production session is used.
"""
from __future__ import annotations

import argparse
import html
import json
import os
from pathlib import Path
import re
import shutil

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
WIDTHS = (320, 360, 375, 390, 414, 430, 640, 768, 1024, 1440)
LABELS = ("Conferir quitação", "Ver registro", "Emitir cupom")
ICON = '<svg class="os-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M20 8V5a2 2 0 0 0-2-2H6a3 3 0 0 0 0 6h14v12H6a3 3 0 0 1-3-3V6m17 7h-4a2 2 0 0 0 0 4h4"/></svg>'


def fixture(label: str, long_name: bool) -> str:
    names = ["Profissional de teste A", "Profissional de teste B"]
    if long_name:
        names[0] = "Nome de profissional bastante extenso para testar quebra normal"
    headings = ("Profissional", "Atendimentos", "Valor vendido", "Comissão", "Vales no período", "Saldo a pagar", "")
    rows = []
    for index, name in enumerate(names):
        amount = "R$&nbsp;123.456,78" if long_name else "R$&nbsp;0,00"
        action = (ICON + html.escape(label)) if label != "Ver registro" else (html.escape(label) + " " + ICON)
        disabled = " disabled" if index else ""
        rows.append('<tr><td><strong>' + html.escape(name) + '</strong><br><small>Base 30%</small></td><td>0</td>' + '<td>' + amount + '</td><td>' + amount + '</td><td>' + amount + '</td><td>' + amount + '</td><td><button type="button" class="os-button os-outline os-small"' + disabled + '>' + action + '</button></td></tr>')
    return '<div id="appView" class="app orion-suite-enabled"><aside class="sidebar"><div class="brand">ORION</div></aside><main class="content"><section id="page-suite" class="page os-main" data-os-current-route="financeiro"><section class="os-card"><div class="os-card-header"><div><h2>Comissões por profissional</h2><p>Comissões geradas por atendimentos concluídos no período.</p></div></div><div class="os-table-scroll"><table class="os-table"><thead><tr>' + ''.join('<th>' + title + '</th>' for title in headings) + '</tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div></section><div style="width:100px;margin-top:20px"><button class="os-button" id="outside-table">Um botão de formulário pode continuar quebrando linhas</button></div></section></main></div>'


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expect-broken', action='store_true', help='Prove the original stylesheet wraps table actions.')
    args = parser.parse_args()
    panel = (ROOT / 'app/static/panel.html').read_text(encoding='utf-8')
    base_css = '\n'.join(re.findall(r'<style[^>]*>(.*?)</style>', panel, flags=re.S | re.I))
    css = (ROOT / 'app/static/orion-suite.css').read_text(encoding='utf-8')
    output = ROOT / 'artifacts/table-actions'
    output.mkdir(parents=True, exist_ok=True)
    measurements = []
    failures = []
    executable = os.getenv('ORION_TEST_BROWSER') or shutil.which('chromium') or shutil.which('google-chrome')
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(**({'executable_path': executable} if executable else {}), args=['--no-sandbox'])
        for theme in ('light', 'dark'):
            for width in WIDTHS:
                context = browser.new_context(viewport={'width': width, 'height': 900}, has_touch=width < 768, is_mobile=width < 768)
                def local_asset(route):
                    name = route.request.url.rsplit('/', 1)[-1]
                    if name in {'figtree-latin.woff2', 'outfit-latin.woff2'} and (ROOT / 'app/static' / name).is_file():
                        route.fulfill(path=str(ROOT / 'app/static' / name), content_type='font/woff2')
                    else:
                        route.abort()
                context.route('**/*', local_asset)
                page = context.new_page()
                for label in LABELS:
                    for long_name in (False, True):
                        page.set_content('<!doctype html><html lang="pt-BR" data-theme="' + theme + '"><head><meta name="viewport" content="width=device-width, initial-scale=1"><base href="https://orion-layout.test/"><style>' + base_css + '\n' + css + '</style></head><body>' + fixture(label, long_name) + '</body></html>')
                        page.evaluate('document.fonts.ready')
                        results = page.locator('.os-table .os-button').evaluate_all('''buttons => buttons.map(button => {
                          const rect = button.getBoundingClientRect();
                          const labelRects = [...button.childNodes].filter(node => node.nodeType === 3 && node.textContent.trim()).flatMap(node => {
                            const range = document.createRange(); range.selectNodeContents(node);
                            return [...range.getClientRects()].filter(r => r.width > 0).map(r => ({top:r.top, right:r.right, left:r.left, height:r.height}));
                          });
                          const icon = button.querySelector('svg').getBoundingClientRect();
                          return {width:rect.width,height:rect.height,disabled:button.disabled,whiteSpace:getComputedStyle(button).whiteSpace,
                            lines:new Set(labelRects.map(r => Math.round(r.top))).size,
                            clipped:labelRects.some(r => r.left < rect.left - 1 || r.right > rect.right + 1),iconWidth:icon.width};
                        })''')
                        document_width = page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
                        wrapper = page.locator('.os-table-scroll')
                        wrapper.evaluate('node => node.scrollLeft = node.scrollWidth')
                        reachable = page.locator('.os-table .os-button').first.evaluate('''button => {const b=button.getBoundingClientRect(),s=button.closest('.os-table-scroll').getBoundingClientRect();return b.left>=s.left-1 && b.right<=s.right+1;}''')
                        for result in results:
                            result.update(theme=theme,viewport=width,label=label,long_name=long_name)
                            measurements.append(result)
                            if result['lines'] != 1 or result['height'] > 48 or result['clipped'] or result['iconWidth'] < 15:
                                failures.append(result)
                            if not args.expect_broken:
                                assert result['lines'] == 1, result
                                assert result['height'] <= 48 and not result['clipped'] and result['iconWidth'] >= 15, result
                                if width < 768:
                                    assert result['height'] >= 44, result
                        if not args.expect_broken:
                            assert document_width and reachable, (width, theme, label, long_name)
                            assert page.locator('#outside-table').evaluate('node => getComputedStyle(node).whiteSpace') != 'nowrap'
                            wrapper.evaluate('node => node.scrollLeft = 0')
                            page.locator('.os-table .os-button:not([disabled])').first.focus()
                            assert page.locator('.os-table .os-button:not([disabled])').first.evaluate('node => node === document.activeElement')
                        if width == 390 and theme == 'light' and label == LABELS[0] and not long_name:
                            wrapper.evaluate('node => node.scrollLeft = node.scrollWidth')
                            page.locator('.os-card').first.screenshot(path=str(output / ('before.png' if args.expect_broken else 'after.png')))
                context.close()
        browser.close()
    (output / ('before.json' if args.expect_broken else 'after.json')).write_text(json.dumps(measurements, indent=2, ensure_ascii=False), encoding='utf-8')
    if args.expect_broken:
        assert failures, 'Baseline did not reproduce any wrapped action; inspect before applying.'
        print(f'BASELINE: {len(failures)} of {len(measurements)} button measurements reproduce wrapping/compression.')
    else:
        print(f'PASS: {len(measurements)} button measurements; 10 widths, 2 themes, 3 labels, long names, large values and disabled states. No API calls.')


if __name__ == '__main__':
    main()
