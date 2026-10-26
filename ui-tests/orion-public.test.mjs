import test from 'node:test';
import assert from 'node:assert/strict';
import { deferred, flush, tenantA } from './harness.mjs';
import { makePublic, catalogue, available, response, publicBase, publicService, publicAddon, publicProfessional, firstStart, quote } from './public-harness.mjs';

test('public configuration renders as text, accepts HTTPS links, and filters the catalogue', async t => {
  const data = catalogue();
  data.site.title = '<img src=x onerror=alert(1)>';
  data.site.instagram_url = 'javascript:alert(1)';
  data.services[0].name = '<script>alert(1)</script>';
  const h = await makePublic({ data }); t.after(() => h.close()); await flush();
  assert.equal(h.document.getElementById('site-title').textContent, data.site.title);
  assert.equal(h.document.querySelector('#site-title img'), null);
  assert.equal(h.document.querySelector('#services-grid script'), null);
  assert.equal(h.document.querySelector('a[href^="javascript:"]'), null);
  const map = h.document.getElementById('maps-link');
  assert.equal(map.href, 'https://www.google.com/maps');
  assert.equal(map.rel, 'noopener noreferrer');
  h.fill('#service-search', 'não existe');
  assert.match(h.document.getElementById('services-grid').textContent, /Nenhum serviço encontrado/);
  assert.equal(h.calls.length, 1);
  assert.equal(h.calls[0].options.credentials, 'omit');
  assert.equal(h.errors.length, 0);
});

test('authenticated preview cannot query availability or submit a public reservation', async t => {
  const h = await makePublic({ path: `/app/site-preview/${tenantA}` }); t.after(() => h.close()); await flush();
  assert.equal(h.document.getElementById('preview-banner').hidden, false);
  assert.equal(h.calls[0].url, `/api/v1/tenants/${tenantA}/management/site/preview`);
  assert.equal(h.calls[0].options.credentials, 'same-origin');
  h.click('#services-grid .service-card'); h.click('#booking-next'); await flush();
  assert.match(h.document.getElementById('time-slots').textContent, /Esta é uma prévia/);
  assert.equal(h.document.getElementById('booking-next').disabled, true);
  h.click('#booking-next'); h.click('#share-site'); await flush();
  assert.match(h.document.getElementById('share-message').textContent, /Publique o site/);
  assert.equal(h.calls.length, 1);
});

test('public wizard confirms a combo with an untimed addon only after explicit consent', async t => {
  const pendingBooking = deferred();
  const h = await makePublic({ respond: url => url.endsWith('/appointments') ? pendingBooking.promise : undefined });
  t.after(() => h.close()); await flush();
  h.click('#services-grid .service-card'); h.click('.addon-option input');
  assert.match(h.document.getElementById('booking-total').textContent, /50 min/);
  assert.match(h.document.getElementById('booking-total').textContent, /52,00/);
  h.click('#booking-next'); await flush();
  const availabilityCall = h.calls.find(call => call.url.includes('/availability?'));
  const params = new URL(availabilityCall.url, 'https://orion.test').searchParams;
  assert.equal(params.get('service_id'), publicService); assert.equal(params.get('professional_id'), publicProfessional);
  assert.deepEqual(params.getAll('addon_ids'), [publicAddon]);
  h.click('#time-slots .slot'); h.click('#booking-next');
  h.fill('[name=customer_name]', ' Cliente sintético '); h.fill('[name=customer_phone]', '(11) 99999-9999');
  h.document.getElementById('customer-form').dispatchEvent(new h.window.Event('submit', { bubbles: true, cancelable: true })); await flush();
  assert.equal(h.calls.some(call => call.options.method === 'POST'), false);
  h.click('[name=confirmed]'); h.click('#booking-next'); h.click('#booking-next'); await flush();
  const posts = h.calls.filter(call => call.options.method === 'POST');
  assert.equal(posts.length, 1);
  assert.equal(posts[0].url, `${publicBase}/appointments`);
  assert.deepEqual(posts[0].body, { quote, start_at: firstStart, customer_name: 'Cliente sintético', customer_phone: '(11) 99999-9999', confirmed: true });
  assert.equal(h.document.getElementById('close-booking').disabled, true);
  pendingBooking.resolve(response({ status: 'confirmed', protocol: 'SYNTHETIC-1', start_at: firstStart,
    end_at: '2030-01-07T09:50:00-03:00', price_cents: 5200, professional: 'Profissional sintético', services: ['Corte + barba', 'Lavagem'] }, 201));
  await flush();
  assert.match(h.document.getElementById('booking-body').textContent, /Agendamento confirmado!/);
  assert.match(h.document.getElementById('booking-body').textContent, /SYNTHETIC-1/);
  assert.equal(h.document.querySelector('[name=customer_phone]'), null);
  assert.ok(h.calls.every(call => call.options.credentials === 'omit'));
  assert.equal(h.errors.length, 0);
});

test('a late availability response cannot replace slots for the newly selected day', async t => {
  const old = deferred();
  const h = await makePublic({ respond: url => {
    if (!url.includes('/availability?')) return undefined;
    return url.includes('day=2030-01-07') ? old.promise : response(available('2030-01-08T10:00:00-03:00', `${quote}-new`));
  } }); t.after(() => h.close()); await flush();
  h.click('#services-grid .service-card'); h.click('#booking-next'); await flush();
  h.click('.day-button:nth-child(2)'); await flush();
  assert.match(h.document.getElementById('time-slots').textContent, /10:00/);
  old.resolve(response(available())); await flush();
  assert.doesNotMatch(h.document.getElementById('time-slots').textContent, /09:00/);
  assert.match(h.document.getElementById('time-slots').textContent, /10:00/);
  assert.equal(h.calls.filter(call => call.url.includes('/availability?')).length, 2);
});

test('serviço variável mostra o preço mínimo no site, no resumo e na confirmação',async t=>{
  const data=catalogue();
  data.services[0].price_mode='from';
  const h=await makePublic({data,respond: url=>{
    if(url.includes('/availability?'))return response({...available(),price_mode:'from'});
  }});t.after(()=>h.close());await flush();
  assert.match(h.document.querySelector('#services-grid .service-card').textContent,/A partir de R\$\s*45,00/);
  h.click('#services-grid .service-card');
  assert.match(h.document.querySelector('#booking-body .service-card').textContent,/A partir de R\$\s*45,00/);
  h.click('#booking-next');await flush();
  assert.match(h.document.getElementById('booking-total').textContent,/A partir de/);
  h.click('#time-slots .slot');h.click('#booking-next');
  assert.match(h.document.querySelector('.review-card').textContent,/Valor mínimo previsto/);
  assert.match(h.document.querySelector('.review-card').textContent,/preço final depende da avaliação/);
  assert.equal(h.calls.filter(x=>x.options.method==='POST').length,0);
  assert.equal(h.errors.length,0);
});
