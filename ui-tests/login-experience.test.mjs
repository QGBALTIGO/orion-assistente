import test from 'node:test';
import assert from 'node:assert/strict';
import { deferred, flush } from './harness.mjs';
import { jsonResponse, makePanel } from './panel-harness.mjs';

const writes = h => h.calls.filter(call => call.method !== 'GET');
const visible = node => !node.classList.contains('hidden');

// All requests run through the panel harness's synthetic, same-origin transport.
// In particular, these tests never create an account or send a recovery email.
async function makeLogin(t, { features = { registration: true, passwordRecovery: true }, respond } = {}) {
  const h = await makePanel({ respond: async (path, method, data, options) => {
    const supplied = await respond?.(path, method, data, options);
    if (supplied !== undefined) return supplied;
    if (path === '/auth/me') return jsonResponse({ detail: 'Faça login no painel' }, 401);
    if (path === '/auth/features') return features;
  } });
  t.after(async () => {
    // Opening an unavailable screen rechecks features; finish those mock reads
    // before removing the document they update.
    await flush();
    h.close();
    assert.deepEqual(h.errors, [], 'no script error, external request or unscripted mutation is allowed');
  });
  assert.equal(visible(h.element('#loginView')), true);
  assert.equal(visible(h.element('#appView')), false);
  return h;
}

function fillRegistration(h) {
  h.fill('#registerName', 'Pessoa Sintética');
  h.fill('#registerEmail', 'Pessoa@Example.invalid');
  h.fill('#registerPassword', 'Synthetic123');
  h.fill('#registerConfirm', 'Synthetic123');
}

test('support usernames and remember payload survive the redesign while pending login sends only once', async t => {
  const pending = deferred();
  const h = await makeLogin(t, { respond: (path, method) => {
    if (path === '/auth/login' && method === 'POST') return pending.promise;
  } });
  const button = h.element('#authSubmitLogin'), form = h.element('#loginForm');
  const originalLabel = button.textContent.trim();
  h.fill('#username', 'admin');
  h.fill('#password', 'synthetic-support-password');
  h.click('#rememberMe');
  assert.equal(h.element('#username').type, 'text');
  assert.equal(h.element('#username').checkValidity(), true, 'support access must not require an email');
  h.submit('#loginForm'); await flush();
  assert.equal(button.disabled, true);
  assert.equal(button.getAttribute('aria-busy'), 'true');
  assert.equal(form.getAttribute('aria-busy'), 'true');
  assert.match(button.textContent, /entrando|acessando/i);
  h.submit('#loginForm'); await flush();
  assert.equal(writes(h).length, 1, 'a second submit cannot start a second transport attempt');
  assert.equal(writes(h)[0].path, '/auth/login');
  assert.deepEqual(writes(h)[0].data, {
    username: 'admin', password: 'synthetic-support-password', remember: true,
  });
  assert.equal(writes(h)[0].options.credentials, 'same-origin');
  pending.resolve({ ok: true, csrf: 'synthetic-login-csrf', role: 'support', name: 'Administrador' });
  await flush();
  assert.equal(visible(h.element('#loginView')), false);
  assert.equal(visible(h.element('#appView')), true);
  assert.equal(h.element('#password').value, '');
  assert.equal(button.disabled, false);
  assert.notEqual(button.getAttribute('aria-busy'), 'true');
  assert.notEqual(form.getAttribute('aria-busy'), 'true');
  assert.equal(button.textContent.trim(), originalLabel);
});

test('a rejected login restores the form and preserves a useful error for another attempt', async t => {
  const message = 'E-mail ou senha incorretos.';
  const h = await makeLogin(t, { respond: (path, method) => {
    if (path === '/auth/login' && method === 'POST') return jsonResponse({ detail: message }, 401);
  } });
  const button = h.element('#authSubmitLogin'), originalLabel = button.textContent.trim();
  h.fill('#username', 'synthetic@example.invalid');
  h.fill('#password', 'synthetic-password');
  h.submit('#loginForm'); await flush();
  assert.equal(visible(h.element('#loginView')), true);
  assert.equal(h.element('#loginError').textContent, message);
  assert.equal(button.disabled, false);
  assert.notEqual(button.getAttribute('aria-busy'), 'true');
  assert.notEqual(h.element('#loginForm').getAttribute('aria-busy'), 'true');
  assert.equal(button.textContent.trim(), originalLabel);
  assert.equal(h.element('#username').value, 'synthetic@example.invalid');
  assert.equal(writes(h).length, 1);
});

