import test from 'node:test';
import assert from 'node:assert/strict';
import { flush, deferred } from './harness.mjs';
import { makePanel as createPanel } from './panel-harness.mjs';

// The operator screens are admin-only; explicit member cases override this role.
const makePanel=(options={})=>createPanel({role:'admin',...options});

const phoneA = '5511999990001', phoneB = '5511999990002';
const messagePath = phone => '/api/v1/qr/messages?phone=' + phone;
const writes = h => h.calls.filter(call => call.method !== 'GET');
const activePage = h => h.document.querySelector('#appView .content > .page:not(.hidden)')?.id;
const currentRoute = h => new URL(h.window.location.href).searchParams.get('aba');

test('the single management menu prioritizes WhatsApp and preserves existing atendimento links', async t => {
  const h = await makePanel({ initialRoute: 'ai' }); t.after(() => h.close());
  const links = [...h.document.querySelectorAll('#osNav [data-os-route]')];
  const routes = links.map(link => link.dataset.osRoute);
  assert.equal(routes[0], 'dispositivo', 'the connection entry must precede the management modules');
  assert.match(links[0].textContent, /Conectar WhatsApp/);
  assert.equal(new Set(routes).size, routes.length, 'each destination appears once in the menu');
  assert.equal(routes.includes('cadastros'), false, 'the duplicate record area is not a sidebar destination');
  assert.equal(currentRoute(h), 'ai', 'a saved simulator URL remains usable');
  assert.equal(activePage(h), 'page-ai');

  const businessForm = h.element('#businessForm'), simulatorForm = h.element('#aiForm');
  for (const route of ['empresa', 'ai', 'dispositivo']) {
    h.click('#osNav [data-os-route=' + route + ']'); await flush();
    assert.equal(currentRoute(h), route);
    assert.equal(activePage(h), 'page-' + route);
    assert.equal(h.element('#osNav [aria-current=page]').dataset.osRoute, route);
  }
  assert.equal(h.element('#businessForm'), businessForm, 'navigation does not replace the business controller DOM');
  assert.equal(h.element('#aiForm'), simulatorForm, 'navigation does not replace the simulator controller DOM');

  h.click('#osNav [data-os-route=configuracoes]'); await flush();
  const settingsSection = h.element('#osSettingsSection');
  assert.equal([...settingsSection.options].some(option=>option.value==='administration'), false,
    'the retired Administration settings section must not reappear');
  settingsSection.value = 'administration';
  settingsSection.dispatchEvent(new h.window.Event('change', { bubbles: true })); await flush();
  assert.equal(h.element('#osSettingsSection').value, 'general', 'stale preferences fall back to the supported business settings');
  assert.equal(h.document.querySelector('#page-suite [data-os-route=cadastros]'), null);
  await h.window.OrionSuite.open('cadastros'); await flush();
  assert.equal(currentRoute(h), 'configuracoes', 'saved legacy record URLs remain safe entry points');
  assert.equal(activePage(h), 'page-suite');
  assert.equal(h.element('#osNav [aria-current=page]').dataset.osRoute, 'configuracoes');
  for (const route of ['conversas', 'ai', 'empresa', 'catalogo'])
    assert.ok(h.document.querySelector('#osNav [data-os-route=' + route + ']'), route + ' remains available to the administrator');
  assert.deepEqual(writes(h), [], 'navigation must not save configuration, generate AI or send WhatsApp messages');
  assert.equal(h.errors.length, 0);
});

