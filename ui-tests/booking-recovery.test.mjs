import test from 'node:test';
import assert from 'node:assert/strict';
import { makeSuite, flush, deferred, defaultResponse, customer, customerId, tenantA, tenantB } from './harness.mjs';

const professionalId = '50000000-0000-4000-8000-000000000005';
const serviceId = '60000000-0000-4000-8000-000000000006';
const addonId = '70000000-0000-4000-8000-000000000007';
const variantId = '80000000-0000-4000-8000-000000000008';
const appointmentId = '90000000-0000-4000-8000-000000000009';
const startAt = '2030-04-05T12:00:00Z';
const customerPhone = '+5511987654321';
const plain = value => JSON.parse(JSON.stringify(value));
const service = { id: serviceId, name: 'Serviço sintético', active: true, catalog_kind: 'base', duration_min: 30, price_cents: 5000 };
const addon = { id: addonId, name: 'Extra sintético', active: true, catalog_kind: 'base', duration_min: 20, price_cents: 2200 };

function bookingResponse(override, { addons = false } = {}) {
  const record = customer();
  return (path, method, data) => {
    const result = override?.(path, method, data, record);
    if (result !== undefined) return result;
    if (path.includes('/management/customers') && method === 'PATCH') {
      Object.assign(record, data, { version: record.version + 1 });
      return { ...record };
    }
    if (path.endsWith(`/management/customers/${customerId}`)) return { ...record };
    if (path.includes('/management/customers?')) return { items: [{ ...record }], total: 1 };
    if (path.endsWith('/professionals')) return [{ id: professionalId, name: 'Profissional sintético', active: true, accepts_appointments: true }];
    if (path.endsWith('/professional_services')) return [service, addon].map(item => ({ id: `link-${item.id}`, professional_id: professionalId, service_id: item.id }));
    if (path.endsWith('/services')) return addons ? [service, addon] : [service];
    if (path.endsWith(`/management/services/${serviceId}/addons`)) return { service_id: serviceId, version: 0, items: addons ? [{ addon_service_id: addonId, name: addon.name, active: true, price_cents: 2200, duration_min: 20, add_duration: false }] : [] };
    if (path.endsWith(`/management/services/${addonId}/addons`)) return { service_id: addonId, version: 0, items: [] };
    if (path.endsWith('/management/booking-selection')) return { engine_service_id: variantId, price_cents: 7200, duration_min: 30, prep_min: 0, cleanup_min: 0, timezone: 'America/Sao_Paulo', items: [] };
    if (path.includes('/availability?')) return { slots: [startAt], timezone: 'America/Sao_Paulo' };
    if (path.endsWith('/appointments/hold')) return { id: appointmentId, status: 'hold', protocol: 'ORION-TEST' };
    if (path.endsWith(`/appointments/${appointmentId}/confirm`)) return { id: appointmentId, status: 'confirmed' };
    if (path.endsWith('/management/block-series') && method === 'POST') return { id: 'series-test', count: 3, blocks: [] };
    return defaultResponse(path);
  };
}

async function change(h, selector, value) {
  const element = h.document.querySelector(selector);
  assert.ok(element, `field exists: ${selector}`);
  element.value = value;
  element.dispatchEvent(new h.window.Event('change', { bubbles: true }));
  await flush();
  return element;
}

async function prepareBooking(h, { phone = '(11) 98765-4321' } = {}) {
  await flush();
  h.click('[data-os-action=new-appointment]'); await flush();
  h.fill('#osBookingCustomer', 'Cliente sintético');
  h.click(`[data-os-book-customer="${customerId}"]`);
  h.fill('#osField-customer_phone', phone);
  await change(h, '#osField-day', '2030-04-05');
  await change(h, '#osField-professional_id', professionalId);
  h.click(`[data-os-book-service="${serviceId}"]`); await flush();
  h.fill('#osBookingSlot', startAt);
}