test('a delayed availability response cannot unlock controls during an active login', async t => {
  const availability = deferred(), login = deferred();
  const h = await makeLogin(t, { respond: (path, method) => {
    if (path === '/auth/features') return availability.promise;
    if (path === '/auth/login' && method === 'POST') return login.promise;
  } });
  assert.equal(h.element('#authSubmitRegister').disabled, true);
  assert.equal(h.element('#authSubmitForgot').disabled, true);
  h.fill('#username', 'synthetic@example.invalid');
  h.fill('#password', 'synthetic-password');
  h.submit('#loginForm'); await flush();
  availability.resolve({ registration: true, passwordRecovery: true }); await flush();
  for (const id of ['authSubmitLogin', 'authSubmitRegister', 'authSubmitForgot', 'authTabRegister']) {
    assert.equal(h.element('#' + id).disabled, true, id + ' must remain locked during login');
  }
  assert.equal(h.element('#loginForm').getAttribute('aria-busy'), 'true');
  h.submit('#loginForm'); h.submit('#registerForm'); await flush();
  assert.equal(writes(h).length, 1);
  login.resolve(jsonResponse({ detail: 'Não foi possível entrar. Tente novamente.' }, 503));
  await flush();
  assert.equal(h.element('#authSubmitLogin').disabled, false);
  assert.equal(h.element('#authSubmitRegister').disabled, false);
  assert.equal(h.element('#authSubmitForgot').disabled, false);
  assert.match(h.element('#loginError').textContent, /tente novamente/i);
});

test('registration errors remain visible after the availability refresh', async t => {
  const message = 'Já existe uma conta com este e-mail. Entre ou recupere a senha.';
  const h = await makeLogin(t, { respond: (path, method) => {
    if (path === '/auth/register' && method === 'POST') return jsonResponse({ detail: message }, 409);
  } });
  h.click('#authTabRegister'); fillRegistration(h);
  h.submit('#registerForm'); await flush();
  assert.equal(visible(h.element('#registerForm')), true);
  assert.equal(h.element('#registerError').textContent, message);
  assert.equal(h.element('#authSubmitRegister').disabled, false);
  assert.notEqual(h.element('#registerForm').getAttribute('aria-busy'), 'true');
  assert.equal(h.element('#registerEmail').value, 'Pessoa@Example.invalid');
  assert.equal(h.element('#authRegisterAvailability').textContent.trim(), '');
  assert.equal(writes(h).length, 1);
  assert.equal(h.calls.filter(call => call.path === '/auth/features').length >= 2, true);
});

test('disabled registration and recovery keep their explanation when moving between screens', async t => {
  const h = await makeLogin(t, { features: { registration: false, passwordRecovery: false } });
  const loginTitle = h.element('#authTitle').textContent;
  h.click('#authTabRegister');
  const registrationNotice = h.element('#authRegisterAvailability').textContent;
  assert.match(registrationNotice, /indispon|habilitad/i);
  assert.equal(visible(h.element('#authRegisterAvailability')), true);
  assert.equal(h.element('#authSubmitRegister').disabled, true);
  assert.notEqual(h.element('#authTitle').textContent, loginTitle);
  h.click('#authTabLogin'); h.click('#authTabRegister');
  assert.equal(h.element('#authRegisterAvailability').textContent, registrationNotice);
  assert.equal(h.element('#authSubmitRegister').disabled, true);
  h.click('#authTabLogin'); h.click('#authForgotLink');
  const recoveryNotice = h.element('#authForgotAvailability').textContent;
  assert.match(recoveryNotice, /indispon|habilitad/i);
  assert.equal(visible(h.element('#authForgotAvailability')), true);
  assert.equal(h.element('#authSubmitForgot').disabled, true);
  assert.equal(h.document.activeElement, h.element('#authTitle'), 'the new screen heading announces the recovery context');
  h.click('#forgotForm [data-back-login]');
  assert.equal(h.element('#authTitle').textContent, loginTitle);
  assert.equal(h.document.activeElement, h.element('#authTitle'));
  h.click('#authForgotLink');
  assert.equal(h.element('#authForgotAvailability').textContent, recoveryNotice);
  assert.equal(h.element('#authSubmitForgot').disabled, true);
  assert.deepEqual(writes(h), []);
});