test('member deep links and direct navigation never load the global WhatsApp operation', async t => {
  const h = await makePanel({ role: 'member', tenantRole: 'owner', initialRoute: 'conversas' });
  t.after(() => h.close());
  assert.equal(h.document.querySelector('#osNav [data-os-route=dispositivo]'), null);
  assert.equal(h.document.querySelector('#osNav [data-os-route=conversas]'), null);
  assert.notEqual(activePage(h), 'page-conversas');
  for (const route of ['dispositivo', 'conversas']) {
    await h.window.OrionSuite.open(route); await flush();
    assert.notEqual(activePage(h), 'page-' + route);
    h.click('[data-page=' + route + ']'); await flush();
    assert.equal(activePage(h), route==='conversas'?'page-suite':'page-member',
      'the legacy controller independently redirects forbidden routes');
  }
  assert.equal(h.calls.some(call => call.path.startsWith('/api/v1/qr/')), false);
  assert.equal(h.calls.some(call => call.path === '/api/v1/tenants'), false, 'members only load assigned businesses');
  assert.deepEqual(writes(h), []);
  assert.equal(h.errors.length, 0);
});

test('the top connection entry keeps the pairing wizard, authenticated request and code expiry', async t => {
  const h = await makePanel({ deviceStatus: 'qr_ready', respond: (path, method) => {
    if (path === '/api/v1/qr/pair' && method === 'POST') return { status: 'pairing', code: 'ABCD-1234', expiresIn: 1 };
  } }); t.after(() => h.close());
  h.click('#osNav [data-os-route=dispositivo]'); await flush();
  h.click('[data-method=code]');
  assert.equal(h.element('#wizardCodeInput').classList.contains('hidden'), false);
  assert.equal(h.element('#wizardTutorial').classList.contains('hidden'), true);
  assert.deepEqual(writes(h), [], 'opening the wizard alone does not request a pairing attempt');
  h.fill('#pairPhone', '(11) 99999-0001'); h.submit('#pairForm'); await flush();
  const mutations = writes(h); assert.equal(mutations.length, 1);
  assert.equal(mutations[0].path, '/api/v1/qr/pair');
  assert.deepEqual(mutations[0].data, { phone: phoneA });
  assert.equal(mutations[0].options.credentials, 'same-origin');
  assert.equal(mutations[0].options.headers['X-Orion-CSRF'], 'synthetic-mobile-csrf');
  assert.equal(h.element('#pairCode').textContent, 'ABCD-1234');
  assert.match(h.element('#codeStatus').textContent, /Aguardando a confirmação no WhatsApp/);
  await h.tickClocks(1100);
  assert.match(h.element('#codeStatus').textContent, /expirou/);
  assert.equal(h.element('#codeResult').classList.contains('hidden'), true);
  assert.equal(writes(h).length, 1, 'code expiration must not silently retry pairing');
  assert.equal(h.errors.length, 0);
});

const contacts = [
  { phone: phoneA, preview: 'Prévia sintética Alfa' },
  { phone: phoneB, preview: 'Prévia sintética Beta' },
];

test('connection recovery uses the Orion dialog and rechecks an intervening connection before any reset', async t => {
  let status = 'logged_out';
  const h = await makePanel({respond(path) {
    if (path === '/api/v1/qr/status') return {status, automaticRepliesEnabled:false};
  }}); t.after(() => h.close());
  h.click('#wizardRecover'); await flush();
  assert.equal(h.element('#actionConfirm').open, true);
  assert.equal(h.document.activeElement, h.element('#actionConfirmCancel'));
  h.click('#actionConfirmCancel'); await flush();
  assert.equal(h.element('#actionConfirm').open, false);
  assert.equal(h.document.activeElement, h.element('#wizardRecover'));
  assert.deepEqual(writes(h), [], 'cancelling must never recover or reconnect a session');
  h.click('#wizardRecover'); await flush();
  status = 'connected';
  h.click('#actionConfirmAccept'); await flush();
  assert.equal(h.element('#connectedCenter').classList.contains('hidden'), false);
  assert.deepEqual(writes(h), [], 'a connection completed in the meantime must be preserved');
  assert.deepEqual(h.errors, []);
});

const incoming = (body, id = 'in:synthetic-mobile') => ({ id, body, direction: 'in', at: 1792202400000 });

