import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { JSDOM } from 'jsdom';
import { makeSuite, flush, deferred, defaultResponse, customer, customerId, tenantA, tenantB } from './harness.mjs';

test('mount preserves original sections, escapes customer content, and is idempotent', async t => {
  let created = false;
  const h = await makeSuite({ respond: (path, method) => {
    if (path.includes('/management/customers') && method === 'GET') return { items: [customer('<img src=x onerror=alert(1)>')], total: 1 };
    if (method === 'POST') { created = true; return customer('Novo cliente'); }
    return defaultResponse(path);
  } }); t.after(() => h.close()); await flush();
  assert.ok(h.document.getElementById('page-original'));
  assert.match(h.document.getElementById('page-suite').textContent, /<img src=x onerror=alert\(1\)>/);
  assert.equal(h.document.querySelector('#page-suite img[src=x]'), null);
  h.window.OrionSuite.mount(h.bridge); await flush();
  assert.equal(h.document.querySelectorAll('#osHeader').length, 1);
  h.click('[data-os-action=new-customer]'); h.fill('#osField-name', 'Novo cliente');
  h.submit('[data-os-form=customer]'); await flush();
  assert.equal(created, true);
  assert.equal(h.calls.filter(c => c.method === 'POST').length, 1, 'mount must not duplicate document submit handlers');
  assert.equal(h.errors.length, 0);
});

test('late tenant A response cannot replace tenant B after an authorized switch', async t => {
  const pending = deferred();
  const h = await makeSuite({ respond: path => {
    if (path.includes(`/tenants/${tenantA}/management/customers`)) return pending.promise;
    if (path.includes(`/tenants/${tenantB}/management/customers`)) return { items: [customer('Cliente da empresa B')], total: 1 };
    return defaultResponse(path);
  } }); t.after(() => h.close());
  h.window.OrionSuite.sync({ tenantId: tenantB }); await flush();
  assert.match(h.document.getElementById('page-suite').textContent, /Cliente da empresa B/);
  pending.resolve({ items: [customer('Cliente antigo da empresa A')], total: 1 }); await flush();
  assert.doesNotMatch(h.document.getElementById('page-suite').textContent, /Cliente antigo da empresa A/);
  assert.equal(h.document.getElementById('osTenant').value, tenantB);
  h.window.OrionSuite.destroy();
  assert.equal(h.document.getElementById('page-suite').childElementCount, 0);
});

test('viewer can inspect customers but cannot submit mutations', async t => {
  const h = await makeSuite({ role: 'viewer', respond: path => path.includes('/management/customers') ? { items: [customer()], total: 1 } : defaultResponse(path) }); t.after(() => h.close()); await flush();
  assert.equal(h.document.querySelector('[data-os-action=new-customer]').disabled, true);
  assert.equal(h.document.querySelector('[data-os-customer-delete]'), null);
  h.click(`[data-os-customer="${customerId}"]`); await flush();
  const form = h.document.querySelector('[data-os-form=customer]');
  assert.ok(form, 'the existing record remains inspectable');
  assert.equal(form.querySelector('fieldset').disabled, true);
  assert.equal(form.querySelector('button[type=submit]').disabled, true);
  form.dispatchEvent(new h.window.Event('submit', { bubbles: true, cancelable: true })); await flush();
  assert.deepEqual(h.calls.filter(c => c.method !== 'GET'), []);
});

test('customer deletion requires concrete confirmation and accepts an empty 204 result', async t => {
  let deleted = false;
  const h = await makeSuite({ respond: (path, method) => {
    if (method === 'DELETE') { deleted = true; return null; }
    if (path.includes('/management/customers')) return { items: deleted ? [] : [customer()], total: deleted ? 0 : 1 };
    return defaultResponse(path);
  } }); t.after(() => h.close()); await flush();
  h.click(`[data-os-customer-delete="${customerId}"]`); await flush();
  assert.equal(h.calls.some(c => c.method === 'DELETE'), false);
  assert.match(h.document.getElementById('osDialog').textContent, /Cliente sintético/);
  h.submit('[data-os-form=confirm]'); await flush();
  const requests = h.calls.filter(c => c.method === 'DELETE');
  assert.equal(requests.length, 1);
  assert.equal(requests[0].path, `/api/v1/tenants/${tenantA}/management/customers/${customerId}`);
  assert.equal(requests[0].data.expected_version, 1);
  assert.match(h.document.getElementById('page-suite').textContent, /Nenhum cliente ainda/);
});

