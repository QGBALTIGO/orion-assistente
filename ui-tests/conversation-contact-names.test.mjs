import test from 'node:test';
import assert from 'node:assert/strict';
import { flush } from './harness.mjs';
import { makePanel as createPanel } from './panel-harness.mjs';

// Legacy conversations now run only under the platform-admin account.
const makePanel=(options={})=>createPanel({role:'admin',...options});

const phoneA = '5511999990001', phoneB = '5511999990002', phoneC = '5511999990003';
const names = [
  { phone: phoneA, display_name: 'José Andrade', name: 'Nome de menor prioridade', preview: 'Horário confirmado' },
  { phone: phoneB, display_name: '  Contato WhatsApp  ', whatsapp_name: 'Marina Duarte', preview: 'Quero agendar' },
  { phone: phoneC, name: 'Cliente WhatsApp', display_name: 'Nome não informado', preview: 'Sem nome recebido' },
];

for (const width of [390, 820, 1366]) {
  test(`conversations show available names and search names or phones at ${width}px`, async t => {
    const h = await makePanel({ width, initialRoute: 'conversas', deviceStatus: 'connected', respond: path => {
      if (path === '/api/v1/qr/chats') return { items: names };
    } }); t.after(() => h.close());
    const rowA = h.element('#chat-contact-' + phoneA), rowB = h.element('#chat-contact-' + phoneB);
    assert.equal(rowA.querySelector('strong').textContent, 'José Andrade');
    assert.equal(rowA.querySelector('.chat-contact-phone').textContent, '+' + phoneA);
    assert.equal(rowB.querySelector('strong').textContent, 'Marina Duarte');
    assert.equal(h.element('#chat-contact-' + phoneC + ' strong').textContent, '+' + phoneC);
    assert.doesNotMatch(h.element('#chatList').textContent, /Cliente WhatsApp/);

    const reads = h.calls.length;
    h.fill('#chatSearch', 'jose');
    assert.equal(h.element('#chatList').querySelectorAll('button').length, 1);
    assert.ok(h.document.querySelector('#chat-contact-' + phoneA));
    h.fill('#chatSearch', '+55 (11) 99999-0002');
    assert.equal(h.element('#chatList').querySelectorAll('button').length, 1);
    assert.ok(h.document.querySelector('#chat-contact-' + phoneB));
    h.fill('#chatSearch', 'agendar');
    assert.equal(h.element('#chatList').querySelectorAll('button').length, 1);
    assert.equal(h.calls.length, reads, 'search filters the loaded names without extra requests');
    h.fill('#chatSearch', '');
    h.click('#chat-contact-' + phoneA); await flush();
    assert.equal(h.element('#chatTitle').textContent, 'José Andrade');
    assert.equal(h.element('#chatContactPhone').hidden, false);
    assert.equal(h.element('#chatContactPhone').textContent, '+' + phoneA);
    assert.equal(h.element('#chatPhone').value, '(11) 99999-0001', 'name rendering must not change the recipient');
    h.click('#chatNew');
    assert.equal(h.element('#chatContactPhone').hidden, true);
    assert.equal(h.element('#chatContactPhone').textContent, '');
    assert.equal(h.element('#chatTitle').textContent, 'Nova conversa');
    assert.deepEqual(h.calls.filter(call => call.method !== 'GET'), []);
    assert.equal(h.errors.length, 0);
  });
}

test('updated contact metadata changes the visible title without disturbing the selected phone or draft', async t => {
  let current = [{ phone: phoneA, preview: 'Prévia sintética' }];
  const h = await makePanel({ initialRoute: 'conversas', deviceStatus: 'connected', respond: path => {
    if (path === '/api/v1/qr/chats') return { items: current };
  } }); t.after(() => h.close());
  h.click('#chat-contact-' + phoneA); await flush();
  assert.match(h.element('#chatTitle').textContent, new RegExp(phoneA));
  h.fill('#chatText', 'Meu rascunho continua aqui'); h.element('#chatText').focus();
  current = [{ phone: phoneA, display_name: 'Renata Lima', preview: 'Prévia sintética' }];
  await h.tickClocks(6500);
  assert.equal(h.element('#chatTitle').textContent, 'Renata Lima');
  assert.equal(h.element('#chatText').value, 'Meu rascunho continua aqui');
  assert.equal(h.document.activeElement, h.element('#chatText'));
  assert.equal(h.element('#chatPhone').value, '(11) 99999-0001');
  assert.equal(h.element('#page-conversas').dataset.chatView, 'thread');
  h.click('#logout'); await flush();
  assert.equal(h.element('#chatContactPhone').textContent, '');
  assert.equal(h.element('#chatContactPhone').hidden, true);
  assert.doesNotMatch(h.element('#chatTitle').textContent, /Renata/);
  assert.equal(h.errors.length, 0);
});

test('equal WhatsApp names keep distinct recipients and untrusted names render only as text', async t => {
  const literalName = '<img src=x onerror=alert(1)>';
  const h = await makePanel({ initialRoute: 'conversas', deviceStatus: 'connected', respond: path => {
    if (path === '/api/v1/qr/chats') return { items: [
      { phone: phoneA, display_name: 'Alex', preview: 'Primeiro telefone' },
      { phone: phoneB, display_name: 'Alex', preview: 'Segundo telefone' },
      { phone: phoneC, display_name: literalName, preview: 'Texto não confiável' },
    ] };
  } }); t.after(() => h.close());
  assert.equal(h.element('#chat-contact-' + phoneC + ' strong').textContent, literalName);
  assert.equal(h.element('#chatList').querySelector('img'), null);
  h.click('#chat-contact-' + phoneA); await flush(); h.fill('#chatText', 'Rascunho do primeiro Alex');
  h.click('#chatBack'); h.click('#chat-contact-' + phoneB); await flush();
  assert.equal(h.element('#chatTitle').textContent, 'Alex');
  assert.equal(h.element('#chatContactPhone').textContent, '+' + phoneB);
  assert.equal(h.element('#chatPhone').value, '(11) 99999-0002');
  assert.equal(h.element('#chatText').value, '');
  h.click('#chatBack'); h.click('#chat-contact-' + phoneA); await flush();
  assert.equal(h.element('#chatText').value, 'Rascunho do primeiro Alex');
  assert.equal(h.element('#chatPhone').value, '(11) 99999-0001');
  assert.deepEqual(h.calls.filter(call => call.method !== 'GET'), []);
  assert.equal(h.errors.length, 0);
});