test('a newly created account returns to login and enters only its member area without an OTP step', async t => {
  const h = await makeLogin(t, { respond: (path, method) => {
    if (path === '/auth/register' && method === 'POST') return { ok: true, message: 'Conta criada. Faça login.' };
    if (path === '/auth/login' && method === 'POST') return {
      ok: true, csrf: 'synthetic-member-csrf', role: 'member', name: 'Pessoa Sintética',
    };
  } });
  h.click('#authTabRegister'); fillRegistration(h);
  h.fill('#registerConfirm', 'Mismatch123');
  h.submit('#registerForm'); await flush();
  assert.match(h.element('#registerError').textContent, /senhas não coincidem/i);
  assert.deepEqual(writes(h), [], 'mismatching passwords never reach registration');
  h.fill('#registerConfirm', 'Synthetic123');
  h.submit('#registerForm'); await flush();
  assert.equal(visible(h.element('#loginForm')), true);
  assert.equal(h.element('#username').value, 'pessoa@example.invalid');
  assert.equal(h.document.activeElement, h.element('#password'));
  assert.equal(h.element('#registerPassword').value, '');
  assert.equal(h.element('#registerConfirm').value, '');
  assert.match(h.element('#authGlobalMessage').textContent, /conta criada/i);
  h.fill('#password', 'Synthetic123');
  h.submit('#loginForm'); await flush();
  assert.equal(visible(h.element('#appView')), true);
  assert.equal(h.element('#memberProfileName').textContent, 'Pessoa Sintética');
  assert.equal(h.element('#memberProfileEmail').textContent, 'pessoa@example.invalid');
  assert.deepEqual(writes(h).map(call => call.path), ['/auth/register', '/auth/login']);
  assert.equal(h.calls.some(call => call.path.includes('/verify')), false);
  assert.equal(h.calls.some(call => call.path.startsWith('/api/v1/qr/')), false);
});

test('recovery preserves the generic response, six-digit code and password confirmation flow', async t => {
  const pending = deferred();
  const message = 'Se esse e-mail estiver cadastrado, enviaremos um código.';
  const h = await makeLogin(t, { respond: (path, method) => {
    if (path === '/auth/password/forgot' && method === 'POST') return pending.promise;
    if (path === '/auth/password/reset' && method === 'POST') return { ok: true, message: 'Senha atualizada. Entre novamente.' };
  } });
  h.click('#authForgotLink');
  h.fill('#forgotEmail', 'Pessoa@Example.invalid');
  h.submit('#forgotForm'); await flush();
  assert.equal(h.element('#authSubmitForgot').disabled, true);
  assert.equal(h.element('#forgotForm').getAttribute('aria-busy'), 'true');
  h.submit('#forgotForm'); await flush();
  assert.equal(writes(h).length, 1);
  pending.resolve({ ok: true, message }); await flush();
  assert.equal(visible(h.element('#resetForm')), true);
  assert.equal(h.element('#resetEmail').value, 'pessoa@example.invalid');
  assert.equal(h.document.activeElement, h.element('#resetCode'));
  assert.equal(h.element('#authGlobalMessage').textContent, message);
  h.fill('#resetCode', '001289');
  h.fill('#resetPassword', 'NewSynthetic123');
  h.fill('#resetConfirm', 'Mismatch123');
  h.submit('#resetForm'); await flush();
  assert.match(h.element('#resetError').textContent, /senhas não coincidem/i);
  assert.equal(writes(h).length, 1);
  h.fill('#resetConfirm', 'NewSynthetic123');
  h.submit('#resetForm'); await flush();
  assert.deepEqual(writes(h)[1].data, {
    email: 'pessoa@example.invalid', code: '001289', password: 'NewSynthetic123',
  });
  assert.deepEqual(writes(h).map(call => call.path), ['/auth/password/forgot', '/auth/password/reset']);
  assert.equal(visible(h.element('#loginForm')), true);
  assert.equal(h.element('#resetCode').value, '');
  assert.equal(h.element('#resetPassword').value, '');
  assert.equal(h.element('#resetConfirm').value, '');
  assert.match(h.element('#authGlobalMessage').textContent, /senha atualizada/i);
});

