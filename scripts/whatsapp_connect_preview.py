"""Loopback-only WhatsApp UI fixture. No database, provider or real session.

Run: python -m scripts.whatsapp_connect_preview --port 8893
Open /panel?fixtureRole=admin|owner|reader&fixtureState=disconnected|logged_out|connected
Each browser context owns a synthetic cookie; use contexts, not concurrent tabs,
when checking different roles. Unknown API operations fail closed with HTTP 403.
"""
from __future__ import annotations

import argparse
import base64
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import re
import secrets
import socket
import struct
import threading
import time
from urllib.parse import parse_qs, urlsplit
import zlib

from scripts.plan_fixture import synthetic_plan_state

ROOT = Path(__file__).resolve().parents[1]
TENANT = '10000000-0000-4000-8000-000000000001'
CHANNEL = '30000000-0000-4000-8000-000000000003'
HISTORICAL_CHANNEL = '30000000-0000-4000-8000-000000000004'
OWNER = '50000000-0000-4000-8000-000000000005'
BASE = '/api/v1/tenants/' + TENANT
ROLES = {'admin', 'owner', 'reader'}
STATES = {'disconnected', 'logged_out', 'connected', 'connected_unverified', 'expired', 'error', 'uncertain', 'qr_expired', 'qr_delayed'}
CONNECTIONS = {'existing', 'missing', 'legacy', 'unsafe_legacy', 'history', 'ambiguous'}
COOKIE = 'orion_whatsapp_fixture'
_sessions: dict[str, dict] = {}
_lock = threading.Lock()


def synthetic_png() -> str:
    """A visible, deliberately nonfunctional square pattern, not a WhatsApp QR."""
    size = 232
    rows = []
    for y in range(size):
        row = bytearray([0])
        for x in range(size):
            xx, yy = x // 8, y // 8
            ink = 2 < xx < 26 and 2 < yy < 26 and (xx * 7 + yy * 3 + xx * yy) % 5 < 2
            for fx, fy in ((3, 3), (19, 3), (3, 19)):
                if fx <= xx < fx + 7 and fy <= yy < fy + 7:
                    dx, dy = xx - fx, yy - fy
                    ink = dx in (0, 6) or dy in (0, 6) or 2 <= dx <= 4 and 2 <= dy <= 4
            row.extend((20, 35, 55) if ink else (255, 255, 255))
        rows.append(bytes(row))
    def chunk(kind, data):
        return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind + data))
    raw = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B', size, size, 8, 2, 0, 0, 0))
    raw += chunk(b'IDAT', zlib.compress(b''.join(rows))) + chunk(b'IEND', b'')
    return 'data:image/png;base64,' + base64.b64encode(raw).decode()


PNG = synthetic_png()


def make_session(role, state, connection='existing'):
    status = 'starting' if state in {'disconnected', 'expired', 'uncertain'} else 'connected' if state == 'connected_unverified' else state
    return {'role': role, 'scenario': state, 'status': status,
            'enabled': state in {'logged_out', 'connected', 'connected_unverified'}, 'assistant_enabled': False, 'revision': 1,
            'csrf': 'synthetic-whatsapp-' + secrets.token_hex(12), 'calls': [], 'unexpected': [], 'dropped': False,
            'connection': connection, 'exists': connection != 'missing',
            'assistant_activation_allowed': connection != 'missing',
            'ready_at': time.monotonic() + .05}


def channel(session):
    can = session['role'] in {'admin', 'owner'} and not (session['connection'] == 'legacy' and session['role'] != 'admin')
    legacy = session['connection'] == 'legacy'
    return {'id': CHANNEL, 'tenant_id': TENANT, 'owner_account_id': OWNER, 'provider': 'baileys',
            'label': 'WhatsApp sintético da empresa', 'enabled': session['enabled'],
            'assistant_enabled': session['assistant_enabled'], 'revision': session['revision'],
            'can_manage': can, 'legacy_transport': legacy,
            'assistant_activation_allowed': can and session['assistant_activation_allowed'],
            'capabilities': {'start': can, 'stop': can and not legacy, 'qr': can, 'pair': can,
                             'change_state': can and not legacy,
                             'change_assistant': can and session['assistant_activation_allowed'],
                             'prepare': can and legacy and session['role'] == 'admin',
                             'recover': can and legacy and session['role'] == 'admin'}}