test('mobile contact navigation preserves each draft, history and reading position without sending', async t => {
  const h = await makePanel({ initialRoute: 'conversas', deviceStatus: 'connected', respond: path => {
    if (path === '/api/v1/qr/chats') return { items: contacts };
    if (path === messagePath(phoneA)) return { items: [incoming('Histórico sintético Alfa')] };
    if (path === messagePath(phoneB)) return { items: [incoming('Histórico sintético Beta')] };
  } }); t.after(() => h.close());
  const page = h.element('#page-conversas'), composer = h.element('#sendForm'), history = h.element('#chatBubbles');
  assert.equal(page.dataset.chatView, 'list');
  h.click('#chat-contact-' + phoneA); await flush();
  assert.equal(page.dataset.chatView, 'thread');
  assert.equal(h.document.activeElement, h.element('#chatTitle'));
  assert.equal(h.element('#chatPhone').readOnly, true, 'changing a selected recipient requires opening a new conversation');
  assert.match(history.textContent, /Histórico sintético Alfa/);
  h.fill('#chatText', 'Rascunho separado Alfa');
  // jsdom has no layout engine. These viewport measurements exercise the
  // controller's reading-position decision, not the rendered CSS dimensions.
  Object.defineProperties(history, {
    scrollHeight: { value: 1500, configurable: true },
    clientHeight: { value: 400, configurable: true },
  });
  history.scrollTop = 210;
  const initialHistory = history.textContent;
  h.click('#chatBack');
  assert.equal(page.dataset.chatView, 'list');
  assert.equal(h.document.activeElement, h.element('#chatNew'));
  assert.equal(h.element('#sendForm'), composer);
  assert.equal(history.textContent, initialHistory);
  assert.equal(h.element('#chatText').value, 'Rascunho separado Alfa');
  const messageReads = h.calls.filter(call => call.path.startsWith('/api/v1/qr/messages?')).length;
  await h.tickClocks(6500);
  assert.equal(h.calls.filter(call => call.path.startsWith('/api/v1/qr/messages?')).length, messageReads,
    'polling the mobile contacts pane must not refresh or reposition its hidden thread');

  const callsBeforeSearch = h.calls.length;
  h.fill('#chatSearch', 'Alfa'); await flush();
  assert.equal(h.document.querySelectorAll('#chatList button').length, 1);
  assert.ok(h.document.querySelector('#chat-contact-' + phoneA));
  assert.equal(h.document.querySelector('#chat-contact-' + phoneB), null);
  assert.equal(h.calls.length, callsBeforeSearch, 'filtering already-loaded contacts is local');
  h.click('#chat-contact-' + phoneA); await flush();
  assert.equal(history.scrollTop, 210, 'refreshing the same thread keeps a reader above the latest messages');
  assert.equal(h.element('#chatText').value, 'Rascunho separado Alfa');

  h.click('#chatBack'); h.fill('#chatSearch', '');
  h.click('#chat-contact-' + phoneB); await flush();
  assert.equal(h.element('#chatText').value, '', 'another contact never inherits the first draft');
  assert.match(history.textContent, /Histórico sintético Beta/);
  assert.doesNotMatch(history.textContent, /Histórico sintético Alfa/);
  h.fill('#chatText', 'Rascunho separado Beta');
  h.click('#chatNew');
  assert.equal(h.element('#chatPhone').value, '');
  assert.equal(h.element('#chatPhone').readOnly, false);
  assert.equal(h.element('#chatText').value, '');
  assert.equal(h.document.activeElement, h.element('#chatPhone'));
  h.fill('#chatPhone', '5511999990003'); h.fill('#chatText', 'Novo destinatário, outro rascunho');
  h.click('#chatBack'); h.click('#chat-contact-' + phoneA); await flush();
  assert.equal(h.element('#chatText').value, 'Rascunho separado Alfa');
  assert.equal(h.element('#chatPhone').value, '(11) 99999-0001');
  h.click('#chatBack'); h.click('#chat-contact-' + phoneB); await flush();
  assert.equal(h.element('#chatText').value, 'Rascunho separado Beta');
  h.click('#chatNew');
  assert.equal(h.element('#chatPhone').value, '(11) 99999-0003');
  assert.equal(h.element('#chatText').value, 'Novo destinatário, outro rascunho');
  assert.deepEqual(writes(h), []);
  assert.equal(h.errors.length, 0);
});

