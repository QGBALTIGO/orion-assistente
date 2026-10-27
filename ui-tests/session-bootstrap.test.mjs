import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { JSDOM, VirtualConsole } from 'jsdom';
import { deferred, flush } from './harness.mjs';
import { jsonResponse, makePanel } from './panel-harness.mjs';

// The shipped document and controllers run against synthetic HTTP responses.
// No account, cookie, customer data or external service is used by these tests.
const session = {
  authenticated: true, csrf: 'synthetic-session-restore', role: 'support',
  name: 'Conta sintética', email: 'bootstrap@example.invalid',
};
const visible = node => !node.hidden && !node.classList.contains('hidden');
const sessionReads = h => h.calls.filter(call => call.path === '/auth/me');
const protectedReads = h => h.calls.filter(call => call.path.startsWith('/api/'));

function expectScreens(h, { boot, login, app }) {
  assert.equal(visible(h.element('#sessionBoot')), boot, 'startup screen');
  assert.equal(visible(h.element('#loginView')), login, 'login screen');
  assert.equal(visible(h.element('#appView')), app, 'authenticated application');
}

async function createPanel(t, options = {}) {
  const h = await makePanel(options);
  t.after(async () => {
    await flush();
    h.close();
    assert.deepEqual(h.errors, [], 'no script error, external request or unscripted write');
  });
  return h;
}

test('the initial HTML hides login and private pages before any script or session response', async t => {
  const html = await readFile(new URL('../app/static/panel.html', import.meta.url), 'utf8');
  const dom = new JSDOM(html, { runScripts: 'outside-only', virtualConsole: new VirtualConsole() });
  t.after(() => dom.window.close());
  const { document } = dom.window;
  // jsdom does not implement the browser cascade for !important versus a
  // later ID selector. Verify the shipped visibility state and its critical
  // stylesheet; real first-paint layout is checked in browser QA.
  for (const id of ['loginView', 'appView']) {
    assert.equal(visible(document.getElementById(id)), false, id);
  }
  const hiddenRule = [...document.styleSheets].flatMap(sheet => [...sheet.cssRules])
    .find(rule => rule.selectorText === '.hidden');
  assert.equal(hiddenRule?.style.getPropertyValue('display'), 'none');
  assert.equal(hiddenRule?.style.getPropertyPriority('display'), 'important');
  const boot = document.getElementById('sessionBoot');
  assert.ok(boot, 'the user receives a neutral loading state while session status is unknown');
  assert.equal(visible(boot), true);
  assert.match(document.getElementById('sessionBootMessage').textContent, /abrindo|carregando/i);
  assert.equal(visible(document.getElementById('sessionBootRetry')), false);
});

test('a delayed valid session opens the requested Clientes page without revealing login', async t => {
  const pending = deferred();
  const h = await createPanel(t, { initialRoute: 'clientes', respond: path => {
    if (path === '/auth/me') return pending.promise;
  } });
  expectScreens(h, { boot: true, login: false, app: false });
  assert.deepEqual(protectedReads(h), [], 'private data is not requested before authentication');
  assert.equal(sessionReads(h).length, 1);
  const request = sessionReads(h)[0];
  assert.equal(request.options.credentials, 'same-origin');
  assert.equal(request.options.cache, 'no-store');
  assert.equal(request.method, 'GET');
  let loginRevealed = false;
  const login = h.element('#loginView');
  const observer = new h.window.MutationObserver(records => {
    for (const record of records) {
      if (record.attributeName === 'class' && !(record.oldValue || '').split(/\s+/).includes('hidden')) {
        loginRevealed = true;
      }
    }
    loginRevealed ||= visible(login);
  });
  observer.observe(login, { attributes: true, attributeFilter: ['class'], attributeOldValue: true });
  t.after(() => observer.disconnect());
  await flush();
  expectScreens(h, { boot: true, login: false, app: false });
  pending.resolve(session);
  await flush();
  expectScreens(h, { boot: false, login: false, app: true });
  assert.equal(h.element('#page-suite').dataset.osCurrentRoute, 'clientes');
  assert.equal(new URL(h.window.location.href).searchParams.get('aba'), 'clientes');
  assert.equal(loginRevealed, false);
  assert.equal(h.calls.some(call => call.method !== 'GET'), false);
});

