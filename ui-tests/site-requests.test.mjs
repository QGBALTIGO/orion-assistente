import test from 'node:test';
import assert from 'node:assert/strict';
import { makeSuite, flush, deferred, defaultResponse } from './harness.mjs';

const endpoint = '/api/v1/admin/sales-requests';
const admin = { role: 'admin', name: 'Admin sintético', email: 'admin@example.invalid' };
const requestId = '60000000-0000-4000-8000-000000000001';
const lead = (extra = {}) => ({
  id: requestId, name: 'Contato sintético', email: 'contato@example.invalid', phone: '+5511912345678',
  business_name: 'Negócio sintético', segment: 'odontologia', intent: 'trial', status: 'new',
  created_at: '2026-10-18T01:30:00Z', updated_at: '2026-10-18T01:30:00Z', contacted_at: null, version: 7, ...extra,
});
const list = (items = [lead()], extra = {}) => ({
  items, total: items.length, limit: 25, offset: 0,
  counts: { all: 81, new: 50, contacted: 20, closed: 11 }, access: { can_update: true }, ...extra,
});
const isRequest = path => path.startsWith(endpoint);
function change(h, selector, value) {
  const element = h.document.querySelector(selector);
  assert.ok(element, `Missing field ${selector}`);
  element.value = value;
  element.dispatchEvent(new h.window.Event('change', { bubbles: true }));
}
async function showRequests(h) {
  await flush();
  h.click('[data-os-settings="site-requests"]');
  await flush();
}

test('site requests remain inside Settings for support and admin with a resolved business context', async t => {
  for (const role of ['support', 'admin']) {
    await t.test(role, async t => {
      const h = await makeSuite({ initialRoute: 'configuracoes', bridge: { user: { ...admin, role } },
        respond: path => isRequest(path) ? list() : defaultResponse(path) });
      t.after(() => h.close()); await flush();
      assert.equal(h.calls.some(call => isRequest(call.path)), false, 'opening Settings does not fetch private leads');
      assert.ok(h.document.querySelector('#osSettingsSection option[value="site-requests"]'));
      assert.equal(h.document.querySelector('#osNav [data-os-route="site-requests"]'), null, 'the main navigation stays unchanged');
      await showRequests(h);
      const request = h.calls.find(call => isRequest(call.path));
      assert.equal(request.method, 'GET');
      assert.equal(new URL(request.path, h.window.location.origin).pathname, endpoint);
      assert.equal(h.document.querySelector('.os-site-request-person strong').textContent, 'Contato sintético');
      assert.equal(h.document.querySelector('.os-site-request-intent').textContent, 'Teste de 3 dias');
      assert.match(h.document.querySelector('.os-site-request-interest').textContent, /Odontologia/);
      assert.match(h.document.querySelector('.os-site-request-state time').textContent, /17\/10\/2026.*22:30/);
      assert.match(h.document.querySelector('.os-site-request-results').textContent, /horários de Brasília/);
      assert.deepEqual(Array.from(h.document.querySelectorAll('.os-site-request-counts dd'), element => element.textContent), ['81', '50', '20', '11']);
      assert.match(h.document.querySelector('.os-site-requests').textContent, /Contagem geral, independente dos filtros/);
      assert.match(h.document.querySelector('.os-site-request-results').textContent, /1 solicitação encontrada/);
      assert.equal(h.calls.some(call => call.method !== 'GET'), false);
      assert.equal(h.errors.length, 0);
    });
  }
});

test('an unresolved administrative context keeps Settings gated until the business is confirmed', async t => {
  const h = await makeSuite({ initialRoute: 'configuracoes', bridge: {
    user: admin, tenants: [], tenantId: '', businessContextReady: false,
    businessContextError: 'Este acesso administrativo não possui uma empresa definida.',
  } }); t.after(() => h.close()); await flush();
  assert.match(h.document.getElementById('page-suite').textContent, /empresa definida/);
  assert.equal(h.document.querySelector('#osSettingsSection'), null);
  assert.equal(h.calls.length, 0);
});