test('logout clears mobile conversation state and late reads cannot contaminate the next session', async t => {
  const oldMessages = deferred(), oldContacts = deferred(), oldInspection = deferred();
  let phase = 'initial';
  const h = await makePanel({ initialRoute: 'conversas', deviceStatus: 'connected', respond: (path, method) => {
    if (path === '/api/v1/qr/chats') return phase === 'pending' ? oldContacts.promise : { items: contacts };
    if (path === messagePath(phoneA)) return phase === 'pending' ? oldMessages.promise :
      { items: [incoming(phase === 'new' ? 'Histórico da sessão atual' : 'Histórico da sessão anterior')] };
    if (path === '/api/v1/qr/recipient?phone=' + phoneA) return oldInspection.promise;
    if (path === '/auth/login' && method === 'POST') return { authenticated: true,
      csrf: 'synthetic-new-session-csrf', role: 'admin', name: 'Outra conta sintética',
      email: 'second-mobile-test@example.invalid' };
  } }); t.after(() => h.close());
  h.click('#chat-contact-' + phoneA); await flush();
  h.fill('#chatText', 'Rascunho privado da sessão anterior');
  h.click('#chatBack'); h.click('#chat-contact-' + phoneA); await flush();
  h.fill('#chatSearch', 'Alfa');
  phase = 'pending';
  h.click('#chat-contact-' + phoneA); h.click('#reloadChats');
  h.element('#chatDeliveryDetails').open = true; h.click('#inspectRecipient'); await flush();
  h.click('#logout'); await flush();

  assert.equal(h.element('#page-conversas').dataset.chatView, 'list');
  for (const id of ['chatPhone', 'chatText', 'chatSearch']) assert.equal(h.element('#' + id).value, '');
  for (const id of ['chatList', 'chatBubbles', 'recipientInspection']) assert.equal(h.element('#' + id).textContent, '');
  assert.equal(h.element('#chatDeliveryDetails').open, false);
  assert.equal(h.element('#loginView').classList.contains('hidden'), false);

  phase = 'new';
  h.fill('#username', 'second-mobile-test@example.invalid');
  h.fill('#password', 'synthetic-test-password'); h.submit('#loginForm'); await flush();
  assert.equal(h.element('#page-conversas').dataset.chatView, 'list');
  h.click('#chat-contact-' + phoneA); await flush();
  assert.equal(h.element('#chatText').value, '', 'draft storage is also cleared, not only the textarea');
  assert.match(h.element('#chatBubbles').textContent, /Histórico da sessão atual/);
  oldMessages.resolve({ items: [incoming('CONTEÚDO ANTIGO ATRASADO')] });
  oldContacts.resolve({ items: [{ phone: phoneB, preview: 'CONTATO ANTIGO ATRASADO' }] });
  oldInspection.resolve({ registered: true, identity: 'pn_confirmed', hasLid: true, delivery: { count: 999 } });
  await flush();
  assert.match(h.element('#chatBubbles').textContent, /Histórico da sessão atual/);
  assert.doesNotMatch(h.element('#chatBubbles').textContent, /CONTEÚDO ANTIGO/);
  assert.doesNotMatch(h.element('#chatList').textContent, /CONTATO ANTIGO/);
  assert.equal(h.element('#recipientInspection').textContent, '');
  assert.equal(h.element('#chatNotice').textContent, '');
  assert.deepEqual(writes(h).map(call => call.path), ['/auth/logout', '/auth/login']);
  assert.equal(h.errors.length, 0);
});

