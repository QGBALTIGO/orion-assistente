"""Loopback-only plans fixture using the shipped panel and existing UI server.

No database, billing client, worker, provider, account or external request is used.
Open /panel?fixtureRole=admin|owner&fixturePlan=none|essencial|profissional|elite.
Only synthetic state in that browser context may change. Unknown writes fail closed.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
from http.server import ThreadingHTTPServer
import json
import secrets
import time
import unicodedata
from urllib.parse import parse_qs, urlsplit

from app.plan_catalog import CODES, PLAN_FEATURES, RECOVERY_FEATURES, feature_catalog
from scripts import whatsapp_connect_preview as base

ACCOUNT = '90000000-0000-4000-8000-000000000009'
ADMIN_PATH = '/api/v1/admin/accounts/' + ACCOUNT
PLAN_PATH = ADMIN_PATH + '/companies/' + base.TENANT + '/plan'
UNASSIGNED_PATH = '/api/v1/admin/accounts/companies/unassigned'
UNASSIGNED_COMPANY = '70000000-0000-4000-8000-000000000002'
COMPANY_PLAN_PATH = '/api/v1/admin/accounts/companies/' + UNASSIGNED_COMPANY + '/plan'


def state(session):
    code, source = session['plan'], session['plan_source']
    has_plan = code in CODES
    active = has_plan and session.get('company_status', 'active') == 'active'
    features = sorted((PLAN_FEATURES[code] if active else frozenset()) | RECOVERY_FEATURES)
    commercial = session['commercial']
    manual = ({'plan_code': code, 'started_at': session['plan_started'],
               'expires_at': session.get('plan_expires'), 'active': has_plan} if source == 'manual' else None)
    result = {'code': code if has_plan else None, 'name': CODES[code]['name'] if has_plan else 'Sem plano ativo',
              'source': source if has_plan else 'none', 'status': 'active' if active else 'tenant_inactive' if has_plan else 'no_access',
              'active': active, 'valid_until': session.get('plan_expires'),
              'version': session['plan_version'], 'features': features,
              'permissions': {key: key in features for key in sorted(set().union(*PLAN_FEATURES.values(), RECOVERY_FEATURES))},
              'limits': {'max_professionals': CODES[code]['max_professionals'] if has_plan else None,
                         'active_professionals': session['staff_count'],
                         'excess_professionals': max(0, session['staff_count'] - CODES[code]['max_professionals']) if has_plan else 0},
              'manual_assignment': manual, 'commercial_subscription': commercial,
              'trial': None, 'platform_workspace': False}
    result['revision'] = hashlib.sha256(json.dumps({**result, 'tenant_id': session.get('tenant_id', base.TENANT)}, sort_keys=True).encode()).hexdigest()
    return result


def account(session):
    effective = state(session)
    return {'id': ACCOUNT, 'company_id': base.TENANT, 'company_name': 'Empresa sintética de planos',
            'name': 'Responsável sintético', 'email': 'plans@example.invalid', 'phone': '+5511900000001',
            'state': 'active', 'auth_version': 1, 'registration_pending': False,
            'created_at': '2026-10-01T12:00:00Z', 'verified_at': '2026-10-01T12:01:00Z',
            'last_access_at': '2026-10-29T12:00:00Z', 'company_membership_count': 1,
            'can_manage_plan': True, 'effective_plan': effective,
            'commercial_subscription': effective['commercial_subscription']}


def envelope(session, company_id=None):
    direct = company_id is not None
    record = session['company_plans'][company_id] if direct else session
    return {'company': {'id': company_id if direct else base.TENANT,
                        'name': record['company_name'] if direct else 'Empresa sintética de planos',
                        'status': record.get('company_status', 'active'),
                        'account_id': None if direct else ACCOUNT, 'can_manage_plan': True,
                        **({'owner_status': record['owner_status']} if direct else {}),
                        'active_professionals': record['staff_count']},
            'effective_plan': state(record), 'history': list(reversed(record['plan_history'])), 'replayed': False}


def unassigned_companies(session):
    rows = [{'id': key, 'name': record['company_name'], 'status': record['company_status'],
             'owner_status': record['owner_status'], 'can_manage_plan': True,
             'effective_plan': state(record), 'active_professionals': record['staff_count']}
            for key, record in session.get('company_plans', {}).items() if record['owner_status'] == 'unassigned']
    return sorted(rows, key=lambda item: (item['name'].casefold(), item['id']))


def seed_unassigned():
    values = {}
    for index in range(1, 6):
        key = f'70000000-0000-4000-8000-{index:012d}'
        values[key] = {'tenant_id': key,
            'company_name': 'Empresa pausada sintética' if index == 2 else f'Empresa com vínculo pendente sintética {index}',
            'company_status': 'paused' if index == 2 else 'active', 'owner_status': 'unassigned',
            'plan': 'essencial', 'plan_source': 'manual', 'plan_version': 1,
            'plan_started': '2026-10-29T00:00:00Z', 'plan_expires': None, 'plan_history': [],
            'staff_count': 0, 'commercial': None}
    return values


class Handler(base.Handler):
    def do_GET(self):
        url = urlsplit(self.path)
        path, query = url.path, parse_qs(url.query)
        # The shipped panel includes a public PWA manifest. Test browsers may
        # fetch it even with service workers disabled; serve it as inert data.
        if path == '/manifest.webmanifest':
            return self.body((base.ROOT / 'app/static/orion-pwa.webmanifest').read_bytes(),
                             mime='application/manifest+json')
        if path in {'/pwa/icon-180.png','/pwa/icon-192.png','/pwa/icon-512.png'}:
            from app.orion_pwa_icon import icon_png
            return self.body(icon_png(int(path.split('icon-')[1].split('.')[0])),mime='image/png')
        if path == '/pwa-sw.js':
            return self.body((base.ROOT / 'app/static/orion-pwa-sw.js').read_bytes(),
                             mime='application/javascript')
        if path in {'/', '/panel'}:
            existing = self.session()
            if existing and 'plan' in existing and 'fixtureRole' not in query and 'fixturePlan' not in query:
                return self.body((base.ROOT / 'app/static/panel.html').read_bytes(), mime='text/html')
            role, plan = query.get('fixtureRole', ['owner'])[0], query.get('fixturePlan', ['essencial'])[0]
            source = query.get('fixtureSource', ['manual'])[0]
            if role not in base.ROLES or plan not in {*CODES, 'none'} or source not in {'manual', 'cakto', 'trial'}:
                return self.body({'detail': 'Unknown isolated plans fixture'}, 400)
            session = base.make_session(role, 'connected')
            session.update(plan=plan, plan_source=source if plan != 'none' else 'none', plan_version=1,
                           plan_started='2026-10-29T00:00:00Z', plan_expires=None, plan_history=[],
                           company_plans=seed_unassigned() if query.get('fixtureUnassigned') == ['1'] else {},
                           delayed=query.get('fixtureDelay') == ['1'],
                           staff_count=8 if query.get('fixtureExcess') == ['1'] else 2,
                           commercial=({'code': 'orion_essencial', 'name': 'Orion Essencial', 'status': 'active',
                                        'monthly_cents': 2990, 'max_professionals': 2} if plan != 'none' else None))
            token = secrets.token_urlsafe(24)
            with base._lock:
                base._sessions[token] = session
            return self.body((base.ROOT / 'app/static/panel.html').read_bytes(), mime='text/html', cookie=token)
        session = self.session()
        if session and session.get('delayed') and path in {'/auth/features', base.BASE + '/management/permissions'}:
            time.sleep(.18)
        if session and path.endswith('/management/permissions'):
            return self.body({'effective_plan': state(session)})
        if session and path.endswith('/management/plan-overview'):
            return self.body({'plans': [{'code': code, 'name': item['name'], 'monthly_cents': item['price_cents'],
                                        'max_professionals': item['max_professionals']} for code, item in CODES.items()],
                              'effective_plan': state(session), 'subscription': session['commercial'], 'trial': None,
                              'active_professionals': session['staff_count'], 'can_manage_plan': True,
                              'checkout_enabled': False, 'feature_catalog': feature_catalog()})
        if session and path.startswith('/api/v1/admin/accounts'):
            if session['role'] != 'admin':
                return self.reject(session, 'Synthetic directory is admin-only')
            session['calls'].append({'method': 'GET', 'path': self.path})
            if session.get('delayed'):
                time.sleep(.12)
            if path == UNASSIGNED_PATH:
                return self.body({'companies': unassigned_companies(session)})
            company_id = next((key for key in session.get('company_plans', {})
                               if path == '/api/v1/admin/accounts/companies/' + key + '/plan'), None)
            if company_id:
                return self.body(envelope(session, company_id))
            if path == '/api/v1/admin/accounts':
                item = account(session)
                normalize = lambda value: ''.join(char for char in unicodedata.normalize('NFD', str(value)) if unicodedata.category(char) != 'Mn').lower()
                matches = not query.get('q') or normalize(query['q'][0]) in normalize(' '.join(str(item.get(key) or '') for key in ('name', 'email', 'company_name', 'phone')))
                matches = matches and (query.get('status', ['all'])[0] in {'all', item['state']})
                matches = matches and (query.get('plan', ['all'])[0] in {'all', item['effective_plan']['code'] or 'none'})
                matches = matches and (query.get('source', ['all'])[0] in {'all', item['effective_plan']['source']})
                wanted = query.get('subscription_status', ['all'])[0]
                commercial = item['effective_plan']['commercial_subscription'] or {}
                matches = matches and (wanted == 'all' or commercial.get('status') == wanted or wanted == 'expired' and item['effective_plan']['status'] == 'expired')
                return self.body({'items': [item] if matches else [], 'total': 1 if matches else 0, 'page': 1, 'page_size': 20})
            if path.endswith('/stats'):
                return self.body({'total': 1, 'active': 1, 'suspended': 0, 'archived': 0, 'erased': 0})
            if path == ADMIN_PATH:
                return self.body({'account': account(session), 'history': []})
            if path == PLAN_PATH:
                return self.body(envelope(session))
        if session and path == base.BASE + '/appointment-communications':
            return self.body({'available': True, 'canonical_channel_id': None, 'notifications': [], 'reviews': [],
                              'return_services': [], 'settings': {'version': 1, 'signature': 'Empresa sintética',
                              'review_delay_min': 60, 'confirmation': True, 'cancellation': True, 'rescheduling': True,
                              'reminder': True, 'review': True}})
        if session and path == base.BASE + '/professionals':
            return self.body([{'id': f'60000000-0000-4000-8000-{i:012d}', 'name': f'Profissional sintético {i}',
                               'active': True, 'accepts_appointments': True, 'commission_config_version': 1,
                               'commission_mode': 'percent', 'commission_percent': 10, 'color': '#31ad52'}
                              for i in range(1, session['staff_count'] + 1)])
        if session and path in {base.BASE + '/services', base.BASE + '/professional_services'}:
            return self.body([])
        if session and path.startswith(base.BASE + '/management/finance'):
            if path.endswith('/summary'):
                return self.body({'summary': {'income_cents': 0, 'expense_cents': 0, 'balance_cents': 0, 'count': 0}})
            return self.body({'items': [], 'total': 0, 'summary': {}})
        if session and path.startswith(base.BASE + '/management/ai/'):
            return self.body({'configured': False, 'enabled': False, 'items': [], 'daily_limit': 0, 'remaining_today': 0})
        if session and path.startswith((base.BASE + '/management/performance', base.BASE + '/management/marketing')):
            return self.body({'items': [], 'total': 0, 'configuration': {}, 'version': 1, 'available': False})
        return super().do_GET()

    def mutate(self):
        session = self.session()
        path = urlsplit(self.path).path
        company_id = next((key for key in (session or {}).get('company_plans', {})
                           if path == '/api/v1/admin/accounts/companies/' + key + '/plan'), None)
        if not session or path not in {PLAN_PATH, '/__fixture/plan'} and not company_id:
            return self.reject(session, 'Only an explicit synthetic plan mutation is allowed in this fixture')
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 <= size <= 12000:
                raise ValueError()
            payload = json.loads(self.rfile.read(size) or b'{}')
            if not isinstance(payload, dict):
                raise ValueError()
        except (ValueError, json.JSONDecodeError):
            return self.body({'detail': 'Invalid synthetic request'}, 400)
        session['calls'].append({'method': self.command, 'path': path, 'data': payload})
        record = session['company_plans'][company_id] if company_id else session
        before = state(record)
        if path == '/__fixture/plan':
            if self.command != 'POST' or payload.get('plan') not in {*CODES, 'none'}:
                return self.reject(session, 'Unknown isolated plan state')
            session['plan'] = payload['plan'];session['plan_source'] = 'manual';session['plan_version'] += 1
            return self.body(envelope(session))
        if session['role'] != 'admin' or self.headers.get('X-Orion-CSRF') != session['csrf']:
            return self.body({'detail': 'Synthetic authority/version conflict'}, 409)
        replay = record.setdefault('plan_operations', {}).get(payload.get('operation_id'))
        if replay:
            if replay['payload'] != payload:
                return self.body({'detail': 'Synthetic operation changed'}, 409)
            return self.body({**envelope(session, company_id), 'replayed': True})
        if payload.get('expected_version') != before['version'] or payload.get('expected_revision') != before['revision']:
            return self.body({'detail': 'Synthetic authority/version conflict'}, 409)
        if self.command == 'PUT' and payload.get('plan_code') in CODES and payload.get('confirmation') == 'ALTERAR PLANO':
            record['plan'] = payload['plan_code'];record['plan_source'] = 'manual'
            record['plan_expires'] = payload.get('expires_at')
        elif self.command == 'DELETE' and payload.get('confirmation') == 'REMOVER ATRIBUIÇÃO':
            record['plan'] = 'essencial' if record['commercial'] else 'none';record['plan_source'] = 'cakto' if record['commercial'] else 'none';record['plan_expires'] = None
        else:
            return self.reject(session, 'Unknown synthetic manual operation')
        record['plan_version'] += 1
        record['plan_history'].append({'action': 'manual_remove' if self.command == 'DELETE' else 'manual_assign',
            'previous_plan': before, 'new_plan': state(record), 'actor_id': base.OWNER,
            'reason': payload.get('reason'), 'access_type': payload.get('access_type'),
            'expires_at': payload.get('expires_at'), 'occurred_at': datetime.now(timezone.utc).isoformat()})
        result = envelope(session, company_id)
        record['plan_operations'][payload.get('operation_id')] = {'payload': copy.deepcopy(payload)}
        return self.body(result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8896)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        raise SystemExit('Use an unprivileged loopback port')
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    print(f'Isolated plans preview: http://127.0.0.1:{args.port}/panel?fixtureRole=admin&fixturePlan=essencial&aba=member', flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