test('login and registration tabs expose their panels and support keyboard navigation', async t => {
  const h = await makeLogin(t);
  const login = h.element('#authTabLogin'), register = h.element('#authTabRegister');
  assert.equal(login.getAttribute('aria-controls'), 'loginForm');
  assert.equal(register.getAttribute('aria-controls'), 'registerForm');
  login.focus();
  function press(key, selected, inactive, selectedForm) {
    const event = new h.window.KeyboardEvent('keydown', { key, bubbles: true, cancelable: true });
    h.document.activeElement.dispatchEvent(event);
    assert.equal(event.defaultPrevented, true);
    assert.equal(h.document.activeElement, selected);
    assert.equal(selected.getAttribute('aria-selected'), 'true');
    assert.equal(inactive.getAttribute('aria-selected'), 'false');
    assert.equal(selected.tabIndex, 0);
    assert.equal(inactive.tabIndex, -1);
    assert.equal(visible(h.element(selectedForm)), true);
  }
  press('ArrowRight', register, login, '#registerForm');
  press('ArrowRight', login, register, '#loginForm');
  press('ArrowLeft', register, login, '#registerForm');
  press('Home', login, register, '#loginForm');
  press('End', register, login, '#registerForm');
  assert.deepEqual(writes(h), []);
});

test('login theme choices share the saved preference, selected state and logo with administration', async t => {
  const h = await makeLogin(t);
  const root = h.document.documentElement;
  const choices = [...h.document.querySelectorAll('[data-theme-choice]')];
  assert.equal(choices.length, 6);
  function chooseTheme(value) {
    h.click('#authThemeToggle');
    assert.equal(h.element('#authThemeToggle').getAttribute('aria-expanded'), 'true');
    assert.equal(visible(h.element('#authThemeMenu')), true);
    h.click('#authThemeMenu [data-theme-choice="' + value + '"]');
    assert.equal(visible(h.element('#authThemeMenu')), false);
    assert.equal(h.element('#authThemeToggle').getAttribute('aria-expanded'), 'false');
    assert.equal(h.document.activeElement, h.element('#authThemeToggle'));
  }
  chooseTheme('dark');
  assert.equal(root.dataset.theme, 'dark');
  assert.equal(root.dataset.themePreference, 'dark');
  assert.equal(h.window.localStorage.getItem('orion-theme'), 'dark');
  for (const choice of choices) {
    assert.equal(choice.getAttribute('aria-pressed'), String(choice.dataset.themeChoice === 'dark'));
  }
  for (const logo of h.document.querySelectorAll('[data-orion-logo]')) {
    assert.equal(logo.getAttribute('src'), '/brand/orion-brand-dark.svg');
  }
  // Re-run the actual head bootstrap as a reload would: saved manual choice wins.
  const bootstrap = [...h.document.scripts].find(script => script.textContent.includes('localStorage.getItem("orion-theme")'));
  assert.ok(bootstrap);
  root.dataset.theme = 'light'; root.dataset.themePreference = 'auto';
  h.window.eval(bootstrap.textContent);
  assert.equal(root.dataset.theme, 'dark');
  assert.equal(root.dataset.themePreference, 'dark');
  chooseTheme('light');
  assert.equal(root.dataset.theme, 'light');
  assert.equal(h.window.localStorage.getItem('orion-theme'), 'light');
  chooseTheme('auto');
  assert.equal(root.dataset.themePreference, 'auto');
  assert.equal(root.dataset.theme, 'light', 'the harness uses a light system preference');
  assert.equal(h.window.localStorage.getItem('orion-theme'), 'auto');
  h.click('#authThemeToggle');
  const escape = new h.window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
  h.document.activeElement.dispatchEvent(escape);
  assert.equal(escape.defaultPrevented, true);
  assert.equal(visible(h.element('#authThemeMenu')), false);
  assert.equal(h.document.activeElement, h.element('#authThemeToggle'));
  assert.deepEqual(writes(h), []);
});

