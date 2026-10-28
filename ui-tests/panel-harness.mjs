import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import { JSDOM, VirtualConsole } from 'jsdom';
import { defaultResponse, flush, tenantA } from './harness.mjs';

// The complete shipped DOM and both controllers run here. Only browser
// primitives and HTTP responses are synthetic; no request reaches a network.
export const jsonResponse = (body, status = 200) => new Response(
  status === 204 ? null : JSON.stringify(body),
  { status, headers: { 'content-type': 'application/json' } },
);

export async function makePanel({ role = 'support', tenantRole = 'owner',
  initialRoute = 'dispositivo', width = 390, deviceStatus = 'starting', respond } = {}) {
  const html = await readFile(new URL('../app/static/panel.html', import.meta.url), 'utf8');
  const suite = await readFile(new URL('../app/static/orion-suite.js', import.meta.url), 'utf8');
  const virtualConsole = new VirtualConsole(), errors = [], calls = [], intervals = new Map();
  virtualConsole.on('jsdomError', error => errors.push(error));
  const dom = new JSDOM(html, { url: 'https://orion.test/panel?aba=' + initialRoute,
    runScripts: 'outside-only', pretendToBeVisual: true, virtualConsole });
  const { window } = dom, { document } = window;
  window.scrollTo = () => {};
  window.HTMLElement.prototype.scrollTo = () => {};
  window.HTMLElement.prototype.scrollIntoView = () => {};
  window.HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', ''); };
  window.HTMLDialogElement.prototype.close = function () { this.removeAttribute('open'); this.dispatchEvent(new window.Event('close')); };
  window.confirm = () => false;
  Object.defineProperty(window.crypto, 'randomUUID', { value: randomUUID, configurable: true });
  Object.defineProperty(window.navigator, 'clipboard', { value: { writeText: async () => {} }, configurable: true });
  Object.defineProperty(window, 'innerWidth', { value: width, configurable: true });
  window.matchMedia = media => {
    const limit = media.match(/\(max-width:\s*(\d+)px\)/);
    return { media, matches: !!limit && width <= Number(limit[1]),
      addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} };
  };
  let intervalId = 0, now = Date.now();
  window.Date.now = () => now;
  window.setInterval = (callback, delay) => { const id = ++intervalId; intervals.set(id, { callback, delay }); return id; };
  window.clearInterval = id => intervals.delete(id);

  const profile = { business_name: 'Empresa sintética', assistant_name: 'Assistente sintética',
    timezone: 'America/Sao_Paulo', categories: [], hours: [], faqs: [], policies: [],
    differentiators: [], objectives: [], capabilities: [], style_rules: {}, autonomy_rules: {} };
  const businesses = [{ id: tenantA, name: profile.business_name, role: tenantRole,
    slug: 'empresa-sintetica', timezone: profile.timezone }];
  function fallback(path) {
    if (path === '/auth/me') return { authenticated: true, csrf: 'synthetic-mobile-csrf', role,
      name: 'Conta sintética', email: 'mobile-test@example.invalid' };
    if (path === '/auth/features') return { registration: false, passwordRecovery: false };
    if (path === '/auth/logout') return { ok: true };
    if (path.startsWith('/api/v1/onboarding/entry')) return { enabled: false, kind: 'dashboard' };
    if (path.endsWith('/management/marketing/execution')) return { available: false };
    if (path === '/api/v1/tenants' || path === '/api/v1/account/businesses') return businesses;
    if (path.endsWith('/business/profile')) return { generation: 0, draft: null, published: null,
      draft_template: profile, supported_capabilities: [], scheduling_timezone: profile.timezone,
      runtime_connected: true, warnings: [], access: { role: tenantRole,
        can_edit_draft: ['owner', 'editor'].includes(tenantRole), can_publish: tenantRole === 'owner' } };
    if (path.endsWith('/business/profile/history')) return [];
    if (path.endsWith('/ai/status')) return { configured: true, preview_enabled: true, audio_preview_enabled: false };
    if (path === '/api/v1/qr/status') return { status: deviceStatus, automaticRepliesEnabled: false };
    if (path === '/api/v1/qr/chats' || path.startsWith('/api/v1/qr/messages?')) return { items: [] };
    return defaultResponse(path);
  }
  window.fetch = async (input, options = {}) => {
    const url = new URL(String(input), window.location.href);
    if (url.origin !== 'https://orion.test') {
      const error = new Error('The isolated harness refused a request to an external origin');
      errors.push(error); throw error;
    }
    const path = url.pathname + url.search, method = options.method || 'GET';
    const data = typeof options.body === 'string' ? JSON.parse(options.body) : options.body;
    const call = { path, method, data, options }; calls.push(call);
    const supplied = await respond?.(path, method, data, options);
    if (method !== 'GET' && supplied === undefined && path !== '/auth/logout') {
      const error = new Error('An explicit synthetic response is required for ' + method + ' ' + path);
      errors.push(error); throw error;
    }
    return supplied instanceof Response ? supplied : jsonResponse(supplied === undefined ? fallback(path) : supplied);
  };

  const inline = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)];
  assert.equal(inline.length, 2, 'theme bootstrap and the existing panel controller must remain present');
  window.eval(inline[0][1]);
  for (const file of ['orion-fields.js','orion-onboarding.js','orion-channels.js','orion-automations.js'])
    window.eval(await readFile(new URL('../app/static/'+file, import.meta.url), 'utf8'));
  window.eval(suite);
  window.eval(inline[1][1]);
  await flush();
  function element(selector) { const node = document.querySelector(selector); assert.ok(node, 'missing ' + selector); return node; }
  return { dom, window, document, calls, errors, element,
    click(selector) { const node = element(selector); node.click(); return node; },
    fill(selector, value) { const node = element(selector); node.value = value;
      node.dispatchEvent(new window.Event('input', { bubbles: true })); return node; },
    submit(selector) { const node = element(selector);
      node.dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true })); return node; },
    async tickClocks(milliseconds) { now += milliseconds;
      for (const { callback, delay } of [...intervals.values()]) if (delay <= milliseconds) await callback();
      await flush(); },
    close() { window.OrionOnboarding?.unmount();window.OrionChannels?.unmount();window.OrionAutomations?.unmount();window.OrionSuite.destroy(); intervals.clear(); dom.window.close(); },
  };
}