test('tenant owners, editors and viewers cannot discover, fetch or mutate global site requests', async t => {
  for (const role of ['owner', 'editor', 'viewer']) {
    await t.test(role, async t => {
      const h = await makeSuite({ initialRoute: 'configuracoes', role });
      t.after(() => h.close()); await flush();
      assert.equal(h.document.querySelector('[data-os-settings="site-requests"]'), null);
      assert.equal(h.document.querySelector('#osSettingsSection option[value="site-requests"]'), null);
      const forged = h.document.createElement('button');
      forged.dataset.osSettings = 'site-requests'; h.document.body.appendChild(forged); forged.click(); await flush();
      assert.equal(h.calls.some(call => isRequest(call.path)), false);
      assert.equal(h.document.querySelector('.os-site-requests'), null);
      forged.removeAttribute('data-os-settings'); forged.dataset.osAction = 'site-request-status';
      forged.dataset.requestId = requestId; forged.dataset.status = 'contacted'; forged.click(); await flush();
      assert.equal(h.calls.some(call => call.method !== 'GET'), false);
    });
  }
});

test('the list escapes contact details and copying is an explicit local action', async t => {
  const value = '<img src=x onerror=alert(1)>', copied = [];
  const h = await makeSuite({ initialRoute: 'configuracoes', bridge: { user: admin },
    respond: path => isRequest(path) ? list([lead({ name: value, business_name: value, phone: null })]) : defaultResponse(path) });
  t.after(() => h.close()); await showRequests(h);
  Object.defineProperty(h.window.navigator, 'clipboard', { value: { writeText: async value => copied.push(value) }, configurable: true });
  assert.equal(h.document.querySelector('.os-site-request-person strong').textContent, value);
  assert.equal(h.document.querySelector('img[src=x]'), null);
  assert.equal(h.document.querySelector('[data-os-site-request-copy="phone"]'), null);
  assert.match(h.document.querySelector('.os-site-request-contact:last-child').textContent, /WhatsAppNão informado/);
  assert.equal(h.document.querySelector('.os-site-requests a[href]'), null, 'the list does not open messaging or email destinations');
  assert.deepEqual(copied, []);
  h.click('.os-site-request summary');
  assert.equal(h.document.querySelector('.os-site-request').open, true);
  h.click('[data-os-site-request-copy="email"]'); await flush();
  assert.deepEqual(copied, ['contato@example.invalid']);
  assert.equal(h.calls.some(call => call.method !== 'GET'), false);
});

test('search, status, interest and pagination are sent to the API, with filters resetting the offset', async t => {
  const records = Array.from({ length: 27 }, (_, index) => lead({ id: `60000000-0000-4000-8000-${String(index + 1).padStart(12, '0')}`, name: `Clínica & equipe ${index + 1}` }));
  const h = await makeSuite({ initialRoute: 'configuracoes', bridge: { user: admin }, respond: path => {
    if (!isRequest(path)) return defaultResponse(path);
    const params = new URL(path, 'https://orion.test').searchParams, offset = Number(params.get('offset'));
    return list(records.slice(offset, offset + 25), { total: 27, offset });
  } });
  t.after(() => h.close()); await showRequests(h);
  h.fill('#osSiteRequestQuery', 'Clínica & equipe');
  h.submit('#osSiteRequestFilters'); await flush();
  change(h, '#osSiteRequestStatus', 'new'); await flush();
  change(h, '#osSiteRequestIntent', 'trial'); await flush();
  h.click('[data-os-action="site-requests-next"]'); await flush();
  let params = new URL(h.calls.filter(call => isRequest(call.path)).at(-1).path, 'https://orion.test').searchParams;
  assert.equal(params.get('q'), 'Clínica & equipe');
  assert.equal(params.get('status'), 'new');
  assert.equal(params.get('intent'), 'trial');
  assert.equal(params.get('offset'), '25');
  assert.equal(params.get('limit'), '25');
  assert.equal(h.document.querySelectorAll('.os-site-request').length, 2);
  assert.match(h.document.querySelector('.os-site-request-pagination').textContent, /26 a 27 de 27/);
  assert.equal(h.document.querySelector('[data-os-action="site-requests-next"]').disabled, true);
  change(h, '#osSiteRequestStatus', 'all'); await flush();
  params = new URL(h.calls.filter(call => isRequest(call.path)).at(-1).path, 'https://orion.test').searchParams;
  assert.equal(params.get('offset'), '0');
  assert.equal(params.get('q'), 'Clínica & equipe');
  assert.equal(params.get('intent'), 'trial');
  assert.equal(h.calls.some(call => call.method !== 'GET'), false);
});

