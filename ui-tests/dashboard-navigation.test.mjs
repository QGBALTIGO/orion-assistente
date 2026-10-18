import test from 'node:test';
import assert from 'node:assert/strict';
import { makeSuite, flush, deferred, defaultResponse, customer, customerId, tenantA } from './harness.mjs';

const admin = { role: 'admin', name: 'Admin sintético', email: 'admin@example.invalid' };
const phone = '+5511912345678';
const idFor = index => `40000000-0000-4000-8000-${String(index).padStart(12, '0')}`;
const customerResponse = items => path => path.includes('/management/customers') ? { items, total: items.length } : defaultResponse(path);
const mobileNames = document => Array.from(document.querySelectorAll('.os-client-mobile-main strong'), element => element.textContent);

test('navigation keeps WhatsApp first, a single Dashboard, and Atendimento after Gestão', async t => {
  const opened = [];
  const h = await makeSuite({ initialRoute: 'dashboard', bridge: { user: admin, openLegacy: route => opened.push(route) } });
  t.after(() => h.close()); await flush();
  const nav = h.document.getElementById('osNav');
  const entries = Array.from(nav.querySelectorAll('[data-os-route]'));
  assert.equal(entries[0].dataset.osRoute, 'dispositivo');
  assert.equal(entries[0].textContent, 'Conectar WhatsApp');
  assert.equal(entries[1].dataset.osRoute, 'dashboard');
  assert.equal(entries.filter(element => element.dataset.osRoute === 'dashboard').length, 1);
  assert.equal(nav.querySelector('[data-os-route=acelerar]'), null);
  assert.equal(nav.querySelector('[data-os-route=cadastros]'), null);
  assert.equal(new Set(entries.map(element => element.dataset.osRoute)).size, entries.length);
  const groups = Array.from(nav.querySelectorAll('.os-nav-section'));
  assert.deepEqual(groups.map(element => element.textContent), ['Gestão', 'Atendimento Orion', 'Ajuda e conta']);
  assert.equal(groups[1].previousElementSibling.dataset.osRoute, 'configuracoes');
  assert.equal(groups[1].nextElementSibling.dataset.osRoute, 'conversas');
  h.click('#osNav [data-os-route=dispositivo]'); await flush();
  assert.deepEqual(opened, ['dispositivo']);
  assert.equal(h.calls.some(call => call.method !== 'GET'), false);
});

test('the acelerar deep link canonicalizes to Dashboard while preserving tenant and query context', async t => {
  const changed = [];
  const h = await makeSuite({ initialRoute: 'acelerar', bridge: { onRouteChange: route => changed.push(route) } });
  t.after(() => h.close()); await flush();
  assert.equal(h.document.querySelector('#page-suite h1').textContent, 'Dashboard');
  assert.equal(h.document.getElementById('page-suite').dataset.osCurrentRoute, 'dashboard');
  assert.equal(h.document.title, 'Dashboard · Orion');
  assert.deepEqual(changed, ['dashboard']);
  assert.equal(h.document.querySelector('#osNav [aria-current=page]').dataset.osRoute, 'dashboard');
  let url = new URL(h.window.location.href);
  assert.equal(url.searchParams.get('aba'), 'dashboard');
  assert.equal(url.searchParams.get('empresa'), tenantA);
  assert.equal(h.calls.filter(call => call.path.includes('/management/dashboard')).length, 1);
  h.window.history.replaceState({}, '', `/panel?aba=acelerar&empresa=${tenantA}&origem=favorito`);
  await h.window.OrionSuite.open('acelerar'); await flush();
  url = new URL(h.window.location.href);
  assert.equal(url.searchParams.get('aba'), 'dashboard');
  assert.equal(url.searchParams.get('empresa'), tenantA);
  assert.equal(url.searchParams.get('origem'), 'favorito');
  assert.equal(h.calls.some(call => call.method !== 'GET'), false);
  assert.equal(h.errors.length, 0);
});

