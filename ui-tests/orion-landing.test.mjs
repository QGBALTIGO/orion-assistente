import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import { JSDOM, VirtualConsole } from 'jsdom';

const htmlURL = new URL('../landing/index.html', import.meta.url);
const scriptURL = new URL('../landing/assets/orion-landing.js', import.meta.url);
const endpoint = '/api/public/sales-requests';
const receiptId = '70000000-0000-4000-8000-000000000007';
const flush = async () => { for (let i = 0; i < 4; i++) await new Promise(resolve => setImmediate(resolve)); };
const response = (status = 201, body = { ok: true, request_id: receiptId }) => ({ status, json: async () => body });
function deferred() { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }

// Execute the real HTML and script in a DOM. Images, stylesheets and scripts are
// not downloaded. Timers, fetch, media and native dialog methods are controlled
// locally; this harness does not render a viewport or prove visual appearance.
async function makeLanding({ motion = false, saveData = false, width = 390, run = true, respond = () => response() } = {}) {
  const virtualConsole = new VirtualConsole(), errors = [];
  virtualConsole.on('jsdomError', error => errors.push(error));
  const dom = new JSDOM(await readFile(htmlURL, 'utf8'), { url: 'https://orion.test/', runScripts: 'outside-only', virtualConsole });
  const { window } = dom, { document } = window;
  Object.defineProperty(window, 'innerWidth', { value: width, writable: true, configurable: true });
  window.scrollTo = () => {};
  window.HTMLElement.prototype.scrollIntoView = () => {};
  const listeners = new Set();
  const media = { matches: motion, media: '(prefers-reduced-motion: reduce)', addEventListener(type, listener) { if (type === 'change') listeners.add(listener); }, removeEventListener(type, listener) { listeners.delete(listener); } };
  window.matchMedia = () => media;
  Object.defineProperty(window.navigator, 'connection', { value: { saveData }, configurable: true });
  Object.defineProperty(window.crypto, 'randomUUID', { value: randomUUID, configurable: true });
  let hidden = false;
  Object.defineProperty(document, 'hidden', { get: () => hidden, configurable: true });
  const timers = new Map(); let now = 0, nextTimer = 1;
  const schedule = (callback, delay, interval, args = []) => {
    const id = nextTimer++, duration = Math.max(1, Number(delay) || 0);
    timers.set(id, { callback, args, due: now + duration, interval: interval ? duration : 0 }); return id;
  };
  window.setTimeout = (callback, delay, ...args) => schedule(callback, delay, false, args);
  window.setInterval = (callback, delay, ...args) => schedule(callback, delay, true, args);
  window.clearTimeout = window.clearInterval = id => timers.delete(id);
  window.requestAnimationFrame = callback => schedule(() => callback(now), 16, false);
  window.cancelAnimationFrame = id => timers.delete(id);
  const observers = [];
  window.IntersectionObserver = class {
    constructor(callback) { this.callback = callback; this.targets = new Set(); observers.push(this); }
    observe(target) { this.targets.add(target); }
    disconnect() { this.targets.clear(); }
  };
  window.HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute('open', ''); this.querySelector('button')?.focus();
  };
  window.HTMLDialogElement.prototype.close = function () {
    this.removeAttribute('open'); this.dispatchEvent(new window.Event('close'));
  };
  let playing = false; const playback = { play: 0, pause: 0 };
  Object.defineProperty(window.HTMLMediaElement.prototype, 'paused', { get: () => !playing, configurable: true });
  Object.defineProperty(window.HTMLMediaElement.prototype, 'ended', { get: () => false, configurable: true });
  window.HTMLMediaElement.prototype.play = async function () { playback.play++; playing = true; this.dispatchEvent(new window.Event('play')); };
  window.HTMLMediaElement.prototype.pause = function () { playback.pause++; playing = false; this.dispatchEvent(new window.Event('pause')); };
  const calls = [];
  window.fetch = async (path, options = {}) => { calls.push({ path, options }); return respond(path, options, window); };
  if (run) {
    window.eval(await readFile(new URL('../app/static/orion-fields.js', import.meta.url), 'utf8'));
    window.eval(await readFile(scriptURL, 'utf8'));
  }
  const element = selector => { const found = document.querySelector(selector); assert.ok(found, `Missing element ${selector}`); return found; };
  return {
    dom, window, document, calls, errors, playback, media,
    element,
    click(selector) { const found = element(selector); found.click(); return found; },
    fill(selector, value) { const found = element(selector); found.value = value; found.dispatchEvent(new window.Event('input', { bubbles: true })); return found; },
    key(selector, key) { const found = element(selector); found.focus(); const event = new window.KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }); found.dispatchEvent(event); return event; },
    submit() { element('#sales-request-form').dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true })); },
    async tick(duration) {
      const end = now + duration; let count = 0;
      while (true) {
        const next = [...timers].filter(([, timer]) => timer.due <= end).sort((a, b) => a[1].due - b[1].due || a[0] - b[0])[0];
        if (!next) break;
        assert.ok(count++ < 1000, 'Synthetic clock must not loop indefinitely');
        const [id, timer] = next; now = timer.due;
        if (timer.interval) timer.due += timer.interval; else timers.delete(id);
        timer.callback(...timer.args); await flush();
      }
      now = end; await flush();
    },
    setMotion(value) { media.matches = value; listeners.forEach(listener => listener({ matches: value })); },
    setHidden(value) { hidden = value; document.dispatchEvent(new window.Event('visibilitychange')); },
    intersect(selector, isIntersecting) { const target = element(selector); observers.filter(observer => observer.targets.has(target)).forEach(observer => observer.callback([{ target, isIntersecting }])); },
    close() { window.dispatchEvent(new window.Event('pagehide')); timers.clear(); dom.window.close(); },
  };
}