test('manual sending locks only its transport attempt and clears only the reviewed draft', async t => {
  const pendingSend = deferred();
  let accepted = false, connection = 'connected';
  const reviewedText = 'Mensagem revisada em Nova conversa';
  const h = await makePanel({ initialRoute: 'conversas', respond: (path, method) => {
    if (path === '/api/v1/qr/status') return { status: connection, automaticRepliesEnabled: false };
    if (path === '/api/v1/qr/chats') return { items: contacts };
    if (path === '/api/v1/qr/send' && method === 'POST') return pendingSend.promise;
    if (path === messagePath(phoneA)) return { items: accepted ? [{
      id: 'out:synthetic-mobile-send', body: reviewedText, direction: 'out',
      delivery: 'server_accepted', at: 1792202400000,
    }] : [incoming('Histórico sintético Alfa')] };
  } }); t.after(() => h.close());
  h.click('#chat-contact-' + phoneA); await flush();
  h.fill('#chatText', 'Outro rascunho ainda não enviado');
  h.click('#chatNew'); h.fill('#chatPhone', phoneA); h.fill('#chatText', reviewedText);
  h.submit('#sendForm'); await flush();
  assert.equal(h.element('#actionConfirm').open, true);
  assert.match(h.element('#actionConfirmText').textContent, new RegExp(phoneA));
  assert.equal(h.element('#chatText').disabled, false, 'reviewing the confirmation does not lock editing');
  assert.equal(h.element('#chatBack').disabled, false);
  assert.deepEqual(writes(h), []);
  h.click('#actionConfirmCancel'); await flush();
  assert.equal(h.element('#chatText').value, reviewedText);
  assert.equal(h.element('#sendButton').disabled, false);
  assert.deepEqual(writes(h), []);

  h.submit('#sendForm'); await flush(); h.click('#actionConfirmAccept'); await flush();
  const transport = writes(h); assert.equal(transport.length, 1);
  assert.equal(transport[0].path, '/api/v1/qr/send');
  assert.deepEqual(transport[0].data, { phone: phoneA, text: reviewedText });
  assert.equal(transport[0].options.headers['X-Orion-CSRF'], 'synthetic-mobile-csrf');
  for (const id of ['chatPhone', 'chatText', 'chatBack', 'chatNew', 'chatSearch']) {
    assert.equal(h.element('#' + id).disabled, true, id + ' remains locked until the attempted transport resolves');
  }
  h.click('#chat-contact-' + phoneB); h.click('#chatBack'); h.click('#chatNew');
  assert.equal(h.element('#chatTitle').textContent, 'Nova conversa');
  assert.equal(h.element('#chatPhone').value, '(11) 99999-0001');
  assert.equal(h.element('#chatText').value, reviewedText);
  assert.equal(h.element('#page-conversas').dataset.chatView, 'thread');

  connection = 'logged_out'; h.click('#statusRefresh'); await flush();
  accepted = true; pendingSend.resolve({ accepted: true, id: 'out:synthetic-mobile-send', delivery: 'server_accepted' });
  await flush();
  assert.match(h.element('#chatTitle').textContent, new RegExp(phoneA));
  assert.match(h.element('#chatBubbles').textContent, new RegExp(reviewedText));
  assert.match(h.element('#chatNotice').textContent, /aceitou o envio/,
    'a confirmed transport result remains recognized if connection status changes while awaiting it');
  assert.equal(h.element('#chatText').value, 'Outro rascunho ainda não enviado',
    'sending from New must not erase another saved draft for the same recipient');
  assert.equal(h.element('#chatText').disabled, false);
  h.click('#chatNew');
  assert.equal(h.element('#chatText').value, '', 'the sent New-conversation draft is cleared');
  h.click('#chatBack'); h.click('#chat-contact-' + phoneA); await flush();
  assert.equal(h.element('#chatText').value, 'Outro rascunho ainda não enviado');
  h.submit('#sendForm'); await flush();
  assert.match(h.element('#chatNotice').textContent, /Conecte seu WhatsApp/);
  assert.equal(writes(h).length, 1, 'cancellation, repeated clicks and disconnected submission never repeat the POST');
  assert.equal(h.errors.length, 0);
});