test('Dashboard retains quick actions, factual onboarding, daily totals, settings and site access', async t => {
  const opened = [];
  let data = { summary: { appointments: 0, completed: 0, cancelled: 2, no_show: 1, customers: 80 }, onboarding: { has_appointment: true, scheduling_ready: true, customers_with_phone: 5 } };
  const h = await makeSuite({ initialRoute: 'acelerar', bridge: { openLegacy: route => opened.push(route) }, respond: path => path.includes('/management/dashboard') ? data : defaultResponse(path) });
  t.after(() => h.close()); await flush();
  const page = h.document.getElementById('page-suite');
  assert.deepEqual(Array.from(page.querySelectorAll('.os-quick-actions button'), element => element.textContent.replace(/\s+/g, ' ').trim()), [
    'Novo agendamentoMarque um horário em segundos', 'Novo clienteCadastre nome e telefone', 'Ver agenda0 agendado(s) hoje',
  ]);
  assert.equal(page.querySelector('[role=progressbar]').getAttribute('aria-valuenow'), '3');
  assert.deepEqual(Array.from(page.querySelectorAll('.os-mission-state'), element => element.textContent), ['Concluída', 'Concluída', 'Concluída']);
  assert.match(page.querySelector('.os-journey-footer').textContent, /Os três passos iniciais estão concluídos\./);
  assert.doesNotMatch(page.querySelector('.os-journey-footer').textContent, /Próxima:/);
  assert.equal(page.querySelector('.os-journey-footer [data-os-route]').dataset.osRoute, 'agenda');
  assert.deepEqual(Array.from(page.querySelectorAll('.os-stat strong'), element => element.textContent), ['0', '0', '3']);
  assert.ok(page.querySelector('[data-os-route=site]'));
  h.click('#page-suite [data-os-action=new-customer]'); await flush();
  assert.ok(h.document.querySelector('[data-os-form=customer]'));
  await h.window.OrionSuite.open('dashboard');
  h.click('#page-suite [data-os-route=empresa]'); await flush();
  assert.deepEqual(opened, ['empresa']);
  data = { summary: { appointments: 80, completed: 80, customers: 80 }, onboarding: { has_appointment: false, scheduling_ready: false, customers_with_phone: 0 } };
  await h.window.OrionSuite.open('dashboard'); await flush();
  assert.equal(page.querySelector('[role=progressbar]').getAttribute('aria-valuenow'), '0', 'daily totals and total customers cannot substitute for the factual onboarding fields');
  assert.match(page.querySelector('.os-journey-footer').textContent, /Crie e confirme seu primeiro agendamento/);
  assert.equal(h.calls.some(call => call.method !== 'GET'), false);
});

test('Dashboard viewer can navigate but cannot create or access the WhatsApp operator routes', async t => {
  const opened = [];
  const h = await makeSuite({ initialRoute: 'acelerar', role: 'viewer', bridge: { openLegacy: route => opened.push(route) } });
  t.after(() => h.close()); await flush();
  assert.equal(h.document.querySelector('#osNav [data-os-route=dispositivo]'), null);
  assert.equal(h.document.querySelector('#osNav [data-os-route=conversas]'), null);
  assert.equal(h.document.querySelector('#osNav [data-os-route]').dataset.osRoute, 'dashboard');
  assert.equal(h.document.querySelector('[data-os-action=new-appointment]').disabled, true);
  assert.equal(h.document.querySelector('[data-os-action=new-customer]').disabled, true);
  assert.equal(h.document.querySelector('[data-os-action=agenda-open]').disabled, false);
  h.click('[data-os-action=new-customer]');
  await h.window.OrionSuite.open('dispositivo');
  await h.window.OrionSuite.open('conversas'); await flush();
  assert.equal(h.document.querySelector('[data-os-form=customer]'), null);
  assert.deepEqual(opened, []);
  h.click('[data-os-action=agenda-open]'); await flush();
  assert.equal(h.document.getElementById('page-suite').dataset.osCurrentRoute, 'agenda');
  assert.equal(h.calls.some(call => call.method !== 'GET'), false);
});