function completeForm(h, extra = {}) {
  const values = { name: 'Contato Sintético', email: 'contato@example.invalid', phone: '(11) 91234-5678', segment: 'odontologia', intent: 'trial', ...extra };
  for (const [key, value] of Object.entries(values)) h.fill(`#lead-${key}`, value);
  h.element('[name=consent]').checked = true;
  return values;
}
function assertPreserved(h, values) {
  for (const [key, value] of Object.entries(values)) assert.equal(h.element(`#lead-${key}`).value, value, `${key} must remain after an unconfirmed request`);
  assert.equal(h.element('[name=consent]').checked, true);
  assert.equal(h.element('#sales-request-form').hidden, false);
  assert.equal(h.element('#lead-success').hidden, true);
  assert.equal(h.element('.form-submit').disabled, false);
  assert.equal(h.window.localStorage.length, 0);
  assert.equal(h.window.sessionStorage.length, 0);
}
function assertActiveTab(h, tab, selector, panels) {
  const tabs = [...h.document.querySelectorAll(selector)];
  assert.equal(tabs.filter(item => item.getAttribute('aria-selected') === 'true').length, 1);
  for (const item of tabs) {
    assert.equal(item.getAttribute('aria-selected'), String(item === tab));
    assert.equal(item.tabIndex, item === tab ? 0 : -1);
    assert.ok(h.document.getElementById(item.getAttribute('aria-controls')));
  }
  const panel = h.document.getElementById(tab.getAttribute('aria-controls'));
  assert.equal(panel.getAttribute('role'), 'tabpanel');
  assert.equal(panel.getAttribute('aria-labelledby'), tab.id);
  assert.equal(panel.hidden, false);
  if (panels) assert.equal([...h.document.querySelectorAll(panels)].filter(item => !item.hidden).length, 1);
}

