import test from 'node:test';
import assert from 'node:assert/strict';
import { flush } from './harness.mjs';
import { makePublic, catalogue, response } from './public-harness.mjs';

const token = 'demo_Test-123';
const demoPath = `/demo/${token}`;
const demoAPI = `/api/v1/public/demos/${token}`;
function demoCatalogue() {
  return { ...catalogue(), demo: true, preview: true, booking_enabled: false };
}
async function fixture(t, data = demoCatalogue(), options = {}) {
  const h = await makePublic({ path: demoPath, data, ...options });
  t.after(() => h.close());
  await flush();
  return h;
}
function assertIsolated(h) {
  assert.equal(h.calls.length, 1, 'a simulação só pode ler seu próprio catálogo');
  assert.equal(h.calls[0].url, demoAPI);
  assert.equal(h.calls[0].options.method || 'GET', 'GET');
  assert.equal(h.calls[0].options.credentials, 'omit');
  assert.equal(h.calls[0].body, null);
  assert.equal(h.document.querySelector('#customer-form, [name^="customer_"], [name="appointment_messages_opt_in"]'), null);
  assert.equal(h.errors.length, 0);
}
function advanceToTime(h) {
  h.click('#services-grid .service-card');
  h.click('#booking-next');
  assert.equal(h.document.getElementById('booking-next').disabled, true);
  assert.match(h.document.getElementById('time-slots').textContent, /09:00/);
}

test('demo carrega a rota própria e identifica a apresentação em toda a navegação', async t => {
  const data = demoCatalogue();
  data.site.hide_orion_brand = true;
  const h = await fixture(t, data);
  assert.equal(h.document.getElementById('site-content').hidden, false);
  assert.equal(h.document.getElementById('preview-banner').hidden, false);
  assert.match(h.document.getElementById('preview-banner').textContent,
    /Demonstração personalizada da Orion.*Uso demonstrativo.*Nenhuma reserva real/);
  assert.equal(h.document.getElementById('orion-credit').hidden, false);
  assert.match(h.document.title, /Demonstração Orion/);
  assert.match(h.document.getElementById('hero-book').textContent, /Simular agendamento/);
  assert.match(h.document.getElementById('footer-book').textContent, /Simular agendamento/);
  assert.equal(h.document.querySelector('.online-label').textContent, 'Agenda demonstrativa');
  assertIsolated(h);
});

test('simulação completa não coleta pessoas, não reserva e não abre WhatsApp ou calendário', async t => {
  const h = await fixture(t);
  const opened = [], stored = [];
  h.window.open = (...args) => { opened.push(args); return null; };
  h.window.Storage.prototype.setItem = (...args) => stored.push(args);
  advanceToTime(h);
  assert.equal(h.document.querySelectorAll('.professional-choice').length, 1);
  assert.match(h.document.getElementById('demo-time-notice').textContent, /Não representam a agenda real/);
  assert.equal(h.document.querySelector('.booking-slots-heading').textContent, 'Horários ilustrativos');
  const slots = [...h.document.querySelectorAll('#time-slots .slot')];
  assert.equal(slots.length, 6);
  assert.ok(slots.every(node => /horário ilustrativo/.test(node.getAttribute('aria-label'))));
  h.click('#time-slots .slot');
  assert.equal(h.document.getElementById('booking-next').disabled, false);
  h.click('#booking-next');
  assert.match(h.document.querySelector('.demo-review-card').textContent,
    /Profissional sintético.*Data ilustrativa.*Horário ilustrativo.*09:00/);
  assert.match(h.document.getElementById('booking-body').textContent, /sem informar dados pessoais/);
  assert.equal(h.document.getElementById('booking-next').textContent, 'Concluir simulação');
  assertIsolated(h);
  h.click('#booking-next');
  await flush();
  const result = h.document.querySelector('.demo-success');
  assert.match(result.textContent, /Simulação concluída/);
  assert.match(result.textContent, /Nenhuma reserva foi criada e nenhuma mensagem foi enviada/);
  assert.doesNotMatch(result.textContent, /Agendamento confirmado|Protocolo/);
  assert.equal(result.querySelector('a, [download]'), null);
  assert.deepEqual(opened, []);
  assert.deepEqual(stored, []);
  assert.equal(h.window.localStorage.length, 0);
  assert.equal(h.window.sessionStorage.length, 0);
  assertIsolated(h);
  h.click('#booking-next');
  assert.equal(h.document.getElementById('booking-dialog').open, false);
  advanceToTime(h);
  assert.equal(h.document.querySelector('#time-slots [aria-pressed="true"]'), null);
  assertIsolated(h);
});

test('equipe e fuso ausentes permitem concluir sem inventar profissional ou fuso real', async t => {
  const data = demoCatalogue();
  data.professionals = [];
  data.timezone = null;
  data.services[0].professional_ids = [];
  const h = await fixture(t, data);
  assert.equal(h.document.getElementById('equipe').hidden, true);
  assert.equal(h.document.querySelectorAll('#team-grid .team-card').length, 0);
  h.click('#services-grid .service-card');
  assert.match(h.document.getElementById('booking-body').textContent, /Equipe a confirmar/);
  assert.equal(h.document.getElementById('booking-next').disabled, false);
  h.click('#booking-next');
  assert.equal(h.document.querySelector('.professional-picker'), null);
  assert.match(h.document.querySelector('.demo-pending-note').textContent, /Equipe a confirmar/);
  h.click('#time-slots .slot'); h.click('#booking-next'); h.click('#booking-next');
  assert.match(h.document.querySelector('.demo-success').textContent, /Equipe a confirmar/);
  assert.doesNotMatch(h.document.querySelector('.demo-success').textContent, /Profissional sintético/);
  assertIsolated(h);
});

