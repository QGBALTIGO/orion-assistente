"""Loopback-only plans fixture using the shipped panel and existing UI server.

No database, billing client, worker, provider, account or external request is used.
Open /panel?fixtureRole=admin|owner&fixturePlan=none|essencial|profissional|elite.
Only synthetic state in that browser context may change. Unknown writes fail closed.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from http.server import ThreadingHTTPServer
import json
import secrets
from urllib.parse import parse_qs, urlsplit

from app.plan_catalog import CODES, PLAN_FEATURES, RECOVERY_FEATURES, feature_catalog
from scripts import whatsapp_connect_preview as base

ACCOUNT = '90000000-0000-4000-8000-000000000009'
ADMIN_PATH = '/api/v1/admin/accounts/' + ACCOUNT
PLAN_PATH = ADMIN_PATH + '/companies/' + base.TENANT + '/plan'


def state(session):
    code, source = session['plan'], session['plan_source']
    active = code in CODES
    features = sorted((PLAN_FEATURES[code] if active else frozenset()) | RECOVERY_FEATURES)
    commercial = session['commercial']
    manual = ({'plan_code': code, 'started_at': session['plan_started'],
               'expires_at': session.get('plan_expires'), 'active': active} if source == 'manual' else None)
    result = {'code': code if active else None, 'name': CODES[code]['name'] if active else 'Sem plano ativo',
              'source': source if active else 'none', 'status': 'active' if active else 'no_access',
              'active': active, 'valid_until': session.get('plan_expires'),
              'version': session['plan_version'], 'features': features,
              'permissions': {key: key in features for key in sorted(set().union(*PLAN_FEATURES.values(), RECOVERY_FEATURES))},
              'limits': {'max_professionals': CODES[code]['max_professionals'] if active else None,
                         'active_professionals': session['staff_count'],
                         'excess_professionals': max(0, session['staff_count'] - CODES[code]['max_professionals']) if active else 0},
              'manual_assignment': manual, 'commercial_subscription': commercial,
              'trial': None, 'platform_workspace': False}
    result['revision'] = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
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


def envelope(session):
    return {'company': {'id': base.TENANT, 'name': 'Empresa sintética de planos', 'status': 'active',
                        'account_id': ACCOUNT, 'active_professionals': session['staff_count']},
            'effective_plan': state(session), 'history': list(reversed(session['plan_history'])), 'replayed': False}


class Handler(base.Handler):
    def do_GET(self):
        url = urlsplit(self.path)
        path, query = url.path, parse_qs(url.query)
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
                           staff_count=8 if query.get('fixtureExcess') == ['1'] else 2,
                           commercial=({'code': 'orion_essencial', 'name': 'Orion Essencial', 'status': 'active',
                                        'monthly_cents': 2990, 'max_professionals': 2} if plan != 'none' else None))
            token = secrets.token_urlsafe(24)
            with base._lock:
                base._sessions[token] = session
            return self.body((base.ROOT / 'app/static/panel.html').read_bytes(), mime='text/html', cookie=token)
        session = self.session()
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
            if path == '/api/v1/admin/accounts':
                return self.body({'items': [account(session)], 'total': 1, 'page': 1, 'page_size': 20})
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
        if not session or path not in {PLAN_PATH, '/__fixture/plan'}:
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
        before = state(session)
        if path == '/__fixture/plan':
            if self.command != 'POST' or payload.get('plan') not in {*CODES, 'none'}:
                return self.reject(session, 'Unknown isolated plan state')
            session['plan'] = payload['plan'];session['plan_source'] = 'manual';session['plan_version'] += 1
            return self.body(envelope(session))
        if (session['role'] != 'admin' or self.headers.get('X-Orion-CSRF') != session['csrf']
                or payload.get('expected_version') != before['version'] or payload.get('expected_revision') != before['revision']):
            return self.body({'detail': 'Synthetic authority/version conflict'}, 409)
        if self.command == 'PUT' and payload.get('plan_code') in CODES and payload.get('confirmation') == 'ALTERAR PLANO':
            session['plan'] = payload['plan_code'];session['plan_source'] = 'manual'
            session['plan_expires'] = payload.get('expires_at')
        elif self.command == 'DELETE' and payload.get('confirmation') == 'REMOVER ATRIBUIÇÃO':
            session['plan'] = 'essencial' if session['commercial'] else 'none';session['plan_source'] = 'cakto' if session['commercial'] else 'none';session['plan_expires'] = None
        else:
            return self.reject(session, 'Unknown synthetic manual operation')
        session['plan_version'] += 1
        session['plan_history'].append({'action': 'manual_remove' if self.command == 'DELETE' else 'manual_assign',
            'previous_plan': before, 'new_plan': state(session), 'actor_id': base.OWNER,
            'reason': payload.get('reason'), 'access_type': payload.get('access_type'),
            'expires_at': payload.get('expires_at'), 'occurred_at': datetime.now(timezone.utc).isoformat()})
        return self.body(envelope(session))


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