test('the landing initializes locally, keeps the no-script submit disabled and does not send API requests while browsing', async t => {
  const staticPage = await makeLanding({ run: false }); t.after(() => staticPage.close());
  assert.equal(staticPage.element('.form-submit').disabled, true, 'native fallback must not submit without the idempotency flow');
  const h = await makeLanding(); t.after(() => h.close());
  assert.equal(h.element('.form-submit').disabled, false);
  assert.equal(h.document.querySelectorAll('h1').length, 1);
  assert.equal(h.document.documentElement.lang, 'pt-BR');
  for (const link of h.document.querySelectorAll('a[href^="#"]')) assert.ok(h.document.getElementById(link.hash.slice(1)), `Anchor ${link.hash} needs a destination`);
  for (const field of h.document.querySelectorAll('.form-field input,.form-field select')) assert.ok(h.document.querySelector(`label[for="${field.id}"]`), `${field.id} needs a label`);
  await h.tick(18000);
  h.click('[data-demo-tab=clientes]'); h.click('[data-sector=restaurante]'); h.click('.menu-toggle');
  assert.equal(h.calls.length, 0);
  assert.equal(h.playback.play, 0);
  assert.equal(h.window.localStorage.length, 0);
  assert.equal(h.errors.length, 0);
});

test('product tabs expose the selected panel, mouse activation and their ARIA relationships', async t => {
  const h = await makeLanding(); t.after(() => h.close());
  const tabs = [...h.document.querySelectorAll('[data-demo-tab]')];
  for (const tab of tabs) { tab.click(); assertActiveTab(h, tab, '[data-demo-tab]', '.demo-scene'); }
  assert.equal(h.calls.length, 0);
});

test('product tabs support arrows, Home, End and the next-step controls with focus', async t => {
  const h = await makeLanding(); t.after(() => h.close());
  const tabs = [...h.document.querySelectorAll('[data-demo-tab]')];
  const keys = [['ArrowLeft', tabs.at(-1)], ['Home', tabs[0]], ['ArrowRight', tabs[1]], ['End', tabs.at(-1)], ['ArrowRight', tabs[0]]];
  let current = tabs[0];
  for (const [key, next] of keys) {
    const event = h.key(`#${current.id}`, key);
    assert.equal(event.defaultPrevented, true);
    assertActiveTab(h, next, '[data-demo-tab]', '.demo-scene');
    assert.equal(h.document.activeElement, next); current = next;
  }
  for (const next of h.document.querySelectorAll('[data-show-demo]')) {
    next.click(); const tab = h.element(`[data-demo-tab="${next.dataset.showDemo}"]`);
    assertActiveTab(h, tab, '[data-demo-tab]', '.demo-scene');
    assert.equal(h.document.activeElement, tab);
  }
});

test('business tabs update meaningful content, image alternatives, panel labels and keyboard focus', async t => {
  const h = await makeLanding(); t.after(() => h.close());
  const tabs = [...h.document.querySelectorAll('[data-sector]')], descriptions = new Set();
  assert.ok(tabs.some(tab => tab.dataset.sector === 'odontologia'));
  for (const tab of tabs) {
    tab.click(); assertActiveTab(h, tab, '[data-sector]');
    assert.equal(h.element('#sector-panel').dataset.segment, tab.dataset.sector);
    assert.match(h.element('#sector-photo').src, new RegExp(`/negocio-${tab.dataset.sector}\\.webp$`));
    assert.ok(h.element('#sector-photo').alt.length > 20);
    assert.ok(h.element('#sector-title').textContent.trim());
    assert.equal(h.element('#sector-use-cases').children.length, 3);
    descriptions.add(h.element('#sector-description').textContent);
  }
  assert.equal(descriptions.size, tabs.length);
  h.key(`#${tabs.at(-1).id}`, 'ArrowRight'); assert.equal(h.document.activeElement, tabs[0]);
  h.key(`#${tabs[0].id}`, 'End'); assert.equal(h.document.activeElement, tabs.at(-1));
  h.key(`#${tabs.at(-1).id}`, 'Home'); assert.equal(h.document.activeElement, tabs[0]);
  assert.equal(h.calls.length, 0);
});

