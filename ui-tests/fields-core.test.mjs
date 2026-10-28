import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { JSDOM } from 'jsdom';

const source = fs.readFileSync(new URL('../app/static/orion-fields.js', import.meta.url), 'utf8');
function setup(t, html = '<form><label for="phone">Telefone</label><input id="phone" data-orion-field="phone"><small id="help">Com DDD</small></form>') {
  const dom = new JSDOM(html, { runScripts: 'outside-only' });
  dom.window.eval(source); dom.window.OrionFields.bind(dom.window.document);
  t.after(() => { dom.window.OrionFields.unbind(dom.window.document); dom.window.close(); });
  return { window: dom.window, document: dom.window.document, fields: dom.window.OrionFields, input: dom.window.document.querySelector('input') };
}
function edit(h, inputType, data = null, input = h.input) {
  const before = new h.window.InputEvent('beforeinput', { bubbles: true, cancelable: true, inputType, data });
  if (!input.dispatchEvent(before)) return;
  let start = input.selectionStart ?? input.value.length, end = input.selectionEnd ?? start;
  if (inputType === 'deleteContentBackward' && start === end) start = Math.max(0, start - 1);
  if (inputType === 'deleteContentForward' && start === end) end = Math.min(input.value.length, end + 1);
  input.setRangeText(data ?? '', start, end, 'end');
  input.dispatchEvent(new h.window.InputEvent('input', { bubbles: true, inputType, data }));
}

test('telefone mantém DDD55, país55, fixo, celular e canônico por ida/volta', t => {
  const { fields } = setup(t);
  for (const [value, display, canonical] of [
    ['67912345678', '(67) 91234-5678', '+5567912345678'],
    ['+55 (67) 91234-5678', '(67) 91234-5678', '+5567912345678'],
    ['5567912345678', '(67) 91234-5678', '+5567912345678'],
    ['(67) 3234-5678', '(67) 3234-5678', '+556732345678'],
    ['55991234567', '(55) 99123-4567', '+5555991234567'],
    ['5555991234567', '(55) 99123-4567', '+5555991234567'],
  ]) {
    assert.equal(fields.phoneDisplay(value), display);
    assert.equal(fields.phoneValue(value), canonical);
    assert.equal(fields.phoneValue(fields.phoneDisplay(canonical)), canonical);
  }
  assert.equal(fields.phoneValue(''), null);
  assert.throws(() => fields.phoneValue('', { required: true }));
  assert.throws(() => fields.phoneValue('123'));
  assert.throws(() => fields.phoneValue('(55) 99123-45678'));
  assert.throws(() => fields.phoneValue('+1 202 555 0123'));
  assert.equal(fields.phoneValue('+12025550123', { allowInternational: true }), '+12025550123');
  assert.equal(fields.phoneValue('+1 (202) 555-0123', { allowInternational: true }), '+12025550123');
});

test('digitação incremental, inserção no meio e seleção preservam todos os dígitos', t => {
  const h = setup(t); h.input.focus();
  for (const char of '67912345678') { edit(h, 'insertText', char); assert.equal(h.input.selectionStart, h.input.value.length); }
  assert.equal(h.input.value, '(67) 91234-5678');
  h.input.setSelectionRange(6, 7); edit(h, 'insertText', '0');
  assert.equal(h.input.value, '(67) 90234-5678');
  assert.equal(h.input.selectionStart, 7);
  h.input.setSelectionRange(0, h.input.value.length); edit(h, 'deleteContentBackward');
  assert.equal(h.input.value, '');
});

test('colar+55 não é truncado por maxlength antigo e entrada excessiva nunca muda de destinatário', t => {
  const h = setup(t, '<form><input maxlength="15" data-orion-field="phone"></form>');
  assert.equal(h.input.hasAttribute('maxlength'), false);
  edit(h, 'insertFromPaste', '+55 (55) 99123-4567');
  assert.equal(h.input.value, '(55) 99123-4567');
  edit(h, 'insertText', '8');
  assert.equal(h.input.value, '(55) 99123-45678');
  assert.equal(h.fields.validate(h.input), false);
  assert.equal(h.input.value, '(55) 99123-45678');
  h.input.value = 'telefone: 67912345678'; h.input.dispatchEvent(new h.window.Event('change', { bubbles: true }));
  assert.equal(h.input.value, 'telefone: 67912345678');
  assert.equal(h.fields.validate(h.input), false);
});