test('login theme menu survives focus loss without a destination before a touch selection', async t => {
  const h = await makeLogin(t);
  const menu = h.element('#authThemeMenu'), toggle = h.element('#authThemeToggle');
  const root = h.document.documentElement;
  // Starting at auto, this sequence covers every transition between distinct
  // preferences in both directions.
  for (const preference of ['dark', 'light', 'auto', 'light', 'dark', 'auto']) {
    h.click('#authThemeToggle');
    const focused = menu.querySelector('[aria-pressed="true"]');
    assert.equal(h.document.activeElement, focused, 'opening the menu focuses its selected option');
    assert.equal(visible(menu), true);

    // A touch can blur the programmatically focused option without focusing
    // the tapped button. The target must stay visible until its click arrives.
    let focusout;
    focused.addEventListener('focusout', event => { focusout = event; }, { once: true });
    focused.blur();
    assert.ok(focusout, 'the selected option actually loses focus before the click');
    assert.equal(focusout.relatedTarget, null, 'this reproduces loss of focus without a destination');
    assert.equal(visible(menu), true, 'blur must preserve the pending touch target for ' + preference);
    assert.equal(toggle.getAttribute('aria-expanded'), 'true');

    h.click('#authThemeMenu [data-theme-choice="' + preference + '"]');
    assert.equal(root.dataset.themePreference, preference);
    assert.equal(root.dataset.theme, preference === 'auto' ? 'light' : preference);
    assert.equal(h.window.localStorage.getItem('orion-theme'), preference);
    assert.equal(menu.querySelector('[aria-pressed="true"]').dataset.themeChoice, preference);
    assert.equal(visible(menu), false, 'a completed selection closes the menu');
    assert.equal(toggle.getAttribute('aria-expanded'), 'false');
  }
  assert.deepEqual(writes(h), []);
});

test('login theme menu closes when keyboard focus moves outside without stealing that focus', async t => {
  const h = await makeLogin(t);
  const menu = h.element('#authThemeMenu'), toggle = h.element('#authThemeToggle');
  h.click('#authThemeToggle');
  const option = h.element('#authThemeMenu [data-theme-choice="light"]');
  option.focus();
  assert.equal(h.document.activeElement, option);
  assert.equal(visible(menu), true, 'moving focus within the menu keeps it open');

  const username = h.element('#username');
  username.focus();
  assert.equal(visible(menu), false);
  assert.equal(toggle.getAttribute('aria-expanded'), 'false');
  assert.equal(h.document.activeElement, username, 'the destination field keeps keyboard focus');
  assert.deepEqual(writes(h), []);
});

test('showing passwords announces the state and returns focus without changing their value', async t => {
  const h = await makeLogin(t);
  for (const id of ['password', 'registerPassword']) {
    if (id === 'registerPassword') h.click('#authTabRegister');
    const field = h.fill('#' + id, 'Synthetic123');
    const selector = '[data-password-toggle="' + id + '"]';
    const toggle = h.element(selector);
    assert.equal(toggle.getAttribute('aria-pressed'), 'false');
    h.click(selector);
    assert.equal(field.type, 'text');
    assert.equal(field.value, 'Synthetic123');
    assert.equal(toggle.getAttribute('aria-pressed'), 'true');
    assert.equal(toggle.getAttribute('aria-label'), 'Ocultar senha');
    assert.equal(h.document.activeElement, field);
    h.click(selector);
    assert.equal(field.type, 'password');
    assert.equal(field.value, 'Synthetic123');
    assert.equal(toggle.getAttribute('aria-pressed'), 'false');
    assert.equal(toggle.getAttribute('aria-label'), 'Mostrar senha');
    assert.equal(h.document.activeElement, field);
  }
  assert.deepEqual(writes(h), []);
});