test('business and plan calls to action preselect the corresponding segment and intent without submitting', async t => {
  const h = await makeLanding(); t.after(() => h.close());
  for (const tab of h.document.querySelectorAll('[data-sector]')) {
    tab.click(); h.click('#sector-cta');
    assert.equal(h.element('#lead-segment').value, tab.dataset.sector);
    assert.equal(h.element('#lead-intent').value, h.element('#sector-cta').dataset.intent);
  }
  const intents = new Set();
  for (const link of h.document.querySelectorAll('[data-intent]')) {
    link.click(); intents.add(link.dataset.intent);
    assert.equal(h.element('#lead-intent').value, link.dataset.intent);
  }
  for (const expected of ['trial', 'essencial', 'profissional', 'escala']) assert.ok(intents.has(expected));
  assert.equal(h.calls.length, 0);
});

test('hero sectors keep grammatical text, matching photography, state and a stable accessible heading', async t => {
  const h = await makeLanding(); t.after(() => h.close());
  const expected = { barbearia: 'sua barbearia.', estetica: 'sua clínica.', odontologia: 'seu consultório.', restaurante: 'seu restaurante.', fitness: 'seu estúdio.' };
  const buttons = [...h.document.querySelectorAll('[data-hero-sector]')];
  assert.ok(buttons.some(button => button.dataset.heroSector === 'odontologia'));
  for (const button of buttons) {
    button.click(); const key = button.dataset.heroSector;
    assert.equal(h.element('#hero-word').textContent, expected[key]);
    assert.match(h.element('#hero-photo').src, new RegExp(`/negocio-${key}\\.webp$`));
    assert.ok(h.element('#hero-photo').alt.length > 20);
    assert.equal(h.element('.hero-visual').dataset.sector, key);
    assert.equal(buttons.filter(item => item.getAttribute('aria-pressed') === 'true').length, 1);
    assert.equal(button.getAttribute('aria-pressed'), 'true');
    assert.match(h.element('.hero-image-index').textContent, new RegExp(`/ 0?${buttons.length}$`));
  }
  assert.equal(h.element('.rotating-line').getAttribute('aria-hidden'), 'true');
  assert.match(h.element('#hero-title .sr-only').textContent, /seu negócio/);
  assert.equal(h.element('#hero-title').hasAttribute('aria-live'), false);
  assert.equal(h.element('.motion-toggle').getAttribute('aria-pressed'), 'true', 'manual selection pauses automatic changes');
});

test('hero rotation can pause and resume, and stays paused after a manual selection', async t => {
  const h = await makeLanding(); t.after(() => h.close());
  const first = h.element('#hero-word').textContent;
  await h.tick(5600); assert.notEqual(h.element('#hero-word').textContent, first);
  h.click('.motion-toggle'); const paused = h.element('#hero-word').textContent;
  assert.equal(h.element('.motion-toggle').getAttribute('aria-pressed'), 'true');
  assert.match(h.element('.motion-toggle').getAttribute('aria-label'), /Retomar/);
  await h.tick(16800); assert.equal(h.element('#hero-word').textContent, paused);
  h.click('.motion-toggle'); await h.tick(5600); assert.notEqual(h.element('#hero-word').textContent, paused);
  h.click('[data-hero-sector=restaurante]'); const selected = h.element('#hero-word').textContent;
  await h.tick(16800); assert.equal(h.element('#hero-word').textContent, selected);
  assert.equal(h.calls.length, 0);
});

test('reduced motion, page visibility, viewport visibility and data saving stop automatic hero changes', async t => {
  const h = await makeLanding({ motion: true }); t.after(() => h.close());
  const initial = h.element('#hero-word').textContent;
  assert.equal(h.element('.motion-toggle').disabled, true);
  await h.tick(18000); assert.equal(h.element('#hero-word').textContent, initial);
  h.click('[data-demo-tab=agenda]'); assert.equal(h.element('#demo-agenda').classList.contains('scene-enter'), false);
  h.setMotion(false); assert.equal(h.element('.motion-toggle').disabled, false);
  await h.tick(5600); assert.notEqual(h.element('#hero-word').textContent, initial);
  h.setHidden(true); const hidden = h.element('#hero-word').textContent;
  await h.tick(11200); assert.equal(h.element('#hero-word').textContent, hidden);
  h.setHidden(false); h.intersect('.hero', false);
  await h.tick(11200); assert.equal(h.element('#hero-word').textContent, hidden);
  h.intersect('.hero', true); await h.tick(5600); assert.notEqual(h.element('#hero-word').textContent, hidden);
  const saver = await makeLanding({ saveData: true }); t.after(() => saver.close());
  const saved = saver.element('#hero-word').textContent;
  await saver.tick(18000); assert.equal(saver.element('#hero-word').textContent, saved);
  assert.equal(h.calls.length + saver.calls.length, 0);
});