test('confirmation retry preserves the original hold and updates the selected customer before booking', async t => {
  let confirmations = 0;
  const h = await makeSuite({ initialRoute: 'agenda', respond: bookingResponse(path => {
    if (path.endsWith(`/appointments/${appointmentId}/confirm`) && ++confirmations === 1) throw new Error('A conexão caiu após confirmar.');
  }) }); t.after(() => h.close());
  await prepareBooking(h);
  h.submit('[data-os-form=appointment]'); await flush();
  const form = h.document.querySelector('[data-os-form=appointment]');
  assert.equal(form.querySelector('fieldset').disabled, true);
  assert.equal(form.querySelector('button[type=submit]').disabled, false);
  assert.match(h.document.getElementById('osPendingBooking').textContent, /ORION-TEST/);
  const writes = h.calls.filter(call => call.method !== 'GET');
  assert.equal(writes[0].path, `/api/v1/tenants/${tenantA}/management/customers/${customerId}`);
  assert.equal(writes[0].method, 'PATCH');
  assert.equal(writes[0].data.expected_version, 1);
  assert.equal(writes[0].data.phone, customerPhone);
  assert.equal(writes[1].data.customer_name, 'Cliente sintético');
  assert.equal(writes[1].data.customer_phone, customerPhone);
  // Disabled DOM fields are still writable by scripts; retries must use the stored intent.
  h.fill('#osBookingCustomer', 'Outra pessoa');
  h.fill('#osField-customer_phone', '(11) 91234-0000');
  await change(h, '#osField-day', '2030-05-06');
  h.submit('[data-os-form=appointment]'); await flush();
  assert.equal(h.calls.filter(call => call.path.endsWith('/appointments/hold')).length, 1);
  assert.equal(h.calls.filter(call => call.path.endsWith(`/appointments/${appointmentId}/confirm`)).length, 2);
  assert.equal(h.calls.filter(call => call.method === 'PATCH').length, 1);
  assert.equal(h.document.getElementById('osDialog').open, false);
  assert.equal(h.errors.length, 0);
});

test('an uncertain hold request is retried with exactly the same idempotency key and booking intent', async t => {
  let holds = 0;
  const h = await makeSuite({ initialRoute: 'agenda', respond: bookingResponse(path => {
    if (path.endsWith('/appointments/hold') && ++holds === 1) throw new Error('Sem resposta do servidor.');
  }) }); t.after(() => h.close());
  await prepareBooking(h); h.submit('[data-os-form=appointment]'); await flush();
  assert.ok(h.document.getElementById('osPendingBooking'));
  assert.equal(h.calls.some(call => call.path.endsWith('/confirm')), false);
  h.fill('#osBookingCustomer', 'Nome alterado depois da falha');
  h.fill('#osField-customer_phone', '(21) 99999-0000');
  await change(h, '#osField-day', '2030-08-09');
  h.submit('[data-os-form=appointment]'); await flush();
  const requests = h.calls.filter(call => call.path.endsWith('/appointments/hold'));
  assert.equal(requests.length, 2);
  assert.deepEqual(plain(requests[1].data), plain(requests[0].data));
  assert.ok(requests[0].data.idempotency_key);
  assert.equal(h.calls.filter(call => call.path.endsWith('/confirm')).length, 1);
});

test('a customer phone update with a lost response is verified by id before creating the hold', async t => {
  const h = await makeSuite({ initialRoute: 'agenda', respond: bookingResponse((path, method, data, record) => {
    if (path.endsWith(`/management/customers/${customerId}`) && method === 'PATCH') {
      Object.assign(record, { phone: data.phone, version: 2 });
      throw new Error('Resposta do cadastro interrompida.');
    }
  }) }); t.after(() => h.close());
  await prepareBooking(h); h.submit('[data-os-form=appointment]'); await flush();
  const patch = h.calls.findIndex(call => call.method === 'PATCH');
  const verify = h.calls.findIndex(call => call.path.endsWith(`/management/customers/${customerId}`) && call.method === 'GET');
  const hold = h.calls.findIndex(call => call.path.endsWith('/appointments/hold'));
  assert.ok(patch >= 0 && patch < verify && verify < hold);
  assert.equal(h.calls.filter(call => call.method === 'PATCH').length, 1);
  assert.equal(h.calls.filter(call => call.method === 'POST' && /\/management\/customers$/.test(call.path)).length, 0);
  assert.equal(h.document.getElementById('osDialog').open, false);
});

test('an explicit foreign country code is not rewritten into a Brazilian customer number', async t => {
  const h = await makeSuite({ initialRoute: 'agenda', respond: bookingResponse() }); t.after(() => h.close());
  await prepareBooking(h, { phone: '+12025550123' });
  h.submit('[data-os-form=appointment]'); await flush();
  assert.equal(h.calls.filter(call => call.method !== 'GET').length, 0);
  assert.match(h.document.querySelector('[data-os-form=appointment] .os-form-message').textContent, /WhatsApp.*DDD/);
});