test('Backspace e Delete em pontuação removem dígito próximo sem prender o cursor', t => {
  const h = setup(t); h.input.value = '(67) 91234-5678';
  h.input.setSelectionRange(11, 11); edit(h, 'deleteContentBackward');
  assert.equal(h.input.value.replace(/\D/g, ''), '6791235678');
  h.input.value = '(67) 91234-5678'; h.input.setSelectionRange(10, 10); edit(h, 'deleteContentForward');
  assert.equal(h.input.value.replace(/\D/g, ''), '6791234678');
});

test('binding é idempotente, opt-in, desmontável e valida sem apagar ajuda existente', t => {
  const h = setup(t, '<form><input data-orion-field="phone" aria-describedby="help"><small id="help">Com DDD</small><input id="plain" type="text" value="67912345678"><input id="secret" type="password" value="55-ab!"></form>');
  assert.equal(h.fields.bind(h.document), h.fields.bind(h.document));
  assert.equal(h.document.querySelector('#plain').value, '67912345678');
  assert.equal(h.document.querySelector('#secret').value, '55-ab!');
  h.input.value = '123';
  const submit = new h.window.Event('submit', { bubbles: true, cancelable: true });
  assert.equal(h.document.querySelector('form').dispatchEvent(submit), false);
  assert.equal(h.input.getAttribute('aria-invalid'), 'true');
  assert.match(h.input.getAttribute('aria-describedby'), /^help orion-field-error-/);
  assert.equal(h.document.querySelectorAll('.orion-field-error').length, 1);
  h.fields.validate(h.input); assert.equal(h.document.querySelectorAll('.orion-field-error').length, 1);
  h.input.value = '67912345678'; h.input.dispatchEvent(new h.window.Event('input', { bubbles: true }));
  assert.equal(h.input.value, '(67) 91234-5678');
  assert.equal(h.input.getAttribute('aria-describedby'), 'help');
  assert.equal(h.input.hasAttribute('aria-invalid'), false);
  assert.equal(h.fields.validateForm(h.document.querySelector('form')), true);
  h.fields.unbind(h.document); h.input.value = '67912345678'; h.input.dispatchEvent(new h.window.Event('input', { bubbles: true }));
  assert.equal(h.input.value, '67912345678');
});

test('autofill, campos readonly/disabled e novos inputs explícitos respeitam seus estados', t => {
  const h = setup(t, '<form><input data-orion-field="phone"><input id="ro" data-orion-field="phone" readonly value="valor original"><input id="disabled" data-orion-field="phone" disabled value="123"></form>');
  h.input.value = '+5567912345678'; h.input.dispatchEvent(new h.window.Event('change', { bubbles: true }));
  assert.equal(h.input.value, '(67) 91234-5678');
  assert.equal(h.document.querySelector('#ro').value, 'valor original');
  assert.equal(h.fields.validate(h.document.querySelector('#disabled')), true);
  const newField = h.document.createElement('input'); newField.dataset.orionField = 'phone'; newField.value = '+5555991234567'; h.document.querySelector('form').append(newField);
  h.fields.refresh(newField); assert.equal(newField.value, '(55) 99123-4567');
  assert.equal(newField.type, 'tel');
});