test('the mobile menu closes through Escape, anchors and desktop resizing, restoring its state', async t => {
  const h = await makeLanding(); t.after(() => h.close());
  const menu = h.element('#mobile-nav'), toggle = h.element('.menu-toggle');
  assert.equal(menu.hidden, true);
  h.click('.menu-toggle'); assert.equal(menu.hidden, false); assert.equal(toggle.getAttribute('aria-expanded'), 'true');
  assert.equal(h.document.body.classList.contains('menu-open'), true);
  h.key('#mobile-nav a', 'Escape');
  assert.equal(menu.hidden, true); assert.equal(h.document.activeElement, toggle);
  assert.equal(toggle.getAttribute('aria-expanded'), 'false');
  assert.equal(h.document.body.classList.contains('menu-open'), false);
  for (const link of menu.querySelectorAll('a[href^="#"]')) { toggle.click(); link.click(); assert.equal(menu.hidden, true); }
  toggle.click(); h.window.innerWidth = 1200; h.window.dispatchEvent(new h.window.Event('resize'));
  assert.equal(menu.hidden, true); assert.equal(toggle.getAttribute('aria-label'), 'Abrir menu');
  assert.equal(h.calls.length, 0);
});

test('privacy opens by explicit request, does not grant consent and returns focus when closed', async t => {
  const h = await makeLanding(); t.after(() => h.close());
  const dialog = h.element('#privacy-dialog'), consent = h.element('[name=consent]');
  assert.equal(dialog.open, false); assert.equal(consent.checked, false);
  const triggers = [...h.document.querySelectorAll('.privacy-trigger')];
  for (const trigger of triggers) {
    trigger.focus(); trigger.click(); assert.equal(dialog.open, true);
    assert.equal(consent.checked, false);
    assert.ok(h.document.getElementById(dialog.getAttribute('aria-labelledby')));
    h.click('#privacy-dialog p'); assert.equal(dialog.open, true);
    h.click('.dialog-close-button'); assert.equal(dialog.open, false); assert.equal(h.document.activeElement, trigger);
  }
  triggers[0].click(); dialog.click(); assert.equal(dialog.open, false); assert.equal(h.document.activeElement, triggers[0]);
  assert.equal(h.calls.length, 0);
});

test('the optional video waits for a click and pauses when the document becomes hidden', async t => {
  const h = await makeLanding(); t.after(() => h.close());
  const video = h.element('#routine-video');
  assert.equal(video.hasAttribute('autoplay'), false); assert.equal(video.getAttribute('preload'), 'none');
  await h.tick(12000); assert.equal(h.playback.play, 0);
  h.click('.video-control'); await flush();
  assert.equal(h.playback.play, 1); assert.equal(h.element('.video-control').getAttribute('aria-pressed'), 'true');
  h.setHidden(true); assert.equal(h.playback.pause, 1);
  assert.equal(h.element('.video-control').getAttribute('aria-pressed'), 'false');
  assert.equal(h.calls.length, 0);
});

