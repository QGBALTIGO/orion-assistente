import test from 'node:test';
import assert from 'node:assert/strict';
import { flush } from './harness.mjs';
import { makePublic, publicBase } from './public-harness.mjs';

async function confirmation(t) {
  const h = await makePublic(); t.after(() => h.close());
  await flush();
  h.click('#services-grid .service-card');
  h.click('#booking-next'); await flush();
  h.click('#time-slots .slot');
  h.click('#booking-next');
  return h;
}

const posts = h => h.calls.filter(call => call.options.method === 'POST');

test('agendamento aceita colagem +55 com DDD55 e preserva nome, data civil e destinatário no payload', async t => {
  const h = await confirmation(t);
  const phone = h.document.querySelector('[name=customer_phone]');
  const birth = h.document.querySelector('[name=customer_birth_date]');
  assert.equal(phone.hasAttribute('maxlength'), false);
  assert.equal(phone.inputMode, 'tel');
  assert.equal(phone.autocomplete, 'tel-national');
  assert.equal(birth.type, 'date');
  assert.equal(birth.lang, 'pt-BR');
  assert.equal(birth.required, true);
  h.fill('[name=customer_name]', '  João D’Ávila-Sá  ');
  h.fill('[name=customer_phone]', '+55 (55) 99123-4567');
  h.fill('[name=customer_birth_date]', '2000-02-29');
  assert.equal(phone.value, '(55) 99123-4567');
  assert.equal(phone.hasAttribute('aria-invalid'), false);
  h.click('#booking-next'); await flush();
  assert.equal(posts(h).length, 1);
  assert.equal(posts(h)[0].url, publicBase + '/appointments');
  assert.equal(posts(h)[0].body.customer_phone, '+5555991234567');
  assert.equal(posts(h)[0].body.customer_birth_date, '2000-02-29');
  assert.equal(posts(h)[0].body.customer_name, 'João D’Ávila-Sá');
  assert.equal(h.errors.length, 0);
});

test('agendamento conserva telefone inválido visível e bloqueia envio com erro associado ao campo', async t => {
  const h = await confirmation(t);
  h.fill('[name=customer_name]', 'Cliente sintética');
  h.fill('[name=customer_birth_date]', '1999-09-09');
  const phone = h.document.querySelector('[name=customer_phone]');
  for (const invalid of ['(55) 99123-45678', 'telefone 55991234567', '123']) {
    h.fill('[name=customer_phone]', invalid);
    h.click('#booking-next'); await flush();
    assert.equal(posts(h).length, 0);
    assert.equal(phone.value, invalid === '123' ? '(12) 3' : invalid);
    assert.equal(phone.getAttribute('aria-invalid'), 'true');
    const error = h.document.getElementById(phone.getAttribute('aria-describedby'));
    assert.ok(error);
    assert.equal(error.getAttribute('role'), 'alert');
    assert.equal(error.parentElement, phone.closest('[data-field-wrapper]'));
    assert.equal(error.parentElement.querySelectorAll('.orion-field-error').length, 1);
    assert.equal(h.document.activeElement, phone);
  }
  h.fill('[name=customer_phone]', '55991234567');
  assert.equal(phone.hasAttribute('aria-invalid'), false);
  assert.equal(phone.hasAttribute('aria-describedby'), false);
  assert.equal(h.document.querySelector('.orion-field-error'), null);
  h.click('#booking-next'); await flush();
  assert.equal(posts(h).length, 1);
  assert.equal(posts(h)[0].body.customer_phone, '+5555991234567');
  assert.equal(h.errors.length, 0);
});

test('agendamento lê autofill sem evento input e não perde zeros no telefone fixo', async t => {
  const h = await confirmation(t);
  h.document.querySelector('[name=customer_name]').value = 'María da Conceição';
  h.document.querySelector('[name=customer_phone]').value = '+55 (55) 3333-0042';
  h.document.querySelector('[name=customer_birth_date]').value = '1980-01-02';
  h.click('#booking-next'); await flush();
  assert.equal(posts(h).length, 1);
  assert.equal(posts(h)[0].body.customer_phone, '+555533330042');
  assert.equal(posts(h)[0].body.customer_name, 'María da Conceição');
  assert.equal(posts(h)[0].body.customer_birth_date, '1980-01-02');
  assert.equal(h.errors.length, 0);
});

test('nome obrigatório e excedente no agendamento têm erro associado sem apagar a grafia ou os demais dados', async t => {
  const h = await confirmation(t);
  const name = h.document.querySelector('[name=customer_name]');
  h.fill('[name=customer_phone]', '55991234567');
  h.fill('[name=customer_birth_date]', '2000-02-29');
  for (const value of ['', 'Á'.repeat(121)]) {
    h.fill('[name=customer_name]', value); h.click('#booking-next'); await flush();
    assert.equal(posts(h).length, 0); assert.equal(name.value, value);
    assert.equal(name.getAttribute('aria-invalid'), 'true');
    const error = h.document.getElementById(name.getAttribute('aria-describedby'));
    assert.equal(error.parentElement, name.closest('[data-field-wrapper]'));
    assert.equal(h.document.activeElement, name);
    assert.equal(h.document.querySelector('[name=customer_phone]').value, '(55) 99123-4567');
    assert.equal(h.document.querySelector('[name=customer_birth_date]').value, '2000-02-29');
  }
  h.fill('[name=customer_name]', "  João D'Ávila-Souza  ");
  assert.equal(name.value, "  João D'Ávila-Souza  ");
  h.click('#booking-next'); await flush();
  assert.equal(posts(h).length, 1);
  assert.equal(posts(h)[0].body.customer_name, "João D'Ávila-Souza");
  assert.equal(h.errors.length, 0);
});