test('Customers uses useful manual names before resolved metadata, with safe phone and emoji fallbacks', async t => {
  const items = [
    { ...customer('Nome manual sintético', idFor(1)), display_name: 'Metadado diferente', whatsapp_name: 'Perfil diferente', phone },
    { ...customer('Cliente WhatsApp', idFor(2)), display_name: 'Aline Sintética', whatsapp_name: 'Nome antigo', phone },
    { ...customer('+55 (11) 91234-5678', idFor(3)), display_name: phone, whatsapp_name: 'Beto Sintético', phone },
    { ...customer('', idFor(4)), whatsapp_name: '👩🏽‍🔧', phone },
    { ...customer('Unknown', idFor(5)), display_name: phone, phone },
    { ...customer({ unexpected: 'object' }, idFor(6)), display_name: {}, whatsapp_name: 'Nome válido', phone },
    { ...customer('Contato', idFor(7)), display_name: '<img src=x onerror=alert(1)>', phone },
    { ...customer('Cliente especial', idFor(8)), whatsapp_name: 'Outro nome', phone },
    { ...customer(null, idFor(9)), display_name: {}, whatsapp_name: [], phone },
  ];
  const expected = ['Nome manual sintético', 'Aline Sintética', 'Beto Sintético', '👩🏽‍🔧', phone, 'Nome válido', '<img src=x onerror=alert(1)>', 'Cliente especial', phone];
  const h = await makeSuite({ respond: customerResponse(items) }); t.after(() => h.close()); await flush();
  assert.deepEqual(mobileNames(h.document), expected);
  assert.deepEqual(Array.from(h.document.querySelectorAll('.os-table .os-person strong'), element => element.textContent), expected);
  assert.equal(h.document.querySelector('#page-suite img[src=x]'), null);
  assert.doesNotMatch(h.document.getElementById('page-suite').textContent, /\[object Object\]|Cliente WhatsApp/);
  assert.equal(h.document.querySelector(`[data-os-customer="${idFor(2)}"]`).getAttribute('aria-label'), 'Editar Aline Sintética');
  assert.equal(h.document.querySelector(`[data-os-customer-delete="${idFor(3)}"]`).getAttribute('aria-label'), 'Excluir Beto Sintético');
  const phoneRow = h.document.querySelector(`.os-client-mobile-main[data-os-customer="${idFor(5)}"]`);
  assert.equal(phoneRow.querySelector('small').textContent, 'Nome ainda não informado');
  assert.ok(phoneRow.querySelector('.os-avatar svg'), 'phone-only records use a neutral avatar');
  const emojiAvatar = h.document.querySelector(`.os-client-mobile-main[data-os-customer="${idFor(4)}"] .os-avatar`).textContent;
  assert.equal(Array.from(emojiAvatar)[0], '👩', 'avatar initials must not split an emoji into a lone surrogate');
  assert.equal(h.calls.some(call => call.method !== 'GET'), false, 'opening Customers must not persist display metadata');
  assert.equal(h.errors.length, 0);
});

test('generic customer placeholders are normalized before falling back to WhatsApp metadata', async t => {
  const placeholders = [' Cliente  WhatsApp ', 'CLIENTE DO WHATSAPP', 'Contato WhatsApp', 'Contato do WhatsApp', 'WhatsApp', 'Cliente', 'Contato', 'Sem nome', ' Nôme\t nÃo   ínformado ', 'Desconhecido', 'unknown', 'whatsapp customer', 'whatsapp contact'];
  const items = placeholders.map((name, index) => ({ ...customer(name, idFor(index + 20)), whatsapp_name: `Nome sintético ${index + 1}`, phone }));
  const h = await makeSuite({ respond: customerResponse(items) }); t.after(() => h.close()); await flush();
  assert.deepEqual(mobileNames(h.document), items.map(item => item.whatsapp_name));
  assert.deepEqual(Array.from(h.document.querySelectorAll('.os-table .os-person strong'), element => element.textContent), items.map(item => item.whatsapp_name));
});