test('required identity, email, segment and consent prevent incomplete API submissions', async t => {
  const h = await makeLanding(); t.after(() => h.close());
  h.submit(); await flush(); assert.equal(h.calls.length, 0);
  completeForm(h); h.element('[name=consent]').checked = false;
  h.submit(); await flush(); assert.equal(h.calls.length, 0);
  h.element('[name=consent]').checked = true; h.fill('#lead-email', 'email inválido');
  h.submit(); await flush(); assert.equal(h.calls.length, 0);
  h.fill('#lead-email', 'contato@example.invalid'); h.fill('#lead-segment', '');
  h.submit(); await flush(); assert.equal(h.calls.length, 0);
  assert.equal(h.element('#sales-request-form').hidden, false);
});

test('a confirmed 201 submits once with an idempotency key, freezes the form and then shows the receipt', async t => {
  const pending = deferred();
  const h = await makeLanding({ respond: () => pending.promise }); t.after(() => h.close());
  const values = completeForm(h, { name: '  Contato Sintético  ', email: 'CONTATO@example.invalid', intent: 'profissional' });
  h.submit(); h.submit(); await flush();
  assert.equal(h.calls.length, 1);
  const { path, options } = h.calls[0];
  assert.equal(path, endpoint); assert.equal(options.method, 'POST');
  assert.match(options.headers['Idempotency-Key'], /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i);
  assert.equal(options.headers['Content-Type'], 'application/json');
  assert.equal(options.credentials, 'omit'); assert.equal(options.cache, 'no-store'); assert.equal(options.redirect, 'error');
  assert.deepEqual(JSON.parse(options.body), { name: values.name.trim(), email: values.email, phone: '+5511912345678', business_name: '', segment: values.segment, intent: values.intent, consent: true, website: '' });
  assert.equal(h.element('.form-submit').disabled, true);
  assert.equal(h.element('#sales-request-form').getAttribute('aria-busy'), 'true');
  for (const field of h.document.querySelectorAll('#sales-request-form input,#sales-request-form select')) assert.equal(field.disabled, true);
  h.click('[data-intent=escala]'); assert.equal(h.element('#lead-intent').value, 'profissional', 'a pending request cannot change through a separate CTA');
  pending.resolve(response()); await flush();
  assert.equal(h.element('#sales-request-form').hidden, true);
  assert.equal(h.element('#lead-success').hidden, false);
  assert.equal(h.document.activeElement, h.element('#lead-success'));
  assert.equal(h.element('#lead-name').value, '');
  assert.equal(h.element('#sales-request-form').hasAttribute('aria-busy'), false);
  assert.equal(h.errors.length, 0);
});

test('the lead form masks a pasted country code without losing DDD55 and preserves email subdomains and case', async t => {
  const h = await makeLanding(); t.after(() => h.close());
  completeForm(h, { name: 'João D’Ávila-Sá', email: 'Contato+Orion@Sub.Example.invalid', phone: '+55 (55) 99123-4567' });
  const phone = h.element('#lead-phone');
  assert.equal(phone.value, '(55) 99123-4567');
  assert.equal(phone.hasAttribute('maxlength'), false);
  assert.equal(phone.inputMode, 'tel');
  h.submit(); await flush();
  assert.equal(h.calls.length, 1);
  const payload = JSON.parse(h.calls[0].options.body);
  assert.equal(payload.phone, '+5555991234567');
  assert.equal(payload.name, 'João D’Ávila-Sá');
  assert.equal(payload.email, 'Contato+Orion@Sub.Example.invalid');
  assert.equal(h.errors.length, 0);
});

test('the lead form blocks a visible excess phone digit and sends null when the optional phone is cleared', async t => {
  const h = await makeLanding(); t.after(() => h.close());
  completeForm(h, { phone: '(55) 99123-45678' });
  h.submit(); await flush();
  assert.equal(h.calls.length, 0);
  const phone = h.element('#lead-phone');
  assert.equal(phone.value, '(55) 99123-45678');
  assert.equal(phone.getAttribute('aria-invalid'), 'true');
  const error = h.document.getElementById(phone.getAttribute('aria-describedby'));
  assert.ok(error);
  assert.equal(error.parentElement, phone.closest('[data-field-wrapper]'));
  assert.equal(h.document.activeElement, phone);
  h.fill('#lead-phone', '');
  assert.equal(phone.hasAttribute('aria-invalid'), false);
  h.submit(); await flush();
  assert.equal(h.calls.length, 1);
  assert.equal(JSON.parse(h.calls[0].options.body).phone, null);
  assert.equal(h.errors.length, 0);
});