test('preço e duração desconhecidos não viram zero nem totais parciais', async t => {
  const data = demoCatalogue();
  data.services.push({ id: 'unknown-service', name: 'Serviço com valor a confirmar', category: 'Serviços',
    catalog_kind: 'service', price_cents: null, duration_min: null, professional_ids: [], addons: [] });
  const h = await fixture(t, data);
  const card = h.document.querySelectorAll('#services-grid .service-card')[1];
  assert.match(card.textContent, /Valor a confirmar/);
  assert.equal(card.querySelector('.service-duration, .service-meta-separator'), null);
  h.click('#services-grid .service-card');
  h.click('#booking-service-list [data-service-id="unknown-service"]');
  assert.equal(h.document.getElementById('booking-total').textContent, 'Valor a confirmar');
  assert.doesNotMatch(h.document.getElementById('booking-body').textContent, /Tempo total:/);
  h.click('#booking-next'); h.click('#time-slots .slot'); h.click('#booking-next');
  const review = h.document.querySelector('.demo-review-card');
  assert.match(review.textContent, /Corte \+ barba \+ Serviço com valor a confirmar/);
  assert.match(review.textContent, /Valor a confirmar/);
  assert.doesNotMatch(review.textContent, /Duração publicada|R\$\s*0,00|0 min/);
  assertIsolated(h);
});

test('catálogo vazio mantém somente navegação, sem fabricar serviços ou abrir a simulação', async t => {
  const data = demoCatalogue(); data.services = []; data.professionals = [];
  const h = await fixture(t, data);
  assert.match(h.document.getElementById('services-grid').textContent, /Catálogo a confirmar/);
  assert.equal(h.document.querySelector('.service-card'), null);
  for (const id of ['hero-book', 'footer-book']) {
    assert.equal(h.document.getElementById(id).disabled, true);
    h.click('#' + id);
  }
  assert.equal(h.document.getElementById('booking-dialog').open, false);
  assertIsolated(h);
});

test('troca de profissional e data permanece local e invalida o horário anterior', async t => {
  const data = demoCatalogue();
  data.services[0].professional_ids = [];
  data.professionals.push({ id: 'second-person', name: 'Outra pessoa publicada', color: '#223344' });
  const h = await fixture(t, data);
  advanceToTime(h);
  h.click('#time-slots .slot');
  h.click('.professional-choice[data-professional-id="second-person"]');
  assert.equal(h.document.getElementById('booking-next').disabled, true);
  assert.equal(h.document.querySelector('#time-slots [aria-pressed="true"]'), null);
  h.click('#time-slots .slot');
  h.click('.day-button:nth-child(2)');
  assert.equal(h.document.getElementById('booking-day-input').value, '2030-01-08');
  assert.equal(h.document.getElementById('booking-next').disabled, true);
  h.click('#time-slots .slot'); h.click('#booking-next');
  assert.match(h.document.querySelector('.demo-review-card').textContent, /Outra pessoa publicada/);
  h.click('#booking-back');
  assert.equal(h.document.querySelector('.professional-choice[aria-pressed="true"]').dataset.professionalId, 'second-person');
  assert.equal(h.document.querySelector('#time-slots [aria-pressed="true"]').textContent, '09:00');
  assert.equal(h.document.getElementById('booking-next').disabled, false);
  assertIsolated(h);
});

test('horários de expediente parciais distinguem desconhecido de fechado', async t => {
  const data = demoCatalogue();
  data.opening_hours = [{ weekday: 0, intervals: [{ open: '08:30', close: '18:30' }] },
    { weekday: 1, intervals: null }, { weekday: 6, intervals: [] }];
  const h = await fixture(t, data);
  const rows = [...h.document.querySelectorAll('#hours-list .hours-row')].map(node => node.textContent);
  assert.deepEqual(rows, ['Segunda-feira08:30–18:30', 'Terça-feira a sábadoA confirmar', 'DomingoFechado']);
  assert.match(h.document.querySelector('#hours-card .hours-note').textContent, /disponibilidade real.*confirmados/);
  assertIsolated(h);
});

test('demo recusa catálogo que permita reservas e erro HTTP não dispara outro endpoint', async t => {
  const data = demoCatalogue(); data.booking_enabled = true;
  const unsafe = await fixture(t, data);
  assert.equal(unsafe.document.getElementById('site-content').hidden, true);
  assert.match(unsafe.document.getElementById('page-message').textContent, /demonstração não está disponível/);
  assertIsolated(unsafe);
  const expired = await fixture(t, demoCatalogue(), { respond: () => response({ detail: 'Não encontrado.' }, 404) });
  assert.equal(expired.document.getElementById('site-content').hidden, true);
  assert.match(expired.document.getElementById('page-message').textContent, /Página indisponível/);
  assert.match(expired.document.getElementById('page-message').textContent, /demonstração expirou/);
  assertIsolated(expired);
});
