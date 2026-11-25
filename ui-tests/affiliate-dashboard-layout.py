"""Offline browser QA for the affiliate Dashboard in the complete Orion panel.

Run: python ui-tests/affiliate-dashboard-layout.py
Synthetic financial fixtures exercise a future integrated report; the unavailable
fixture reflects the current capability. Every network request is intercepted,
and non-GET requests fail. No production session, account or contact is used.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import shutil
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts' / 'affiliate-dashboard'
ORIGIN = 'https://orion-preview.invalid'
TENANT = '10000000-0000-4000-8000-000000000001'
EMAIL = 'layout-test@example.invalid'
API = f'/api/v1/afiliados/{TENANT}/api/'
PANEL = (ROOT / 'app/static/panel.html').read_text(encoding='utf-8')
EPOCH = int(datetime.fromisoformat('2026-11-25T15:00:00+00:00').timestamp())
BUSINESS = {'id': TENANT, 'name': 'Canal de ofertas de teste', 'role': 'owner',
            'status': 'active', 'slug': 'canal-de-ofertas-de-teste',
            'timezone': 'America/Campo_Grande', 'workspace_profile': 'affiliate'}
PERMISSIONS = {
    'code': 'none', 'name': 'Sem acesso ativo', 'source': 'none', 'status': 'inactive',
    'active': False, 'workspace_profile': 'affiliate', 'layout_modes': ['classic', 'app'],
    'features': ['plan.manage', 'account.manage', 'business.setup'],
    'permissions': {'plan.manage': True, 'account.manage': True, 'business.setup': True},
    'limits': {'max_professionals': 0, 'active_professionals': 0, 'excess_professionals': 0},
    'platform_workspace': False, 'revision': 'synthetic-affiliate-layout',
}


def workspace(empty: bool = False) -> dict:
    destinations = [
        {'id': 'synthetic-telegram', 'name': 'Ofertas em tecnologia e acessórios',
         'mode': 'telegram', 'test': True, 'verified': True},
        {'id': 'synthetic-whatsapp', 'name': 'Achados para casa e decoração',
         'mode': 'whatsapp_channel', 'test': False, 'verified': True},
    ]
    titles = ['Fone de ouvido sem fio com cancelamento de ruído e estojo de recarga',
              'Luminária de mesa articulada para escritório e leitura',
              'Organizador multiuso para cozinha com divisórias ajustáveis']
    return {
        'company': BUSINESS['name'], 'niches': [], 'offer': [], 'template': [],
        'decision': [], 'product_flag': [], 'sorts': {'2': 'Mais vendidos'},
        'connection': [{'id': 'shopee', 'status': 'connected',
                        'account': 'Conta sintética', 'verified': EPOCH}],
        'destination': [] if empty else destinations,
        'rule': [] if empty else [{'id': 'synthetic-rule', 'name': 'Achados da tarde',
                                  'active': True, 'next_at': EPOCH + 3600,
                                  'timezone': BUSINESS['timezone'],
                                  'destinations': ['synthetic-telegram']}],
        'jobs': [] if empty else [
            {'id': f'synthetic-job-{index}', 'status': status, 'changed': EPOCH - index * 600,
             'due': EPOCH + 1800 if status == 'queued' else None,
             'destination': destinations[index % 2]['id'],
             'doc': {'offer': {'title': titles[index]}}}
            for index, status in enumerate(('queued', 'published', 'published'))
        ],
    }


def report(period: str, ready: bool) -> dict:
    labels = {'today': 'Hoje', '7d': '7 dias', '30d': '30 dias', 'month': 'Este mês'}
    count = {'today': 1, '7d': 7, '30d': 30, 'month': 25}[period]
    end = datetime(2026, 11, 25)
    series = []
    for index in range(count):
        date = (end - timedelta(days=count - index - 1)).date().isoformat()
        commission = [0, 1600, 2600, 1100, 4200, 3600, 5100][index % 7]
        series.append({'date': date, 'commission_cents': commission if ready else None,
                       'validated_commission_cents': None,
                       'sales_cents': commission * 14 if ready else None,
                       'orders': [0, 3, 4, 2, 6, 5, 8][index % 7] if ready else None,
                       'items': None, 'clicks': None,
                       'published': [1, 4, 3, 2, 5, 4, 6][index % 7] if ready else None})
    state = workspace(empty=not ready)
    jobs = [{**job, 'title': job['doc']['offer']['title'],
             'destination_id': job['destination'],
             'destination_name': next(d['name'] for d in state['destination'] if d['id'] == job['destination'])}
            for job in state['jobs']]
    return {
        'period': {'key': period, 'label': labels[period], 'start_date': series[0]['date'],
                   'end_date': series[-1]['date'], 'timezone': BUSINESS['timezone']},
        'updated_at': '2026-11-25T15:00:00+00:00',
        'sources': {
            'sales': {'status': 'ready' if ready else 'unavailable', 'updated_at': None,
                      'reason': None if ready else 'O relatório de vendas ainda não foi integrado.'},
            'clicks': {'status': 'unavailable', 'updated_at': None,
                       'reason': 'Consulte o relatório de cliques da Shopee.'},
            'publications': {'status': 'ready' if ready else 'partial', 'complete': ready,
                             'scope': 'period' if ready else 'latest_120', 'updated_at': None},
        },
        'summary': {
            'commission_cents': sum(row['commission_cents'] for row in series) if ready else None,
            'validated_commission_cents': 28400 if ready else None,
            'sales_cents': sum(row['sales_cents'] for row in series) if ready else None,
            'orders': sum(row['orders'] for row in series) if ready else None,
            'items': None, 'clicks': None, 'conversion_rate': None,
            'published': sum(row['published'] for row in series) if ready else None,
            'pending': 1 if ready else 0, 'failed': 0,
            'active_rules': 1 if ready else 0, 'verified_destinations': 2 if ready else 0,
        },
        'operation': {'published_snapshot': 2 if ready else 0, 'pending_snapshot': 1 if ready else 0,
                      'failed_snapshot': 0, 'sample_size': 3 if ready else 0, 'sample_limit': 120},
        'series': series,
        'upcoming': [job for job in jobs if job['status'] == 'queued'],
        'recent': [job for job in jobs if job['status'] == 'published'],
        'destinations': [{**destination, 'published': 39 if index == 0 else 17,
                          'published_snapshot': 1, 'pending_snapshot': 1 if index == 0 else 0,
                          'failed_snapshot': 0, 'orders': 58 if index == 0 else 29,
                          'commission_cents': 31000 if index == 0 else 15200}
                         for index, destination in enumerate(state['destination'])],
        'products': [] if not ready else [
            {'id': 'synthetic-product-a', 'title': 'Fone de ouvido sem fio com cancelamento de ruído',
             'orders': 24, 'commission_cents': 18400},
            {'id': 'synthetic-product-b', 'title': 'Luminária de mesa articulada para escritório e leitura',
             'orders': 18, 'commission_cents': 11600},
            {'id': 'synthetic-product-c', 'title': 'Organizador multiuso com divisórias ajustáveis',
             'orders': 13, 'commission_cents': 7100},
        ],
    }


def run(context, width: int, theme: str, layout: str, state_name: str) -> dict:
    ready = state_name == 'ready'
    calls, errors, blocked = [], [], []
    page = context.new_page()
    page.on('pageerror', lambda error: errors.append(str(error)))

    def respond(route):
        request = route.request
        url = urlsplit(request.url)
        path = url.path
        calls.append((request.method, path))
        if request.method != 'GET':
            errors.append('Blocked write: ' + request.method + ' ' + path)
            route.fulfill(status=405, body='No writes allowed in layout QA')
            return
        if f'{url.scheme}://{url.netloc}' != ORIGIN:
            blocked.append(request.url)
            route.abort()
            return

        def fulfill(payload, status=200):
            route.fulfill(status=status, content_type='application/json',
                          body=json.dumps(payload, ensure_ascii=False))

        if path == '/panel':
            route.fulfill(status=200, content_type='text/html', body=PANEL)
            return
        if path.startswith(('/panel-assets/', '/brand/')) or path.startswith('/api/v1/afiliados-assets/workspace.'):
            name = path.rsplit('/', 1)[-1]
            name = {'workspace.js': 'orion-affiliate-workspace.js',
                    'workspace.css': 'orion-affiliate-workspace.css'}.get(name, name)
            asset = ROOT / 'app/static' / name
            if asset.is_file():
                mime = {'.js': 'application/javascript', '.css': 'text/css',
                        '.svg': 'image/svg+xml', '.woff2': 'font/woff2',
                        '.png': 'image/png', '.webp': 'image/webp'}.get(asset.suffix, 'application/octet-stream')
                route.fulfill(status=200, content_type=mime, body=asset.read_bytes())
            else:
                errors.append('Missing local asset: ' + path)
                route.fulfill(status=404, body='Missing local asset')
            return
        if path == '/auth/me':
            fulfill({'authenticated': True, 'role': 'member', 'csrf': 'synthetic-affiliate-csrf',
                     'name': 'Conta sintética', 'email': EMAIL, 'verified': True})
        elif path == '/auth/features':
            fulfill({'registration': False, 'passwordRecovery': False})
        elif path in ('/api/v1/account/businesses', '/api/v1/tenants'):
            fulfill([BUSINESS])
        elif path == '/api/v1/onboarding/entry':
            fulfill({'enabled': True, 'kind': 'affiliate', 'create_allowed': False,
                     'account': {'name': 'Conta sintética', 'email': EMAIL, 'verified': True},
                     'businesses': [BUSINESS], 'invitations': [], 'tenant_id': TENANT,
                     'current_step': 'business'})
        elif path.endswith('/management/permissions'):
            fulfill({'effective_plan': PERMISSIONS})
        elif path == '/api/v1/account/business-identity':
            fulfill({'name': BUSINESS['name']})
        elif path == API + 'state':
            fulfill(workspace(empty=not ready))
        elif path == API + 'analytics':
            period = parse_qs(url.query).get('period', ['month'])[0]
            fulfill(report(period, ready))
        elif path.startswith('/api/v1/'):
            fulfill([])
        elif path in ('/favicon.ico', '/manifest.webmanifest') or path.startswith('/pwa/'):
            route.fulfill(status=204, body='')
        else:
            errors.append('Unexpected local request: ' + path)
            route.fulfill(status=404, body='Unknown local path')

    # Context-level interception blocks every origin, including any accidental
    # asset, popup or link request. There is no fallthrough to a live server.
    context.route('**/*', respond)
    context.add_init_script('localStorage.setItem("orion-theme", ' + json.dumps(theme) + ');'
        + 'localStorage.setItem(' + json.dumps(f'orion-navigation-v1:{TENANT}:{EMAIL}')
        + ', ' + json.dumps(layout) + ');')
    page.goto(ORIGIN + '/panel?aba=dashboard', wait_until='domcontentloaded')
    page.locator('.af-dashboard-overview[aria-busy="false"]').wait_for(timeout=15000)
    page.evaluate('document.fonts.ready')
    assert page.locator('.af-dashboard h1').inner_text() == 'Dashboard'
    assert page.locator('[data-dashboard-period][aria-pressed="true"]').get_attribute('data-dashboard-period') == 'month'
    assert page.locator('#appView').evaluate('(node)=>node.classList.contains("os-layout-app")') == (layout == 'app')
    assert page.locator('#osAppDock').is_visible() == (layout == 'app')
    assert not errors, (width, theme, layout, state_name, errors)
    assert not blocked, (width, theme, layout, state_name, blocked)

    if ready:
        assert page.locator('[data-dashboard-kpi="clicks"]').inner_text().find('Na Shopee') != -1
        assert page.locator('[data-dashboard-point]').count() == 25
        # Real browser focus/keyboard, beyond the DOM-only interaction suite.
        page.locator('[data-dashboard-point][tabindex="0"]').focus()
        page.keyboard.press('Home')
        assert page.locator('[data-dashboard-point="0"]').evaluate('(node)=>node===document.activeElement')
        page.keyboard.press('ArrowRight')
        assert page.locator('[data-dashboard-point="1"]').evaluate('(node)=>node===document.activeElement')
        assert '16,00' in page.locator('[data-dashboard-point-value]').inner_text()
        page.keyboard.press('End')
        assert page.locator('[data-dashboard-point="24"]').evaluate('(node)=>node===document.activeElement')
    else:
        assert page.locator('.af-dashboard-revenue-value').count() == 0
        assert 'R$ 0,00' not in page.locator('.af-dashboard').inner_text().replace('\xa0', ' ')
        assert page.locator('[data-dashboard-point]').count() == 0
        assert 'Preparar primeira oferta' in page.locator('.af-dashboard-revenue').inner_text()

    page.evaluate('window.scrollTo(0, 0)')
    metrics = page.evaluate('''() => {
      const root=document.querySelector('.af-dashboard');
      const rect=node=>{const r=node.getBoundingClientRect();return {left:r.left,right:r.right,width:r.width,height:r.height};};
      const cards=[...root.querySelectorAll('.af-dashboard-revenue,.af-dashboard-kpi,.af-dashboard-panel')].map(node=>({
        kind:node.className,rect:rect(node),clientWidth:node.clientWidth,scrollWidth:node.scrollWidth
      }));
      const clippedText=[];
      for(const node of root.querySelectorAll('.af-dashboard-revenue-value,.af-dashboard-revenue-validation strong,.af-dashboard-kpi>strong,.af-dashboard-chart-total strong,.af-dashboard-rank-value')){
        const card=node.closest('.af-dashboard-revenue,.af-dashboard-kpi,.af-dashboard-panel');
        const bounds=card.getBoundingClientRect();
        const range=document.createRange();range.selectNodeContents(node);
        if([...range.getClientRects()].some(r=>r.left<bounds.left-1||r.right>bounds.right+1))
          clippedText.push({text:node.textContent,rect:rect(node),card:rect(card)});
      }
      return {viewport:innerWidth,documentWidth:document.documentElement.scrollWidth,
        theme:document.documentElement.dataset.theme,root:rect(root),cards,clippedText,
        periodButtons:[...root.querySelectorAll('[data-dashboard-period]')].map(node=>({label:node.textContent,...rect(node)}))};
    }''')
    name = f'{state_name}-{layout}-{width}-{theme}'
    page.screenshot(path=str(OUT / (name + '.png')), full_page=True)
    metrics.update(width=width, layout=layout, state=state_name, screenshot=name + '.png')
    assert metrics['theme'] == theme, metrics
    assert metrics['documentWidth'] <= width + 1, metrics
    assert metrics['root']['left'] >= -1 and metrics['root']['right'] <= width + 1, metrics
    for card in metrics['cards']:
        assert card['rect']['left'] >= -1 and card['rect']['right'] <= width + 1, (name, card)
        assert card['scrollWidth'] <= card['clientWidth'] + 2, (name, card)
    assert not metrics['clippedText'], (name, metrics['clippedText'])
    for button in metrics['periodButtons']:
        assert button['height'] >= 40 and button['left'] >= -1 and button['right'] <= width + 1, (name, button)

    if width == 430 and theme == 'light':
        if ready:
            page.locator('[data-dashboard-publications="recent"]').click()
            assert page.locator('.af-dashboard-job-list li').count() == 2
            page.locator('[data-dashboard-metric="orders"]').click()
            assert page.locator('[data-dashboard-metric="orders"]').get_attribute('aria-pressed') == 'true'
            page.locator('[data-dashboard-period="7d"]').click()
            page.locator('.af-dashboard-overview[aria-busy="false"]').wait_for()
            assert page.locator('[data-dashboard-point]').count() == 7
        else:
            page.locator('.af-dashboard-revenue a[href="#ofertas"]').click()
            page.locator('[data-form="offer"]').wait_for()
            assert parse_qs(urlsplit(page.url).query)['aba'] == ['afiliados-ofertas']
    assert not errors and not blocked, (name, errors, blocked)
    assert all(method == 'GET' for method, _ in calls)
    metrics['get_requests'] = len(calls)
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--widths', type=int, nargs='+', default=[1280, 768, 430, 360, 320])
    parser.add_argument('--themes', nargs='+', choices=['light', 'dark'], default=['light', 'dark'])
    parser.add_argument('--layouts', nargs='+', choices=['classic', 'app'], default=['classic', 'app'])
    parser.add_argument('--states', nargs='+', choices=['ready', 'unavailable'], default=['ready', 'unavailable'])
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    measurements = []
    with sync_playwright() as playwright:
        bundled = Path(playwright.chromium.executable_path)
        executable = str(bundled) if bundled.is_file() else (
            os.getenv('ORION_TEST_BROWSER') or shutil.which('chromium') or shutil.which('google-chrome'))
        browser = playwright.chromium.launch(**({'executable_path': executable} if executable else {}), args=['--no-sandbox'])
        try:
            for width in args.widths:
                for theme in args.themes:
                    for layout in args.layouts:
                        for state_name in args.states:
                            context = browser.new_context(viewport={'width': width, 'height': 900},
                                is_mobile=width < 768, has_touch=width < 768,
                                device_scale_factor=1, service_workers='block')
                            try:
                                measurements.append(run(context, width, theme, layout, state_name))
                                print(f'PASS {width}px {theme} {layout} {state_name}', flush=True)
                            finally:
                                context.close()
        finally:
            browser.close()
            (OUT / 'measurements.json').write_text(json.dumps(measurements, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'PASS: {len(measurements)} states; zero writes or external requests; screenshots in {OUT}')


if __name__ == '__main__':
    main()