test('the lead honeypot stays unmasked and keeps its original contract', async t => {
  const h = await makeLanding(); t.after(() => h.close());
  completeForm(h);
  const trap = h.fill('#lead-website', 'https://example.invalid/55-extra');
  assert.equal(trap.hasAttribute('data-orion-field'), false);
  assert.equal(trap.getAttribute('autocomplete'), 'off');
  assert.equal(trap.tabIndex, -1);
  assert.equal(trap.maxLength, 200);
  assert.equal(trap.closest('[aria-hidden]').getAttribute('aria-hidden'), 'true');
  h.submit(); await flush();
  assert.equal(h.calls.length, 1);
  assert.equal(JSON.parse(h.calls[0].options.body).website, 'https://example.invalid/55-extra');
});

test('required name and segment show associated native errors without changing literal names', async t => {
  const h = await makeLanding(); t.after(() => h.close());
  completeForm(h, { name: '' }); h.submit(); await flush();
  const name = h.element('#lead-name');
  assert.equal(h.calls.length, 0); assert.equal(name.getAttribute('aria-invalid'), 'true');
  assert.equal(h.document.getElementById(name.getAttribute('aria-describedby')).parentElement, name.closest('[data-field-wrapper]'));
  h.fill('#lead-name', "  João D'Ávila-Souza  ");
  assert.equal(name.value, "  João D'Ávila-Souza  ");
  const segment = h.fill('#lead-segment', ''); h.submit(); await flush();
  assert.equal(h.calls.length, 0); assert.equal(segment.getAttribute('aria-invalid'), 'true');
  assert.equal(h.document.getElementById(segment.getAttribute('aria-describedby')).parentElement, segment.closest('[data-field-wrapper]'));
  assert.equal(h.document.activeElement, segment);
  h.fill('#lead-segment', 'barbearia'); h.submit(); await flush();
  assert.equal(h.calls.length, 1);
  assert.equal(JSON.parse(h.calls[0].options.body).name, "João D'Ávila-Souza");
  assert.equal(h.errors.length, 0);
});

test('503 preserves every field, permits an explicit retry and reuses the exact payload and key', async t => {
  let attempt = 0;
  const h = await makeLanding({ respond: () => ++attempt === 1 ? response(503, {}) : response() }); t.after(() => h.close());
  const values = completeForm(h, { intent: 'escala', segment: 'restaurante' });
  h.submit(); await flush(); assertPreserved(h, values);
  assert.match(h.element('#lead-error').textContent, /Seus dados continuam preenchidos/);
  assert.equal(h.document.activeElement, h.element('#lead-error'));
  await h.tick(20000); assert.equal(h.calls.length, 1, 'failure must not trigger an automatic retry');
  h.submit(); await flush();
  assert.equal(h.calls.length, 2);
  assert.equal(h.calls[1].options.headers['Idempotency-Key'], h.calls[0].options.headers['Idempotency-Key']);
  assert.equal(h.calls[1].options.body, h.calls[0].options.body);
  assert.equal(h.element('#lead-success').hidden, false);
});

test('editing a failed request changes its key while keeping the revised input intact', async t => {
  const h = await makeLanding({ respond: () => response(503, {}) }); t.after(() => h.close());
  const values = completeForm(h); h.submit(); await flush();
  h.fill('#lead-email', 'novo-contato@example.invalid'); h.submit(); await flush();
  assert.equal(h.calls.length, 2);
  assert.notEqual(h.calls[1].options.headers['Idempotency-Key'], h.calls[0].options.headers['Idempotency-Key']);
  assert.equal(JSON.parse(h.calls[1].options.body).email, 'novo-contato@example.invalid');
  assertPreserved(h, { ...values, email: 'novo-contato@example.invalid' });
});