test('BRL interpreta milhares brasileiros, centavos e decimal legado por regras distintas', t => {
  const { fields } = setup(t);
  for (const [raw, cents, display] of [
    ['0', 0, '0,00'], ['0,01', 1, '0,01'], ['10', 1000, '10,00'], ['10,50', 1050, '10,50'],
    ['1234', 123400, '1.234,00'], ['1.234', 123400, '1.234,00'], ['1234,56', 123456, '1.234,56'],
    ['1.234,56', 123456, '1.234,56'], ['1234.56', 123456, '1.234,56'], ['R$ 1.234,56', 123456, '1.234,56'],
    ['0.01', 1, '0,01'], ['1.234.567,89', 123456789, '1.234.567,89'],
  ]) {
    assert.equal(fields.parseMoney(raw), cents, raw);
    assert.equal(fields.moneyDisplay(cents), display);
    assert.equal(fields.parseMoney(fields.moneyDisplay(cents)), cents);
  }
  assert.equal(fields.parseMoney(''), null);
  assert.equal(fields.moneyDisplay(null), '');
  assert.throws(() => fields.parseMoney('', { required: true }));
  assert.throws(() => fields.parseMoney('1234.56', { allowLegacyDecimal: false }));
  assert.equal(fields.parseMoney('1.234', { allowLegacyDecimal: false }), 123400);
});

test('dinheiro rejeita entrada parcial, agrupamento inválido, precisão extra e overflow sem arredondar', t => {
  const { fields } = setup(t);
  for (const invalid of ['R$', '1e3', 'abc 123', '12.34,56', '1,234', '1.234.56', '1234.567', '-1', '1,', '1 234,56', 'NaN', '900719925474099.00']) {
    assert.throws(() => fields.parseMoney(invalid), undefined, invalid);
  }
  assert.equal(fields.parseMoney('90.071.992.547.409,91'), Number.MAX_SAFE_INTEGER);
  assert.throws(() => fields.parseMoney('90.071.992.547.409,92'));
  assert.throws(() => fields.parseMoney('0', { min: 1 }));
  assert.throws(() => fields.parseMoney('10,01', { max: 1000 }));
  assert.equal(fields.parseMoney('10', { max: 1000 }), 1000);
});

test('dinheiro é inteiro-first; digitar 1234 mantém o cursor e editar centavos não multiplica o valor', t => {
  const h = setup(t, '<form><input data-orion-field="money"></form>');
  for (const char of '1234') edit(h, 'insertText', char);
  assert.equal(h.input.value, '1.234,00'); assert.equal(h.input.selectionStart, 5);
  edit(h, 'insertText', ','); assert.equal(h.input.selectionStart, 6);
  edit(h, 'insertText', '5'); edit(h, 'insertText', '6');
  assert.equal(h.input.value, '1.234,56'); assert.equal(h.fields.parseMoney(h.input.value), 123456);
  edit(h, 'insertText', '7');
  assert.equal(h.input.value, '1.234,567'); assert.equal(h.fields.validate(h.input), false, 'não truncar um terceiro decimal');
  h.input.setSelectionRange(0, h.input.value.length); edit(h, 'deleteContentBackward');
  assert.equal(h.input.value, '');
  edit(h, 'insertText', '1'); edit(h, 'deleteContentBackward');
  assert.equal(h.input.value, '', 'apagar o único real deixa vazio, não zero');
});

test('dinheiro aceita colagem completa, seleção e Backspace em separador de milhar', t => {
  const h = setup(t, '<form><input data-orion-field="money"></form>');
  edit(h, 'insertFromPaste', 'R$ 1.234,56');
  assert.equal(h.input.value, '1.234,56'); assert.equal(h.input.selectionStart, 8);
  h.input.setSelectionRange(2, 2); edit(h, 'deleteContentBackward');
  assert.equal(h.input.value, '234,56');
  h.input.setSelectionRange(4, 6); edit(h, 'insertFromPaste', '01');
  assert.equal(h.input.value, '234,01');
  h.input.value = '1234.56'; h.input.dispatchEvent(new h.window.Event('input', { bubbles: true }));
  assert.equal(h.input.value, '1.234,56');
});