test('add-ons use a single server-prepared service id for both availability and the hold', async t => {
  const h = await makeSuite({ initialRoute: 'agenda', respond: bookingResponse(undefined, { addons: true }) }); t.after(() => h.close());
  await prepareBooking(h);
  h.click(`[data-os-book-addon="${addonId}"]`); await flush();
  const preparation = h.calls.find(call => call.path.endsWith('/management/booking-selection'));
  assert.deepEqual(plain(preparation.data), { service_id: serviceId, professional_id: professionalId, addon_ids: [addonId] });
  const availability = h.calls.filter(call => call.path.includes('/availability?')).at(-1);
  assert.deepEqual(new URL(availability.path, 'https://orion.test').searchParams.getAll('service_ids'), [variantId]);
  assert.match(h.document.getElementById('osBookingSummary').textContent, /30 min/);
  assert.match(h.document.getElementById('osBookingSummary').textContent, /72,00/);
  h.fill('#osBookingSlot', startAt); h.submit('[data-os-form=appointment]'); await flush();
  const hold = h.calls.find(call => call.path.endsWith('/appointments/hold'));
  assert.deepEqual(plain(hold.data.service_ids), [variantId], 'raw add-on ids would incorrectly add their duration in the booking engine');
});

test('switching to multiple individual services removes the earlier add-on variant from availability', async t => {
  const h = await makeSuite({ initialRoute: 'agenda', respond: bookingResponse(undefined, { addons: true }) }); t.after(() => h.close());
  await prepareBooking(h);
  h.click(`[data-os-book-addon="${addonId}"]`); await flush();
  h.click(`[data-os-book-service="${addonId}"]`); await flush();
  assert.equal(h.document.getElementById('osBookingAddons').classList.contains('hidden'), true);
  const availability = h.calls.filter(call => call.path.includes('/availability?')).at(-1);
  assert.deepEqual(new URL(availability.path, 'https://orion.test').searchParams.getAll('service_ids'), [serviceId, addonId]);
  assert.match(h.document.getElementById('osBookingSummary').textContent, /50 min/);
});

test('a late availability response cannot overwrite slots for a newer service selection', async t => {
  const delayed = deferred(), newer = '2030-04-05T14:00:00Z';
  let reads = 0;
  const h = await makeSuite({ initialRoute: 'agenda', respond: bookingResponse(path => {
    if (path.includes('/availability?')) return ++reads === 1 ? delayed.promise : { slots: [newer], timezone: 'America/Sao_Paulo' };
  }, { addons: true }) }); t.after(() => h.close());
  await prepareBooking(h);
  h.click(`[data-os-book-service="${addonId}"]`); await flush();
  delayed.resolve({ slots: [startAt], timezone: 'America/Sao_Paulo' }); await flush();
  const slots = [...h.document.getElementById('osBookingSlot').options].map(option => option.value);
  assert.deepEqual(slots, ['', newer]);
});

test('recurring block submission preserves Monday-based weekdays without requiring the repeat checkbox', async t => {
  const h = await makeSuite({ initialRoute: 'agenda', respond: bookingResponse() }); t.after(() => h.close()); await flush();
  h.click('[data-os-action=new-block]'); await flush();
  await change(h, '#osField-professional_id', professionalId);
  await change(h, '#osField-block_type', 'days');
  await change(h, '#osField-block_type', 'hours');
  const form = h.document.querySelector('[data-os-form=block]');
  assert.equal(form.elements.repeat.required, false);
  assert.equal(form.elements.until_date.required, false);
  h.fill('#osField-day', '2030-04-01');
  h.fill('#osField-start_time', '12:00'); h.fill('#osField-end_time', '13:00');
  h.click('[data-os-form=block] [name=repeat]'); await flush();
  await change(h, '#osField-frequency', 'weekdays');
  form.querySelectorAll('[name=weekdays]').forEach(input => { input.checked = ['0', '2'].includes(input.value); });
  h.fill('#osField-until_date', '2030-04-08'); h.fill('#osField-reason', 'Treinamento sintético');
  h.submit('[data-os-form=block]'); await flush();
  const request = h.calls.find(call => call.path.endsWith('/management/block-series') && call.method === 'POST');
  assert.ok(request);
  const { idempotency_key, ...body } = plain(request.data);
  assert.ok(idempotency_key);
  assert.deepEqual(body, { professional_id: professionalId, start_date: '2030-04-01', until_date: '2030-04-08', frequency: 'weekdays', weekdays: [0, 2], kind: 'hours', start_time: '12:00', end_time: '13:00', reason: 'Treinamento sintético' });
});