test('an immediately restored member session finishes without waiting for a decorative delay', async t => {
  const h = await createPanel(t, { role: 'member', initialRoute: 'dashboard' });
  expectScreens(h, { boot: false, login: false, app: true });
  assert.equal(h.element('#memberProfileName').textContent, 'Conta sintética');
  assert.equal(h.calls.some(call => call.path.startsWith('/api/v1/qr/')), false);
  assert.equal(sessionReads(h).length, 1);
});

test('login appears only after the session endpoint confirms that the session is absent', async t => {
  const pending = deferred();
  const h = await createPanel(t, { respond: path => {
    if (path === '/auth/me') return pending.promise;
  } });
  expectScreens(h, { boot: true, login: false, app: false });
  pending.resolve(jsonResponse({ detail: 'Faça login no painel' }, 401));
  await flush();
  expectScreens(h, { boot: false, login: true, app: false });
  assert.equal(visible(h.element('#loginForm')), true);
  assert.equal(h.element('#authSubmitLogin').disabled, false);
  assert.deepEqual(protectedReads(h), []);
});

test('a temporary server failure offers a single retry and preserves the requested page', async t => {
  const retried = deferred();
  let checks = 0;
  const h = await createPanel(t, { initialRoute: 'financeiro', respond: path => {
    if (path === '/auth/me') return ++checks === 1
      ? jsonResponse({ detail: 'Serviço indisponível' }, 503) : retried.promise;
  } });
  expectScreens(h, { boot: true, login: false, app: false });
  assert.equal(visible(h.element('#sessionBootRetry')), true);
  assert.match(h.element('#sessionBootMessage').textContent, /conexão|conectar|conferir|carregar|verificar/i);
  assert.deepEqual(protectedReads(h), []);
  h.click('#sessionBootRetry');
  h.click('#sessionBootRetry');
  await flush();
  assert.equal(checks, 2, 'repeated taps cannot start duplicate session checks');
  expectScreens(h, { boot: true, login: false, app: false });
  retried.resolve(session);
  await flush();
  expectScreens(h, { boot: false, login: false, app: true });
  assert.equal(h.element('#page-suite').dataset.osCurrentRoute, 'financeiro');
  assert.equal(new URL(h.window.location.href).searchParams.get('aba'), 'financeiro');
});

test('a network interruption remains recoverable without presenting a sign-in form', async t => {
  let checks = 0;
  const h = await createPanel(t, { respond: path => {
    if (path === '/auth/me' && ++checks === 1) throw new TypeError('Synthetic network failure');
  } });
  expectScreens(h, { boot: true, login: false, app: false });
  assert.equal(visible(h.element('#sessionBootRetry')), true);
  assert.equal(h.element('#sessionBootRetry').disabled, false);
  assert.deepEqual(protectedReads(h), []);
  h.click('#sessionBootRetry');
  await flush();
  expectScreens(h, { boot: false, login: false, app: true });
  assert.equal(checks, 2);
});

for (const [label, response] of [
  ['missing authentication', { csrf: 'synthetic-csrf', role: 'support' }],
  ['false authentication', { ...session, authenticated: false }],
  ['missing CSRF', { authenticated: true, role: 'support' }],
  ['empty CSRF', { ...session, csrf: '' }],
  ['unsupported role', { ...session, role: 'unknown' }],
]) {
  test('an incomplete session response stays private and recoverable: ' + label, async t => {
    const h = await createPanel(t, { respond: path => path === '/auth/me' ? response : undefined });
    expectScreens(h, { boot: true, login: false, app: false });
    assert.equal(visible(h.element('#sessionBootRetry')), true);
    assert.deepEqual(protectedReads(h), []);
  });
}