test('edição monetária sem beforeinput substitui centavos existentes mas nunca trunca uma colagem', t => {
  const h = setup(t, '<form><input data-orion-field="money" value="4,00"></form>');
  const insert = (value, inputType = 'insertText') => {
    h.input.setRangeText(value, h.input.selectionStart, h.input.selectionEnd, 'end');
    h.input.dispatchEvent(new h.window.InputEvent('input', { bubbles: true, inputType, data: value }));
  };
  h.input.setSelectionRange(2, 4); insert('2');
  assert.equal(h.input.value, '4,20'); assert.equal(h.input.selectionStart, 3);
  insert('5');
  assert.equal(h.input.value, '4,25'); assert.equal(h.input.selectionStart, 4);
  assert.equal(h.fields.parseMoney(h.input.value), 425);
  insert('7');
  assert.equal(h.input.value, '4,257'); assert.equal(h.fields.validate(h.input), false);
  h.input.setSelectionRange(0, h.input.value.length); insert('4,567', 'insertFromPaste');
  assert.equal(h.input.value, '4,567'); assert.equal(h.fields.validate(h.input), false);
  h.input.setSelectionRange(0, h.input.value.length); insert('4,25', 'insertFromPaste');
  h.input.setSelectionRange(3, 3); insert('8', 'insertFromPaste');
  assert.equal(h.input.value, '4,285'); assert.equal(h.fields.validate(h.input), false);
});

test('decimais são explícitos, preservam vírgula, validam limites e não recebem máscara monetária', t => {
  const h = setup(t, '<form><input data-orion-field="decimal" data-field-min="0" data-field-max="100"></form>');
  edit(h, 'insertFromPaste', '10.5'); assert.equal(h.input.value, '10,5');
  assert.equal(h.fields.parseDecimal(h.input.value), 10.5);
  assert.equal(h.fields.parseDecimal(''), null);
  assert.equal(h.fields.parseDecimal('-3', { min: -10, scale: 0 }), -3);
  assert.throws(() => h.fields.parseDecimal('1.234,56'));
  assert.throws(() => h.fields.parseDecimal('10,5', { scale: 0 }));
  h.input.value = '100,01'; assert.equal(h.fields.validate(h.input), false);
  assert.match(h.input.validationMessage, /100/);
});

test('fieldset desabilitado é preservado e erro em campo composto é inserido no wrapper', t => {
  const h = setup(t, '<form><fieldset disabled><input data-orion-field="phone" value="original"></fieldset><div data-field-wrapper><span>R$</span><input id="money" data-orion-field="money" value="abc"></div></form>');
  assert.equal(h.input.value, 'original'); assert.equal(h.fields.validate(h.input), true);
  const money = h.document.querySelector('#money'); assert.equal(h.fields.validate(money), false);
  const error = h.document.querySelector('.orion-field-error');
  assert.equal(error.parentElement, money.closest('[data-field-wrapper]'));
  assert.equal(error, error.parentElement.lastElementChild);
});

test('CPF valida verificadores e mantém zero inicial como identificador textual', t => {
  const { fields } = setup(t);
  assert.equal(fields.documentValue('012.345.678-90', 'cpf'), '01234567890');
  assert.equal(fields.documentDisplay('01234567890', 'cpf'), '012.345.678-90');
  assert.equal(fields.documentValue('529.982.247-25', 'cpf'), '52998224725');
  for (const invalid of ['00000000000', '11111111111', '01234567891', '52998224724', '123', '529A8224725']) assert.throws(() => fields.documentValue(invalid, 'cpf'));
  assert.equal(fields.documentValue('', 'cpf'), null);
});

