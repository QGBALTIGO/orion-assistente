import test from 'node:test';
import assert from 'node:assert/strict';
import { makePanel, jsonResponse } from './panel-harness.mjs';
import { makeSuite, tenantA, tenantB, flush, deferred } from './harness.mjs';

const business = { id: tenantA, name: 'Empresa fixa da conta', role: 'owner' };
const scopedReads = calls => calls.filter(call => /^\/api\/v1\/(tenants\/|qr\/)/.test(call.path));

for (const role of ['member', 'support', 'admin']) {
  test(`${role} uses the account context and shows a fixed business name`, async t => {
    const h = await makePanel({ role, initialRoute: 'dashboard', respond(path) {
      if (path === '/api/v1/account/businesses') return [business];
    } }); t.after(() => h.close()); await flush();
    assert.ok(h.calls.some(call => call.path === '/api/v1/account/businesses'));
    assert.equal(h.calls.some(call => call.path === '/api/v1/tenants'), false);
    assert.equal(h.document.querySelector('#osTenant, [data-os-action=business-new], #tenantForm'), null);
    assert.match(h.element('#osSidebarContext').textContent, /Empresa fixa da conta/);
    for (const id of ['businessTenant', 'comTenant', 'aiTenant', 'tenant']) {
      assert.equal(h.element('#' + id).type, 'hidden');
      assert.equal(h.element('#' + id).value, tenantA);
    }
    for (const node of h.document.querySelectorAll('[data-account-business-name]')) {
      assert.equal(node.textContent, business.name);
    }
    assert.equal(h.calls.some(call => call.method !== 'GET'), false);
    assert.equal(h.errors.length, 0);
  });
}

test('a foreign business in the URL never changes the authorized context', async t => {
  const h = await makePanel({ role: 'member', initialRoute: 'dashboard&empresa=' + tenantB });
  t.after(() => h.close()); await flush();
  assert.equal(new URL(h.window.location.href).searchParams.has('empresa'), false);
  assert.equal(h.element('#businessTenant').value, tenantA);
  assert.equal(h.calls.some(call => call.path.includes(tenantB)), false);
  assert.ok(scopedReads(h.calls).some(call => call.path.includes(tenantA)));
});

test('no business or legacy QR data is fetched before account context is confirmed', async t => {
  const pending = deferred();
  const h = await makePanel({ role: 'support', initialRoute: 'dashboard', respond(path) {
    if (path === '/api/v1/account/businesses') return pending.promise;
  } }); t.after(() => h.close());
  assert.equal(scopedReads(h.calls).length, 0);
  assert.ok(h.element('#appView').classList.contains('ob-gated'));
  pending.resolve([business]); await flush();
  assert.ok(scopedReads(h.calls).length > 0);
  assert.equal(h.element('#appView').classList.contains('ob-gated'), false);
});

for (const response of [
  () => [business, { id: tenantB, name: 'Outro cadastro', role: 'owner' }],
  () => jsonResponse({ detail: 'Sua conta está vinculada a mais de uma empresa.' }, 409),
  () => ({ items: [business] }),
]) {
  test('invalid or ambiguous context blocks data and keeps retry and login available', async t => {
    const h = await makePanel({ role: 'support', initialRoute: 'dashboard', respond(path) {
      if (path === '/api/v1/account/businesses') return response();
    } }); t.after(() => h.close()); await flush();
    assert.ok(h.element('#appView').classList.contains('ob-gated'));
    assert.ok(h.element('#retryBusinessContext'));
    assert.ok(h.element('#businessContextLogout'));
    assert.equal(scopedReads(h.calls).length, 0);
    assert.equal(h.calls.some(call => call.method !== 'GET'), false);
    assert.equal(h.document.querySelector('#osTenant, [data-ob-action=choose-business]'), null);
    assert.equal(h.element('#businessTenant').value, '');
    assert.equal(h.errors.length, 0);
  });
}

test('retry resolves the account context before loading data and without creating a business', async t => {
  let attempts = 0;
  const h = await makePanel({ role: 'support', initialRoute: 'dashboard', respond(path) {
    if (path === '/api/v1/account/businesses') return ++attempts === 1
      ? jsonResponse({ detail: 'Consulta temporariamente indisponível' }, 503) : [business];
  } }); t.after(() => h.close()); await flush();
  assert.equal(scopedReads(h.calls).length, 0);
  h.click('#retryBusinessContext'); await flush();
  assert.equal(attempts, 2);
  assert.equal(h.element('#appView').classList.contains('ob-gated'), false);
  assert.equal(h.element('#businessTenant').value, tenantA);
  assert.ok(scopedReads(h.calls).length > 0);
  assert.equal(h.calls.some(call => call.method !== 'GET'), false);
});

test('logging out discards an in-flight business response before it can load data', async t => {
  const pending = deferred();
  const h = await makePanel({ role: 'support', respond(path) {
    if (path === '/api/v1/account/businesses') return pending.promise;
  } }); t.after(() => h.close());
  h.click('#logout'); await flush();
  pending.resolve([business]); await flush();
  assert.ok(h.element('#appView').classList.contains('hidden'));
  assert.equal(scopedReads(h.calls).length, 0);
  assert.equal(h.element('#businessTenant').value, '');
  assert.equal(h.errors.length, 0);
});

test('the suite itself rejects a multiple-business bridge without picking the first', async t => {
  const h = await makeSuite({ bridge: { tenants: [business, { id: tenantB, role: 'owner' }], tenantId: tenantA } });
  t.after(() => h.close()); await flush();
  assert.match(h.document.getElementById('page-suite').textContent, /mais de uma empresa/);
  assert.equal(h.calls.length, 0);
  assert.equal(h.document.querySelector('#osTenant, [data-os-action=business-new]'), null);
});