test('409 renews the key only on the next explicit attempt and does not clear the fields', async t => {
  let attempt = 0;
  const h = await makeLanding({ respond: () => ++attempt === 1 ? response(409, {}) : response() }); t.after(() => h.close());
  const values = completeForm(h); h.submit(); await flush(); assertPreserved(h, values);
  assert.match(h.element('#lead-error').textContent, /renovar a identificação/);
  assert.equal(h.calls.length, 1); h.submit(); await flush();
  assert.equal(h.calls.length, 2);
  assert.notEqual(h.calls[1].options.headers['Idempotency-Key'], h.calls[0].options.headers['Idempotency-Key']);
  assert.equal(h.calls[1].options.body, h.calls[0].options.body);
  assert.equal(h.element('#lead-success').hidden, false);
});

test('validation and rate errors are actionable and preserve the request', async t => {
  for (const [status, expected] of [[422, /Confira seu nome, e-mail, WhatsApp e segmento/], [429, /Aguarde alguns minutos/]]) {
    await t.test(String(status), async t => {
      const h = await makeLanding({ respond: () => response(status, {}) }); t.after(() => h.close());
      const values = completeForm(h); h.submit(); await flush(); assertPreserved(h, values);
      assert.match(h.element('#lead-error').textContent, expected);
      assert.equal(h.calls.length, 1);
    });
  }
});

test('a timeout aborts the request and a retry keeps the key for an uncertain outcome', async t => {
  let attempt = 0;
  const h = await makeLanding({ respond: (path, options, window) => {
    if (++attempt > 1) return response();
    return new Promise((resolve, reject) => options.signal.addEventListener('abort', () => reject(new window.DOMException('Timed out', 'AbortError')), { once: true }));
  } }); t.after(() => h.close());
  const values = completeForm(h); h.submit(); await flush();
  await h.tick(16000); assertPreserved(h, values);
  assert.equal(h.calls[0].options.signal.aborted, true);
  assert.match(h.element('#lead-error').textContent, /conexão não confirmou/);
  h.submit(); await flush();
  assert.equal(h.calls[1].options.headers['Idempotency-Key'], h.calls[0].options.headers['Idempotency-Key']);
  assert.equal(h.element('#lead-success').hidden, false);
});

test('a network failure preserves the input and offers the same identifiable retry', async t => {
  const h = await makeLanding({ respond: (path, options, window) => { throw new window.TypeError('Network failed'); } }); t.after(() => h.close());
  const values = completeForm(h); h.submit(); await flush(); assertPreserved(h, values);
  assert.match(h.element('#lead-error').textContent, /Verifique sua conexão/);
  h.submit(); await flush();
  assert.equal(h.calls[1].options.headers['Idempotency-Key'], h.calls[0].options.headers['Idempotency-Key']);
  assertPreserved(h, values);
});

test('201 only clears personal input after a valid acknowledgement with a canonical request UUID', async t => {
  const invalid = [
    ['missing receipt', { ok: true }],
    ['negative acknowledgement', { ok: false, request_id: receiptId }],
    ['malformed UUID', { ok: true, request_id: '------------------------------------' }],
    ['invalid JSON', null],
  ];
  for (const [name, body] of invalid) {
    await t.test(name, async t => {
      let attempt = 0;
      const h = await makeLanding({ respond: () => {
        if (++attempt > 1) return response();
        return body === null ? { status: 201, json: async () => { throw new SyntaxError('Invalid JSON'); } } : response(201, body);
      } }); t.after(() => h.close());
      const values = completeForm(h); h.submit(); await flush(); assertPreserved(h, values);
      assert.match(h.element('#lead-error').textContent, /Não conseguimos confirmar o registro/);
      h.submit(); await flush();
      assert.equal(h.calls[1].options.headers['Idempotency-Key'], h.calls[0].options.headers['Idempotency-Key']);
      assert.equal(h.calls[1].options.body, h.calls[0].options.body);
      assert.equal(h.element('#lead-success').hidden, false);
    });
  }
});