test('editing a resolved customer retains the stored manual name and does not write metadata fields', async t => {
  const record = { ...customer('Cliente WhatsApp'), phone, display_name: 'Bruna Sintética', whatsapp_name: 'Bruna Sintética', version: 7 };
  const h = await makeSuite({ respond: (path, method, data) => method === 'PATCH' ? { ...record, ...data, version: 8 } : customerResponse([record])(path) });
  t.after(() => h.close()); await flush();
  h.click(`[data-os-customer="${customerId}"]`); await flush();
  assert.equal(h.document.getElementById('osField-name').value, record.name);
  h.fill('#osField-notes', 'Observação sintética');
  h.submit('[data-os-form=customer]'); await flush();
  const writes = h.calls.filter(call => call.method !== 'GET');
  assert.equal(writes.length, 1);
  assert.equal(writes[0].method, 'PATCH');
  assert.equal(writes[0].path, `/api/v1/tenants/${tenantA}/management/customers/${customerId}`);
  assert.equal(writes[0].data.name, 'Cliente WhatsApp');
  assert.equal(writes[0].data.expected_version, 7);
  assert.equal(writes[0].data.notes, 'Observação sintética');
  assert.equal(Object.hasOwn(writes[0].data, 'display_name'), false);
  assert.equal(Object.hasOwn(writes[0].data, 'whatsapp_name'), false);
});

test('deleting a resolved customer names the visible person and preserves explicit confirmation and version', async t => {
  const record = { ...customer('Contato'), display_name: 'Caio Sintético', whatsapp_name: 'Caio Sintético', phone, version: 4 };
  let removed = false;
  const h = await makeSuite({ respond: (path, method) => {
    if (method === 'DELETE') { removed = true; return null; }
    return customerResponse(removed ? [] : [record])(path);
  } }); t.after(() => h.close()); await flush();
  h.click(`[data-os-customer-delete="${customerId}"]`); await flush();
  assert.match(h.document.getElementById('osDialog').textContent, /O cadastro de Caio Sintético será removido/);
  assert.equal(h.calls.some(call => call.method === 'DELETE'), false);
  h.submit('[data-os-form=confirm]'); await flush();
  const writes = h.calls.filter(call => call.method !== 'GET');
  assert.equal(writes.length, 1);
  assert.equal(writes[0].method, 'DELETE');
  assert.equal(writes[0].path, `/api/v1/tenants/${tenantA}/management/customers/${customerId}`);
  assert.equal(writes[0].data.expected_version, 4);
  assert.match(h.document.getElementById('page-suite').textContent, /Nenhum cliente ainda/);
});

test('customer search sends names and formatted phones to the tenant-scoped read endpoint', { timeout: 4000 }, async t => {
  const named = deferred(), numbered = deferred();
  const record = { ...customer('Cliente WhatsApp'), phone, display_name: 'João Sintético' };
  const h = await makeSuite({ respond: (path, method) => {
    if (!path.includes('/management/customers')) return defaultResponse(path);
    const q = new URL(path, 'https://orion.test').searchParams.get('q');
    if (q === 'João Sintético') named.resolve();
    if (q === '(11) 91234-5678') numbered.resolve();
    return { items: q ? [record] : [], total: q ? 1 : 0 };
  } }); t.after(() => h.close()); await flush();
  assert.equal(h.document.getElementById('osCustomerSearch').placeholder, 'Buscar por nome ou telefone...');
  h.fill('#osCustomerSearch', 'João Sintético'); await named.promise; await flush();
  assert.deepEqual(mobileNames(h.document), ['João Sintético']);
  h.fill('#osCustomerSearch', '(11) 91234-5678'); await numbered.promise; await flush();
  const searches = h.calls.filter(call => new URL(call.path, 'https://orion.test').searchParams.get('q'));
  assert.deepEqual(searches.map(call => new URL(call.path, 'https://orion.test').searchParams.get('q')), ['João Sintético', '(11) 91234-5678']);
  assert.ok(searches.every(call => call.method === 'GET' && call.path.startsWith(`/api/v1/tenants/${tenantA}/management/customers?`)));
  assert.equal(h.calls.some(call => call.method !== 'GET'), false);
});