test('CNPJ numérico e alfanumérico usam algoritmo oficial ASCII−48, incluindo letras na filial', t => {
  const { fields } = setup(t);
  // Official Receita Federal/SERPRO manual example: first DV3, second DV5.
  assert.equal(fields.documentValue('12.ABC.345/01DE-35', 'cnpj'), '12ABC34501DE35');
  assert.equal(fields.documentValue('12.abc.345/01de-35', 'cnpj'), '12ABC34501DE35');
  assert.equal(fields.documentDisplay('12ABC34501DE35', 'cnpj'), '12.ABC.345/01DE-35');
  assert.equal(fields.documentValue('04.252.011/0001-10', 'cnpj'), '04252011000110');
  for (const invalid of ['12ABC34501DE34', '12ABC34501DE3A', '00000000000000', '04252011000111', '12ÁBC34501DE35', '12ABC34501DE350']) assert.throws(() => fields.documentValue(invalid, 'cnpj'));
  assert.equal(fields.documentDisplay('12ABC34501DE350', 'cnpj'), '12ABC34501DE350', 'excedente fica visível');
});

test('documento histórico só é tolerado quando explicitamente original e inalterado', t => {
  const h = setup(t, '<form><input data-orion-field="cpf" data-orion-original="12345678900" data-orion-original-kind="cpf" value="123.456.789-00"></form>');
  assert.equal(h.fields.validate(h.input), true);
  assert.equal(h.fields.documentValue('123.456.789-00', 'cpf', { original: '12345678900', originalKind: 'cpf' }), '12345678900');
  h.input.value = '123.456.789-01'; assert.equal(h.fields.validate(h.input), false);
  assert.throws(() => h.fields.documentValue('12345678900', 'cpf'));
  assert.throws(() => h.fields.documentValue('12345678900', 'cnpj', { original: '12345678900', originalKind: 'cpf' }));
  assert.throws(() => h.fields.documentValue('12345678900', 'cpf', { original: '12345678900', originalKind: 'cnpj' }));
  assert.throws(() => h.fields.documentValue('12345678900', 'cpf', { original: '12345678900' }));
});

test('máscara de CNPJ mantém letras, seleção e a troca explícita de tipo sem descartar conteúdo', t => {
  const h = setup(t, '<form><input data-orion-field="cnpj"></form>');
  for (const char of '12abc34501de35') edit(h, 'insertText', char);
  assert.equal(h.input.value, '12.ABC.345/01DE-35');
  assert.equal(h.input.inputMode, 'text'); assert.equal(h.input.selectionStart, h.input.value.length);
  h.input.setSelectionRange(6, 6); edit(h, 'deleteContentForward');
  assert.equal(h.input.value.replace(/[.\/-]/g, ''), '12ABC4501DE35');
  h.input.dataset.orionField = 'cpf'; h.fields.refresh(h.input);
  assert.equal(h.input.inputMode, 'numeric'); assert.match(h.input.value, /ABC/);
  assert.equal(h.fields.validate(h.input), false, 'mudar tipo não apaga letras silenciosamente');
});

test('CEP preserva zeros e nunca trunca uma colagem longa', t => {
  const h = setup(t, '<form><input data-orion-field="postal-code" maxlength="9"></form>');
  edit(h, 'insertFromPaste', '01001-000');
  assert.equal(h.input.value, '01001-000'); assert.equal(h.fields.postalCodeValue(h.input.value), '01001000');
  assert.equal(h.fields.postalCodeValue('01001000'), '01001000'); assert.equal(h.fields.postalCodeValue(''), null);
  edit(h, 'insertText', '9'); assert.equal(h.input.value, '01001-0009'); assert.equal(h.fields.validate(h.input), false);
  assert.throws(() => h.fields.postalCodeValue('abc01001000'));
  assert.throws(() => h.fields.postalCodeValue('01001'));
});

test('OTP continua texto, preserva zero inicial, aceita colagem completa e não filtra letras', t => {
  const h = setup(t, '<form><input data-orion-field="otp" maxlength="6" required></form>');
  edit(h, 'insertFromPaste', '012 345');
  assert.equal(h.input.value, '012345'); assert.equal(h.fields.otpValue(h.input.value), '012345');
  assert.equal(h.input.type, 'text'); assert.equal(h.input.inputMode, 'numeric'); assert.equal(h.input.autocomplete, 'one-time-code');
  h.input.setSelectionRange(0, 6); edit(h, 'insertFromPaste', '012a345');
  assert.equal(h.input.value, '012a345'); assert.equal(h.fields.validate(h.input), false);
  assert.throws(() => h.fields.otpValue('12345')); assert.throws(() => h.fields.otpValue('1234567'));
  assert.throws(() => h.fields.otpValue('', { required: true }));
});