test('marking a request as contacted uses its version, ignores duplicate clicks and reloads server counts', async t => {
  let record = lead(); const pending = deferred();
  const h = await makeSuite({ initialRoute: 'configuracoes', bridge: { user: admin }, respond: async (path, method, data) => {
    if (!isRequest(path)) return defaultResponse(path);
    if (method === 'PATCH') { await pending.promise; record = { ...record, status: data.status, version: 8, contacted_at: '2026-10-18T15:00:00Z' }; return record; }
    return list([record], { counts: { all: 1, new: record.status === 'new' ? 1 : 0, contacted: record.status === 'contacted' ? 1 : 0, closed: 0 } });
  } });
  t.after(() => h.close()); await showRequests(h);
  h.click('.os-site-request summary');
  const contact = h.click('[data-os-action="site-request-status"][data-status="contacted"]');
  contact.click(); await flush();
  assert.equal(contact.disabled, true);
  assert.equal(h.calls.filter(call => call.method === 'PATCH').length, 1);
  assert.equal(h.document.querySelector('.os-site-request-state .os-badge').textContent, 'Nova', 'a pending write does not pretend the status changed');
  const write = h.calls.find(call => call.method === 'PATCH');
  assert.equal(write.path, `${endpoint}/${requestId}`);
  assert.deepEqual(JSON.parse(JSON.stringify(write.data)), { status: 'contacted', expected_version: 7 });
  pending.resolve(); await flush();
  assert.equal(h.document.querySelector('.os-site-request-state .os-badge').textContent, 'Contatada');
  assert.deepEqual(Array.from(h.document.querySelectorAll('.os-site-request-counts dd'), element => element.textContent), ['1', '0', '1', '0']);
  assert.match(h.document.querySelector('.os-site-request-footer').textContent, /Contato registrado em/);
  assert.equal(h.calls.filter(call => call.method !== 'GET').length, 1, 'no message, account or trial is created');
  assert.equal(h.errors.length, 0);
});

test('the server can make admin site requests read-only', async t => {
  const h = await makeSuite({ initialRoute: 'configuracoes', bridge: { user: admin },
    respond: path => isRequest(path) ? list([lead()], { access: { can_update: false } }) : defaultResponse(path) });
  t.after(() => h.close()); await showRequests(h);
  assert.equal(h.document.querySelector('[data-os-action="site-request-status"]'), null);
  const forged = h.document.createElement('button'); forged.dataset.osAction = 'site-request-status';
  forged.dataset.requestId = requestId; forged.dataset.status = 'contacted'; h.document.body.appendChild(forged); forged.click(); await flush();
  assert.equal(h.calls.some(call => call.method !== 'GET'), false);
});

test('updating the last new request on the final page returns to the remaining results', async t => {
  const records = Array.from({ length: 26 }, (_, index) => lead({ id: `60000000-0000-4000-8000-${String(index + 1).padStart(12, '0')}`, name: `Contato sintético ${index + 1}` }));
  const h = await makeSuite({ initialRoute: 'configuracoes', bridge: { user: admin }, respond: (path, method, payload) => {
    if (!isRequest(path)) return defaultResponse(path);
    if (method === 'PATCH') {
      const item = records.find(item => path.endsWith('/' + item.id));
      item.status = payload.status; item.version += 1; return item;
    }
    const params = new URL(path, 'https://orion.test').searchParams, offset = Number(params.get('offset'));
    const filtered = records.filter(item => params.get('status') === 'all' || item.status === params.get('status'));
    return list(filtered.slice(offset, offset + 25), { total: filtered.length, offset });
  } });
  t.after(() => h.close()); await showRequests(h);
  change(h, '#osSiteRequestStatus', 'new'); await flush();
  h.click('[data-os-action="site-requests-next"]'); await flush();
  assert.equal(h.document.querySelectorAll('.os-site-request').length, 1);
  h.click('.os-site-request summary'); h.click('[data-os-action="site-request-status"][data-status="contacted"]'); await flush();
  assert.equal(h.document.querySelectorAll('.os-site-request').length, 25);
  assert.match(h.document.querySelector('.os-site-request-pagination').textContent, /1 a 25 de 25/);
  assert.equal(h.document.querySelector('#osSiteRequestStatus').value, 'new');
  const latest = new URL(h.calls.filter(call => isRequest(call.path) && call.method === 'GET').at(-1).path, 'https://orion.test');
  assert.equal(latest.searchParams.get('offset'), '0');
  assert.equal(h.calls.filter(call => call.method === 'PATCH').length, 1);
});

