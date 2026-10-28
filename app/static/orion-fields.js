/* Shared, opt-in field presentation. Values sent to APIs remain explicit at each caller. */
(function () {
  'use strict';

  const bindings = new WeakMap();
  const errors = new WeakMap();
  const handled = new WeakSet();
  const moneyEdits = new WeakSet();
  const moneyValues = new WeakMap();
  let errorSequence = 0;
  const selector = 'input[data-orion-field],textarea[data-orion-field],select[data-orion-field]';
  const text = value => String(value ?? '');
  const digits = value => text(value).replace(/\D/g, '');
  const isEditable = input => !!input && !input.disabled && !input.matches?.(':disabled') && !input.readOnly;

  function phoneParts(value, { allowInternational = false } = {}) {
    const raw = text(value).trim();
    if (!/^\+?[\d\s().-]*$/.test(raw)) return null;
    const number = digits(raw);
    if (raw.startsWith('+') || number.length > 11) {
      // An extra digit in a national (55) number must never change its recipient.
      if (!raw.startsWith('+') && !/^\d+$/.test(raw)) return null;
      if (number.startsWith('55') && [12, 13].includes(number.length)) {
        return { digits: number.slice(2), prefix: 2, international: false };
      }
      if (allowInternational && raw.startsWith('+') && /^[1-9]\d{7,14}$/.test(number) && !number.startsWith('55')) {
        return { digits: number, prefix: 0, international: true };
      }
      return null;
    }
    return { digits: number, prefix: 0, international: false };
  }

  function phoneDisplay(value, options) {
    const parts = phoneParts(value, options);
    if (!parts || parts.international) return text(value);
    const number = parts.digits;
    if (!number) return '';
    if (number.length <= 2) return '(' + number;
    const local = number.slice(2), split = local.length > 8 ? 5 : 4;
    return '(' + number.slice(0, 2) + ') ' + local.slice(0, split) +
      (local.length > split ? '-' + local.slice(split) : '');
  }

  function phoneValue(value, options = {}) {
    if (!text(value).trim()) {
      if (options.required) throw Error('Informe o telefone com DDD.');
      return null;
    }
    const parts = phoneParts(value, options);
    if (parts?.international) return '+' + parts.digits;
    if (!parts || !/^[1-9][0-9]{9,10}$/.test(parts.digits)) {
      throw Error('Informe um telefone com DDD e 10 ou 11 dígitos.');
    }
    return '+55' + parts.digits;
  }

  function moneyParts(value, { allowLegacyDecimal = true, incomplete = false } = {}) {
    const raw = text(value).trim().replace(/^R\$\s*/, '');
    if (!raw) return null;
    let whole = raw, fraction = '', separator = '';
    if (raw.includes(',')) {
      const pieces = raw.split(',');
      if (pieces.length !== 2 || !(incomplete ? /^\d{0,2}$/ : /^\d{1,2}$/).test(pieces[1])) throw Error('Informe o valor em reais, como 1.234,56.');
      [whole, fraction] = pieces; separator = ',';
      if (!/^\d+$/.test(whole) && !/^\d{1,3}(?:\.\d{3})+$/.test(whole)) throw Error('Informe o valor em reais, como 1.234,56.');
    } else if (/^\d+$/.test(raw) || /^\d{1,3}(?:\.\d{3})+$/.test(raw)) {
      // In Brazilian input, 1.234 is one thousand two hundred and thirty-four.
    } else if (allowLegacyDecimal && /^\d+\.\d{1,2}$/.test(raw)) {
      [whole, fraction] = raw.split('.'); separator = '.';
    } else throw Error('Informe o valor em reais, como 1.234,56.');
    whole = whole.replace(/\./g, '').replace(/^0+(?=\d)/, '');
    const integer = BigInt(whole) * 100n + BigInt(fraction.padEnd(2, '0') || '0');
    if (integer > BigInt(Number.MAX_SAFE_INTEGER)) throw Error('O valor informado é muito alto.');
    return { whole, fraction, separator, cents: Number(integer) };
  }

  function parseMoney(value, options = {}) {
    if (!text(value).trim()) {
      if (options.required) throw Error('Informe o valor em reais.');
      return null;
    }
    const parts = moneyParts(value, options);
    if (!parts) throw Error('Informe o valor em reais, como 1.234,56.');
    if (options.min != null && parts.cents < options.min) throw Error('O valor deve ser de pelo menos R$ ' + moneyDisplay(options.min) + '.');
    if (options.max != null && parts.cents > options.max) throw Error('O valor deve ser de até R$ ' + moneyDisplay(options.max) + '.');
    return parts.cents;
  }

  function moneyDisplay(cents) {
    if (cents == null || cents === '') return '';
    if (!Number.isSafeInteger(Number(cents)) || Number(cents) < 0) throw Error('Informe um valor inteiro em centavos.');
    const value = BigInt(cents), whole = String(value / 100n).replace(/\B(?=(\d{3})+(?!\d))/g, '.');
    return whole + ',' + String(value % 100n).padStart(2, '0');
  }

  function parseDecimal(value, options = {}) {
    const raw = text(value).trim();
    if (!raw) {
      if (options.required) throw Error('Preencha este valor.');
      return null;
    }
    const scale = options.scale ?? 2;
    if (!Number.isInteger(scale) || scale < 0 || scale > 8) throw Error('Precisão decimal inválida.');
    const match = raw.match(/^(-?\d+)(?:([,.])(\d+))?$/);
    if (!match || (match[3]?.length || 0) > scale) throw Error(scale ? 'Informe um número válido, como 10,5.' : 'Informe um número inteiro.');
    if (BigInt(match[1].replace('-', '') + (match[3] || '')) > BigInt(Number.MAX_SAFE_INTEGER)) throw Error('O número informado é muito alto.');
    const valueNumber = Number(raw.replace(',', '.'));
    if (!Number.isFinite(valueNumber) || Math.abs(valueNumber) > Number.MAX_SAFE_INTEGER) throw Error('O número informado é muito alto.');
    if (options.min != null && valueNumber < options.min) throw Error('O valor mínimo é ' + decimalDisplay(options.min) + '.');
    if (options.max != null && valueNumber > options.max) throw Error('O valor máximo é ' + decimalDisplay(options.max) + '.');
    return valueNumber;
  }

  function decimalDisplay(value) {
    if (value == null || value === '') return '';
    if (!Number.isFinite(Number(value))) throw Error('Informe um número válido.');
    return text(value).replace('.', ',');
  }

  function documentParts(value, kind) {
    const raw = text(value).trim(), type = text(kind).toLowerCase();
    if (type !== 'cpf' && type !== 'cnpj') return null;
    if (!(type === 'cpf' ? /^[\d.\-\s]*$/ : /^[a-zA-Z\d.\/\-\s]*$/).test(raw)) return null;
    const clean = raw.replace(/[.\/\-\s]/g, '').toUpperCase();
    const limit = type === 'cpf' ? 11 : 14;
    if (clean.length > limit || type === 'cnpj' && /[^0-9]/.test(clean.slice(12))) return null;
    return clean;
  }

  function documentDisplay(value, kind) {
    const clean = documentParts(value, kind);
    if (clean === null) return text(value);
    const groups = text(kind).toLowerCase() === 'cpf' ? [3, 3, 3, 2] : [2, 3, 3, 4, 2];
    const separators = text(kind).toLowerCase() === 'cpf' ? ['.', '.', '-'] : ['.', '.', '/', '-'];
    let result = '', offset = 0;
    for (let index = 0; index < groups.length && offset < clean.length; index++) {
      if (index) result += separators[index - 1];
      result += clean.slice(offset, offset + groups[index]); offset += groups[index];
    }
    return result;
  }

  function validCpf(value) {
    if (!/^\d{11}$/.test(value) || /^(\d)\1{10}$/.test(value)) return false;
    const digit = part => {
      const sum = [...part].reduce((total, character, index) => total + Number(character) * (part.length + 1 - index), 0);
      const remainder = sum % 11; return String(remainder < 2 ? 0 : 11 - remainder);
    };
    const base = value.slice(0, 9), first = digit(base);
    return value === base + first + digit(base + first);
  }

  function validCnpj(value) {
    if (!/^[A-Z0-9]{12}\d{2}$/.test(value) || /^(\d)\1{13}$/.test(value)) return false;
    // Receita Federal / SERPRO: ASCII minus 48, weights 2–9 from right to left.
    // Official worked example: 12.ABC.345/01DE-35 (manual-dv-cnpj.pdf).
    const digit = part => {
      let sum = 0;
      for (let index = part.length - 1, weight = 2; index >= 0; index--, weight = weight === 9 ? 2 : weight + 1) {
        sum += (part.charCodeAt(index) - 48) * weight;
      }
      const remainder = sum % 11; return String(remainder < 2 ? 0 : 11 - remainder);
    };
    const base = value.slice(0, 12), first = digit(base);
    return value === base + first + digit(base + first);
  }

  function documentValue(value, kind, options = {}) {
    if (!text(value).trim()) {
      if (options.required) throw Error('Informe o ' + text(kind).toUpperCase() + '.');
      return null;
    }
    const type = text(kind).toLowerCase(), clean = documentParts(value, type);
    const size = type === 'cpf' ? 11 : type === 'cnpj' ? 14 : 0;
    if (!clean || clean.length !== size) throw Error('Informe um ' + type.toUpperCase() + ' completo e válido.');
    // A historical value can remain unchanged; edited or new documents must have valid DVs.
    if (options.original != null && text(options.originalKind).toLowerCase() === type && clean === documentParts(options.original, type)) return clean;
    if (!(type === 'cpf' ? validCpf(clean) : validCnpj(clean))) throw Error('Confira os dígitos do ' + type.toUpperCase() + '.');
    return clean;
  }

  function postalCodeDisplay(value) {
    const raw = text(value).trim();
    if (!/^[\d\s-]*$/.test(raw) || digits(raw).length > 8) return text(value);
    const clean = digits(raw);
    return clean.slice(0, 5) + (clean.length > 5 ? '-' + clean.slice(5) : '');
  }

  function postalCodeValue(value, options = {}) {
    const raw = text(value).trim();
    if (!raw) { if (options.required) throw Error('Informe o CEP.'); return null; }
    if (!/^[\d\s-]*$/.test(raw) || digits(raw).length !== 8) throw Error('Informe um CEP com 8 dígitos, como 00000-000.');
    return digits(raw);
  }

  function otpValue(value, { required = false, length = 6 } = {}) {
    const raw = text(value).replace(/\s/g, '');
    if (!raw) { if (required) throw Error('Informe o código de ' + length + ' dígitos.'); return null; }
    if (!/^\d+$/.test(raw) || raw.length !== length) throw Error('Informe o código de ' + length + ' dígitos.');
    return raw;
  }

  function isoDate(value) {
    const raw = text(value).trim(), br = raw.match(/^(\d{2})\/(\d{2})\/(\d{4})$/);
    const iso = br ? br[3] + '-' + br[2] + '-' + br[1] : raw;
    const match = iso.match(/^(\d{4})-(\d{2})-(\d{2})$/);
    if (!match) return null;
    const year = Number(match[1]), month = Number(match[2]), day = Number(match[3]);
    const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
    const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
    if (!year || month < 1 || month > 12 || day < 1 || day > days[month - 1]) return null;
    return iso;
  }

  function dateValue(value, options = {}) {
    if (!text(value).trim()) { if (options.required) throw Error('Informe a data.'); return null; }
    const iso = isoDate(value);
    if (!iso) throw Error('Informe uma data válida.');
    const min = options.min ? isoDate(options.min) : null, max = options.max ? isoDate(options.max) : null;
    if (min && iso < min) throw Error('A data deve ser a partir de ' + dateDisplay(min) + '.');
    if (max && iso > max) throw Error('A data deve ser até ' + dateDisplay(max) + '.');
    return iso;
  }

  function dateDisplay(value) {
    if (value == null || value === '') return '';
    const iso = dateValue(value);
    return iso.slice(8) + '/' + iso.slice(5, 7) + '/' + iso.slice(0, 4);
  }

  function cursorAfter(value, count, pattern = /\d/) {
    if (count <= 0) return 0;
    let seen = 0;
    for (let index = 0; index < value.length; index++) {
      if (pattern.test(value[index])) seen++;
      if (seen === count) return index + 1;
    }
    return value.length;
  }

  function setFormatted(input, next, positions) {
    if (next === input.value) return;
    const direction = input.selectionDirection || 'none';
    input.value = next;
    if (input.selectionStart !== null && positions) {
      input.setSelectionRange(positions[0], positions[1], direction);
    }
  }

  function inputOptions(input) {
    const bound = name => input.dataset[name] == null || input.dataset[name] === '' ? undefined : Number(input.dataset[name]);
    return { required: input.required, allowInternational: input.dataset.orionInternational === 'true',
      min: input.dataset.orionField === 'date' ? input.min : bound('fieldMin'),
      max: input.dataset.orionField === 'date' ? input.max : bound('fieldMax'), scale: bound('fieldScale'),
      original: input.dataset.orionOriginal, originalKind: input.dataset.orionOriginalKind, length: bound('fieldLength') ?? 6 };
  }

  function formatInput(input, { liveMoney = false } = {}) {
    if (!isEditable(input)) return;
    const raw = input.value, start = input.selectionStart ?? raw.length, end = input.selectionEnd ?? start;
    if (input.dataset.orionField === 'phone') {
      const parts = phoneParts(raw, inputOptions(input));
      if (!parts || parts.international) return;
      const next = phoneDisplay(raw);
      const position = index => cursorAfter(next, Math.max(0, digits(raw.slice(0, index)).length - parts.prefix));
      setFormatted(input, next, [position(start), position(end)]);
    } else if (input.dataset.orionField === 'money') {
      moneyValues.delete(input);
      if (!raw.trim()) return;
      if (/^,0{0,2}$/.test(raw)) { setFormatted(input, '', [0, 0]); return; }
      const comma = raw.indexOf(',');
      const editable = liveMoney && comma >= 0 ? raw.slice(0, comma).replace(/\./g, '') + raw.slice(comma) : raw;
      let parts; try { parts = moneyParts(editable, { incomplete: true }); } catch (_) { return; }
      if (!parts) return;
      const next = moneyDisplay(parts.cents), wholeLength = next.indexOf(',');
      const decimalAt = parts.separator ? raw.lastIndexOf(parts.separator) : -1;
      const position = index => decimalAt >= 0 && index > decimalAt ?
        wholeLength + 1 + Math.min(2, digits(raw.slice(decimalAt + 1, index)).length) :
        cursorAfter(next.slice(0, wholeLength), digits(raw.slice(0, index)).length);
      setFormatted(input, next, [position(start), position(end)]);
      moneyValues.set(input, next);
    } else if (input.dataset.orionField === 'decimal') {
      // No grouping in percentages or quantities; retain incomplete/invalid text for correction.
      if (/^-?\d+(?:[,.]\d*)?$/.test(raw)) setFormatted(input, raw.replace('.', ','), [start, end]);
    } else if (['cpf', 'cnpj', 'postal-code'].includes(input.dataset.orionField)) {
      const kind = input.dataset.orionField;
      const next = kind === 'postal-code' ? postalCodeDisplay(raw) : documentDisplay(raw, kind);
      const pattern = kind === 'cnpj' ? /[a-zA-Z0-9]/ : /\d/;
      const position = index => cursorAfter(next, [...raw.slice(0, index)].filter(char => pattern.test(char)).length, pattern);
      setFormatted(input, next, [position(start), position(end)]);
    } else if (input.dataset.orionField === 'otp' && /^[\d\s]*$/.test(raw)) {
      const next = raw.replace(/\s/g, '');
      setFormatted(input, next, [digits(raw.slice(0, start)).length, digits(raw.slice(0, end)).length]);
    }
  }

  function fieldNodes(root) {
    return [...(root.matches?.(selector) ? [root] : []), ...root.querySelectorAll(selector)];
  }

  function refresh(root = document) {
    for (const input of fieldNodes(root)) {
      if (input.dataset.orionField === 'phone') {
        input.type = 'tel'; input.inputMode = 'tel';
        if (!input.autocomplete) input.autocomplete = 'tel-national';
        if (!input.placeholder) input.placeholder = '(00) 00000-0000';
        // Native maxlength truncates pasted +55 numbers before the input event.
        input.removeAttribute('maxlength');
      } else if (['money', 'decimal'].includes(input.dataset.orionField)) {
        input.type = 'text'; input.inputMode = input.dataset.orionField === 'decimal' && input.dataset.fieldScale === '0' ? 'numeric' : 'decimal';
        if (!input.placeholder) input.placeholder = input.dataset.orionField === 'money' ? '0,00' : '0';
        input.removeAttribute('maxlength');
      } else if (['cpf', 'cnpj', 'postal-code', 'otp'].includes(input.dataset.orionField)) {
        const kind = input.dataset.orionField;
        input.type = 'text'; input.inputMode = kind === 'cnpj' ? 'text' : 'numeric';
        input.removeAttribute('maxlength');
        if (!input.placeholder || ['cpf', 'cnpj'].includes(kind)) input.placeholder = { cpf: '000.000.000-00', cnpj: 'XX.XXX.XXX/XXXX-00', 'postal-code': '00000-000', otp: '000000' }[kind];
        if (kind === 'otp') input.autocomplete = 'one-time-code';
        if (kind === 'postal-code' && !input.autocomplete) input.autocomplete = 'postal-code';
        if (kind === 'cnpj') { input.setAttribute('autocapitalize', 'characters'); input.spellcheck = false; }
      } else if (input.dataset.orionField === 'email') {
        input.type = 'email'; input.inputMode = 'email';
        if (!input.autocomplete) input.autocomplete = 'email';
        if (!input.placeholder) input.placeholder = 'nome@email.com';
        input.setAttribute('autocapitalize', 'none'); input.spellcheck = false;
      }
      formatInput(input);
    }
  }

  function clearError(input) {
    const previous = errors.get(input);
    if (!previous) return;
    previous.node.remove();
    const describedBy = (input.getAttribute('aria-describedby') || '').split(/\s+/).filter(id => id && id !== previous.id);
    if (describedBy.length) input.setAttribute('aria-describedby', describedBy.join(' '));
    else input.removeAttribute('aria-describedby');
    if (previous.invalid === null) input.removeAttribute('aria-invalid');
    else input.setAttribute('aria-invalid', previous.invalid);
    if (input.validationMessage === previous.message) input.setCustomValidity(previous.customError);
    errors.delete(input);
  }

  function showError(input, message) {
    clearError(input);
    const doc = input.ownerDocument, id = 'orion-field-error-' + (++errorSequence), node = doc.createElement('small');
    const invalid = input.getAttribute('aria-invalid');
    const customError = input.validity.customError ? input.validationMessage : '';
    node.id = id; node.className = 'orion-field-error'; node.setAttribute('role', 'alert'); node.textContent = message;
    const target = input.dataset.fieldErrorTarget ? doc.getElementById(input.dataset.fieldErrorTarget) : input.closest('[data-field-wrapper]');
    if (target && target !== input) target.append(node);
    else input.insertAdjacentElement('afterend', node);
    input.setAttribute('aria-describedby', [input.getAttribute('aria-describedby'), id].filter(Boolean).join(' '));
    input.setAttribute('aria-invalid', 'true'); input.setCustomValidity(message);
    errors.set(input, { id, node, message, invalid, customError });
  }

  function validateNative(input) {
    if (input.willValidate === false) return;
    const validity = input.validity;
    if (validity.customError) throw Error(input.validationMessage);
    if (validity.valueMissing) throw Error(input.tagName === 'SELECT' || input.type === 'radio' ?
      'Selecione uma opção.' : input.type === 'checkbox' ? 'Marque esta opção para continuar.' : 'Preencha este campo.');
    if (validity.badInput) throw Error('Informe um valor válido.');
    if (validity.typeMismatch) throw Error(input.type === 'url' ?
      'Informe um endereço válido, como https://exemplo.com.' : input.type === 'email' ?
        'Informe um e-mail válido, como nome@email.com.' : 'Confira o formato deste campo.');
    if (validity.patternMismatch) throw Error(input.title || 'Use o formato indicado para este campo.');
    if (validity.rangeUnderflow) throw Error('O valor mínimo é ' + (input.type === 'number' ? decimalDisplay(input.min) : input.min) + '.');
    if (validity.rangeOverflow) throw Error('O valor máximo é ' + (input.type === 'number' ? decimalDisplay(input.max) : input.max) + '.');
    if (validity.stepMismatch) throw Error(input.type === 'number' ?
      'Use incrementos de ' + decimalDisplay(input.step || 1) + '.' : 'Use um valor compatível com o intervalo definido.');
    // Browsers do not consistently report length constraints after autofill or
    // programmatic assignment. Match native text lengths without changing text.
    const hasLength = input.tagName === 'TEXTAREA' || ['text', 'search', 'tel', 'url', 'email', 'password'].includes(input.type);
    if (hasLength && input.maxLength >= 0 && input.value.length > input.maxLength) throw Error('Use no máximo ' + input.maxLength + ' caracteres.');
    if (hasLength && input.value && input.minLength > 0 && input.value.length < input.minLength) throw Error('Use pelo menos ' + input.minLength + ' caracteres.');
    if (!validity.valid) throw Error('Confira este campo.');
  }

  function validate(input, { show = true } = {}) {
    if (!isEditable(input) || !input.matches?.(selector)) return true;
    clearError(input);
    try {
      if (input.dataset.orionField === 'phone') phoneValue(input.value, inputOptions(input));
      else if (input.dataset.orionField === 'money') parseMoney(input.value, inputOptions(input));
      else if (input.dataset.orionField === 'decimal') parseDecimal(input.value, inputOptions(input));
      else if (['cpf', 'cnpj'].includes(input.dataset.orionField)) documentValue(input.value, input.dataset.orionField, inputOptions(input));
      else if (input.dataset.orionField === 'postal-code') postalCodeValue(input.value, inputOptions(input));
      else if (input.dataset.orionField === 'otp') otpValue(input.value, inputOptions(input));
      else if (input.dataset.orionField === 'date') dateValue(input.value, inputOptions(input));
      else if (input.dataset.orionField === 'native') validateNative(input);
      else if (input.dataset.orionField === 'email') {
        input.value = input.value.trim();
        if (!input.value && input.required) throw Error('Informe seu e-mail.');
        if (input.value && (input.validity.typeMismatch || input.validity.patternMismatch ||
            input.maxLength > 0 && input.value.length > input.maxLength ||
            input.minLength > 0 && input.value.length < input.minLength)) throw Error('Informe um e-mail válido, como nome@email.com.');
      }
      return true;
    } catch (error) {
      if (show) showError(input, error.message);
      return false;
    }
  }

  function validateForm(form, { focus = true } = {}) {
    let first = null;
    for (const input of fieldNodes(form)) if (!validate(input) && !first) first = input;
    if (first && focus) first.focus();
    return !first;
  }

  function deletion(event) {
    const input = event.target;
    const backward = event.inputType === 'deleteContentBackward', forward = event.inputType === 'deleteContentForward';
    if (!isEditable(input) || !input.matches?.(selector) || (!backward && !forward) || input.selectionStart !== input.selectionEnd) return;
    if (input.dataset.orionField === 'money') {
      try { if (!moneyParts(input.value)) return; } catch (_) { return; }
      const comma = input.value.indexOf(','), caret = input.selectionStart ?? 0;
      if (backward && caret === comma + 1 || forward && caret === comma) {
        event.preventDefault(); input.setSelectionRange(backward ? comma : comma + 1, backward ? comma : comma + 1); return;
      }
      if (comma < 0 || caret > comma) return;
    } else if (input.dataset.orionField === 'phone') {
      if (!phoneParts(input.value, inputOptions(input))) return;
    } else if (['cpf', 'cnpj'].includes(input.dataset.orionField)) {
      if (documentParts(input.value, input.dataset.orionField) === null) return;
    } else if (input.dataset.orionField === 'postal-code') {
      if (!/^[\d\s-]*$/.test(input.value) || digits(input.value).length > 8) return;
    } else return;
    const caret = input.selectionStart ?? 0, step = backward ? -1 : 1;
    const pattern = input.dataset.orionField === 'cnpj' ? /[a-zA-Z0-9]/ : /\d/;
    let index = backward ? caret - 1 : caret;
    if (index < 0 || index >= input.value.length || pattern.test(input.value[index])) return;
    while (index >= 0 && index < input.value.length && !pattern.test(input.value[index])) index += step;
    event.preventDefault();
    if (index < 0 || index >= input.value.length) {
      input.setSelectionRange(backward ? 0 : input.value.length, backward ? 0 : input.value.length); return;
    }
    input.setRangeText('', index, index + 1, 'end');
    if (forward) input.setSelectionRange(caret, caret);
    formatInput(input, { liveMoney: input.dataset.orionField === 'money' });
    input.dispatchEvent(new input.ownerDocument.defaultView.Event('input', { bubbles: true }));
  }

  function beforeInput(event) {
    const input = event.target;
    if (isEditable(input) && input.matches?.(selector) && input.dataset.orionField === 'money') {
      moneyEdits.delete(input);
      if (/^delete/.test(event.inputType || '') || event.inputType === 'insertText' && /^\d+$/.test(event.data || '')) {
        try { if (moneyParts(input.value)) moneyEdits.add(input); } catch (_) { /* Invalid input remains intact for correction. */ }
      }
    }
    if (isEditable(input) && input.matches?.(selector) && input.dataset.orionField === 'money' &&
        event.inputType === 'insertText' && input.selectionStart === input.selectionEnd) {
      const comma = input.value.indexOf(','), caret = input.selectionStart ?? 0;
      if (comma >= 0 && /^[,.]$/.test(event.data || '')) {
        event.preventDefault(); input.setSelectionRange(comma + 1, comma + 1); return;
      }
      if (comma >= 0 && caret > comma && caret < comma + 3 && /^\d$/.test(event.data || '') && /^\d{2}$/.test(input.value.slice(comma + 1))) {
        event.preventDefault(); input.setRangeText(event.data, caret, caret + 1, 'end'); formatInput(input);
        input.dispatchEvent(new input.ownerDocument.defaultView.Event('input', { bubbles: true })); return;
      }
    }
    deletion(event);
  }

  function finishMoneyInsertion(event) {
    const input = event.target, previous = moneyValues.get(input);
    if (!isEditable(input) || input.dataset.orionField !== 'money' || !previous ||
        event.inputType !== 'insertText' || !/^\d$/.test(event.data || '')) return;
    const caret = input.selectionStart, comma = previous.indexOf(','), index = caret - 1;
    // Some editing paths emit only input. Replace an existing centavo only when
    // this exact single-character insertion can be proven from the last value.
    // A paste or an extra digit after the two centavos remains visible/invalid.
    if (caret !== input.selectionEnd || comma < 0 || index <= comma || index >= comma + 3 ||
        input.value.slice(0, index) + input.value.slice(caret) !== previous) return;
    setFormatted(input, input.value.slice(0, caret) + input.value.slice(caret + 1), [caret, caret]);
  }

  function bind(root = document) {
    if (bindings.has(root)) { refresh(root); return bindings.get(root); }
    const listeners = [];
    const on = (name, listener, capture = false) => {
      const unique = event => { if (handled.has(event)) return; handled.add(event); listener(event); };
      root.addEventListener(name, unique, capture); listeners.push([name, unique, capture]);
    };
    on('beforeinput', beforeInput);
    on('input', event => { if (event.target.matches?.(selector)) {
      clearError(event.target);
      if (!event.isComposing) {
        finishMoneyInsertion(event);
        formatInput(event.target, { liveMoney: moneyEdits.has(event.target) });
      }
      moneyEdits.delete(event.target);
    } });
    on('compositionend', event => { if (event.target.matches?.(selector)) formatInput(event.target); });
    on('change', event => { if (event.target.matches?.(selector)) formatInput(event.target); });
    on('focusout', event => { if (event.target.matches?.(selector) && event.target.value.trim()) validate(event.target); });
    on('invalid', event => { if (event.target.matches?.(selector)) validate(event.target); }, true);
    on('submit', event => { if (!validateForm(event.target)) { event.preventDefault(); event.stopImmediatePropagation(); } }, true);
    const cleanup = () => { for (const args of listeners) root.removeEventListener(...args); bindings.delete(root); };
    bindings.set(root, cleanup); refresh(root); return cleanup;
  }

  function unbind(root = document) { bindings.get(root)?.(); }

  window.OrionFields = Object.freeze({ phoneParts, phoneDisplay, phoneValue, parseMoney, moneyDisplay, parseDecimal, decimalDisplay,
    documentValue, documentDisplay, postalCodeValue, postalCodeDisplay, otpValue, dateValue, dateDisplay,
    formatInput, refresh, validate, validateForm, clearError, bind, unbind });
})();
