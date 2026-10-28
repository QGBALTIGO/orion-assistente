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
  const route = new URL(map.href);
  assert.equal(route.origin, 'https://www.google.com');
  assert.equal(route.pathname, '/maps/dir/');
  assert.equal(route.searchParams.get('api'), '1');
  assert.equal(route.searchParams.get('dir_action'), 'navigate');
  assert.match(route.searchParams.get('destination'), /Endereço sintético, São Paulo, SP/);
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

test('public wizard confirms a combo through the explicit button after collecting birth date', async t => {
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
  assert.equal(h.document.querySelector('[name=confirmed]'),null);
  assert.equal(h.document.getElementById('booking-total').hidden,true);
  h.fill('[name=customer_birth_date]', '1995-08-20');
  h.click('#booking-next'); h.click('#booking-next'); await flush();
  const posts = h.calls.filter(call => call.options.method === 'POST');
  assert.equal(posts.length, 1);
  assert.equal(posts[0].url, `${publicBase}/appointments`);
  assert.deepEqual(posts[0].body, { quote, start_at: firstStart, customer_name: 'Cliente sintético', customer_phone: '+5511999999999', customer_birth_date: '1995-08-20', confirmed: true });
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

test('serviço variável preserva A partir de sem aviso redundante na confirmação e no comprovante',async t=>{
  const data=catalogue();
  data.services[0].price_mode='from';
  const h=await makePublic({data,respond: url=>{
    if(url.includes('/availability?'))return response({...available(),price_mode:'from'});
    if(url.endsWith('/appointments'))return response({
      status:'confirmed',protocol:'SYNTHETIC-VARIABLE',start_at:firstStart,
      end_at:'2030-01-07T09:50:00-03:00',price_cents:5200,price_mode:'from',
      professional:'Profissional sintético',services:['Corte + barba']
    },201);
  }});t.after(()=>h.close());await flush();
  assert.match(h.document.querySelector('#services-grid .service-card').textContent,/A partir de R\$\s*45,00/);
  h.click('#services-grid .service-card');
  assert.match(h.document.querySelector('#booking-body .service-card').textContent,/A partir de R\$\s*45,00/);
  h.click('#booking-next');await flush();
  assert.match(h.document.getElementById('booking-total').textContent,/A partir de/);
  h.click('#time-slots .slot');h.click('#booking-next');
  const assertCleanSummary=()=>{
    const card=h.document.querySelector('.review-card');
    assert.match(card.textContent,/Valor mínimo previsto/);
    assert.match(card.textContent,/A partir de R\$\s*52,00/);
    assert.equal(card.querySelector('.price-disclaimer'),null);
    assert.doesNotMatch(card.textContent,/O valor exibido é inicial|preço final depende da avaliação|Nenhum pagamento é cobrado/);
  };
  assertCleanSummary();
  assert.equal(h.calls.filter(x=>x.options.method==='POST').length,0);
  h.fill('[name=customer_name]','Cliente sintético');
  h.fill('[name=customer_phone]','86972676282');
  h.fill('[name=customer_birth_date]','1995-08-20');
  h.click('#booking-next');await flush();
  assert.equal(h.calls.filter(x=>x.options.method==='POST').length,1);
  assert.ok(h.document.querySelector('.success'));
  assert.match(h.document.querySelector('.success').textContent,/SYNTHETIC-VARIABLE/);
  assertCleanSummary();
  assert.equal(h.errors.length,0);
});


test('card público mostra primeiro o preço, depois • e a duração compacta', async t => {
  const h = await makePublic(); t.after(() => h.close()); await flush();
  const card = h.document.querySelector('#services-grid .service-card');
  const meta = card.querySelector('.service-meta');
  const children = [...meta.children];
  assert.deepEqual(children.map(node => node.className), ['price','service-meta-separator','service-duration']);
  assert.match(children[0].textContent, /R\$\s*45,00/);
  assert.equal(children[1].textContent, '•');
  assert.equal(children[1].getAttribute('aria-hidden'), 'true');
  assert.equal(children[2].textContent, '50min');
  assert.match(meta.textContent, /R\$\s*45,00•50min/);
  card.click(); await flush();
  const selected = h.document.querySelector('#booking-body .service-card .service-meta');
  assert.deepEqual([...selected.children].map(node => node.className), ['price','service-meta-separator','service-duration']);
  assert.equal(selected.querySelector('.service-duration').textContent, '50min');
  assert.equal(h.errors.length,0);
});

test('serviço com valor variável mantém A partir de e duração de 1h 30min depois do preço', async t => {
  const data = catalogue();
  data.services[0].price_mode = 'from';
  data.services[0].duration_min = 90;
  data.services[0].price_cents = 14000;
  const h = await makePublic({ data }); t.after(() => h.close()); await flush();
  const meta = h.document.querySelector('#services-grid .service-card .service-meta');
  assert.match(meta.querySelector('.price').textContent,/A partir de R\$\s*140,00/);
  assert.equal(meta.querySelector('.service-meta-separator').textContent,'•');
  assert.equal(meta.querySelector('.service-duration').textContent,'1h 30min');
  assert.match(meta.textContent,/A partir de R\$\s*140,00•1h 30min/);
  assert.equal(h.errors.length,0);
});

test('ocultação de preços mostra apenas duração, sem marcador solto', async t => {
  const data = catalogue(); data.site.show_prices = false;
  const h = await makePublic({ data }); t.after(() => h.close()); await flush();
  const meta = h.document.querySelector('#services-grid .service-card .service-meta');
  assert.equal(meta.textContent,'50min');
  assert.equal(meta.querySelector('.price'), null);
  assert.equal(meta.querySelector('.service-meta-separator'), null);
  assert.equal(meta.querySelector('.service-duration').textContent,'50min');
  assert.equal(h.errors.length,0);
});