test('a version conflict displays the error and reloads the authoritative status', async t => {
  let record = lead();
  const h = await makeSuite({ initialRoute: 'configuracoes', bridge: { user: admin }, respond: (path, method) => {
    if (!isRequest(path)) return defaultResponse(path);
    if (method === 'PATCH') { record = { ...record, status: 'closed', version: 8 }; throw Object.assign(Error('A solicitação foi atualizada por outra pessoa.'), { status: 409 }); }
    return list([record]);
  } });
  t.after(() => h.close()); await showRequests(h);
  h.click('.os-site-request summary'); h.click('[data-os-action="site-request-status"][data-status="contacted"]'); await flush();
  assert.match(h.document.getElementById('osToastRegion').textContent, /atualizada por outra pessoa/);
  assert.equal(h.document.querySelector('.os-site-request-state .os-badge').textContent, 'Encerrada');
  assert.equal(h.calls.filter(call => isRequest(call.path) && call.method === 'GET').length, 2);
  assert.equal(h.calls.filter(call => call.method === 'PATCH').length, 1);
});

test('an unavailable lead store is distinct from an empty list and offers a real retry', async t => {
  let unavailable = true;
  const h = await makeSuite({ initialRoute: 'configuracoes', bridge: { user: admin }, respond: path => {
    if (!isRequest(path)) return defaultResponse(path);
    if (unavailable) throw Object.assign(Error('Armazenamento de solicitações indisponível.'), { status: 503 });
    return list([], { counts: { all: 0, new: 0, contacted: 0, closed: 0 } });
  } });
  t.after(() => h.close()); await showRequests(h);
  assert.match(h.document.querySelector('.os-site-requests [role=alert]').textContent, /Armazenamento de solicitações indisponível/);
  assert.doesNotMatch(h.document.querySelector('.os-site-requests').textContent, /Nenhuma solicitação recebida/);
  assert.ok(h.document.querySelector('#osSettingsSection'));
  unavailable = false; h.click('.os-site-requests [role=alert] [data-os-action="site-requests-refresh"]'); await flush();
  assert.equal(h.document.querySelector('.os-site-requests [role=alert]'), null);
  assert.match(h.document.querySelector('.os-site-requests').textContent, /Nenhuma solicitação recebida/);
  assert.equal(h.calls.filter(call => isRequest(call.path)).length, 2);
});

test('late site-request reads cannot reveal contacts after the operator becomes a member', async t => {
  const pending = deferred();
  const h = await makeSuite({ initialRoute: 'configuracoes', bridge: { user: admin }, respond: path => isRequest(path) ? pending.promise : defaultResponse(path) });
  t.after(() => h.close()); await showRequests(h);
  assert.ok(h.document.querySelector('.os-site-requests [aria-busy=true]'));
  h.window.OrionSuite.sync({ user: { role: 'member', name: 'Membro sintético', email: 'member@example.invalid' } }); await flush();
  pending.resolve(list([lead({ name: 'Contato restrito tardio' })])); await flush();
  assert.equal(h.document.querySelector('[data-os-settings="site-requests"]'), null);
  assert.equal(h.document.querySelector('.os-site-requests'), null);
  assert.doesNotMatch(h.document.getElementById('page-suite').textContent, /Contato restrito tardio|contato@example.invalid/);
  assert.equal(h.calls.filter(call => isRequest(call.path)).length, 1);
  assert.equal(h.errors.length, 0);
});

test('a late status update does not repopulate contacts or leave the next account blocked', async t => {
  const pending = deferred();
  const h = await makeSuite({ initialRoute: 'configuracoes', bridge: { user: admin }, respond: (path, method) => {
    if (!isRequest(path)) return defaultResponse(path);
    return method === 'PATCH' ? pending.promise : list();
  } });
  t.after(() => h.close()); await showRequests(h);
  h.click('.os-site-request summary'); h.click('[data-os-action="site-request-status"][data-status="contacted"]'); await flush();
  h.window.OrionSuite.sync({ user: { role: 'member', name: 'Membro sintético', email: 'member@example.invalid' } }); await flush();
  h.click('[data-os-settings="appearance"]'); await flush();
  assert.ok(h.document.querySelector('.os-appearance-options'));
  pending.resolve(lead({ status: 'contacted', version: 8 })); await flush();
  assert.ok(h.document.querySelector('.os-appearance-options'));
  assert.equal(h.document.querySelector('.os-site-requests'), null);
  assert.equal(h.document.getElementById('osToastRegion').textContent, '');
  assert.equal(h.calls.filter(call => isRequest(call.path) && call.method === 'GET').length, 1);
  assert.equal(h.errors.length, 0);
});