test('consultant loads status/history without generation and requires an explicit submit', async t => {
  const h = await makeSuite({ initialRoute: 'consultor', respond: (path, method) => method === 'POST' ? { output: { answer: 'Resposta sintética sem envio.', actions: [] }, sources: [] } : defaultResponse(path) }); t.after(() => h.close()); await flush();
  assert.equal(h.calls.filter(c => c.method === 'POST').length, 0);
  assert.ok(h.document.getElementById('osConsultorQuestion'));
  h.click('[data-os-consultor-prompt="0"]'); await flush();
  assert.equal(h.calls.filter(c => c.method === 'POST').length, 0, 'suggestion buttons only populate the question');
  assert.ok(h.document.getElementById('osConsultorQuestion').value.length >= 3);
  h.submit('[data-os-form=ai-consultant]'); await flush();
  const calls = h.calls.filter(c => c.method === 'POST'); assert.equal(calls.length, 1);
  assert.equal(calls[0].path, `/api/v1/tenants/${tenantA}/management/ai/consultant`);
  assert.ok(calls[0].data.idempotency_key);
  assert.match(h.document.getElementById('osConsultorMessages').textContent, /Resposta sintética sem envio/);
});

test('invitation replay with null link never fabricates or exposes a link', async t => {
  const h = await makeSuite({ initialRoute: 'funcionarios', respond: (path, method) => method === 'POST' ? { link: null, expires_at: '2030-01-01T12:00:00Z', status: 'pending' } : defaultResponse(path) }); t.after(() => h.close()); await flush();
  h.click('[data-os-action=new-invitation]'); h.submit('[data-os-form=invitation]'); await flush();
  const dialog = h.document.getElementById('osDialog');
  assert.match(dialog.textContent, /já havia sido criado|exibido apenas uma vez/);
  assert.equal(dialog.querySelector('[aria-label="Link do convite"]'), null);
  assert.equal(dialog.querySelector('[data-os-action=copy-invitation]'), null);
  assert.equal(h.calls.filter(c => c.method === 'POST').length, 1);
});

test('approving a claimed invitation waits for an explicit confirmation and includes version', async t => {
  const invitationId = '40000000-0000-4000-8000-000000000004';
  const invitation = { id: invitationId, role: 'editor', status: 'claimed', claimed_by_name: 'Pessoa sintética', expires_at: '2030-01-01T12:00:00Z', version: 3 };
  const h = await makeSuite({ initialRoute: 'funcionarios', respond: path => path.endsWith('/management/team') ? { can_manage: true, members: [], invitations: [invitation] } : defaultResponse(path) }); t.after(() => h.close()); await flush();
  h.click(`[data-os-action=approve-invitation][data-invitation="${invitationId}"]`); await flush();
  assert.equal(h.calls.filter(c => c.method === 'PATCH').length, 0);
  assert.match(h.document.getElementById('osDialog').textContent, /Pessoa sintética/);
  h.submit('[data-os-form=confirm]'); await flush();
  const writes = h.calls.filter(c => c.method === 'PATCH');
  assert.equal(writes.length, 1); assert.equal(writes[0].data.action, 'approve'); assert.equal(writes[0].data.expected_version, 3);
});

test('actual panel API returns null for HTTP 204 without parsing JSON', async t => {
  const html = await readFile(new URL('../app/static/panel.html', import.meta.url), 'utf8');
  const actual = html.match(/async function api\(url,method="GET",data\)\{[\s\S]*?\n\}/)?.[0];
  assert.ok(actual, 'read the real API adapter from panel.html');
  const dom = new JSDOM('', { url: 'https://orion.test/panel', runScripts: 'outside-only' }); t.after(() => dom.window.close());
  let options, jsonRead = false;
  dom.window.fetch = async (path, value) => { options = value; return { ok: true, status: 204, json: async () => { jsonRead = true; throw new Error('204 has no JSON body'); } }; };
  dom.window.eval(`var authEpoch=1, csrf='test-csrf'; function errorMessage(){return 'test error';} function showLogin(){}; ${actual}`);
  assert.equal(await dom.window.api('/api/v1/tenants/test/services/id', 'DELETE'), null);
  assert.equal(jsonRead, false); assert.equal(options.headers['X-Orion-CSRF'], 'test-csrf'); assert.equal(options.credentials, 'same-origin');
});
