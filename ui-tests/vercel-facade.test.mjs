import test from 'node:test';
import assert from 'node:assert/strict';
import { makeSuite, flush, defaultResponse, tenantA } from './harness.mjs';

const upstream = 'https://orion-assistente-production.up.railway.app';
const liveSuiteURL = new URL('../app/static/orion-suite.js', import.meta.url);

const disconnected = {
  configured: true, connected: false, sync_enabled: false, sync_status: 'not_connected',
  capabilities: { can_connect: true, can_disconnect: false, can_retry: false },
};

async function fixture(t, { google = disconnected, tenant = tenantA, url = 'https://orion-painel-iota.vercel.app/panel' } = {}) {
  const h = await makeSuite({
    sourceURL: liveSuiteURL, url, initialRoute: 'agenda',
    bridge: { tenantId: tenant, tenants: [{ id: tenant, name: 'Empresa sintética', role: 'owner', timezone: 'America/Sao_Paulo' }] },
    respond(path, method) {
      assert.ok(path.startsWith('/'), 'Only relative, mocked Orion API calls are permitted');
      if (path.endsWith('/google/status')) return google;
      if (path.endsWith('/appointments')) return { items: [], total: 0 };
      if (method === 'POST') return {};
      return defaultResponse(path);
    },
  });
  t.after(() => h.close());
  h.networkAttempts = [];
  h.window.fetch = (...args) => { h.networkAttempts.push(args); throw new Error('Network forbidden in facade DOM test'); };
  h.window.XMLHttpRequest.prototype.open = (...args) => { h.networkAttempts.push(args); throw new Error('Network forbidden in facade DOM test'); };
  await flush();
  return h;
}

const writes = h => h.calls.filter(call => call.method !== 'GET');

test('live Vercel facade sends capable Google connect to the original panel for exactly the selected tenant, without OAuth or POST', async t => {
  for (const tenant of [tenantA, 'synthetic\"\'><img src=x onerror=alert(1)>&empresa=other/#?']) {
    const h = await fixture(t, { tenant });
    h.click('[data-os-action=agenda-google]');
    await flush();
    h.click('[data-os-action=google-connect]');
    await flush();
    const dialog = h.document.getElementById('osDialog');
    assert.equal(dialog.open, true);
    assert.match(dialog.textContent, /continue no painel original do Orion/);
    const link = dialog.querySelector('a');
    assert.ok(link);
    const url = new URL(link.href);
    assert.equal(url.origin, upstream);
    assert.equal(url.pathname, '/panel');
    assert.deepEqual([...url.searchParams.entries()], [['aba', 'agenda'], ['empresa', tenant]]);
    assert.equal(url.hash, '');
    assert.equal(link.attributes.length, 2);
    assert.match(link.outerHTML, /&amp;empresa=/);
    assert.equal(dialog.querySelector('form,img,script,[onerror]'), null);
    assert.equal(writes(h).length, 0);
    assert.equal(h.calls.some(call => call.path.includes('/google/connect') || call.path.includes('accounts.google.com')), false);
    assert.deepEqual(h.networkAttempts, []);
    assert.deepEqual(h.errors, []);
  }
});

test('live Vercel facade keeps the capability guard even for a forged Google connect control', async t => {
  const h = await fixture(t, { google: { ...disconnected, capabilities: { can_connect: false } } });
  h.click('[data-os-action=agenda-google]');
  await flush();
  assert.equal(h.document.querySelector('[data-os-action=google-connect]'), null);
  h.click('[data-os-action=close-dialog]');
  const forged = h.document.createElement('button');
  forged.dataset.osAction = 'google-connect';
  h.document.body.append(forged);
  forged.click();
  await flush();
  assert.equal(h.document.getElementById('osDialog').open, false);
  assert.equal(writes(h).length, 0);
  assert.deepEqual(h.networkAttempts, []);
  assert.deepEqual(h.errors, []);
});

test('live Vercel facade preserves explicit revoke and retry confirmation and their original API calls', async t => {
  for (const [action, endpoint, title] of [
    ['google-revoke', '/google/revoke', /Desconectar Google Agenda/],
    ['google-retry', '/google/retry', /Repetir sincronização/],
  ]) {
    const h = await fixture(t, { google: { ...disconnected, connected: true, sync_status: 'attention',
      capabilities: { can_connect: false, can_disconnect: true, can_retry: true } } });
    h.click('[data-os-action=agenda-google]');
    await flush();
    h.click(`[data-os-action=${action}]`);
    await flush();
    assert.match(h.document.getElementById('osDialogTitle').textContent, title);
    assert.ok(h.document.querySelector('[data-os-form=google-action]'));
    assert.equal(h.document.getElementById('osDialog').querySelector('a'), null);
    assert.equal(writes(h).length, 0);
    h.submit('[data-os-form=google-action]');
    await flush();
    assert.equal(writes(h).length, 1);
    assert.equal(writes(h)[0].method, 'POST');
    assert.equal(writes(h)[0].path, `/api/v1/tenants/${tenantA}${endpoint}`);
    assert.deepEqual(JSON.parse(JSON.stringify(writes(h)[0].data)), {});
    assert.deepEqual(h.networkAttempts, []);
    assert.deepEqual(h.errors, []);
  }
});


test('Railway uses the same source while keeping its normal Google OAuth confirmation', async t => {
  const h = await fixture(t, { url: upstream + '/panel' });
  h.click('[data-os-action=agenda-google]');
  await flush();
  h.click('[data-os-action=google-connect]');
  await flush();
  assert.ok(h.document.querySelector('[data-os-form=google-action]'));
  assert.equal(h.document.getElementById('osDialog').querySelector('a'), null);
  assert.equal(writes(h).length, 0);
  assert.deepEqual(h.errors, []);
});