def connection_listing(session):
    can = session['role'] in {'admin', 'owner'} and not (session['connection'] == 'legacy' and session['role'] != 'admin')
    state = 'ambiguous' if session['connection'] in {'ambiguous', 'unsafe_legacy'} else 'ready' if session['exists'] else 'missing'
    connection = channel(session) if state == 'ready' else None
    capabilities = connection['capabilities'] if connection else dict.fromkeys(
        ('start', 'stop', 'qr', 'pair', 'change_state', 'change_assistant'), False)
    return {'connection': connection, 'canonical_id': CHANNEL if connection else None, 'canonical_state': state,
            'items': [connection] if connection else [], 'access': {'can_manage': can},
            'capabilities': {**capabilities, 'provision': can and state == 'missing',
                             'edit_label': False, 'edit_allowlist': False}}


def runtime(session, scoped=False):
    if not scoped and session['status'] == 'starting' and session['ready_at'] <= time.monotonic():
        session['status'] = 'qr_ready'
    if session['status'] == 'connecting' and session.get('ready_at', float('inf')) <= time.monotonic():
        # Models an internal provider readiness event, without requesting a QR image.
        session['status'] = 'qr_ready'
    if session.get('pair_expires_at', float('inf')) <= time.monotonic() and session['status'] == 'pairing':
        session['status'] = 'pair_expired'
    status = session['status']
    if scoped:
        status = 'disabled' if not session['enabled'] else 'stopped' if status == 'starting' else status
        verified = status == 'connected' and session['scenario'] != 'connected_unverified'
        return {'channel_id': CHANNEL, 'revision': session['revision'], 'enabled': session['enabled'],
                'assistant_enabled': session['assistant_enabled'], 'status': status,
                'phone': '5511900000001' if verified else None, 'phone_verified': verified,
                'capabilities': {'pair': session['connection'] != 'legacy' and session['enabled'] and status in {'connecting', 'qr_ready', 'pairing', 'pair_expired'}}}
    return {'status': status, 'phone': '5511900000001' if status == 'connected' else None,
            'ingestion': 'receiving' if status == 'connected' else 'inactive', 'automaticRepliesEnabled': False,
            'profile': {'name': 'Conta WhatsApp sintética', 'phone': '5511900000001', 'image': None},
            'messageStats': {'available': False}, 'featureSettings': {}, 'featureCapabilities': {}}