test('data civil valida calendário e limites sem conversão UTC ou deslocamento do dia', t => {
  const { fields } = setup(t);
  for (const [raw, iso] of [['29/02/2024', '2024-02-29'], ['2026-10-27', '2026-10-27'], ['01/01/0001', '0001-01-01'], ['29/02/2000', '2000-02-29']]) {
    assert.equal(fields.dateValue(raw), iso); assert.equal(fields.dateValue(fields.dateDisplay(iso)), iso);
  }
  for (const invalid of ['2023-02-29', '1900-02-29', '2026-04-31', '0000-01-01', '2026-13-01', '2026-10-00', '2026-1-1', '2026-10-27T23:00:00-04:00']) assert.throws(() => fields.dateValue(invalid));
  assert.equal(fields.dateValue(''), null);
  assert.throws(() => fields.dateValue('2026-10-28', { max: '2026-10-27' }));
  assert.throws(() => fields.dateValue('1899-12-31', { min: '1900-01-01' }));
  const h = setup(t, '<form><input type="date" data-orion-field="date" min="1900-01-01" max="2026-10-27" value="2024-02-29"></form>');
  assert.equal(h.input.type, 'date'); assert.equal(h.input.value, '2024-02-29'); assert.equal(h.fields.validate(h.input), true);
  h.input.value = '2026-10-28'; assert.equal(h.fields.validate(h.input), false);
});

test('email usa contrato local, preserva caixa/+subdomínio e nunca inventa domínio', t => {
  const h = setup(t, '<form><label for="email">E-mail</label><input id="email" data-orion-field="email" maxlength="254" aria-describedby="help"><small id="help">Seu contato</small><input id="username" type="text" autocomplete="username"></form>');
  assert.equal(h.input.type, 'email'); assert.equal(h.input.inputMode, 'email');
  assert.equal(h.input.placeholder, 'nome@email.com');
  h.input.value = '  Kayky+Agenda@sub.example.com  ';
  assert.equal(h.fields.validate(h.input), true); assert.equal(h.input.value, 'Kayky+Agenda@sub.example.com');
  h.input.value = 'Kayky'; assert.equal(h.fields.validate(h.input), false); assert.equal(h.input.value, 'Kayky');
  assert.match(h.input.getAttribute('aria-describedby'), /^help orion-field-error-/);
  h.input.value = ''; assert.equal(h.fields.validate(h.input), true);
  h.input.required = true; assert.equal(h.fields.validate(h.input), false);
  h.input.value = 'a'.repeat(250) + '@x.com'; assert.equal(h.fields.validate(h.input), false);
  h.input.maxLength = 320; assert.equal(h.fields.validate(h.input), true, 'sem limite global de254');
  assert.equal(h.document.querySelector('#username').type, 'text');
  assert.equal(h.document.querySelector('#username').autocomplete, 'username');
});