test('an all-day block ignores the hidden hourly recurrence controls', async t => {
  const h = await makeSuite({ initialRoute: 'agenda', respond: bookingResponse() }); t.after(() => h.close()); await flush();
  h.click('[data-os-action=new-block]'); await flush();
  await change(h, '#osField-professional_id', professionalId);
  h.click('[data-os-form=block] [name=repeat]'); await flush();
  await change(h, '#osField-block_type', 'days');
  h.fill('#osField-from_day', '2030-04-01'); h.fill('#osField-to_day', '2030-04-03');
  h.submit('[data-os-form=block]'); await flush();
  const request = h.calls.find(call => call.path.endsWith('/management/block-series') && call.method === 'POST');
  assert.ok(request);
  assert.equal(request.data.kind, 'day_off');
  assert.equal(request.data.frequency, 'daily');
  assert.equal(request.data.start_time, null); assert.equal(request.data.end_time, null);
  assert.deepEqual(plain(request.data.weekdays), []);
});

test('the tenant-change guard can retain the current company and its unsaved form', async t => {
  const guarded = [], changed = [];
  const h = await makeSuite({ bridge: { beforeTenantChange: async id => { guarded.push(id); return false; }, setTenant: id => changed.push(id) } }); t.after(() => h.close()); await flush();
  h.click('[data-os-action=new-customer]'); h.fill('#osField-name', 'Rascunho da empresa A');
  await change(h, '#osTenant', tenantB);
  assert.deepEqual(guarded, [tenantB]); assert.deepEqual(changed, []);
  assert.equal(h.document.getElementById('osTenant').value, tenantA);
  assert.equal(h.document.getElementById('osField-name').value, 'Rascunho da empresa A');
  assert.equal(h.calls.some(call => call.path.includes(`/tenants/${tenantB}/`)), false);
});

test('switching companies clears the search debounce and notification content from the previous company', async t => {
  const h = await makeSuite({ respond: path => path.includes('/management/notifications') ? { items: [{ id: 'event-test', event: 'confirmed', happened_at: startAt, start_at: startAt, customer_name: 'Pessoa privada A', professional_name: 'Equipe A' }], can_mark_read: true } : defaultResponse(path) }); t.after(() => h.close()); await flush();
  h.click('[data-os-action=notifications]'); await flush();
  assert.match(h.document.getElementById('osNotificationsPanel').textContent, /Pessoa privada A/);
  h.fill('#osCustomerSearch', 'Busca privada da empresa A');
  h.window.OrionSuite.sync({ tenantId: tenantB }); await flush();
  assert.equal(h.document.getElementById('osCustomerSearch').value, '');
  assert.equal(h.document.getElementById('osNotificationsPanel').textContent, '');
  await new Promise(resolve => setTimeout(resolve, 300)); await flush();
  const reads = h.calls.filter(call => call.path.includes(`/tenants/${tenantB}/management/customers`));
  assert.equal(reads.length, 1, 'the old debounce must not query the new company');
  assert.equal(new URL(reads[0].path, 'https://orion.test').searchParams.has('q'), false);
});

test('AI retry reuses its key only for unchanged content', async t => {
  const h = await makeSuite({ initialRoute: 'consultor', respond: (path, method) => {
    if (method === 'POST') throw new Error('Provedor temporariamente indisponível.');
    return defaultResponse(path);
  } }); t.after(() => h.close()); await flush();
  h.fill('#osConsultorQuestion', 'Como organizar minha agenda?');
  h.submit('[data-os-form=ai-consultant]'); await flush();
  h.submit('[data-os-form=ai-consultant]'); await flush();
  h.fill('#osConsultorQuestion', 'Qual serviço teve mais atendimentos?');
  h.submit('[data-os-form=ai-consultant]'); await flush();
  const writes = h.calls.filter(call => call.method === 'POST');
  assert.equal(writes.length, 3);
  assert.equal(writes[0].data.idempotency_key, writes[1].data.idempotency_key);
  assert.notEqual(writes[2].data.idempotency_key, writes[1].data.idempotency_key);
  assert.equal(writes[2].data.question, 'Qual serviço teve mais atendimentos?');
});

test('a late-night customer visit is displayed on the date in the company timezone', async t => {
  const h = await makeSuite({ respond: path => path.includes('/management/customers') ? { items: [{ ...customer(), last_visit_at: '2030-04-06T01:00:00Z' }], total: 1 } : defaultResponse(path) }); t.after(() => h.close()); await flush();
  assert.match(h.document.getElementById('page-suite').textContent, /05\/04\/2030/);
  assert.doesNotMatch(h.document.getElementById('page-suite').textContent, /06\/04\/2030/);
});