class Handler(BaseHTTPRequestHandler):
    server_version = 'OrionIsolatedUI/1.0'

    def log_message(self, *_):
        pass

    def body(self, value, status=200, mime='application/json', cookie=None):
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False).encode()
        elif isinstance(value, str):
            value = value.encode()
        self.send_response(status)
        self.send_header('Content-Type', mime + ('; charset=utf-8' if mime.startswith(('text/', 'application/json')) else ''))
        self.send_header('Content-Length', str(len(value)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-src 'none'; object-src 'none'; form-action 'self'")
        if cookie:
            self.send_header('Set-Cookie', COOKIE + '=' + cookie + '; Path=/; HttpOnly; SameSite=Strict')
        self.end_headers()
        self.wfile.write(value)

    def session(self):
        cookies = SimpleCookie()
        try:
            cookies.load(self.headers.get('Cookie', ''))
        except Exception:
            return None
        token = cookies.get(COOKIE)
        return _sessions.get(token.value) if token else None

    def reject(self, session, reason='No explicit synthetic response for this operation'):
        if session is not None:
            session['unexpected'].append({'method': self.command, 'path': urlsplit(self.path).path, 'reason': reason})
        return self.body({'detail': reason + '. No real operation was performed.'}, 403)

    def do_GET(self):
        url = urlsplit(self.path)
        path, query = url.path, parse_qs(url.query)
        if path in {'/', '/panel'}:
            role = query.get('fixtureRole', ['admin'])[0]
            state = query.get('fixtureState', ['disconnected'])[0]
            connection = query.get('fixtureConnection', ['existing'])[0]
            if role not in ROLES or state not in STATES or connection not in CONNECTIONS:
                return self.body({'detail': 'Unknown synthetic role/state'}, 400)
            token = secrets.token_urlsafe(24)
            with _lock:
                _sessions[token] = make_session(role, state, connection)
            return self.body((ROOT / 'app/static/panel.html').read_bytes(), mime='text/html', cookie=token)
        if path.startswith(('/panel-assets/', '/brand/')):
            name = path.rsplit('/', 1)[-1]
            if not re.fullmatch(r'[a-zA-Z0-9_.-]+', name):
                return self.body({'detail': 'Invalid asset'}, 403)
            if name == 'orion-suite-live.js':
                name = 'orion-suite.js'
            source = ROOT / 'app/static' / name
            if not source.is_file() or source.suffix not in {'.js', '.css', '.svg', '.woff2', '.png', '.webp'}:
                return self.body({'detail': 'Unknown fixture asset'}, 404)
            return self.body(source.read_bytes(), mime=mimetypes.guess_type(name)[0] or 'application/octet-stream')
        if path == '/favicon.ico':
            return self.body(b'', 204, 'image/x-icon')
        session = self.session()
        if session is None:
            return self.reject(None, 'Open the synthetic panel URL first')
        if path == '/__fixture/calls':
            return self.body({'role': session['role'], 'scenario': session['scenario'],
                              'connection': session['connection'],
                              'stored_connection_ids': ([CHANNEL] if session['exists'] else []) +
                                  ([HISTORICAL_CHANNEL] if session['connection'] in {'history', 'ambiguous'} else []),
                              'calls': session['calls'], 'unexpected': session['unexpected']})
        session['calls'].append({'method': 'GET', 'path': self.path})
        role = session['role']
        tenant_role = 'viewer' if role == 'reader' else 'owner'
        business = {'id': TENANT, 'name': 'Empresa sintética de interface', 'role': tenant_role,
                    'slug': 'empresa-sintetica', 'timezone': 'America/Cuiaba'}
        if path == '/auth/me':
            return self.body({'authenticated': True, 'role': 'admin' if role == 'admin' else 'member',
                              'name': 'Conta sintética', 'email': 'synthetic@example.invalid',
                              'account': role != 'admin', 'email_verified': True, 'csrf': session['csrf']})
        if path == '/auth/features':
            return self.body({'registration': False, 'passwordRecovery': False})
        if path in {'/api/v1/account/businesses', '/api/v1/tenants'}:
            return self.body([business])
        if path == '/api/v1/onboarding/entry':
            return self.body({'enabled': False, 'kind': 'dashboard'})
        if path == '/api/v1/account/business-identity':
            return self.body({'name': business['name'], 'contact_phone': '+5511900000001'})
        if path == BASE + '/business/profile':
            return self.body({'generation': 0, 'draft': None, 'published': None,
                              'draft_template': {'business_name': business['name'], 'assistant_name': 'Recepção sintética', 'timezone': 'America/Cuiaba'},
                              'supported_capabilities': [], 'scheduling_timezone': 'America/Cuiaba', 'runtime_connected': False,
                              'warnings': [], 'access': {'role': tenant_role, 'can_edit_draft': role != 'reader', 'can_publish': role != 'reader'}})
        if path in {BASE + '/business/profile/history', BASE + '/ai/history'}:
            return self.body([])
        if path == BASE + '/ai/status':
            return self.body({'configured': False, 'enabled': False, 'remaining_today': 0, 'daily_limit': 0})
        if path == BASE + '/management/dashboard':
            return self.body({'summary': {'appointments': 0, 'completed': 0, 'customers': 0}})
        if path == BASE + '/management/permissions':
            return self.body({'effective_plan': synthetic_plan_state(TENANT)})
        if path == BASE + '/management/preferences':
            return self.body({'version': 1, 'can_save': role != 'reader', 'business': {
                'contact_phone': '+5511900000001', 'trade_name': business['name'], 'legal_name': business['name'],
                'tax_document_kind': 'none', 'tax_document': None, 'monthly_goal_cents': 0,
                'auto_review_requested': False, 'weekly_summary_requested': False}})
        if path in {BASE + '/appointments', BASE + '/management/goals', BASE + '/management/notifications', BASE + '/management/customers'}:
            return self.body({'items': [], 'total': 0, 'templates': [], 'unread_count': 0, 'can_mark_read': False})
        if path == BASE + '/management/finance/summary':
            return self.body({'summary': {'income_cents': 0, 'expense_cents': 0, 'balance_cents': 0, 'count': 0}})
        if path == BASE + '/management/marketing/execution':
            return self.body({'available': False})
        if path == BASE + '/qr-channels':
            return self.body(connection_listing(session))
        if path.startswith('/api/v1/qr/'):
            if role != 'admin':
                return self.reject(session, 'Global connector is admin-only')
            if path == '/api/v1/qr/status':
                return self.body(runtime(session))
            if path == '/api/v1/qr/image':
                if session['status'] in {'logged_out', 'pair_failed'}:
                    return self.body({'status': session['status'], 'image': None, 'expiresIn': 0})
                session['status'] = 'qr_ready'
                return self.body({'status': 'qr_ready', 'image': PNG, 'expiresIn': 1 if session['scenario'] == 'expired' else 45})
            if path in {'/api/v1/qr/chats', '/api/v1/qr/messages'}:
                return self.body({'items': []})
        if path.startswith(BASE + '/qr-channels/' + CHANNEL + '/'):
            if role == 'reader' or session['connection'] == 'legacy' and role != 'admin':
                return self.reject(session, 'Channel operation is owner-only')
            if not session['exists'] or session['connection'] in {'ambiguous', 'unsafe_legacy'}:
                return self.reject(session, 'A canonical connection is required')
            action = path.rsplit('/', 1)[-1]
            if action == 'status':
                return self.body(runtime(session, True))
            if action == 'qr':
                if not session['enabled']:
                    return self.body({'detail': 'Synthetic device is disabled'}, 409)
                if session['status'] in {'pairing', 'connected', 'logged_out', 'pair_failed'}:
                    return self.body({'channel_id': CHANNEL, 'revision': session['revision'], 'image': None, 'expires_in_ms': 0})
                session['status'] = 'qr_ready'
                return self.body({'channel_id': CHANNEL, 'revision': session['revision'], 'image': PNG,
                                  'expires_in_ms': 1000 if session['scenario'] == 'expired' else 45000})
        return self.reject(session)

    def do_POST(self):
        return self.mutate()

    def do_PATCH(self):
        return self.mutate()

    def do_PUT(self):
        return self.mutate()

    def do_DELETE(self):
        return self.mutate()

    def mutate(self):
        session = self.session()
        if session is None:
            return self.reject(None, 'Missing synthetic context')
        path = urlsplit(self.path).path
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 <= length <= 20000:
                raise ValueError('body size')
            payload = json.loads(self.rfile.read(length) or b'{}')
            if not isinstance(payload, dict):
                raise ValueError('body')
        except (ValueError, json.JSONDecodeError):
            return self.reject(session, 'Invalid synthetic body')
        session['calls'].append({'method': self.command, 'path': path,
                                 'data': {key: ('[synthetic phone omitted]' if key == 'phone' else value)
                                          for key, value in payload.items()}})
        if self.headers.get('X-Orion-CSRF') != session['csrf']:
            return self.reject(session, 'Missing synthetic CSRF')
        if session['role'] == 'reader':
            return self.reject(session, 'Read-only synthetic account')
        if session['connection'] == 'legacy' and session['role'] != 'admin':
            return self.reject(session, 'Preserved legacy connection is native-admin-only')
        global_action = path.removeprefix('/api/v1/qr/') if path.startswith('/api/v1/qr/') else None
        scoped_action = path.removeprefix(BASE + '/qr-channels/' + CHANNEL + '/') if path.startswith(BASE + '/qr-channels/' + CHANNEL + '/') else None
        if global_action and session['role'] != 'admin':
            return self.reject(session, 'Global connector is admin-only')
        if self.command == 'POST' and path == BASE + '/qr-channels/provision':
            if set(payload) != {'idempotency_key'} or not isinstance(payload['idempotency_key'], str) or not re.fullmatch(r'[a-fA-F0-9-]{36}', payload['idempotency_key']):
                return self.reject(session, 'Provision requires only its synthetic idempotency key')
            if session['connection'] in {'ambiguous', 'unsafe_legacy'}:
                return self.body({'detail': 'A conexão existente precisa de revisão antes de continuar.'}, 409)
            if not session['exists']:
                session.update(exists=True, enabled=False, assistant_enabled=False, revision=1,
                               assistant_activation_allowed=False, status='starting')
            return self.body(connection_listing(session))
        if path.startswith(BASE + '/qr-channels/' + CHANNEL) and (
                not session['exists'] or session['connection'] in {'ambiguous', 'unsafe_legacy'}):
            return self.reject(session, 'A canonical connection is required')
        if self.command == 'PATCH' and path == BASE + '/qr-channels/' + CHANNEL:
            if payload.get('expected_revision') != session['revision']:
                return self.body({'detail': 'Synthetic revision changed'}, 409)
            if set(payload) - {'expected_revision', 'enabled', 'assistant_enabled'}:
                return self.reject(session)
            for key in ('enabled', 'assistant_enabled'):
                if key in payload:
                    if not isinstance(payload[key], bool):
                        return self.reject(session)
                    if key == 'assistant_enabled' and payload[key] and not session['assistant_activation_allowed']:
                        return self.body({'detail': 'A configuração de atendimento precisa ser revisada antes de ativar.'}, 409)
                    session[key] = payload[key]
            session['revision'] += 1
            return self.body(channel(session))
        if self.command != 'POST' or (global_action not in {'pair', 'reconnect', 'recover'} and scoped_action not in {'start', 'stop', 'pair'}):
            return self.reject(session)
        if scoped_action and payload.get('expected_revision') != session['revision']:
            return self.body({'detail': 'Synthetic revision changed'}, 409)
        action = global_action or scoped_action
        if session['scenario'] == 'error':
            return self.body({'detail': 'Falha sintética. Nenhum serviço real foi chamado.'}, 503)
        if action == 'pair':
            if not re.fullmatch(r'[1-9]\d{7,14}', str(payload.get('phone', ''))):
                return self.body({'detail': 'Informe um telefone completo.'}, 422)
            if scoped_action and (not session['enabled'] or session['status'] == 'stopped'):
                return self.body({'detail': 'Inicie este dispositivo explicitamente antes de solicitar código.'}, 409)
            if session['status'] in {'logged_out', 'pair_failed'}:
                return self.body({'detail': 'Sessão sintética desvinculada. Refazer vínculo é necessário.'}, 409)
            session['status'] = 'pairing'
            expiry = 1000 if session['scenario'] == 'expired' else 79000
            session['pair_expires_at'] = time.monotonic() + expiry / 1000
            if session['scenario'] == 'uncertain' and not session['dropped']:
                session['dropped'] = True
                self.close_connection = True
                try:
                    self.connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                self.connection.close()
                return
            if scoped_action:
                return self.body({'channel_id': CHANNEL, 'revision': session['revision'], 'enabled': session['enabled'],
                                  'assistant_enabled': session['assistant_enabled'], 'status': 'pairing',
                                  'code': 'TEST-0001', 'expires_in_ms': expiry})
            return self.body({'status': 'pairing', 'code': 'TEST-0001', 'expiresIn': expiry // 1000})
        session['status'] = 'stopped' if action == 'stop' else 'starting' if global_action else 'connecting'
        if scoped_action == 'start' or global_action:
            session['ready_at'] = time.monotonic() + .05
        return self.body(runtime(session, scoped_action is not None))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8893)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        raise SystemExit('Use an unprivileged loopback port')
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    print(f'Isolated WhatsApp fixture: http://127.0.0.1:{server.server_port}/panel?fixtureRole=admin&fixtureState=disconnected&aba=dispositivo', flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