test('native valida obrigatoriedade e limites de texto sem normalizar Unicode, espaços ou quebras de linha', t => {
  const h = setup(t, '<form><div data-field-wrapper><label for="name">Nome</label><input id="name" data-orion-field="native" required minlength="2" maxlength="30" aria-describedby="help"><small id="help">Nome completo</small></div><textarea data-orion-field="native" maxlength="30"></textarea></form>');
  assert.equal(h.fields.validateForm(h.document.querySelector('form')), false);
  assert.equal(h.document.activeElement, h.input);
  assert.match(h.input.getAttribute('aria-describedby'), /^help orion-field-error-/);
  const name = '  João D’Ávila-Sá  ', notes = 'Atenção à pele.\nNão usar X.';
  h.input.value = name;
  const textarea = h.document.querySelector('textarea'); textarea.value = notes;
  assert.equal(h.fields.validateForm(h.document.querySelector('form')), true);
  assert.equal(h.input.value, name); assert.equal(textarea.value, notes);
  assert.equal(h.input.getAttribute('aria-describedby'), 'help');
  h.input.value = 'Á'.repeat(31);
  assert.equal(h.fields.validate(h.input), false);
  assert.equal(h.input.value, 'Á'.repeat(31)); assert.match(h.input.validationMessage, /30 caracteres/);
  h.input.value = 'A'; assert.equal(h.fields.validate(h.input), false);
  assert.match(h.input.validationMessage, /2 caracteres/);
  textarea.value = '🧑'.repeat(16);
  assert.equal(h.fields.validate(textarea), false, 'native maxlength uses UTF-16 code units');
  assert.equal(textarea.value, '🧑'.repeat(16));
});

test('native respeita número, min/max/step, horário, URL, pattern e seleção sem atribuir máscara', t => {
  const h = setup(t, '<form><input id="amount" data-orion-field="native" type="number" min="1" max="20" step="2"><input id="clock" data-orion-field="native" type="time" min="09:00" max="18:00" step="900"><input id="url" data-orion-field="native" type="url"><input id="pattern" data-orion-field="native" pattern="[A-Z]{2}" title="Use duas letras maiúsculas."><select data-orion-field="native" required><option value="">Selecionar</option><option value="á">Opção á</option></select></form>');
  for (const value of ['0', '21', '2']) { h.input.value = value; assert.equal(h.fields.validate(h.input), false, value); }
  h.input.value = '3'; assert.equal(h.fields.validate(h.input), true); assert.equal(h.input.type, 'number');
  const clock = h.document.querySelector('#clock');
  for (const value of ['08:45', '18:15', '09:07']) { clock.value = value; assert.equal(h.fields.validate(clock), false, value); }
  clock.value = '17:45'; assert.equal(h.fields.validate(clock), true); assert.equal(clock.type, 'time');
  const url = h.document.querySelector('#url'); url.value = 'sem-protocolo'; assert.equal(h.fields.validate(url), false);
  assert.equal(url.value, 'sem-protocolo'); url.value = 'https://example.invalid/Á'; assert.equal(h.fields.validate(url), true);
  const pattern = h.document.querySelector('#pattern'); pattern.value = 'sp'; assert.equal(h.fields.validate(pattern), false);
  assert.equal(pattern.value, 'sp'); assert.equal(pattern.validationMessage, 'Use duas letras maiúsculas.');
  pattern.value = 'SP'; assert.equal(h.fields.validate(pattern), true);
  const select = h.document.querySelector('select'); assert.equal(h.fields.validate(select), false);
  assert.equal(select.getAttribute('aria-invalid'), 'true'); select.value = 'á';
  assert.equal(h.fields.validateForm(h.document.querySelector('form')), true); assert.equal(select.value, 'á');
});

test('native não valida controles disabled/readonly e preserva senhas e campos sem opt-in', t => {
  const h = setup(t, '<form><fieldset disabled><input data-orion-field="native" required><select data-orion-field="native" required><option value="">Selecionar</option></select></fieldset><input data-orion-field="native" readonly required><input id="secret" type="password" value="  ÁZ!  "><textarea id="plain">  texto livre\nlinha 2  </textarea></form>');
  assert.equal(h.input.disabled, false); assert.equal(h.input.matches(':disabled'), true);
  assert.equal(h.fields.validateForm(h.document.querySelector('form')), true);
  assert.equal(h.document.querySelector('.orion-field-error'), null);
  assert.equal(h.document.querySelector('#secret').value, '  ÁZ!  ');
  assert.equal(h.document.querySelector('#plain').value, '  texto livre\nlinha 2  ');
  h.fields.refresh(h.document);
  assert.equal(h.document.querySelector('#secret').type, 'password');
  assert.equal(h.document.querySelector('#secret').value, '  ÁZ!  ');
});