test('a successful HTTP response with invalid JSON does not masquerade as a valid session or a logout', async t => {
  const h = await createPanel(t, { respond: path => path === '/auth/me'
    ? new Response('temporarily unavailable', { status: 200 }) : undefined });
  expectScreens(h, { boot: true, login: false, app: false });
  assert.equal(visible(h.element('#sessionBootRetry')), true);
  assert.deepEqual(protectedReads(h), []);
});

for (const phase of ['headers', 'body']) {
  test('a stalled session ' + phase + ' request times out, cancels transport and can be retried', async t => {
    t.mock.timers.enable({ apis: ['setTimeout'] });
    let checks = 0, signal;
    const h = await createPanel(t, { respond: (path, method, data, options) => {
      if (path !== '/auth/me' || ++checks > 1) return;
      signal = options.signal;
      const pending = new Promise((resolve, reject) => {
        signal.addEventListener('abort', () => reject(new DOMException('Synthetic aborted read', 'AbortError')), { once: true });
      });
      if (phase === 'headers') return pending;
      const response = jsonResponse(session);
      response.json = () => pending;
      return response;
    } });
    expectScreens(h, { boot: true, login: false, app: false });
    assert.equal(signal.aborted, false);
    t.mock.timers.tick(15_000);
    await flush();
    assert.equal(signal.aborted, true, 'the stalled read is cancelled rather than left in flight');
    expectScreens(h, { boot: true, login: false, app: false });
    assert.equal(visible(h.element('#sessionBootRetry')), true);
    h.click('#sessionBootRetry');
    await flush();
    expectScreens(h, { boot: false, login: false, app: true });
    assert.equal(checks, 2);
  });
}

test('explicit logout still clears the restored app and shows login without reentering startup', async t => {
  const h = await createPanel(t, { initialRoute: 'clientes' });
  expectScreens(h, { boot: false, login: false, app: true });
  h.click('#logout');
  await flush();
  expectScreens(h, { boot: false, login: true, app: false });
  assert.equal(h.calls.filter(call => call.path === '/auth/logout' && call.method === 'POST').length, 1);
  assert.equal(sessionReads(h).length, 1);
});

for (const authenticated of [true, false]) {
  test('returning from browser history does not restart a settled ' + (authenticated ? 'app' : 'login') + ' screen', async t => {
    const h = await createPanel(t, { respond: path => !authenticated && path === '/auth/me'
      ? jsonResponse({ detail: 'Sessão ausente' }, 401) : undefined });
    expectScreens(h, { boot: false, login: !authenticated, app: authenticated });
    h.window.dispatchEvent(new h.window.PageTransitionEvent('pageshow', { persisted: true }));
    await flush();
    assert.equal(sessionReads(h).length, 1, 'a visible finished screen must not flash startup again');
    expectScreens(h, { boot: false, login: !authenticated, app: authenticated });
  });
}

test('returning to an unfinished startup screen recovers once without exposing login', async t => {
  const pending = deferred();
  let checks = 0;
  const h = await createPanel(t, { initialRoute: 'agenda', respond: path => {
    if (path === '/auth/me') return ++checks === 1
      ? jsonResponse({ detail: 'Serviço temporariamente indisponível' }, 503) : pending.promise;
  } });
  expectScreens(h, { boot: true, login: false, app: false });
  h.window.dispatchEvent(new h.window.PageTransitionEvent('pageshow', { persisted: false }));
  await flush();
  assert.equal(checks, 1, 'the ordinary initial pageshow event cannot retry a failed check');
  h.window.dispatchEvent(new h.window.PageTransitionEvent('pageshow', { persisted: true }));
  h.window.dispatchEvent(new h.window.PageTransitionEvent('pageshow', { persisted: true }));
  await flush();
  assert.equal(checks, 2, 'history restoration reuses the active check instead of duplicating it');
  expectScreens(h, { boot: true, login: false, app: false });
  pending.resolve(session);
  await flush();
  expectScreens(h, { boot: false, login: false, app: true });
  assert.equal(h.element('#page-suite').dataset.osCurrentRoute, 'agenda');
});
